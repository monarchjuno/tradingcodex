from __future__ import annotations

import json
import os
import re
import select
import shlex
import subprocess
import time
import urllib.request
from datetime import timedelta
from typing import Any

from django.utils import timezone
from django.db.models import QuerySet

from apps.mcp.models import (
    McpExternalPermissionRequest,
    McpExternalTool,
    McpExternalToolCall,
    McpExternalToolPermission,
    McpRouter,
    McpToolDefinition,
)
from apps.policy.services import role_for_principal_id
from tradingcodex_service.application.common import stable_hash
from tradingcodex_service.application.runtime import (
    ensure_runtime_database,
    tradingcodex_file_lock,
    tradingcodex_state_dir,
    workspace_context_payload,
)


READ_ONLY_PROXY_MODES = {"read_only", "summary_only"}
SERVICE_PROXY_MODES = {"service_adapter", "service_path"}
EXTERNAL_MCP_GATE_STATUSES = {
    "registered",
    "disabled",
    "checked",
    "check_failed",
    "discovered",
    "reviewed",
    "enabled_read_only",
    "adapter_mapped",
}
RESEARCH_ROLES = {
    "head-manager",
    "fundamental-analyst",
    "technical-analyst",
    "news-analyst",
    "macro-analyst",
    "instrument-analyst",
    "valuation-analyst",
}
ACCOUNT_READ_ROLES = {"head-manager", "portfolio-manager", "risk-manager", "execution-operator"}
PORTFOLIO_STATE_ROLES = {"portfolio-manager", "risk-manager"}
USER_APPROVAL_CATEGORIES = {"account_read", "portfolio_state", "research_write", "workflow_prompt", "execution"}
SENSITIVE_ARGUMENT_RE = re.compile(r"secret|credential|password|api[_-]?key|token", flags=re.I)


def set_mcp_tools_enabled(queryset: QuerySet[McpToolDefinition], enabled: bool, actor: str = "admin") -> int:
    count = queryset.update(enabled=enabled)
    _audit("mcp_tool.enabled" if enabled else "mcp_tool.disabled", {"count": count}, actor)
    return count


def sync_builtin_mcp_registry(actor: str = "admin") -> None:
    from tradingcodex_service.mcp_runtime import sync_mcp_tool_definitions

    sync_mcp_tool_definitions()
    _audit("mcp_tool_registry.synced", {"source": "builtin"}, actor)


def create_or_update_router(
    *,
    name: str,
    label: str = "",
    transport: str = "stdio",
    command: str = "",
    args: list[str] | None = None,
    env: dict[str, str] | None = None,
    url: str = "",
    credential_ref: str = "",
    enabled: bool = False,
    actor: str = "web",
) -> McpRouter:
    if not name:
        raise ValueError("router name is required")
    router, created = McpRouter.objects.update_or_create(
        name=name,
        defaults={
            "label": label,
            "transport": transport or "stdio",
            "command": command,
            "args": args or [],
            "env": env or {},
            "url": url,
            "credential_ref": credential_ref,
            "enabled": bool(enabled),
            "last_status": "registered",
        },
    )
    _audit("external_mcp_router.created" if created else "external_mcp_router.updated", {"router": router.name, "enabled": router.enabled}, actor)
    return router


def import_external_mcp_discovery(router: McpRouter, discovery_payload: str | dict[str, Any], actor: str = "web") -> dict[str, Any]:
    payload = _coerce_payload(discovery_payload)
    imported: list[McpExternalTool] = []
    for primitive, item in _iter_discovered_primitives(payload):
        imported.append(upsert_external_mcp_tool(router, primitive, item))
    router.last_status = "discovered"
    router.last_error = ""
    router.last_checked_at = timezone.now()
    router.save(update_fields=["last_status", "last_error", "last_checked_at", "updated_at"])
    _audit("external_mcp.discovery_imported", {"router": router.name, "count": len(imported)}, actor)
    return {"router": router.name, "imported": len(imported), "tool_ids": [tool.id for tool in imported]}


def list_external_mcp_connections(workspace_root: Any = None, args: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_runtime_database(workspace_root)
    args = args or {}
    queryset = McpRouter.objects.prefetch_related("external_tools").all()
    if args.get("name"):
        queryset = queryset.filter(name=str(args["name"]))
    if args.get("enabled") is not None:
        queryset = queryset.filter(enabled=bool(args["enabled"]))
    limit = max(1, min(int(args.get("limit") or 50), 200))
    return {
        "connections": [serialize_external_mcp_router(router, include_tools=True) for router in queryset[:limit]],
        "db_canonical": True,
        "workspace_context": workspace_context_payload(workspace_root),
    }


def register_external_mcp_connection(workspace_root: Any, args: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_runtime_database(workspace_root)
    args = args or {}
    router = create_or_update_router(
        name=str(args.get("name") or args.get("router_name") or "").strip(),
        label=str(args.get("label") or ""),
        transport=str(args.get("transport") or "stdio"),
        command=str(args.get("command") or ""),
        args=_coerce_string_list(args.get("args")),
        env=_coerce_string_dict(args.get("env")),
        url=str(args.get("url") or ""),
        credential_ref=str(args.get("credential_ref") or ""),
        enabled=bool(args.get("enabled", False)),
        actor=str(args.get("principal_id") or args.get("actor") or "head-manager"),
    )
    return {
        "status": "registered",
        "connection": serialize_external_mcp_router(router, include_tools=True),
        "db_canonical": True,
        "workspace_context": workspace_context_payload(workspace_root),
    }


def check_external_mcp_connection(workspace_root: Any, args: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_runtime_database(workspace_root)
    args = args or {}
    router = resolve_external_mcp_router(args)
    actor = str(args.get("principal_id") or args.get("actor") or "head-manager")
    if not router.enabled:
        _mark_router(router, "disabled", "external MCP connection is disabled")
        _audit("external_mcp.check_skipped", {"router": router.name, "reason": "disabled"}, actor)
        return _external_mcp_lifecycle_result(workspace_root, router, "disabled", ["external MCP connection is disabled"])
    try:
        with tradingcodex_file_lock(f"external-mcp-{router.name}"):
            payload = _external_mcp_initialize(router, timeout=float(args.get("timeout") or 8))
        _mark_router(router, "checked", "")
        _audit("external_mcp.checked", {"router": router.name, "transport": router.transport}, actor)
        return _external_mcp_lifecycle_result(workspace_root, router, "checked", [], payload)
    except Exception as exc:
        _mark_router(router, "check_failed", str(exc))
        _audit("external_mcp.check_failed", {"router": router.name, "error": str(exc)}, actor)
        return _external_mcp_lifecycle_result(workspace_root, router, "check_failed", [str(exc)])


def discover_external_mcp_connection(workspace_root: Any, args: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_runtime_database(workspace_root)
    args = args or {}
    router = resolve_external_mcp_router(args)
    actor = str(args.get("principal_id") or args.get("actor") or "head-manager")
    if not router.enabled:
        _mark_router(router, "disabled", "external MCP connection is disabled")
        _audit("external_mcp.discovery_skipped", {"router": router.name, "reason": "disabled"}, actor)
        return _external_mcp_lifecycle_result(workspace_root, router, "disabled", ["external MCP connection is disabled"])
    try:
        with tradingcodex_file_lock(f"external-mcp-{router.name}"):
            discovery_payload = _external_mcp_discover(router, timeout=float(args.get("timeout") or 12))
        imported = import_external_mcp_discovery(router, discovery_payload, actor=actor)
        router.refresh_from_db()
        _audit("external_mcp.discovered", {"router": router.name, "imported": imported["imported"]}, actor)
        return {
            "status": "discovered",
            "connection": serialize_external_mcp_router(router, include_tools=True),
            "imported": imported,
            "db_canonical": True,
            "workspace_context": workspace_context_payload(workspace_root),
        }
    except Exception as exc:
        _mark_router(router, "check_failed", str(exc))
        _audit("external_mcp.discovery_failed", {"router": router.name, "error": str(exc)}, actor)
        return _external_mcp_lifecycle_result(workspace_root, router, "check_failed", [str(exc)])


def review_external_mcp_tool(workspace_root: Any, args: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_runtime_database(workspace_root)
    args = args or {}
    tool = resolve_external_mcp_tool(args)
    actor = str(args.get("principal_id") or args.get("actor") or "head-manager")
    category = str(args.get("category") or tool.category)
    proxy_mode = str(args.get("proxy_mode") or tool.proxy_mode)
    enabled = _bool_or_none(args.get("enabled"))
    review_status = str(args.get("review_status") or "reviewed")
    if category == "execution" and enabled:
        enabled = False
        review_status = "adapter_mapping_required"
        proxy_mode = proxy_mode if proxy_mode in SERVICE_PROXY_MODES else "service_adapter"
    updated = set_external_tool_policy(
        tool,
        category=category,
        risk_level=str(args.get("risk_level") or tool.risk_level),
        sensitivity=str(args.get("sensitivity") or tool.sensitivity),
        canonical_capability=str(args.get("canonical_capability") or tool.canonical_capability),
        proxy_mode=proxy_mode,
        allowed_roles=_coerce_string_list(args.get("allowed_roles")) if args.get("allowed_roles") is not None else None,
        enabled=enabled,
        review_status=review_status,
        actor=actor,
    )
    _refresh_router_lifecycle_status(updated.router)
    return {
        "status": updated.review_status,
        "tool": serialize_external_mcp_tool(updated),
        "connection": serialize_external_mcp_router(updated.router),
        "db_canonical": True,
        "workspace_context": workspace_context_payload(workspace_root),
    }


def upsert_external_mcp_tool(router: McpRouter, primitive: str, item: dict[str, Any]) -> McpExternalTool:
    external_name = str(item.get("name") or item.get("uri") or item.get("id") or "").strip()
    if not external_name:
        raise ValueError("external MCP item is missing name, uri, or id")
    description = str(item.get("description") or item.get("title") or "")
    input_schema = item.get("inputSchema") or item.get("input_schema") or item.get("schema") or {}
    output_schema = item.get("outputSchema") or item.get("output_schema") or {}
    schema_hash = stable_hash({"primitive": primitive, "name": external_name, "description": description, "input_schema": input_schema, "output_schema": output_schema})
    classification = classify_external_mcp_item(external_name, description, input_schema, primitive=primitive)
    tool, created = McpExternalTool.objects.get_or_create(
        router=router,
        primitive=primitive,
        external_name=external_name,
        defaults={
            "description": description,
            "input_schema": input_schema if isinstance(input_schema, dict) else {},
            "output_schema": output_schema if isinstance(output_schema, dict) else {},
            "schema_hash": schema_hash,
            **classification,
            "last_seen_at": timezone.now(),
        },
    )
    if created:
        return tool
    changed = bool(tool.schema_hash and tool.schema_hash != schema_hash)
    tool.description = description
    tool.input_schema = input_schema if isinstance(input_schema, dict) else {}
    tool.output_schema = output_schema if isinstance(output_schema, dict) else {}
    tool.schema_hash = schema_hash
    tool.last_seen_at = timezone.now()
    if changed:
        tool.enabled = False
        tool.drift_detected = True
        tool.review_status = "schema_changed"
    else:
        for field, value in classification.items():
            if tool.review_status in {"review_required", "auto_classified"} or field in {"category", "risk_level", "sensitivity", "canonical_capability"}:
                setattr(tool, field, value)
    tool.save()
    return tool


def classify_external_mcp_item(name: str, description: str = "", schema: dict[str, Any] | None = None, *, primitive: str = "tool") -> dict[str, Any]:
    text = " ".join([name, description, json.dumps(schema or {}, sort_keys=True, default=str)]).lower()
    if primitive != "tool":
        return {
            "category": "market_data" if primitive == "resource" else "workflow_prompt",
            "risk_level": "read",
            "sensitivity": "public",
            "canonical_capability": "market_data.read" if primitive == "resource" else "workflow.prompt.read",
            "proxy_mode": "read_only",
            "allowed_roles": sorted(RESEARCH_ROLES),
            "conditions": {"as_of_required": primitive == "resource"},
            "review_status": "auto_classified",
        }
    if _matches(text, r"secret|credential|password|api[_\s-]?key|token|\.env"):
        return _classification("secret", "blocked", "secret", "secret.read", "blocked", [])
    if _matches(text, r"transfer|withdraw|wire|ach|deposit"):
        return _classification("execution", "execution", "private", "cash.transfer", "service_path", [])
    if _matches(text, r"place[_\s-]?order|submit[_\s-]?order|create[_\s-]?order|replace[_\s-]?order|cancel[_\s-]?order|trade|execute"):
        capability = "order.cancel" if "cancel" in text else "order.submit"
        return _classification("execution", "execution", "private", capability, "service_adapter", [])
    if _matches(text, r"policy|permission|principal|capability|allowlist|admin|enable[_\s-]?tool|disable[_\s-]?tool"):
        return _classification("policy_admin", "write", "canonical_state", "policy.config.write", "blocked", [])
    if _matches(text, r"position|positions|balance|balances|account|buying[_\s-]?power|portfolio|orders|fills|holdings"):
        return _classification("account_read", "read", "private", "account.positions.read", "summary_only", sorted(ACCOUNT_READ_ROLES))
    if _matches(text, r"quote|quotes|candles|bars|ohlcv|price|market[_\s-]?data|ticker|tickers|news|filing|fundamental|financial|earnings"):
        return _classification("market_data", "read", "public", "market_data.read", "read_only", sorted(RESEARCH_ROLES | PORTFOLIO_STATE_ROLES))
    if _matches(text, r"snapshot|source|artifact|research|dataset|import"):
        return _classification("research_write", "write", "research", "research.snapshot.write", "service_path", sorted(RESEARCH_ROLES))
    return _classification("unknown", "unknown", "unknown", "mcp.external.unknown", "blocked", [])


def set_external_tool_policy(
    tool: McpExternalTool,
    *,
    category: str | None = None,
    risk_level: str | None = None,
    sensitivity: str | None = None,
    canonical_capability: str | None = None,
    proxy_mode: str | None = None,
    allowed_roles: list[str] | None = None,
    enabled: bool | None = None,
    review_status: str = "reviewed",
    actor: str = "web",
) -> McpExternalTool:
    if category is not None:
        tool.category = category or "unknown"
    if risk_level is not None:
        tool.risk_level = risk_level or "unknown"
    if sensitivity is not None:
        tool.sensitivity = sensitivity or "unknown"
    if canonical_capability is not None:
        tool.canonical_capability = canonical_capability
    if proxy_mode is not None:
        tool.proxy_mode = proxy_mode or "blocked"
    if allowed_roles is not None:
        tool.allowed_roles = [role for role in allowed_roles if role]
    if enabled is not None:
        if enabled:
            _validate_external_tool_can_enable(tool)
        tool.enabled = bool(enabled)
    tool.review_status = review_status or "reviewed"
    if tool.review_status == "reviewed":
        tool.drift_detected = False
    tool.save()
    _audit("external_mcp_tool.policy_updated", {"tool": str(tool), "enabled": tool.enabled, "proxy_mode": tool.proxy_mode}, actor)
    return tool


def evaluate_external_mcp_proxy_call(
    workspace_root: Any,
    tool: McpExternalTool,
    *,
    principal_id: str,
    arguments: dict[str, Any] | None = None,
    actor: str = "mcp-proxy",
) -> dict[str, Any]:
    arguments = arguments or {}
    reasons = external_tool_denial_reasons(tool, principal_id)
    request_hash = stable_hash(arguments)
    permission_request = None
    if not reasons and _external_tool_requires_user_approval(tool) and not _approved_external_permission(tool, principal_id, request_hash):
        role = role_for_principal_id(principal_id)
        permission_request = _create_or_reuse_permission_request(
            tool,
            principal_id=principal_id,
            role=role,
            request_hash=request_hash,
            arguments=arguments,
            workflow_run_id=str(arguments.get("workflow_run_id") or ""),
            reasons=["user permission required for external MCP call"],
        )
        reasons = ["user permission required for external MCP call"]
        decision = "approval_required"
    else:
        decision = "allow" if not reasons else "deny"
    result = {
        "decision": decision,
        "reasons": reasons,
        "router": tool.router.name,
        "external_name": tool.external_name,
        "proxy_mode": tool.proxy_mode,
        "category": tool.category,
        "risk_level": tool.risk_level,
        "canonical_capability": tool.canonical_capability,
        "adapter_call_allowed": decision == "allow" and tool.proxy_mode in SERVICE_PROXY_MODES,
        "direct_proxy_allowed": decision == "allow" and tool.proxy_mode in READ_ONLY_PROXY_MODES,
        "permission_request": serialize_permission_request(permission_request) if permission_request else None,
        "db_canonical": True,
        "workspace_context": workspace_context_payload(workspace_root),
    }
    McpExternalToolCall.objects.create(
        external_tool=tool,
        router_name=tool.router.name,
        external_name=tool.external_name,
        principal_id=principal_id,
        proxy_mode=tool.proxy_mode,
        decision=decision,
        reasons=reasons,
        request=arguments,
        response=result,
        request_hash=request_hash,
        result_hash=stable_hash(result),
        workspace_context=result["workspace_context"],
    )
    audit_action = "external_mcp.proxy_allowed" if decision == "allow" else "external_mcp.proxy_permission_required" if decision == "approval_required" else "external_mcp.proxy_denied"
    _audit(audit_action, {"tool": str(tool), "reasons": reasons}, actor)
    return result


def list_external_mcp_permission_requests(workspace_root: Any = None, args: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_runtime_database(workspace_root)
    _expire_external_mcp_permission_requests()
    args = args or {}
    queryset = McpExternalPermissionRequest.objects.select_related("external_tool").all()
    status = str(args.get("status") or "pending")
    if status and status != "all":
        queryset = queryset.filter(status=status)
    if args.get("principal_id"):
        queryset = queryset.filter(principal_id=str(args["principal_id"]))
    if args.get("router_name") or args.get("name"):
        queryset = queryset.filter(router_name=str(args.get("router_name") or args.get("name")))
    limit = max(1, min(int(args.get("limit") or 50), 200))
    return {
        "status": "ok",
        "requests": [serialize_permission_request(item) for item in queryset[:limit]],
        "count": queryset.count(),
        "workspace_context": workspace_context_payload(workspace_root),
    }


def approve_external_mcp_permission_request(workspace_root: Any, args: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_runtime_database(workspace_root)
    _expire_external_mcp_permission_requests()
    args = args or {}
    request = resolve_external_mcp_permission_request(args)
    if request.status != "pending":
        raise ValueError(f"external MCP permission request is not pending: {request.status}")
    request.status = "approved"
    request.decided_by = str(args.get("principal_id") or args.get("decided_by") or "user")
    request.decided_at = timezone.now()
    request.decision_reason = str(args.get("reason") or "")
    request.save(update_fields=["status", "decided_by", "decided_at", "decision_reason", "updated_at"])
    _audit("external_mcp.permission_approved", {"request_id": request.id, "tool": f"{request.router_name}:{request.external_name}"}, request.decided_by)
    return {"status": "approved", "request": serialize_permission_request(request), "workspace_context": workspace_context_payload(workspace_root)}


def deny_external_mcp_permission_request(workspace_root: Any, args: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_runtime_database(workspace_root)
    _expire_external_mcp_permission_requests()
    args = args or {}
    request = resolve_external_mcp_permission_request(args)
    if request.status != "pending":
        raise ValueError(f"external MCP permission request is not pending: {request.status}")
    request.status = "denied"
    request.decided_by = str(args.get("principal_id") or args.get("decided_by") or "user")
    request.decided_at = timezone.now()
    request.decision_reason = str(args.get("reason") or "")
    request.save(update_fields=["status", "decided_by", "decided_at", "decision_reason", "updated_at"])
    _audit("external_mcp.permission_denied", {"request_id": request.id, "tool": f"{request.router_name}:{request.external_name}"}, request.decided_by)
    return {"status": "denied", "request": serialize_permission_request(request), "workspace_context": workspace_context_payload(workspace_root)}


def resolve_external_mcp_permission_request(args: dict[str, Any]) -> McpExternalPermissionRequest:
    request_id = args.get("request_id") or args.get("id")
    if request_id in (None, ""):
        raise ValueError("external MCP permission request requires request_id")
    request = McpExternalPermissionRequest.objects.select_related("external_tool").filter(pk=int(request_id)).first()
    if request is None:
        raise ValueError(f"external MCP permission request not found: {request_id}")
    return request


def serialize_permission_request(request: McpExternalPermissionRequest | None) -> dict[str, Any] | None:
    if request is None:
        return None
    return {
        "id": request.id,
        "created_at": request.created_at.isoformat() if request.created_at else "",
        "updated_at": request.updated_at.isoformat() if request.updated_at else "",
        "router_name": request.router_name,
        "external_name": request.external_name,
        "principal_id": request.principal_id,
        "role": request.role,
        "workflow_run_id": request.workflow_run_id,
        "request_hash": request.request_hash,
        "arguments_summary": request.arguments_summary,
        "approval_scope": request.approval_scope,
        "status": request.status,
        "reasons": list(request.reasons or []),
        "expires_at": request.expires_at.isoformat() if request.expires_at else "",
        "decided_by": request.decided_by,
        "decided_at": request.decided_at.isoformat() if request.decided_at else "",
        "decision_reason": request.decision_reason,
    }


def external_tool_denial_reasons(tool: McpExternalTool, principal_id: str) -> list[str]:
    reasons: list[str] = []
    role = role_for_principal_id(principal_id)
    if not tool.router.enabled:
        reasons.append(f"router is disabled: {tool.router.name}")
    if not tool.enabled:
        reasons.append(f"external tool is disabled: {tool.external_name}")
    if tool.drift_detected:
        reasons.append("schema drift requires review")
    if tool.review_status not in {"reviewed", "approved"}:
        reasons.append(f"tool review is not complete: {tool.review_status}")
    if tool.category in {"secret", "policy_admin"}:
        reasons.append(f"category is not proxyable: {tool.category}")
    if tool.category == "execution" and tool.proxy_mode not in SERVICE_PROXY_MODES:
        reasons.append("execution tools must map to a TradingCodex service adapter path")
    if tool.category == "unknown":
        reasons.append("unknown tools require classification before proxy")
    allowed = set(tool.allowed_roles or [])
    if not _permission_allows(tool, principal_id, role, allowed):
        reasons.append(f"principal is not allowed for external tool: {principal_id}")
    return list(dict.fromkeys(reasons))


def _external_tool_requires_user_approval(tool: McpExternalTool) -> bool:
    if tool.category in USER_APPROVAL_CATEGORIES:
        return True
    return tool.sensitivity not in {"public", ""} or tool.risk_level in {"write", "approval", "execution"}


def _approved_external_permission(tool: McpExternalTool, principal_id: str, request_hash: str) -> bool:
    now = timezone.now()
    return McpExternalPermissionRequest.objects.filter(
        external_tool=tool,
        principal_id=principal_id,
        request_hash=request_hash,
        status="approved",
    ).filter(expires_at__gt=now).exists()


def _create_or_reuse_permission_request(
    tool: McpExternalTool,
    *,
    principal_id: str,
    role: str,
    request_hash: str,
    arguments: dict[str, Any],
    workflow_run_id: str = "",
    reasons: list[str] | None = None,
) -> McpExternalPermissionRequest:
    now = timezone.now()
    _expire_external_mcp_permission_requests(now)
    request = McpExternalPermissionRequest.objects.filter(
        external_tool=tool,
        principal_id=principal_id,
        request_hash=request_hash,
        status="pending",
        expires_at__gt=now,
    ).first()
    if request is not None:
        return request
    return McpExternalPermissionRequest.objects.create(
        external_tool=tool,
        router_name=tool.router.name,
        external_name=tool.external_name,
        principal_id=principal_id,
        role=role,
        workflow_run_id=workflow_run_id,
        request_hash=request_hash,
        arguments_summary=_summarize_arguments(arguments),
        reasons=reasons or [],
        expires_at=now + timedelta(hours=24),
    )


def _expire_external_mcp_permission_requests(now: Any = None) -> None:
    now = now or timezone.now()
    McpExternalPermissionRequest.objects.filter(status="pending", expires_at__lte=now).update(status="expired", updated_at=now)


def _summarize_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for key, value in sorted((arguments or {}).items()):
        if SENSITIVE_ARGUMENT_RE.search(str(key)):
            summary[str(key)] = "<redacted>"
        elif isinstance(value, str | int | float | bool) or value is None:
            text = str(value)
            summary[str(key)] = text if len(text) <= 120 else text[:117] + "..."
        elif isinstance(value, list):
            summary[str(key)] = f"list[{len(value)}]"
        elif isinstance(value, dict):
            summary[str(key)] = f"object[{len(value)}]"
        else:
            summary[str(key)] = type(value).__name__
    return summary


def resolve_external_mcp_router(args: dict[str, Any]) -> McpRouter:
    router_id = args.get("router_id") or args.get("connection_id")
    name = args.get("name") or args.get("router_name")
    queryset = McpRouter.objects.all()
    router = None
    if router_id not in (None, ""):
        router = queryset.filter(pk=router_id).first()
    if router is None and name:
        router = queryset.filter(name=str(name)).first()
    if router is None:
        raise ValueError("external MCP connection requires router_id or name")
    return router


def resolve_external_mcp_tool(args: dict[str, Any]) -> McpExternalTool:
    tool_id = args.get("tool_id") or args.get("external_tool_id")
    if tool_id not in (None, ""):
        tool = McpExternalTool.objects.select_related("router").filter(pk=tool_id).first()
        if tool is not None:
            return tool
    router_name = args.get("router_name") or args.get("name")
    external_name = args.get("external_name") or args.get("tool_name")
    primitive = str(args.get("primitive") or "tool")
    if router_name and external_name:
        tool = McpExternalTool.objects.select_related("router").filter(
            router__name=str(router_name),
            primitive=primitive,
            external_name=str(external_name),
        ).first()
        if tool is not None:
            return tool
    raise ValueError("external MCP tool requires tool_id or router_name plus external_name")


def serialize_external_mcp_router(router: McpRouter, *, include_tools: bool = False) -> dict[str, Any]:
    record = {
        "id": router.id,
        "name": router.name,
        "label": router.label,
        "transport": router.transport,
        "command": router.command,
        "args": list(router.args or []),
        "url": router.url,
        "credential_ref": router.credential_ref,
        "trust_level": router.trust_level,
        "enabled": router.enabled,
        "last_status": router.last_status,
        "last_error": router.last_error,
        "last_checked_at": router.last_checked_at.isoformat() if router.last_checked_at else "",
        "lifecycle": router.last_status if router.last_status in EXTERNAL_MCP_GATE_STATUSES else "registered",
    }
    if include_tools:
        record["tools"] = [serialize_external_mcp_tool(tool) for tool in router.external_tools.all()]
    return record


def serialize_external_mcp_tool(tool: McpExternalTool) -> dict[str, Any]:
    return {
        "id": tool.id,
        "router": tool.router.name,
        "primitive": tool.primitive,
        "external_name": tool.external_name,
        "description": tool.description,
        "schema_hash": tool.schema_hash,
        "category": tool.category,
        "risk_level": tool.risk_level,
        "sensitivity": tool.sensitivity,
        "canonical_capability": tool.canonical_capability,
        "proxy_mode": tool.proxy_mode,
        "allowed_roles": list(tool.allowed_roles or []),
        "conditions": tool.conditions or {},
        "enabled": tool.enabled,
        "review_status": tool.review_status,
        "drift_detected": tool.drift_detected,
        "last_seen_at": tool.last_seen_at.isoformat() if tool.last_seen_at else "",
    }


def _external_mcp_lifecycle_result(
    workspace_root: Any,
    router: McpRouter,
    status: str,
    reasons: list[str],
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "status": status,
        "reasons": reasons,
        "connection": serialize_external_mcp_router(router, include_tools=True),
        "payload": payload or {},
        "db_canonical": True,
        "workspace_context": workspace_context_payload(workspace_root),
    }


def _mark_router(router: McpRouter, status: str, error: str = "") -> None:
    router.last_status = status
    router.last_error = error
    router.last_checked_at = timezone.now()
    router.save(update_fields=["last_status", "last_error", "last_checked_at", "updated_at"])


def _refresh_router_lifecycle_status(router: McpRouter) -> None:
    tools = list(router.external_tools.all())
    if any(tool.enabled and tool.review_status in {"reviewed", "approved"} and tool.proxy_mode in READ_ONLY_PROXY_MODES for tool in tools):
        status = "enabled_read_only"
    elif any(
        tool.review_status == "adapter_mapping_required"
        or (tool.category == "execution" and tool.proxy_mode in SERVICE_PROXY_MODES and tool.review_status in {"reviewed", "approved"})
        for tool in tools
    ):
        status = "adapter_mapped"
    elif tools and all(tool.review_status in {"reviewed", "approved", "adapter_mapping_required"} for tool in tools):
        status = "reviewed"
    else:
        status = router.last_status if router.last_status in EXTERNAL_MCP_GATE_STATUSES else "registered"
    router.last_status = status
    router.last_checked_at = timezone.now()
    router.save(update_fields=["last_status", "last_checked_at", "updated_at"])


def _external_mcp_initialize(router: McpRouter, timeout: float = 8.0) -> dict[str, Any]:
    responses = _external_mcp_rpc(router, ["initialize"], timeout=timeout)
    return responses.get("initialize", {})


def _external_mcp_discover(router: McpRouter, timeout: float = 12.0) -> dict[str, Any]:
    responses = _external_mcp_rpc(router, ["initialize", "tools/list", "resources/list", "prompts/list"], timeout=timeout)
    payload: dict[str, Any] = {"tools": [], "resources": [], "prompts": []}
    for method, key in (("tools/list", "tools"), ("resources/list", "resources"), ("prompts/list", "prompts")):
        result = responses.get(method, {}).get("result", {})
        items = result.get(key) if isinstance(result, dict) else None
        if isinstance(items, list):
            payload[key] = items
    initialize = responses.get("initialize", {}).get("result", {})
    if isinstance(initialize, dict):
        payload["server"] = initialize.get("serverInfo") or {}
        payload["protocolVersion"] = initialize.get("protocolVersion", "")
    return payload


def _external_mcp_rpc(router: McpRouter, methods: list[str], timeout: float) -> dict[str, dict[str, Any]]:
    transport = (router.transport or "stdio").lower()
    if transport in {"http", "streamable-http", "streamable_http"}:
        return {method: _http_mcp_rpc(router, method, timeout) for method in methods}
    if transport == "stdio":
        return _stdio_mcp_rpc(router, methods, timeout)
    raise ValueError(f"unsupported external MCP transport: {router.transport}")


def _http_mcp_rpc(router: McpRouter, method: str, timeout: float) -> dict[str, Any]:
    if not router.url:
        raise ValueError("HTTP external MCP requires url")
    request = urllib.request.Request(
        router.url,
        data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": {}}).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if isinstance(payload, list):
        payload = next((item for item in payload if isinstance(item, dict) and item.get("id") == 1), {})
    if not isinstance(payload, dict):
        raise ValueError("HTTP external MCP returned a non-object response")
    if payload.get("error"):
        return payload
    return payload


def _stdio_mcp_rpc(router: McpRouter, methods: list[str], timeout: float) -> dict[str, dict[str, Any]]:
    argv = _router_argv(router)
    env = os.environ.copy()
    env.update(_coerce_string_dict(router.env))
    run_dir = tradingcodex_state_dir() / "run" / "external-mcp"
    run_dir.mkdir(parents=True, exist_ok=True)
    log_path = run_dir / f"{router.name}.stderr.log"
    responses: dict[str, dict[str, Any]] = {}
    deadline = time.monotonic() + timeout
    with log_path.open("ab") as stderr:
        process = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=stderr,
            text=True,
            env=env,
            start_new_session=True,
        )
        try:
            request_id = 0
            for method in methods:
                request_id += 1
                _stdio_write(process, {"jsonrpc": "2.0", "id": request_id, "method": method, "params": {}})
                responses[method] = _stdio_read_response(process, request_id, deadline)
                if method == "initialize":
                    _stdio_write(process, {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
            return responses
        finally:
            _terminate_process(process)


def _stdio_write(process: subprocess.Popen, payload: dict[str, Any]) -> None:
    if process.stdin is None:
        raise ValueError("external MCP stdio stdin is unavailable")
    process.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
    process.stdin.flush()


def _stdio_read_response(process: subprocess.Popen, request_id: int, deadline: float) -> dict[str, Any]:
    if process.stdout is None:
        raise ValueError("external MCP stdio stdout is unavailable")
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"external MCP process exited with code {process.returncode}")
        timeout = max(0.05, deadline - time.monotonic())
        ready, _, _ = select.select([process.stdout], [], [], timeout)
        if not ready:
            continue
        line = process.stdout.readline()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except Exception:
            continue
        if isinstance(payload, dict) and payload.get("id") == request_id:
            return payload
    raise TimeoutError("external MCP stdio response timed out")


def _terminate_process(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=2)


def _router_argv(router: McpRouter) -> list[str]:
    command = str(router.command or "").strip()
    argv = shlex.split(command) if command else []
    argv.extend(str(item) for item in (router.args or []) if str(item))
    if not argv:
        raise ValueError("stdio external MCP requires command or args")
    return argv


def _coerce_string_list(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return [str(item) for item in parsed if str(item)]
        except Exception:
            return [item for item in shlex.split(value) if item]
    if isinstance(value, list | tuple):
        return [str(item) for item in value if str(item)]
    return [str(value)]


def _coerce_string_dict(value: Any) -> dict[str, str]:
    if not value:
        return {}
    if isinstance(value, str):
        parsed = json.loads(value)
        if not isinstance(parsed, dict):
            raise ValueError("env must be a JSON object")
        value = parsed
    if not isinstance(value, dict):
        raise ValueError("env must be an object")
    return {str(key): str(item) for key, item in value.items() if str(key)}


def _bool_or_none(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    return str(value).lower() in {"1", "true", "yes", "on", "enabled"}


def _validate_external_tool_can_enable(tool: McpExternalTool) -> None:
    if tool.drift_detected:
        raise ValueError("schema drift requires review before enabling")
    if tool.proxy_mode == "direct":
        raise ValueError("direct raw proxy mode is not allowed")
    if tool.category in {"secret", "policy_admin", "unknown"}:
        raise ValueError(f"{tool.category} tools cannot be enabled for proxy")
    if tool.category == "execution" and tool.proxy_mode not in SERVICE_PROXY_MODES:
        raise ValueError("execution tools must use service_adapter or service_path proxy mode")


def _permission_allows(tool: McpExternalTool, principal_id: str, role: str, allowed_roles: set[str]) -> bool:
    if principal_id in allowed_roles or role in allowed_roles:
        return True
    permissions = McpExternalToolPermission.objects.filter(external_tool=tool, enabled=True)
    if permissions.filter(decision="deny", principal_or_role__in={principal_id, role}).exists():
        return False
    return permissions.filter(decision="allow", principal_or_role__in={principal_id, role}).exists()


def _classification(category: str, risk_level: str, sensitivity: str, capability: str, proxy_mode: str, roles: list[str]) -> dict[str, Any]:
    return {
        "category": category,
        "risk_level": risk_level,
        "sensitivity": sensitivity,
        "canonical_capability": capability,
        "proxy_mode": proxy_mode,
        "allowed_roles": roles,
        "conditions": {},
        "review_status": "auto_classified",
    }


def _matches(text: str, pattern: str) -> bool:
    return bool(re.search(pattern, text, flags=re.I))


def _coerce_payload(payload: str | dict[str, Any]) -> dict[str, Any]:
    if isinstance(payload, dict):
        return payload
    if not str(payload).strip():
        return {}
    parsed = json.loads(payload)
    if not isinstance(parsed, dict):
        raise ValueError("MCP discovery payload must be a JSON object")
    return parsed


def _iter_discovered_primitives(payload: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    body = payload.get("result") if isinstance(payload.get("result"), dict) else payload
    primitives: list[tuple[str, dict[str, Any]]] = []
    for key, primitive in [("tools", "tool"), ("resources", "resource"), ("prompts", "prompt")]:
        items = body.get(key) if isinstance(body, dict) else None
        if isinstance(items, list):
            primitives.extend((primitive, item) for item in items if isinstance(item, dict))
    return primitives


def _audit(action: str, payload: dict[str, Any], actor: str) -> None:
    from tradingcodex_service.application.audit import write_audit_event_if_available

    write_audit_event_if_available(None, actor, "admin", {"type": action, "payload": payload})

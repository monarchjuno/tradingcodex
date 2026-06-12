from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


RESEARCH_ROLES = {
    "head-manager",
    "fundamental-analyst",
    "technical-analyst",
    "news-analyst",
    "macro-analyst",
    "instrument-analyst",
    "valuation-analyst",
    "portfolio-manager",
    "risk-manager",
}
POLICY_ROLES = {"head-manager", "valuation-analyst", "portfolio-manager", "risk-manager", "execution-operator"}
PORTFOLIO_ROLES = {"head-manager", "portfolio-manager", "risk-manager", "execution-operator"}
APPROVAL_ROLES = {"risk-manager"}
EXECUTION_ROLES = {"execution-operator"}
SAFE_HOME_TOOL_NAMES = frozenset({
    "get_tradingcodex_status",
    "get_order_status",
    "get_positions",
    "get_portfolio_snapshot",
    "list_workflow_artifacts",
    "get_research_artifact",
    "list_research_artifacts",
    "search_research_artifacts",
})


@dataclass(frozen=True)
class McpToolSpec:
    name: str
    description: str
    category: str
    risk_level: str
    allowed_roles: frozenset[str]
    handler_name: str
    input_schema: dict[str, Any]
    capability_required: str = ""
    requires_approval: bool = False
    audit_required: bool = True
    experimental: bool = False

    def public_definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
            "annotations": {
                "category": self.category,
                "risk_level": self.risk_level,
                "requires_approval": self.requires_approval,
                "audit_required": self.audit_required,
                "allowed_roles": sorted(self.allowed_roles),
                "experimental": self.experimental,
            },
        }


def object_schema(properties: dict[str, Any] | None = None, required: list[str] | None = None) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties or {},
        "required": required or [],
        "additionalProperties": True,
    }


TOOL_SPECS: tuple[McpToolSpec, ...] = (
    McpToolSpec(
        name="get_tradingcodex_status",
        description="Return TradingCodex service, DB, workspace, and active profile status.",
        category="harness",
        risk_level="read",
        allowed_roles=frozenset({"head-manager"}),
        handler_name="get_tradingcodex_status",
        input_schema=object_schema(),
    ),
    McpToolSpec(
        name="simulate_policy",
        description="Evaluate TradingCodex policy for a proposed action without bypassing service-layer checks.",
        category="policy",
        risk_level="read",
        allowed_roles=frozenset(POLICY_ROLES),
        handler_name="simulate_policy",
        input_schema=object_schema({
            "principal_id": {"type": "string"},
            "action": {"type": "string"},
            "resource": {"type": "string"},
            "order_intent": {"type": "object"},
            "approval_receipt": {"type": "object"},
        }),
    ),
    McpToolSpec(
        name="validate_order_intent",
        description="Validate a draft order intent against schema, restricted list, limits, and adapter policy.",
        category="orders",
        risk_level="write",
        allowed_roles=frozenset({"portfolio-manager", "risk-manager", "execution-operator"}),
        handler_name="validate_order_intent",
        input_schema=object_schema({"order_intent": {"type": "object"}, "order_intent_path": {"type": "string"}}),
        capability_required="order_intent.validate",
    ),
    McpToolSpec(
        name="validate_approval_receipt",
        description="Validate that an approval receipt is valid, unexpired, and matches its order intent.",
        category="orders",
        risk_level="write",
        allowed_roles=frozenset({"risk-manager", "execution-operator"}),
        handler_name="validate_approval_receipt",
        input_schema=object_schema({"order_intent": {"type": "object"}, "approval_receipt": {"type": "object"}}),
        capability_required="approval_receipt.validate",
    ),
    McpToolSpec(
        name="create_approval_receipt",
        description="Create an approval receipt only after order-intent and policy validation.",
        category="approvals",
        risk_level="approval",
        allowed_roles=frozenset(APPROVAL_ROLES),
        handler_name="create_approval_receipt",
        input_schema=object_schema({"order_intent": {"type": "object"}, "approved_by": {"type": "string"}, "expires_hours": {"type": "integer"}}),
        capability_required="approval_receipt.create",
        requires_approval=True,
    ),
    McpToolSpec(
        name="submit_approved_order",
        description="Experimental: submit an approved order through a non-live adapter after order, approval, idempotency, and policy revalidation.",
        category="execution",
        risk_level="execution",
        allowed_roles=frozenset(EXECUTION_ROLES),
        handler_name="submit_approved_order",
        input_schema=object_schema({"order_intent": {"type": "object"}, "approval_receipt": {"type": "object"}, "order_intent_id": {"type": "string"}}),
        capability_required="mcp.tradingcodex.submit_approved_order",
        requires_approval=True,
        experimental=True,
    ),
    McpToolSpec(
        name="cancel_approved_order",
        description="Experimental placeholder cancellation gateway; records audit but does not call a live broker.",
        category="execution",
        risk_level="execution",
        allowed_roles=frozenset(EXECUTION_ROLES),
        handler_name="cancel_approved_order",
        input_schema=object_schema({"order_id": {"type": "string"}}),
        capability_required="mcp.tradingcodex.cancel_approved_order",
        requires_approval=True,
        experimental=True,
    ),
    McpToolSpec(
        name="get_order_status",
        description="Experimental: return local-only order status information for the initial harness.",
        category="execution",
        risk_level="read",
        allowed_roles=frozenset(EXECUTION_ROLES | {"head-manager"}),
        handler_name="get_order_status",
        input_schema=object_schema({"order_id": {"type": "string"}}),
        experimental=True,
    ),
    McpToolSpec(
        name="get_positions",
        description="Return paper portfolio positions and cash state.",
        category="portfolio",
        risk_level="read",
        allowed_roles=frozenset(PORTFOLIO_ROLES),
        handler_name="list_positions",
        input_schema=object_schema(),
    ),
    McpToolSpec(
        name="get_portfolio_snapshot",
        description="Return portfolio snapshot for portfolio, risk, and execution checks.",
        category="portfolio",
        risk_level="read",
        allowed_roles=frozenset(PORTFOLIO_ROLES),
        handler_name="list_positions",
        input_schema=object_schema(),
    ),
    McpToolSpec(
        name="list_workflow_artifacts",
        description="List workflow artifacts from workspace paths and file-native research memory.",
        category="workflows",
        risk_level="read",
        allowed_roles=frozenset(RESEARCH_ROLES | {"execution-operator"}),
        handler_name="list_workflow_artifacts",
        input_schema=object_schema(),
    ),
    McpToolSpec(
        name="create_research_artifact",
        description="Store markdown research as a workspace-native file through the service layer.",
        category="research",
        risk_level="write",
        allowed_roles=frozenset(RESEARCH_ROLES),
        handler_name="create_research_artifact",
        input_schema=object_schema({
            "artifact_id": {"type": "string"},
            "artifact_type": {"type": "string"},
            "universe": {"type": "string"},
            "symbol": {"type": "string"},
            "title": {"type": "string"},
            "markdown": {"type": "string"},
            "markdown_path": {"type": "string"},
            "source_as_of": {"type": "string"},
            "readiness_label": {"type": "string"},
        }),
        capability_required="research_artifact.write",
    ),
    McpToolSpec(
        name="append_research_artifact_version",
        description="Append a workspace-file version for an existing research artifact.",
        category="research",
        risk_level="write",
        allowed_roles=frozenset(RESEARCH_ROLES),
        handler_name="append_research_artifact_version",
        input_schema=object_schema({"artifact_id": {"type": "string"}, "markdown": {"type": "string"}}, ["artifact_id"]),
        capability_required="research_artifact.write",
    ),
    McpToolSpec(
        name="get_research_artifact",
        description="Fetch a workspace-native research artifact by artifact_id.",
        category="research",
        risk_level="read",
        allowed_roles=frozenset(RESEARCH_ROLES),
        handler_name="get_research_artifact",
        input_schema=object_schema({"artifact_id": {"type": "string"}, "include_markdown": {"type": "boolean"}}, ["artifact_id"]),
    ),
    McpToolSpec(
        name="list_research_artifacts",
        description="List workspace-native research artifacts and metadata.",
        category="research",
        risk_level="read",
        allowed_roles=frozenset(RESEARCH_ROLES),
        handler_name="list_research_artifacts",
        input_schema=object_schema({"artifact_type": {"type": "string"}, "universe": {"type": "string"}, "symbol": {"type": "string"}, "limit": {"type": "integer"}}),
    ),
    McpToolSpec(
        name="search_research_artifacts",
        description="Search workspace-native markdown research artifacts with lexical file search.",
        category="research",
        risk_level="read",
        allowed_roles=frozenset(RESEARCH_ROLES),
        handler_name="search_research_artifacts",
        input_schema=object_schema({"query": {"type": "string"}, "limit": {"type": "integer"}}, ["query"]),
    ),
    McpToolSpec(
        name="export_research_artifact_md",
        description="Export or copy a workspace-native research artifact markdown file.",
        category="research",
        risk_level="write",
        allowed_roles=frozenset(RESEARCH_ROLES),
        handler_name="export_research_artifact_md",
        input_schema=object_schema({"artifact_id": {"type": "string"}, "export_path": {"type": "string"}}, ["artifact_id"]),
        capability_required="research_artifact.export",
    ),
    McpToolSpec(
        name="record_source_snapshot",
        description="Record provider/as-of/retrieved metadata and warnings as a workspace source-snapshot JSON file.",
        category="research",
        risk_level="write",
        allowed_roles=frozenset(RESEARCH_ROLES),
        handler_name="record_source_snapshot",
        input_schema=object_schema({"provider": {"type": "string"}, "source_category": {"type": "string"}, "as_of": {"type": "string"}, "artifact_id": {"type": "string"}, "warnings": {"type": "array"}, "payload": {"type": "object"}}),
        capability_required="source_snapshot.record",
    ),
    McpToolSpec(
        name="record_audit_event",
        description="Append an explicit audit event through the TradingCodex audit ledger.",
        category="audit",
        risk_level="write",
        allowed_roles=frozenset(RESEARCH_ROLES | {"execution-operator"}),
        handler_name="record_audit_event",
        input_schema=object_schema({"event": {"type": "object"}, "principal_id": {"type": "string"}}),
        capability_required="audit_event.record",
    ),
)

TOOL_REGISTRY = {tool.name: tool for tool in TOOL_SPECS}
_REGISTRY_SYNCED = False
_REGISTRY_SYNCED_DB = ""


def prepare_mcp_runtime(workspace_root: Path | str | None = None) -> None:
    global _REGISTRY_SYNCED, _REGISTRY_SYNCED_DB
    from tradingcodex_service.application.runtime import tradingcodex_db_path

    current_db = str(tradingcodex_db_path())
    if _REGISTRY_SYNCED and (not current_db or _REGISTRY_SYNCED_DB == current_db):
        return
    try:
        if workspace_root is not None:
            from tradingcodex_service.application.runtime import ensure_runtime_database, workspace_file_lock

            ensure_runtime_database(workspace_root)
            with workspace_file_lock(workspace_root, "mcp-registry"):
                sync_mcp_tool_definitions()
        else:
            sync_mcp_tool_definitions()
        _REGISTRY_SYNCED = True
        _REGISTRY_SYNCED_DB = current_db
    except Exception:
        return


def list_mcp_tools() -> list[dict[str, Any]]:
    return [tool.public_definition() for tool in visible_tool_specs() if tool_enabled(tool.name)]


def static_mcp_tools() -> list[dict[str, Any]]:
    return [tool.public_definition() for tool in TOOL_SPECS]


def visible_tool_specs() -> tuple[McpToolSpec, ...]:
    if safe_home_mcp_scope():
        return tuple(tool for tool in TOOL_SPECS if tool.name in SAFE_HOME_TOOL_NAMES)
    return TOOL_SPECS


def safe_home_mcp_scope() -> bool:
    import os

    return os.environ.get("TRADINGCODEX_MCP_SAFE_TOOLS", "").lower() in {"1", "true", "yes", "on"}


def sync_mcp_tool_definitions() -> None:
    from apps.mcp.models import McpToolDefinition
    from apps.policy.services import sync_builtin_principals_and_capabilities

    for tool in TOOL_SPECS:
        existing = McpToolDefinition.objects.filter(name=tool.name).first()
        McpToolDefinition.objects.update_or_create(
            name=tool.name,
            defaults={
                "description": tool.description,
                "category": tool.category,
                "capability_required": tool.capability_required,
                "input_schema": tool.input_schema,
                "risk_level": tool.risk_level,
                "allowed_roles": sorted(tool.allowed_roles),
                "requires_approval": tool.requires_approval,
                "audit_required": tool.audit_required,
                "experimental": tool.experimental,
                "enabled": existing.enabled if existing is not None else True,
            },
        )
    sync_builtin_principals_and_capabilities(TOOL_SPECS)


def tool_enabled(name: str) -> bool:
    try:
        from apps.mcp.models import McpToolDefinition

        configured = McpToolDefinition.objects.filter(name=name).first()
        if configured is not None:
            return configured.enabled
    except Exception:
        return True
    return True


def role_for_principal(principal_id: str) -> str:
    try:
        from apps.policy.services import role_for_principal_id

        return role_for_principal_id(principal_id)
    except Exception:
        pass
    return principal_id or "unknown"


def call_mcp_tool(workspace_root: Path | str, name: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
    prepare_mcp_runtime(workspace_root)
    args = dict(args or {})
    tool = TOOL_REGISTRY.get(name)
    if tool is None:
        raise ValueError(f"Unknown TradingCodex tool: {name}")
    if safe_home_mcp_scope() and name not in SAFE_HOME_TOOL_NAMES:
        raise PermissionError(f"MCP tool is not available in tradingcodex-home safe scope: {name}")
    if not tool_enabled(name):
        raise PermissionError(f"MCP tool is disabled: {name}")
    principal_id = str(args.get("principal_id") or default_principal_for_tool(tool))
    role = role_for_principal(principal_id)
    if role not in tool.allowed_roles:
        raise PermissionError(f"{principal_id} is not allowed to call {name}")
    from apps.policy.services import capability_check, tool_capability_action

    action = tool_capability_action(tool.name, tool.capability_required)
    capability_allowed, capability_reasons = capability_check(principal_id, action, args.get("resource"))
    if not capability_allowed:
        raise PermissionError("; ".join(capability_reasons))
    validate_input_schema(tool, args)

    started = time.monotonic()
    request_payload = {"tool_name": name, "arguments": args, "principal_id": principal_id}
    try:
        result = raw_call_tool(workspace_root, tool, args, principal_id)
        if isinstance(result, dict):
            from tradingcodex_service.application.runtime import persist_workspace_context_if_available

            result = dict(result)
            result.setdefault("db_canonical", True)
            result.setdefault("workspace_context", persist_workspace_context_if_available(workspace_root))
        record_tool_call(workspace_root, name, principal_id, "ok", request_payload, result, started)
        return result
    except Exception as exc:
        error = {"status": "error", "error": str(exc), "tool_name": name}
        record_tool_call(workspace_root, name, principal_id, "error", request_payload, error, started, str(exc))
        raise


def raw_call_tool(workspace_root: Path | str, tool: McpToolSpec, args: dict[str, Any], principal_id: str) -> dict[str, Any]:
    from tradingcodex_service.application import audit, orders, policy, portfolio, research

    name = tool.name
    if name == "get_tradingcodex_status":
        from tradingcodex_service.application.runtime import persist_workspace_context_if_available, tradingcodex_db_path
        from tradingcodex_service.version import TRADINGCODEX_VERSION

        return {
            "status": "ok",
            "service": "tradingcodex",
            "version": TRADINGCODEX_VERSION,
            "db_path": str(tradingcodex_db_path()),
            "workspace_context": persist_workspace_context_if_available(workspace_root),
            "mcp_scope": "global-home" if safe_home_mcp_scope() else "project-scoped",
        }
    if name == "simulate_policy":
        return policy.simulate_policy(workspace_root, args)
    if name == "validate_order_intent":
        return orders.validate_order_intent(workspace_root, args)
    if name == "validate_approval_receipt":
        return orders.validate_approval_receipt(workspace_root, args)
    if name == "create_approval_receipt":
        order = orders.resolve_order_intent(Path(workspace_root), args)
        return orders.create_approval_receipt(workspace_root, order, args.get("approved_by") or principal_id, int(args.get("expires_hours") or 24))
    if name == "submit_approved_order":
        return orders.submit_approved_order(workspace_root, {**args, "principal_id": principal_id})
    if name in {"get_positions", "get_portfolio_snapshot"}:
        return portfolio.list_positions(workspace_root)
    if name == "list_workflow_artifacts":
        return research.list_workflow_artifacts(workspace_root)
    if name == "create_research_artifact":
        return research.create_research_artifact(workspace_root, {**args, "principal_id": principal_id})
    if name == "append_research_artifact_version":
        return research.append_research_artifact_version(workspace_root, {**args, "principal_id": principal_id})
    if name == "get_research_artifact":
        return research.get_research_artifact(workspace_root, args)
    if name == "list_research_artifacts":
        return research.list_research_artifacts(workspace_root, args)
    if name == "search_research_artifacts":
        return research.search_research_artifacts(workspace_root, args)
    if name == "export_research_artifact_md":
        return research.export_research_artifact_md(workspace_root, args)
    if name == "record_source_snapshot":
        return research.record_source_snapshot(workspace_root, {**args, "principal_id": principal_id})
    if name == "record_audit_event":
        return audit.write_audit_event(workspace_root, args.get("event") or args, principal_id, "mcp")
    if name == "cancel_approved_order":
        result = {"status": "not_supported", "order_id": args.get("order_id"), "reason": "cancel is a placeholder in the initial harness"}
        audit.write_audit_event(workspace_root, {"type": "cancel_approved_order", "payload": result}, principal_id, "mcp")
        return result
    if name == "get_order_status":
        return {"order_id": args.get("order_id"), "status": "local-only", "note": "Initial harness has no live broker order status."}
    raise ValueError(f"Unknown TradingCodex tool: {name}")


def default_principal_for_tool(tool: McpToolSpec) -> str:
    if "execution-operator" in tool.allowed_roles and len(tool.allowed_roles) == 1:
        return "execution-operator"
    if "risk-manager" in tool.allowed_roles and tool.category == "approvals":
        return "risk-manager"
    if "head-manager" in tool.allowed_roles:
        return "head-manager"
    return sorted(tool.allowed_roles)[0]


def validate_input_schema(tool: McpToolSpec, args: dict[str, Any]) -> None:
    for field in tool.input_schema.get("required", []):
        if args.get(field) in (None, ""):
            raise ValueError(f"{tool.name} requires {field}")


def record_tool_call(
    workspace_root: Path | str | None,
    name: str,
    principal_id: str,
    status: str,
    request_payload: dict[str, Any],
    response_payload: dict[str, Any],
    started: float,
    error: str = "",
) -> None:
    if _skip_db_tool_call_ledger(name):
        return
    try:
        from apps.mcp.models import McpToolCall
        from tradingcodex_service.application.common import stable_hash
        from tradingcodex_service.application.runtime import workspace_context_payload

        McpToolCall.objects.create(
            tool_name=name,
            principal_id=principal_id,
            status=status,
            request=request_payload,
            response=response_payload,
            workspace_context=workspace_context_payload(workspace_root),
            request_hash=stable_hash(request_payload),
            result_hash=stable_hash(response_payload),
            error=error,
            duration_ms=max(0, int((time.monotonic() - started) * 1000)),
        )
    except Exception:
        return


def _skip_db_tool_call_ledger(name: str) -> bool:
    tool = TOOL_REGISTRY.get(name)
    if tool and tool.category == "research":
        return True
    return name == "list_workflow_artifacts"


def handle_mcp_rpc(workspace_root: Path | str, message: dict[str, Any]) -> dict[str, Any] | None:
    from tradingcodex_service.version import TRADINGCODEX_VERSION

    prepare_mcp_runtime(workspace_root)
    method = message.get("method")
    if method == "notifications/initialized":
        return None
    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": message.get("id"),
            "result": {
                "protocolVersion": "2025-06-18",
                "capabilities": {"tools": {"listChanged": False}, "resources": {}, "prompts": {}},
                "serverInfo": {"name": "tradingcodex-home" if safe_home_mcp_scope() else "tradingcodex", "version": TRADINGCODEX_VERSION},
                "instructions": "TradingCodex MCP is a Django service-layer gateway backed by workspace files for agent/skill/research state and the central local TradingCodex DB for runtime ledgers. Codex projects are callers/provenance; research tools use workspace markdown; execution tools revalidate policy, approval, adapter, and audit.",
            },
        }
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": message.get("id"), "result": {"tools": list_mcp_tools()}}
    if method == "resources/list":
        return {"jsonrpc": "2.0", "id": message.get("id"), "result": {"resources": []}}
    if method == "prompts/list":
        return {"jsonrpc": "2.0", "id": message.get("id"), "result": {"prompts": []}}
    if method == "tools/call":
        try:
            params = message.get("params") or {}
            result = call_mcp_tool(workspace_root, params.get("name"), params.get("arguments") or {})
            return {"jsonrpc": "2.0", "id": message.get("id"), "result": {"content": [{"type": "text", "text": json.dumps(result, indent=2, ensure_ascii=False)}]}}
        except Exception as exc:
            return {"jsonrpc": "2.0", "id": message.get("id"), "error": {"code": -32000, "message": str(exc)}}
    return {"jsonrpc": "2.0", "id": message.get("id"), "error": {"code": -32601, "message": f"Method not found: {method}"}}


def handle_mcp_batch(workspace_root: Path | str, payload: Any) -> Any:
    if isinstance(payload, list):
        responses = [handle_mcp_rpc(workspace_root, item) for item in payload]
        return [response for response in responses if response is not None]
    return handle_mcp_rpc(workspace_root, payload)

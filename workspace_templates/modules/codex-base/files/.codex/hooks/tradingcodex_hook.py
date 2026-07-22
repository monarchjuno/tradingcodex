#!/usr/bin/env python3
"""Keep native Codex work native; guard only TradingCodex safety boundaries."""

import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
os.environ.setdefault("TRADINGCODEX_WORKSPACE_ROOT", str(ROOT))

from tradingcodex_service.application.build_gateway import (  # noqa: E402
    MANAGED_SKILL_SCOPES,
    WORKSPACE_PROTECTED_MCP_TOOLS,
    BuildInvocationError,
    issue_build_turn_grant,
    issue_managed_skill_turn_grant,
    reserve_build_turn_use,
    revoke_build_turn_grants,
    validate_build_mcp_permission,
)
from tradingcodex_service.application.common import (  # noqa: E402
    atomic_write_text,
    safe_workspace_path,
    workspace_launcher_command,
)
from tradingcodex_service.application.execution_gateway import (  # noqa: E402
    NativeExecutionInvocationError,
    OrderTurnInFlightError,
    execute_native_execution_mandate,
    issue_order_turn_grant,
    parse_native_execution_invocation,
    reserve_order_turn_grant,
    revoke_order_turn_grants,
)
from tradingcodex_service.application.skill_invocations import (  # noqa: E402
    SkillInvocationError,
    parse_first_meaningful_invocation,
)
from tradingcodex_cli.startup_status import build_server_status  # noqa: E402

HOOK_WRITE_ROOTS = (Path(".tradingcodex/mainagent"), Path("trading/audit"))
ORDER_ALLOW_SKILL = "$tcx-order-allow"
BUILD_SKILL = "$tcx-build"
MANAGED_SKILL_MARKERS = frozenset(MANAGED_SKILL_SCOPES)
BUILD_TURN_GRANT_PROOF_FIELD = "_build_turn_proof"
ORDER_TURN_GRANT_TOOL = "use_order_turn_grant"
ORDER_TURN_GRANT_PROOF_FIELD = "_execution_turn_proof"
NATIVE_EXECUTION_MARKERS = frozenset({
    ORDER_ALLOW_SKILL,
    "$tcx-order-submit",
    "$tcx-order-cancel",
    "$execute-paper-order",
})
AUTHORITY_MARKERS = frozenset({BUILD_SKILL, *MANAGED_SKILL_MARKERS, *NATIVE_EXECUTION_MARKERS})
PROOF_FREE_MANAGED_ACTIONS = frozenset({"list", "inspect", "validate"})
SECRET_PATH = re.compile(
    r"(?:^|[\\/])(?:\.env(?:\.|$)|\.netrc$|id_(?:rsa|ecdsa|ed25519)$|"
    r"credentials?(?:\.json)?$|secrets?(?:\.json)?$|\.aws[\\/])",
    re.I,
)
RAW_CREDENTIAL_ACCESS = re.compile(
    r"(?:^|[\s;&|])(?:cat|head|tail|less|more|sed|awk|grep|rg)\b[^\n]*"
    r"(?:\.env(?:\b|/|\\\\)|\.aws[/\\\\]credentials|\.netrc|\.ssh[/\\\\])|"
    r"\bprintenv\b|\bsecret\.read\b|\$(?:\{|\()?\w*(?:api[_-]?key|api[_-]?secret|"
    r"access[_-]?token|password|cookie)\w*",
    re.I,
)
DIRECT_ORDER_OR_BROKER = re.compile(
    r"(?:use_order_turn_grant|submit_approved_order|cancel_submitted_order|"
    r"raw_order_(?:submit|cancel)|order\.(?:submit|cancel)|broker\s+api)",
    re.I,
)
SERVICE_OWNED_PATH = re.compile(r"(?:^|[\\/])trading[\\/](?:audit|approvals|orders)(?:[\\/]|$)", re.I)
SENSITIVE_ARGUMENT_KEY = re.compile(
    r"(?:api[_-]?key|token|secret|password|passphrase|credential|authorization|cookie|private[_-]?key)",
    re.I,
)
SENSITIVE_ARGUMENT_TEXT = re.compile(
    r"(?i)((?:api[_-]?key|token|secret|password|credential|authorization|cookie)\s*[:=]\s*)([^\s,;&]+)"
)


def main() -> None:
    event = sys.argv[1] if len(sys.argv) > 1 else "unknown"
    payload = read_payload(event)
    if payload is None:
        return
    if event == "session-start":
        session_start(payload)
    elif event == "user-prompt-submit":
        user_prompt_submit(payload)
    elif event in {"pre-tool-use", "permission-request"}:
        policy_gate(event, payload)
    elif event == "stop":
        revoke_stopped_order_grant(payload)


def read_payload(event: str) -> dict | None:
    tool_event = event in {"pre-tool-use", "permission-request"}
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        payload = None
    if not isinstance(payload, dict):
        if tool_event:
            block("TradingCodex safety hook requires a JSON object")
        return None
    if tool_event and (
        not isinstance(payload.get("tool_name"), str)
        or not payload["tool_name"].strip()
        or not isinstance(payload.get("tool_input"), dict)
    ):
        block("TradingCodex safety hook requires tool_name and object tool_input")
        return None
    return payload


def session_start(payload: dict) -> None:
    try:
        status = build_server_status(ROOT, check_latest_release=True)
    except Exception as exc:
        append_hook_audit({"event": "session-start", "warning": "server_status_failed", "error": str(exc)[:180]})
        raise
    context = {
        "marker": "tradingcodex-session-context",
        "service_status": status.get("service_status", "unknown"),
        "restart_codex_required": bool(status.get("restart_codex_required")),
        "planning_instruction": (
            "Answer narrow trusted facts and status requests directly. For investment analysis, begin one "
            "run only when needed and choose the smallest useful role set. Native Codex permissions govern "
            "ordinary workspace work; service calls govern TradingCodex state and final order effects."
        ),
    }
    first_response_notice = update_system_message(status.get("update_status"))
    if payload.get("source") == "startup" and first_response_notice:
        context["first_response_notice"] = first_response_notice
    write_json(ROOT / ".tradingcodex" / "mainagent" / "session-start.json", context)
    append_hook_audit({"event": "session-start", "service_status": context["service_status"], "redacted": True})
    output_context(
        "SessionStart",
        context,
        system_message=session_system_message(status),
    )


def session_system_message(status: object) -> str:
    if not isinstance(status, dict):
        return ""
    messages: list[str] = []
    if status.get("service_status") == "ok":
        dashboard_url = str(status.get("dashboard_url") or "").strip().rstrip("/")
        if dashboard_url:
            messages.append(f"TradingCodex Viewer: {dashboard_url}/ · Wiki: {dashboard_url}/#/wiki")
    update_message = update_system_message(status.get("update_status"))
    if update_message:
        messages.append(update_message)
    return "\n".join(messages)


def update_system_message(update_status: object) -> str:
    if not isinstance(update_status, dict):
        return ""
    if not update_status.get("update_available") or update_status.get("update_recommendation_suppressed"):
        return ""
    workspace_version = str(update_status.get("workspace_version") or "unknown")
    latest_version = str(update_status.get("latest_release_version") or "unknown")
    installed_version = str(update_status.get("installed_version") or "unknown")
    if latest_version not in {"unknown", "not_checked"}:
        version_detail = f"workspace {workspace_version}, latest {latest_version}"
    elif installed_version != "unknown":
        version_detail = f"workspace {workspace_version}, installed {installed_version}"
    else:
        version_detail = f"workspace {workspace_version}"
    launcher = workspace_launcher_command()
    command = (
        f"{launcher} update --from <path-to-tradingcodex>"
        if update_status.get("package_source_requires_explicit")
        else f"{launcher} update"
    )
    return (
        f"TradingCodex update available ({version_detail}). "
        f"From an interactive terminal in this workspace, run `{command}`. "
        "Then fully quit and reopen Codex and start a new task."
    )


def user_prompt_submit(payload: dict) -> None:
    prompt = str(payload.get("prompt") or payload.get("user_prompt") or payload.get("message") or "")
    if not prompt:
        return
    marker = first_authority_marker(prompt)
    is_subagent = bool(payload.get("agent_type") or payload.get("subagent_type"))
    if not is_subagent and not revoke_prior_order_turn(payload, sensitive=bool(marker)):
        return
    if not is_subagent and marker not in {BUILD_SKILL, *MANAGED_SKILL_MARKERS}:
        revoke_prior_workspace_grants(payload)
    if marker == BUILD_SKILL:
        handle_workspace_grant_prompt(payload, prompt, scope="build")
        return
    if marker in MANAGED_SKILL_MARKERS:
        handle_workspace_grant_prompt(payload, prompt, scope=marker.removeprefix("$tcx-"))
        return
    if marker == ORDER_ALLOW_SKILL:
        grant_context = handle_order_allow_prompt(payload, prompt)
        if grant_context:
            output_context("UserPromptSubmit", grant_context)
        return
    if marker:
        handle_native_execution_prompt(payload, prompt)
        return


def first_authority_marker(prompt: str) -> str:
    try:
        invocation = parse_first_meaningful_invocation(prompt, AUTHORITY_MARKERS, workspace_root=ROOT)
    except SkillInvocationError as exc:
        block(str(exc))
        return ""
    return invocation.marker if invocation is not None else ""


def handle_workspace_grant_prompt(payload: dict, prompt: str, *, scope: str) -> None:
    """Issue only the service proof needed by legacy protected MCP operations.

    Native Codex permissions continue to govern ordinary files and tools. This
    narrow compatibility bridge exists because the lifecycle services consume a
    current-turn proof rather than trusting a model-supplied capability claim.
    """
    if payload.get("agent_type") or payload.get("subagent_type"):
        block("Workspace lifecycle grants are accepted only from a root native Codex user turn")
        return
    if permission_mode(payload) in {"plan", "planning"}:
        block("TradingCodex workspace lifecycle grants are unavailable while Codex is in Plan mode")
        return
    session_id = str(payload.get("session_id") or "").strip()
    turn_id = str(payload.get("turn_id") or "").strip()
    cwd = str(payload.get("cwd") or "").strip()
    if not session_id or not turn_id or not cwd:
        block("Workspace lifecycle grants require Codex session_id, turn_id, and cwd bindings")
        return
    try:
        if scope == "build":
            grant = issue_build_turn_grant(
                ROOT,
                prompt,
                session_id=session_id,
                turn_id=turn_id,
                cwd=cwd,
                permission_mode=permission_mode(payload),
            )
        else:
            grant = issue_managed_skill_turn_grant(
                ROOT,
                prompt,
                session_id=session_id,
                turn_id=turn_id,
                cwd=cwd,
                permission_mode=permission_mode(payload),
            )
    except (BuildInvocationError, PermissionError, ValueError) as exc:
        append_hook_audit({"event": "workspace-grant-blocked", "scope": scope, "reason_code": "invalid_invocation", "redacted": True})
        block(str(exc))
        return
    except Exception:
        append_hook_audit({"event": "workspace-grant-blocked", "scope": scope, "reason_code": "service_unavailable", "redacted": True})
        block("TradingCodex workspace lifecycle grant service is unavailable")
        return
    if not isinstance(grant, dict):
        block("Invalid TradingCodex workspace lifecycle invocation")
        return
    context = {
        "marker": "tradingcodex-build-turn" if scope == "build" else "tradingcodex-managed-skill-turn",
        "authority_scope": str(grant.get("authority_scope") or scope),
        "entrypoint": str(grant.get("entrypoint") or (BUILD_SKILL if scope == "build" else f"$tcx-{scope}")),
        "expires_at": str(grant.get("expires_at") or ""),
        "turn_scoped": True,
        "planning_instruction": (
            "Native Codex permissions govern ordinary workspace work. This turn grant is only for the matching "
            "proof-protected TradingCodex lifecycle MCP operation; it grants no broker, secret, order, or subagent authority."
        ),
    }
    append_hook_audit({"event": "workspace-grant-issued", "scope": context["authority_scope"], "redacted": True})
    output_context("UserPromptSubmit", context)


def revoke_prior_order_turn(payload: dict, *, sensitive: bool) -> bool:
    session_id = str(payload.get("session_id") or "").strip()
    if not session_id:
        return True
    try:
        revoke_order_turn_grants(ROOT, session_id, reason="new_user_turn", fail_if_authorizing=sensitive)
    except OrderTurnInFlightError:
        append_hook_audit({"event": "order-turn-grant-in-flight", "redacted": True})
        if sensitive:
            block("A prior TradingCodex order effect is still authorizing; inspect canonical order status first")
            return False
    except Exception:
        append_hook_audit({"event": "order-turn-grant-revoke-failed", "redacted": True})
        if sensitive:
            block("TradingCodex could not safely close prior order turn grants")
            return False
    return True


def revoke_prior_workspace_grants(payload: dict) -> None:
    session_id = str(payload.get("session_id") or "").strip()
    if not session_id:
        return
    try:
        revoke_build_turn_grants(ROOT, session_id, reason="new_user_turn")
    except Exception:
        append_hook_audit({"event": "workspace-grant-revoke-failed", "redacted": True})


def handle_order_allow_prompt(payload: dict, prompt: str) -> dict | None:
    if payload.get("agent_type") or payload.get("subagent_type"):
        block("Order turn grants are accepted only from a root native Codex user turn")
        return None
    if permission_mode(payload) in {"plan", "planning"}:
        block("TradingCodex order execution is unavailable while Codex is in Plan mode")
        return None
    session_id = str(payload.get("session_id") or "").strip()
    turn_id = str(payload.get("turn_id") or "").strip()
    cwd = str(payload.get("cwd") or "").strip()
    if not session_id or not turn_id or not cwd:
        block("Order turn grants require Codex session_id, turn_id, and cwd bindings")
        return None
    try:
        grant = issue_order_turn_grant(ROOT, prompt, session_id=session_id, turn_id=turn_id, cwd=cwd, permission_mode=permission_mode(payload))
    except (NativeExecutionInvocationError, PermissionError, ValueError) as exc:
        append_hook_audit({"event": "order-turn-grant-blocked", "reason_code": "invalid_invocation", "redacted": True})
        block(str(exc))
        return None
    except Exception:
        append_hook_audit({"event": "order-turn-grant-blocked", "reason_code": "service_unavailable", "redacted": True})
        block("TradingCodex order turn grant service is unavailable")
        return None
    append_hook_audit({"event": "order-turn-grant-issued", "mode": str(grant.get("mode") or ""), "redacted": True})
    return {
        "marker": "tradingcodex-order-turn-grant",
        "mode": str(grant.get("mode") or ""),
        "expires_at": str(grant.get("expires_at") or ""),
        "single_use": True,
        "allowed_tool": ORDER_TURN_GRANT_TOOL,
        "planning_instruction": (
            "Use at most one final submit or cancel through use_order_turn_grant after the canonical ticket, "
            "policy, risk, approval, idempotency, and audit gates. Never pass this authority to a subagent."
        ),
    }


def handle_native_execution_prompt(payload: dict, prompt: str) -> None:
    if payload.get("agent_type") or payload.get("subagent_type"):
        block("Native execution actions are accepted only from a root native Codex user turn")
        return
    if permission_mode(payload) in {"plan", "planning"}:
        block("TradingCodex order execution is unavailable while Codex is in Plan mode")
        return
    try:
        mandate = parse_native_execution_invocation(prompt, ROOT)
    except NativeExecutionInvocationError as exc:
        block(str(exc))
        return
    if mandate is None:
        return
    if not append_hook_audit({"event": "native-execution-mandate", **mandate.audit_metadata()}):
        block("TradingCodex execution audit is unavailable; no action was attempted")
        return
    try:
        result = execute_native_execution_mandate(ROOT, mandate)
    except Exception:
        block("TradingCodex execution authorization failed; no action was attempted")
        return
    append_hook_audit({
        "event": "native-execution-result",
        "action": mandate.action,
        "ticket_id": mandate.ticket_id,
        "status": result.get("status", "error"),
        "redacted": True,
    })
    output_context("UserPromptSubmit", {
        "marker": "tradingcodex-native-execution-result",
        "result": result,
        "planning_instruction": "Report this service result only. Do not retry an uncertain action; inspect canonical order status.",
    })


def policy_gate(event: str, payload: dict) -> None:
    tool_name = payload_tool_name(payload)
    if is_order_turn_grant_tool(tool_name):
        handle_order_turn_grant_tool(event, payload)
        return
    if build_protected_mcp_tool_name(tool_name):
        handle_workspace_proof_tool(event, payload)
        return
    if tool_name.lower().startswith("mcp__tradingcodex__"):
        # Canonical services re-authorize every TradingCodex MCP operation.
        return
    reason = native_tool_block_reason(payload)
    if reason:
        append_hook_audit({
            "event": event,
            "tool_name": tool_name,
            "decision": "block",
            "reason": reason,
            "redacted": True,
        })
        block(reason)
        return
    if event == "pre-tool-use" and is_external_evidence_tool(tool_name):
        observe_external_tool_call(tool_name, payload)


def build_protected_mcp_tool_name(tool_name: str) -> str:
    lowered = tool_name.lower()
    prefix = "mcp__tradingcodex__"
    identifier = lowered[len(prefix):] if lowered.startswith(prefix) else lowered
    return identifier if identifier in WORKSPACE_PROTECTED_MCP_TOOLS else ""


def handle_workspace_proof_tool(event: str, payload: dict) -> None:
    identifier = build_protected_mcp_tool_name(payload_tool_name(payload))
    tool_input = payload["tool_input"]
    if (
        identifier in {"manage_investment_brain", "manage_knowledge_wiki"}
        and str(tool_input.get("action") or "") in PROOF_FREE_MANAGED_ACTIONS
    ):
        if BUILD_TURN_GRANT_PROOF_FIELD in tool_input:
            block("Workspace lifecycle proof is hook-owned and is not accepted for read-only management actions")
        return
    if payload.get("agent_type") or payload.get("subagent_type"):
        block("Only root Head Manager may use proof-protected TradingCodex lifecycle MCP tools")
        return
    if permission_mode(payload) in {"plan", "planning"}:
        block("TradingCodex workspace lifecycle tools are unavailable while Codex is in Plan mode")
        return
    session_id = str(payload.get("session_id") or "").strip()
    turn_id = str(payload.get("turn_id") or "").strip()
    if event == "permission-request":
        if not session_id or not turn_id:
            block("Proof-protected MCP permission requires current Codex session and turn bindings")
            return
        try:
            validate_build_mcp_permission(
                ROOT,
                session_id,
                turn_id,
                identifier,
                tool_input,
                permission_mode=permission_mode(payload),
            )
        except (BuildInvocationError, PermissionError, ValueError) as exc:
            append_hook_audit({"event": event, "tool_name": identifier, "decision": "block", "redacted": True})
            block(str(exc))
        except Exception:
            block("TradingCodex workspace lifecycle grant service is unavailable")
        return
    tool_use_id = str(payload.get("tool_use_id") or "").strip()
    if not session_id or not turn_id or not tool_use_id:
        block("Proof-protected MCP use requires current Codex session, turn, and tool-use bindings")
        return
    if BUILD_TURN_GRANT_PROOF_FIELD in tool_input:
        block("Workspace lifecycle proof is hook-owned and cannot be supplied by the model")
        return
    try:
        proof = reserve_build_turn_use(
            ROOT,
            session_id,
            turn_id,
            tool_use_id,
            identifier,
            tool_input,
            permission_mode=permission_mode(payload),
        )
    except (BuildInvocationError, PermissionError, ValueError) as exc:
        append_hook_audit({"event": event, "tool_name": identifier, "decision": "block", "redacted": True})
        block(str(exc))
        return
    except Exception:
        append_hook_audit({"event": event, "tool_name": identifier, "decision": "block", "reason_code": "service_unavailable", "redacted": True})
        block("TradingCodex workspace lifecycle grant service is unavailable")
        return
    rewritten = dict(tool_input)
    rewritten[BUILD_TURN_GRANT_PROOF_FIELD] = proof
    append_hook_audit({"event": event, "tool_name": identifier, "decision": "allow_once", "redacted": True})
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow", "updatedInput": rewritten}}))


def handle_order_turn_grant_tool(event: str, payload: dict) -> None:
    if payload.get("agent_type") or payload.get("subagent_type"):
        block("Only root Head Manager may use the current order turn grant")
        return
    if permission_mode(payload) in {"plan", "planning"}:
        block("TradingCodex order execution is unavailable while Codex is in Plan mode")
        return
    if event == "permission-request":
        return
    session_id = str(payload.get("session_id") or "").strip()
    turn_id = str(payload.get("turn_id") or "").strip()
    tool_use_id = str(payload.get("tool_use_id") or "").strip()
    tool_input = payload["tool_input"]
    if not session_id or not turn_id or not tool_use_id:
        block("Order execution requires current Codex session, turn, and tool-use bindings")
        return
    if ORDER_TURN_GRANT_PROOF_FIELD in tool_input:
        block("Order turn proof is hook-owned and cannot be supplied by the model")
        return
    try:
        proof = reserve_order_turn_grant(ROOT, session_id, turn_id, tool_use_id, tool_input, permission_mode=permission_mode(payload))
    except (PermissionError, ValueError) as exc:
        append_hook_audit({"event": event, "tool_name": ORDER_TURN_GRANT_TOOL, "decision": "block", "redacted": True})
        block(str(exc))
        return
    except Exception:
        append_hook_audit({"event": event, "tool_name": ORDER_TURN_GRANT_TOOL, "decision": "block", "reason_code": "service_unavailable", "redacted": True})
        block("TradingCodex order turn grant service is unavailable")
        return
    rewritten = dict(tool_input)
    rewritten[ORDER_TURN_GRANT_PROOF_FIELD] = proof
    append_hook_audit({"event": event, "tool_name": ORDER_TURN_GRANT_TOOL, "decision": "allow_once", "redacted": True})
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow", "updatedInput": rewritten}}))


def native_tool_block_reason(payload: dict) -> str:
    tool_name = payload_tool_name(payload).lower()
    tool_input = payload["tool_input"]
    serialized = json.dumps(tool_input, ensure_ascii=False)
    if any(SECRET_PATH.search(value) for value in string_values(tool_input)):
        return "TradingCodex native tools cannot read or write secret material"
    if any(SERVICE_OWNED_PATH.search(value) for value in string_values(tool_input)):
        return "TradingCodex order, approval, and audit records are service-owned"
    command = str(tool_input.get("command") or tool_input.get("cmd") or "")
    if RAW_CREDENTIAL_ACCESS.search(command):
        return "TradingCodex native tools cannot read, print, or persist raw credential material"
    if DIRECT_ORDER_OR_BROKER.search(f"{tool_name} {serialized}"):
        return "Direct broker and order effects are blocked; use the canonical TradingCodex service gate"
    return ""


def is_external_evidence_tool(tool_name: str) -> bool:
    lowered = tool_name.lower()
    return (
        (lowered.startswith("mcp__") and not lowered.startswith("mcp__tradingcodex__"))
        or lowered.startswith("web__")
    )


def observe_external_tool_call(tool_name: str, payload: dict) -> None:
    canonical = json.dumps(
        secret_free_tool_input(payload["tool_input"]),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    scope = external_tool_scope(payload)
    append_hook_audit({
        "event": "external-tool-observed",
        "tool_name": tool_name,
        "arguments_sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        **scope,
        "outcome": "unknown",
        "redacted": True,
    })


def external_tool_scope(payload: dict) -> dict:
    session_id = payload_scope_value(payload, "session_id", "sessionId")
    turn_id = payload_scope_value(payload, "turn_id", "turnId")
    if not session_id or not turn_id:
        return {"scope": "unknown"}
    scope = {"session_id": session_id, "turn_id": turn_id}
    for key, aliases in (
        ("agent_id", ("agent_id", "agentId")),
        ("child_id", ("child_id", "childId", "subagent_id", "subagentId")),
        ("agent_type", ("agent_type", "subagent_type")),
    ):
        if value := payload_scope_value(payload, *aliases):
            scope[key] = value
    canonical = json.dumps(scope, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {"scope": "opaque", "scope_sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest()}


def payload_scope_value(payload: dict, *keys: str) -> str:
    for key in keys:
        if value := str(payload.get(key) or "").strip():
            return value
    return ""


def secret_free_tool_input(value):
    if isinstance(value, dict):
        return {
            str(key): "<redacted>" if sensitive_argument_key(str(key)) else secret_free_tool_input(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        redacted = []
        redact_next = False
        for item in value:
            if redact_next:
                redacted.append("<redacted>")
                redact_next = False
                continue
            redacted.append(secret_free_tool_input(item))
            redact_next = isinstance(item, str) and sensitive_argument_key(item.lstrip("-"))
        return redacted
    if isinstance(value, tuple):
        return secret_free_tool_input(list(value))
    if isinstance(value, str):
        return SENSITIVE_ARGUMENT_TEXT.sub(r"\1<redacted>", re.sub(r"(?i)(bearer\s+)[^\s,;]+", r"\1<redacted>", value))
    return value


def sensitive_argument_key(key: str) -> bool:
    return key.lower() == "env" or bool(SENSITIVE_ARGUMENT_KEY.search(key))


def revoke_stopped_order_grant(payload: dict) -> None:
    session_id = str(payload.get("session_id") or "").strip()
    if not session_id:
        return
    try:
        revoked = revoke_order_turn_grants(ROOT, session_id, turn_id=str(payload.get("turn_id") or "") or None, reason="turn_stopped")
    except Exception:
        append_hook_audit({"event": "order-turn-grant-revoke-failed", "redacted": True})
        return
    if revoked:
        append_hook_audit({"event": "order-turn-grant-revoked", "count": revoked, "redacted": True})
    try:
        workspace_revoked = revoke_build_turn_grants(
            ROOT,
            session_id,
            turn_id=str(payload.get("turn_id") or "") or None,
            reason="turn_stopped",
        )
    except Exception:
        append_hook_audit({"event": "workspace-grant-revoke-failed", "redacted": True})
        return
    if workspace_revoked:
        append_hook_audit({"event": "workspace-grant-revoked", "count": workspace_revoked, "redacted": True})


def payload_tool_name(payload: dict) -> str:
    return str(payload.get("tool_name") or "")


def is_order_turn_grant_tool(tool_name: str) -> bool:
    return tool_name.lower() in {ORDER_TURN_GRANT_TOOL, f"mcp__tradingcodex__{ORDER_TURN_GRANT_TOOL}"}


def permission_mode(payload: dict) -> str:
    return str(payload.get("permission_mode") or payload.get("permissionMode") or "").strip().lower().replace("_", "-")


def string_values(value) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [item for child in value.values() for item in string_values(child)]
    if isinstance(value, list):
        return [item for child in value for item in string_values(child)]
    return []


def output_context(event_name: str, context: dict, *, system_message: str = "") -> None:
    output = {
        "hookSpecificOutput": {
            "hookEventName": event_name,
            "additionalContext": json.dumps(context, ensure_ascii=False),
        }
    }
    if system_message:
        output["systemMessage"] = system_message
    print(json.dumps(output, ensure_ascii=False))


def block(reason: str) -> None:
    print(json.dumps({"decision": "block", "reason": reason}))


def append_hook_audit(record: dict) -> bool:
    try:
        append_jsonl(ROOT / "trading" / "audit" / "codex-hooks.jsonl", {"ts": now(), **record})
    except Exception:
        return False
    return True


def now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path: Path, value) -> None:
    target = safe_hook_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(target, json.dumps(value, indent=2, ensure_ascii=False) + "\n")


def append_jsonl(path: Path, value) -> None:
    target = safe_hook_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(value, ensure_ascii=False) + "\n")


def safe_hook_path(path: Path) -> Path:
    lexical = path if path.is_absolute() else ROOT / path
    relative = lexical.relative_to(ROOT)
    current = ROOT
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            raise ValueError("TradingCodex hook state path must not contain symlinks")
    return safe_workspace_path(ROOT, relative.as_posix(), allowed_roots=HOOK_WRITE_ROOTS)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        if len(sys.argv) > 1 and sys.argv[1] in {"pre-tool-use", "permission-request", "user-prompt-submit"}:
            block("TradingCodex safety hook could not evaluate this request")
        else:
            raise

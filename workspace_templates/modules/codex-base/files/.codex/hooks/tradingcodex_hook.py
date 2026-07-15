#!/usr/bin/env python3
import hashlib
import json
import os
import re
import shlex
import sys
import tomllib
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRATCH_ROOT = Path({{TRADINGCODEX_SCRATCH_PATH_PYTHON}}).resolve()
os.environ.setdefault("TRADINGCODEX_WORKSPACE_ROOT", str(ROOT))

from tradingcodex_service.application.agents import EXPECTED_SUBAGENTS  # noqa: E402
from tradingcodex_service.application.analysis_runs import (  # noqa: E402
    explicit_investment_brain_invocation,
    new_analysis_run_id,
    read_analysis_run,
)
from tradingcodex_service.application.build_gateway import (  # noqa: E402
    MANAGED_SKILL_SCOPES,
    BUILD_OPERATOR_ONLY_MCP_TOOLS,
    WORKSPACE_PROTECTED_MCP_TOOLS,
    BuildInvocationError,
    authorize_local_build_tool,
    issue_build_turn_grant,
    issue_managed_skill_turn_grant,
    reserve_build_turn_use,
    revoke_build_turn_grants,
    validate_build_mcp_permission,
    validate_local_build_permission,
)
from tradingcodex_service.application.common import atomic_write_text, safe_workspace_path  # noqa: E402
from tradingcodex_service.application.execution_gateway import (  # noqa: E402
    NativeExecutionInvocationError,
    OrderTurnInFlightError,
    execute_native_execution_mandate,
    issue_order_turn_grant,
    parse_native_execution_invocation,
    reserve_order_turn_grant,
    reserved_native_execution_token,
    revoke_order_turn_grants,
)
from tradingcodex_service.application.investment_brains import (  # noqa: E402
    resolve_sealed_investment_brain_reference,
)
from tradingcodex_cli.startup_status import build_server_status  # noqa: E402

MAX_SESSION_EVENTS = 12
MAX_COMPLETED_RECORDS = 12
SESSION_RUNS_PATH = ROOT / ".tradingcodex" / "mainagent" / "session-workflow-runs.json"
HOOK_WRITE_ROOTS = (Path(".tradingcodex/mainagent"), Path("trading/audit"))
SENSITIVE_ACTION_MARKERS = ("broker api", "api_key", "secret.read", "cash.withdraw", "policy.write")
SHELL_TOOL_NAMES = frozenset({"bash", "shell", "exec_command", "write_stdin", "unified_exec"})
AGENT_RUNTIME_TCX_MUTATION = re.compile(
    r"(?<![\w.-])(?:\./)?tcx(?:\.cmd)?\s+(?:"
    r"workflow\b|"
    r"__hook\b|"
    r"subagents\s+(?:loop|plan)\b|"
    r"mcp\s+call\b|"
    r"connectors\s+(?:connect|scaffold|register|validate|approve-provider|revoke-provider)\b|"
    r"research\s+(?:"
    r"create|append|export|run-card|validation-card|causal-analysis|judgment-prior|judgment-review|"
    r"index\s+rebuild|spec\s+create|replay\s+create|experiment\s+record"
    r")\b|"
    r"forecast\s+(?:issue|revise|resolve|score)\b|"
    r"evaluation\b|"
    r"validate\s+order\b|risk-check\b|approve\b"
    r")",
    re.I,
)
AGENT_RUNTIME_TCX_IDENTITY_OVERRIDE = re.compile(
    r"(?<![\w.-])(?:\./)?tcx(?:\.cmd)?\b(?:(?![;&|]).)*"
    r"--(?:principal|approved-by|created-by)(?:\s|=|$)",
    re.I,
)
PROTECTED_AGENT_RUNTIME_PATH = re.compile(
    r"(?<![\w-])(?:\.git|\.tradingcodex|\.codex|\.agents|AGENTS\.md|"
    r"tradingcodex_cli|tradingcodex_service|workspace_templates)(?![\w-])",
    re.I,
)
PROTECTED_BUILD_EDIT_PATH = re.compile(
    r"^(?:\.git(?:/|$)|\.gitignore$|\.env(?:\.|$)|\.envrc$|\.netrc$|\.npmrc$|\.pypirc$|"
    r"\.ssh(?:/|$)|\.aws(?:/|$)|"
    r"\.tradingcodex(?:/|$)|\.codex(?:/|$)|\.agents(?:/|$)|"
    r"AGENTS\.md$|tcx(?:\.cmd)?$|"
    r"trading/(?:audit|approvals|orders)(?:/|$)|"
    r"tradingcodex_cli(?:/|$)|tradingcodex_service(?:/|$)|workspace_templates(?:/|$))",
    re.I,
)
RAW_CREDENTIAL_ACCESS = re.compile(
    r"(?:^|[\s;&|])(?:cat|head|tail|less|more|sed|awk|grep|rg|cp|mv)\b[^\n]*(?:\.env(?:\b|/|\\)|"
    r"\.aws[/\\]credentials|\.netrc|\.ssh[/\\])|\bprintenv\b|\bsecret\.read\b|"
    r"\$(?:\{|\()?\w*(?:api[_-]?key|api[_-]?secret|access[_-]?token|password|cookie)\w*",
    re.I,
)
ORDER_EFFECT_MATERIAL = re.compile(
    r"(?:\$tcx-order-(?:allow|submit|cancel)|use_order_turn_grant|"
    r"submit_approved_order|cancel_submitted_order|raw_order_(?:submit|cancel)|"
    r"broker\s+api|order\.(?:submit|cancel))",
    re.I,
)
REMOTE_PUBLICATION_MATERIAL = re.compile(
    r"(?<![\w.-])(?:git\s+push\b|git\s+remote\s+(?:add|set-url|remove|rename)\b|"
    r"gh\s+(?:pr|release|repo)\s+create\b|npm\s+publish\b|uv\s+publish\b|"
    r"twine\s+upload\b|docker\s+push\b)",
    re.I,
)
BUILD_SKILL = "$tcx-build"
MANAGED_SKILL_MARKERS = frozenset(MANAGED_SKILL_SCOPES)
BUILD_TURN_GRANT_PROOF_FIELD = "_build_turn_proof"
ORDER_ALLOW_SKILL = "$tcx-order-allow"
ORDER_TURN_GRANT_TOOL = "use_order_turn_grant"
ORDER_TURN_GRANT_PROOF_FIELD = "_execution_turn_proof"


def payload_permission_mode(payload: dict) -> str:
    raw = payload.get("permission_mode")
    if raw in (None, ""):
        raw = payload.get("permissionMode")
    return str(raw or "").strip().lower().replace("_", "-")


def plan_mode_block_reason(payload: dict) -> str:
    if payload_permission_mode(payload) in {"plan", "planning"}:
        return "TradingCodex managed workspace changes are unavailable while Codex is in Plan mode"
    return ""


def main() -> None:
    event = sys.argv[1] if len(sys.argv) > 1 else "unknown"
    tool_gate = event in {"pre-tool-use", "permission-request"}
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        if tool_gate:
            print(json.dumps({"decision": "block", "reason": "TradingCodex tool policy requires valid JSON input"}))
            return
        payload = {}
    if not isinstance(payload, dict):
        if tool_gate:
            print(json.dumps({"decision": "block", "reason": "TradingCodex tool policy requires a JSON object"}))
            return
        payload = {}
    if tool_gate and (
        not isinstance(payload.get("tool_name"), str)
        or not payload["tool_name"].strip()
        or not isinstance(payload.get("tool_input"), dict)
    ):
        print(json.dumps({"decision": "block", "reason": "TradingCodex tool policy requires tool_name and object tool_input"}))
        return
    if event == "session-start":
        session_start(payload)
    elif event == "user-prompt-submit":
        user_prompt_submit(payload)
    elif event in {"subagent-start", "subagent-stop"}:
        subagent_session_state(event, payload)
    elif event in {"pre-tool-use", "permission-request"}:
        policy_gate(event, payload)
    elif event == "post-tool-use":
        append_hook_audit({
            "event": event,
            "workflow_run_id": resolve_workflow_run_id(payload),
            "tool_name": payload_tool_name(payload),
            "redacted": True,
        })
    elif event == "stop":
        revoke_stopped_turn_grant(payload)


def session_start(payload: dict) -> None:
    try:
        server_status = build_server_status(ROOT)
    except Exception as exc:
        append_hook_audit({"event": "session-start", "warning": "server status check failed", "error": str(exc)})
        raise
    update_status = server_status["update_status"]
    service_detail = server_status.get("service_detail") or {}
    preallocated_run_id = str(os.environ.get("TRADINGCODEX_WORKFLOW_RUN_ID") or "").strip()
    bound_run = read_analysis_run(ROOT, preallocated_run_id) if preallocated_run_id else {}
    if bound_run:
        routing_status = {
            "run_status": "bound",
            "workflow_run_id": bound_run.get("workflow_run_id") or preallocated_run_id,
            "run_record_path": f".tradingcodex/mainagent/runs/{preallocated_run_id}/run.json",
            "orchestration_owner": "codex-head-manager",
            "investment_brain_binding": bound_run.get("investment_brain_binding") or {},
            "planning_instruction": "Continue this Codex-native analysis run. Choose and revise the role workflow dynamically from the request and accepted artifacts.",
        }
    elif preallocated_run_id:
        routing_status = {
            "run_status": "preallocated_unbound",
            "workflow_run_id": preallocated_run_id,
            "run_start_tool": "begin_analysis_run",
            "orchestration_owner": "codex-head-manager",
            "planning_instruction": "The run id is transport-only. For investment analysis, begin the run, then choose and revise the fixed-role workflow dynamically.",
        }
    else:
        routing_status = {
            "run_status": "unbound",
            "run_start_tool": "begin_analysis_run",
            "orchestration_owner": "codex-head-manager",
            "planning_instruction": "For investment analysis, Head Manager interprets the original request directly, begins a lightweight run, and orchestrates the smallest useful fixed-role team.",
        }
    readiness = {
        "marker": "tradingcodex-session-context",
        "build_authorization": server_status["build_authorization"],
        "managed_skill_authorization": server_status["managed_skill_authorization"],
        "permission_status": server_status["permission_status"],
        "update_status": {
            "update_available": update_status["update_available"],
            "package_update_required": update_status["package_update_required"],
            "package_refresh_user_terminal_required": update_status["package_refresh_user_terminal_required"],
            "workspace_update_required": update_status["workspace_update_required"],
            "can_self_update": update_status["can_self_update"],
            "command": update_status["command"],
            "interactive_user_terminal_command": update_status["interactive_user_terminal_command"],
            "restart_required_after_update": update_status["restart_required_after_update"],
            "blocked_reason": update_status["head_manager_update_blocked_reason"],
        },
        "server_status": {
            "status_path": ".tradingcodex/mainagent/server-status.json",
            "dashboard_url": server_status["dashboard_url"],
            "service_status": server_status["service_status"],
            "service_issue": service_detail.get("issue", ""),
            "service_version": service_detail.get("version", ""),
            "package_version": service_detail.get("package_version", ""),
            "service_db_path": service_detail.get("db_path", ""),
            "expected_db_path": service_detail.get("expected_db_path", ""),
            "next_action": service_detail.get("next_action", ""),
            "startup_notice": server_status.get("startup_notice", ""),
            "restart_codex_required": server_status["restart_codex_required"],
            "recommended_action": server_status["recommended_action"],
        },
        "allowed_next_actions": server_status["allowed_next_actions"],
        "routing_status": routing_status,
    }
    write_json(ROOT / ".tradingcodex" / "mainagent" / "session-start.json", readiness)
    write_json(ROOT / ".tradingcodex" / "mainagent" / "server-status.json", server_status)
    append_hook_audit({"event": "session-start", "readiness": readiness})
    output = {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": json.dumps(readiness, ensure_ascii=False),
        }
    }
    print(json.dumps(output, ensure_ascii=False))


def user_prompt_submit(payload: dict) -> None:
    prompt = payload.get("prompt") or payload.get("user_prompt") or payload.get("message") or ""
    if not prompt:
        return
    agent_type = payload.get("agent_type") or payload.get("subagent_type")
    native_token = reserved_native_execution_token(prompt)
    build_candidate = is_build_invocation_candidate(prompt)
    managed_skill_candidate = is_managed_skill_invocation_candidate(prompt)
    sensitive_candidate = build_candidate or managed_skill_candidate or bool(native_token)
    turn_grant_context = None
    if not agent_type:
        session_id = str(payload.get("session_id") or "").strip()
        if session_id:
            try:
                revoke_order_turn_grants(
                    ROOT,
                    session_id,
                    reason="new_user_turn",
                    fail_if_authorizing=sensitive_candidate,
                )
            except OrderTurnInFlightError:
                append_hook_audit({
                    "event": "order-turn-grant-in-flight",
                    "reason_code": "new_sensitive_turn",
                    "redacted": True,
                })
                if sensitive_candidate:
                    print(json.dumps({
                        "decision": "block",
                        "reason": (
                            "A prior TradingCodex order effect is still authorizing. "
                            "Inspect its canonical order status before starting another managed, Build, or order turn."
                        ),
                    }))
                    return
            except Exception:
                append_hook_audit({
                    "event": "order-turn-grant-revoke-failed",
                    "reason_code": "new_user_turn",
                    "redacted": True,
                })
                if sensitive_candidate:
                    print(json.dumps({
                        "decision": "block",
                        "reason": "TradingCodex could not safely close prior order turn grants",
                    }))
                    return
            if not (build_candidate or managed_skill_candidate):
                try:
                    revoke_build_turn_grants(ROOT, session_id, reason="new_user_turn")
                except Exception:
                    append_hook_audit({
                        "event": "build-turn-grant-revoke-failed",
                        "reason_code": "new_user_turn",
                        "redacted": True,
                    })
                    if sensitive_candidate:
                        print(json.dumps({
                            "decision": "block",
                            "reason": "TradingCodex could not safely close prior workspace turn grants",
                        }))
                        return
    if build_candidate:
        handle_build_prompt(payload, str(prompt))
        return
    if managed_skill_candidate:
        handle_managed_skill_prompt(payload, str(prompt))
        return
    if native_token == ORDER_ALLOW_SKILL:
        turn_grant_context = handle_order_allow_prompt(payload, str(prompt))
        if turn_grant_context is None:
            return
    elif native_token:
        handle_native_execution_prompt(payload, str(prompt))
        return
    if agent_type in EXPECTED_SUBAGENTS:
        return
    try:
        explicit_brain_id = explicit_investment_brain_invocation(str(prompt))
        brain_selection = {
            "status": "explicit" if explicit_brain_id else "baseline",
            "brain_id": explicit_brain_id,
            "validation": "begin_analysis_run",
        }
    except ValueError as exc:
        explicit_brain_id = ""
        brain_selection = {
            "status": "invalid_multiple",
            "brain_id": "",
            "validation": "begin_analysis_run",
            "error": str(exc),
        }
    preallocated_run_id = str(os.environ.get("TRADINGCODEX_WORKFLOW_RUN_ID") or "").strip()
    followup = str(os.environ.get("TRADINGCODEX_WORKFLOW_FOLLOWUP") or "").lower() in {"1", "true", "yes", "on"}
    existing_run = read_analysis_run(ROOT, preallocated_run_id) if preallocated_run_id else {}
    if followup and preallocated_run_id:
        if not existing_run:
            append_hook_audit({"event": "user-prompt-submit", "warning": "preallocated follow-up analysis run is unavailable", "workflow_run_id": preallocated_run_id})
            return
        session_key = event_session_key(payload)
        if session_key:
            remember_session_run(session_key, preallocated_run_id)
        append_hook_audit({"event": "user-prompt-submit", "workflow_run_id": preallocated_run_id, "followup": True, "record_hash": existing_run.get("record_hash", "")})
        followup_context = {
            "marker": "tradingcodex-analysis-followup",
            "workflow_run_id": preallocated_run_id,
            "run_record_path": f".tradingcodex/mainagent/runs/{preallocated_run_id}/run.json",
            "investment_brain_binding": existing_run.get("investment_brain_binding") or {},
            "planning_instruction": "Continue the existing Codex-native analysis. Reassess the next useful role from the follow-up and current artifacts without a server DAG.",
        }
        if turn_grant_context:
            followup_context["order_turn_grant"] = turn_grant_context
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": json.dumps(followup_context, ensure_ascii=False)}}, ensure_ascii=False))
        return
    run_id = preallocated_run_id or str(existing_run.get("workflow_run_id") or "") or new_analysis_run_id()
    session_key = event_session_key(payload)
    if session_key:
        remember_session_run(session_key, run_id)
    prompt_bytes = prompt.encode("utf-8")
    append_hook_audit({
        "event": "user-prompt-submit",
        "workflow_run_id": run_id,
        "orchestration_owner": "codex-head-manager",
        "run_status": "bound" if existing_run else "unbound",
        "prompt_sha256": hashlib.sha256(prompt_bytes).hexdigest(),
        "prompt_bytes": len(prompt_bytes),
        "investment_brain_id": explicit_brain_id,
        "investment_brain_selection_status": brain_selection["status"],
    })
    if existing_run:
        additional_context = {
            "marker": existing_run["marker"],
            "workflow_run_id": run_id,
            "run_status": "bound",
            "run_record_path": f".tradingcodex/mainagent/runs/{run_id}/run.json",
            "investment_brain_binding": existing_run.get("investment_brain_binding") or {},
            "planning_instruction": "Use the existing run binding and orchestrate the workflow dynamically.",
        }
    else:
        additional_context = {
            "marker": "tradingcodex-agentic-analysis",
            "workflow_run_id": run_id,
            "run_status": "unbound",
            "orchestration_owner": "codex-head-manager",
            "run_start_tool": "begin_analysis_run",
            "investment_brain_selection": brain_selection,
            "planning_instruction": "If this is investment analysis, interpret it directly in its original language, call begin_analysis_run once, then choose and revise the smallest useful fixed-role workflow. The hook does not classify intent, select roles, or provide a DAG.",
        }
        if brain_selection["status"] == "invalid_multiple":
            additional_context["planning_instruction"] = (
                "Do not begin analysis or dispatch a role. Multiple explicit Investment Brains are invalid; ask the user to select exactly one."
            )
    if turn_grant_context:
        additional_context["order_turn_grant"] = turn_grant_context
    output = {
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": json.dumps(additional_context, ensure_ascii=False),
        }
    }
    print(json.dumps(output, ensure_ascii=False))


def is_build_invocation_candidate(prompt: object) -> bool:
    raw = str(prompt or "")
    return raw.lstrip().startswith(BUILD_SKILL)


def is_managed_skill_invocation_candidate(prompt: object) -> bool:
    raw = str(prompt or "")
    return any(raw.lstrip().startswith(marker) for marker in MANAGED_SKILL_MARKERS)


def handle_build_prompt(payload: dict, prompt: str) -> None:
    agent_type = str(payload.get("agent_type") or payload.get("subagent_type") or "").strip()
    prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    plan_reason = plan_mode_block_reason(payload)
    if plan_reason:
        append_hook_audit({
            "event": "build-turn-grant-blocked",
            "reason_code": "plan_mode",
            "prompt_sha256": prompt_hash,
            "redacted": True,
        })
        print(json.dumps({"decision": "block", "reason": plan_reason}))
        return
    if agent_type:
        append_hook_audit({
            "event": "build-turn-grant-blocked",
            "reason_code": "non_root_turn",
            "agent_type": agent_type[:80],
            "prompt_sha256": prompt_hash,
        })
        print(json.dumps({
            "decision": "block",
            "reason": "TradingCodex build turns are accepted only from a root native Codex user turn",
        }))
        return
    session_id = str(payload.get("session_id") or "").strip()
    turn_id = str(payload.get("turn_id") or "").strip()
    cwd = str(payload.get("cwd") or "").strip()
    if not session_id or not turn_id or not cwd:
        print(json.dumps({
            "decision": "block",
            "reason": "TradingCodex build turns require Codex session_id, turn_id, and cwd bindings",
        }))
        return
    try:
        grant = issue_build_turn_grant(
            ROOT,
            prompt,
            session_id=session_id,
            turn_id=turn_id,
            cwd=cwd,
            permission_mode=payload_permission_mode(payload),
        )
    except (BuildInvocationError, PermissionError, ValueError) as exc:
        append_hook_audit({
            "event": "build-turn-grant-blocked",
            "reason_code": "invalid_invocation",
            "prompt_sha256": prompt_hash,
        })
        print(json.dumps({"decision": "block", "reason": str(exc)}))
        return
    except Exception:
        append_hook_audit({
            "event": "build-turn-grant-blocked",
            "reason_code": "grant_service_unavailable",
            "prompt_sha256": prompt_hash,
        })
        print(json.dumps({
            "decision": "block",
            "reason": "TradingCodex build turn grant service is unavailable",
        }))
        return
    if not isinstance(grant, dict):
        print(json.dumps({"decision": "block", "reason": "Invalid $tcx-build invocation"}))
        return
    safe_context = {
        "marker": "tradingcodex-build-turn",
        "status": str(grant.get("status") or "active"),
        "expires_at": str(grant.get("expires_at") or ""),
        "turn_scoped": True,
        "planning_instruction": (
            "This root turn may perform workspace-local build work through hook-authorized tools. "
            "Brain and Strategy lifecycle work use their own exact first-line skill turns instead. "
            "Do not pass build authority to a subagent, access raw credentials, modify protected runtime state, "
            "or treat build authority as order submission or cancellation authority."
        ),
    }
    append_hook_audit({
        "event": "build-turn-grant-issued",
        "prompt_sha256": prompt_hash,
        "expires_at": safe_context["expires_at"],
        "redacted": True,
    })
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": json.dumps(safe_context, ensure_ascii=False),
        }
    }, ensure_ascii=False))


def handle_managed_skill_prompt(payload: dict, prompt: str) -> None:
    agent_type = str(payload.get("agent_type") or payload.get("subagent_type") or "").strip()
    prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    plan_reason = plan_mode_block_reason(payload)
    if plan_reason:
        append_hook_audit({
            "event": "managed-skill-turn-grant-blocked",
            "reason_code": "plan_mode",
            "prompt_sha256": prompt_hash,
            "redacted": True,
        })
        print(json.dumps({"decision": "block", "reason": plan_reason}))
        return
    if agent_type:
        append_hook_audit({
            "event": "managed-skill-turn-grant-blocked",
            "reason_code": "non_root_turn",
            "agent_type": agent_type[:80],
            "prompt_sha256": prompt_hash,
        })
        print(json.dumps({
            "decision": "block",
            "reason": "Managed skill turns are accepted only from a root native Codex user turn",
        }))
        return
    session_id = str(payload.get("session_id") or "").strip()
    turn_id = str(payload.get("turn_id") or "").strip()
    cwd = str(payload.get("cwd") or "").strip()
    if not session_id or not turn_id or not cwd:
        print(json.dumps({
            "decision": "block",
            "reason": "Managed skill turns require Codex session_id, turn_id, and cwd bindings",
        }))
        return
    try:
        grant = issue_managed_skill_turn_grant(
            ROOT,
            prompt,
            session_id=session_id,
            turn_id=turn_id,
            cwd=cwd,
            permission_mode=payload_permission_mode(payload),
        )
    except (BuildInvocationError, PermissionError, ValueError) as exc:
        append_hook_audit({
            "event": "managed-skill-turn-grant-blocked",
            "reason_code": "invalid_invocation",
            "prompt_sha256": prompt_hash,
            "redacted": True,
        })
        print(json.dumps({"decision": "block", "reason": str(exc)}))
        return
    except Exception:
        append_hook_audit({
            "event": "managed-skill-turn-grant-blocked",
            "reason_code": "grant_service_unavailable",
            "prompt_sha256": prompt_hash,
            "redacted": True,
        })
        print(json.dumps({
            "decision": "block",
            "reason": "TradingCodex managed skill turn grant service is unavailable",
        }))
        return
    if not isinstance(grant, dict):
        print(json.dumps({"decision": "block", "reason": "Invalid managed skill invocation"}))
        return
    authority_scope = str(grant.get("authority_scope") or "")
    entrypoint = str(grant.get("entrypoint") or "")
    safe_context = {
        "marker": "tradingcodex-managed-skill-turn",
        "authority_scope": authority_scope,
        "entrypoint": entrypoint,
        "status": str(grant.get("status") or "active"),
        "expires_at": str(grant.get("expires_at") or ""),
        "turn_scoped": True,
        "recommended_profile": "trading-research",
        "planning_instruction": (
            f"This root turn may perform only {entrypoint} management through its exact allowlisted source paths "
            "and proof-protected lifecycle MCP tool. "
            "Do not use tcx-build, cross into another managed skill, pass authority to a subagent, "
            "or treat management authority as order, credential, publication, or global-config authority."
        ),
    }
    append_hook_audit({
        "event": "managed-skill-turn-grant-issued",
        "authority_scope": authority_scope,
        "prompt_sha256": prompt_hash,
        "expires_at": safe_context["expires_at"],
        "redacted": True,
    })
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": json.dumps(safe_context, ensure_ascii=False),
        }
    }, ensure_ascii=False))


def handle_order_allow_prompt(payload: dict, prompt: str) -> dict | None:
    agent_type = str(payload.get("agent_type") or payload.get("subagent_type") or "").strip()
    prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    plan_reason = plan_mode_block_reason(payload)
    if plan_reason:
        append_hook_audit({
            "event": "order-turn-grant-blocked",
            "reason_code": "plan_mode",
            "prompt_sha256": prompt_hash,
            "redacted": True,
        })
        print(json.dumps({
            "decision": "block",
            "reason": "TradingCodex order execution is unavailable while Codex is in Plan mode",
        }))
        return None
    if agent_type:
        append_hook_audit({
            "event": "order-turn-grant-blocked",
            "reason_code": "non_root_turn",
            "agent_type": agent_type[:80],
            "prompt_sha256": prompt_hash,
        })
        print(json.dumps({
            "decision": "block",
            "reason": "Order turn grants are accepted only from a root native Codex user turn",
        }))
        return None
    session_id = str(payload.get("session_id") or "").strip()
    turn_id = str(payload.get("turn_id") or "").strip()
    cwd = str(payload.get("cwd") or "").strip()
    if not session_id or not turn_id or not cwd:
        print(json.dumps({
            "decision": "block",
            "reason": "Order turn grants require Codex session_id, turn_id, and cwd bindings",
        }))
        return None
    try:
        grant = issue_order_turn_grant(
            ROOT,
            prompt,
            session_id=session_id,
            turn_id=turn_id,
            cwd=cwd,
            permission_mode=payload_permission_mode(payload),
        )
    except (NativeExecutionInvocationError, PermissionError, ValueError) as exc:
        append_hook_audit({
            "event": "order-turn-grant-blocked",
            "reason_code": "invalid_invocation",
            "prompt_sha256": prompt_hash,
        })
        print(json.dumps({"decision": "block", "reason": str(exc)}))
        return None
    except Exception:
        append_hook_audit({
            "event": "order-turn-grant-blocked",
            "reason_code": "grant_service_unavailable",
            "prompt_sha256": prompt_hash,
        })
        print(json.dumps({
            "decision": "block",
            "reason": "TradingCodex order turn grant service is unavailable",
        }))
        return None
    if not isinstance(grant, dict):
        print(json.dumps({"decision": "block", "reason": "Invalid $tcx-order-allow invocation"}))
        return None
    safe_context = {
        "marker": "tradingcodex-order-turn-grant",
        "mode": str(grant.get("mode") or ""),
        "expires_at": str(grant.get("expires_at") or ""),
        "single_use": True,
        "allowed_tool": ORDER_TURN_GRANT_TOOL,
        "planning_instruction": (
            "This turn may use at most one final submit or cancel effect through use_order_turn_grant. "
            "First complete all canonical ticket, policy, risk, approval, connection, live-confirmation, "
            "idempotency, and audit gates. Never pass grant metadata to a subagent or retry an uncertain result."
        ),
    }
    append_hook_audit({
        "event": "order-turn-grant-issued",
        "prompt_sha256": prompt_hash,
        "mode": safe_context["mode"],
        "expires_at": safe_context["expires_at"],
        "redacted": True,
    })
    return safe_context


def revoke_stopped_turn_grant(payload: dict) -> None:
    session_id = str(payload.get("session_id") or "").strip()
    turn_id = str(payload.get("turn_id") or "").strip()
    if not session_id:
        return
    try:
        revoked = revoke_order_turn_grants(
            ROOT,
            session_id,
            turn_id=turn_id or None,
            reason="turn_stopped",
        )
    except Exception:
        append_hook_audit({"event": "order-turn-grant-revoke-failed", "redacted": True})
        revoked = 0
    if revoked:
        append_hook_audit({
            "event": "order-turn-grant-revoked",
            "reason_code": "turn_stopped",
            "count": revoked,
            "redacted": True,
        })
    try:
        build_revoked = revoke_build_turn_grants(
            ROOT,
            session_id,
            turn_id=turn_id or None,
            reason="turn_stopped",
        )
    except Exception:
        append_hook_audit({"event": "build-turn-grant-revoke-failed", "redacted": True})
        return
    if build_revoked:
        append_hook_audit({
            "event": "build-turn-grant-revoked",
            "reason_code": "turn_stopped",
            "count": build_revoked,
            "redacted": True,
        })


def handle_native_execution_prompt(payload: dict, prompt: str) -> None:
    agent_type = str(payload.get("agent_type") or payload.get("subagent_type") or "").strip()
    prompt_hash = hashlib.sha256(prompt.strip().encode("utf-8")).hexdigest()
    plan_reason = plan_mode_block_reason(payload)
    if plan_reason:
        append_hook_audit({
            "event": "native-execution-blocked",
            "reason_code": "plan_mode",
            "prompt_sha256": prompt_hash,
            "redacted": True,
        })
        print(json.dumps({
            "decision": "block",
            "reason": "TradingCodex order execution is unavailable while Codex is in Plan mode",
        }))
        return
    if agent_type:
        append_hook_audit({
            "event": "native-execution-blocked",
            "reason_code": "non_root_turn",
            "agent_type": agent_type[:80],
            "prompt_sha256": prompt_hash,
        })
        print(json.dumps({
            "decision": "block",
            "reason": "Native execution actions are accepted only from a root native Codex user turn",
        }))
        return
    try:
        mandate = parse_native_execution_invocation(prompt, ROOT)
    except NativeExecutionInvocationError as exc:
        append_hook_audit({
            "event": "native-execution-blocked",
            "reason_code": "invalid_invocation",
            "prompt_sha256": prompt_hash,
        })
        print(json.dumps({"decision": "block", "reason": str(exc)}))
        return
    except Exception:
        append_hook_audit({
            "event": "native-execution-blocked",
            "reason_code": "mandate_unavailable",
            "prompt_sha256": prompt_hash,
        })
        print(json.dumps({
            "decision": "block",
            "reason": "TradingCodex could not establish a canonical native execution mandate",
        }))
        return
    if mandate is None:
        return
    if not append_hook_audit({
        "event": "native-execution-mandate",
        **mandate.audit_metadata(),
    }):
        print(json.dumps({
            "decision": "block",
            "reason": "TradingCodex native execution audit is unavailable; no action was attempted",
        }))
        return
    try:
        result = execute_native_execution_mandate(ROOT, mandate)
    except Exception:
        print(json.dumps({
            "decision": "block",
            "reason": "TradingCodex native execution authorization or pre-action audit failed; no action was attempted",
        }))
        return
    append_hook_audit({
        "event": "native-execution-result",
        "action": mandate.action,
        "ticket_id": mandate.ticket_id,
        "prompt_sha256": mandate.prompt_sha256,
        "status": result.get("status", "error"),
        "result_audit": result.get("result_audit", "recorded"),
    })
    additional_context = {
        "marker": "tradingcodex-native-execution-result",
        "result": result,
        "planning_instruction": (
            "Report this already-recorded service result only. Do not begin an analysis run, spawn a role, "
            "call a mutation tool, repeat the action, or infer a retry. If status is needs_review or recovery "
            "is present, tell the user to inspect canonical order status before any new action."
        ),
    }
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": json.dumps(additional_context, ensure_ascii=False),
        }
    }, ensure_ascii=False))


def subagent_session_state(event: str, payload: dict) -> None:
    state_path = ROOT / ".tradingcodex" / "mainagent" / "subagent-session-state.json"
    state = read_json(state_path, {
        "updated_at": None,
        "active": {},
        "completed": [],
        "events": [],
        "event_count_total": 0,
        "completed_count_total": 0,
    })
    run_id = resolve_workflow_run_id(payload)
    role = str(payload.get("agent_type") or "")
    if role not in EXPECTED_SUBAGENTS:
        append_hook_audit({
            "event": event,
            "warning": "subagent event requires an exact registered agent_type",
            "workflow_run_id": run_id,
        })
        return
    event_count_total = int(state.get("event_count_total") or len(state.get("events", [])))
    completed_count_total = int(state.get("completed_count_total") or len(state.get("completed", [])))
    agent_session_id = subagent_session_id(payload, run_id, role)
    active_key = f"{run_id}:{role}:{agent_session_id}"
    existing_role_sessions = [
        item for item in state.get("active", {}).values()
        if item.get("run_id") == run_id and item.get("role") == role
    ] if isinstance(state.get("active"), dict) else []
    record = {
        "event": event,
        "role": role,
        "task_name": payload.get("task_name"),
        "run_id": run_id,
        "agent_session_id": agent_session_id,
        "subagent_continuation": "continues_active_role_session" if event == "subagent-start" and existing_role_sessions else "new_or_reused_unknown",
        "ts": now(),
    }
    if event == "subagent-start":
        state.setdefault("active", {})[active_key] = record
    else:
        state.setdefault("active", {}).pop(active_key, None)
        for key, item in list(state.setdefault("active", {}).items()):
            if item.get("run_id") == run_id and item.get("role") == role and item.get("agent_session_id") == agent_session_id:
                state["active"].pop(key, None)
        state.setdefault("completed", []).append(record)
        state["completed"] = state["completed"][-MAX_COMPLETED_RECORDS:]
        state["completed_count_total"] = completed_count_total + 1
    state.setdefault("events", []).append(record)
    state["events"] = state["events"][-MAX_SESSION_EVENTS:]
    state["event_count_total"] = event_count_total + 1
    state["retention"] = {
        "events": f"last {MAX_SESSION_EVENTS}",
        "completed": f"last {MAX_COMPLETED_RECORDS}",
        "full_event_log": "trading/audit/subagent-session-events.jsonl",
    }
    state["updated_at"] = now()
    write_json(state_path, state)
    append_jsonl(ROOT / "trading" / "audit" / "subagent-session-events.jsonl", record)


def unique(items: list) -> list:
    result = []
    for item in items:
        if item and item not in result:
            result.append(item)
    return result


def event_session_key(payload: dict) -> str:
    for key in ("session_id", "codex_session_id", "conversation_id", "thread_id", "transcript_path"):
        value = payload.get(key)
        if value:
            return f"{key}:{value}"
    session = payload.get("session")
    if isinstance(session, dict) and session.get("id"):
        return f"session.id:{session['id']}"
    return ""


def remember_session_run(session_key: str, run_id: str) -> None:
    mapping = read_json(SESSION_RUNS_PATH, {})
    if not isinstance(mapping, dict):
        mapping = {}
    mapping[session_key] = run_id
    write_json(SESSION_RUNS_PATH, mapping)


def resolve_workflow_run_id(payload: dict) -> str:
    for key in ("workflow_run_id", "run_id", "parent_run_id"):
        if payload.get(key):
            return str(payload[key])
    session_key = event_session_key(payload)
    mapping = read_json(SESSION_RUNS_PATH, {})
    if session_key and isinstance(mapping, dict) and mapping.get(session_key):
        return str(mapping[session_key])
    tool_input = payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else {}
    message = str(tool_input.get("message") or "")
    run_ids = list(dict.fromkeys(re.findall(r"(?<![A-Za-z0-9_-])analysis-[A-Za-z0-9_-]{6,170}(?![A-Za-z0-9_-])", message)))
    if len(run_ids) == 1:
        return run_ids[0]
    return ""


def subagent_session_id(payload: dict, run_id: str, role: str) -> str:
    for key in ("agent_session_id", "subagent_session_id", "subagent_id", "agent_id", "thread_id", "conversation_id"):
        if payload.get(key):
            return str(payload[key])
    return f"{run_id}:{role}"


def policy_gate(event: str, payload: dict) -> None:
    if event == "pre-tool-use" and is_native_spawn_tool(payload_tool_name(payload)):
        reason = dispatch_tool_block_reason(payload)
        audited = append_hook_audit({
            "event": event,
            "workflow_run_id": resolve_workflow_run_id(payload),
            "tool_name": payload_tool_name(payload),
            "decision": "block" if reason else "allow",
            **native_spawn_audit_metadata(payload),
            "redacted": True,
        })
        if not audited:
            print(json.dumps({"decision": "block", "reason": "TradingCodex subagent dispatch audit is unavailable"}))
            return
        if reason:
            print(json.dumps({"decision": "block", "reason": reason}))
        return
    tool_name = payload_tool_name(payload).lower()
    if is_order_turn_grant_tool(tool_name):
        handle_order_turn_grant_tool(event, payload)
        return
    operator_tool = build_operator_only_mcp_tool_name(tool_name)
    if operator_tool:
        append_hook_audit({
            "event": event,
            "tool_name": operator_tool,
            "decision": "block",
            "reason_code": "operator_only_external_mcp",
            "redacted": True,
        })
        print(json.dumps({
            "decision": "block",
            "reason": "External MCP registration, connection checks, discovery, and review require the explicit user-terminal operator workflow",
        }))
        return
    if build_protected_mcp_tool_name(tool_name):
        handle_build_mcp_tool(event, payload)
        return
    if tool_name.startswith("mcp__tradingcodex__"):
        # Project-scoped TradingCodex MCP calls are authenticated and authorized
        # again by the service. Do not mistake negative safety metadata such as
        # blocked_actions=["direct broker API"] for an attempted broker action.
        return
    if is_local_build_tool(payload):
        handle_local_build_tool(event, payload)
        return
    if event in {"pre-tool-use", "permission-request"}:
        reason = native_tool_block_reason(payload)
        if reason:
            append_hook_audit({
                "event": event,
                "workflow_run_id": resolve_workflow_run_id(payload),
                "tool_name": payload_tool_name(payload),
                "decision": "block",
                "reason": reason,
                "redacted": True,
            })
            print(json.dumps({"decision": "block", "reason": reason}))
        return
    text = json.dumps(payload, ensure_ascii=False).lower()
    if any(item in text for item in SENSITIVE_ACTION_MARKERS):
        print(json.dumps({"decision": "block", "reason": "TradingCodex policy gate blocked sensitive request"}))


def payload_tool_name(payload: dict) -> str:
    return str(payload.get("tool_name") or "")[:180]


def is_order_turn_grant_tool(tool_name: str) -> bool:
    lowered = str(tool_name or "").lower()
    return lowered in {
        ORDER_TURN_GRANT_TOOL,
        f"mcp__tradingcodex__{ORDER_TURN_GRANT_TOOL}",
    }


def build_protected_mcp_tool_name(tool_name: str) -> str:
    lowered = str(tool_name or "").lower()
    prefix = "mcp__tradingcodex__"
    identifier = lowered[len(prefix):] if lowered.startswith(prefix) else lowered
    return identifier if identifier in WORKSPACE_PROTECTED_MCP_TOOLS else ""


def build_operator_only_mcp_tool_name(tool_name: str) -> str:
    lowered = str(tool_name or "").lower()
    prefix = "mcp__tradingcodex__"
    identifier = lowered[len(prefix):] if lowered.startswith(prefix) else lowered
    return identifier if identifier in BUILD_OPERATOR_ONLY_MCP_TOOLS else ""


def handle_build_mcp_tool(event: str, payload: dict) -> None:
    identifier = build_protected_mcp_tool_name(payload_tool_name(payload))
    plan_reason = plan_mode_block_reason(payload)
    if plan_reason:
        print(json.dumps({"decision": "block", "reason": plan_reason}))
        return
    agent_type = str(payload.get("agent_type") or payload.get("subagent_type") or "").strip()
    if agent_type:
        print(json.dumps({
            "decision": "block",
            "reason": "Only root Head Manager may use turn-protected TradingCodex MCP tools",
        }))
        return
    session_id = str(payload.get("session_id") or "").strip()
    turn_id = str(payload.get("turn_id") or "").strip()
    tool_use_id = str(payload.get("tool_use_id") or "").strip()
    tool_input = payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else {}
    if event == "permission-request":
        if not session_id or not turn_id:
            print(json.dumps({
                "decision": "block",
                "reason": "Turn-protected MCP permission requires the current Codex session and turn bindings",
            }))
            return
        try:
            validate_build_mcp_permission(
                ROOT,
                session_id,
                turn_id,
                identifier,
                tool_input,
                permission_mode=payload_permission_mode(payload),
            )
        except (BuildInvocationError, PermissionError, ValueError) as exc:
            append_hook_audit({
                "event": event,
                "tool_name": identifier,
                "decision": "block",
                "reason_code": "build_turn_grant_rejected",
                "redacted": True,
            })
            print(json.dumps({"decision": "block", "reason": str(exc)}))
            return
        except Exception:
            print(json.dumps({
                "decision": "block",
                "reason": "TradingCodex workspace turn grant service is unavailable",
            }))
            return
        append_hook_audit({
            "event": event,
            "tool_name": identifier,
            "decision": "defer_to_codex_permission",
            "redacted": True,
        })
        return
    if not session_id or not turn_id or not tool_use_id:
        print(json.dumps({
            "decision": "block",
            "reason": "Turn-protected MCP use requires the current Codex session, turn, and tool-use bindings",
        }))
        return
    if BUILD_TURN_GRANT_PROOF_FIELD in tool_input:
        print(json.dumps({
            "decision": "block",
            "reason": "Workspace turn proof is hook-owned and cannot be supplied by the model",
        }))
        return
    try:
        proof = reserve_build_turn_use(
            ROOT,
            session_id,
            turn_id,
            tool_use_id,
            identifier,
            tool_input,
            permission_mode=payload_permission_mode(payload),
        )
    except (BuildInvocationError, PermissionError, ValueError) as exc:
        append_hook_audit({
            "event": event,
            "tool_name": identifier,
            "decision": "block",
            "reason_code": "build_turn_grant_rejected",
            "redacted": True,
        })
        print(json.dumps({"decision": "block", "reason": str(exc)}))
        return
    except Exception:
        append_hook_audit({
            "event": event,
            "tool_name": identifier,
            "decision": "block",
            "reason_code": "build_turn_grant_service_unavailable",
            "redacted": True,
        })
        print(json.dumps({
            "decision": "block",
            "reason": "TradingCodex workspace turn grant service is unavailable",
        }))
        return
    rewritten = dict(tool_input)
    rewritten[BUILD_TURN_GRANT_PROOF_FIELD] = proof
    append_hook_audit({
        "event": event,
        "tool_name": identifier,
        "decision": "allow_once",
        "redacted": True,
    })
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
            "updatedInput": rewritten,
        }
    }))


def handle_order_turn_grant_tool(event: str, payload: dict) -> None:
    plan_reason = plan_mode_block_reason(payload)
    if plan_reason:
        print(json.dumps({
            "decision": "block",
            "reason": "TradingCodex order execution is unavailable while Codex is in Plan mode",
        }))
        return
    agent_type = str(payload.get("agent_type") or payload.get("subagent_type") or "").strip()
    if agent_type:
        print(json.dumps({
            "decision": "block",
            "reason": "Only root Head Manager may use the current order turn grant",
        }))
        return
    if event == "permission-request":
        append_hook_audit({
            "event": event,
            "tool_name": ORDER_TURN_GRANT_TOOL,
            "decision": "allow_service_revalidation",
            "redacted": True,
        })
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PermissionRequest",
                "decision": {"behavior": "allow"},
            }
        }))
        return
    session_id = str(payload.get("session_id") or "").strip()
    turn_id = str(payload.get("turn_id") or "").strip()
    tool_use_id = str(payload.get("tool_use_id") or "").strip()
    tool_input = payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else {}
    if not session_id or not turn_id or not tool_use_id:
        print(json.dumps({
            "decision": "block",
            "reason": "Order execution requires the current Codex session, turn, and tool-use bindings",
        }))
        return
    if ORDER_TURN_GRANT_PROOF_FIELD in tool_input:
        print(json.dumps({
            "decision": "block",
            "reason": "Order turn proof is hook-owned and cannot be supplied by the model",
        }))
        return
    try:
        proof = reserve_order_turn_grant(
            ROOT,
            session_id,
            turn_id,
            tool_use_id,
            tool_input,
            permission_mode=payload_permission_mode(payload),
        )
    except (PermissionError, ValueError) as exc:
        append_hook_audit({
            "event": event,
            "tool_name": ORDER_TURN_GRANT_TOOL,
            "decision": "block",
            "reason_code": "turn_grant_rejected",
            "redacted": True,
        })
        print(json.dumps({"decision": "block", "reason": str(exc)}))
        return
    except Exception:
        append_hook_audit({
            "event": event,
            "tool_name": ORDER_TURN_GRANT_TOOL,
            "decision": "block",
            "reason_code": "turn_grant_service_unavailable",
            "redacted": True,
        })
        print(json.dumps({
            "decision": "block",
            "reason": "TradingCodex order turn grant service is unavailable",
        }))
        return
    rewritten = dict(tool_input)
    rewritten[ORDER_TURN_GRANT_PROOF_FIELD] = proof
    append_hook_audit({
        "event": event,
        "tool_name": ORDER_TURN_GRANT_TOOL,
        "decision": "allow_once",
        "action": str(tool_input.get("action") or ""),
        "ticket_id": str(tool_input.get("ticket_id") or "")[:160],
        "redacted": True,
    })
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
            "updatedInput": rewritten,
        }
    }))


def is_local_build_tool(payload: dict) -> bool:
    return bool(local_tool_authority_scope(payload))


def local_tool_authority_scope(payload: dict) -> str:
    tool_name = payload_tool_name(payload)
    edit_name = normalized_edit_tool_name(tool_name)
    if edit_name:
        tool_input = payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else {}
        raw_paths = edit_target_paths(tool_input)
        if not raw_paths:
            return "build"
        workspace_root = ROOT.resolve()
        scopes: set[str] = set()
        for raw_path in raw_paths:
            try:
                supplied = Path(raw_path)
                resolved = supplied.resolve(strict=False) if supplied.is_absolute() else (workspace_root / supplied).resolve(strict=False)
                relative = resolved.relative_to(workspace_root).as_posix()
            except (OSError, RuntimeError, ValueError):
                return "build"
            if relative == "investment-brains" or relative.startswith("investment-brains/"):
                scopes.add("brain")
            elif relative == "trading" or relative.startswith("trading/") or PROTECTED_BUILD_EDIT_PATH.search(relative):
                scopes.add("build")
            else:
                scopes.add("ordinary")
        if "brain" in scopes and len(scopes) > 1:
            return "invalid"
        if "brain" in scopes:
            return "brain"
        if "build" in scopes:
            return "build"
        return ""
    shell_name = normalized_shell_tool_name(tool_name)
    if not shell_name or shell_name in {"write_stdin", "unified_exec"}:
        return ""
    tool_input = payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else {}
    command = str(tool_input.get("command") or tool_input.get("cmd") or "")
    if re.search(
        r"(?<![\w.-])investment-brains(?:[/\\]|(?=$|\s|['\"]))",
        command,
        re.I,
    ):
        return "brain"
    if re.match(r"^\s*(?:\./)?tcx(?:\.cmd)?\s+investment-brains(?:\s|$)", command, re.I):
        return "brain"
    if re.match(r"^\s*(?:\./)?tcx(?:\.cmd)?\s+strategies(?:\s|$)", command, re.I):
        return "strategy"
    if re.search(r"(?:^|[\s;&|])(?:\./)?tcx(?:\.cmd)?(?:\s|$)", command, re.I):
        return "build"
    return ""


def local_build_hard_block_reason(payload: dict) -> str:
    tool_name = payload_tool_name(payload).lower()
    tool_input = payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else {}
    normalized_edit_tool = normalized_edit_tool_name(tool_name)
    if normalized_edit_tool in {"edit", "write"}:
        return "TradingCodex Build file changes require native apply_patch so every change remains reviewable"
    edit_path_reason = local_edit_path_reason(tool_name, tool_input)
    if edit_path_reason:
        return edit_path_reason
    serialized = json.dumps(tool_input, ensure_ascii=False)
    shell_name = normalized_shell_tool_name(tool_name)
    shell_payload_reason = shell_tool_payload_reason(tool_name, tool_input)
    if shell_payload_reason:
        return shell_payload_reason
    if shell_name == "write_stdin":
        return "TradingCodex build turns do not permit interactive shell sessions"
    if shell_name == "unified_exec":
        return "TradingCodex build turns do not permit unified or interactive execution"
    shell_command = str(tool_input.get("command") or tool_input.get("cmd") or "") if shell_name else ""
    if re.match(r"^\s*(?:\./)?tcx(?:\.cmd)?\s+investment-brains(?:\s|$)", shell_command, re.I):
        return (
            "Codex-managed Investment Brain lifecycle uses the proof-protected "
            "manage_investment_brain MCP tool; the tcx launcher is a user-terminal fallback"
        )
    if re.match(r"^\s*(?:\./)?tcx(?:\.cmd)?\s+strategies(?:\s|$)", shell_command, re.I):
        return (
            "Codex-managed Strategy lifecycle uses the proof-protected manage_strategy MCP tool; "
            "the tcx launcher is a user-terminal fallback"
        )
    if shell_name and PROTECTED_AGENT_RUNTIME_PATH.search(shell_command):
        return "TradingCodex build turns cannot directly modify protected runtime, hook, generated, role, or implementation paths"
    if shell_command and RAW_CREDENTIAL_ACCESS.search(shell_command):
        return "TradingCodex build turns cannot read, print, or persist raw credential material"
    if shell_command and ORDER_EFFECT_MATERIAL.search(shell_command):
        return "TradingCodex build authority is not order submission or cancellation authority"
    if shell_command and REMOTE_PUBLICATION_MATERIAL.search(shell_command):
        return "TradingCodex build turns cannot publish, push, or reconfigure Git remotes"
    lowered = serialized.lower()
    if shell_name and any(marker in lowered for marker in SENSITIVE_ACTION_MARKERS if marker != "api_key"):
        return "TradingCodex build turns cannot perform sensitive policy, secret, cash, or raw broker actions"
    if shell_command and AGENT_RUNTIME_TCX_IDENTITY_OVERRIDE.search(shell_command):
        return "TradingCodex build turns cannot override a service principal or role identity"
    if re.search(
        r"(?<![\w.-])(?:\./)?tcx(?:\.cmd)?\s+(?:build|mcp)\s+permission\s+(?:approve|deny)\b",
        shell_command,
        re.I,
    ):
        return "External MCP permission decisions remain explicit user authority"
    if re.search(
        r"(?<![\w.-])(?:\./)?tcx(?:\.cmd)?\s+mcp\s+external\s+"
        r"(?:register|check|discover|review-tool)\b",
        shell_command,
        re.I,
    ):
        return "External MCP registration, probing, discovery, and review remain explicit user-terminal authority"
    if re.search(
        r"(?<![\w.-])(?:\./)?tcx(?:\.cmd)?\s+(?:mcp\s+install-global\b|"
        r"build\s+codex-mcp\s+add\b[^\n]*--scope(?:\s+|=)global\b)",
        shell_command,
        re.I,
    ):
        return "TradingCodex build turns are workspace-local and cannot modify global Codex configuration"
    if re.search(
        r"(?<![\w.-])codex\s+mcp\s+(?:add|remove)\b[^\n]*--scope(?:\s+|=)global\b",
        shell_command,
        re.I,
    ):
        return "TradingCodex build turns are workspace-local and cannot modify global Codex configuration"
    if shell_name:
        command = str(tool_input.get("command") or tool_input.get("cmd") or "").strip()
        if not command:
            return "TradingCodex build shell use requires one non-empty command"
        compact = re.sub(r"\s+", " ", command)
        if AGENT_RUNTIME_TCX_MUTATION.search(compact):
            return "TradingCodex build turns cannot use analysis, role, research, forecast, evaluation, connector, order, or approval CLI mutation paths"
        command_reason = build_shell_command_reason(command)
        if command_reason:
            return command_reason
    return ""


def local_edit_path_reason(tool_name: str, tool_input: dict) -> str:
    lowered = normalized_edit_tool_name(tool_name)
    if lowered not in {"apply_patch", "edit", "write"}:
        return ""
    raw_paths = edit_target_paths(tool_input)
    if not raw_paths:
        return "TradingCodex file edits require an explicit workspace target path"
    workspace_root = ROOT.resolve()
    for raw_path in raw_paths:
        if raw_path.startswith(("~", "$")):
            return "TradingCodex file edits must target a canonical workspace path"
        supplied = Path(raw_path)
        if any(part in {"", ".", ".."} for part in supplied.parts):
            return "TradingCodex file edits must target a canonical workspace path"
        try:
            lexical_relative = supplied.relative_to(workspace_root) if supplied.is_absolute() else supplied
            resolved = supplied.resolve(strict=False) if supplied.is_absolute() else (workspace_root / supplied).resolve(strict=False)
            relative = resolved.relative_to(workspace_root)
        except (OSError, RuntimeError, ValueError):
            return "TradingCodex file edits must remain inside the generated workspace"
        if not relative.parts:
            return "TradingCodex file edits must target a file inside the generated workspace"
        if PROTECTED_BUILD_EDIT_PATH.search(relative.as_posix()):
            return "TradingCodex cannot directly edit protected runtime, hook, generated, role, audit, policy, approval, order, or implementation paths"
        current = workspace_root
        for part in lexical_relative.parts[:-1]:
            current /= part
            if current.is_symlink():
                return "TradingCodex file edits cannot traverse a symlinked directory"
        if (workspace_root / lexical_relative).is_symlink():
            return "TradingCodex file edits cannot target a symlink"
    return ""


def edit_target_paths(tool_input: dict) -> list[str]:
    raw_paths: list[str] = []
    for field in ("file_path", "path", "target_path", "destination"):
        value = tool_input.get(field)
        if isinstance(value, str) and value.strip():
            raw_paths.append(value.strip())
    patch_text = str(tool_input.get("command") or tool_input.get("patch") or "")
    raw_paths.extend(
        match.group(1).strip()
        for match in re.finditer(
            r"^\*\*\* (?:Add File|Update File|Delete File|Move to):\s*(.+?)\s*$",
            patch_text,
            re.M,
        )
    )
    return raw_paths


def handle_local_build_tool(event: str, payload: dict) -> None:
    required_scope = local_tool_authority_scope(payload)
    if required_scope == "invalid":
        print(json.dumps({
            "decision": "block",
            "reason": "Investment Brain source edits must not be combined with unrelated workspace changes",
        }))
        return
    plan_reason = plan_mode_block_reason(payload)
    if plan_reason:
        print(json.dumps({"decision": "block", "reason": plan_reason}))
        return
    agent_type = str(payload.get("agent_type") or payload.get("subagent_type") or "").strip()
    if agent_type:
        print(json.dumps({
            "decision": "block",
            "reason": "TradingCodex managed workspace tools are available only to the root Head Manager",
        }))
        return
    reason = local_build_hard_block_reason(payload)
    if reason:
        append_hook_audit({
            "event": event,
            "tool_name": payload_tool_name(payload),
            "decision": "block",
            "reason": reason,
            "redacted": True,
        })
        print(json.dumps({"decision": "block", "reason": reason}))
        return
    session_id = str(payload.get("session_id") or "").strip()
    turn_id = str(payload.get("turn_id") or "").strip()
    tool_use_id = str(payload.get("tool_use_id") or "").strip()
    tool_input = payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else {}
    if not session_id or not turn_id or (event != "permission-request" and not tool_use_id):
        binding_reason = (
            "TradingCodex managed shell use requires the current Codex session, turn, and tool-use bindings"
            if normalized_shell_tool_name(payload_tool_name(payload))
            else "TradingCodex managed tool use requires the current Codex session, turn, and tool-use bindings"
        )
        print(json.dumps({
            "decision": "block",
            "reason": binding_reason,
        }))
        return
    if event == "permission-request":
        try:
            validate_local_build_permission(
                ROOT,
                session_id,
                turn_id,
                payload_tool_name(payload),
                tool_input,
                permission_mode=payload_permission_mode(payload),
                required_scope=required_scope,
            )
        except (BuildInvocationError, PermissionError, ValueError) as exc:
            append_hook_audit({
                "event": event,
                "tool_name": payload_tool_name(payload),
                "decision": "block",
                "reason_code": "workspace_turn_grant_rejected",
                "redacted": True,
            })
            print(json.dumps({"decision": "block", "reason": str(exc)}))
            return
        except Exception:
            append_hook_audit({
                "event": event,
                "tool_name": payload_tool_name(payload),
                "decision": "block",
                "reason_code": "workspace_turn_grant_service_unavailable",
                "redacted": True,
            })
            print(json.dumps({
                "decision": "block",
                "reason": "TradingCodex workspace turn grant service is unavailable",
            }))
            return
        append_hook_audit({
            "event": event,
            "tool_name": payload_tool_name(payload),
            "decision": "defer_to_codex_permission",
            "redacted": True,
        })
        return
    try:
        authorize_local_build_tool(
            ROOT,
            session_id,
            turn_id,
            tool_use_id,
            payload_tool_name(payload),
            tool_input,
            permission_mode=payload_permission_mode(payload),
            required_scope=required_scope,
        )
    except (BuildInvocationError, PermissionError, ValueError) as exc:
        append_hook_audit({
            "event": event,
            "tool_name": payload_tool_name(payload),
            "decision": "block",
            "reason_code": "workspace_turn_grant_rejected",
            "redacted": True,
        })
        print(json.dumps({"decision": "block", "reason": str(exc)}))
        return
    except Exception:
        append_hook_audit({
            "event": event,
            "tool_name": payload_tool_name(payload),
            "decision": "block",
            "reason_code": "workspace_turn_grant_service_unavailable",
            "redacted": True,
        })
        print(json.dumps({
            "decision": "block",
            "reason": "TradingCodex workspace turn grant service is unavailable",
        }))
        return
    append_hook_audit({
        "event": event,
        "tool_name": payload_tool_name(payload),
        "decision": "workspace_turn_validated",
        "authority_scope": required_scope,
        "redacted": True,
    })


def is_native_spawn_tool(tool_name: str) -> bool:
    # Codex V1 reports the unnamespaced tool while MultiAgent V2 currently
    # concatenates the `agents` namespace and tool name in hook events.
    return tool_name.lower() in {"spawn_agent", "agentsspawn_agent"}


def native_spawn_audit_metadata(payload: dict) -> dict:
    """Return structural dispatch evidence without persisting the child brief."""
    tool_input = payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else {}
    message = str(tool_input.get("message") or "")
    message_bytes = message.encode("utf-8")
    return {
        "agent_type": str(tool_input.get("agent_type") or "")[:80],
        "task_name": str(tool_input.get("task_name") or "")[:80],
        "fork_turns": str(tool_input.get("fork_turns") or "")[:20],
        "message_sha256": hashlib.sha256(message_bytes).hexdigest() if message_bytes else "",
        "message_bytes": len(message_bytes),
    }


def dispatch_tool_block_reason(payload: dict) -> str:
    tool_input = payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else {}
    allowed_fields = {"agent_type", "fork_turns", "message", "task_name"}
    unexpected_fields = sorted(set(tool_input) - allowed_fields)
    if unexpected_fields:
        return (
            "TradingCodex subagent dispatch accepts only agent_type, fork_turns, "
            "message, and task_name; model, reasoning, sandbox, and other overrides "
            "are forbidden"
        )
    role = str(tool_input.get("agent_type") or "")
    if role not in EXPECTED_SUBAGENTS:
        return "TradingCodex subagent dispatch requires an exact registered agent_type"
    if tool_input.get("fork_turns") != "none":
        return "TradingCodex subagent dispatch requires fork_turns=none"
    task_name = str(tool_input.get("task_name") or "")
    if not re.fullmatch(r"[a-z0-9_]{1,64}", task_name):
        return "TradingCodex subagent dispatch requires a compact underscore-only task_name"
    run_id = resolve_workflow_run_id(payload)
    if not run_id:
        return "TradingCodex subagent dispatch requires the current analysis run id in its compact message"
    try:
        run = read_analysis_run(ROOT, run_id)
    except (OSError, ValueError):
        return "TradingCodex subagent dispatch requires a valid analysis run binding"
    if not run:
        return "TradingCodex subagent dispatch requires begin_analysis_run before the first role"
    return ""


def normalized_shell_tool_name(tool_name: str) -> str:
    normalized = str(tool_name or "").lower().rsplit("__", 1)[-1].rsplit(".", 1)[-1]
    return normalized if normalized in SHELL_TOOL_NAMES else ""


def normalized_edit_tool_name(tool_name: str) -> str:
    normalized = str(tool_name or "").lower().rsplit("__", 1)[-1].rsplit(".", 1)[-1]
    return normalized if normalized in {"apply_patch", "edit", "write"} else ""


def shell_tool_payload_reason(tool_name: str, tool_input: dict) -> str:
    shell_name = normalized_shell_tool_name(tool_name)
    if not shell_name:
        return ""
    if shell_name == "write_stdin":
        allowed_fields = {"session_id", "chars", "yield_time_ms", "max_output_tokens"}
    else:
        allowed_fields = {"command", "cmd", "workdir", "yield_time_ms", "max_output_tokens"}
    unexpected = sorted(set(tool_input) - allowed_fields)
    if unexpected:
        return "TradingCodex shell policy rejects unsupported fields: " + ", ".join(unexpected)
    workdir = str(tool_input.get("workdir") or "")
    if workdir:
        try:
            resolved = Path(workdir).resolve()
            allowed_roots = (ROOT.resolve(), SCRATCH_ROOT)
            if not any(resolved == root or root in resolved.parents for root in allowed_roots):
                return "TradingCodex shell workdirs must remain in the generated workspace or its dedicated scratch directory"
        except (OSError, RuntimeError, ValueError):
            return "TradingCodex shell working directory is invalid"
    return ""


def build_shell_command_reason(command: str) -> str:
    """Admit only non-extensible managed commands whose effects stay reviewable.

    A general interpreter, test runner, shell script, build system, or shell
    composition operator can hide a direct import of TradingCodex services and
    bypass hook-owned proofs. Provider source is therefore syntax-checked with
    isolated ``py_compile`` only; richer execution remains an explicit user
    terminal workflow.
    """

    if not command or any(character in command for character in ("\x00", "\n", "\r")):
        return "TradingCodex build shell use requires one physical command"
    if any(character in command for character in ("%", "!", "^", "\\")):
        return "TradingCodex build shell commands cannot use platform expansion or escape syntax"
    if re.search(r"[;&|><`$]", command) or has_unquoted_shell_expansion(command):
        return "TradingCodex build shell commands cannot use composition, redirection, or expansion"
    try:
        argv = tuple(shlex.split(command, comments=False, posix=True))
    except ValueError:
        return "TradingCodex build shell command quoting is invalid"
    if not argv:
        return "TradingCodex build shell use requires one non-empty command"

    executable = argv[0].replace("\\", "/")
    if executable == "pwd" and len(argv) == 1:
        return ""
    if executable == "cat" and len(argv) > 1 and all(
        not value.startswith("-") and not build_shell_workspace_path_reason(value)
        for value in argv[1:]
    ):
        return ""
    if executable == "ls":
        paths = [value for value in argv[1:] if not value.startswith("-")]
        options = [value for value in argv[1:] if value.startswith("-")]
        if all(value in {"-a", "-l", "-al", "-la"} for value in options) and all(
            not build_shell_workspace_path_reason(value, allow_workspace_root=True)
            for value in (paths or ["."])
        ):
            return ""
    if executable in {"python", "python3"} and argv[1:5] == ("-I", "-S", "-m", "py_compile"):
        source_paths = argv[5:]
        if source_paths and all(
            value.endswith(".py")
            and not value.startswith("-")
            and not build_shell_workspace_path_reason(
                value,
                allowed_prefix=Path("trading/connectors"),
            )
            for value in source_paths
        ):
            return ""
    if executable in {"./tcx", "tcx.cmd", "./tcx.cmd"}:
        launcher = ROOT / ("tcx.cmd" if executable.endswith(".cmd") else "tcx")
        if launcher.is_file() and not launcher.is_symlink() and build_tcx_argv_allowed(argv[1:]):
            return ""
    return (
        "TradingCodex managed workspace turns block general shell, scripts, tests, and interpreters; "
        "use apply_patch, protected services, trusted tcx commands, workspace reads, or isolated py_compile"
    )


def has_unquoted_shell_expansion(command: str) -> bool:
    quote = ""
    escaped = False
    for character in command:
        if escaped:
            escaped = False
            continue
        if character == "\\" and quote != "'":
            escaped = True
            continue
        if quote:
            if character == quote:
                quote = ""
            continue
        if character in {"'", '"'}:
            quote = character
        elif character in "*?[]{}~":
            return True
    return False


def build_shell_workspace_path_reason(
    raw_path: str,
    *,
    allow_workspace_root: bool = False,
    allowed_prefix: Path | None = None,
) -> str:
    if not raw_path or raw_path.startswith(("~", "$")):
        return "workspace path is not canonical"
    supplied = Path(raw_path)
    if any(part in {"", ".."} for part in supplied.parts):
        return "workspace path is not canonical"
    workspace_root = ROOT.resolve()
    try:
        lexical = supplied.relative_to(workspace_root) if supplied.is_absolute() else supplied
        resolved = supplied.resolve(strict=False) if supplied.is_absolute() else (workspace_root / supplied).resolve(strict=False)
        relative = resolved.relative_to(workspace_root)
    except (OSError, RuntimeError, ValueError):
        return "workspace path escapes the generated workspace"
    if not relative.parts:
        return "" if allow_workspace_root else "workspace root is not a file target"
    if PROTECTED_BUILD_EDIT_PATH.search(relative.as_posix()):
        return "workspace path targets protected TradingCodex state"
    if allowed_prefix is not None:
        try:
            relative.relative_to(allowed_prefix)
        except ValueError:
            return "workspace path is outside the allowed Build validation root"
    current = workspace_root
    for part in lexical.parts:
        if part == ".":
            continue
        current /= part
        if current.is_symlink():
            return "workspace path traverses a symlink"
    return ""


def build_tcx_argv_allowed(argv: tuple[str, ...]) -> bool:
    if not argv:
        return False
    command = argv[0]
    rest = argv[1:]
    if command == "doctor":
        return all(re.fullmatch(r"[A-Za-z0-9_.:/=+-]+", value) for value in rest)
    if command == "update":
        return rest == ("status",) or (
            "--skip-refresh" in rest
            and set(rest).issubset({"--skip-refresh", "--no-doctor"})
        )
    if command == "build":
        if rest in {("status",), ("status", "--json")}:
            return True
        if len(rest) >= 2 and rest[:2] == ("codex-mcp", "discover"):
            flags = rest[2:]
            return len(flags) == len(set(flags)) and set(flags).issubset({"--workspace-only", "--json"})
        if len(rest) >= 2 and rest[:2] == ("codex-mcp", "add"):
            return build_codex_mcp_add_argv_allowed(rest[2:])
        return False
    if command == "skills":
        if not rest:
            return False
        if rest[0] in {"list", "inspect"}:
            return True
        if len(rest) >= 2 and rest[0] == "optional" and rest[1] in {
            "list", "inspect", "create", "update", "activate", "archive", "delete",
        }:
            return build_optional_skill_argv_allowed(rest[1:])
        return False
    if command == "subagents":
        return bool(rest) and rest[0] in {"status", "skills", "context-audit"}
    if command == "connectors":
        return bool(rest) and rest[0] in {"status", "providers", "inspect-provider"}
    if command == "mcp":
        return len(rest) >= 2 and rest[:2] == ("permission", "list")
    if command == "mode":
        return rest == ("status",)
    return False


def build_optional_skill_argv_allowed(argv: tuple[str, ...]) -> bool:
    for index, value in enumerate(argv):
        if value == "--body-file":
            if index + 1 >= len(argv) or build_shell_workspace_path_reason(argv[index + 1]):
                return False
    return True


def build_codex_mcp_add_argv_allowed(argv: tuple[str, ...]) -> bool:
    """Validate the exact workspace-only managed-config CLI grammar."""

    value_options = {
        "--name",
        "--scope",
        "--transport",
        "--command",
        "--url",
        "--args-json",
        "--arg",
        "--env-key",
        "--credential-ref",
    }
    repeatable = {"--arg", "--env-key"}
    seen: set[str] = set()
    values: dict[str, list[str]] = {}
    index = 0
    while index < len(argv):
        option = argv[index]
        if option == "--dry-run":
            if option in seen:
                return False
            seen.add(option)
            index += 1
            continue
        if option not in value_options or index + 1 >= len(argv):
            return False
        value = argv[index + 1]
        if not value or value.startswith("--"):
            return False
        if option in seen and option not in repeatable:
            return False
        seen.add(option)
        values.setdefault(option, []).append(value)
        index += 2
    return bool(values.get("--name")) and values.get("--scope", ["workspace"]) == ["workspace"]


def simple_cat_paths(command: str) -> tuple[str, ...]:
    """Extract paths from a strictly read-only cat bundle.

    Codex commonly batches projected skill reads and inserts literal ``printf``
    headings between them.  Accept that harmless form without accepting a
    general shell pipeline: only ``cat`` commands, optional literal headings,
    and ``&&`` separators are valid.  The caller still validates every path
    against its narrower authority boundary.
    """

    if not command.strip() or any(marker in command for marker in ("\n", "\r", "\x00", "$", "`")):
        return ()
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|<>")
        lexer.whitespace_split = True
        lexer.commenters = ""
        argv = tuple(lexer)
    except ValueError:
        return ()
    if not argv:
        return ()
    segments: list[tuple[str, ...]] = []
    current: list[str] = []
    for token in argv:
        if token == "&&":
            if not current:
                return ()
            segments.append(tuple(current))
            current = []
            continue
        if token in {";", "&", "||", "|", "<", ">", "<<", ">>"}:
            return ()
        current.append(token)
    if not current:
        return ()
    segments.append(tuple(current))

    paths: list[str] = []
    previous_was_heading = False
    for index, segment in enumerate(segments):
        if segment[0] == "cat":
            if len(segment) < 2 or any(arg.startswith("-") for arg in segment[1:]):
                return ()
            paths.extend(segment[1:])
            previous_was_heading = False
            continue
        if segment[0] == "printf":
            heading = segment[1] if len(segment) == 2 else ""
            if (
                index == 0
                or index == len(segments) - 1
                or previous_was_heading
                or not re.fullmatch(r"(?:\\n|[ A-Za-z0-9_.:/-])+", heading)
            ):
                return ()
            previous_was_heading = True
            continue
        return ()
    return tuple(paths)


def session_bound_analysis_run(payload: dict) -> dict:
    session_key = event_session_key(payload)
    if not session_key:
        return {}
    try:
        mapping_path = safe_hook_path(SESSION_RUNS_PATH)
    except ValueError:
        return {}
    if not mapping_path.is_file() or mapping_path.is_symlink():
        return {}
    mapping = read_json(mapping_path, {})
    run_id = str(mapping.get(session_key) or "") if isinstance(mapping, dict) else ""
    if not run_id:
        return {}
    for field in ("workflow_run_id", "run_id", "parent_run_id"):
        if payload.get(field) and str(payload[field]) != run_id:
            return {}
    try:
        run = read_analysis_run(ROOT, run_id)
    except (OSError, ValueError):
        return {}
    if str(run.get("workflow_run_id") or "") != run_id:
        return {}
    return run


def selected_brain_reference_read_allowed(payload: dict, command: str) -> bool:
    paths = simple_cat_paths(command)
    if not paths:
        return False
    run = session_bound_analysis_run(payload)
    if not run:
        return False
    binding = run.get("investment_brain_binding")
    if not isinstance(binding, dict) or not binding.get("brain_id"):
        return False
    try:
        for path in paths:
            resolve_sealed_investment_brain_reference(ROOT, binding, path)
    except (OSError, ValueError):
        return False
    return True


def managed_non_brain_skill_read_allowed(command: str) -> bool:
    paths = simple_cat_paths(command)
    if not paths:
        return False
    skill_root = ROOT / ".agents" / "skills"
    for arg in paths:
        candidate = Path(arg)
        if candidate.is_absolute() or any(part in {"", ".", ".."} for part in candidate.parts):
            return False
        candidate = ROOT / candidate
        try:
            relative = candidate.relative_to(skill_root)
        except ValueError:
            return False
        if len(relative.parts) < 2 or relative.parts[0].startswith("investment-brain-"):
            return False
        skill_relative = Path(*relative.parts[1:])
        if skill_relative != Path("SKILL.md") and not (
            len(skill_relative.parts) >= 2
            and skill_relative.parts[0] == "references"
            and skill_relative.suffix.lower() == ".md"
        ):
            return False
        current = ROOT
        for part in candidate.relative_to(ROOT).parts:
            current /= part
            if current.is_symlink():
                return False
        if not candidate.is_file():
            return False
    return True


def projected_role_skill_read_allowed(payload: dict, command: str) -> bool:
    """Allow a child to read only skills enabled in its exact role config."""

    role = str(payload.get("agent_type") or payload.get("subagent_type") or "").strip()
    if role not in EXPECTED_SUBAGENTS:
        return False
    paths = simple_cat_paths(command)
    if not paths:
        return False
    config_path = ROOT / ".codex" / "agents" / f"{role}.toml"
    if not config_path.is_file() or config_path.is_symlink():
        return False
    try:
        config = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return False
    skill_config = config.get("skills") if isinstance(config.get("skills"), dict) else {}
    blocks = skill_config.get("config") if isinstance(skill_config.get("config"), list) else []
    allowed_roots: list[tuple[Path, Path]] = []
    role_root = (ROOT / ".tradingcodex" / "subagents" / "skills" / role).resolve()
    shared_root = (ROOT / ".tradingcodex" / "subagents" / "skills" / "shared").resolve()
    for block in blocks:
        if not isinstance(block, dict) or block.get("enabled") is False:
            continue
        raw_path = str(block.get("path") or "").strip()
        if not raw_path or Path(raw_path).is_absolute():
            continue
        configured = (config_path.parent / raw_path).resolve(strict=False)
        if configured.name != "SKILL.md" or not configured.is_file():
            continue
        try:
            configured.relative_to(role_root)
        except ValueError:
            try:
                configured.relative_to(shared_root)
            except ValueError:
                continue
        current = ROOT
        symlinked = False
        try:
            relative = configured.relative_to(ROOT.resolve())
        except ValueError:
            continue
        for part in relative.parts:
            current /= part
            if current.is_symlink():
                symlinked = True
                break
        if not symlinked:
            allowed_roots.append((configured, configured.parent / "references"))
    if not allowed_roots:
        return False
    for raw_path in paths:
        candidate_path = Path(raw_path)
        if candidate_path.is_absolute() or any(part in {"", ".", ".."} for part in candidate_path.parts):
            return False
        candidate = (ROOT / candidate_path).resolve(strict=False)
        if not candidate.is_file():
            return False
        permitted = False
        for skill_file, references_root in allowed_roots:
            if candidate == skill_file:
                permitted = True
                break
            try:
                reference = candidate.relative_to(references_root)
            except ValueError:
                continue
            if reference.parts and candidate.suffix.lower() == ".md":
                permitted = True
                break
        if not permitted:
            return False
        current = ROOT
        try:
            relative = candidate.relative_to(ROOT.resolve())
        except ValueError:
            return False
        for part in relative.parts:
            current /= part
            if current.is_symlink():
                return False
    return True


def native_tool_block_reason(payload: dict) -> str:
    tool_name = payload_tool_name(payload).lower()
    tool_input = payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else {}
    serialized = json.dumps(tool_input, ensure_ascii=False).lower()
    if tool_name.startswith("mcp__"):
        return "TradingCodex policy gate blocks direct external MCP tools"
    protected_artifact_roots = (
        "trading/reports/",
        "trading/research/",
        "trading/forecasts/",
        "trading/decisions/",
    )
    protected_write_roots = (*protected_artifact_roots, ".tradingcodex/mainagent/", ".tradingcodex/generated/", ".tradingcodex/schemas/")
    if normalized_edit_tool_name(tool_name):
        if payload_permission_mode(payload) in {"plan", "planning"}:
            return "TradingCodex workspace file edits are unavailable while Codex is in Plan mode"
        if normalized_edit_tool_name(tool_name) in {"edit", "write"}:
            return "TradingCodex workspace file changes require native apply_patch so every change remains reviewable"
        edit_reason = local_edit_path_reason(tool_name, tool_input)
        if edit_reason:
            return edit_reason
        if any(root in serialized for root in protected_write_roots):
            return "TradingCodex workflow state and artifacts are service-owned and must use authenticated TradingCodex MCP tools"
        if any(marker in serialized for marker in SENSITIVE_ACTION_MARKERS):
            return "TradingCodex policy gate blocked a sensitive file-edit request"

    shell_name = normalized_shell_tool_name(tool_name)
    shell_payload_reason = shell_tool_payload_reason(tool_name, tool_input)
    if shell_payload_reason:
        return shell_payload_reason
    if shell_name == "write_stdin":
        return "TradingCodex native analysis does not permit interactive shell sessions"
    command = str(tool_input.get("command") or tool_input.get("cmd") or "") if isinstance(tool_input, dict) else ""
    if shell_name:
        if (
            managed_non_brain_skill_read_allowed(command)
            or projected_role_skill_read_allowed(payload, command)
            or selected_brain_reference_read_allowed(payload, command)
        ):
            return ""
        if not command.strip():
            return "TradingCodex shell execution requires a non-empty command"
        lowered = command.lower()
        compact = re.sub(r"\s+", " ", lowered)
        if RAW_CREDENTIAL_ACCESS.search(command):
            return "TradingCodex native tools cannot read, print, or persist raw credential material"
        if ORDER_EFFECT_MATERIAL.search(command):
            return "TradingCodex native shell authority is not order submission or cancellation authority"
        if REMOTE_PUBLICATION_MATERIAL.search(command):
            return "TradingCodex native tools cannot publish, push, or reconfigure Git remotes"
        if re.search(
            r"(?<![\w.-])codex\s+mcp\s+(?:add|remove)\b[^\n]*--scope(?:\s+|=)global\b",
            command,
            re.I,
        ):
            return "TradingCodex native tools cannot modify global Codex configuration"
        if any(marker in compact for marker in SENSITIVE_ACTION_MARKERS):
            return "TradingCodex policy gate blocked a sensitive shell request"
        if AGENT_RUNTIME_TCX_IDENTITY_OVERRIDE.search(compact) or AGENT_RUNTIME_TCX_MUTATION.search(compact):
            return "TradingCodex agent runtime mutations use authenticated structured MCP tools; role-selecting and state-mutating CLI commands are human/operator surfaces only"
        if PROTECTED_AGENT_RUNTIME_PATH.search(command):
            return "TradingCodex generated state, schemas, indexes, role configs, and implementation source are not an agent runtime API"
        write_markers = (">", "write_text", "write_bytes", " touch ", " tee ", " cp ", " mv ", "sed -i", "install ")
        if any(root in lowered for root in protected_artifact_roots) and any(marker in compact for marker in write_markers):
            return "TradingCodex role and synthesis artifacts must be stored through authenticated TradingCodex MCP tools"
    return ""


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
    flags = os.O_APPEND | os.O_CREAT | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(target, flags, 0o600)
    with os.fdopen(descriptor, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(value, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


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
        event_name = sys.argv[1] if len(sys.argv) > 1 else "unknown"
        if event_name in {"pre-tool-use", "permission-request", "user-prompt-submit"}:
            print(json.dumps({
                "decision": "block",
                "reason": "TradingCodex safety hook could not evaluate this request",
            }))
        else:
            raise

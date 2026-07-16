#!/usr/bin/env python3
import hashlib
import ipaddress
import json
import os
import re
import shlex
import shutil
import stat
import sys
import tomllib
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRATCH_ROOT = Path({{TRADINGCODEX_SCRATCH_PATH_PYTHON}}).resolve()
PROVIDER_SOURCES_ROOT = SCRATCH_ROOT / "provider-sources"
GENERATED_PYTHON = {{TRADINGCODEX_PYTHON_PYTHON}}
GENERATED_PYTHON_COMMAND = GENERATED_PYTHON.replace("\\", "/")
GENERATED_GIT_COMMAND = {{TRADINGCODEX_GIT_COMMAND_PYTHON}}
TRADINGCODEX_HOME_ROOT = Path({{TRADINGCODEX_HOME_PYTHON}}).resolve()
CODEX_HOME_ROOT = Path({{CODEX_HOME_PATH_PYTHON}}).resolve()
MAX_STAGED_GIT_CONFIG_BYTES = 128 * 1024
os.environ.setdefault("TRADINGCODEX_WORKSPACE_ROOT", str(ROOT))

from tradingcodex_service.application.agents import EXPECTED_SUBAGENTS  # noqa: E402
from tradingcodex_service.application.analysis_runs import (  # noqa: E402
    explicit_investment_brain_invocation,
    new_analysis_run_id,
    read_analysis_run,
)
from tradingcodex_service.application.build_gateway import (  # noqa: E402
    MANAGED_SKILL_SCOPES,
    WORKSPACE_PROTECTED_MCP_TOOLS,
    BuildInvocationError,
    authorize_local_build_tool,
    has_active_build_turn_grant,
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
    revoke_order_turn_grants,
)
from tradingcodex_service.application.investment_brains import (  # noqa: E402
    resolve_sealed_investment_brain_reference,
)
from tradingcodex_service.application.skill_invocations import (  # noqa: E402
    SkillInvocationError,
    parse_first_meaningful_invocation,
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
    r"(?<![\w.-])(?:git\s+push\b|git\s+remote\s+(?:add|set-url|remove|rename|set-head|update|prune)\b|"
    r"git(?:\s+-C\s+\S+)?\s+config\b[^\n]*(?:remote\.[^\s=]+\.url|url\.[^\s=]+\.insteadof|"
    r"credential\.[^\s=]+|http\.[^\s=]+\.extraheader)|"
    r"gh\s+(?:pr|release|repo)\s+create\b|npm\s+publish\b|uv\s+publish\b|"
    r"twine\s+upload\b|docker\s+push\b)",
    re.I,
)
NETWORK_PACKAGE_INSTALL = re.compile(
    r"^\s*(?:(?:python(?:3)?\s+-m\s+)?pip(?:3)?\s+install\b|"
    r"uv\s+(?:pip\s+install|tool\s+install)\b|uvx\b|"
    r"npm\s+(?:install|add|ci)\b|npx\b|yarn\s+add\b|pnpm\s+(?:add|install)\b|"
    r"bun\s+(?:add|install|x)\b|cargo\s+install\b|go\s+install\b)",
    re.I,
)
NETWORK_FETCH_EXECUTABLES = frozenset({"curl", "wget", "git"})
PROVIDER_READ_EXECUTABLES = frozenset(
    {"cat", "cmp", "diff", "grep", "head", "ls", "sha256sum", "shasum", "tail", "wc"}
)
VCS_METADATA_NAMES = frozenset({".bzr", ".git", ".hg", ".svn", "_darcs"})
SECRET_LIKE_FILENAME = re.compile(
    r"(?:^|[._-])(?:access[._-]?token|api[._-]?key|auth(?:orization)?|client[._-]?secret|"
    r"cookies?|credentials?|git[._-]?credentials|id[._-]?(?:dsa|ecdsa|ed25519|rsa)|netrc|"
    r"keys?|password|passwd|private[._-]?key|refresh[._-]?token|secrets?|sessions?)(?:[._-]|$)",
    re.I,
)
SENSITIVE_HEADER_NAMES = frozenset(
    {"authorization", "proxy-authorization", "cookie", "x-api-key", "api-key", "app-key", "app-secret"}
)
PRIVATE_HOST_SUFFIXES = (
    ".alt",
    ".home",
    ".home.arpa",
    ".internal",
    ".invalid",
    ".lan",
    ".local",
    ".localdomain",
    ".localhost",
    ".onion",
    ".test",
)
BUILD_SKILL = "$tcx-build"
MANAGED_SKILL_MARKERS = frozenset(MANAGED_SKILL_SCOPES)
BUILD_TURN_GRANT_PROOF_FIELD = "_build_turn_proof"
ORDER_ALLOW_SKILL = "$tcx-order-allow"
AUTHORITY_SKILL_MARKERS = (
    BUILD_SKILL,
    *MANAGED_SKILL_MARKERS,
    ORDER_ALLOW_SKILL,
    "$tcx-order-submit",
    "$tcx-order-cancel",
    "$execute-paper-order",
)
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
    try:
        authority_invocation = parse_first_meaningful_invocation(
            prompt,
            AUTHORITY_SKILL_MARKERS,
            workspace_root=ROOT,
        )
    except SkillInvocationError as exc:
        print(json.dumps({"decision": "block", "reason": str(exc)}))
        return
    authority_marker = authority_invocation.marker if authority_invocation is not None else ""
    native_token = authority_marker if authority_marker in {
        ORDER_ALLOW_SKILL,
        "$tcx-order-submit",
        "$tcx-order-cancel",
        "$execute-paper-order",
    } else ""
    build_candidate = authority_marker == BUILD_SKILL
    managed_skill_candidate = authority_marker in MANAGED_SKILL_MARKERS
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
        explicit_brain_id = explicit_investment_brain_invocation(str(prompt), ROOT)
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
        "build_tools": {
            "provider_sources_workdir": str(PROVIDER_SOURCES_ROOT).replace("\\", "/"),
            "py_compile_interpreter": GENERATED_PYTHON_COMMAND,
            "py_compile_argv": ["-I", "-S", "-m", "py_compile"],
            "workspace_root": str(ROOT.resolve()).replace("\\", "/"),
            "workspace_launchers": list(_generated_workspace_launcher_commands()),
            "absolute_command_proof": {
                "use_when_tool_workdir_is_not_forwarded_to_hooks": True,
                "trusted_executables": {
                    name: command
                    for name in (*NETWORK_FETCH_EXECUTABLES, *PROVIDER_READ_EXECUTABLES)
                    if (command := _trusted_external_command(name))
                },
                "provider_sources_root": str(PROVIDER_SOURCES_ROOT).replace("\\", "/"),
                "requirements": (
                    "Use the advertised absolute executable and absolute staged file paths. "
                    "Every Git command must also use an absolute -C provider_sources_root or "
                    "direct <provider-id> repository; clone uses --no-checkout and curl uses --globoff. "
                    "A fresh HTTP-only provider directory uses curl --create-dirs with one direct "
                    "<provider-id>/<file> output."
                ),
            },
        },
        "planning_instruction": (
            "This root turn may perform workspace-local build work through hook-authorized tools. "
            "Brain and Strategy lifecycle work use their own first-meaningful-line skill turns instead. "
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
    prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
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
    if build_protected_mcp_tool_name(tool_name):
        handle_build_mcp_tool(event, payload)
        return
    if tool_name.startswith("mcp__tradingcodex__"):
        # Project-scoped TradingCodex MCP calls are authenticated and authorized
        # again by the service. Do not mistake negative safety metadata such as
        # blocked_actions=["direct broker API"] for an attempted broker action.
        return
    build_network_reason = build_native_network_tool_block_reason(payload)
    if build_network_reason:
        append_hook_audit({
            "event": event,
            "tool_name": payload_tool_name(payload),
            "decision": "block",
            "reason_code": "build_native_network_tool",
            "redacted": True,
        })
        print(json.dumps({"decision": "block", "reason": build_network_reason}))
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


def build_native_network_tool_block_reason(payload: dict) -> str:
    if not payload_has_build_authority(payload):
        return ""
    tool_name = payload_tool_name(payload).strip().lower()
    if (
        not tool_name
        or normalized_shell_tool_name(tool_name)
        or normalized_edit_tool_name(tool_name)
        or tool_name.startswith("mcp__tradingcodex__")
    ):
        return ""
    normalized_name = re.sub(r"[^a-z0-9]+", "_", tool_name).strip("_")
    network_markers = {
        "browser", "chrome", "computer", "download", "fetch", "http", "https", "internet", "navigate",
        "navigation", "network", "open_url", "playwright", "request", "requests", "url", "url_open",
        "web", "web_search", "websearch",
    }
    name_parts = set(normalized_name.split("_"))
    tool_input = payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else {}
    serialized = json.dumps(tool_input, ensure_ascii=False).lower()
    url_shaped_input = bool(
        re.search(r"https?://", serialized)
        or any(key.lower() in {"host", "href", "uri", "url", "urls"} for key in tool_input)
    )
    if name_parts.intersection(network_markers) or url_shaped_input:
        return (
            "TradingCodex Build permits public network reads only through the exact shell curl, wget, "
            "and read-only HTTPS Git staging lane; browser, web, HTTP, fetch, and navigation tools are blocked"
        )
    return ""


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


def payload_has_build_authority(payload: dict) -> bool:
    """Route an exact Build turn through one hard shell/edit policy.

    The selected profile is a fail-closed signal even before a grant exists.
    The DB-bound check also covers test/legacy clients whose permission mode is
    reported as ``default`` or omitted.
    """

    if payload_permission_mode(payload) == "trading-build":
        return True
    session_id = str(payload.get("session_id") or "").strip()
    turn_id = str(payload.get("turn_id") or "").strip()
    if not session_id or not turn_id:
        return False
    try:
        return has_active_build_turn_grant(ROOT, session_id, turn_id)
    except Exception:
        return False


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
        if payload_has_build_authority(payload):
            return "build"
        return ""
    shell_name = normalized_shell_tool_name(tool_name)
    if not shell_name or shell_name == "write_stdin":
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
    if command.lstrip().lower().startswith(".\\tcx.cmd"):
        return "build"
    if command_references_controlled_workspace(command):
        return "build"
    if re.search(r"(?:^|[\s;&|])(?:\./)?tcx(?:\.cmd)?(?:\s|$)", command, re.I):
        return "build"
    if payload_has_build_authority(payload):
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
    fetch_reason = (
        public_fetch_command_reason(
            shell_command,
            str(tool_input.get("workdir") or ""),
            require_provider_root=True,
        )
        if shell_name
        else None
    )
    if fetch_reason is not None:
        return fetch_reason
    provider_source_reason = (
        provider_sources_shell_command_reason(
            shell_command,
            str(tool_input.get("workdir") or ""),
        )
        if shell_name
        else None
    )
    if provider_source_reason is not None:
        return provider_source_reason
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
        r"(?<![\w.-])(?:\./)?tcx(?:\.cmd)?\s+mcp\s+install-global\b",
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
        command_reason = build_shell_command_reason(
            command,
            str(tool_input.get("workdir") or ""),
        )
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


def command_references_controlled_workspace(command: str) -> bool:
    normalized = str(command or "").replace("\\", "/")
    absolute_trading = str((ROOT / "trading").resolve()).replace("\\", "/")
    references_trading = absolute_trading in normalized or bool(
        re.search(r"(?<![A-Za-z0-9_./:-])(?:\./)?trading(?:/|(?=$|\s|['\"]))", normalized, re.I)
    )
    if not references_trading:
        return False
    if _has_unquoted_network_control(normalized):
        return True
    try:
        argv = tuple(shlex.split(str(command or ""), comments=False, posix=True))
    except ValueError:
        return True
    if not argv:
        return False
    executable = argv[0].replace("\\", "/").rsplit("/", 1)[-1].lower()
    read_only_commands = {
        "cat",
        "cmp",
        "diff",
        "file",
        "grep",
        "head",
        "ls",
        "rg",
        "sha256sum",
        "shasum",
        "stat",
        "tail",
        "wc",
    }
    if executable in read_only_commands:
        if executable == "diff" and any(
            value == "--output" or value.startswith("--output=") for value in argv[1:]
        ):
            return True
        return False
    if executable == "sed":
        return not (
            len(argv) >= 4
            and argv[1] == "-n"
            and re.fullmatch(r"[0-9]+(?:,[0-9]+)?p", argv[2]) is not None
            and all(not value.startswith("-") for value in argv[3:])
        )
    if executable == "find":
        return any(
            value in {
                "-delete",
                "-exec",
                "-execdir",
                "-fls",
                "-fprint",
                "-fprint0",
                "-fprintf",
                "-ok",
                "-okdir",
            }
            for value in argv[1:]
        )
    return True


def public_fetch_command_reason(
    command: str,
    workdir: str = "",
    *,
    require_provider_root: bool = False,
) -> str | None:
    """Validate credential-free public fetch commands without granting write authority."""

    raw = str(command or "").strip()
    if not raw:
        return None
    if NETWORK_PACKAGE_INSTALL.search(raw):
        return "TradingCodex public fetch does not permit network package or dependency installation"
    expansion_safe = _mask_scratch_expansion(raw)
    if _has_unquoted_network_control(expansion_safe):
        if any(
            re.search(rf"(?<![A-Za-z0-9_.-]){name}(?![A-Za-z0-9_.-])", raw)
            for name in NETWORK_FETCH_EXECUTABLES
        ):
            return "TradingCodex public fetch cannot use shell composition, redirection, substitution, or background execution"
        return None
    try:
        argv = tuple(shlex.split(raw, comments=False, posix=True))
    except ValueError:
        return "TradingCodex public fetch command quoting is invalid"
    if not argv:
        return None
    if any(any(ord(character) < 32 or ord(character) == 127 for character in value) for value in argv):
        return "TradingCodex public fetch arguments must not contain control characters"
    if _network_package_install_argv(argv):
        return "TradingCodex public fetch does not permit network package or dependency installation"
    executable_token = argv[0]
    executable = _external_command_name(executable_token)
    if executable not in NETWORK_FETCH_EXECUTABLES:
        if any(
            _external_command_name(value) in NETWORK_FETCH_EXECUTABLES
            for value in argv[1:]
        ):
            return "TradingCodex public fetch command must begin directly with curl, wget, or git"
        return None
    self_contained = require_provider_root and not str(workdir or "").strip()
    if self_contained:
        if not _trusted_external_command_matches(executable_token, executable):
            return (
                "When Codex does not forward tool workdir to hooks, TradingCodex public fetch requires "
                "the hook-advertised absolute trusted executable path"
            )
        cwd = PROVIDER_SOURCES_ROOT
    else:
        if executable_token != executable:
            return "TradingCodex public fetch must use the exact bare curl, wget, or git executable name"
        cwd = _fetch_workdir(workdir)
    if require_provider_root:
        workdir_reason = _safe_provider_sources_workdir_reason(cwd)
        if workdir_reason:
            return workdir_reason
    if executable == "curl":
        return _curl_fetch_reason(
            argv[1:],
            cwd,
            require_absolute_paths=self_contained,
            require_globoff=require_provider_root,
        )
    if executable == "wget":
        return _wget_fetch_reason(argv[1:], cwd, require_absolute_paths=self_contained)
    return _git_fetch_reason(argv[1:], cwd, self_contained=self_contained)


def _external_command_name(token: str) -> str:
    leaf = str(token or "").replace("\\", "/").rsplit("/", 1)[-1].lower()
    for suffix in (".exe", ".cmd", ".com", ".bat"):
        if leaf.endswith(suffix):
            return leaf[: -len(suffix)]
    return leaf


def _has_unquoted_network_control(command: str) -> bool:
    quote = ""
    escaped = False
    index = 0
    while index < len(command):
        character = command[index]
        if escaped:
            escaped = False
            index += 1
            continue
        if character == "\\" and quote != "'":
            escaped = True
            index += 1
            continue
        if quote:
            if quote == '"' and character in {"$", "`"}:
                return True
            if character == quote:
                quote = ""
            index += 1
            continue
        if character in {"'", '"'}:
            quote = character
        elif character in ";&|><`$\n\r":
            return True
        index += 1
    return False


def _mask_scratch_expansion(value: str) -> str:
    return re.sub(
        r"\$(?:TRADINGCODEX_SCRATCH(?=/)|\{TRADINGCODEX_SCRATCH\}(?=/))",
        "TRADINGCODEX_SCRATCH",
        str(value or ""),
    )


def _network_package_install_argv(argv: tuple[str, ...]) -> bool:
    values = list(argv)
    while values and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", values[0], re.S):
        values.pop(0)
    if not values:
        return False
    executable = values[0].replace("\\", "/").rsplit("/", 1)[-1].lower()
    if executable == "env":
        nested = values[1:]
        while nested:
            if nested[0] in {"-u", "--unset"} and len(nested) > 1:
                nested = nested[2:]
            elif nested[0].startswith("--unset=") or nested[0] in {"-i", "--ignore-environment", "-0", "--null"}:
                nested.pop(0)
            elif re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", nested[0], re.S):
                nested.pop(0)
            else:
                break
        return _network_package_install_argv(tuple(nested))
    if executable == "command":
        nested = values[1:]
        while nested and nested[0].startswith("-"):
            nested.pop(0)
        return _network_package_install_argv(tuple(nested))
    if executable == "timeout":
        nested = values[1:]
        while nested and nested[0].startswith("-"):
            nested.pop(0)
        return _network_package_install_argv(tuple(nested[1:])) if len(nested) > 1 else False
    if executable in {"bash", "sh", "zsh"} and len(values) >= 3 and values[1] in {"-c", "-lc"}:
        try:
            nested = tuple(shlex.split(values[2], comments=False, posix=True))
        except ValueError:
            return True
        return bool(NETWORK_PACKAGE_INSTALL.search(values[2]) or _network_package_install_argv(nested))
    args = [value.lower() for value in values[1:]]
    if executable in {"pip", "pip3"}:
        return bool(args[:1] == ["install"])
    if executable in {"python", "python3"}:
        return args[:3] in (["-m", "pip", "install"], ["-m", "pip3", "install"])
    if executable == "uv":
        return args[:2] in (["pip", "install"], ["tool", "install"])
    if executable in {"uvx", "npx"}:
        return True
    if executable == "npm":
        return bool(args[:1] and args[0] in {"install", "add", "ci"})
    if executable == "yarn":
        return bool(args[:1] == ["add"])
    if executable == "pnpm":
        return bool(args[:1] and args[0] in {"add", "install"})
    if executable == "bun":
        return bool(args[:1] and args[0] in {"add", "install", "x"})
    return executable in {"cargo", "go"} and bool(args[:1] == ["install"])


def _fetch_workdir(raw: str) -> Path:
    if not raw:
        return ROOT.resolve()
    try:
        supplied = Path(raw).expanduser()
        return supplied if supplied.is_absolute() else ROOT / supplied
    except (OSError, RuntimeError, ValueError):
        return ROOT.resolve()


def _provider_source_path_allowed(raw_path: str, cwd: Path, *, allow_stdout: bool = False) -> bool:
    value = str(raw_path or "").strip()
    if allow_stdout and value == "-":
        return True
    value = value.replace("${TRADINGCODEX_SCRATCH}", str(SCRATCH_ROOT), 1) if value.startswith(
        "${TRADINGCODEX_SCRATCH}/"
    ) else value
    value = value.replace("$TRADINGCODEX_SCRATCH", str(SCRATCH_ROOT), 1) if value.startswith(
        "$TRADINGCODEX_SCRATCH/"
    ) else value
    try:
        supplied = Path(value).expanduser()
        resolved = supplied.resolve(strict=False) if supplied.is_absolute() else (cwd / supplied).resolve(strict=False)
        relative = resolved.relative_to(PROVIDER_SOURCES_ROOT)
        return bool(relative.parts and re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", relative.parts[0]))
    except (OSError, RuntimeError, ValueError):
        return False


def _path_is_link_like(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
        is_junction = getattr(path, "is_junction", None)
        if callable(is_junction) and is_junction():
            return True
        attributes = getattr(os.lstat(path), "st_file_attributes", 0)
        return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))
    except OSError:
        return False


def _generated_python_is_trusted() -> bool:
    declared = Path(GENERATED_PYTHON)
    if not declared.is_absolute():
        return False
    try:
        resolved = declared.resolve(strict=True)
        metadata = os.stat(resolved)
    except (OSError, RuntimeError, ValueError):
        return False
    if not stat.S_ISREG(metadata.st_mode) or not os.access(resolved, os.X_OK):
        return False
    declared_absolute = declared.absolute()
    forbidden_roots = (ROOT.resolve(), SCRATCH_ROOT, CODEX_HOME_ROOT)
    if any(
        candidate == root or root in candidate.parents
        for candidate in (declared_absolute, resolved)
        for root in forbidden_roots
    ):
        return False
    managed_runtime_root = TRADINGCODEX_HOME_ROOT / "runtime" / "python"
    if declared_absolute == TRADINGCODEX_HOME_ROOT or TRADINGCODEX_HOME_ROOT in declared_absolute.parents:
        if declared_absolute != managed_runtime_root and managed_runtime_root not in declared_absolute.parents:
            return False
    if resolved == TRADINGCODEX_HOME_ROOT or TRADINGCODEX_HOME_ROOT in resolved.parents:
        return resolved == managed_runtime_root or managed_runtime_root in resolved.parents
    return True


def _trusted_external_command(name: str) -> str:
    """Return a cwd-independent executable path for the workdir-less proof lane."""

    candidate = GENERATED_GIT_COMMAND if name == "git" else shutil.which(str(name or ""))
    if not candidate:
        return ""
    declared = Path(candidate).expanduser()
    if not declared.is_absolute():
        declared = Path(os.path.abspath(declared))
    try:
        resolved = declared.resolve(strict=True)
        metadata = os.stat(resolved)
    except (OSError, RuntimeError, ValueError):
        return ""
    if not stat.S_ISREG(metadata.st_mode) or not os.access(resolved, os.X_OK):
        return ""
    forbidden_roots = (
        ROOT.resolve(),
        SCRATCH_ROOT,
        CODEX_HOME_ROOT,
        TRADINGCODEX_HOME_ROOT,
    )
    declared_absolute = declared.absolute()
    if any(
        candidate_path == root or root in candidate_path.parents
        for candidate_path in (declared_absolute, resolved)
        for root in forbidden_roots
    ):
        return ""
    return str(declared_absolute).replace("\\", "/")


def _trusted_external_command_matches(token: str, name: str) -> bool:
    trusted = _trusted_external_command(name)
    return bool(trusted and str(token or "").replace("\\", "/") == trusted)


def _generated_workspace_launcher_commands() -> tuple[str, ...]:
    commands: list[str] = []
    for name in ("tcx", "tcx.cmd"):
        candidate = ROOT / name
        try:
            metadata = os.lstat(candidate)
        except OSError:
            continue
        attributes = int(getattr(metadata, "st_file_attributes", 0) or 0)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        ):
            continue
        commands.append(str(candidate.absolute()).replace("\\", "/"))
    return tuple(commands)


def _path_has_symlink_below(root: Path, target: Path, *, include_target: bool = True) -> bool:
    try:
        relative = target.relative_to(root)
    except ValueError:
        return True
    current = root
    parts = relative.parts if include_target else relative.parts[:-1]
    for part in parts:
        current /= part
        if _path_is_link_like(current):
            return True
    return False


def _secret_like_filename(path: Path) -> bool:
    name = path.name.strip().lower()
    return bool(
        not name
        or name.startswith(".env")
        or name in {".netrc", "_netrc", ".npmrc", ".pypirc", "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519"}
        or path.suffix.lower() in {".der", ".jks", ".key", ".keystore", ".p12", ".pfx", ".pkcs12", ".pem"}
        or SECRET_LIKE_FILENAME.search(name)
    )


def _public_download_destination_reason(raw_path: str, cwd: Path) -> str:
    value = str(raw_path or "").strip()
    if value == "-":
        return ""
    if any(character in value for character in "*?[]{}"):
        return "TradingCodex public fetch destination must not use shell pathname expansion"
    if re.search(r"#[0-9]+", value):
        return "TradingCodex public fetch rejects curl output template placeholders"
    lexical = _lexical_fetch_path(value, cwd)
    try:
        lexical_relative = lexical.relative_to(PROVIDER_SOURCES_ROOT)
    except ValueError:
        return "TradingCodex public fetch destination escapes provider-source staging"
    if _path_has_symlink_below(PROVIDER_SOURCES_ROOT, lexical, include_target=True):
        return "TradingCodex public fetch destination cannot traverse or replace a symlink"
    if lexical.exists():
        return "TradingCodex public fetch destination must be a new staged file"
    if not _provider_source_path_allowed(value, cwd):
        return (
            "TradingCodex public fetch files must be written under "
            "$TRADINGCODEX_SCRATCH/provider-sources/<provider-id>/"
        )
    resolved = _resolved_fetch_path(value, cwd)
    try:
        relative = resolved.relative_to(PROVIDER_SOURCES_ROOT)
    except ValueError:
        return "TradingCodex public fetch destination escapes provider-source staging"
    if len(relative.parts) < 2 or len(lexical_relative.parts) < 2:
        return "TradingCodex public fetch destination must name a staged provider file"
    if any(part.lower() in VCS_METADATA_NAMES for part in relative.parts):
        return "TradingCodex public fetch cannot write into Git or other VCS metadata"
    if _secret_like_filename(resolved):
        return "TradingCodex public fetch rejects secret-like destination filenames"
    return ""


def _fresh_http_provider_directory_reason(raw_path: str, cwd: Path) -> str:
    """Validate the one directory curl may create for an HTTP-only provider source."""

    destination = _lexical_fetch_path(raw_path, cwd)
    try:
        relative = destination.relative_to(PROVIDER_SOURCES_ROOT)
    except ValueError:
        return "TradingCodex curl --create-dirs output escapes provider-source staging"
    if len(relative.parts) != 2:
        return (
            "TradingCodex curl --create-dirs output must be exactly one direct "
            "provider-id/file path"
        )
    provider_directory = PROVIDER_SOURCES_ROOT / relative.parts[0]
    reason = _safe_git_clone_destination_reason(str(provider_directory), PROVIDER_SOURCES_ROOT)
    if reason:
        return reason.replace(
            "public Git clone destination",
            "curl --create-dirs provider directory",
        )
    return ""


def _implicit_public_download_path(raw_url: str, directory: Path, *, default_index: bool) -> tuple[Path | None, str]:
    try:
        parsed = urllib.parse.urlsplit(raw_url)
        decoded_path = urllib.parse.unquote(parsed.path, errors="strict")
    except (UnicodeDecodeError, ValueError):
        return None, "TradingCodex public fetch implicit destination is invalid"
    path_parts = tuple(part for part in decoded_path.replace("\\", "/").split("/") if part)
    if any(part.lower() in VCS_METADATA_NAMES for part in path_parts):
        return None, "TradingCodex public fetch implicit destination cannot originate in VCS metadata"
    leaf = path_parts[-1] if path_parts else ("index.html" if default_index else "")
    if not leaf or any(ord(character) < 32 for character in leaf):
        return None, "TradingCodex public fetch implicit destination has no safe filename"
    if "/" in leaf or "\\" in leaf or leaf in {".", ".."}:
        return None, "TradingCodex public fetch implicit destination is invalid"
    destination = directory / leaf
    if _secret_like_filename(destination):
        return None, "TradingCodex public fetch rejects secret-like implicit destination filenames"
    return destination, ""


def _public_url_reason(raw_url: str, *, git_https_only: bool = False) -> str:
    value = str(raw_url or "").strip()
    if any(character in value for character in ("$", "`", "\x00")):
        return "TradingCodex public fetch URL cannot use shell expansion"
    try:
        parsed = urllib.parse.urlsplit(value)
    except ValueError:
        return "TradingCodex public fetch URL is invalid"
    allowed_schemes = {"https"} if git_https_only else {"http", "https"}
    if parsed.scheme.lower() not in allowed_schemes or not parsed.hostname:
        return "TradingCodex public fetch requires a public HTTP(S) URL"
    if parsed.username is not None or parsed.password is not None:
        return "TradingCodex public fetch URL must not contain credentials"
    if git_https_only and (parsed.query or parsed.fragment):
        return "TradingCodex public Git URLs must not contain query or fragment data"
    hostname = parsed.hostname.rstrip(".").lower()
    if "%" in hostname or hostname == "localhost" or hostname.endswith(PRIVATE_HOST_SUFFIXES):
        return "TradingCodex public fetch cannot target local or private hosts"
    try:
        address = ipaddress.ip_address(hostname.strip("[]"))
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        return "TradingCodex public fetch cannot target local, private, or link-local addresses"
    labels = hostname.split(".")
    numeric_label = re.compile(r"(?:0x[0-9a-f]+|[0-9]+)", re.I)
    if address is None and (all(numeric_label.fullmatch(label) for label in labels) or labels[-1].isdigit()):
        return "TradingCodex public fetch cannot target numeric aliases for local or private hosts"
    for key, _value in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True):
        if re.search(r"(?:auth|token|secret|password|passwd|api[_-]?key|signature|credential|cookie|session)", key, re.I):
            return "TradingCodex public fetch URL must not contain secret-bearing query parameters"
    return ""


def _absolute_staging_operand_reason(raw_path: str) -> str:
    value = str(raw_path or "").strip()
    if not value or value.startswith(("$", "~")):
        return "Workdir-less TradingCodex commands require literal absolute provider-source paths"
    try:
        supplied = Path(value)
    except (OSError, RuntimeError, ValueError):
        return "Workdir-less TradingCodex commands require valid absolute provider-source paths"
    if not supplied.is_absolute() or any(part in {"", ".", ".."} for part in supplied.parts[1:]):
        return "Workdir-less TradingCodex commands require literal absolute provider-source paths"
    return ""


def _curl_url_has_glob_brackets(raw_url: str) -> bool:
    if "[" not in raw_url and "]" not in raw_url:
        return False
    try:
        parsed = urllib.parse.urlsplit(raw_url)
    except ValueError:
        return True
    if any("[" in value or "]" in value for value in (parsed.path, parsed.query, parsed.fragment)):
        return True
    return re.fullmatch(r"\[[0-9A-Fa-f:.]+\](?::[0-9]+)?", parsed.netloc or "") is None


def _curl_fetch_reason(
    argv: tuple[str, ...],
    cwd: Path,
    *,
    require_absolute_paths: bool = False,
    require_globoff: bool = False,
) -> str:
    urls: list[str] = []
    output_paths: list[str] = []
    remote_name = False
    globoff = False
    create_dirs = False
    output_directory = ""
    no_value_long = {
        "--compressed",
        "--create-dirs",
        "--fail",
        "--fail-with-body",
        "--get",
        "--globoff",
        "--head",
        "--http1.1",
        "--http2",
        "--ipv4",
        "--ipv6",
        "--location",
        "--no-progress-meter",
        "--retry-connrefused",
        "--show-error",
        "--silent",
        "--tlsv1.2",
        "--tlsv1.3",
    }
    value_long = {
        "--connect-timeout",
        "--limit-rate",
        "--max-redirs",
        "--max-time",
        "--retry",
        "--retry-delay",
        "--retry-max-time",
        "--user-agent",
    }
    no_value_short = frozenset("fgGsSLIO46")
    value_short = frozenset("AoHX")
    index = 0
    while index < len(argv):
        value = argv[index]
        lowered = value.lower()
        if value == "--":
            urls.extend(argv[index + 1 :])
            break
        if not value.startswith("-") or value == "-":
            urls.append(value)
            index += 1
            continue
        if lowered in no_value_long:
            if lowered == "--globoff":
                globoff = True
            elif lowered == "--create-dirs":
                if create_dirs:
                    return "TradingCodex public curl fetch accepts --create-dirs only once"
                create_dirs = True
            index += 1
            continue
        option, has_equals, attached = value.partition("=")
        option = option.lower()
        if option in value_long:
            if has_equals:
                argument = attached
                index += 1
            elif index + 1 < len(argv):
                argument = argv[index + 1]
                index += 2
            else:
                return f"TradingCodex public fetch option {option} is incomplete"
            if "$" in argument or "`" in argument:
                return "TradingCodex public fetch options cannot use shell expansion"
            continue
        if option in {"--request", "--header", "--output", "--output-dir", "--url"}:
            if has_equals:
                argument = attached
                index += 1
            elif index + 1 < len(argv):
                argument = argv[index + 1]
                index += 2
            else:
                return f"TradingCodex public fetch option {option} is incomplete"
            if option == "--request":
                if argument.upper() not in {"GET", "HEAD"}:
                    return "TradingCodex public fetch permits only HTTP GET or HEAD"
            elif option == "--header":
                reason = _public_header_reason(argument)
                if reason:
                    return reason
            elif option == "--output":
                output_paths.append(argument)
            elif option == "--output-dir":
                if output_directory:
                    return "TradingCodex public fetch output directory may be specified only once"
                output_directory = argument
            else:
                urls.append(argument)
            continue
        if lowered in {"--remote-name", "--remote-name-all"}:
            remote_name = True
            index += 1
            continue
        if lowered.startswith("--"):
            return "TradingCodex public fetch rejects unsupported or effectful curl options"

        short = value[1:]
        cursor = 0
        while cursor < len(short):
            flag = short[cursor]
            if flag in no_value_short:
                if flag == "g":
                    globoff = True
                if flag == "O":
                    remote_name = True
                cursor += 1
                continue
            if flag not in value_short:
                return "TradingCodex public fetch rejects unsupported or effectful curl options"
            if cursor + 1 < len(short):
                argument = short[cursor + 1 :]
            elif index + 1 < len(argv):
                argument = argv[index + 1]
                index += 1
            else:
                return f"TradingCodex public fetch option -{flag} is incomplete"
            if flag == "X":
                if argument.upper() not in {"GET", "HEAD"}:
                    return "TradingCodex public fetch permits only HTTP GET or HEAD"
            elif flag == "H":
                reason = _public_header_reason(argument)
                if reason:
                    return reason
            elif flag == "o":
                output_paths.append(argument)
            elif "$" in argument or "`" in argument:
                return "TradingCodex public fetch options cannot use shell expansion"
            cursor = len(short)
        index += 1

    if not urls:
        return "TradingCodex public fetch requires at least one URL"
    if create_dirs and not require_globoff:
        return "TradingCodex public curl --create-dirs is limited to Build provider staging"
    if require_globoff and not globoff:
        return "TradingCodex public curl fetch requires --globoff or -g"
    for url in urls:
        if require_globoff and ("{" in url or "}" in url or _curl_url_has_glob_brackets(url)):
            return "TradingCodex public curl fetch rejects URL glob and range templates"
        reason = _public_url_reason(url)
        if reason:
            return reason
    if remote_name:
        if require_absolute_paths and not output_directory:
            return (
                "Workdir-less TradingCodex curl remote-name output requires an explicit absolute "
                "provider-source output directory"
            )
        if require_absolute_paths:
            reason = _absolute_staging_operand_reason(output_directory)
            if reason:
                return reason
        destination_directory = _resolved_fetch_path(output_directory, cwd) if output_directory else cwd
        for url in urls:
            implicit_path, reason = _implicit_public_download_path(url, destination_directory, default_index=False)
            if reason:
                return reason
            assert implicit_path is not None
            output_paths.append(str(implicit_path))
    elif output_directory:
        return "TradingCodex public fetch output directory requires remote-name output"
    if create_dirs and (remote_name or output_directory or len(urls) != 1 or len(output_paths) != 1):
        return (
            "TradingCodex public curl --create-dirs requires one URL and one explicit "
            "provider-id/file output"
        )
    for path in output_paths:
        if require_absolute_paths:
            reason = _absolute_staging_operand_reason(path)
            if reason:
                return reason
        reason = _public_download_destination_reason(path, cwd)
        if reason:
            return reason
        if create_dirs:
            reason = _fresh_http_provider_directory_reason(path, cwd)
            if reason:
                return reason
        else:
            parent = _lexical_fetch_path(path, cwd).parent
            if not parent.is_dir() or _path_is_link_like(parent):
                return (
                    "TradingCodex public curl output parent must be an existing real provider "
                    "directory; use the restricted --create-dirs form for one fresh provider-id"
                )
    return ""


def _public_header_reason(raw_header: str) -> str:
    value = str(raw_header or "")
    if (
        not value
        or value.startswith("@")
        or "$" in value
        or "`" in value
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        return "TradingCodex public fetch headers must be literal and must not come from files or shell expansion"
    if ":" not in value:
        return "TradingCodex public fetch header is invalid"
    header_name = value.split(":", 1)[0].strip().lower()
    if (
        header_name in SENSITIVE_HEADER_NAMES
        or re.search(r"(?:auth|token|secret|credential|cookie|api[_-]?key|signature)", header_name, re.I)
    ):
        return "TradingCodex public fetch cannot send authentication or secret-bearing headers"
    if header_name not in {
        "accept",
        "accept-encoding",
        "accept-language",
        "cache-control",
        "if-match",
        "if-modified-since",
        "if-none-match",
        "if-unmodified-since",
        "range",
        "user-agent",
        "x-github-api-version",
    }:
        return "TradingCodex public fetch permits only benign content-negotiation and cache headers"
    return ""


def _wget_fetch_reason(
    argv: tuple[str, ...],
    cwd: Path,
    *,
    require_absolute_paths: bool = False,
) -> str:
    urls: list[str] = []
    output_document = ""
    directory_prefix = ""
    spider = False
    no_value_options = {
        "--https-only",
        "--no-verbose",
        "--quiet",
        "--server-response",
        "--spider",
    }
    value_options = {
        "--connect-timeout",
        "--max-redirect",
        "--read-timeout",
        "--timeout",
        "--tries",
        "--user-agent",
        "--waitretry",
    }
    index = 0
    while index < len(argv):
        value = argv[index]
        lowered = value.lower()
        if value == "--":
            urls.extend(argv[index + 1 :])
            break
        if not value.startswith("-") or value == "-":
            urls.append(value)
            index += 1
            continue
        if lowered in no_value_options or value in {"-q", "-nv", "-S"}:
            if lowered == "--spider":
                spider = True
            index += 1
            continue
        option, has_equals, attached = value.partition("=")
        option = option.lower()
        if option in value_options or option in {"--method", "--header", "--output-document", "--directory-prefix"}:
            if has_equals:
                argument = attached
                index += 1
            elif index + 1 < len(argv):
                argument = argv[index + 1]
                index += 2
            else:
                return f"TradingCodex public fetch option {option} is incomplete"
            if option == "--method":
                if argument.upper() not in {"GET", "HEAD"}:
                    return "TradingCodex public fetch permits only HTTP GET or HEAD"
            elif option == "--header":
                reason = _public_header_reason(argument)
                if reason:
                    return reason
            elif option == "--output-document":
                if output_document:
                    return "TradingCodex public fetch output path may be specified only once"
                output_document = argument
            elif option == "--directory-prefix":
                if directory_prefix:
                    return "TradingCodex public fetch directory prefix may be specified only once"
                directory_prefix = argument
            elif "$" in argument or "`" in argument:
                return "TradingCodex public fetch options cannot use shell expansion"
            continue
        if value.startswith("-qO") or value.startswith("-O"):
            prefix_length = 3 if value.startswith("-qO") else 2
            if len(value) > prefix_length:
                if output_document:
                    return "TradingCodex public fetch output path may be specified only once"
                output_document = value[prefix_length:]
                index += 1
            elif index + 1 < len(argv):
                if output_document:
                    return "TradingCodex public fetch output path may be specified only once"
                output_document = argv[index + 1]
                index += 2
            else:
                return "TradingCodex public fetch output path is incomplete"
            continue
        if lowered.startswith("--") or value.startswith("-"):
            return "TradingCodex public fetch rejects unsupported or effectful wget options"
    if not urls:
        return "TradingCodex public fetch requires at least one URL"
    for url in urls:
        reason = _public_url_reason(url)
        if reason:
            return reason
    if output_document:
        if require_absolute_paths and output_document != "-":
            reason = _absolute_staging_operand_reason(output_document)
            if reason:
                return reason
        return _public_download_destination_reason(output_document, cwd)
    if spider:
        return ""
    if require_absolute_paths and not directory_prefix:
        return (
            "Workdir-less TradingCodex wget output requires an explicit absolute provider-source "
            "directory or stdout"
        )
    if require_absolute_paths:
        reason = _absolute_staging_operand_reason(directory_prefix)
        if reason:
            return reason
    destination_directory = _resolved_fetch_path(directory_prefix, cwd) if directory_prefix else cwd
    for url in urls:
        implicit_path, reason = _implicit_public_download_path(url, destination_directory, default_index=True)
        if reason:
            return reason
        assert implicit_path is not None
        reason = _public_download_destination_reason(str(implicit_path), cwd)
        if reason:
            return reason
    return ""


def _safe_provider_sources_workdir_reason(cwd: Path) -> str:
    try:
        if cwd != PROVIDER_SOURCES_ROOT or cwd.resolve(strict=False) != PROVIDER_SOURCES_ROOT:
            return (
                "TradingCodex public fetch and provider review commands must use the exact tool workdir "
                "$TRADINGCODEX_SCRATCH/provider-sources/"
            )
    except (OSError, RuntimeError, ValueError):
        return "TradingCodex provider-source Git staging root is invalid"
    if _path_is_link_like(cwd) or not cwd.is_dir() or _path_is_link_like(PROVIDER_SOURCES_ROOT):
        return "TradingCodex provider-source staging root must be a real nonsymlink directory"
    shadow_names = {
        variant
        for executable in (*NETWORK_FETCH_EXECUTABLES, *PROVIDER_READ_EXECUTABLES, "python", "python3")
        for variant in (executable, f"{executable}.bat", f"{executable}.cmd", f"{executable}.com", f"{executable}.exe")
    }
    try:
        for entry in cwd.iterdir():
            if entry.name.lower() in shadow_names and (_path_is_link_like(entry) or not entry.is_dir()):
                return "TradingCodex provider-source staging root contains an executable-shadowing entry"
    except OSError:
        return "TradingCodex provider-source staging root could not be inspected safely"
    return ""


def _safe_git_staging_root_reason(cwd: Path) -> str:
    workdir_reason = _safe_provider_sources_workdir_reason(cwd)
    if workdir_reason:
        return workdir_reason
    for metadata_name in VCS_METADATA_NAMES:
        metadata = cwd / metadata_name
        if metadata.exists() or metadata.is_symlink():
            return "TradingCodex fresh Git commands cannot inherit repository-local VCS settings"
    return ""


def _safe_git_clone_destination_reason(raw_destination: str, cwd: Path) -> str:
    lexical_destination = _lexical_fetch_path(raw_destination, cwd)
    try:
        lexical_relative = lexical_destination.relative_to(PROVIDER_SOURCES_ROOT)
    except ValueError:
        return "TradingCodex public Git clone destination escapes provider-source staging"
    if len(lexical_relative.parts) != 1 or re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", lexical_relative.name) is None:
        return "TradingCodex public Git clone destination must be one new provider-id directory"
    if lexical_relative.name.lower() in VCS_METADATA_NAMES or _secret_like_filename(lexical_destination):
        return "TradingCodex public Git clone destination is reserved or secret-like"
    if _path_has_symlink_below(PROVIDER_SOURCES_ROOT, lexical_destination, include_target=True):
        return "TradingCodex public Git clone destination cannot traverse a symlink"
    destination = lexical_destination.resolve(strict=False)
    try:
        resolved_relative = destination.relative_to(PROVIDER_SOURCES_ROOT)
    except ValueError:
        return "TradingCodex public Git clone destination escapes provider-source staging"
    if resolved_relative != lexical_relative:
        return "TradingCodex public Git clone destination cannot traverse a symlink"
    if destination.exists() or _path_is_link_like(destination):
        return "TradingCodex public Git clone destination must not already exist"
    return ""


def _staged_git_repository_path_reason(repository: Path) -> str:
    try:
        resolved_repository = repository.resolve(strict=False)
        relative = resolved_repository.relative_to(PROVIDER_SOURCES_ROOT)
    except (OSError, RuntimeError, ValueError):
        return "Fetched provider Git inspection must stay in provider-source staging"
    if len(relative.parts) != 1 or re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", relative.name) is None:
        return "Fetched provider Git inspection requires one direct provider repository"
    if _path_is_link_like(repository) or not repository.is_dir():
        return "Fetched provider Git repository must be a real nonsymlink directory"
    if _path_has_symlink_below(PROVIDER_SOURCES_ROOT, repository, include_target=True):
        return "Fetched provider Git repository path cannot traverse a symlink"
    return ""


def _read_real_staged_git_config(config_path: Path) -> tuple[str, str]:
    try:
        metadata = os.lstat(config_path)
    except OSError:
        return "", "Fetched provider Git repository requires a real .git/config file"
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        return "", "Fetched provider Git .git/config must be a real nonsymlink regular file"
    if metadata.st_size > MAX_STAGED_GIT_CONFIG_BYTES:
        return "", "Fetched provider Git .git/config exceeds the inspection size limit"
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(config_path, flags)
        try:
            opened = os.fstat(descriptor)
            if not stat.S_ISREG(opened.st_mode) or opened.st_size > MAX_STAGED_GIT_CONFIG_BYTES:
                return "", "Fetched provider Git .git/config must remain a bounded regular file"
            chunks: list[bytes] = []
            remaining = MAX_STAGED_GIT_CONFIG_BYTES + 1
            while remaining > 0:
                chunk = os.read(descriptor, min(64 * 1024, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
        finally:
            os.close(descriptor)
    except OSError:
        return "", "Fetched provider Git .git/config could not be inspected safely"
    content = b"".join(chunks)
    if len(content) > MAX_STAGED_GIT_CONFIG_BYTES:
        return "", "Fetched provider Git .git/config exceeds the inspection size limit"
    try:
        return content.decode("utf-8"), ""
    except UnicodeDecodeError:
        return "", "Fetched provider Git .git/config must be valid UTF-8"


def _parse_staged_git_config(content: str) -> tuple[dict[tuple[str, str, str], str], str]:
    entries: dict[tuple[str, str, str], str] = {}
    section = ""
    subsection = ""
    for line_number, raw_line in enumerate(content.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith(("#", ";")):
            continue
        section_match = re.fullmatch(
            r'\[([A-Za-z][A-Za-z0-9.-]*)(?:\s+"([^"\\\x00-\x1f]+)")?\]',
            line,
        )
        if section_match:
            section = section_match.group(1).lower()
            subsection = section_match.group(2) or ""
            continue
        if not section or "=" not in line:
            return {}, f"Fetched provider Git .git/config has invalid syntax on line {line_number}"
        raw_key, raw_value = line.split("=", 1)
        key = raw_key.strip().lower()
        value = raw_value.strip()
        if re.fullmatch(r"[A-Za-z][A-Za-z0-9-]*", key) is None:
            return {}, f"Fetched provider Git .git/config has an invalid key on line {line_number}"
        if not value or value.startswith(('"', "'")) or any(ord(character) < 32 for character in value):
            return {}, f"Fetched provider Git .git/config has a nonliteral value on line {line_number}"
        identity = (section, subsection, key)
        if identity in entries:
            return {}, "Fetched provider Git .git/config contains duplicate settings"
        entries[identity] = value
        if len(entries) > 32:
            return {}, "Fetched provider Git .git/config contains too many settings"
    return entries, ""


def _safe_git_ref_name(value: str) -> bool:
    return bool(
        value
        and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]*", value)
        and not value.endswith((".", "/", ".lock"))
        and not any(marker in value for marker in ("..", "//", "@{", "\\"))
    )


def _forbidden_staged_git_config_key(section: str, subsection: str, key: str) -> bool:
    descriptor = ".".join(part for part in (section, subsection, key) if part).lower()
    return bool(
        section in {"alias", "credential", "diff", "filter", "http", "include", "includeif", "merge", "url"}
        or re.search(
            r"(?:^|[.])(?:askpass|command|credential|extraheader|filter|helper|hookspath|include|"
            r"proxy|receivepack|sshcommand|transport|uploadpack)(?:[.]|$)",
            descriptor,
        )
    )


def _staged_git_repository_config_reason(repository: Path) -> str:
    repository_reason = _staged_git_repository_path_reason(repository)
    if repository_reason:
        return repository_reason
    git_directory = repository / ".git"
    config_path = git_directory / "config"
    if _path_is_link_like(git_directory) or not git_directory.is_dir():
        return "Fetched provider Git repository requires a real nonsymlink .git directory"
    indirection_paths = (
        git_directory / "commondir",
        git_directory / "gitdir",
        git_directory / "worktrees",
        git_directory / "objects" / "info" / "alternates",
    )
    if any(path.exists() or _path_is_link_like(path) for path in indirection_paths):
        return (
            "Fetched provider Git repository rejects alternates, common-dir, gitdir, and worktree "
            "indirection"
        )
    if _path_is_link_like(config_path):
        return "Fetched provider Git .git/config must not be a symlink"
    content, reason = _read_real_staged_git_config(config_path)
    if reason:
        return reason
    entries, reason = _parse_staged_git_config(content)
    if reason:
        return reason
    required = {
        ("core", "", "repositoryformatversion"),
        ("core", "", "filemode"),
        ("core", "", "bare"),
        ("core", "", "logallrefupdates"),
        ("remote", "origin", "url"),
        ("remote", "origin", "fetch"),
    }
    if not required.issubset(entries):
        return "Fetched provider Git .git/config is not a complete clone-generated configuration"
    branch_settings: dict[str, set[str]] = {}
    for (section, subsection, key), value in entries.items():
        if _forbidden_staged_git_config_key(section, subsection, key):
            return "Fetched provider Git .git/config contains unsafe helper, transport, header, proxy, hook, filter, include, or rewrite settings"
        if section == "core" and not subsection:
            if key == "repositoryformatversion" and value not in {"0", "1"}:
                return "Fetched provider Git .git/config has an unsupported repository format"
            if key in {"filemode", "ignorecase", "precomposeunicode", "symlinks"} and value.lower() not in {"true", "false"}:
                return "Fetched provider Git .git/config has an invalid clone-generated core setting"
            if key in {"bare", "logallrefupdates"} and value.lower() != ({"bare": "false", "logallrefupdates": "true"}[key]):
                return "Fetched provider Git .git/config is not a normal working clone"
            if key not in {
                "repositoryformatversion", "filemode", "bare", "logallrefupdates", "ignorecase", "precomposeunicode", "symlinks",
            }:
                return "Fetched provider Git .git/config contains settings outside the clone-generated allowlist"
            continue
        if section == "extensions" and not subsection:
            if (key == "objectformat" and value in {"sha1", "sha256"}) or (
                key == "refstorage" and value in {"files", "reftable"}
            ):
                continue
            return "Fetched provider Git .git/config contains unsupported repository extensions"
        if section == "remote" and subsection == "origin":
            if key == "url":
                url_reason = _public_url_reason(value, git_https_only=True)
                if url_reason:
                    return "Fetched provider Git origin URL is not a credential-free public HTTPS URL"
                continue
            if key == "fetch":
                standard_fetch = value == "+refs/heads/*:refs/remotes/origin/*"
                exact_fetch = re.fullmatch(
                    r"\+refs/(heads|tags)/([^:]+):refs/(remotes/origin|tags)/([^:]+)",
                    value,
                )
                if standard_fetch or (
                    exact_fetch
                    and exact_fetch.group(2) == exact_fetch.group(4)
                    and (
                        (exact_fetch.group(1) == "heads" and exact_fetch.group(3) == "remotes/origin")
                        or (exact_fetch.group(1) == "tags" and exact_fetch.group(3) == "tags")
                    )
                    and _safe_git_ref_name(exact_fetch.group(2))
                ):
                    continue
                return "Fetched provider Git .git/config has a nonstandard fetch mapping"
            if key == "tagopt" and value == "--no-tags":
                continue
            return "Fetched provider Git .git/config contains non-clone remote settings"
        if section == "branch" and _safe_git_ref_name(subsection):
            branch_settings.setdefault(subsection, set()).add(key)
            if key == "remote" and value == "origin":
                continue
            if key == "merge" and value == f"refs/heads/{subsection}":
                continue
            return "Fetched provider Git .git/config contains non-clone branch settings"
        return "Fetched provider Git .git/config contains settings outside the clone-generated allowlist"
    if any(keys != {"remote", "merge"} for keys in branch_settings.values()):
        return "Fetched provider Git .git/config has incomplete branch tracking settings"
    return ""


def _git_fetch_reason(
    argv: tuple[str, ...],
    cwd: Path,
    *,
    self_contained: bool = False,
) -> str | None:
    args = list(argv)
    if self_contained and args[:1] != ["-C"]:
        return (
            "Workdir-less TradingCodex Git commands require an absolute -C provider-source "
            "staging root or direct provider repository"
        )
    if args[:1] == ["-C"]:
        if len(args) < 3:
            return (
                "TradingCodex public Git fetch repositories must stay under "
                "$TRADINGCODEX_SCRATCH/provider-sources/<provider-id>/"
            )
        if self_contained:
            path_reason = _absolute_staging_operand_reason(args[1])
            if path_reason:
                return path_reason
        resolved_cwd = _lexical_fetch_path(args[1], cwd)
        if resolved_cwd != PROVIDER_SOURCES_ROOT and not _provider_source_path_allowed(args[1], cwd):
            return (
                "TradingCodex public Git fetch repositories must stay under "
                "$TRADINGCODEX_SCRATCH/provider-sources/<provider-id>/"
            )
        cwd = resolved_cwd
        args = args[2:]
    if not args or args[0] not in {"clone", "fetch", "ls-remote"}:
        if any(value in {"clone", "fetch", "ls-remote"} for value in args):
            return "TradingCodex public Git fetch rejects global config, environment, and unsupported option prefixes"
        return None
    subcommand = args[0]
    values = args[1:]
    if self_contained and subcommand in {"clone", "ls-remote"} and cwd != PROVIDER_SOURCES_ROOT:
        return (
            "Workdir-less TradingCodex Git clone and ls-remote require -C at the exact "
            "provider-source staging root"
        )
    if subcommand == "clone":
        return _git_clone_reason(values, cwd, require_absolute_destination=self_contained)
    if subcommand == "fetch":
        return _git_incremental_fetch_reason(values, cwd)
    return _git_ls_remote_reason(values, cwd)


def _resolved_fetch_path(raw_path: str, cwd: Path) -> Path:
    return _lexical_fetch_path(raw_path, cwd).resolve(strict=False)


def _lexical_fetch_path(raw_path: str, cwd: Path) -> Path:
    value = str(raw_path or "")
    if value.startswith("${TRADINGCODEX_SCRATCH}/"):
        value = value.replace("${TRADINGCODEX_SCRATCH}", str(SCRATCH_ROOT), 1)
    elif value.startswith("$TRADINGCODEX_SCRATCH/"):
        value = value.replace("$TRADINGCODEX_SCRATCH", str(SCRATCH_ROOT), 1)
    supplied = Path(value).expanduser()
    return supplied.absolute() if supplied.is_absolute() else (cwd / supplied).absolute()


def _git_option_values(
    values: list[str],
    *,
    no_value: set[str],
    with_value: set[str],
) -> tuple[list[str], str]:
    positional: list[str] = []
    index = 0
    while index < len(values):
        value = values[index]
        if value == "--":
            positional.extend(values[index + 1 :])
            break
        if not value.startswith("-"):
            positional.append(value)
            index += 1
            continue
        option, has_equals, attached = value.partition("=")
        if option in no_value and not has_equals:
            index += 1
            continue
        if option in with_value:
            if has_equals:
                argument = attached
                index += 1
            elif index + 1 < len(values):
                argument = values[index + 1]
                index += 2
            else:
                return [], f"TradingCodex public Git option {option} is incomplete"
            if not argument or "$" in argument or "`" in argument:
                return [], "TradingCodex public Git options must use literal non-secret values"
            continue
        return [], "TradingCodex public Git fetch rejects unsupported or effectful options"
    return positional, ""


def _git_clone_reason(
    values: list[str],
    cwd: Path,
    *,
    require_absolute_destination: bool = False,
) -> str:
    staging_reason = _safe_git_staging_root_reason(cwd)
    if staging_reason:
        return staging_reason
    if "--no-checkout" not in values:
        return "TradingCodex public Git clone requires --no-checkout so fetched source stays inert"
    positional, reason = _git_option_values(
        values,
        no_value={"--no-checkout", "--no-tags", "--quiet", "--single-branch"},
        with_value={"-b", "--branch", "--depth", "--revision"},
    )
    if reason:
        return reason
    if not positional or len(positional) > 2:
        return "TradingCodex public Git clone requires one public HTTPS URL and at most one destination"
    url = positional[0]
    reason = _public_url_reason(url, git_https_only=True)
    if reason:
        return reason
    if len(positional) == 2:
        destination = positional[1]
    else:
        if require_absolute_destination:
            return "Workdir-less TradingCodex Git clone requires an explicit absolute destination"
        repository_name = Path(urllib.parse.urlsplit(url).path.rstrip("/")).name.removesuffix(".git")
        destination = repository_name
    if require_absolute_destination:
        path_reason = _absolute_staging_operand_reason(destination)
        if path_reason:
            return path_reason
    if not _provider_source_path_allowed(destination, cwd):
        return (
            "TradingCodex public Git clones must be written under "
            "$TRADINGCODEX_SCRATCH/provider-sources/<provider-id>/"
        )
    destination_reason = _safe_git_clone_destination_reason(destination, cwd)
    if destination_reason:
        return destination_reason
    return ""


def _git_incremental_fetch_reason(values: list[str], cwd: Path) -> str:
    if not _provider_source_path_allowed(str(cwd), cwd):
        return (
            "TradingCodex public Git fetch repositories must stay under "
            "$TRADINGCODEX_SCRATCH/provider-sources/<provider-id>/"
        )
    config_reason = _staged_git_repository_config_reason(cwd)
    if config_reason:
        return config_reason
    positional, reason = _git_option_values(
        values,
        no_value={"--force", "--no-tags", "--prune", "--quiet", "--tags"},
        with_value={"--deepen", "--depth", "--shallow-exclude", "--shallow-since"},
    )
    if reason:
        return reason
    if not positional:
        return "TradingCodex public Git fetch requires an explicit public HTTPS URL"
    remote = positional[0]
    reason = _public_url_reason(remote, git_https_only=True)
    if reason:
        return reason
    for value in positional[1:]:
        if "$" in value or "`" in value or any(ord(character) < 32 for character in value):
            return "TradingCodex public Git fetch arguments must be literal"
    return ""


def _git_ls_remote_reason(values: list[str], cwd: Path) -> str:
    staging_reason = _safe_git_staging_root_reason(cwd)
    if staging_reason:
        return staging_reason
    positional, reason = _git_option_values(
        values,
        no_value={"--exit-code", "--get-url", "--heads", "--refs", "--symref", "--tags"},
        with_value={"--sort"},
    )
    if reason:
        return reason
    if not positional:
        return "TradingCodex public Git ls-remote requires an explicit public HTTPS URL"
    reason = _public_url_reason(positional[0], git_https_only=True)
    if reason:
        return reason
    if any("://" in value or "@" in value or "$" in value or "`" in value for value in positional[1:]):
        return "TradingCodex public Git ls-remote refs must be literal ref patterns"
    return ""


def _provider_read_paths_reason(
    paths: tuple[str, ...],
    cwd: Path,
    *,
    require_absolute_paths: bool = False,
    allow_real_directories: bool = False,
) -> str:
    if not paths:
        return "Fetched provider source review requires explicit staged file paths"
    for value in paths:
        if not value or value == "-" or any(character in value for character in ("$", "`", "\x00")):
            return "Fetched provider source review paths must be literal staged files"
        if require_absolute_paths:
            reason = _absolute_staging_operand_reason(value)
            if reason:
                return reason
        if not _provider_source_path_allowed(value, cwd):
            return "Fetched provider source review paths must stay under provider-source staging"
        resolved = _resolved_fetch_path(value, cwd)
        try:
            relative = resolved.relative_to(PROVIDER_SOURCES_ROOT)
        except ValueError:
            return "Fetched provider source review paths must stay under provider-source staging"
        if any(part.lower() in VCS_METADATA_NAMES for part in relative.parts):
            return "Fetched provider VCS metadata must be inspected only through validated Git commands"
        if any(_secret_like_filename(Path(part)) for part in relative.parts):
            return "Fetched provider source review rejects secret-like paths"
        lexical = _lexical_fetch_path(value, cwd)
        if _path_has_symlink_below(PROVIDER_SOURCES_ROOT, lexical, include_target=True):
            return "Fetched provider source review paths cannot traverse symlinks or junctions"
        try:
            metadata = os.lstat(lexical)
        except OSError:
            return "Fetched provider source review requires existing regular files"
        attributes = int(getattr(metadata, "st_file_attributes", 0) or 0)
        if attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0):
            return "Fetched provider source review paths cannot traverse symlinks or junctions"
        if not stat.S_ISREG(metadata.st_mode) and not (
            allow_real_directories and stat.S_ISDIR(metadata.st_mode)
        ):
            return "Fetched provider source review permits only real regular files or explicit ls directories"
    return ""


def _provider_read_command_reason(
    executable: str,
    args: tuple[str, ...],
    cwd: Path,
    *,
    require_absolute_paths: bool = False,
) -> str:
    if executable == "ls":
        options = tuple(value for value in args if value.startswith("-"))
        paths = tuple(value for value in args if not value.startswith("-"))
        if any(value not in {"-a", "-l", "-al", "-la"} for value in options):
            return "Fetched provider ls inspection rejects unsupported options"
        return _provider_read_paths_reason(
            paths,
            cwd,
            require_absolute_paths=require_absolute_paths,
            allow_real_directories=True,
        )
    if executable == "diff":
        options = tuple(value for value in args if value.startswith("-"))
        paths = tuple(value for value in args if not value.startswith("-"))
        if any(
            value not in {"-u", "-q", "--brief", "--no-dereference", "--report-identical-files"}
            and re.fullmatch(r"(?:-U|--unified=)[0-9]+", value) is None
            for value in options
        ):
            return "Fetched provider diff inspection rejects output or unsupported options"
        if len(paths) != 2:
            return "Fetched provider diff inspection requires exactly two staged files"
        return _provider_read_paths_reason(paths, cwd, require_absolute_paths=require_absolute_paths)
    if executable == "grep":
        if len(args) < 2 or any(value.startswith("-") for value in args):
            return "Fetched provider grep inspection requires one literal pattern and staged files"
        return _provider_read_paths_reason(args[1:], cwd, require_absolute_paths=require_absolute_paths)
    if executable in {"head", "tail", "wc"}:
        if any(value.startswith("-") for value in args):
            return f"Fetched provider {executable} inspection rejects unsupported options"
        return _provider_read_paths_reason(args, cwd, require_absolute_paths=require_absolute_paths)
    if executable == "shasum":
        if args[:2] == ("-a", "256"):
            paths = args[2:]
        else:
            return "Fetched provider shasum inspection permits only SHA-256"
        return _provider_read_paths_reason(paths, cwd, require_absolute_paths=require_absolute_paths)
    if any(value.startswith("-") for value in args):
        return f"Fetched provider {executable} inspection rejects unsupported options"
    if executable == "cmp" and len(args) != 2:
        return "Fetched provider cmp inspection requires exactly two staged files"
    return _provider_read_paths_reason(args, cwd, require_absolute_paths=require_absolute_paths)


def _git_inspection_arguments_reason(subcommand: str, args: tuple[str, ...]) -> str:
    dangerous_prefixes = (
        "--command", "--config", "--exec", "--ext-diff", "--filters", "--format", "--output", "--textconv",
    )
    if any(value.lower().startswith(dangerous_prefixes) for value in args):
        return "Fetched provider Git inspection rejects external, filter, formatting, and output options"
    if any(any(character in value for character in ("$", "`", "\x00", "\n", "\r")) for value in args):
        return "Fetched provider Git inspection arguments must be literal"
    if subcommand == "status":
        allowed = {
            "--short", "-s", "--branch", "-b", "--porcelain", "--porcelain=v1", "--porcelain=v2",
            "--show-stash", "--ahead-behind", "--no-ahead-behind", "--untracked-files=no",
            "--untracked-files=normal", "--untracked-files=all",
        }
        return "" if all(value in allowed for value in args) else "Fetched provider Git status options are not allowlisted"
    if subcommand == "rev-parse":
        allowed_options = {
            "--absolute-git-dir", "--abbrev-ref", "--git-dir", "--is-bare-repository",
            "--is-inside-git-dir", "--is-inside-work-tree", "--show-prefix", "--show-toplevel",
            "--symbolic-full-name", "--verify",
        }
        for value in args:
            if value.startswith("-") and value not in allowed_options and re.fullmatch(r"--short(?:=[0-9]+)?", value) is None:
                return "Fetched provider Git rev-parse options are not allowlisted"
        return ""
    if subcommand == "ls-files":
        allowed = {
            "--cached", "-c", "--deleted", "-d", "--modified", "-m", "--others", "-o", "--stage", "-s",
            "--unmerged", "-u", "--ignored", "-i", "--exclude-standard", "--error-unmatch", "-z", "-t", "-v",
        }
        return "" if all(value in allowed for value in args) else "Fetched provider Git ls-files options are not allowlisted"
    if subcommand == "ls-tree":
        allowed = {
            "-r", "-d", "-t", "-l", "--name-only", "--name-status", "--object-only", "--full-name",
            "--full-tree", "-z",
        }
        for value in args:
            if value.startswith("-") and value not in allowed and re.fullmatch(r"--abbrev(?:=[0-9]+)?", value) is None:
                return "Fetched provider Git ls-tree options are not allowlisted"
        return "" if args else "Fetched provider Git ls-tree requires an object"
    if subcommand == "cat-file":
        if len(args) != 2 or args[0] not in {"-e", "-p", "-s", "-t"} or args[1].startswith("-"):
            return "Fetched provider Git cat-file permits one inert object inspection"
        if args[0] == "-p":
            object_name, separator, source_path = args[1].partition(":")
            if (
                not separator
                or not _safe_git_ref_name(object_name)
                or _unsafe_git_source_path(source_path)
            ):
                return (
                    "Fetched provider Git cat-file -p requires a safe ref:path and rejects "
                    "secret-like or metadata paths"
                )
        return ""
    return "Fetched provider Git inspection subcommand is not allowlisted"


def _unsafe_git_source_path(value: str) -> bool:
    normalized = str(value or "").replace("\\", "/")
    parts = tuple(normalized.split("/"))
    return bool(
        not normalized
        or normalized.startswith("/")
        or any(part in {"", ".", ".."} for part in parts)
        or any(part.lower() in VCS_METADATA_NAMES for part in parts)
        or any(_secret_like_filename(Path(part)) for part in parts)
    )


def provider_sources_shell_command_reason(command: str, workdir: str = "") -> str | None:
    """Keep fetched provider material inert while permitting review and static checks."""

    raw = str(command or "").strip()
    if not _command_uses_provider_sources(raw, workdir):
        return None
    expansion_safe = _mask_scratch_expansion(raw)
    if _has_unquoted_network_control(expansion_safe):
        return "TradingCodex provider source review cannot use shell composition, redirection, or expansion"
    try:
        argv = tuple(shlex.split(raw, comments=False, posix=True))
    except ValueError:
        return "TradingCodex provider source review command quoting is invalid"
    if not argv:
        return "TradingCodex provider source review requires one non-empty command"
    if any(any(ord(character) < 32 or ord(character) == 127 for character in value) for value in argv):
        return "TradingCodex provider source review arguments must not contain control characters"
    executable_token = argv[0]
    executable = _external_command_name(executable_token)
    self_contained = not str(workdir or "").strip()
    cwd = PROVIDER_SOURCES_ROOT if self_contained else _fetch_workdir(workdir)
    workdir_reason = _safe_provider_sources_workdir_reason(cwd)
    if workdir_reason:
        return workdir_reason
    if executable_token == "pwd" and len(argv) == 1:
        return ""
    if executable in PROVIDER_READ_EXECUTABLES:
        if self_contained:
            if not _trusted_external_command_matches(executable_token, executable):
                return (
                    "Workdir-less fetched provider review requires the hook-advertised absolute "
                    "trusted read executable"
                )
        elif executable_token != executable:
            return "Fetched provider review requires exact bare managed read executable names"
        return _provider_read_command_reason(
            executable,
            argv[1:],
            cwd,
            require_absolute_paths=self_contained,
        )
    if executable_token == GENERATED_PYTHON_COMMAND and argv[1:5] == ("-I", "-S", "-m", "py_compile"):
        if not _generated_python_is_trusted():
            return "Fetched provider py_compile requires the exact real generated interpreter"
        source_paths = argv[5:]
        source_reason = _provider_read_paths_reason(
            source_paths,
            cwd,
            require_absolute_paths=self_contained,
        ) if source_paths else "Fetched provider py_compile requires source files"
        if not source_reason and all(value.endswith(".py") and not value.startswith("-") for value in source_paths):
            return ""
    git_args = list(argv[1:]) if executable == "git" else []
    if git_args and self_contained and not _trusted_external_command_matches(executable_token, "git"):
        return (
            "Workdir-less fetched provider Git inspection requires the hook-advertised absolute "
            "trusted Git executable"
        )
    if git_args and not self_contained and executable_token != "git":
        return "Fetched provider Git inspection requires the exact bare git executable name"
    used_git_c = False
    if git_args[:1] == ["-C"]:
        if len(git_args) < 3 or not _provider_source_path_allowed(
            git_args[1],
            cwd,
        ):
            return (
                "Fetched provider Git inspection must stay under "
                "$TRADINGCODEX_SCRATCH/provider-sources/<provider-id>/"
            )
        if self_contained:
            path_reason = _absolute_staging_operand_reason(git_args[1])
            if path_reason:
                return path_reason
        cwd = _lexical_fetch_path(git_args[1], cwd)
        git_args = git_args[2:]
        used_git_c = True
    if git_args and git_args[0] in {
        "cat-file",
        "ls-files",
        "ls-tree",
        "rev-parse",
        "status",
    }:
        if not used_git_c:
            return "Fetched provider Git inspection requires git -C <provider-id> from the staging root"
        config_reason = _staged_git_repository_config_reason(cwd)
        if config_reason:
            return config_reason
        return _git_inspection_arguments_reason(git_args[0], tuple(git_args[1:]))
    return (
        "Fetched provider sources are inert: only reads, SHA-256, diff, Git inspection, and isolated "
        "py_compile are allowed; execution, installation, and writes are blocked"
    )


def _command_uses_provider_sources(command: str, workdir: str) -> bool:
    normalized = str(command or "").replace("\\", "/")
    markers = (
        str(PROVIDER_SOURCES_ROOT).replace("\\", "/"),
        "$TRADINGCODEX_SCRATCH/provider-sources",
        "${TRADINGCODEX_SCRATCH}/provider-sources",
    )
    if any(marker in normalized for marker in markers):
        return True
    if not workdir:
        return False
    try:
        resolved = _fetch_workdir(workdir)
        return resolved == PROVIDER_SOURCES_ROOT or PROVIDER_SOURCES_ROOT in resolved.parents
    except (OSError, RuntimeError, ValueError):
        return False


def build_shell_command_reason(command: str, workdir: str = "") -> str:
    """Admit only non-extensible managed commands whose effects stay reviewable.

    A general interpreter, test runner, shell script, build system, or shell
    composition operator can hide a direct import of TradingCodex services and
    bypass hook-owned proofs. Provider source is therefore syntax-checked with
    isolated ``py_compile`` only; richer execution remains an explicit user
    terminal workflow.
    """

    if not command or any(character in command for character in ("\x00", "\n", "\r")):
        return "TradingCodex build shell use requires one physical command"
    canonical_command = re.sub(
        r"^(\s*)\.\\tcx\.cmd(?=\s|$)",
        r"\1tcx.cmd",
        command,
        count=1,
        flags=re.I,
    )
    if any(character in canonical_command for character in ("%", "!", "^", "\\")):
        return "TradingCodex build shell commands cannot use platform expansion or escape syntax"
    if re.search(r"[;&|><`$]", canonical_command) or has_unquoted_shell_expansion(canonical_command):
        return "TradingCodex build shell commands cannot use composition, redirection, or expansion"
    try:
        argv = tuple(shlex.split(canonical_command, comments=False, posix=True))
    except ValueError:
        return "TradingCodex build shell command quoting is invalid"
    if not argv:
        return "TradingCodex build shell use requires one non-empty command"

    self_contained = not str(workdir or "").strip()
    executable_token = argv[0]
    executable = _external_command_name(executable_token)
    if self_contained:
        exact_workdir = None
    else:
        try:
            exact_workdir = _fetch_workdir(workdir).resolve(strict=True)
        except (OSError, RuntimeError, ValueError):
            return "TradingCodex managed shell workdir must be an existing generated root"
        if exact_workdir not in {ROOT.resolve(), PROVIDER_SOURCES_ROOT}:
            return (
                "Relative TradingCodex managed shell commands require the exact generated workspace "
                "or provider-source root workdir"
            )
    if executable_token == "pwd" and len(argv) == 1:
        return ""
    if exact_workdir == PROVIDER_SOURCES_ROOT:
        return "Only exact pwd may use the provider-source root outside the dedicated review lane"
    if executable == "cat" and (
        (self_contained and _trusted_external_command_matches(executable_token, "cat"))
        or (not self_contained and executable_token == "cat")
    ) and len(argv) > 1 and all(
        not value.startswith("-") and not build_shell_workspace_path_reason(value)
        and (not self_contained or not _absolute_workspace_operand_reason(value))
        for value in argv[1:]
    ):
        return ""
    if executable == "ls" and (
        (self_contained and _trusted_external_command_matches(executable_token, "ls"))
        or (not self_contained and executable_token == "ls")
    ):
        paths = [value for value in argv[1:] if not value.startswith("-")]
        options = [value for value in argv[1:] if value.startswith("-")]
        if (
            (paths or not self_contained)
            and all(value in {"-a", "-l", "-al", "-la"} for value in options)
            and all(
            not build_shell_workspace_path_reason(value, allow_workspace_root=True)
            and (not self_contained or not _absolute_workspace_operand_reason(value))
            for value in (paths or ["."])
            )
        ):
            return ""
    if executable_token == GENERATED_PYTHON_COMMAND and argv[1:5] == ("-I", "-S", "-m", "py_compile"):
        source_paths = argv[5:]
        if _generated_python_is_trusted() and source_paths and all(
            value.endswith(".py")
            and not value.startswith("-")
            and (not self_contained or not _absolute_workspace_operand_reason(value))
            and not build_shell_workspace_path_reason(
                value,
                allowed_prefix=Path("trading/connectors"),
            )
            for value in source_paths
        ):
            return ""
    if self_contained and executable_token.replace("\\", "/") in _generated_workspace_launcher_commands():
        if build_tcx_argv_allowed(argv[1:]):
            return ""
    elif not self_contained and executable_token.replace("\\", "/") in {"./tcx", "tcx.cmd", "./tcx.cmd"}:
        launcher = ROOT / ("tcx.cmd" if executable.endswith(".cmd") else "tcx")
        if launcher.is_file() and not launcher.is_symlink() and build_tcx_argv_allowed(argv[1:]):
            return ""
    return (
        "TradingCodex managed workspace turns block general shell, scripts, tests, and interpreters; "
        "use apply_patch, protected services, trusted tcx commands, workspace reads, or isolated py_compile"
    )


def _absolute_workspace_operand_reason(raw_path: str) -> str:
    value = str(raw_path or "").strip()
    try:
        supplied = Path(value)
    except (OSError, RuntimeError, ValueError):
        return "Workdir-less TradingCodex commands require valid absolute workspace paths"
    if (
        not value
        or value.startswith(("$", "~"))
        or not supplied.is_absolute()
        or any(part in {"", ".", ".."} for part in supplied.parts[1:])
    ):
        return "Workdir-less TradingCodex commands require literal absolute workspace paths"
    return ""


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
        if _path_is_link_like(current):
            return "workspace path traverses a symlink or reparse point"
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
        return rest in {("status",), ("status", "--json")}
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
    if shell_name in {"write_stdin", "unified_exec"}:
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
        fetch_reason = public_fetch_command_reason(command, str(tool_input.get("workdir") or ""))
        if fetch_reason is not None:
            return fetch_reason
        provider_source_reason = provider_sources_shell_command_reason(
            command,
            str(tool_input.get("workdir") or ""),
        )
        if provider_source_reason is not None:
            return provider_source_reason
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

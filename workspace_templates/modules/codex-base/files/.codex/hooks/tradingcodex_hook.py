#!/usr/bin/env python3
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

RECORDED_PYTHON = "{{PYTHON_EXECUTABLE}}"
if (
    RECORDED_PYTHON
    and os.path.exists(RECORDED_PYTHON)
    and os.path.realpath(sys.executable) != os.path.realpath(RECORDED_PYTHON)
    and os.environ.get("TRADINGCODEX_HOOK_REEXEC") != "1"
):
    os.environ["TRADINGCODEX_HOOK_REEXEC"] = "1"
    os.execv(RECORDED_PYTHON, [RECORDED_PYTHON, __file__, *sys.argv[1:]])

SOURCE_ROOT = "{{SOURCE_ROOT}}"
if SOURCE_ROOT not in sys.path:
    sys.path.insert(0, SOURCE_ROOT)

os.environ.setdefault("TRADINGCODEX_WORKSPACE_ROOT", "{{PROJECT_DIR}}")

from tradingcodex_service.application.agents import EXPECTED_SUBAGENTS
from tradingcodex_service.application.harness import (
    build_compact_dispatch_context,
    build_subagent_starter_prompt,
    classify_starter_request,
    is_connector_build_request,
    is_investment_workflow_request,
    is_secret_only_request,
    is_secret_warning_request,
)
from tradingcodex_cli.startup_status import build_server_status, fallback_server_status

ROOT = Path("{{PROJECT_DIR}}")
MAX_SESSION_EVENTS = 12
MAX_COMPLETED_RECORDS = 12


def main() -> None:
    event = sys.argv[1] if len(sys.argv) > 1 else "unknown"
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except Exception:
        payload = {}
    if event == "session-start":
        session_start(payload)
    elif event == "user-prompt-submit":
        user_prompt_submit(payload)
    elif event in {"subagent-start", "subagent-stop"}:
        subagent_session_state(event, payload)
    elif event in {"pre-tool-use", "permission-request"}:
        policy_gate(event, payload)
    elif event == "post-tool-use":
        append_hook_audit({"event": event, "payload": payload})
    elif event == "stop":
        return


def session_start(payload: dict) -> None:
    try:
        server_status = build_server_status(ROOT)
    except Exception as exc:
        server_status = fallback_server_status(ROOT, exc)
        append_hook_audit({"event": "session-start", "warning": "server status check failed", "error": str(exc)})
    update_status = server_status["update_status"]
    service_detail = server_status.get("service_detail") or {}
    readiness = {
        "marker": "tradingcodex-session-context",
        "mode_status": server_status["mode_status"],
        "permission_status": server_status["permission_status"],
        "update_status": {
            "update_available": update_status["update_available"],
            "package_update_required": update_status["package_update_required"],
            "workspace_update_required": update_status["workspace_update_required"],
            "can_self_update": update_status["can_self_update"],
            "command": update_status["command"],
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
        "routing_status": {
            "lane": "startup",
            "selected_team": [],
            "blocked_actions": ["live_order", "raw secret", "direct broker API", "execution without approved artifacts"],
        },
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
    if agent_type in EXPECTED_SUBAGENTS:
        return
    secret_warning = is_secret_warning_request(prompt)
    secret_only = is_secret_only_request(prompt)
    connector_build_request = is_connector_build_request(prompt)
    investment_request = is_investment_workflow_request(prompt) and not secret_only
    if not investment_request and not secret_warning and not connector_build_request:
        return
    plan = classify_starter_request(prompt) if investment_request or connector_build_request else {"lane": "secret_warning", "subagents": []}
    explicit = any(token in prompt.lower() for token in ["subagent", "parallel", "delegated", "$tcx-workflow", "$orchestrate-workflow", "서브에이전트"])
    activation_source = "explicit_subagent" if explicit else "auto_routed_investment_request"
    if connector_build_request:
        activation_source = "connector_build_request"
    if not investment_request:
        activation_source = "secret_warning_only"
    if connector_build_request:
        activation_source = "connector_build_request"
    compact_context = build_compact_dispatch_context(prompt, ROOT) if investment_request else {
        "context_mode": "compact_workflow_gate",
        "workflow_lane": plan["lane"],
        "required_subagents": plan.get("subagents", []),
        "starter_prompt_path": ".tradingcodex/mainagent/latest-user-prompt-gate.json",
        "blocked_actions": plan.get("blockedActions", ["secret storage", "secret echo", "raw credential handling"]),
        "routing_status": {
            "lane": plan["lane"],
            "selected_team": plan.get("subagents", []),
            "blocked_actions": plan.get("blockedActions", []),
        },
    }
    gate = {
        "marker": "tradingcodex-workflow-gate",
        "workflow_run_id": f"workflow-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')}",
        "requires_subagent_dispatch": investment_request,
        "auto_dispatch_allowed": investment_request,
        "confirmation_required": False,
        "explicit_subagent_request": explicit,
        "activation_source": activation_source,
        "workflow_lane": plan["lane"],
        "required_subagents": plan.get("subagents", []),
        "starter_prompt": build_subagent_starter_prompt(prompt, ROOT) if investment_request or connector_build_request else "",
        "compact_additional_context": compact_context,
        "secret_warning": secret_warning,
    }
    write_json(ROOT / ".tradingcodex" / "mainagent" / "latest-user-prompt-gate.json", gate)
    append_jsonl(ROOT / ".tradingcodex" / "mainagent" / "prompt-gate-history.jsonl", {
        "ts": now(),
        "workflow_run_id": gate["workflow_run_id"],
        "workflow_lane": gate["workflow_lane"],
        "required_subagents": gate["required_subagents"],
        "requires_subagent_dispatch": gate["requires_subagent_dispatch"],
        "activation_source": gate["activation_source"],
        "secret_warning": gate["secret_warning"],
        "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        "prompt_bytes": len(prompt.encode("utf-8")),
        "compact_context": compact_context,
        "starter_prompt_estimated_chars": len(gate["starter_prompt"]),
    })
    append_hook_audit({
        "event": "user-prompt-submit",
        "workflow_run_id": gate["workflow_run_id"],
        "workflow_lane": gate["workflow_lane"],
        "required_subagents": gate["required_subagents"],
        "requires_subagent_dispatch": gate["requires_subagent_dispatch"],
        "activation_source": gate["activation_source"],
        "secret_warning": gate["secret_warning"],
        "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        "prompt_bytes": len(prompt.encode("utf-8")),
    })
    additional_context = {
        "marker": gate["marker"],
        "workflow_run_id": gate["workflow_run_id"],
        "requires_subagent_dispatch": gate["requires_subagent_dispatch"],
        "auto_dispatch_allowed": gate["auto_dispatch_allowed"],
        "confirmation_required": gate["confirmation_required"],
        "explicit_subagent_request": gate["explicit_subagent_request"],
        "activation_source": gate["activation_source"],
        "secret_warning": gate["secret_warning"],
        **compact_context,
    }
    output = {
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": json.dumps(additional_context, ensure_ascii=False),
        }
    }
    print(json.dumps(output, ensure_ascii=False))


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
    gate = read_json(ROOT / ".tradingcodex" / "mainagent" / "latest-user-prompt-gate.json", {})
    role = payload.get("agent_type") or payload.get("subagent_type") or payload.get("subagent") or payload.get("agent") or payload.get("task_name", "").split(" ")[0]
    event_count_total = int(state.get("event_count_total") or len(state.get("events", [])))
    completed_count_total = int(state.get("completed_count_total") or len(state.get("completed", [])))
    record = {
        "event": event,
        "role": role,
        "task_name": payload.get("task_name"),
        "run_id": gate.get("workflow_run_id"),
        "ts": now(),
    }
    if event == "subagent-start":
        state.setdefault("active", {})[role] = record
    else:
        state.setdefault("active", {}).pop(role, None)
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


def policy_gate(event: str, payload: dict) -> None:
    text = json.dumps(payload, ensure_ascii=False).lower()
    forbidden = ["broker api", "api_key", "secret.read", "cash.withdraw", "policy.write"]
    if any(item in text for item in forbidden):
        print(json.dumps({"decision": "block", "reason": "TradingCodex policy gate blocked sensitive request"}))


def append_hook_audit(record: dict) -> None:
    append_jsonl(ROOT / "trading" / "audit" / "codex-hooks.jsonl", {"ts": now(), **record})


def now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def append_jsonl(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(value, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()

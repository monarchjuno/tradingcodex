#!/usr/bin/env python3
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
from tradingcodex_service.application.workflow_diagnostics import diagnose_workflow_loop_state
from tradingcodex_service.application.workflow_planner import build_workflow_intake, record_workflow_intake
from tradingcodex_cli.startup_status import build_server_status, fallback_server_status

ROOT = Path("{{PROJECT_DIR}}")
MAX_SESSION_EVENTS = 12
MAX_COMPLETED_RECORDS = 12
LOOP_STATE_PATH = ROOT / ".tradingcodex" / "mainagent" / "workflow-loop-state.json"
LOOP_RUNS_DIR = ROOT / ".tradingcodex" / "mainagent" / "workflows"
SESSION_RUNS_PATH = ROOT / ".tradingcodex" / "mainagent" / "session-workflow-runs.json"


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
    try:
        preview = build_workflow_intake(prompt, ROOT)
    except Exception as exc:
        append_hook_audit({"event": "user-prompt-submit", "warning": "workflow intake failed", "error": str(exc)})
        return
    if not preview.get("requires_workflow_planning") and not preview.get("secret_warning"):
        return
    intake = record_workflow_intake(ROOT, prompt, workflow_run_id=preview["workflow_run_id"])
    session_key = event_session_key(payload)
    if session_key:
        remember_session_run(session_key, intake["workflow_run_id"])
    append_hook_audit({
        "event": "user-prompt-submit",
        "workflow_run_id": intake["workflow_run_id"],
        "requires_workflow_planning": intake["requires_workflow_planning"],
        "investment_candidate": intake["investment_candidate"],
        "connector_build": intake["connector_build"],
        "secret_warning": intake["secret_warning"],
        "heuristic_lane": intake["heuristic_lane"],
        "heuristic_roles": intake["heuristic_roles"],
        "prompt_sha256": intake["prompt_sha256"],
        "prompt_bytes": intake["prompt_bytes"],
    })
    additional_context = {
        "marker": intake["marker"],
        "workflow_run_id": intake["workflow_run_id"],
        "requires_workflow_planning": intake["requires_workflow_planning"],
        "intake_path": intake["intake_path"],
        "investment_candidate": intake["investment_candidate"],
        "connector_build": intake["connector_build"],
        "secret_warning": intake["secret_warning"],
        "explicit_negations": intake["explicit_negations"],
        "heuristic_lane": intake["heuristic_lane"],
        "heuristic_roles": intake["heuristic_roles"],
        "blocked_actions": intake["blocked_actions"],
        "deterministic_hint": intake["deterministic_hint"],
        "planning_instruction": "Use $tcx-workflow to draft, validate, and record a staged workflow plan before dispatch or investment analysis.",
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
    run_id = resolve_workflow_run_id(payload)
    role = payload.get("agent_type") or payload.get("subagent_type") or payload.get("subagent") or payload.get("agent") or payload.get("task_name", "").split(" ")[0]
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
    update_loop_state_for_subagent_event(event, role, record)
    append_jsonl(ROOT / "trading" / "audit" / "subagent-session-events.jsonl", record)


def update_loop_state_for_subagent_event(event: str, role: str, record: dict) -> None:
    state = read_json(loop_state_path(record.get("run_id")), {}) if record.get("run_id") else {}
    if not state or state.get("workflow_run_id") != record.get("run_id"):
        return
    pending = state.get("pending_tasks") if isinstance(state.get("pending_tasks"), list) else []
    for task in pending:
        task_roles = task.get("roles") if isinstance(task.get("roles"), list) else [task.get("role")]
        if role in task_roles and task.get("status") in {"pending", "active"}:
            task["status"] = "active" if event == "subagent-start" else "completed"
            task["updated_at"] = record["ts"]
            break
    if event == "subagent-stop":
        state.setdefault("completed_artifacts", []).append({
            "role": role,
            "task_name": record.get("task_name"),
            "agent_session_id": record.get("agent_session_id"),
            "handoff_state": "waiting",
            "artifact_path": "",
            "completed_at": record["ts"],
        })
    state["pending_tasks"] = pending
    state["iteration"] = len(state.get("completed_artifacts") or [])
    state["stop_reason"] = "waiting_for_artifacts" if any(task.get("status") != "completed" for task in pending) else "ready_for_artifact_verification"
    state["updated_at"] = now()
    latest = read_json(LOOP_STATE_PATH, {})
    write_loop_state(state, update_latest=(not latest or latest.get("workflow_run_id") == state.get("workflow_run_id")))


def safe_id(value) -> str:
    text = str(value or "")
    cleaned = "".join(ch if ch.isalnum() or ch in "._-" else "-" for ch in text).strip("-")
    return cleaned or "unknown"


def loop_state_relpath(run_id) -> str:
    return f".tradingcodex/mainagent/workflows/{safe_id(run_id)}/loop-state.json"


def loop_state_path(run_id) -> Path:
    return ROOT / ".tradingcodex" / "mainagent" / "workflows" / safe_id(run_id) / "loop-state.json"


def compact_loop_summary(state: dict) -> dict:
    state = diagnose_workflow_loop_state(state) if state else state
    pending = state.get("pending_tasks") if isinstance(state.get("pending_tasks"), list) else []
    completed = state.get("completed_artifacts") if isinstance(state.get("completed_artifacts"), list) else []
    decisions = state.get("loop_decisions") if isinstance(state.get("loop_decisions"), list) else []
    return {
        "workflow_run_id": state.get("workflow_run_id", ""),
        "lane": state.get("lane", ""),
        "state_path": state.get("state_path") or loop_state_relpath(state.get("workflow_run_id")),
        "iteration": state.get("iteration", 0),
        "pending_tasks": pending,
        "completed_artifacts": completed[-12:],
        "loop_decisions": decisions[-12:],
        "escalation_proposals": state.get("escalation_proposals", []) if isinstance(state.get("escalation_proposals"), list) else [],
        "blocked_actions": state.get("blocked_actions", []) if isinstance(state.get("blocked_actions"), list) else [],
        "stop_reason": state.get("stop_reason", ""),
        "diagnostics": state.get("diagnostics", {}),
        "state_mode": state.get("state_mode", "inspectable_assisted_loop"),
        "auto_spawn": False,
        "recursive_hook_dispatch": False,
        "updated_at": state.get("updated_at", ""),
    }


def write_loop_state(state: dict, *, update_latest: bool = True) -> None:
    path = loop_state_path(state.get("workflow_run_id"))
    state["state_path"] = loop_state_relpath(state.get("workflow_run_id"))
    write_json(path, state)
    if update_latest:
        write_json(LOOP_STATE_PATH, compact_loop_summary(state))


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
    plan = read_json(ROOT / ".tradingcodex" / "mainagent" / "latest-workflow-plan.json", {})
    intake = read_json(ROOT / ".tradingcodex" / "mainagent" / "latest-workflow-intake.json", {})
    return str(plan.get("workflow_run_id") or intake.get("workflow_run_id") or "")


def subagent_session_id(payload: dict, run_id: str, role: str) -> str:
    for key in ("agent_session_id", "subagent_session_id", "subagent_id", "agent_id", "thread_id", "conversation_id"):
        if payload.get(key):
            return str(payload[key])
    return f"{run_id}:{role}"


def policy_gate(event: str, payload: dict) -> None:
    text = json.dumps(payload, ensure_ascii=False).lower()
    forbidden = ["broker api", "api_key", "secret.read", "cash.withdraw", "policy.write"]
    if is_workflow_plan_command(payload):
        forbidden.remove("broker api")
    if any(item in text for item in forbidden):
        print(json.dumps({"decision": "block", "reason": "TradingCodex policy gate blocked sensitive request"}))


def is_workflow_plan_command(payload: dict) -> bool:
    command = json.dumps(payload.get("tool_input") or payload.get("input") or payload, ensure_ascii=False).lower()
    return (
        ".tradingcodex/mainagent/workflows/" in command
        and ".json" in command
        and "--plan" in command
        and ("tcx workflow validate" in command or "tcx workflow record" in command)
    )


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

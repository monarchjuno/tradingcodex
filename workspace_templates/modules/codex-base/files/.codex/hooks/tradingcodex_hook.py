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
    loop_state_rel = loop_state_relpath(gate["workflow_run_id"])
    session_key = event_session_key(payload)
    if isinstance(compact_context.get("loop_contract"), dict):
        compact_context["loop_contract"]["state_path"] = loop_state_rel
    gate["workflow_loop_state_path"] = loop_state_rel
    gate["latest_workflow_loop_state_path"] = ".tradingcodex/mainagent/workflow-loop-state.json"
    gate["session_key"] = session_key
    loop_state = build_initial_loop_state(gate, plan, compact_context)
    write_json(ROOT / ".tradingcodex" / "mainagent" / "latest-user-prompt-gate.json", gate)
    write_json(workflow_gate_path(gate["workflow_run_id"]), gate)
    write_loop_state(loop_state)
    if session_key:
        remember_session_run(session_key, gate["workflow_run_id"])
    append_jsonl(ROOT / ".tradingcodex" / "mainagent" / "prompt-gate-history.jsonl", {
        "ts": now(),
        "workflow_run_id": gate["workflow_run_id"],
        "workflow_lane": gate["workflow_lane"],
        "required_subagents": gate["required_subagents"],
        "allowed_followup_team": loop_state["allowed_followup_team"],
        "escalation_team": loop_state["escalation_team"],
        "requires_subagent_dispatch": gate["requires_subagent_dispatch"],
        "activation_source": gate["activation_source"],
        "secret_warning": gate["secret_warning"],
        "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        "prompt_bytes": len(prompt.encode("utf-8")),
        "compact_context": compact_context,
        "starter_prompt_estimated_chars": len(gate["starter_prompt"]),
        "workflow_loop_state_path": loop_state_rel,
        "latest_workflow_loop_state_path": ".tradingcodex/mainagent/workflow-loop-state.json",
    })
    append_hook_audit({
        "event": "user-prompt-submit",
        "workflow_run_id": gate["workflow_run_id"],
        "workflow_lane": gate["workflow_lane"],
        "required_subagents": gate["required_subagents"],
        "workflow_loop_state_path": loop_state_rel,
        "latest_workflow_loop_state_path": ".tradingcodex/mainagent/workflow-loop-state.json",
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
    run_id = resolve_workflow_run_id(payload)
    gate = read_json(workflow_gate_path(run_id), {}) if run_id else {}
    if not gate:
        gate = read_json(ROOT / ".tradingcodex" / "mainagent" / "latest-user-prompt-gate.json", {})
        run_id = run_id or gate.get("workflow_run_id")
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


def build_initial_loop_state(gate: dict, plan: dict, compact_context: dict) -> dict:
    selected_team = plan.get("selectedTeam") or plan.get("subagents") or []
    allowed_followup_team = plan.get("allowedFollowupTeam") or compact_context.get("allowed_followup_team") or []
    escalation_team = plan.get("escalationTeam") or compact_context.get("escalation_team") or []
    pending_tasks = [
        {
            "task_id": f"{gate['workflow_run_id']}:{role}:initial",
            "role": role,
            "task_type": "initial_dispatch",
            "status": "pending",
            "planner_action": "downstream_handoff",
            "delta_brief": "Initial selected-team dispatch from user prompt gate.",
        }
        for role in selected_team
    ]
    return {
        "workflow_run_id": gate["workflow_run_id"],
        "lane": gate["workflow_lane"],
        "state_path": gate.get("workflow_loop_state_path") or loop_state_relpath(gate["workflow_run_id"]),
        "latest_state_path": ".tradingcodex/mainagent/workflow-loop-state.json",
        "session_key": gate.get("session_key", ""),
        "iteration": 0,
        "loop_policy": plan.get("loopPolicy") or compact_context.get("loop_policy") or {},
        "selected_team": selected_team,
        "allowed_followup_team": allowed_followup_team,
        "escalation_team": escalation_team,
        "pending_tasks": pending_tasks,
        "completed_artifacts": [],
        "loop_decisions": [
            {
                "ts": now(),
                "planner_action": "waiting" if pending_tasks else "synthesize",
                "reason": "Initial prompt gate wrote assisted loop state; hooks do not auto-spawn subagents.",
            }
        ],
        "escalation_proposals": [],
        "blocked_actions": (compact_context.get("routing_status") or {}).get("blocked_actions") or compact_context.get("blocked_actions") or [],
        "stop_reason": "waiting_for_selected_subagents" if pending_tasks else "head_manager_lane",
        "state_mode": "inspectable_assisted_loop",
        "auto_spawn": False,
        "recursive_hook_dispatch": False,
        "updated_at": now(),
    }


def update_loop_state_for_subagent_event(event: str, role: str, record: dict) -> None:
    state = read_json(loop_state_path(record.get("run_id")), {}) if record.get("run_id") else {}
    if not state or state.get("workflow_run_id") != record.get("run_id"):
        return
    pending = state.get("pending_tasks") if isinstance(state.get("pending_tasks"), list) else []
    for task in pending:
        if task.get("role") == role and task.get("status") in {"pending", "active"}:
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


def workflow_gate_path(run_id) -> Path:
    return ROOT / ".tradingcodex" / "mainagent" / "workflows" / safe_id(run_id) / "prompt-gate.json"


def compact_loop_summary(state: dict) -> dict:
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
    gate = read_json(ROOT / ".tradingcodex" / "mainagent" / "latest-user-prompt-gate.json", {})
    return str(gate.get("workflow_run_id") or "")


def subagent_session_id(payload: dict, run_id: str, role: str) -> str:
    for key in ("agent_session_id", "subagent_session_id", "subagent_id", "agent_id", "thread_id", "conversation_id"):
        if payload.get(key):
            return str(payload[key])
    return f"{run_id}:{role}"


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

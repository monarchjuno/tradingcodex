#!/usr/bin/env python3
import hashlib
import json
import os
import re
import sys
import urllib.request
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
    is_investment_workflow_request,
    is_secret_only_request,
    is_secret_warning_request,
)
from tradingcodex_cli.service_autostart import DEFAULT_SERVICE_ADDR, service_http_url
from tradingcodex_service.application.runtime import tradingcodex_db_path, tradingcodex_home
from tradingcodex_service.version import TRADINGCODEX_VERSION

ROOT = Path("{{PROJECT_DIR}}")
MAX_SESSION_EVENTS = 12
MAX_COMPLETED_RECORDS = 12
UPDATE_PREFERENCES_REL = "preferences/update.json"
PYPI_JSON_URL = "https://pypi.org/pypi/tradingcodex/json"


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
        server_status = build_server_status()
    except Exception as exc:
        server_status = fallback_server_status(exc)
        append_hook_audit({"event": "session-start", "warning": "server status check failed", "error": str(exc)})
    readiness = {
        "spawn_requested": False,
        "natural_language_investment_routing": True,
        "explicit_user_request_required": False,
        "startup_health": {
            "status_path": ".tradingcodex/mainagent/server-status.json",
            "dashboard_url": server_status["dashboard_url"],
            "service_status": server_status["service_status"],
            "restart_codex_required": server_status["restart_codex_required"],
            "update_status": server_status["update_status"],
            "recommended_action": server_status["recommended_action"],
        },
        "subagents": EXPECTED_SUBAGENTS,
        "local_cli": {
            "command": "./tcx",
            "plan_all": "./tcx subagents plan --all",
            "service_ensure": "./tcx service ensure",
        },
        "spawn_tool_notes": [
            "use spawn_agent agent_type only when the active schema can select the exact fixed role",
            "treat missing fixed-role selection as routing-unverified and fail closed",
            "do not pass model, reasoning, or service-tier overrides for fixed roles",
        ],
    }
    write_json(ROOT / ".tradingcodex" / "mainagent" / "session-start.json", readiness)
    write_json(ROOT / ".tradingcodex" / "mainagent" / "server-status.json", server_status)
    append_hook_audit({"event": "session-start", "readiness": readiness})


def user_prompt_submit(payload: dict) -> None:
    prompt = payload.get("prompt") or payload.get("user_prompt") or payload.get("message") or ""
    if not prompt:
        return
    agent_type = payload.get("agent_type") or payload.get("subagent_type")
    if agent_type in EXPECTED_SUBAGENTS:
        return
    secret_warning = is_secret_warning_request(prompt)
    secret_only = is_secret_only_request(prompt)
    investment_request = is_investment_workflow_request(prompt) and not secret_only
    if not investment_request and not secret_warning:
        return
    plan = classify_starter_request(prompt) if investment_request else {"lane": "secret_warning", "subagents": []}
    explicit = any(token in prompt.lower() for token in ["subagent", "parallel", "delegated", "$orchestrate-workflow", "서브에이전트"])
    activation_source = "explicit_subagent" if explicit else "auto_routed_investment_request"
    if not investment_request:
        activation_source = "secret_warning_only"
    compact_context = build_compact_dispatch_context(prompt) if investment_request else {
        "context_mode": "compact_workflow_gate_v1",
        "workflow_lane": plan["lane"],
        "required_subagents": [],
        "starter_prompt_path": ".tradingcodex/mainagent/latest-user-prompt-gate.json",
        "blocked_actions": ["secret storage", "secret echo", "raw credential handling"],
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
        "required_subagents": plan["subagents"],
        "starter_prompt": build_subagent_starter_prompt(prompt) if investment_request else "",
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


def build_server_status() -> dict:
    addr = os.environ.get("TRADINGCODEX_SERVICE_ADDR", DEFAULT_SERVICE_ADDR)
    dashboard_url = safe_service_http_url(addr)
    health_url = f"{dashboard_url.rstrip('/')}/api/health"
    mcp_config_present = is_project_mcp_config_present()
    update_status = build_update_status()
    health = read_health(health_url)
    service_status = "not_running_or_unreachable"
    if health:
        service_status = "ok" if is_compatible_health(health) else "incompatible"
    restart_codex_required = not mcp_config_present
    if restart_codex_required:
        recommended_action = "Run ./tcx update or ./tcx attach ., then fully quit and restart Codex and start a new thread."
    elif service_status == "ok":
        recommended_action = f"Open TradingCodex dashboard at {dashboard_url}"
    elif service_status == "incompatible":
        recommended_action = "Resolve the TradingCodex service version or central DB mismatch before using the dashboard."
    else:
        recommended_action = "./tcx service ensure"
    return {
        "checked_at": now(),
        "service_addr": addr,
        "dashboard_url": dashboard_url,
        "health_url": health_url,
        "service_status": service_status,
        "service_health": health,
        "mcp_config_present": mcp_config_present,
        "restart_codex_required": restart_codex_required,
        "update_status": update_status,
        "recommended_action": recommended_action,
    }


def fallback_server_status(exc: Exception) -> dict:
    addr = os.environ.get("TRADINGCODEX_SERVICE_ADDR", DEFAULT_SERVICE_ADDR)
    dashboard_url = safe_service_http_url(addr)
    return {
        "checked_at": now(),
        "service_addr": addr,
        "dashboard_url": dashboard_url,
        "health_url": f"{dashboard_url.rstrip('/')}/api/health",
        "service_status": "unknown",
        "service_health": {},
        "mcp_config_present": is_project_mcp_config_present(),
        "restart_codex_required": False,
        "update_status": fallback_update_status(),
        "recommended_action": "./tcx doctor --layer service",
        "warning": f"server status check failed: {exc}",
    }


def build_update_status() -> dict:
    module_lock = read_json(ROOT / ".tradingcodex" / "generated" / "module-lock.json", {})
    preference_path = tradingcodex_home() / UPDATE_PREFERENCES_REL
    preferences = read_json(preference_path, {})
    workspace_version = str(module_lock.get("tradingcodex_version") or "unknown")
    installed_version = TRADINGCODEX_VERSION
    suppressed = bool(preferences.get("suppress_update_recommendation"))
    versions_match = workspace_version == installed_version
    workspace_update_available = workspace_version not in {"", "unknown"} and not versions_match
    if workspace_update_available:
        latest = latest_release_info()
    else:
        latest = {
            "latest_release_version": "not_checked",
            "latest_release_status": "not_needed",
            "latest_release_source": "versions_match" if versions_match else "workspace_version_unavailable",
        }
    latest_version = latest["latest_release_version"]
    latest_status = latest["latest_release_status"]
    installed_release_is_stale = latest_status == "ok" and version_less_than(installed_version, latest_version)
    package_update_required_first = workspace_update_available and installed_release_is_stale
    workspace_update_allowed = workspace_update_available and not package_update_required_first
    workspace_update_recommended = workspace_update_allowed and not suppressed
    blocked_reason = "installed tcx is older than the latest release; update the package before refreshing this workspace" if package_update_required_first else ""
    if package_update_required_first:
        recommended_action = "uvx --refresh --from tradingcodex tcx update ."
    elif workspace_update_recommended:
        recommended_action = "./tcx update"
    else:
        recommended_action = ""
    return {
        "checked_at": now(),
        "workspace_version": workspace_version,
        "installed_version": installed_version,
        "package_version": installed_version,
        "latest_release_version": latest_version,
        "latest_release_status": latest_status,
        "latest_release_source": latest["latest_release_source"],
        "versions_match": versions_match,
        "workspace_update_available": workspace_update_available,
        "workspace_update_allowed": workspace_update_allowed,
        "workspace_update_recommended": workspace_update_recommended,
        "package_update_required_first": package_update_required_first,
        "installed_is_latest_release": latest_status == "ok" and installed_version == latest_version,
        "installed_release_is_stale": installed_release_is_stale,
        "update_recommendation_suppressed": suppressed,
        "preference_path": str(preference_path),
        "recommended_action": recommended_action,
        "blocked_reason": blocked_reason,
    }


def fallback_update_status() -> dict:
    return {
        "checked_at": now(),
        "workspace_version": "unknown",
        "installed_version": TRADINGCODEX_VERSION,
        "package_version": TRADINGCODEX_VERSION,
        "latest_release_version": "unknown",
        "latest_release_status": "unknown",
        "latest_release_source": "fallback",
        "versions_match": False,
        "workspace_update_available": False,
        "workspace_update_allowed": False,
        "workspace_update_recommended": False,
        "package_update_required_first": False,
        "installed_is_latest_release": False,
        "installed_release_is_stale": False,
        "update_recommendation_suppressed": False,
        "preference_path": str(tradingcodex_home() / UPDATE_PREFERENCES_REL),
        "recommended_action": "",
        "blocked_reason": "",
    }


def latest_release_info() -> dict:
    override = os.environ.get("TRADINGCODEX_LATEST_RELEASE_VERSION", "").strip()
    if override:
        return {
            "latest_release_version": override,
            "latest_release_status": "ok",
            "latest_release_source": "env",
        }
    if os.environ.get("TRADINGCODEX_DISABLE_LATEST_RELEASE_CHECK", "").lower() in {"1", "true", "yes", "on"}:
        return {
            "latest_release_version": "unknown",
            "latest_release_status": "unknown",
            "latest_release_source": "disabled",
        }
    try:
        timeout = float(os.environ.get("TRADINGCODEX_LATEST_RELEASE_TIMEOUT", "0.75"))
        with urllib.request.urlopen(PYPI_JSON_URL, timeout=timeout) as response:
            payload = response.read().decode("utf-8")
        data = json.loads(payload)
        version = str((data.get("info") or {}).get("version") or "").strip()
        if version:
            return {
                "latest_release_version": version,
                "latest_release_status": "ok",
                "latest_release_source": "pypi",
            }
    except Exception:
        pass
    return {
        "latest_release_version": "unknown",
        "latest_release_status": "unknown",
        "latest_release_source": "unavailable",
    }


def version_less_than(left: str, right: str) -> bool:
    left_key = release_version_key(left)
    right_key = release_version_key(right)
    if not left_key or not right_key:
        return False
    length = max(len(left_key), len(right_key))
    return left_key + (0,) * (length - len(left_key)) < right_key + (0,) * (length - len(right_key))


def release_version_key(version: str) -> tuple[int, ...]:
    match = re.match(r"^\s*(\d+(?:\.\d+)*)", version)
    if not match:
        return ()
    return tuple(int(part) for part in match.group(1).split("."))


def safe_service_http_url(addr: str) -> str:
    try:
        return service_http_url(addr)
    except Exception:
        return service_http_url(DEFAULT_SERVICE_ADDR)


def is_project_mcp_config_present() -> bool:
    config_path = ROOT / ".codex" / "config.toml"
    try:
        text = config_path.read_text(encoding="utf-8")
    except Exception:
        return False
    return "[mcp_servers.tradingcodex]" in text and "TRADINGCODEX_MCP_AUTOSTART_SERVICE" in text


def read_health(url: str) -> dict:
    try:
        with urllib.request.urlopen(url, timeout=0.5) as response:
            payload = response.read().decode("utf-8")
        data = json.loads(payload)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def is_compatible_health(health: dict) -> bool:
    return (
        health.get("service") == "tradingcodex"
        and health.get("version") == TRADINGCODEX_VERSION
        and str(health.get("db_path") or "") == str(tradingcodex_db_path())
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

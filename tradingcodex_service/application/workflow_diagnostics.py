from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Any

DEFAULT_SUBAGENT_SPAWN_TIMEOUT_SECONDS = 3600


def pending_task_timeout_fields(created_at: str, timeout_seconds: int = DEFAULT_SUBAGENT_SPAWN_TIMEOUT_SECONDS) -> dict[str, Any]:
    created = _parse_iso(created_at) or _now_dt()
    timeout = max(1, int(timeout_seconds or DEFAULT_SUBAGENT_SPAWN_TIMEOUT_SECONDS))
    return {
        "created_at": created.isoformat().replace("+00:00", "Z"),
        "timeout_seconds": timeout,
        "spawn_deadline_at": (created + timedelta(seconds=timeout)).isoformat().replace("+00:00", "Z"),
    }


def workflow_validation_diagnostics(errors: list[str], warnings: list[str]) -> dict[str, Any]:
    causes = [_validation_cause(error) for error in errors]
    causes.extend(
        {
            "code": "workflow_plan_warning",
            "category": "validation",
            "severity": "warning",
            "message": warning,
        }
        for warning in warnings
    )
    if errors:
        status = "failed"
        reason_code = "workflow_plan_invalid"
        next_action = "fix_workflow_plan_and_validate_again"
    elif warnings:
        status = "warning"
        reason_code = "workflow_plan_warnings"
        next_action = "review_warnings_before_dispatch"
    else:
        status = "pass"
        reason_code = "workflow_plan_valid"
        next_action = "record_plan_then_dispatch_selected_roles"
    return {
        "status": status,
        "reason_code": reason_code,
        "causes": causes,
        "next_action": next_action,
        "inline_fallback": _inline_fallback(False, ""),
    }


def diagnose_workflow_loop_state(state: dict[str, Any], *, now_iso: str | None = None) -> dict[str, Any]:
    diagnosed = deepcopy(state)
    pending = diagnosed.get("pending_tasks") if isinstance(diagnosed.get("pending_tasks"), list) else []
    now = _parse_iso(now_iso or "") or _now_dt()
    causes: list[dict[str, Any]] = []
    for task in pending:
        if not isinstance(task, dict):
            continue
        if _task_spawn_timed_out(task, now):
            task["status"] = "timed_out"
            failure = _spawn_timeout_failure(task, now)
            task["failure"] = failure
            causes.append(failure)
    if causes:
        diagnostics = {
            "status": "failed",
            "reason_code": "subagent_spawn_timeout",
            "causes": causes,
            "next_action": "report_waiting_timeout_and_retry_or_return_inline_status",
            "inline_fallback": _inline_fallback(True, "subagent_spawn_timeout"),
        }
        diagnosed["stop_reason"] = "subagent_spawn_timeout"
        diagnosed["terminal_action"] = diagnosed.get("terminal_action") or "waiting"
    elif pending:
        diagnostics = {
            "status": "waiting",
            "reason_code": str(diagnosed.get("stop_reason") or "waiting_for_subagent_dispatch"),
            "causes": [_waiting_cause(task) for task in pending if isinstance(task, dict)],
            "next_action": "dispatch_or_wait_for_pending_subagent_tasks",
            "inline_fallback": _inline_fallback(False, ""),
        }
    else:
        diagnostics = {
            "status": "ready",
            "reason_code": str(diagnosed.get("stop_reason") or "no_pending_subagent_tasks"),
            "causes": [],
            "next_action": "verify_artifacts_then_synthesize_or_block",
            "inline_fallback": _inline_fallback(False, ""),
        }
    diagnosed["pending_tasks"] = pending
    diagnosed["diagnostics"] = diagnostics
    return diagnosed


def _validation_cause(error: str) -> dict[str, Any]:
    lowered = error.lower()
    if "unknown role" in lowered:
        code = "unknown_role"
    elif "depends on unknown or later stage" in lowered:
        code = "invalid_stage_dependency"
    elif "negated" in lowered:
        code = "negated_scope_violation"
    elif "execution-operator" in lowered:
        code = "invalid_execution_role"
    elif "must not dispatch judgment-reviewer" in lowered:
        code = "invalid_judgment_review"
    else:
        code = "workflow_plan_invalid"
    return {
        "code": code,
        "category": "validation",
        "severity": "error",
        "message": error,
    }


def _task_spawn_timed_out(task: dict[str, Any], now: datetime) -> bool:
    if task.get("status") != "pending":
        return False
    deadline = _parse_iso(str(task.get("spawn_deadline_at") or ""))
    if deadline is None:
        created = _parse_iso(str(task.get("created_at") or ""))
        if created is None:
            return False
        timeout = max(1, int(task.get("timeout_seconds") or DEFAULT_SUBAGENT_SPAWN_TIMEOUT_SECONDS))
        deadline = created + timedelta(seconds=timeout)
    return now >= deadline


def _spawn_timeout_failure(task: dict[str, Any], now: datetime) -> dict[str, Any]:
    created = _parse_iso(str(task.get("created_at") or "")) or now
    timeout = max(1, int(task.get("timeout_seconds") or DEFAULT_SUBAGENT_SPAWN_TIMEOUT_SECONDS))
    elapsed = max(0, int((now - created).total_seconds()))
    return {
        "code": "subagent_spawn_timeout",
        "reason_code": "subagent_spawn_timeout",
        "category": "dispatch",
        "severity": "error",
        "task_id": str(task.get("task_id") or ""),
        "stage_id": str(task.get("stage_id") or ""),
        "roles": task.get("roles") if isinstance(task.get("roles"), list) else [task.get("role")],
        "elapsed_seconds": elapsed,
        "timeout_seconds": timeout,
        "message": "Pending subagent dispatch exceeded the configured spawn timeout.",
    }


def _waiting_cause(task: dict[str, Any]) -> dict[str, Any]:
    return {
        "code": "pending_subagent_dispatch",
        "category": "dispatch",
        "severity": "info",
        "task_id": str(task.get("task_id") or ""),
        "stage_id": str(task.get("stage_id") or ""),
        "roles": task.get("roles") if isinstance(task.get("roles"), list) else [task.get("role")],
        "message": "Subagent task is still waiting for dispatch or completion.",
    }


def _inline_fallback(recommended: bool, reason_code: str) -> dict[str, Any]:
    return {
        "recommended": recommended,
        "reason_code": reason_code,
        "mode": "status_only_no_investment_analysis" if recommended else "",
        "message": (
            "Return a concise waiting/blocked status and retry guidance; do not fill fixed-role investment analysis inline."
            if recommended
            else ""
        ),
    }


def _parse_iso(value: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _now_dt() -> datetime:
    return datetime.now(timezone.utc)

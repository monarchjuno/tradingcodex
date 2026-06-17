from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tradingcodex_service.application.artifact_quality import evaluate_artifact_quality, estimate_tokens
from tradingcodex_service.application.research import list_workspace_research_artifacts

MAX_HOOK_CONTEXT_TOKENS = 500
MAX_STARTER_PROMPT_TOKENS = 1200
MAX_SESSION_STATE_TOKENS = 2000
MAX_CONTEXT_SUMMARY_CHARS = 1200
LARGE_ARTIFACT_BODY_TOKENS = 6000


def audit_context_budget(workspace_root: Path | str, *, strict: bool = False) -> dict[str, Any]:
    root = Path(workspace_root)
    gate_path = root / ".tradingcodex" / "mainagent" / "latest-user-prompt-gate.json"
    gate_history_path = root / ".tradingcodex" / "mainagent" / "prompt-gate-history.jsonl"
    state_path = root / ".tradingcodex" / "mainagent" / "subagent-session-state.json"
    gate = _read_json(gate_path, {})
    gate_history = _read_jsonl(gate_history_path)
    state = _read_json(state_path, {"active": {}, "completed": [], "events": []})
    checks: list[dict[str, Any]] = []
    warnings: list[str] = []

    compact_context = gate.get("compact_additional_context") if isinstance(gate.get("compact_additional_context"), dict) else {}
    compact_text = _json_text(compact_context)
    starter_prompt = str(gate.get("starter_prompt") or "")
    gate_text = _json_text(gate)
    state_text = _json_text(state)
    state_events = state.get("events", []) if isinstance(state.get("events"), list) else []
    state_completed = state.get("completed", []) if isinstance(state.get("completed"), list) else []
    state_event_count_total = int(state.get("event_count_total") or len(state_events))
    state_completed_count_total = int(state.get("completed_count_total") or len(state_completed))

    if not gate:
        warnings.append("no latest prompt gate found; run an investment workflow before strict context-budget audit")
    if strict and not gate_history:
        warnings.append("no prompt gate history found; run routed workflow prompts with the current hook before strict context-budget audit")
    _add_check(
        checks,
        "latest prompt gate exists",
        bool(gate) if strict else True,
        detail=str(gate_path.relative_to(root)),
        strict=strict,
    )
    _add_check(
        checks,
        "hook additional context stays compact",
        estimate_tokens(compact_text) <= MAX_HOOK_CONTEXT_TOKENS,
        estimated_tokens=estimate_tokens(compact_text),
        limit_tokens=MAX_HOOK_CONTEXT_TOKENS,
    )
    _add_check(
        checks,
        "hook additional context excludes full starter prompt",
        "starter_prompt" not in compact_context,
    )
    history_records = _prompt_history_records(gate_history)
    history_oversized = [
        record
        for record in history_records
        if int(record["compact_context_estimated_tokens"] or 0) > MAX_HOOK_CONTEXT_TOKENS
    ]
    history_prompt_leaks = [
        record["workflow_run_id"]
        for record in gate_history
        if "starter_prompt" in record or "original_prompt" in record or "prompt" in record
    ]
    history_artifact_leaks = [
        record["workflow_run_id"]
        for record in gate_history
        if _looks_like_pasted_markdown_artifact(_json_text(record))
    ]
    _add_check(
        checks,
        "prompt gate history exists",
        bool(gate_history) if strict else True,
        entries=len(gate_history),
        strict=strict,
    )
    _add_check(
        checks,
        "prompt gate history stays compact",
        not history_oversized,
        max_estimated_tokens=max((record["compact_context_estimated_tokens"] for record in history_records), default=0),
        limit_tokens=MAX_HOOK_CONTEXT_TOKENS,
        oversized=history_oversized,
    )
    _add_check(
        checks,
        "prompt gate history excludes raw prompts and full starter prompts",
        not history_prompt_leaks,
        leaked_workflow_run_ids=history_prompt_leaks,
    )
    _add_check(
        checks,
        "prompt gate history avoids pasted markdown artifacts",
        not history_artifact_leaks,
        leaked_workflow_run_ids=history_artifact_leaks,
    )
    _add_check(
        checks,
        "starter prompt stays bounded",
        not starter_prompt or estimate_tokens(starter_prompt) <= MAX_STARTER_PROMPT_TOKENS,
        estimated_tokens=estimate_tokens(starter_prompt),
        limit_tokens=MAX_STARTER_PROMPT_TOKENS,
    )
    _add_check(
        checks,
        "starter prompt names context budget",
        not gate.get("requires_subagent_dispatch") or "Context budget:" in starter_prompt,
    )
    _add_check(
        checks,
        "gate and state avoid pasted markdown artifacts",
        not _looks_like_pasted_markdown_artifact(gate_text + "\n" + state_text),
    )
    _add_check(
        checks,
        "subagent session state stays compact",
        estimate_tokens(state_text) <= MAX_SESSION_STATE_TOKENS,
        estimated_tokens=estimate_tokens(state_text),
        limit_tokens=MAX_SESSION_STATE_TOKENS,
        retained_events=len(state_events),
        total_events=state_event_count_total,
    )

    artifact_records = []
    missing_context_summary = []
    oversized_context_summary = []
    large_artifacts = []
    for artifact in list_workspace_research_artifacts(root, include_markdown=False):
        quality = evaluate_artifact_quality(root, artifact["path"], strict=False)
        context_efficiency = quality.get("context_efficiency", {})
        record = {
            "artifact_id": artifact.get("artifact_id"),
            "path": artifact.get("path"),
            "role": artifact.get("role"),
            "handoff_state": artifact.get("handoff_state"),
            "context_summary_chars": context_efficiency.get("context_summary_chars", 0),
            "context_summary_present": context_efficiency.get("context_summary_present", False),
            "body_estimated_tokens": context_efficiency.get("body_estimated_tokens", 0),
        }
        artifact_records.append(record)
        if not record["context_summary_present"]:
            missing_context_summary.append(record["path"])
        if int(record["context_summary_chars"] or 0) > MAX_CONTEXT_SUMMARY_CHARS:
            oversized_context_summary.append(record["path"])
        if int(record["body_estimated_tokens"] or 0) > LARGE_ARTIFACT_BODY_TOKENS:
            large_artifacts.append(record)

    context_summary_ok = not missing_context_summary if strict else True
    if missing_context_summary:
        warnings.append(f"{len(missing_context_summary)} research artifact(s) missing context_summary")
    _add_check(
        checks,
        "research artifacts expose context summaries",
        context_summary_ok,
        artifacts_checked=len(artifact_records),
        missing=missing_context_summary,
        strict=strict,
    )
    if oversized_context_summary:
        warnings.append(f"{len(oversized_context_summary)} research artifact context_summary value(s) exceed {MAX_CONTEXT_SUMMARY_CHARS} characters")
    _add_check(
        checks,
        "context summaries stay concise",
        not oversized_context_summary,
        limit_chars=MAX_CONTEXT_SUMMARY_CHARS,
        oversized=oversized_context_summary,
    )
    if large_artifacts:
        warnings.append(
            "large research artifacts detected; downstream roles should consume artifact path, context_summary, and targeted excerpts"
        )

    failed_checks = [check for check in checks if check["status"] == "fail"]
    return {
        "status": "fail" if failed_checks else "pass",
        "strict": strict,
        "checks": checks,
        "warnings": warnings,
        "latest_gate": {
            "path": ".tradingcodex/mainagent/latest-user-prompt-gate.json",
            "workflow_run_id": gate.get("workflow_run_id"),
            "workflow_lane": gate.get("workflow_lane"),
            "required_subagents": gate.get("required_subagents", []),
            "compact_context_estimated_tokens": estimate_tokens(compact_text),
            "starter_prompt_estimated_tokens": estimate_tokens(starter_prompt),
        },
        "prompt_gate_history": {
            "path": ".tradingcodex/mainagent/prompt-gate-history.jsonl",
            "entries": len(gate_history),
            "max_compact_context_estimated_tokens": max((record["compact_context_estimated_tokens"] for record in history_records), default=0),
            "workflow_lanes": list(dict.fromkeys(record["workflow_lane"] for record in history_records if record["workflow_lane"])),
            "records": history_records,
        },
        "session_state": {
            "path": ".tradingcodex/mainagent/subagent-session-state.json",
            "estimated_tokens": estimate_tokens(state_text),
            "active_count": len(state.get("active", {}) if isinstance(state.get("active"), dict) else {}),
            "completed_count": state_completed_count_total,
            "retained_completed_count": len(state_completed),
            "event_count": state_event_count_total,
            "retained_event_count": len(state_events),
            "retention": state.get("retention", {}),
        },
        "artifacts": {
            "checked": len(artifact_records),
            "missing_context_summary": missing_context_summary,
            "oversized_context_summary": oversized_context_summary,
            "large_body_count": len(large_artifacts),
            "large_bodies": large_artifacts,
            "records": artifact_records,
        },
        "recommended_handoff": "pass artifact path plus context_summary first; open full markdown only for load-bearing evidence, stale-source, or disagreement checks",
    }


def _add_check(checks: list[dict[str, Any]], name: str, ok: bool, **extra: Any) -> None:
    checks.append({"name": name, "status": "pass" if ok else "fail", **extra})


def _looks_like_pasted_markdown_artifact(text: str) -> bool:
    lowered = text.lower()
    return "\n---\n" in text and ("artifact_id:" in lowered or "artifact_type:" in lowered)


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception:
        return records
    for line in lines:
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except Exception:
            continue
        if isinstance(record, dict):
            records.append(record)
    return records


def _json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _prompt_history_records(gate_history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for record in gate_history:
        compact_context = record.get("compact_context") if isinstance(record.get("compact_context"), dict) else {}
        records.append(
            {
                "workflow_run_id": record.get("workflow_run_id"),
                "workflow_lane": record.get("workflow_lane"),
                "required_subagents": record.get("required_subagents", []),
                "activation_source": record.get("activation_source"),
                "compact_context_estimated_tokens": estimate_tokens(_json_text(compact_context)),
                "starter_prompt_estimated_chars": record.get("starter_prompt_estimated_chars", 0),
            }
        )
    return records

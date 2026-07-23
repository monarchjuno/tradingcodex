from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tradingcodex_service.application.common import atomic_write_text, exclusive_file_lock, file_hash, now_iso, safe_workspace_path, sanitize_id, stable_hash
from tradingcodex_service.application.decision_packages import verify_decision_snapshot
from tradingcodex_service.application.forecasting import get_forecast, is_forecast_event_anchored
from tradingcodex_service.application.runtime import workspace_context_payload


POSTMORTEM_ROOT = Path("trading/reports/postmortem")
POSTMORTEM_SCHEMA_VERSION = 1
IMPROVE_LEDGER_PATH = Path(".tradingcodex/mainagent/improve.jsonl")
LESSON_HEADS_PATH = Path(".tradingcodex/mainagent/lesson-chain-heads.json")
LESSON_STATES = {
    "candidate",
    "corroborated",
    "validated",
    "retired",
}
LESSON_TRANSITIONS = {
    "candidate": {"corroborated", "retired"},
    "corroborated": {"validated", "retired"},
    "validated": {"retired"},
    "retired": set(),
}
JUDGMENT_REVIEW_FIELDS = (
    "original_thesis",
    "what_happened",
    "failed_assumption",
    "role_evidence_miss_or_overstatement",
    "stale_weak_or_misleading_source",
    "confidence_calibration",
    "future_warning_pattern",
)
PROCESS_REVIEW_FIELDS = (
    "original_thesis",
    "evidence_quality",
    "base_rate_quality",
    "alternatives_considered",
    "assumptions",
    "confidence_process",
    "invalidation_discipline",
    "handoff_process",
    "process_conclusion",
)


def record_postmortem_process_review(workspace_root: Path | str, args: dict[str, Any]) -> dict[str, Any]:
    root = Path(workspace_root).expanduser().resolve()
    decision_id = _required_text(args, "decision_snapshot_id")
    decision_result = verify_decision_snapshot(root, decision_id)
    decision = decision_result["decision_snapshot"]
    decision_path = safe_workspace_path(root, decision_result["export_path"], allowed_roots=(Path("trading/decisions"),))
    created_by = _required_text(args, "created_by")
    if created_by != "head-manager":
        raise PermissionError("postmortem process reviews must be recorded by head-manager")
    locked_at = _system_now()
    review = _required_text_object(args.get("process_review"), PROCESS_REVIEW_FIELDS, "process_review")
    seed = {"decision_snapshot_hash": decision["snapshot_hash"], "process_review": review}
    review_id = sanitize_id(args.get("id") or f"process-review-{stable_hash(seed)[:16]}")
    document = {
        "schema_version": 1,
        "artifact_type": "postmortem_process_review",
        "id": review_id,
        "created_by": created_by,
        "locked_at": locked_at,
        "recorded_at": locked_at,
        "outcome_blind": True,
        "decision_snapshot_ref": {
            "decision_id": decision_id,
            "path": decision_result["export_path"],
            "sha256": file_hash(decision_path),
            "snapshot_hash": decision["snapshot_hash"],
        },
        "process_review": review,
        "authority": "evidence_only",
        "blocked_actions": ["policy_change", "skill_change", "order_approval", "order_execution"],
    }
    document["process_review_hash"] = stable_hash(document)
    path = safe_workspace_path(root, POSTMORTEM_ROOT / f"{review_id}.process-review.json", allowed_roots=(POSTMORTEM_ROOT,))
    if path.exists():
        stored = _read_process_review(path)
        if stored.get("decision_snapshot_ref") != document.get("decision_snapshot_ref") or stored.get("process_review") != review:
            raise ValueError(f"postmortem process review is immutable and already exists: {review_id}")
        status = "existing"
    else:
        if _iso(decision.get("recorded_at"), "decision snapshot recorded_at") > locked_at:
            raise ValueError("postmortem process review cannot predate its decision snapshot")
        _require_outcome_blind_process_review(root, decision)
        stored, status = _store_immutable_document(path, document, "process_review_hash", "postmortem process review")
    return {
        "status": status,
        "process_review": stored,
        "export_path": path.relative_to(root).as_posix(),
        "workspace_native": True,
        "authority": "evidence_only",
        "workspace_context": workspace_context_payload(root),
    }


def create_postmortem(workspace_root: Path | str, args: dict[str, Any]) -> dict[str, Any]:
    root = Path(workspace_root).expanduser().resolve()
    decision_id = _required_text(args, "decision_snapshot_id")
    decision_result = verify_decision_snapshot(root, decision_id)
    decision = decision_result["decision_snapshot"]
    decision_path = safe_workspace_path(root, decision_result["export_path"], allowed_roots=(Path("trading/decisions"),))
    process_review_id = _required_text(args, "process_review_id")
    process_review_path = safe_workspace_path(
        root,
        POSTMORTEM_ROOT / f"{sanitize_id(process_review_id)}.process-review.json",
        allowed_roots=(POSTMORTEM_ROOT,),
    )
    process_review = _read_process_review(process_review_path)
    process_decision_ref = process_review.get("decision_snapshot_ref") if isinstance(process_review.get("decision_snapshot_ref"), dict) else {}
    if process_decision_ref.get("decision_id") != decision_id or process_decision_ref.get("snapshot_hash") != decision.get("snapshot_hash"):
        raise ValueError("postmortem process review does not match the decision snapshot")
    findings = _required_object_list(args, "findings")
    review = _judgment_review(args.get("investment_judgment_review"))
    next_actions = _required_list(args, "next_actions")
    lessons = _lesson_candidates(args.get("lesson_candidates"), decision)
    outcome_refs = _forecast_outcome_refs(root, decision, args.get("forecast_ids"), str(process_review["locked_at"]))
    default_known_at = max(
        [str(ref.get("observed_at") or ref.get("resolved_at") or "") for ref in outcome_refs]
        + [str(decision.get("decided_at") or "")]
    )
    known_at = _iso(default_known_at, "known_at")
    recorded_at = _system_now()
    if recorded_at < known_at:
        raise ValueError("postmortem cannot be recorded before its outcome is knowable")
    created_by = _required_text(args, "created_by")
    if created_by != "head-manager":
        raise PermissionError("postmortems must be recorded by head-manager")
    seed = {
        "decision_snapshot_hash": decision["snapshot_hash"],
        "process_review_hash": process_review["process_review_hash"],
        "forecast_event_hashes": [ref["event_hash"] for ref in outcome_refs],
        "trigger": _required_text(args, "trigger"),
        "known_at": known_at,
    }
    report_id = sanitize_id(args.get("id") or f"postmortem-{stable_hash(seed)[:16]}")
    report = {
        "schema_version": POSTMORTEM_SCHEMA_VERSION,
        "artifact_type": "postmortem_report",
        "id": report_id,
        "created_by": created_by,
        "created_at": recorded_at,
        "recorded_at": recorded_at,
        "known_at": known_at,
        "trigger": seed["trigger"],
        "evidence_lane": decision["evidence_lane"],
        "decision_snapshot_ref": {
            "decision_id": decision_id,
            "path": decision_result["export_path"],
            "sha256": file_hash(decision_path),
            "snapshot_hash": decision["snapshot_hash"],
        },
        "process_review_ref": {
            "id": process_review_id,
            "path": process_review_path.relative_to(root).as_posix(),
            "sha256": file_hash(process_review_path),
            "process_review_hash": process_review["process_review_hash"],
            "locked_at": process_review["locked_at"],
        },
        "forecast_outcome_refs": outcome_refs,
        "findings": findings,
        "investment_judgment_review": review,
        "next_actions": next_actions,
        "lesson_candidates": lessons,
        "authority": "evidence_only",
        "blocked_actions": ["policy_change", "skill_change", "order_approval", "order_execution"],
    }
    report["report_hash"] = stable_hash(report)
    path = safe_workspace_path(root, POSTMORTEM_ROOT / f"{report_id}.postmortem_report.json", allowed_roots=(POSTMORTEM_ROOT,))
    with exclusive_file_lock(path):
        if path.exists():
            existing = _read_postmortem(path)
            ignored = {"created_at", "recorded_at", "report_hash"}
            if stable_hash({key: value for key, value in existing.items() if key not in ignored}) != stable_hash(
                {key: value for key, value in report.items() if key not in ignored}
            ):
                raise ValueError(f"postmortem is immutable and already exists: {report_id}")
            report = existing
            status = "existing"
        else:
            atomic_write_text(path, json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n")
            status = "recorded"
    improvement_records = _record_lesson_candidates(root, report, path)
    return {
        "status": status,
        "postmortem": report,
        "lesson_records": improvement_records,
        "export_path": path.relative_to(root).as_posix(),
        "workspace_native": True,
        "authority": "evidence_only",
        "workspace_context": workspace_context_payload(root),
    }


def get_postmortem(workspace_root: Path | str, report_id: str) -> dict[str, Any]:
    root = Path(workspace_root).expanduser().resolve()
    path = safe_workspace_path(root, POSTMORTEM_ROOT / f"{sanitize_id(report_id)}.postmortem_report.json", allowed_roots=(POSTMORTEM_ROOT,))
    if not path.exists():
        raise ValueError(f"postmortem not found: {report_id}")
    report = _read_postmortem(path)
    if report.get("schema_version") == 2:
        from tradingcodex_service.application.judgment_postmortems import verify_judgment_postmortem
        verify_judgment_postmortem(root, report)
    else:
        _verify_postmortem_refs(root, report)
    return {
        "status": "ok",
        "postmortem": report,
        "verification_status": "verified",
        "export_path": path.relative_to(root).as_posix(),
        "workspace_native": True,
        "authority": "evidence_only",
        "workspace_context": workspace_context_payload(root),
    }


def list_postmortems(workspace_root: Path | str, limit: int = 50) -> dict[str, Any]:
    root = Path(workspace_root).expanduser().resolve()
    items = []
    for path in sorted((root / POSTMORTEM_ROOT).glob("*.postmortem_report.json")):
        report = _read_postmortem(path)
        if report.get("schema_version") == 2:
            from tradingcodex_service.application.judgment_postmortems import verify_judgment_postmortem
            verify_judgment_postmortem(root, report)
        else:
            _verify_postmortem_refs(root, report)
        items.append({
            "id": report.get("id", ""),
            "decision_id": (report.get("decision_snapshot_ref") or {}).get("decision_id", "") or (report.get("judgment_snapshot_ref") or {}).get("judgment_id", ""),
            "evidence_lane": report.get("evidence_lane", ""),
            "known_at": report.get("known_at", ""),
            "lesson_count": len(report.get("lesson_candidates") or []),
            "report_hash": report.get("report_hash", ""),
            "path": path.relative_to(root).as_posix(),
            "verification_status": "verified",
        })
    items.sort(key=lambda item: str(item["known_at"]), reverse=True)
    return {
        "postmortems": items[: max(1, min(int(limit), 200))],
        "count": len(items),
        "workspace_native": True,
        "authority": "evidence_only",
        "workspace_context": workspace_context_payload(root),
    }


def promote_lesson(
    workspace_root: Path | str,
    args: dict[str, Any],
    *,
    authenticated_principal: str,
) -> dict[str, Any]:
    root = Path(workspace_root).expanduser().resolve()
    if authenticated_principal != "judgment-reviewer":
        raise PermissionError("lesson promotion requires an authenticated judgment-reviewer")
    claimed_reviewer = str(args.get("reviewed_by") or authenticated_principal)
    if claimed_reviewer != authenticated_principal:
        raise PermissionError("claimed reviewer does not match the authenticated principal")
    lesson_id = _required_text(args, "lesson_id")
    ledger = root / IMPROVE_LEDGER_PATH
    ledger.parent.mkdir(parents=True, exist_ok=True)
    with exclusive_file_lock(ledger):
        histories, heads = _read_lesson_chains(root, ledger)
        records = histories.get(lesson_id) or []
        if not records:
            raise ValueError(f"lesson not found: {lesson_id}")
        current = records[-1]
        from_state = str(current.get("lesson_state") or "candidate")
        to_state = _required_text(args, "to_state")
        if to_state not in LESSON_STATES:
            raise ValueError(f"to_state must be one of: {', '.join(sorted(LESSON_STATES))}")
        if to_state not in LESSON_TRANSITIONS.get(from_state, set()):
            raise ValueError(f"invalid lesson transition: {from_state} -> {to_state}")
        if authenticated_principal == str(current.get("created_by") or ""):
            raise ValueError("lesson promotion reviewer must differ from the lesson author")
        evidence_refs = _verified_evidence_refs(root, args.get("evidence_refs")) if to_state != "retired" else []
        regimes = sorted({str(item).strip() for item in (args.get("regimes") or []) if str(item).strip()})
        if to_state != "retired" and not regimes:
            raise ValueError("lesson promotion requires at least one explicit regime")
        used_episode_ids = {str(item) for item in current.get("used_episode_ids") or [] if str(item)}
        evidence_episode_ids = {str(ref.get("episode_id") or "") for ref in evidence_refs} - {""}
        if to_state == "corroborated" and len(used_episode_ids | evidence_episode_ids) < 2:
            raise ValueError("corroborated requires evidence from at least two independent snapshot-root episodes")
        if to_state == "validated":
            validation_refs = [ref for ref in evidence_refs if _supports_validation(ref)]
            validation_episode_ids = {str(ref.get("episode_id") or "") for ref in validation_refs} - {""}
            if not validation_refs or not validation_episode_ids - used_episode_ids:
                raise ValueError("validated requires an untouched scored holdout or live-forward snapshot-root episode")
        combined_episode_ids = sorted(used_episode_ids | evidence_episode_ids)
        evidence_known_at = [str(ref.get("known_at") or "") for ref in evidence_refs if ref.get("known_at")]
        known_at = max([str(current.get("known_at") or ""), *evidence_known_at])
        recorded_at = _system_now()
        if known_at and _iso(known_at, "lesson known_at") > recorded_at:
            raise ValueError("lesson evidence is not knowable at system recorded_at")
        base = {
            "lesson_id": lesson_id,
            "prior_improvement_id": current.get("improvement_id"),
            "from_state": from_state,
            "to_state": to_state,
            "reviewer": authenticated_principal,
            "known_at": known_at,
            "evidence_refs": evidence_refs,
            "regimes": regimes,
            "reason": _required_text(args, "reason"),
        }
        event = {
            **current,
            "improvement_id": "improve-event-" + stable_hash(base)[:16],
            "event_type": "lesson_state_changed",
            "lesson_state": to_state,
            "status": "retired" if to_state == "retired" else "reviewed",
            "review_state": "independently_reviewed",
            "reuse_state": "available_for_future_judgment" if to_state == "validated" else "retired" if to_state == "retired" else "candidate",
            "prior_improvement_id": current.get("improvement_id"),
            "reviewed_by": authenticated_principal,
            "review_reason": base["reason"],
            "evidence_refs": evidence_refs,
            "used_episode_ids": combined_episode_ids,
            "regimes": regimes,
            "known_at": known_at,
            "recorded_at": recorded_at,
        }
        event = _seal_lesson_event(event, current)
        _append_records_unlocked(ledger, [event])
        _update_lesson_heads(root, heads, [event])
    return {
        "status": "recorded",
        "lesson": event,
        "ledger_path": IMPROVE_LEDGER_PATH.as_posix(),
        "authority": "evidence_only",
        "workspace_context": workspace_context_payload(root),
    }


def _forecast_outcome_refs(
    root: Path,
    decision: dict[str, Any],
    raw_ids: Any,
    process_locked_at: str,
) -> list[dict[str, Any]]:
    requested = [str(item) for item in raw_ids] if isinstance(raw_ids, list) else []
    if not requested:
        return []
    decision_refs = {str(ref.get("forecast_id") or ""): ref for ref in decision.get("forecast_refs") or []}
    refs = []
    for forecast_id in requested:
        bound = decision_refs.get(forecast_id)
        if not bound:
            raise ValueError(f"forecast is not bound to the decision snapshot: {forecast_id}")
        history = get_forecast(root, {"forecast_id": forecast_id, "include_history": True})["history"]
        if not any(event.get("event_hash") == bound.get("event_hash") for event in history):
            raise ValueError(f"decision-time forecast event is missing or changed: {forecast_id}")
        current = history[-1]
        if current.get("event_type") not in {"resolved", "dispute_resolved", "scored"}:
            raise ValueError(f"forecast outcome is not resolved: {forecast_id}")
        if current.get("dispute_state") != "undisputed":
            raise ValueError(f"forecast outcome is disputed or under review: {forecast_id}")
        if not current.get("event_hash") or not is_forecast_event_anchored(root, current):
            raise ValueError(f"forecast outcome is not chain-anchored: {forecast_id}")
        resolution_event = next((event for event in history if event.get("event_type") == "resolved"), None)
        if not resolution_event:
            raise ValueError(f"forecast outcome resolution event is missing: {forecast_id}")
        resolution_recorded_at = _iso(
            resolution_event.get("recorded_at"),
            f"forecast {forecast_id} resolution recorded_at",
        )
        if resolution_recorded_at <= _iso(process_locked_at, "process review locked_at"):
            raise ValueError("outcome was stored before the outcome-blind process review was locked")
        outcome_recorded_at = _iso(current.get("recorded_at"), f"forecast {forecast_id} recorded_at")
        refs.append({
            "forecast_id": forecast_id,
            "decision_event_id": bound["event_id"],
            "decision_event_hash": bound["event_hash"],
            "event_id": current["event_id"],
            "event_hash": current["event_hash"],
            "event_type": current["event_type"],
            "resolution_event_id": resolution_event["event_id"],
            "resolution_event_hash": resolution_event["event_hash"],
            "resolution_recorded_at": resolution_recorded_at,
            "outcome": current.get("outcome"),
            "scores": current.get("scores") or {},
            "observed_at": current.get("observed_at") or "",
            "resolved_at": current.get("resolved_at") or "",
            "recorded_at": outcome_recorded_at,
            "dispute_state": "undisputed",
            "evidence_lane": current.get("evidence_lane") or decision.get("evidence_lane") or "",
        })
    return refs


def _require_outcome_blind_process_review(root: Path, decision: dict[str, Any]) -> None:
    for ref in decision.get("forecast_refs") or []:
        forecast_id = str(ref.get("forecast_id") or "")
        history = get_forecast(root, {"forecast_id": forecast_id, "include_history": True})["history"]
        if any(event.get("event_type") in {"resolved", "dispute_resolved", "scored"} for event in history):
            raise ValueError(f"outcome is already recorded; process review cannot remain outcome-blind: {forecast_id}")


def _lesson_candidates(value: Any, decision: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise ValueError("lesson_candidates must be a non-empty list")
    lessons = []
    for index, raw in enumerate(value, start=1):
        if not isinstance(raw, dict):
            raise ValueError(f"lesson_candidates[{index}] must be an object")
        base = {
            "statement": _item_text(raw, "statement", index),
            "reason": _item_text(raw, "reason", index),
            "scope": _item_text(raw, "scope", index),
            "counterevidence": _item_list(raw, "counterevidence", index),
            "invalidation_conditions": _item_list(raw, "invalidation_conditions", index),
            "evidence_lane": decision["evidence_lane"],
            "decision_id": decision["decision_id"],
        }
        lessons.append({"lesson_id": sanitize_id(raw.get("lesson_id") or f"lesson-{stable_hash(base)[:16]}"), **base})
    return lessons


def _record_lesson_candidates(root: Path, report: dict[str, Any], report_path: Path) -> list[dict[str, Any]]:
    ledger = root / IMPROVE_LEDGER_PATH
    ledger.parent.mkdir(parents=True, exist_ok=True)
    with exclusive_file_lock(ledger):
        histories, heads = _read_lesson_chains(root, ledger)
        recorded: list[dict[str, Any]] = []
        pending: list[dict[str, Any]] = []
        for lesson in report["lesson_candidates"]:
            lesson_id = lesson["lesson_id"]
            existing = histories.get(lesson_id) or []
            if existing:
                if existing[0].get("source_hash") != report.get("report_hash"):
                    raise ValueError(f"lesson_id is already bound to another postmortem: {lesson_id}")
                recorded.append(existing[0])
                continue
            base = {"lesson_id": lesson_id, "postmortem_id": report["id"], "report_hash": report["report_hash"]}
            episode_id = str(
                (report.get("decision_snapshot_ref") or {}).get("decision_id")
                or (report.get("judgment_snapshot_ref") or {}).get("judgment_id")
                or ""
            )
            event = {
                "improvement_id": "improve-" + stable_hash(base)[:16],
                "lesson_id": lesson_id,
                "event_type": "lesson_candidate_recorded",
                "lesson_state": "candidate",
                "status": "captured",
                "review_state": "needs_investment_review",
                "reuse_state": "candidate",
                "authority_boundary": "no_policy_skill_or_execution_change",
                "created_at": report["created_at"],
                "recorded_at": _system_now(),
                "known_at": report["known_at"],
                "created_by": report["created_by"],
                "workflow_run_id": "",
                "source_type": "postmortem",
                "source_path": report_path.relative_to(root).as_posix(),
                "source_hash": report["report_hash"],
                "source_role": "head-manager",
                "improvement_type": "decision_readiness",
                "improvement": lesson["statement"],
                "reason": lesson["reason"],
                "materiality": "medium",
                "suggested_role": "judgment-reviewer",
                "applies_to": [lesson["scope"]],
                "evidence_refs": [],
                "origin_episode_ids": [episode_id],
                "used_episode_ids": [episode_id],
                "blocked_actions": ["policy_change", "skill_change", "order_execution"],
                "counterevidence": lesson["counterevidence"],
                "invalidation_conditions": lesson["invalidation_conditions"],
                "evidence_lane": lesson["evidence_lane"],
            }
            event = _seal_lesson_event(event, None)
            pending.append(event)
            recorded.append(event)
        if pending:
            _append_records_unlocked(ledger, pending)
            _update_lesson_heads(root, heads, pending)
        return recorded


def _seal_lesson_event(event: dict[str, Any], prior: dict[str, Any] | None) -> dict[str, Any]:
    sealed = dict(event)
    sealed.pop("lesson_event_hash", None)
    sealed["lesson_schema_version"] = 1
    sealed["lesson_sequence"] = int(prior.get("lesson_sequence") or 0) + 1 if prior else 1
    sealed["prior_lesson_event_hash"] = str(prior.get("lesson_event_hash") or "") if prior else ""
    sealed["lesson_event_hash"] = stable_hash(sealed)
    return sealed


def _append_records_unlocked(path: Path, records: list[dict[str, Any]]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _read_lesson_chains(root: Path, ledger: Path) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    heads = _read_lesson_heads(root)
    histories: dict[str, list[dict[str, Any]]] = {}
    if ledger.exists():
        for line_number, line in enumerate(ledger.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"improvement ledger line {line_number} is invalid JSON") from exc
            if not isinstance(record, dict) or not record.get("lesson_id"):
                continue
            lesson_id = str(record["lesson_id"])
            if not record.get("lesson_event_hash"):
                raise ValueError(f"lesson ledger line {line_number} is missing chain metadata")
            payload = {key: value for key, value in record.items() if key != "lesson_event_hash"}
            if stable_hash(payload) != record.get("lesson_event_hash"):
                raise ValueError(f"lesson ledger line {line_number} event hash mismatch")
            history = histories.setdefault(lesson_id, [])
            prior = history[-1] if history else None
            expected_sequence = int(prior.get("lesson_sequence") or 0) + 1 if prior else 1
            expected_prior_hash = str(prior.get("lesson_event_hash") or "") if prior else ""
            if int(record.get("lesson_sequence") or 0) != expected_sequence or str(record.get("prior_lesson_event_hash") or "") != expected_prior_hash:
                raise ValueError(f"lesson ledger line {line_number} chain mismatch")
            history.append(record)
    head_entries = heads.get("lessons") or {}
    for lesson_id in histories:
        if lesson_id not in head_entries:
            raise ValueError(f"sealed lesson chain has no anchored head: {lesson_id}")
    for lesson_id, head in head_entries.items():
        history = histories.get(lesson_id) or []
        current = history[-1] if history else {}
        if (
            current.get("lesson_event_hash") != head.get("lesson_event_hash")
            or current.get("improvement_id") != head.get("improvement_id")
            or int(current.get("lesson_sequence") or 0) != int(head.get("lesson_sequence") or 0)
        ):
            raise ValueError(f"lesson chain head mismatch: {lesson_id}")
    return histories, heads


def verified_lesson_records(workspace_root: Path | str) -> list[dict[str, Any]]:
    root = Path(workspace_root).expanduser().resolve()
    ledger = root / IMPROVE_LEDGER_PATH
    if not ledger.exists():
        return []
    with exclusive_file_lock(ledger):
        histories, _heads = _read_lesson_chains(root, ledger)
    return [record for history in histories.values() for record in history]


def _read_lesson_heads(root: Path) -> dict[str, Any]:
    path = root / LESSON_HEADS_PATH
    if not path.exists():
        return {"schema_version": 1, "lessons": {}}
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("lesson chain heads are invalid") from exc
    expected = str(document.get("heads_hash") or "")
    payload = {key: value for key, value in document.items() if key != "heads_hash"}
    if not expected or stable_hash(payload) != expected or not isinstance(document.get("lessons"), dict):
        raise ValueError("lesson chain heads hash mismatch")
    return document


def _update_lesson_heads(root: Path, heads: dict[str, Any], records: list[dict[str, Any]]) -> None:
    lessons = dict(heads.get("lessons") or {})
    for record in records:
        lessons[str(record["lesson_id"])] = {
            "improvement_id": record["improvement_id"],
            "lesson_event_hash": record["lesson_event_hash"],
            "lesson_sequence": record["lesson_sequence"],
        }
    document = {"schema_version": 1, "lessons": lessons, "updated_at": _system_now()}
    document["heads_hash"] = stable_hash(document)
    atomic_write_text(root / LESSON_HEADS_PATH, json.dumps(document, indent=2, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n")


def _verified_evidence_refs(root: Path, value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise ValueError("evidence_refs must be a non-empty list")
    refs = []
    seen = set()
    for index, raw in enumerate(value, start=1):
        if not isinstance(raw, dict):
            raise ValueError(f"evidence_refs[{index}] must be an object")
        if raw.get("forecast_id"):
            forecast_id = str(raw["forecast_id"])
            decision_id = str(raw.get("decision_snapshot_id") or "")
            if not decision_id:
                raise ValueError("forecast evidence requires decision_snapshot_id")
            decision = verify_decision_snapshot(root, decision_id)["decision_snapshot"]
            history = get_forecast(root, {"forecast_id": forecast_id, "include_history": True})["history"]
            event = next((item for item in history if item.get("event_id") == raw.get("event_id")), None)
            if (
                not event
                or event.get("event_hash") != raw.get("event_hash")
                or event.get("event_type") != "scored"
                or event.get("dispute_state") != "undisputed"
                or not is_forecast_event_anchored(root, event)
            ):
                raise ValueError(f"forecast evidence ref is missing or changed: {forecast_id}")
            bound = next((item for item in decision.get("forecast_refs") or [] if item.get("forecast_id") == forecast_id), None)
            if not bound or not any(item.get("event_hash") == bound.get("event_hash") for item in history):
                raise ValueError("forecast evidence is not rooted in the decision snapshot")
            validation_eligible = _forecast_validation_eligible(event, history)
            normalized = {
                "forecast_id": forecast_id,
                "event_id": event["event_id"],
                "event_hash": event["event_hash"],
                "event_type": "scored",
                "evidence_lane": event.get("evidence_lane") or "",
                "episode_id": decision_id,
                "decision_snapshot_hash": decision["snapshot_hash"],
                "known_at": _iso(event.get("recorded_at"), "forecast evidence recorded_at"),
                "validation_eligible": validation_eligible,
            }
        else:
            rel = str(raw.get("path") or "")
            path = safe_workspace_path(root, rel, allowed_roots=(POSTMORTEM_ROOT,))
            digest = file_hash(path)
            if not digest or digest != str(raw.get("sha256") or ""):
                raise ValueError(f"evidence ref is missing or changed: {rel}")
            report = _read_postmortem(path)
            if report.get("schema_version") != POSTMORTEM_SCHEMA_VERSION:
                raise ValueError("unsupported postmortem schema")
            _verify_postmortem_refs(root, report)
            decision_ref = report.get("decision_snapshot_ref") if isinstance(report.get("decision_snapshot_ref"), dict) else {}
            decision_id = str(decision_ref.get("decision_id") or "")
            decision = verify_decision_snapshot(root, decision_id)["decision_snapshot"]
            if decision.get("snapshot_hash") != decision_ref.get("snapshot_hash"):
                raise ValueError("postmortem evidence decision snapshot mismatch")
            normalized = {
                "path": path.relative_to(root).as_posix(),
                "sha256": digest,
                "evidence_lane": str(report.get("evidence_lane") or ""),
                "artifact_type": "postmortem_report",
                "report_hash": report["report_hash"],
                "episode_id": decision_id,
                "decision_snapshot_hash": decision["snapshot_hash"],
                "known_at": _iso(report.get("recorded_at"), "postmortem evidence recorded_at"),
                "validation_eligible": False,
            }
        key = stable_hash(normalized)
        if key not in seen:
            seen.add(key)
            refs.append(normalized)
    return refs


def _supports_validation(ref: dict[str, Any]) -> bool:
    return bool(ref.get("forecast_id") and ref.get("event_type") == "scored" and ref.get("validation_eligible"))


def _forecast_validation_eligible(event: dict[str, Any], history: list[dict[str, Any]]) -> bool:
    lane = str(event.get("evidence_lane") or "")
    if lane == "live_forward":
        issued = next((item for item in history if item.get("event_type") == "issued"), None)
        return bool(issued and _iso(issued.get("recorded_at"), "live forecast recorded_at") < _iso(issued.get("horizon"), "live forecast horizon"))
    if lane == "historical_holdout":
        spec_ref = event.get("research_spec_ref") if isinstance(event.get("research_spec_ref"), dict) else {}
        parent_ref = spec_ref.get("parent_spec_ref") if isinstance(spec_ref.get("parent_spec_ref"), dict) else {}
        return bool(parent_ref.get("analysis_plan_hash") and parent_ref.get("holdout_id") and spec_ref.get("system_recorded_at"))
    return False


def _read_postmortem(path: Path) -> dict[str, Any]:
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid postmortem: {path.stem}") from exc
    if not isinstance(report, dict):
        raise ValueError(f"postmortem must be an object: {path.stem}")
    if report.get("schema_version") not in {POSTMORTEM_SCHEMA_VERSION, 2}:
        raise ValueError(f"unsupported postmortem schema: {path.stem}")
    expected = str(report.get("report_hash") or "")
    payload = {key: value for key, value in report.items() if key != "report_hash"}
    if not expected or stable_hash(payload) != expected:
        raise ValueError(f"postmortem hash mismatch: {path.stem}")
    return report


def _read_process_review(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ValueError(f"postmortem process review not found: {path.stem}")
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid postmortem process review: {path.stem}") from exc
    if not isinstance(document, dict):
        raise ValueError("postmortem process review must be an object")
    if document.get("schema_version") != 1:
        raise ValueError("unsupported postmortem process review schema")
    expected = str(document.get("process_review_hash") or "")
    payload = {key: value for key, value in document.items() if key != "process_review_hash"}
    if not expected or stable_hash(payload) != expected or document.get("outcome_blind") is not True:
        raise ValueError(f"postmortem process review hash mismatch: {path.stem}")
    return document


def _verify_postmortem_refs(root: Path, report: dict[str, Any]) -> None:
    decision_ref = report.get("decision_snapshot_ref") if isinstance(report.get("decision_snapshot_ref"), dict) else {}
    decision_id = str(decision_ref.get("decision_id") or "")
    decision_result = verify_decision_snapshot(root, decision_id)
    decision = decision_result["decision_snapshot"]
    decision_path = safe_workspace_path(root, str(decision_ref.get("path") or ""), allowed_roots=(Path("trading/decisions"),))
    if (
        file_hash(decision_path) != decision_ref.get("sha256")
        or decision.get("snapshot_hash") != decision_ref.get("snapshot_hash")
    ):
        raise ValueError("postmortem decision snapshot ref mismatch")
    process_ref = report.get("process_review_ref") if isinstance(report.get("process_review_ref"), dict) else {}
    process_path = safe_workspace_path(root, str(process_ref.get("path") or ""), allowed_roots=(POSTMORTEM_ROOT,))
    process_review = _read_process_review(process_path)
    if (
        file_hash(process_path) != process_ref.get("sha256")
        or process_review.get("process_review_hash") != process_ref.get("process_review_hash")
        or (process_review.get("decision_snapshot_ref") or {}).get("snapshot_hash") != decision.get("snapshot_hash")
    ):
        raise ValueError("postmortem process review ref mismatch")
    locked_at = _iso(process_review.get("locked_at"), "process review locked_at")
    for ref in report.get("forecast_outcome_refs") or []:
        history = get_forecast(root, {"forecast_id": str(ref.get("forecast_id") or ""), "include_history": True})["history"]
        event = next((item for item in history if item.get("event_id") == ref.get("event_id")), None)
        resolution_event = next((item for item in history if item.get("event_type") == "resolved"), None)
        if (
            not event
            or event.get("event_hash") != ref.get("event_hash")
            or event.get("dispute_state") != "undisputed"
            or not is_forecast_event_anchored(root, event)
            or not resolution_event
            or resolution_event.get("event_id") != ref.get("resolution_event_id")
            or resolution_event.get("event_hash") != ref.get("resolution_event_hash")
            or _iso(resolution_event.get("recorded_at"), "forecast resolution recorded_at") != ref.get("resolution_recorded_at")
            or _iso(resolution_event.get("recorded_at"), "forecast resolution recorded_at") <= locked_at
        ):
            raise ValueError("postmortem forecast outcome ref mismatch")


def _store_immutable_document(path: Path, document: dict[str, Any], hash_field: str, label: str) -> tuple[dict[str, Any], str]:
    with exclusive_file_lock(path):
        if path.exists():
            existing = json.loads(path.read_text(encoding="utf-8"))
            if existing.get(hash_field) != document.get(hash_field):
                raise ValueError(f"{label} is immutable and already exists")
            return existing, "existing"
        atomic_write_text(path, json.dumps(document, indent=2, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n")
    return document, "recorded"


def _judgment_review(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        raise ValueError("investment_judgment_review must be an object")
    missing = [field for field in JUDGMENT_REVIEW_FIELDS if not str(value.get(field) or "").strip()]
    if missing:
        raise ValueError(f"investment_judgment_review missing: {', '.join(missing)}")
    return {field: str(value[field]).strip() for field in JUDGMENT_REVIEW_FIELDS}


def _required_text_object(value: Any, fields: tuple[str, ...], label: str) -> dict[str, str]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    missing = [field for field in fields if not str(value.get(field) or "").strip()]
    if missing:
        raise ValueError(f"{label} missing: {', '.join(missing)}")
    return {field: str(value[field]).strip() for field in fields}


def _required_text(args: dict[str, Any], field: str) -> str:
    value = str(args.get(field) or "").strip()
    if not value:
        raise ValueError(f"{field} is required")
    return value


def _required_list(args: dict[str, Any], field: str) -> list[Any]:
    value = args.get(field)
    if not isinstance(value, list) or not value:
        raise ValueError(f"{field} must be a non-empty list")
    return value


def _required_object_list(args: dict[str, Any], field: str) -> list[dict[str, Any]]:
    value = _required_list(args, field)
    if any(not isinstance(item, dict) for item in value):
        raise ValueError(f"{field} entries must be objects")
    return value


def _item_text(value: dict[str, Any], field: str, index: int) -> str:
    text = str(value.get(field) or "").strip()
    if not text:
        raise ValueError(f"lesson_candidates[{index}].{field} is required")
    return text


def _item_list(value: dict[str, Any], field: str, index: int) -> list[Any]:
    items = value.get(field)
    if not isinstance(items, list) or not items:
        raise ValueError(f"lesson_candidates[{index}].{field} must be a non-empty list")
    return items


def _iso(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field} is required")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO-8601 datetime") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _system_now() -> str:
    return _iso(now_iso(), "system recorded_at")

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tradingcodex_service.application.common import atomic_write_text, exclusive_file_lock, now_iso, safe_workspace_path, sanitize_id, stable_hash
from tradingcodex_service.application.judgments import get_judgment_snapshot, list_decision_adoptions
from tradingcodex_service.application.forecasting import get_forecast

POSTMORTEM_ROOT = Path("trading/reports/postmortem")
PROCESS_FIELDS = (
    "original_thesis", "evidence_quality", "base_rate_quality",
    "alternatives_considered", "assumptions", "confidence_process",
    "invalidation_discipline", "handoff_process", "process_conclusion",
)


def record_judgment_process_review(workspace_root: Path | str, args: dict[str, Any]) -> dict[str, Any]:
    root = Path(workspace_root).expanduser().resolve()
    judgment_id = _required(args, "judgment_id")
    result = get_judgment_snapshot(root, judgment_id)
    judgment = result["judgment_snapshot"]
    review = _review(args.get("process_review"))
    _head_manager(args)
    locked_at = now_iso()
    if _timestamp(str(judgment.get("recorded_at") or ""), "JudgmentSnapshot recorded_at") > _timestamp(locked_at, "process review locked_at"):
        raise ValueError("postmortem process review cannot predate its JudgmentSnapshot")
    _require_outcome_blind_process_review(root, judgment)
    seed = {"judgment_hash": judgment["snapshot_hash"], "process_review": review}
    record_id = sanitize_id(args.get("id") or f"process-review-{stable_hash(seed)[:16]}")
    document = {
        "schema_version": 2, "artifact_type": "postmortem_process_review",
        "id": record_id, "created_by": "head-manager", "locked_at": locked_at,
        "recorded_at": locked_at, "outcome_blind": True,
        "judgment_snapshot_ref": {"judgment_id": judgment_id, "snapshot_hash": judgment["snapshot_hash"], "path": result["path"]},
        "process_review": review, "authority": "evidence_only",
        "blocked_actions": ["policy_change", "skill_change", "order_approval", "order_execution"],
    }
    document["process_review_hash"] = stable_hash(document)
    path = safe_workspace_path(root, POSTMORTEM_ROOT / f"{record_id}.process-review.json", allowed_roots=(POSTMORTEM_ROOT,))
    return _store(root, path, document, "process_review", "process_review_hash")


def create_judgment_postmortem(workspace_root: Path | str, args: dict[str, Any]) -> dict[str, Any]:
    root = Path(workspace_root).expanduser().resolve()
    judgment_id = _required(args, "judgment_id")
    result = get_judgment_snapshot(root, judgment_id)
    judgment = result["judgment_snapshot"]
    process_id = _required(args, "process_review_id")
    process_path = safe_workspace_path(root, POSTMORTEM_ROOT / f"{sanitize_id(process_id)}.process-review.json", allowed_roots=(POSTMORTEM_ROOT,))
    process = _json(process_path)
    _verify_process_review(root, process, judgment)
    adoption_ref: dict[str, Any] = {}
    adoption_id = str(args.get("adoption_id") or "").strip()
    if bool(args.get("evaluate_user_decision")) and not adoption_id:
        raise ValueError("evaluating the user's actual decision requires adoption_id")
    if adoption_id:
        adoption = next((item for item in list_decision_adoptions(root) if item.get("adoption_id") == adoption_id), None)
        if not adoption or (adoption.get("judgment_ref") or {}).get("judgment_id") != judgment_id:
            raise ValueError("decision adoption does not match the JudgmentSnapshot")
        adoption_ref = {"adoption_id": adoption_id, "adoption_hash": adoption["adoption_hash"], "path": adoption["path"]}
    _head_manager(args)
    forecast_outcome_refs = []
    for forecast_ref in judgment.get("forecast_refs", []):
        forecast_id = str(forecast_ref.get("forecast_id") or "")
        forecast_result = get_forecast(root, {"forecast_id": forecast_id, "include_history": True})
        history = forecast_result.get("history") or []
        if not any(item.get("event_hash") == forecast_ref.get("event_hash") for item in history):
            raise ValueError(f"JudgmentSnapshot forecast binding is no longer verifiable: {forecast_id}")
        outcome = forecast_result["forecast"]
        if outcome.get("status") != "closed" or outcome.get("event_type") not in {"resolved", "dispute_resolved", "scored"}:
            raise ValueError(f"forecast must be resolved before postmortem: {forecast_id}")
        reveal_events = [
            item
            for item in history
            if item.get("event_type") in {"resolved", "dispute_resolved", "scored"}
        ]
        if not reveal_events:
            raise ValueError(f"forecast outcome resolution event is missing: {forecast_id}")
        first_reveal = min(
            reveal_events,
            key=lambda item: _timestamp(
                str(item.get("recorded_at") or ""),
                f"forecast {forecast_id} outcome recorded_at",
            ),
        )
        outcome_revealed_at = _timestamp(
            str(first_reveal.get("recorded_at") or ""),
            f"forecast {forecast_id} outcome recorded_at",
        )
        if outcome_revealed_at <= _timestamp(str(process.get("locked_at") or ""), "process review locked_at"):
            raise ValueError("outcome was stored before the outcome-blind process review was locked")
        forecast_outcome_refs.append({
            "forecast_id": forecast_id,
            "event_type": outcome["event_type"],
            "event_hash": outcome["event_hash"],
            "outcome_revealed_at": outcome_revealed_at,
        })
    recorded_at = now_iso()
    seed = {"judgment_hash": judgment["snapshot_hash"], "process_hash": process["process_review_hash"], "trigger": _required(args, "trigger")}
    report_id = sanitize_id(args.get("id") or f"postmortem-{stable_hash(seed)[:16]}")
    report = {
        "schema_version": 2, "artifact_type": "postmortem_report", "id": report_id,
        "created_by": "head-manager", "created_at": recorded_at, "recorded_at": recorded_at,
        "known_at": recorded_at, "trigger": seed["trigger"], "evidence_lane": "live_forward",
        "judgment_snapshot_ref": {"judgment_id": judgment_id, "snapshot_hash": judgment["snapshot_hash"], "path": result["path"]},
        "process_review_ref": {"id": process_id, "process_review_hash": process["process_review_hash"], "path": process_path.relative_to(root).as_posix()},
        "adoption_ref": adoption_ref,
        "forecast_outcome_refs": forecast_outcome_refs,
        "findings": _objects(args.get("findings"), "findings"),
        "investment_judgment_review": _object(args.get("investment_judgment_review"), "investment_judgment_review"),
        "next_actions": _strings(args.get("next_actions"), "next_actions"),
        "lesson_candidates": _lessons(args.get("lesson_candidates"), judgment_id),
        "authority": "evidence_only",
        "blocked_actions": ["policy_change", "skill_change", "order_approval", "order_execution"],
    }
    report["report_hash"] = stable_hash(report)
    path = safe_workspace_path(root, POSTMORTEM_ROOT / f"{report_id}.postmortem_report.json", allowed_roots=(POSTMORTEM_ROOT,))
    stored = _store(root, path, report, "postmortem", "report_hash")
    from tradingcodex_service.application.postmortems import _record_lesson_candidates
    stored["lesson_records"] = _record_lesson_candidates(root, stored["postmortem"], path)
    return stored


def verify_judgment_postmortem(workspace_root: Path | str, report: dict[str, Any]) -> None:
    root = Path(workspace_root).expanduser().resolve()
    if report.get("report_hash") != stable_hash({key: value for key, value in report.items() if key != "report_hash"}):
        raise ValueError("postmortem report integrity check failed")
    ref = report.get("judgment_snapshot_ref") or {}
    judgment = get_judgment_snapshot(root, str(ref.get("judgment_id") or ""))["judgment_snapshot"]
    if judgment.get("snapshot_hash") != ref.get("snapshot_hash"):
        raise ValueError("postmortem JudgmentSnapshot binding mismatch")
    process_ref = report.get("process_review_ref") or {}
    process_path = safe_workspace_path(
        root,
        str(process_ref.get("path") or ""),
        allowed_roots=(POSTMORTEM_ROOT,),
    )
    process = _json(process_path)
    try:
        _verify_process_review(root, process, judgment)
    except ValueError as exc:
        raise ValueError("postmortem process review binding mismatch") from exc
    if process.get("process_review_hash") != process_ref.get("process_review_hash"):
        raise ValueError("postmortem process review binding mismatch")
    adoption_ref = report.get("adoption_ref") or {}
    if adoption_ref:
        adoption = next(
            (
                item
                for item in list_decision_adoptions(root)
                if item.get("adoption_id") == adoption_ref.get("adoption_id")
            ),
            None,
        )
        if (
            not adoption
            or adoption.get("adoption_hash") != adoption_ref.get("adoption_hash")
            or (adoption.get("judgment_ref") or {}).get("judgment_id") != judgment.get("judgment_id")
        ):
            raise ValueError("postmortem User Adoption binding mismatch")
    outcome_refs = report.get("forecast_outcome_refs") or []
    if judgment.get("forecast_refs") and not outcome_refs:
        raise ValueError("postmortem requires resolved Forecast outcomes")
    for outcome_ref in outcome_refs:
        history = get_forecast(
            root,
            {"forecast_id": str(outcome_ref.get("forecast_id") or ""), "include_history": True},
        )["history"]
        event = next(
            (item for item in history if item.get("event_hash") == outcome_ref.get("event_hash")),
            None,
        )
        if (
            not event
            or event.get("event_type") != outcome_ref.get("event_type")
            or event.get("status") != "closed"
        ):
            raise ValueError("postmortem Forecast outcome binding mismatch")
        if str(outcome_ref.get("forecast_id") or "") not in {
            str(item.get("forecast_id") or "")
            for item in judgment.get("forecast_refs") or []
        }:
            raise ValueError("postmortem Forecast is not bound to the JudgmentSnapshot")
        reveal_events = [
            item
            for item in history
            if item.get("event_type") in {"resolved", "dispute_resolved", "scored"}
        ]
        if not reveal_events:
            raise ValueError("postmortem Forecast outcome resolution event is missing")
        first_reveal = min(
            reveal_events,
            key=lambda item: _timestamp(
                str(item.get("recorded_at") or ""),
                "forecast outcome recorded_at",
            ),
        )
        reveal_at = _timestamp(
            str(first_reveal.get("recorded_at") or ""),
            "forecast outcome recorded_at",
        )
        if (
            reveal_at != str(outcome_ref.get("outcome_revealed_at") or "")
            or reveal_at <= _timestamp(str(process.get("locked_at") or ""), "process review locked_at")
        ):
            raise ValueError("postmortem outcome-blind ordering check failed")


def list_judgment_process_reviews(
    workspace_root: Path | str,
    limit: int = 50,
) -> dict[str, Any]:
    root = Path(workspace_root).expanduser().resolve()
    items: list[dict[str, Any]] = []
    for path in sorted((root / POSTMORTEM_ROOT).glob("*.process-review.json")):
        process = _json(path)
        judgment_id = str(
            (process.get("judgment_snapshot_ref") or {}).get("judgment_id") or ""
        )
        if not judgment_id:
            continue
        judgment = get_judgment_snapshot(root, judgment_id)["judgment_snapshot"]
        _verify_process_review(root, process, judgment)
        items.append(
            {
                "id": process["id"],
                "judgment_id": judgment_id,
                "locked_at": process["locked_at"],
                "outcome_blind": True,
                "process_review_hash": process["process_review_hash"],
                "path": path.relative_to(root).as_posix(),
            }
        )
    items.sort(key=lambda item: str(item.get("locked_at") or ""), reverse=True)
    bounded = items[: max(1, min(int(limit), 200))]
    return {"items": bounded, "count": len(items)}


def _verify_process_review(
    root: Path,
    process: dict[str, Any],
    judgment: dict[str, Any],
) -> None:
    if process.get("outcome_blind") is not True or process.get("process_review_hash") != stable_hash(
        {key: value for key, value in process.items() if key != "process_review_hash"}
    ):
        raise ValueError("postmortem process review integrity check failed")
    process_ref = process.get("judgment_snapshot_ref") or {}
    if (
        process_ref.get("judgment_id") != judgment.get("judgment_id")
        or process_ref.get("snapshot_hash") != judgment.get("snapshot_hash")
    ):
        raise ValueError("postmortem process review does not match the JudgmentSnapshot")


def _require_outcome_blind_process_review(
    root: Path,
    judgment: dict[str, Any],
) -> None:
    for forecast_ref in judgment.get("forecast_refs") or []:
        forecast_id = str(forecast_ref.get("forecast_id") or "")
        history = get_forecast(
            root,
            {"forecast_id": forecast_id, "include_history": True},
        ).get("history") or []
        if any(
            item.get("event_type") in {"resolved", "dispute_resolved", "scored"}
            for item in history
        ):
            raise ValueError(
                f"outcome is already recorded; process review cannot remain outcome-blind: {forecast_id}"
            )


def _timestamp(value: str, field: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be RFC3339") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _store(root: Path, path: Path, document: dict[str, Any], key: str, hash_field: str) -> dict[str, Any]:
    with exclusive_file_lock(path):
        if path.exists():
            stored = _json(path)
            if stored.get(hash_field) != document.get(hash_field):
                volatile = {hash_field, "locked_at", "recorded_at", "created_at", "known_at"}
                if {field: value for field, value in stored.items() if field not in volatile} != {field: value for field, value in document.items() if field not in volatile}:
                    raise ValueError(f"{key} is immutable and already exists: {document['id']}")
            status = "existing"
        else:
            atomic_write_text(path, json.dumps(document, indent=2, ensure_ascii=False, sort_keys=True) + "\n")
            stored, status = document, "recorded"
    return {"status": status, key: stored, "export_path": path.relative_to(root).as_posix(), "authority": "evidence_only"}


def _json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ValueError(f"record not found: {path.name}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"invalid record: {path.name}")
    return value


def _head_manager(args: dict[str, Any]) -> None:
    if _required(args, "created_by") != "head-manager":
        raise PermissionError("postmortem records must be recorded by head-manager")


def _review(value: Any) -> dict[str, str]:
    raw = _object(value, "process_review")
    result = {field: str(raw.get(field) or "").strip() for field in PROCESS_FIELDS}
    missing = [field for field, item in result.items() if not item]
    if missing:
        raise ValueError("process_review requires: " + ", ".join(missing))
    return result


def _required(args: dict[str, Any], field: str) -> str:
    value = str(args.get(field) or "").strip()
    if not value:
        raise ValueError(f"{field} is required")
    return value


def _object(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be an object")
    return dict(value)


def _objects(value: Any, field: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value or any(not isinstance(item, dict) for item in value):
        raise ValueError(f"{field} must be a non-empty array of objects")
    return [dict(item) for item in value]


def _strings(value: Any, field: str) -> list[str]:
    if not isinstance(value, list) or not value or any(not isinstance(item, str) or not item.strip() for item in value):
        raise ValueError(f"{field} must be a non-empty array of strings")
    return [item.strip() for item in value]


def _lessons(value: Any, judgment_id: str) -> list[dict[str, Any]]:
    records = _objects(value, "lesson_candidates")
    lessons = []
    for index, item in enumerate(records):
        statement = str(item.get("statement") or "").strip()
        reason = str(item.get("reason") or "").strip()
        scope = str(item.get("scope") or "").strip()
        if not statement or not reason or not scope:
            raise ValueError(f"lesson_candidates[{index}] requires statement, reason, and scope")
        base = {
            "statement": statement,
            "reason": reason,
            "scope": scope,
            "counterevidence": [str(value) for value in item.get("counterevidence", [])],
            "invalidation_conditions": [str(value) for value in item.get("invalidation_conditions", [])],
            "evidence_lane": "live_forward",
            "decision_id": judgment_id,
        }
        lessons.append({"lesson_id": sanitize_id(item.get("lesson_id") or f"lesson-{stable_hash(base)[:16]}"), **base})
    return lessons

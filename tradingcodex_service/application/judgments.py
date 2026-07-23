from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from tradingcodex_service.application.artifact_bindings import (
    verify_authenticated_artifact_binding,
)
from tradingcodex_service.application.artifact_v2 import project_artifact
from tradingcodex_service.application.common import (
    atomic_write_text,
    exclusive_file_lock,
    now_iso,
    safe_workspace_path,
    sanitize_id,
    stable_hash,
)
from tradingcodex_service.application.forecasting import get_forecast
from tradingcodex_service.application.research import (
    find_workspace_research_artifact_read_only,
)


DECISION_ROOT = Path("trading/decisions")
JUDGMENT_SNAPSHOT_SCHEMA_VERSION = 2
DECISION_ADOPTION_SCHEMA_VERSION = 1
_USER_TERMINAL = object()


def record_judgment_snapshot(workspace_root: Path | str, args: dict[str, Any]) -> dict[str, Any]:
    root = Path(workspace_root).expanduser().resolve()
    run_id = _required(args, "workflow_run_id")
    synthesis_id = f"synthesis-{run_id}"
    synthesis = find_workspace_research_artifact_read_only(root, synthesis_id)
    if not synthesis:
        raise ValueError(f"accepted canonical synthesis not found: {synthesis_id}")
    if synthesis.get("artifact_type") != "synthesis_report":
        raise ValueError("judgment snapshot requires a canonical synthesis_report")
    authentication = verify_authenticated_artifact_binding(root, synthesis)
    projected_synthesis = project_artifact(
        {**synthesis, "authentication": authentication}
    )["artifact"]
    projected_status = projected_synthesis.get("status") or {}
    if projected_status.get("handoff") != "accepted":
        raise ValueError("judgment snapshot requires an accepted synthesis")
    if projected_status.get("evidence_readiness") in {"factual", "screen"}:
        raise ValueError("factual and screening synthesis must not create a judgment snapshot")
    run_lineage = (
        authentication.get("run_lineage")
        if isinstance(authentication.get("run_lineage"), dict)
        else {}
    )
    created_by = _required(args, "created_by")
    if created_by != "head-manager":
        raise PermissionError("judgment snapshots must be recorded by head-manager")
    forecast_ids = _strings(args.get("forecast_ids"), "forecast_ids")
    forecast_block_reason = str(args.get("forecast_block_reason") or "").strip()
    synthesis_forecast = (
        projected_synthesis.get("forecast")
        if isinstance(projected_synthesis.get("forecast"), dict)
        else {}
    )
    forecast_posture = str(synthesis_forecast.get("posture") or "")
    if forecast_posture == "blocked" and forecast_ids:
        raise ValueError("blocked synthesis Forecast posture cannot bind forecast_ids")
    if forecast_posture == "eligible" and forecast_block_reason:
        raise ValueError("eligible synthesis Forecast posture cannot use forecast_block_reason")
    forecast_refs = []
    for forecast_id in forecast_ids:
        forecast = get_forecast(
            root, {"forecast_id": forecast_id, "include_history": False}
        )["forecast"]
        if str(forecast.get("workflow_run_id") or "") != run_id:
            raise ValueError(f"forecast belongs to another workflow run: {forecast_id}")
        forecast_refs.append(
            {
                "forecast_id": forecast_id,
                "event_hash": forecast.get("event_hash", ""),
            }
        )
    if forecast_posture == "blocked":
        sealed_reason = str(synthesis_forecast.get("block_reason") or "").strip()
        if forecast_block_reason and forecast_block_reason != sealed_reason:
            raise ValueError("forecast_block_reason must match the canonical synthesis")
        forecast_block_reason = sealed_reason
    elif forecast_posture == "eligible":
        if not forecast_refs:
            raise ValueError("eligible synthesis Forecast posture requires forecast_ids")
    elif bool(forecast_refs) == bool(forecast_block_reason):
        raise ValueError("exactly one of forecast_ids or forecast_block_reason is required")
    recorded_at = now_iso()
    seed = {
        "workflow_run_id": run_id,
        "synthesis_receipt_hash": authentication["receipt_hash"],
        "synthesis_version": synthesis["version"],
    }
    judgment_id = sanitize_id(args.get("judgment_id") or f"judgment-{stable_hash(seed)[:20]}")
    snapshot = {
        "schema_version": JUDGMENT_SNAPSHOT_SCHEMA_VERSION,
        "artifact_type": "judgment_snapshot",
        "judgment_id": judgment_id,
        "workflow_run_id": run_id,
        "knowledge_cutoff": synthesis.get("knowledge_cutoff", ""),
        "recorded_at": recorded_at,
        "created_by": created_by,
        "authority": "evidence_only",
        "synthesis_ref": {
            "artifact_id": synthesis_id,
            "version": synthesis["version"],
            "content_hash": synthesis["content_hash"],
            "receipt_hash": authentication["receipt_hash"],
            "path": synthesis["path"],
        },
        "run_context": {
            "strategy_hash": run_lineage.get("strategy_hash", ""),
            "investment_brain_content_digest": run_lineage.get(
                "investment_brain_content_digest", ""
            ),
            "investor_context_hash": run_lineage.get("investor_context_hash", ""),
        },
        "forecast_refs": forecast_refs,
        "forecast_block_reason": forecast_block_reason,
        "blocked_actions": ["order_approval", "order_execution"],
    }
    snapshot["snapshot_hash"] = stable_hash(snapshot)
    path = safe_workspace_path(
        root,
        DECISION_ROOT / f"{judgment_id}.judgment-snapshot.json",
        allowed_roots=(DECISION_ROOT,),
    )
    with exclusive_file_lock(path):
        if path.exists():
            existing = _read_json(path)
            comparable = {"workflow_run_id", "synthesis_ref", "run_context", "forecast_refs", "forecast_block_reason", "authority", "blocked_actions"}
            if any(existing.get(field) != snapshot.get(field) for field in comparable):
                raise ValueError(f"judgment snapshot is immutable and already exists: {judgment_id}")
            _verify_judgment(root, existing)
            snapshot = existing
            status = "existing"
        else:
            atomic_write_text(path, json.dumps(snapshot, indent=2, ensure_ascii=False, sort_keys=True) + "\n")
            status = "recorded"
    return {"status": status, "judgment_snapshot": snapshot, "path": path.relative_to(root).as_posix()}


def get_judgment_snapshot(workspace_root: Path | str, judgment_id: str) -> dict[str, Any]:
    root = Path(workspace_root).expanduser().resolve()
    path = safe_workspace_path(
        root,
        DECISION_ROOT / f"{sanitize_id(judgment_id)}.judgment-snapshot.json",
        allowed_roots=(DECISION_ROOT,),
    )
    if not path.exists():
        from tradingcodex_service.application.decision_packages import get_decision_snapshot

        legacy = get_decision_snapshot(root, judgment_id)
        decision = legacy["decision_snapshot"]
        return {
            "judgment_snapshot": {
                **decision,
                "source_schema_version": 1,
                "artifact_type": "legacy_judgment_snapshot",
                "judgment_id": decision.get("decision_id", judgment_id),
                "adoption_status": "unknown_legacy",
                "authority": "evidence_only",
            },
            "verification_status": "verified",
            "path": legacy["export_path"],
            "compatibility_warnings": ["legacy_decision_snapshot"],
        }
    snapshot = _read_json(path)
    _verify_judgment(root, snapshot)
    return {"judgment_snapshot": snapshot, "verification_status": "verified", "path": path.relative_to(root).as_posix()}


def list_judgment_snapshots(workspace_root: Path | str, limit: int = 50) -> dict[str, Any]:
    root = Path(workspace_root).expanduser().resolve()
    items = []
    for path in sorted((root / DECISION_ROOT).glob("*.judgment-snapshot.json")):
        snapshot = _read_json(path)
        _verify_judgment(root, snapshot)
        items.append({
            "judgment_id": snapshot["judgment_id"],
            "workflow_run_id": snapshot["workflow_run_id"],
            "recorded_at": snapshot["recorded_at"],
            "snapshot_hash": snapshot["snapshot_hash"],
            "path": path.relative_to(root).as_posix(),
        })
    from tradingcodex_service.application.decision_packages import list_decision_snapshots

    for legacy in list_decision_snapshots(root, 200)["decision_snapshots"]:
        items.append({
            "judgment_id": legacy["decision_id"],
            "workflow_run_id": legacy["workflow_run_id"],
            "recorded_at": legacy["decided_at"],
            "snapshot_hash": legacy["snapshot_hash"],
            "path": legacy["path"],
            "source_schema_version": 1,
            "adoption_status": "unknown_legacy",
            "compatibility_warnings": ["legacy_decision_snapshot"],
        })
    items.sort(key=lambda item: item["recorded_at"], reverse=True)
    return {"items": items[: max(1, min(int(limit), 200))], "count": len(items)}


def terminal_adoption_args(args: dict[str, Any]) -> dict[str, Any]:
    return {**args, "_recorded_via": _USER_TERMINAL}


def record_decision_adoption(workspace_root: Path | str, args: dict[str, Any]) -> dict[str, Any]:
    if args.get("_recorded_via") is not _USER_TERMINAL:
        raise PermissionError("decision adoption may only be recorded from the user terminal")
    root = Path(workspace_root).expanduser().resolve()
    judgment_id = _required(args, "judgment_id")
    judgment = get_judgment_snapshot(root, judgment_id)["judgment_snapshot"]
    adopted_at = str(args.get("adopted_at") or now_iso())
    adopted_time = _timestamp(adopted_at, "adopted_at")
    statement = _required(args, "decision")
    seed = {"judgment_hash": judgment["snapshot_hash"], "decision": statement, "adopted_at": adopted_at}
    adoption_id = sanitize_id(args.get("adoption_id") or f"adoption-{stable_hash(seed)[:20]}")
    recorded_at = now_iso()
    if adopted_time > _timestamp(recorded_at, "recorded_at"):
        raise ValueError("adopted_at must not be after recorded_at")
    adoption = {
        "schema_version": DECISION_ADOPTION_SCHEMA_VERSION,
        "artifact_type": "decision_adoption",
        "adoption_id": adoption_id,
        "judgment_ref": {"judgment_id": judgment_id, "snapshot_hash": judgment["snapshot_hash"]},
        "decision": statement,
        "adopted_at": adopted_at,
        "recorded_at": recorded_at,
        "recorded_via": "user-terminal",
        "authority": "evidence_only",
    }
    superseded = str(args.get("supersedes_adoption_id") or "").strip()
    if superseded:
        prior = next(
            (item for item in list_decision_adoptions(root) if item.get("adoption_id") == superseded),
            None,
        )
        if not prior:
            raise ValueError(f"superseded decision adoption not found: {superseded}")
        if (prior.get("judgment_ref") or {}).get("judgment_id") != judgment_id:
            raise ValueError("superseded decision adoption belongs to another JudgmentSnapshot")
        adoption["supersedes_adoption_id"] = superseded
    adoption["adoption_hash"] = stable_hash(adoption)
    path = safe_workspace_path(root, DECISION_ROOT / f"{adoption_id}.decision-adoption.json", allowed_roots=(DECISION_ROOT,))
    with exclusive_file_lock(path):
        if path.exists():
            existing = _read_json(path)
            _verify_adoption(existing)
            comparable = {
                "artifact_type", "adoption_id", "judgment_ref", "decision",
                "adopted_at", "recorded_via", "authority", "supersedes_adoption_id",
            }
            if any(existing.get(field) != adoption.get(field) for field in comparable):
                raise ValueError(f"decision adoption is immutable and already exists: {adoption_id}")
            adoption = existing
            status = "existing"
        else:
            atomic_write_text(path, json.dumps(adoption, indent=2, ensure_ascii=False, sort_keys=True) + "\n")
            status = "recorded"
    return {"status": status, "decision_adoption": adoption, "path": path.relative_to(root).as_posix()}


def list_decision_adoptions(workspace_root: Path | str) -> list[dict[str, Any]]:
    root = Path(workspace_root).expanduser().resolve()
    items = []
    for path in sorted((root / DECISION_ROOT).glob("*.decision-adoption.json")):
        adoption = _read_json(path)
        _verify_adoption(adoption)
        items.append(adoption | {"path": path.relative_to(root).as_posix()})
    return items


def _verify_adoption(adoption: dict[str, Any]) -> None:
    claimed = adoption.get("adoption_hash")
    if claimed != stable_hash({key: value for key, value in adoption.items() if key != "adoption_hash"}):
        raise ValueError("decision adoption integrity check failed")
    if adoption.get("recorded_via") != "user-terminal" or adoption.get("authority") != "evidence_only":
        raise ValueError("decision adoption authority boundary is invalid")
    if _timestamp(str(adoption.get("adopted_at") or ""), "adopted_at") > _timestamp(
        str(adoption.get("recorded_at") or ""), "recorded_at"
    ):
        raise ValueError("decision adoption time ordering is invalid")


def _verify_judgment(root: Path, snapshot: dict[str, Any]) -> None:
    claimed = snapshot.get("snapshot_hash")
    if claimed != stable_hash({key: value for key, value in snapshot.items() if key != "snapshot_hash"}):
        raise ValueError("judgment snapshot integrity check failed")
    ref = snapshot.get("synthesis_ref") or {}
    synthesis = find_workspace_research_artifact_read_only(root, str(ref.get("artifact_id") or ""))
    if not synthesis or synthesis.get("content_hash") != ref.get("content_hash") or synthesis.get("version") != ref.get("version"):
        raise ValueError("judgment synthesis binding mismatch")
    authentication = verify_authenticated_artifact_binding(root, synthesis)
    if authentication.get("receipt_hash") != ref.get("receipt_hash"):
        raise ValueError("judgment synthesis receipt binding mismatch")
    if snapshot.get("authority") != "evidence_only":
        raise ValueError("judgment authority boundary is invalid")
    forecast_refs = snapshot.get("forecast_refs")
    if not isinstance(forecast_refs, list):
        raise ValueError("judgment forecast refs must be an array")
    if not forecast_refs and not str(snapshot.get("forecast_block_reason") or "").strip():
        raise ValueError("judgment requires forecast refs or a forecast block reason")
    for forecast_ref in forecast_refs:
        if not isinstance(forecast_ref, dict):
            raise ValueError("judgment forecast refs must contain objects")
        forecast_id = str(forecast_ref.get("forecast_id") or "")
        result = get_forecast(root, {"forecast_id": forecast_id, "include_history": True})
        history = result.get("history") or []
        if not any(item.get("event_hash") == forecast_ref.get("event_hash") for item in history):
            raise ValueError(f"judgment forecast binding mismatch: {forecast_id}")
        if str(result["forecast"].get("workflow_run_id") or "") != snapshot.get("workflow_run_id"):
            raise ValueError(f"judgment forecast belongs to another workflow run: {forecast_id}")


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"invalid decision record: {path}")
    return value


def _required(args: dict[str, Any], field: str) -> str:
    value = str(args.get(field) or "").strip()
    if not value:
        raise ValueError(f"{field} is required")
    return value


def _strings(value: Any, field: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise ValueError(f"{field} must be an array of non-empty strings")
    return [item.strip() for item in value]


def _timestamp(value: str, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be an RFC 3339 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must include a timezone")
    return parsed

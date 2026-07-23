from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tradingcodex_service.application.analysis_runs import (
    ANALYSIS_RUNS_ROOT,
    read_analysis_run,
)
from tradingcodex_service.application.artifact_bindings import (
    verify_authenticated_artifact_binding,
)
from tradingcodex_service.application.artifact_v2 import (
    authenticate_artifact_for_read,
    project_artifact,
)
from tradingcodex_service.application.forecasting import list_forecasts
from tradingcodex_service.application.judgments import (
    list_decision_adoptions,
    list_judgment_snapshots,
)
from tradingcodex_service.application.judgment_postmortems import (
    list_judgment_process_reviews,
)
from tradingcodex_service.application.postmortems import (
    IMPROVE_LEDGER_PATH,
    list_postmortems,
)
from tradingcodex_service.application.research import list_research_artifacts


def list_decision_episodes(workspace_root: Path | str, args: dict[str, Any] | None = None) -> dict[str, Any]:
    root = Path(workspace_root).expanduser().resolve()
    args = args or {}
    run_ids = _run_ids(root)
    episodes = [_build_episode(root, run_id) for run_id in run_ids]
    episodes.sort(key=lambda item: str((item.get("run") or {}).get("created_at") or ""), reverse=True)
    limit = max(1, min(int(args.get("limit") or 100), 200))
    items = [_episode_card(item) for item in episodes[:limit]]
    return {"items": items, "page": {"returned_count": len(items), "total_count": len(episodes), "has_more": len(episodes) > limit}}


def get_decision_episode(workspace_root: Path | str, workflow_run_id: str) -> dict[str, Any]:
    root = Path(workspace_root).expanduser().resolve()
    if not read_analysis_run(root, workflow_run_id):
        raise ValueError(f"analysis run not found: {workflow_run_id}")
    return {"episode": _build_episode(root, workflow_run_id)}


def _build_episode(root: Path, run_id: str) -> dict[str, Any]:
    run = read_analysis_run(root, run_id)
    artifacts = [
        authenticate_artifact_for_read(root, item)
        for item in list_research_artifacts(
            root,
            {"workflow_run_id": run_id, "limit": 200},
        )["artifacts"]
    ]
    synthesis_candidates = [item for item in artifacts if item.get("artifact_type") == "synthesis_report"]
    canonical_id = f"synthesis-{run_id}"
    canonical = next((item for item in synthesis_candidates if item.get("artifact_id") == canonical_id), None)
    warnings: list[str] = []
    synthesis = canonical
    if synthesis is None and len(synthesis_candidates) == 1:
        synthesis = synthesis_candidates[0]
    elif synthesis is None and len(synthesis_candidates) > 1:
        warnings.append("ambiguous_legacy_synthesis")
    projected_synthesis: dict[str, Any] | None = None
    if synthesis is not None:
        authentication = synthesis.get("authentication") or verify_authenticated_artifact_binding(root, synthesis)
        synthesis = {**synthesis, "authentication": authentication}
        projected_synthesis = project_artifact(synthesis)["artifact"]

    judgments = [
        item for item in list_judgment_snapshots(root, 200)["items"]
        if item.get("workflow_run_id") == run_id
    ]
    judgment_ids = {item["judgment_id"] for item in judgments}
    adoptions = [
        item for item in list_decision_adoptions(root)
        if (item.get("judgment_ref") or {}).get("judgment_id") in judgment_ids
    ]
    forecasts = list_forecasts(root, {"workflow_run_id": run_id, "limit": 1000})["forecasts"]
    forecast_state = _forecast_state(forecasts)
    legacy_decision_ids = {
        path.name.removesuffix(".decision-snapshot.json")
        for path in (root / "trading/decisions").glob("*.decision-snapshot.json")
        if _json_run_id(path) == run_id
    }
    postmortems = [
        item for item in list_postmortems(root, 200)["postmortems"]
        if item.get("decision_id") in legacy_decision_ids | judgment_ids
    ]
    process_reviews = [
        item
        for item in list_judgment_process_reviews(root, 200)["items"]
        if item.get("judgment_id") in judgment_ids
    ]
    lessons = _lesson_states(root, legacy_decision_ids | judgment_ids)
    decision_quality = (projected_synthesis or {}).get("decision_quality") or {}
    dataset_ids = sorted({
        str(dataset_id)
        for artifact in artifacts
        for dataset_id in artifact.get("dataset_ids", [])
        if dataset_id
    })
    calculation_run_ids = sorted({
        str(calculation_run_id)
        for artifact in artifacts
        for calculation_run_id in artifact.get("calculation_run_ids", [])
        if calculation_run_id
    })
    return {
        "workflow_run_id": run_id,
        "run": run,
        "analysis": {
            "state": "synthesized" if projected_synthesis else ("ambiguous" if warnings else "researching"),
            "synthesis": projected_synthesis,
            "synthesis_candidates": [
                {"id": item.get("artifact_id"), "path": item.get("path"), "version": item.get("version")}
                for item in synthesis_candidates
            ] if warnings else [],
            "artifacts": [
                {
                    "id": item.get("artifact_id"),
                    "type": item.get("artifact_type"),
                    "role": item.get("producer_role") or item.get("role"),
                    "path": item.get("path"),
                    "canonical_synthesis": bool(
                        synthesis
                        and item.get("artifact_id") == synthesis.get("artifact_id")
                    ),
                }
                for item in artifacts
            ],
            "datasets": [{"id": dataset_id} for dataset_id in dataset_ids],
            "calculations": [{"id": run_id} for run_id in calculation_run_ids],
        },
        "judgment": {"state": "frozen" if judgments else "not_frozen", "items": judgments},
        "adoption": {"state": "adopted" if adoptions else "not_adopted", "items": adoptions},
        "forecast": {"state": forecast_state, "items": forecasts},
        "process_review": {
            "state": "locked" if process_reviews else "not_locked",
            "items": process_reviews,
        },
        "postmortem": {
            "state": "completed" if postmortems else ("eligible" if judgments and forecast_state in {"resolved", "scored", "not_required"} else "not_eligible"),
            "items": postmortems,
        },
        "lesson": {"state": _aggregate_lesson_state(lessons), "items": lessons},
        "memory": (projected_synthesis or {}).get("memory"),
        "next_update_triggers": decision_quality.get("update_triggers", []),
        "warnings": warnings,
        "read_only": True,
    }


def _episode_card(episode: dict[str, Any]) -> dict[str, Any]:
    synthesis = (episode.get("analysis") or {}).get("synthesis") or {}
    return {
        "workflow_run_id": episode["workflow_run_id"],
        "created_at": (episode.get("run") or {}).get("created_at", ""),
        "title": synthesis.get("title") or episode["workflow_run_id"],
        "summary": synthesis.get("summary") or "Analysis is in progress; no canonical synthesis has been recorded.",
        "status": synthesis.get("status", {}),
        "analysis_state": episode["analysis"]["state"],
        "judgment_state": episode["judgment"]["state"],
        "adoption_state": episode["adoption"]["state"],
        "forecast_state": episode["forecast"]["state"],
        "postmortem_state": episode["postmortem"]["state"],
        "process_review_state": episode["process_review"]["state"],
        "lesson_state": episode["lesson"]["state"],
        "warnings": episode["warnings"],
    }


def _run_ids(root: Path) -> list[str]:
    base = root / ANALYSIS_RUNS_ROOT
    if not base.exists():
        return []
    return [path.parent.name for path in base.glob("*/run.json")]


def _forecast_state(items: list[dict[str, Any]]) -> str:
    if not items:
        return "not_required"
    events = {str(item.get("event_type") or "") for item in items}
    if "scored" in events:
        return "scored"
    if any(str(item.get("status") or "") == "closed" for item in items):
        return "resolved"
    return "open"


def _json_run_id(path: Path) -> str:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    return str(value.get("workflow_run_id") or "") if isinstance(value, dict) else ""


def _lesson_states(root: Path, decision_ids: set[str]) -> list[dict[str, Any]]:
    path = root / IMPROVE_LEDGER_PATH
    if not path.exists():
        return []
    latest: dict[str, dict[str, Any]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        origins = set(str(item) for item in event.get("origin_episode_ids", []))
        if origins & decision_ids:
            latest[str(event.get("lesson_id") or "")] = event
    return list(latest.values())


def _aggregate_lesson_state(items: list[dict[str, Any]]) -> str:
    if not items:
        return "none"
    states = {str(item.get("lesson_state") or "candidate") for item in items}
    return next(iter(states)) if len(states) == 1 else "mixed"

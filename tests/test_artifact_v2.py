from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from tradingcodex_service.application.analysis_runs import begin_analysis_run
from tradingcodex_service.application.artifact_bindings import (
    authenticated_service_artifact_binding_args,
    record_authenticated_artifact_binding,
    verify_authenticated_artifact_binding,
)
from tradingcodex_service.application.artifact_quality import evaluate_artifact_quality
from tradingcodex_service.application.artifact_v2 import canonicalize_write_request, project_artifact
from tradingcodex_service.application.decision_episodes import get_decision_episode
from tradingcodex_service.application.forecasting import issue_forecast, resolve_forecast
from tradingcodex_service.application.judgments import (
    record_decision_adoption,
    terminal_adoption_args,
)
from tradingcodex_service.application.judgment_postmortems import (
    create_judgment_postmortem,
    record_judgment_process_review,
)
from tradingcodex_service.application.markdown_preview import split_markdown_frontmatter
from tradingcodex_service.application.research import (
    find_workspace_research_artifact_read_only,
    list_research_artifacts,
    record_source_snapshot,
)
from tradingcodex_service.application.research import (
    authenticated_service_research_args,
    store_authenticated_research_artifact,
)
from tradingcodex_service.application.runtime import ensure_workspace_manifest
from tradingcodex_service.application.common import file_hash
from tradingcodex_service.application.viewer import viewer_snapshot
from tradingcodex_service.mcp_runtime import call_mcp_tool
from tradingcodex_service.api import ArtifactLineageRequest, ArtifactStatusRequest


RUN_ID = "analysis-artifact-v2"


@pytest.fixture(autouse=True)
def workspace_run(tmp_path: Path) -> None:
    ensure_workspace_manifest(tmp_path)
    begin_analysis_run(
        tmp_path,
        "Integrate v2 evidence.",
        run_id=RUN_ID,
        apply_investor_context=False,
    )


def _v2_args(artifact_id: str, *, artifact_type: str = "research_memo", inputs: list[str] | None = None) -> dict[str, object]:
    value: dict[str, object] = {
        "artifact_id": artifact_id,
        "artifact_type": artifact_type,
        "title": artifact_id,
        "universe": "public_equity",
        "markdown": f"# {artifact_id}\n\nEvidence, implication, and bounded judgment.\n",
        "summary": "A compact decision-relevant conclusion.",
        "status": {
            "handoff": "accepted",
            "evidence_readiness": "decision-grade",
            "action_readiness": "research-only",
            "confidence": "medium",
            "confidence_basis": "Authenticated current-run evidence with explicit limits.",
            "missing_evidence": [],
            "blocked_actions": ["order_execution"],
        },
        "lineage": {
            "workflow_run_id": RUN_ID,
            "knowledge_cutoff": "2026-07-12T00:00:00Z",
            "input_artifact_ids": inputs or [],
            "source_snapshot_ids": [],
            "dataset_ids": [],
            "calculation_run_ids": [],
        },
        "requirements": [],
    }
    if artifact_type == "synthesis_report":
        value.pop("artifact_id")
    return value


def test_unstructured_research_markdown_is_a_nonfatal_diagnostic(tmp_path: Path) -> None:
    note = tmp_path / "trading/research/user-note.md"
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text("# User note\n\nPreserve this workspace-owned note.\n", encoding="utf-8")

    listing = list_research_artifacts(tmp_path, {"limit": 50})

    assert listing["artifacts"] == []
    assert listing["invalid_artifact_count"] == 1
    assert listing["invalid_artifacts"] == [
        {
            "path": "trading/research/user-note.md",
            "error": "research artifact frontmatter requires artifact_id: trading/research/user-note.md",
        }
    ]


def _v1_args(
    artifact_id: str,
    *,
    run_id: str,
    artifact_type: str = "research_memo",
    role: str = "fundamental-analyst",
    inputs: list[str] | None = None,
    input_hashes: dict[str, str] | None = None,
) -> dict[str, object]:
    return {
        "artifact_id": artifact_id,
        "artifact_type": artifact_type,
        "universe": "public_equity",
        "title": artifact_id,
        "markdown": f"# {artifact_id}\n\nAuthenticated legacy evidence.\n",
        "workflow_run_id": run_id,
        "artifact_schema_version": 1,
        "role": role,
        "producer_role": role,
        "principal_id": role,
        "handoff_state": "accepted",
        "readiness_label": "factual-baseline",
        "source_as_of": "2026-07-12T00:00:00Z",
        "confidence": "medium",
        "context_summary": "Authenticated legacy summary.",
        "reader_summary": "Authenticated legacy summary for review.",
        "next_recipient": "head-manager",
        "next_action": "Compare the authenticated legacy evidence.",
        "input_artifact_ids": inputs or [],
        "input_artifact_hashes": input_hashes or {},
        "source_snapshot_ids": [],
        "source_snapshot_hashes": {},
        "dataset_ids": [],
        "dataset_manifest_hashes": {},
        "calculation_run_ids": [],
        "calculation_run_hashes": {},
        "calculation_reuse_origins": {},
        "strategy_name": "",
        "strategy_hash": "",
        "investment_brain_id": "",
        "investment_brain_version": "",
        "investment_brain_content_digest": "",
        "investor_context_applied": False,
        "investor_context_hash": "",
    }


def _process_review_payload(judgment_id: str) -> dict[str, object]:
    return {
        "judgment_id": judgment_id,
        "created_by": "head-manager",
        "process_review": {
            field: "Reviewed before outcome reveal."
            for field in (
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
        },
    }


def test_v2_markdown_is_compact_and_receipt_restores_lineage(tmp_path: Path) -> None:
    result = call_mcp_tool(
        tmp_path,
        "create_research_artifact",
        _v2_args("fundamental-v2"),
        transport_principal="fundamental-analyst",
    )
    artifact = result["artifact"]
    assert artifact["id"] == "fundamental-v2"
    stored = find_workspace_research_artifact_read_only(tmp_path, "fundamental-v2")
    assert stored is not None
    authentication = verify_authenticated_artifact_binding(tmp_path, stored)
    receipt = json.loads((tmp_path / authentication["path"]).read_text(encoding="utf-8"))
    assert receipt["schema_version"] == 4
    assert receipt["input_artifact_versions"] == {}
    document = split_markdown_frontmatter((tmp_path / stored["path"]).read_text(encoding="utf-8"))
    assert document.frontmatter["artifact_schema_version"] == 2
    for omitted in ("input_artifact_hashes", "source_snapshot_hashes", "dataset_manifest_hashes", "strategy_hash", "created_by", "role", "workspace_native"):
        assert omitted not in document.frontmatter
    assert "missing_evidence" not in document.frontmatter


def test_synthesis_identity_judgment_adoption_and_episode(tmp_path: Path) -> None:
    call_mcp_tool(tmp_path, "create_research_artifact", _v2_args("fundamental-v2"), transport_principal="fundamental-analyst")
    synthesis = call_mcp_tool(
        tmp_path,
        "create_research_artifact",
        _v2_args("ignored", artifact_type="synthesis_report", inputs=["fundamental-v2"]),
        transport_principal="head-manager",
    )["artifact"]
    assert synthesis["id"] == f"synthesis-{RUN_ID}"
    assert synthesis["path"] == f"trading/reports/head-manager/synthesis-{RUN_ID}.md"
    assert synthesis["lineage"]["input_artifacts"] == [
        {
            "id": "fundamental-v2",
            "version": 1,
            "content_hash": synthesis["authentication"]["input_artifact_hashes"]["fundamental-v2"],
        }
    ]
    judgment = call_mcp_tool(
        tmp_path,
        "record_judgment_snapshot",
        {"workflow_run_id": RUN_ID, "forecast_block_reason": "No scoreable forecast was required."},
        transport_principal="head-manager",
    )["judgment_snapshot"]
    with pytest.raises(PermissionError, match="user terminal"):
        record_decision_adoption(tmp_path, {"judgment_id": judgment["judgment_id"], "decision": "Adopt for monitoring."})
    adoption = record_decision_adoption(
        tmp_path,
        terminal_adoption_args({"judgment_id": judgment["judgment_id"], "decision": "Adopt for monitoring."}),
    )["decision_adoption"]
    assert adoption["recorded_via"] == "user-terminal"
    assert adoption["authority"] == "evidence_only"
    process = record_judgment_process_review(tmp_path, {
        "judgment_id": judgment["judgment_id"],
        "created_by": "head-manager",
        "process_review": {field: "Reviewed before outcome reveal." for field in (
            "original_thesis", "evidence_quality", "base_rate_quality",
            "alternatives_considered", "assumptions", "confidence_process",
            "invalidation_discipline", "handoff_process", "process_conclusion",
        )},
    })["process_review"]
    with pytest.raises(ValueError, match="requires adoption_id"):
        create_judgment_postmortem(tmp_path, {
            "judgment_id": judgment["judgment_id"], "process_review_id": process["id"],
            "evaluate_user_decision": True, "trigger": "review", "created_by": "head-manager",
            "findings": [{"finding": "Process evidence."}],
            "investment_judgment_review": {"quality": "sound"},
            "next_actions": ["Monitor."],
            "lesson_candidates": [{"statement": "Keep explicit gates.", "reason": "They preserved scope.", "scope": "decision process", "counterevidence": [], "invalidation_conditions": ["Gate fails."]}],
        })
    create_judgment_postmortem(tmp_path, {
        "judgment_id": judgment["judgment_id"], "process_review_id": process["id"],
        "evaluate_user_decision": True, "adoption_id": adoption["adoption_id"],
        "trigger": "review", "created_by": "head-manager",
        "findings": [{"finding": "Process evidence."}],
        "investment_judgment_review": {"quality": "sound"},
        "next_actions": ["Monitor."],
        "lesson_candidates": [{"statement": "Keep explicit gates.", "reason": "They preserved scope.", "scope": "decision process", "counterevidence": [], "invalidation_conditions": ["Gate fails."]}],
    })
    episode = get_decision_episode(tmp_path, RUN_ID)["episode"]
    assert episode["analysis"]["state"] == "synthesized"
    assert episode["judgment"]["state"] == "frozen"
    assert episode["adoption"]["state"] == "adopted"
    assert episode["postmortem"]["state"] == "completed"
    assert episode["lesson"]["state"] == "candidate"
    canonical = [item for item in episode["analysis"]["artifacts"] if item["canonical_synthesis"]]
    assert [item["id"] for item in canonical] == [f"synthesis-{RUN_ID}"]


def test_v2_process_review_is_locked_before_forecast_outcome(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    clock = [datetime(2026, 7, 22, 0, 0, tzinfo=timezone.utc)]

    def system_now() -> str:
        return clock[0].isoformat().replace("+00:00", "Z")

    monkeypatch.setattr("tradingcodex_service.application.forecasting._system_now", system_now)
    monkeypatch.setattr("tradingcodex_service.application.judgment_postmortems.now_iso", system_now)
    monkeypatch.setattr("tradingcodex_service.application.judgments.now_iso", system_now)
    monkeypatch.setattr("tradingcodex_service.application.research.now_iso", system_now)
    base_snapshot = record_source_snapshot(
        tmp_path,
        {
            "provider": "artifact-v2-test",
            "source_category": "base-rate",
            "known_at": "2026-07-12T00:00:00Z",
            "retrieved_at": "2026-07-12T00:00:00Z",
            "recorded_at": "2026-07-12T00:00:00Z",
            "revision": "original",
            "vintage": "2026-07-12",
            "payload": {"value": 0.5},
        },
    )["snapshot_id"]
    outcome_snapshot = ""

    def create_forecast_judgment(run_id: str, suffix: str) -> tuple[dict[str, object], str]:
        if run_id != RUN_ID:
            begin_analysis_run(
                tmp_path,
                f"Forecast order {suffix}.",
                run_id=run_id,
                apply_investor_context=False,
            )
        evidence_id = f"forecast-evidence-{suffix}"
        evidence_args = _v2_args(evidence_id)
        evidence_args["lineage"]["workflow_run_id"] = run_id
        evidence = call_mcp_tool(
            tmp_path,
            "create_research_artifact",
            evidence_args,
            transport_principal="fundamental-analyst",
        )["artifact"]
        forecast_id = f"forecast-{suffix}"
        issue_forecast(
            tmp_path,
            {
                "forecast_id": forecast_id,
                "artifact_id": evidence_id,
                "artifact_path": evidence["path"],
                "role": "fundamental-analyst",
                "author": "fundamental-analyst",
                "forecast_target": "Revenue growth is positive.",
                "target_type": "binary",
                "horizon": "2026-07-22T12:00:00Z",
                "issued_at": "2026-07-21T00:00:00Z",
                "knowledge_cutoff": "2026-07-12T00:00:00Z",
                "probability": 0.7,
                "base_rate": {
                    "cohort": "comparable issuers",
                    "source_snapshot_id": base_snapshot,
                    "sample_size": 30,
                    "selection_rule": "same reporting regime",
                    "value": 0.5,
                },
                "evidence_ids": [evidence_id],
                "contrary_evidence": ["Demand may weaken."],
                "invalidation_conditions": ["Reported growth is non-positive."],
                "update_triggers": ["Guidance changes."],
                "resolution_rule": "Resolve from the audited filing.",
            },
        )
        synthesis_args = _v2_args(
            "ignored",
            artifact_type="synthesis_report",
            inputs=[evidence_id],
        )
        synthesis_args["lineage"]["workflow_run_id"] = run_id
        synthesis_args["requirements"] = ["forecast"]
        synthesis_args["forecast"] = {"posture": "eligible"}
        call_mcp_tool(
            tmp_path,
            "create_research_artifact",
            synthesis_args,
            transport_principal="head-manager",
        )
        judgment = call_mcp_tool(
            tmp_path,
            "record_judgment_snapshot",
            {"workflow_run_id": run_id, "forecast_ids": [forecast_id]},
            transport_principal="head-manager",
        )["judgment_snapshot"]
        return judgment, forecast_id

    def resolve(forecast_id: str) -> None:
        resolve_forecast(
            tmp_path,
            {
                "forecast_id": forecast_id,
                "resolver": "judgment-reviewer",
                "outcome": 1,
                "resolution_source_snapshot_id": outcome_snapshot,
                "observed_at": "2026-07-23T00:00:00Z",
                "resolved_at": "2026-07-23T00:00:00Z",
            },
        )

    late_judgment, late_forecast_id = create_forecast_judgment(
        RUN_ID,
        "late-review",
    )
    ordered_judgment, ordered_forecast_id = create_forecast_judgment(
        "analysis-ordered-v2-postmortem",
        "ordered",
    )
    process = record_judgment_process_review(
        tmp_path,
        _process_review_payload(str(ordered_judgment["judgment_id"])),
    )["process_review"]
    clock[0] += timedelta(days=1)
    outcome_snapshot = record_source_snapshot(
        tmp_path,
        {
            "provider": "artifact-v2-test",
            "source_category": "resolution",
            "known_at": "2026-07-23T00:00:00Z",
            "retrieved_at": "2026-07-23T00:00:00Z",
            "recorded_at": "2026-07-23T00:00:00Z",
            "revision": "original",
            "vintage": "2026-07-23",
            "payload": {"outcome": 1},
        },
    )["snapshot_id"]
    resolve(late_forecast_id)
    with pytest.raises(ValueError, match="outcome is already recorded"):
        record_judgment_process_review(
            tmp_path,
            _process_review_payload(str(late_judgment["judgment_id"])),
        )
    resolve(ordered_forecast_id)
    report = create_judgment_postmortem(
        tmp_path,
        {
            "judgment_id": ordered_judgment["judgment_id"],
            "process_review_id": process["id"],
            "trigger": "forecast resolved",
            "created_by": "head-manager",
            "findings": [{"finding": "The outcome followed the locked review."}],
            "investment_judgment_review": {"quality": "sound"},
            "next_actions": ["Monitor calibration."],
            "lesson_candidates": [
                {
                    "statement": "Lock review before outcome reveal.",
                    "reason": "It prevents hindsight contamination.",
                    "scope": "forecast review",
                    "counterevidence": [],
                    "invalidation_conditions": ["The outcome was already known."],
                }
            ],
        },
    )["postmortem"]
    assert report["forecast_outcome_refs"][0]["outcome_revealed_at"] > process["locked_at"]
    episode = get_decision_episode(tmp_path, "analysis-ordered-v2-postmortem")["episode"]
    assert episode["process_review"]["state"] == "locked"
    assert episode["process_review"]["items"][0]["id"] == process["id"]


def test_legacy_projection_and_old_write_rejection() -> None:
    projection = project_artifact({
        "artifact_schema_version": 1,
        "artifact_id": "legacy",
        "artifact_type": "research_memo",
        "universe": "equity",
        "readiness_label": "ready-for-draft",
        "reader_summary": "Legacy summary.",
    })["artifact"]
    assert projection["status"]["evidence_readiness"] == "decision-grade"
    assert projection["status"]["action_readiness"] == "draft-eligible"
    assert projection["compatibility_warnings"] == ["legacy_artifact_v1"]
    with pytest.raises(ValueError, match="legacy fields: readiness_label"):
        canonicalize_write_request({"readiness_label": "ready-for-draft"})


def test_rest_nested_v2_envelopes_reject_unknown_fields() -> None:
    with pytest.raises(ValidationError, match="readiness_label"):
        ArtifactStatusRequest.model_validate(
            {
                "handoff": "accepted",
                "evidence_readiness": "decision-grade",
                "action_readiness": "research-only",
                "confidence": "medium",
                "confidence_basis": "Bounded evidence.",
                "readiness_label": "ready-for-draft",
            }
        )
    with pytest.raises(ValidationError, match="input_artifact_hashes"):
        ArtifactLineageRequest.model_validate(
            {
                "workflow_run_id": RUN_ID,
                "input_artifact_hashes": {"legacy": "forbidden"},
            }
        )


def test_v1_canonical_bytes_remain_unchanged_and_verifiable(tmp_path: Path) -> None:
    legacy = store_authenticated_research_artifact(
        tmp_path,
        authenticated_service_research_args({
            "artifact_id": "legacy-v1",
            "artifact_type": "research_memo",
            "universe": "public_equity",
            "title": "Legacy v1",
            "markdown": "# Legacy v1\n\nImmutable legacy evidence.\n",
            "workflow_run_id": RUN_ID,
            "artifact_schema_version": 1,
            "role": "fundamental-analyst",
            "producer_role": "fundamental-analyst",
            "principal_id": "fundamental-analyst",
            "handoff_state": "revise",
            "readiness_label": "not-decision-ready",
            "context_summary": "Legacy summary.",
            "input_artifact_ids": [],
            "input_artifact_hashes": {},
            "source_snapshot_ids": [],
            "source_snapshot_hashes": {},
            "dataset_ids": [],
            "dataset_manifest_hashes": {},
            "calculation_run_ids": [],
            "calculation_run_hashes": {},
            "calculation_reuse_origins": {},
            "strategy_name": "",
            "strategy_hash": "",
            "investment_brain_id": "",
            "investment_brain_version": "",
            "investment_brain_content_digest": "",
            "investor_context_applied": False,
            "investor_context_hash": "",
        }),
    )
    path = tmp_path / legacy["path"]
    before = path.read_bytes()
    stored = find_workspace_research_artifact_read_only(tmp_path, "legacy-v1")
    assert stored is not None
    verify_authenticated_artifact_binding(tmp_path, stored)
    projected = project_artifact(stored)["artifact"]
    assert projected["status"]["evidence_readiness"] == "insufficient"
    assert path.read_bytes() == before


def test_legacy_factual_synthesis_cannot_freeze_judgment(tmp_path: Path) -> None:
    run_id = "analysis-legacy-factual"
    begin_analysis_run(
        tmp_path,
        "Legacy factual synthesis.",
        run_id=run_id,
        apply_investor_context=False,
    )
    source = store_authenticated_research_artifact(
        tmp_path,
        authenticated_service_research_args(
            _v1_args("legacy-factual-source", run_id=run_id)
        ),
    )
    store_authenticated_research_artifact(
        tmp_path,
        authenticated_service_research_args(
            _v1_args(
                "ignored",
                run_id=run_id,
                artifact_type="synthesis_report",
                role="head-manager",
                inputs=["legacy-factual-source"],
                input_hashes={"legacy-factual-source": source["content_hash"]},
            )
        ),
    )
    with pytest.raises(ValueError, match="factual and screening"):
        call_mcp_tool(
            tmp_path,
            "record_judgment_snapshot",
            {
                "workflow_run_id": run_id,
                "forecast_block_reason": "No forecast was required.",
            },
            transport_principal="head-manager",
        )


def test_durable_insufficient_abstention_can_freeze_judgment(tmp_path: Path) -> None:
    run_id = "analysis-insufficient-abstention"
    begin_analysis_run(
        tmp_path,
        "Preserve a durable abstention.",
        run_id=run_id,
        apply_investor_context=False,
    )
    source = _v2_args("abstention-source")
    source["lineage"]["workflow_run_id"] = run_id
    call_mcp_tool(
        tmp_path,
        "create_research_artifact",
        source,
        transport_principal="fundamental-analyst",
    )
    synthesis = _v2_args(
        "ignored",
        artifact_type="synthesis_report",
        inputs=["abstention-source"],
    )
    synthesis["lineage"]["workflow_run_id"] = run_id
    synthesis["status"].update(
        {
            "evidence_readiness": "insufficient",
            "action_readiness": "blocked",
            "missing_evidence": ["A decision-critical anchor is unavailable."],
            "blocked_actions": ["portfolio_review", "order_drafting"],
        }
    )
    call_mcp_tool(
        tmp_path,
        "create_research_artifact",
        synthesis,
        transport_principal="head-manager",
    )
    judgment = call_mcp_tool(
        tmp_path,
        "record_judgment_snapshot",
        {
            "workflow_run_id": run_id,
            "forecast_block_reason": "The abstention has no scoreable target.",
        },
        transport_principal="head-manager",
    )["judgment_snapshot"]
    assert judgment["workflow_run_id"] == run_id


def test_requirements_are_inherited_and_reviewer_ids_are_exact(tmp_path: Path) -> None:
    forecast_input = _v2_args("forecast-input")
    forecast_input["requirements"] = ["forecast"]
    forecast_input["forecast"] = {
        "posture": "blocked",
        "block_reason": "No defensible scoreable target is available.",
    }
    call_mcp_tool(
        tmp_path,
        "create_research_artifact",
        forecast_input,
        transport_principal="fundamental-analyst",
    )
    synthesis = _v2_args(
        "ignored", artifact_type="synthesis_report", inputs=["forecast-input"]
    )
    with pytest.raises(ValueError, match="inherited forecast requirement"):
        call_mcp_tool(
            tmp_path,
            "create_research_artifact",
            synthesis,
            transport_principal="head-manager",
        )
    synthesis["forecast"] = {
        "posture": "blocked",
        "block_reason": "No defensible scoreable target is available.",
    }
    stored = call_mcp_tool(
        tmp_path,
        "create_research_artifact",
        synthesis,
        transport_principal="head-manager",
    )["artifact"]
    assert stored["requirements"] == ["forecast"]
    with pytest.raises(ValueError, match="must match the canonical synthesis"):
        call_mcp_tool(
            tmp_path,
            "record_judgment_snapshot",
            {
                "workflow_run_id": RUN_ID,
                "forecast_block_reason": "Caller supplied a different reason.",
            },
            transport_principal="head-manager",
        )
    judgment = call_mcp_tool(
        tmp_path,
        "record_judgment_snapshot",
        {"workflow_run_id": RUN_ID},
        transport_principal="head-manager",
    )["judgment_snapshot"]
    assert judgment["forecast_refs"] == []
    assert judgment["forecast_block_reason"] == "No defensible scoreable target is available."

    other_run = "analysis-reviewer-v2"
    begin_analysis_run(
        tmp_path,
        "Verify exact reviewer lineage.",
        run_id=other_run,
        apply_investor_context=False,
    )
    evidence = _v2_args("review-evidence")
    evidence["lineage"]["workflow_run_id"] = other_run
    review = _v2_args("independent-review")
    review["lineage"]["workflow_run_id"] = other_run
    call_mcp_tool(tmp_path, "create_research_artifact", evidence, transport_principal="fundamental-analyst")
    call_mcp_tool(tmp_path, "create_research_artifact", review, transport_principal="judgment-reviewer")
    reviewed_synthesis = _v2_args(
        "ignored", artifact_type="synthesis_report", inputs=["review-evidence"]
    )
    reviewed_synthesis["lineage"]["workflow_run_id"] = other_run
    reviewed_synthesis["requirements"] = ["decision_quality"]
    reviewed_synthesis["decision_quality"] = {
        "review_artifact_ids": ["independent-review"],
        "update_triggers": ["Material evidence changes."],
    }
    with pytest.raises(ValueError, match="judgment-reviewer inputs"):
        call_mcp_tool(tmp_path, "create_research_artifact", reviewed_synthesis, transport_principal="head-manager")
    reviewed_synthesis["lineage"]["input_artifact_ids"].append("independent-review")
    call_mcp_tool(tmp_path, "create_research_artifact", reviewed_synthesis, transport_principal="head-manager")


def test_memory_cutoff_hash_and_tamper_detection(tmp_path: Path) -> None:
    call_mcp_tool(tmp_path, "create_research_artifact", _v2_args("memory-base"), transport_principal="fundamental-analyst")
    call_mcp_tool(
        tmp_path,
        "create_research_artifact",
        _v2_args("ignored", artifact_type="synthesis_report", inputs=["memory-base"]),
        transport_principal="head-manager",
    )
    judgment = call_mcp_tool(
        tmp_path,
        "record_judgment_snapshot",
        {"workflow_run_id": RUN_ID, "forecast_block_reason": "No scoreable forecast was required."},
        transport_principal="head-manager",
    )["judgment_snapshot"]

    memory_run = "analysis-memory-ref-v2"
    begin_analysis_run(tmp_path, "Use a prior judgment as memory.", run_id=memory_run, apply_investor_context=False)
    memory_input = _v2_args("memory-current")
    memory_input["lineage"].update(
        {"workflow_run_id": memory_run, "knowledge_cutoff": judgment["recorded_at"]}
    )
    call_mcp_tool(tmp_path, "create_research_artifact", memory_input, transport_principal="fundamental-analyst")
    memory_synthesis = _v2_args(
        "ignored", artifact_type="synthesis_report", inputs=["memory-current"]
    )
    memory_synthesis["lineage"].update(
        {"workflow_run_id": memory_run, "knowledge_cutoff": judgment["recorded_at"]}
    )
    memory_synthesis["memory"] = {
        "cutoff": judgment["recorded_at"],
        "initial_view": "Current evidence alone supports monitoring.",
        "refs": [{"kind": "judgment_snapshot", "id": judgment["judgment_id"]}],
        "delta": {"direction": "strengthened", "summary": "The prior process held under review."},
    }
    stored = call_mcp_tool(tmp_path, "create_research_artifact", memory_synthesis, transport_principal="head-manager")
    assert stored["artifact"]["memory"]["delta"]["direction"] == "strengthened"
    receipt_path = tmp_path / stored["artifact"]["authentication"]["path"]
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["memory_ref_hashes"] == {
        f"judgment_snapshot:{judgment['judgment_id']}": judgment["snapshot_hash"]
    }

    judgment_path = tmp_path / "trading" / "decisions" / f"{judgment['judgment_id']}.judgment-snapshot.json"
    tampered = json.loads(judgment_path.read_text(encoding="utf-8"))
    tampered["forecast_block_reason"] = "tampered"
    judgment_path.write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(ValueError, match="integrity"):
        verify_authenticated_artifact_binding(
            tmp_path,
            find_workspace_research_artifact_read_only(tmp_path, f"synthesis-{memory_run}"),
        )


def test_public_cards_search_and_episode_fail_closed_on_tampered_role_artifact(tmp_path: Path) -> None:
    role = call_mcp_tool(
        tmp_path,
        "create_research_artifact",
        _v2_args("tamper-visible-role"),
        transport_principal="fundamental-analyst",
    )["artifact"]
    call_mcp_tool(
        tmp_path,
        "create_research_artifact",
        _v2_args(
            "ignored",
            artifact_type="synthesis_report",
            inputs=["tamper-visible-role"],
        ),
        transport_principal="head-manager",
    )
    role_path = tmp_path / role["path"]
    role_path.write_text(
        role_path.read_text(encoding="utf-8") + "\nTampered public projection.\n",
        encoding="utf-8",
    )
    search = call_mcp_tool(
        tmp_path,
        "search_research_artifacts",
        {"query": "tamper-visible-role"},
        transport_principal="head-manager",
    )
    assert all(item["id"] != "tamper-visible-role" for item in search["items"])
    with pytest.raises(ValueError, match="content_hash|file hash"):
        get_decision_episode(tmp_path, RUN_ID)
    snapshot = viewer_snapshot(tmp_path)
    assert snapshot["sections"]["artifacts"]["ok"] is False
    assert snapshot["sections"]["episodes"]["ok"] is False


def test_append_cutoff_boundary_quality_and_legacy_ambiguity(tmp_path: Path) -> None:
    created = call_mcp_tool(tmp_path, "create_research_artifact", _v2_args("append-v2"), transport_principal="fundamental-analyst")
    changed = _v2_args("append-v2")
    changed["lineage"]["knowledge_cutoff"] = "2026-07-13T00:00:00Z"
    with pytest.raises(ValueError, match="changed knowledge_cutoff requires a new analysis run"):
        call_mcp_tool(tmp_path, "append_research_artifact_version", changed, transport_principal="fundamental-analyst")
    quality = evaluate_artifact_quality(
        tmp_path, created["artifact"]["path"], strict=True
    )
    assert quality["status"] == "pass"
    assert quality["artifact_schema_version"] == 2

    legacy_run = "analysis-legacy-ambiguous"
    begin_analysis_run(tmp_path, "Legacy ambiguity.", run_id=legacy_run, apply_investor_context=False)
    source = store_authenticated_research_artifact(
        tmp_path,
        authenticated_service_research_args(
            _v1_args("legacy-source", run_id=legacy_run)
        ),
    )
    canonical = store_authenticated_research_artifact(
        tmp_path,
        authenticated_service_research_args(
            _v1_args(
                "legacy-canonical",
                run_id=legacy_run,
                artifact_type="synthesis_report",
                role="head-manager",
                inputs=["legacy-source"],
                input_hashes={"legacy-source": source["content_hash"]},
            )
        ),
    )
    canonical_path = tmp_path / canonical["path"]
    document = split_markdown_frontmatter(canonical_path.read_text(encoding="utf-8"))
    reports = tmp_path / "trading" / "reports" / "head-manager"
    for suffix in ("a", "b"):
        candidate_id = f"legacy-{suffix}"
        candidate_frontmatter = {**document.frontmatter, "artifact_id": candidate_id}
        candidate_path = reports / f"{candidate_id}.md"
        candidate_path.write_text(
            f"---\n{yaml.safe_dump(candidate_frontmatter, sort_keys=True)}---\n\n{document.body}",
            encoding="utf-8",
        )
        candidate = find_workspace_research_artifact_read_only(tmp_path, candidate_id)
        assert candidate is not None
        record_authenticated_artifact_binding(
            tmp_path,
            authenticated_service_artifact_binding_args(
                candidate,
                expected_file_sha256=file_hash(candidate_path),
            ),
        )
    canonical_path.unlink()
    episode = get_decision_episode(tmp_path, legacy_run)["episode"]
    assert episode["analysis"]["state"] == "ambiguous"
    assert episode["warnings"] == ["ambiguous_legacy_synthesis"]
    assert len(episode["analysis"]["synthesis_candidates"]) == 2

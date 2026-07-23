from __future__ import annotations

from pathlib import Path

import yaml
import pytest

from tradingcodex_service.application.artifact_quality import (
    ANTI_OVERFIT_CHECK_KEYS,
    evaluate_decision_quality,
)
from tradingcodex_service.application.analysis_runs import begin_analysis_run
from tradingcodex_service.application.research import get_research_artifact
from tradingcodex_service.application.runtime import ensure_workspace_manifest
from tradingcodex_service.api import ResearchArtifactRequest
from tradingcodex_service.mcp_runtime import call_mcp_tool


def _write_artifact(
    root: Path,
    extra_frontmatter: str = "",
    *,
    role: str = "portfolio-manager",
    readiness_label: str = "research-ready",
    body: str = "The artifact exists. Head Manager owns workflow judgment.\n",
) -> str:
    path = root / "trading" / "research" / "dynamic-quality.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\n"
        "artifact_id: dynamic-quality\n"
        "artifact_type: research_report\n"
        "universe: ACME\n"
        "workflow_type: thesis_review_then_portfolio_risk_review\n"
        f"role: {role}\n"
        "title: Dynamic quality contract\n"
        "source_as_of: 2026-07-13T00:00:00Z\n"
        f"readiness_label: {readiness_label}\n"
        "context_summary: Evidence summary.\n"
        "reader_summary: Reader summary.\n"
        "next_action: Head Manager decides the next useful step.\n"
        "handoff_state: accepted\n"
        "confidence: medium\n"
        "next_recipient: head-manager\n"
        "missing_evidence: []\n"
        "blocked_actions: []\n"
        "source_snapshot_ids: []\n"
        f"{extra_frontmatter}"
        "---\n\n"
        "# Dynamic quality contract\n\n"
        f"{body}",
        encoding="utf-8",
    )
    return path.relative_to(root).as_posix()


@pytest.mark.parametrize(
    "readiness_label",
    (
        "not-decision-ready",
        "calculation-only; not-decision-ready",
        "calculation-only / not-decision-ready / anchor-missing",
    ),
)
def test_calculation_only_valuation_accepts_exact_not_decision_ready_token(
    tmp_path: Path,
    readiness_label: str,
) -> None:
    artifact_path = _write_artifact(
        tmp_path,
        role="valuation-analyst",
        readiness_label=readiness_label,
    )

    result = evaluate_decision_quality(tmp_path, artifact_path, strict=True)

    assert result["status"] == "pass"
    assert "decision_quality.current_price_or_market_anchor" not in result[
        "required_fields_missing"
    ]


@pytest.mark.parametrize(
    "readiness_label",
    (
        "calculation-only-not-decision-readyish",
        "calculation-only; not-decision-readiness",
        "calculation-only",
    ),
)
def test_valuation_does_not_accept_not_decision_ready_as_a_substring(
    tmp_path: Path,
    readiness_label: str,
) -> None:
    artifact_path = _write_artifact(
        tmp_path,
        role="valuation-analyst",
        readiness_label=readiness_label,
    )

    result = evaluate_decision_quality(tmp_path, artifact_path, strict=True)

    assert result["status"] == "fail"
    assert "decision_quality.current_price_or_market_anchor" in result[
        "required_fields_missing"
    ]


@pytest.mark.parametrize(
    ("anchor_field", "placeholder"),
    (
        ("market_anchor_as_of", "Not provided; calculation-only output."),
        ("market_anchor_as_of", "N/A"),
        ("current_price_as_of", "Unknown at calculation time"),
    ),
)
def test_valuation_absence_placeholder_is_not_a_market_anchor(
    tmp_path: Path,
    anchor_field: str,
    placeholder: str,
) -> None:
    artifact_path = _write_artifact(
        tmp_path,
        f'{anchor_field}: "{placeholder}"\n',
        role="valuation-analyst",
        readiness_label="calculation-only",
    )

    result = evaluate_decision_quality(tmp_path, artifact_path, strict=True)

    assert result["status"] == "fail"
    assert "decision_quality.current_price_or_market_anchor" in result[
        "required_fields_missing"
    ]


def test_free_form_valuation_readiness_remains_compatible_with_real_market_anchor(
    tmp_path: Path,
) -> None:
    artifact_path = _write_artifact(
        tmp_path,
        "market_anchor_as_of: 2026-07-16T20:00:00Z\n",
        role="valuation-analyst",
        readiness_label="calculation-only; internal-review-pending",
    )

    result = evaluate_decision_quality(tmp_path, artifact_path, strict=True)

    assert result["status"] == "pass"
    assert "decision_quality.current_price_or_market_anchor" not in result[
        "required_fields_missing"
    ]


def test_descriptive_workflow_type_does_not_activate_server_quality_lane(tmp_path: Path) -> None:
    artifact_path = _write_artifact(tmp_path)

    result = evaluate_decision_quality(tmp_path, artifact_path, strict=True)

    assert result["status"] == "pass"
    assert not any(
        field.startswith("decision_quality.")
        for field in result["required_fields_missing"]
    )


def test_explicit_quality_requirements_still_fail_closed(tmp_path: Path) -> None:
    artifact_path = _write_artifact(
        tmp_path,
        "forecast_required: true\n"
        "forecast_allowed: false\n"
        "investor_context_gate_required: true\n"
        "decision_quality_required: true\n",
    )

    result = evaluate_decision_quality(tmp_path, artifact_path, strict=True)

    assert result["status"] == "fail"
    assert "decision_quality.forecast_block_reason" in result["required_fields_missing"]
    assert "decision_quality.investor_context_gaps" in result["required_fields_missing"]
    assert "decision_quality.contrary_evidence" in result["required_fields_missing"]


def test_monitoring_lifecycle_error_names_the_two_supported_fields(tmp_path: Path) -> None:
    artifact_path = _write_artifact(
        tmp_path,
        "decision_quality_required: true\n"
        "contrary_evidence: []\n"
        "update_triggers: []\n"
        "invalidation_conditions: []\n"
        "source_trust_notes: []\n"
        "thesis_lifecycle:\n"
        "  state: monitoring\n",
    )

    result = evaluate_decision_quality(tmp_path, artifact_path, strict=True)

    assert "decision_quality.thesis_lifecycle.monitoring_artifact_or_cadence" in result["required_fields_missing"]
    assert (
        "thesis_lifecycle.state=monitoring requires either monitoring_artifact or review_cadence"
        in result["warnings"]
    )


def test_explicit_context_gate_is_not_suppressed_by_producer_role(tmp_path: Path) -> None:
    artifact_path = _write_artifact(
        tmp_path,
        "investor_context_gate_required: true\n",
        role="fundamental-analyst",
    )

    result = evaluate_decision_quality(tmp_path, artifact_path, strict=True)

    assert result["status"] == "fail"
    assert "investor_context_gaps" in result["decision_quality"]["checks"]
    assert "decision_quality.investor_context_gaps" in result["required_fields_missing"]

    complete_path = _write_artifact(
        tmp_path,
        "investor_context_gate_required: true\ninvestor_context_gaps: []\n",
        role="fundamental-analyst",
    )
    complete = evaluate_decision_quality(tmp_path, complete_path, strict=True)
    assert complete["status"] == "pass"


def test_body_keywords_do_not_activate_language_specific_quality_routing(tmp_path: Path) -> None:
    artifact_path = _write_artifact(
        tmp_path,
        body=(
            "[factual] A backtest and signal are mentioned in English. "
            "[inference] Descriptive prose is not a machine quality selector.\n"
        ),
    )

    result = evaluate_decision_quality(tmp_path, artifact_path, strict=True)

    assert result["status"] == "pass"
    assert "anti_overfit" not in result["decision_quality"]["checks"]


def test_explicit_anti_overfit_contract_requires_complete_structured_checks(tmp_path: Path) -> None:
    artifact_path = _write_artifact(
        tmp_path,
        "anti_overfit_required: true\n"
        "anti_overfit_checks:\n"
        "  leakage:\n"
        "    status: pass\n"
        "    reason: Checked timestamps.\n"
        "    evidence_refs: [source-1]\n",
    )

    result = evaluate_decision_quality(tmp_path, artifact_path, strict=True)

    assert result["status"] == "fail"
    assert "decision_quality.anti_overfit_checks.survivorship_bias" in result[
        "required_fields_missing"
    ]


def test_anti_overfit_contract_rejects_schema_drift(tmp_path: Path) -> None:
    checks = {
        key: {
            "status": "not_applicable",
            "reason": "Not applicable to this evidence-only artifact.",
            "evidence_refs": [],
        }
        for key in ANTI_OVERFIT_CHECK_KEYS
    }
    checks["leakage"]["evidence_refs"] = [""]
    checks["leakage"]["unexpected"] = True
    checks["invented_check"] = {
        "status": "pass",
        "reason": "Not part of the contract.",
        "evidence_refs": ["source-1"],
    }
    artifact_path = _write_artifact(
        tmp_path,
        yaml.safe_dump(
            {
                "anti_overfit_required": True,
                "anti_overfit_checks": checks,
            },
            sort_keys=False,
        ),
    )

    result = evaluate_decision_quality(tmp_path, artifact_path, strict=True)

    assert result["status"] == "fail"
    assert "decision_quality.anti_overfit_checks.unexpected_fields" in result[
        "required_fields_missing"
    ]
    assert "decision_quality.anti_overfit_checks.leakage.unexpected_fields" in result[
        "required_fields_missing"
    ]
    assert "decision_quality.anti_overfit_checks.leakage.evidence_refs" in result[
        "required_fields_missing"
    ]


def _v2_request(run_id: str, *, markdown: str = "# Quality contract\n") -> dict[str, object]:
    return {
        "artifact_id": "quality-round-trip",
        "artifact_type": "research_report",
        "universe": "ACME",
        "title": "Quality round trip",
        "markdown": markdown,
        "summary": "Evidence-only quality contract.",
        "status": {
            "handoff": "accepted",
            "evidence_readiness": "decision-grade",
            "action_readiness": "research-only",
            "confidence": "medium",
            "confidence_basis": "Authenticated bounded evidence.",
            "missing_evidence": [],
            "blocked_actions": ["order_execution"],
        },
        "lineage": {
            "workflow_run_id": run_id,
            "knowledge_cutoff": "2026-07-13T00:00:00Z",
            "input_artifact_ids": [],
            "source_snapshot_ids": [],
            "dataset_ids": [],
            "calculation_run_ids": [],
        },
        "requirements": ["forecast", "anti_overfit"],
        "forecast": {
            "posture": "blocked",
            "block_reason": "No calibrated base rate.",
        },
        "anti_overfit": {
            "summary": "No model fitting or historical selection was performed."
        },
    }


def test_mcp_requires_the_v2_anti_overfit_block_and_rejects_v1_fields(tmp_path: Path) -> None:
    ensure_workspace_manifest(tmp_path)
    run_id = "analysis-anti-overfit-v2"
    begin_analysis_run(
        tmp_path,
        "Record a bounded quality fixture.",
        run_id=run_id,
        apply_investor_context=False,
    )
    request = _v2_request(run_id)
    request.pop("anti_overfit")

    with pytest.raises(ValueError, match="anti_overfit requirement requires anti_overfit"):
        call_mcp_tool(
            tmp_path,
            "create_research_artifact",
            request,
            transport_principal="fundamental-analyst",
        )
    with pytest.raises(ValueError, match="does not allow additional properties: anti_overfit_checks"):
        call_mcp_tool(
            tmp_path,
            "create_research_artifact",
            {**_v2_request(run_id), "anti_overfit_checks": {}},
            transport_principal="fundamental-analyst",
        )


def test_explicit_quality_contract_round_trips_through_mcp_and_append(tmp_path: Path) -> None:
    ensure_workspace_manifest(tmp_path)
    run_id = "analysis-quality-round-trip"
    begin_analysis_run(
        tmp_path,
        "Round-trip a v2 quality contract.",
        run_id=run_id,
        apply_investor_context=False,
    )
    request = _v2_request(run_id)
    call_mcp_tool(
        tmp_path,
        "create_research_artifact",
        request,
        transport_principal="fundamental-analyst",
    )

    created = get_research_artifact(
        tmp_path,
        {"artifact_id": "quality-round-trip", "include_markdown": False},
    )
    assert created["requirements"] == ["anti_overfit", "forecast"]
    assert created["forecast"] == request["forecast"]
    assert created["anti_overfit"] == request["anti_overfit"]

    call_mcp_tool(
        tmp_path,
        "append_research_artifact_version",
        {
            **request,
            "markdown": "# Quality round trip\n\nUpdated evidence-only output.\n",
        },
        transport_principal="fundamental-analyst",
    )
    appended = get_research_artifact(
        tmp_path,
        {"artifact_id": "quality-round-trip", "include_markdown": False},
    )
    assert appended["version"] == 2
    for field in ("requirements", "forecast", "anti_overfit"):
        assert appended[field] == created[field]


def test_research_api_schema_serializes_explicit_quality_contract() -> None:
    request = ResearchArtifactRequest(
        artifact_type="research_report",
        universe="ACME",
        title="Explicit quality contract",
        markdown="# Explicit quality contract",
        summary="Explicit compact contract.",
        status={
            "handoff": "accepted",
            "evidence_readiness": "decision-grade",
            "action_readiness": "research-only",
            "confidence": "medium",
            "confidence_basis": "Bounded fixture.",
        },
        lineage={"workflow_run_id": "analysis-api-v2"},
        requirements=["forecast", "anti_overfit"],
        forecast={"posture": "blocked", "block_reason": "No base rate."},
        anti_overfit={"summary": "No model fitting."},
    )

    payload = request.model_dump(exclude_none=True)

    assert payload["requirements"] == ["forecast", "anti_overfit"]
    assert payload["status"]["evidence_readiness"] == "decision-grade"
    assert payload["forecast"]["posture"] == "blocked"
    assert "anti_overfit_checks" not in payload


def test_advisory_quality_records_may_suggest_judgment_review(tmp_path: Path) -> None:
    artifact_path = _write_artifact(
        tmp_path,
        yaml.safe_dump(
            {
                "follow_up_requests": [
                    {
                        "trigger": "contradiction",
                        "suggested_role": "judgment-reviewer",
                        "question": "Independently review why approval and execution remain blocked.",
                        "reason": "The sources disagree; service policy retains authority.",
                        "materiality": "high",
                    }
                ],
                "improvements": [
                    {
                        "improvement_type": "contradiction",
                        "improvement": "Do not submit order; add an independent challenge pass.",
                        "reason": "The conclusion depends on a disputed claim.",
                        "suggested_role": "judgment-reviewer",
                    }
                ],
            },
            sort_keys=False,
        ),
    )

    result = evaluate_decision_quality(tmp_path, artifact_path, strict=True)

    assert result["status"] == "pass"
    assert not any("suggested_role" in warning for warning in result["warnings"])

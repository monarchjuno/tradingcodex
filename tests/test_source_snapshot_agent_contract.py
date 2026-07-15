from __future__ import annotations

import json
from pathlib import Path

import pytest

from tradingcodex_service.api import SourceSnapshotRequest
from tradingcodex_service.application import research as research_module
from tradingcodex_service.application.common import sanitize_id, stable_hash
from tradingcodex_service.application.research import (
    create_research_artifact,
    record_source_snapshot,
    validated_source_snapshot_hashes,
)
from tradingcodex_service.application.runtime import ensure_workspace_manifest
from tradingcodex_service.application.source_snapshots import validate_source_snapshot
from tradingcodex_service.mcp_runtime import (
    TOOL_REGISTRY,
    validate_input_schema,
)


FIXED_NOW = "2026-07-13T03:26:00Z"


def _base_args(**overrides: object) -> dict[str, object]:
    args: dict[str, object] = {
        "provider": "Samsung Electronics Global Newsroom",
        "source_category": "issuer_release",
        "source_locator": "https://example.test/source",
        "coverage_note": "Public read-only source.",
    }
    args.update(overrides)
    return args


def test_source_snapshot_defaults_service_times_and_bounds_derived_id(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    ensure_workspace_manifest(tmp_path)
    monkeypatch.setattr(research_module, "now_iso", lambda: FIXED_NOW)

    result = record_source_snapshot(
        tmp_path,
        _base_args(
            provider=(
                "Samsung Electronics Global Newsroom; "
                "U.S. Bureau of Industry and Security"
            ),
            source_category=(
                "web_and_official_issuer_regulator_disclosures_with_a_very_long_"
                "point_in_time_evidence_category"
            ),
        ),
    )

    snapshot_id = result["snapshot_id"]
    assert len(snapshot_id) <= 128
    assert sanitize_id(snapshot_id) == snapshot_id

    document = json.loads((tmp_path / result["export_path"]).read_text(encoding="utf-8"))
    assert document["snapshot_id"] == snapshot_id
    assert document["known_at"] == FIXED_NOW
    assert document["retrieved_at"] == FIXED_NOW
    assert document["recorded_at"] == FIXED_NOW
    assert document["system_recorded_at"] == FIXED_NOW
    assert result["known_at"] == FIXED_NOW
    assert result["retrieved_at"] == FIXED_NOW
    assert result["recorded_at"] == FIXED_NOW
    assert result["system_recorded_at"] == FIXED_NOW


def test_source_snapshot_rejects_caller_id_and_bad_explicit_times(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(research_module, "now_iso", lambda: FIXED_NOW)

    with pytest.raises(ValueError, match="snapshot_id is derived by the service"):
        record_source_snapshot(tmp_path, _base_args(snapshot_id="caller-id"))
    with pytest.raises(ValueError, match="retrieved_at must include a timezone"):
        record_source_snapshot(tmp_path, _base_args(retrieved_at="2026-07-13"))
    with pytest.raises(ValueError, match="known_at <= retrieved_at <= recorded_at"):
        record_source_snapshot(
            tmp_path,
            _base_args(
                known_at="2026-07-13T03:25:00Z",
                retrieved_at="2026-07-13T03:24:00Z",
            ),
        )
    with pytest.raises(ValueError, match="recorded_at must not be after"):
        record_source_snapshot(
            tmp_path,
            _base_args(recorded_at="2026-07-13T03:27:00Z"),
        )


def test_source_snapshot_id_is_revalidated_from_current_content(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    ensure_workspace_manifest(tmp_path)
    monkeypatch.setattr(research_module, "now_iso", lambda: FIXED_NOW)
    result = record_source_snapshot(
        tmp_path,
        _base_args(payload={"claim": "original"}),
    )
    path = tmp_path / result["export_path"]
    document = json.loads(path.read_text(encoding="utf-8"))

    document["payload"] = {"claim": "rewritten"}
    document["payload_hash"] = stable_hash(document["payload"])
    document["snapshot_hash"] = stable_hash({
        key: value
        for key, value in document.items()
        if key not in {"snapshot_id", "snapshot_hash"}
    })

    with pytest.raises(ValueError, match="snapshot_id does not match its content"):
        validate_source_snapshot(document, expected_snapshot_id=result["snapshot_id"])


def test_source_snapshot_api_tool_and_role_instructions_align() -> None:
    tool = TOOL_REGISTRY["record_source_snapshot"]
    assert "Omit snapshot_id, retrieved_at, and recorded_at" in tool.description
    assert "Provide known_at only" in tool.description
    assert "returns the exact service-owned known_at" in tool.description
    assert tool.input_schema["additionalProperties"] is False
    assert "snapshot_id" not in tool.input_schema["properties"]
    for field in ("retrieved_at", "recorded_at"):
        assert "Service-owned by default" in tool.input_schema["properties"][field][
            "description"
        ]
    assert "genuinely known" in tool.input_schema["properties"]["known_at"][
        "description"
    ]

    with pytest.raises(ValueError, match="does not allow additional properties"):
        validate_input_schema(
            tool,
            {
                "provider": "issuer",
                "source_category": "filing",
                "snapshot_id": "caller-id",
            },
        )

    api_schema = SourceSnapshotRequest.model_json_schema()
    assert "service owns receipt times and snapshot id" in api_schema["description"]
    assert "Service-owned by default" in api_schema["properties"]["retrieved_at"][
        "description"
    ]
    assert "genuinely known" in api_schema["properties"]["known_at"]["description"]
    assert "Service-owned by default" in api_schema["properties"]["recorded_at"][
        "description"
    ]

    root = Path(__file__).resolve().parents[1]
    skill_root = (
        root
        / "workspace_templates/modules/repo-skills/files/.tradingcodex/subagents/skills"
    )
    external_gate = (
        skill_root / "shared/tcx-source-gate/SKILL.md"
    ).read_text(encoding="utf-8")
    collect_evidence = (skill_root / "shared/tcx-evidence/SKILL.md").read_text(
        encoding="utf-8"
    )
    fundamental = (
        skill_root / "fundamental-analyst/tcx-fundamental/SKILL.md"
    ).read_text(encoding="utf-8")
    macro = (skill_root / "macro-analyst/tcx-macro/SKILL.md").read_text(
        encoding="utf-8"
    )
    anti_overfit = (
        skill_root / "shared/tcx-anti-overfit/SKILL.md"
    ).read_text(encoding="utf-8")
    for instructions in (external_gate, collect_evidence):
        for field in ("snapshot_id", "retrieved_at", "recorded_at", "known_at"):
            assert f"`{field}`" in instructions
    assert "omit `known_at` when it is not genuinely known" in external_gate
    assert "do not retry with invented clock times" in external_gate

    artifact_cutoff = TOOL_REGISTRY["create_research_artifact"].input_schema[
        "properties"
    ]["knowledge_cutoff"]["description"]
    assert "explicit timezone" in artifact_cutoff
    assert "maximum service-returned snapshot known_at" in artifact_cutoff
    assert "must not be later than the service receipt time" in artifact_cutoff
    assert "never send a date-only value" in collect_evidence
    assert "Never use end-of-day or another future time" in collect_evidence
    assert "current company-facts or calendar-frame view" in external_gate
    assert "identifier/accession" in fundamental
    assert "first-release, vintage, or real-time-period" in macro
    assert "observed trial count or defensible" in anti_overfit
    assert "Treat a holdout as single-use" in anti_overfit


def test_artifact_cutoff_error_returns_exact_snapshot_known_at(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    ensure_workspace_manifest(tmp_path)
    monkeypatch.setattr(research_module, "now_iso", lambda: FIXED_NOW)
    snapshot = record_source_snapshot(tmp_path, _base_args())
    snapshot_document = json.loads(
        (tmp_path / snapshot["export_path"]).read_text(encoding="utf-8")
    )

    with pytest.raises(
        ValueError,
        match=(
            rf"known_at {FIXED_NOW} is after artifact knowledge_cutoff "
            rf"2026-07-13T03:25:00Z; set knowledge_cutoff at or after {FIXED_NOW}"
        ),
    ):
        validated_source_snapshot_hashes(
            tmp_path,
            [snapshot["snapshot_id"]],
            "2026-07-13T03:25:00Z",
        )

    assert validated_source_snapshot_hashes(
        tmp_path,
        [snapshot["snapshot_id"]],
        FIXED_NOW,
    ) == {snapshot["snapshot_id"]: snapshot_document["snapshot_hash"]}


def test_artifact_cutoff_is_validated_even_without_snapshots_and_required_with_them(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    ensure_workspace_manifest(tmp_path)
    monkeypatch.setattr(research_module, "now_iso", lambda: FIXED_NOW)
    snapshot = record_source_snapshot(tmp_path, _base_args())

    with pytest.raises(ValueError, match="knowledge_cutoff must include a timezone"):
        validated_source_snapshot_hashes(tmp_path, [], "2026-07-13")
    with pytest.raises(
        ValueError,
        match="knowledge_cutoff is required when source_snapshot_ids are supplied",
    ):
        validated_source_snapshot_hashes(tmp_path, [snapshot["snapshot_id"]], "")


def test_research_artifact_rejects_future_knowledge_cutoff(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    ensure_workspace_manifest(tmp_path)
    monkeypatch.setattr(research_module, "now_iso", lambda: FIXED_NOW)

    with pytest.raises(
        ValueError,
        match=(
            r"knowledge_cutoff 2026-07-13T03:27:00Z must not be after service "
            r"recorded_at 2026-07-13T03:26:00Z"
        ),
    ):
        create_research_artifact(
            tmp_path,
            {
                "artifact_id": "future-cutoff",
                "artifact_type": "research_memo",
                "universe": "public_equity",
                "title": "Future cutoff",
                "markdown": "# Future cutoff\n",
                "knowledge_cutoff": "2026-07-13T03:27:00Z",
                "source_snapshot_ids": [],
            },
        )

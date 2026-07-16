from __future__ import annotations

import json
from pathlib import Path

import pytest

from tradingcodex_cli.commands.research import research as research_command
from tradingcodex_service.application import forecasting as forecasting_module
from tradingcodex_service.application.artifact_catalog import (
    ARTIFACT_CATALOG_PATH,
    list_artifact_catalog,
    rebuild_artifact_catalog,
    search_artifact_catalog,
)
from tradingcodex_service.application.research import create_research_artifact
from tradingcodex_service.application.runtime import ensure_workspace_manifest
from tradingcodex_service.mcp_runtime import SAFE_HOME_TOOL_NAMES, TOOL_REGISTRY, call_mcp_tool


@pytest.fixture(autouse=True)
def attached_workspace(tmp_path: Path) -> None:
    ensure_workspace_manifest(tmp_path.resolve())


def _write_mixed_artifacts(root: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, bytes]:
    root = root.resolve()
    create_research_artifact(
        root,
        {
            "artifact_id": "current-research",
            "artifact_type": "research_memo",
            "universe": "public_equity",
            "symbol": "ACME",
            "title": "Current research",
            "knowledge_cutoff": "2026-01-10T00:00:00Z",
            "markdown": "# Current research\n\nDemand recovery supports the current thesis.\n",
        },
    )
    legacy = root / "trading/decisions/legacy-decision.md"
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text(
        "# Legacy decision\n\nDemand recovery was not yet visible.\n",
        encoding="utf-8",
    )
    postmortem = root / "trading/reports/postmortem/review.postmortem_report.json"
    postmortem.parent.mkdir(parents=True, exist_ok=True)
    postmortem.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "artifact_type": "postmortem_report",
                "id": "postmortem-current-research",
                "known_at": "2026-02-01T00:00:00Z",
                "what_happened": "Demand recovered later than expected.",
            }
        ),
        encoding="utf-8",
    )
    malformed = root / "trading/research/specs/malformed.json"
    malformed.parent.mkdir(parents=True, exist_ok=True)
    malformed.write_text("{not-json", encoding="utf-8")
    ledger = root / "trading/forecasts/forecast-ledger.jsonl"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text("placeholder\n", encoding="utf-8")
    monkeypatch.setattr(
        forecasting_module,
        "list_forecasts",
        lambda workspace_root, args=None: {
            "forecasts": [
                {
                    "forecast_id": "forecast-demand",
                    "forecast_target": "Demand recovery occurs before year end.",
                    "artifact_id": "current-research",
                    "workflow_run_id": "run-demand",
                    "knowledge_cutoff": "2025-12-31T00:00:00Z",
                    "event_type": "issued",
                    "recorded_at": "2026-01-01T00:00:00Z",
                }
            ]
        },
    )
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in (legacy, postmortem, malformed, ledger)
    }


def test_v2_catalog_lazily_indexes_new_legacy_and_structured_artifacts_without_rewriting_sources(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = tmp_path.resolve()
    original = _write_mixed_artifacts(root, monkeypatch)

    catalog = list_artifact_catalog(root, {"include_invalid": True})
    by_type = {entry["artifact_type"]: entry for entry in catalog["entries"]}

    assert catalog["coverage"] == {
        "total": 5,
        "full": 3,
        "legacy_partial": 1,
        "invalid": 1,
    }
    assert by_type["research_memo"]["compatibility"] == "full"
    assert by_type["decision_package"]["compatibility"] == "legacy_partial"
    assert by_type["decision_package"]["canonical_id"] is False
    assert by_type["decision_package"]["missing_fields"] == ["decision_id"]
    assert by_type["postmortem_report"]["catalog_id"] == "postmortem-current-research"
    assert by_type["forecast"]["relation_ids"] == ["current-research"]
    assert by_type["invalid_artifact"]["compatibility"] == "invalid"
    assert (root / ARTIFACT_CATALOG_PATH).is_file()
    assert {
        relative: (root / relative).read_bytes()
        for relative in original
    } == original


def test_catalog_search_ranks_content_and_fails_closed_for_missing_or_future_cutoffs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = tmp_path.resolve()
    _write_mixed_artifacts(root, monkeypatch)

    current = search_artifact_catalog(root, {"query": "demand recovery"})
    assert current["entries"][0]["artifact_type"] == "forecast"
    assert {entry["artifact_type"] for entry in current["entries"]} == {
        "forecast",
        "research_memo",
        "postmortem_report",
        "decision_package",
    }
    assert all(entry["compatibility"] != "invalid" for entry in current["entries"])
    historical = search_artifact_catalog(
        root,
        {
            "query": "demand",
            "knowledge_cutoff": "2025-12-31T23:59:59Z",
        },
    )
    assert [entry["catalog_id"] for entry in historical["entries"]] == ["forecast-demand"]
    assert historical["cutoff_excluded_count"] == 3


def test_catalog_reuses_unchanged_projection_then_refreshes_changes_and_removals(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = tmp_path.resolve()
    _write_mixed_artifacts(root, monkeypatch)
    list_artifact_catalog(root)
    index_path = root / ARTIFACT_CATALOG_PATH
    unchanged = index_path.read_bytes()

    list_artifact_catalog(root)
    assert index_path.read_bytes() == unchanged

    chain_heads = root / "trading/forecasts/forecast-chain-heads.json"
    chain_heads.write_text('{"changed":true}\n', encoding="utf-8")
    list_artifact_catalog(root)
    dependency_refreshed = index_path.read_bytes()
    assert dependency_refreshed != unchanged
    list_artifact_catalog(root)
    assert index_path.read_bytes() == dependency_refreshed

    legacy = root / "trading/decisions/legacy-decision.md"
    legacy.write_text("# Legacy decision\n\nA changed margin thesis.\n", encoding="utf-8")
    changed = search_artifact_catalog(root, {"query": "changed margin"})
    assert [entry["artifact_type"] for entry in changed["entries"]] == ["decision_package"]
    assert index_path.read_bytes() != dependency_refreshed

    legacy.unlink()
    refreshed = list_artifact_catalog(root, {"include_invalid": True})
    assert all(entry["path"] != "trading/decisions/legacy-decision.md" for entry in refreshed["entries"])


def test_catalog_cli_and_mcp_expose_parallel_read_and_rebuild_surfaces(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path.resolve()
    _write_mixed_artifacts(root, monkeypatch)

    research_command(root, ["catalog", "search", "demand", "--limit", "2"])
    cli_search = json.loads(capsys.readouterr().out)
    assert len(cli_search["entries"]) == 2
    research_command(root, ["catalog", "rebuild"])
    assert json.loads(capsys.readouterr().out)["status"] == "rebuilt"

    assert {"list_artifact_catalog", "search_artifact_catalog"}.issubset(SAFE_HOME_TOOL_NAMES)
    assert TOOL_REGISTRY["search_artifact_catalog"].risk_level == "read"
    mcp_search = call_mcp_tool(
        root,
        "search_artifact_catalog",
        {"query": "demand", "limit": 1},
        transport_principal="head-manager",
    )
    assert len(mcp_search["entries"]) == 1

    rebuilt = rebuild_artifact_catalog(root)
    assert rebuilt["coverage"]["legacy_partial"] == 1

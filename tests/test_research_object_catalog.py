from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

import tradingcodex_service.application.research_object_catalog as catalog_module
from tradingcodex_service.application.research_object_catalog import (
    RESEARCH_OBJECT_CATALOG_PATH,
    rebuild_research_object_catalog,
    refresh_research_object_catalog,
    search_calculation_objects,
    search_research_objects,
)
from tradingcodex_service.application.runtime import ensure_workspace_manifest


def _write_spec(root: Path, *, title: str = "Demand recovery evidence") -> Path:
    path = root / "trading/research/specs/spec-demand.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "artifact_type": "research_spec",
                "spec_id": "spec-demand",
                "title": title,
                "knowledge_cutoff": "2026-01-01T00:00:00Z",
                "status": "accepted",
            }
        ),
        encoding="utf-8",
    )
    return path


def test_v3_catalog_refreshes_incrementally_and_removes_deleted_records(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    ensure_workspace_manifest(root)
    path = _write_spec(root)

    first = refresh_research_object_catalog(root)
    assert first["status"] == "refreshed"
    assert first["changed_count"] == 1
    assert first["object_count"] == 1
    assert (root / RESEARCH_OBJECT_CATALOG_PATH).is_file()

    unchanged = refresh_research_object_catalog(root)
    assert unchanged["status"] == "current"
    assert unchanged["changed_count"] == 0

    _write_spec(root, title="Changed margin evidence")
    changed = search_research_objects(root, {"query": "Changed margin"})
    assert [item["object_id"] for item in changed["objects"]] == ["spec-demand"]

    path.unlink()
    removed = refresh_research_object_catalog(root)
    assert removed["removed_count"] == 1
    assert removed["object_count"] == 0


def test_v3_catalog_does_not_reproject_unchanged_objects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path.resolve()
    ensure_workspace_manifest(root)
    _write_spec(root)
    refresh_research_object_catalog(root)

    def unexpected_projection(*_args: object, **_kwargs: object) -> dict[str, object]:
        raise AssertionError("unchanged catalog objects must not be reprojected")

    monkeypatch.setattr(catalog_module, "_projection", unexpected_projection)
    current = refresh_research_object_catalog(root)
    assert current["status"] == "current"
    assert current["changed_count"] == 0


def test_v3_catalog_has_structured_cutoff_and_like_fallback(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    ensure_workspace_manifest(root)
    _write_spec(root)
    state = refresh_research_object_catalog(root)

    current = search_research_objects(
        root,
        {"query": "Demand", "object_type": "research_spec", "knowledge_cutoff": "2026-01-02T00:00:00Z"},
    )
    assert [item["object_id"] for item in current["objects"]] == ["spec-demand"]
    future_excluded = search_research_objects(
        root,
        {"query": "Demand", "knowledge_cutoff": "2025-12-31T23:59:59Z"},
    )
    assert future_excluded["objects"] == []

    if state["fts_enabled"]:
        with sqlite3.connect(root / RESEARCH_OBJECT_CATALOG_PATH) as connection:
            connection.execute("DROP TABLE objects_fts")
            connection.execute("UPDATE catalog_meta SET value='0' WHERE key='fts_enabled'")
        fallback = search_research_objects(root, {"query": "Demand recovery"})
        assert fallback["fts_enabled"] is False
        assert [item["object_id"] for item in fallback["objects"]] == ["spec-demand"]


def test_v3_catalog_rebuilds_a_corrupt_derived_database(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    ensure_workspace_manifest(root)
    _write_spec(root)
    path = root / RESEARCH_OBJECT_CATALOG_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"not sqlite")

    rebuilt = refresh_research_object_catalog(root)
    assert rebuilt["rebuilt"] is True
    assert rebuilt["object_count"] == 1
    explicit = rebuild_research_object_catalog(root)
    assert explicit["status"] == "rebuilt"
    assert explicit["object_count"] == 1


def test_v3_catalog_quarantines_a_malformed_special_object(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    ensure_workspace_manifest(root)
    _write_spec(root)
    malformed = root / f"trading/research/datasets/manifests/dataset-{'a' * 24}.json"
    malformed.parent.mkdir(parents=True, exist_ok=True)
    malformed.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "artifact_type": "dataset_manifest",
                "dataset_id": "dataset-" + "a" * 24,
                "title": "Malformed Dataset",
            }
        ),
        encoding="utf-8",
    )

    state = refresh_research_object_catalog(root)
    assert state["invalid_count"] == 1
    result = search_research_objects(root, {"query": "Demand recovery"})
    assert [item["object_id"] for item in result["objects"]] == ["spec-demand"]


def test_v3_catalog_projects_compact_calculation_cards_and_metrics(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    ensure_workspace_manifest(root)
    spec_id = "calc-spec-" + "a" * 20
    run_id = "calc-run-" + "b" * 20
    spec_path = root / f"trading/research/calculations/specs/{spec_id}.json"
    run_path = root / f"trading/research/calculations/runs/{run_id}.json"
    spec_path.parent.mkdir(parents=True, exist_ok=True)
    run_path.parent.mkdir(parents=True, exist_ok=True)
    spec_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "artifact_type": "calculation_spec",
                "calculation_spec_id": spec_id,
                "calculation_type": "fcff_dcf",
                "calculation_version": "1",
                "fingerprint": "c" * 64,
                "knowledge_cutoff": "2026-01-01T00:00:00Z",
                "system_recorded_at": "2026-01-01T00:00:01Z",
            }
        ),
        encoding="utf-8",
    )
    run_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "artifact_type": "calculation_run",
                "calculation_run_id": run_id,
                "calculation_spec_id": spec_id,
                "fingerprint": "c" * 64,
                "workflow_run_id": "analysis-1",
                "status": "succeeded",
                "original_run_id": "",
                "metrics": [
                    {"name": "fair_value", "value": 156.03, "unit": "per_share", "currency": "USD"}
                ],
                "warnings": ["synthetic input"],
                "system_recorded_at": "2026-01-01T00:00:02Z",
            }
        ),
        encoding="utf-8",
    )

    result = search_calculation_objects(
        root,
        {
            "query": "fair_value",
            "calculation_type": "fcff_dcf",
            "status": "succeeded",
            "knowledge_cutoff": "2026-01-01T00:00:00Z",
        },
    )
    assert result["count"] == 1
    card = result["calculations"][0]
    assert card["object_id"] == run_id
    assert card["calculation_type"] == "fcff_dcf"
    assert card["details"]["calculation_version"] == "1"
    assert card["details"]["metrics"] == [
        {"name": "fair_value", "value": 156.03, "unit": "per_share", "currency": "USD"}
    ]
    assert spec_id in card["relation_ids"]


def test_v3_catalog_projects_10k_cards_without_payload_reads_and_skips_unchanged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path.resolve()
    index_path = root / RESEARCH_OBJECT_CATALOG_PATH
    entries: dict[str, dict[str, object]] = {}
    files: dict[str, dict[str, object]] = {}
    for index in range(10_000):
        relative = f"trading/research/load/memo-{index:05d}.md"
        entries[relative] = {
            "record_key": relative,
            "catalog_id": f"memo-{index:05d}",
            "artifact_id": f"memo-{index:05d}",
            "artifact_type": "research_memo",
            "compatibility": "full",
            "path": relative,
            "source_format": "markdown",
            "title": f"Load fixture memo {index:05d}",
            "updated_at": "2026-01-01T00:00:00Z",
            "relation_ids": [],
        }
        files[relative] = {
            "mtime_ns": index + 1,
            "size": 100,
            "file_hash": f"{index:064x}",
            "status": "valid",
            "record_keys": [relative],
            "dependency": {},
        }
    legacy = {"entries": entries, "files": files}
    real_projection = catalog_module._projection
    projection_calls = 0

    def counted_projection(*args: object, **kwargs: object) -> dict[str, object]:
        nonlocal projection_calls
        projection_calls += 1
        return real_projection(*args, **kwargs)

    def forbidden_payload_read(*_args: object, **_kwargs: object) -> dict[str, object]:
        raise AssertionError("generic catalog projection must not open payload files")

    monkeypatch.setattr(catalog_module, "_projection", counted_projection)
    monkeypatch.setattr(catalog_module, "read_regular_json", forbidden_payload_read)

    first = catalog_module._refresh_locked(root, index_path, legacy)
    assert first["changed_count"] == 10_000
    assert first["object_count"] == 10_000
    assert projection_calls == 10_000
    with sqlite3.connect(index_path) as connection:
        assert connection.execute("SELECT count(*) FROM objects").fetchone()[0] == 10_000
        assert connection.execute(
            "SELECT count(*) FROM dataset_columns"
        ).fetchone()[0] == 0

    projection_calls = 0
    second = catalog_module._refresh_locked(root, index_path, legacy)
    assert second["status"] == "current"
    assert second["changed_count"] == 0
    assert projection_calls == 0

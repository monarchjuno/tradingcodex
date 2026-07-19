from __future__ import annotations

from pathlib import Path

import pytest
from django.test import Client

from tradingcodex_cli.generator import bootstrap_workspace
from tradingcodex_service.application import viewer
from tradingcodex_service.application.runtime import workspace_context_payload


def test_viewer_snapshot_has_no_work_execution_state(tmp_path: Path) -> None:
    workspace = tmp_path / "viewer-snapshot"
    bootstrap_workspace(workspace)

    snapshot = viewer.viewer_snapshot(workspace)

    section_names = set(snapshot["sections"])
    assert {"workspace", "skills", "artifacts", "datasets", "calculations"}.issubset(section_names)
    assert section_names.isdisjoint({"runs", "workflow", "work"})
    assert snapshot["sections"]["workspace"]["ok"] is True


def test_native_action_skills_are_visible_without_web_start_contract(tmp_path: Path) -> None:
    workspace = tmp_path / "viewer-native-skills"
    bootstrap_workspace(workspace)

    skills = {item["id"]: item for item in viewer.skill_catalog(workspace)}

    for skill_id in ("tcx-order-allow", "tcx-order-submit", "tcx-order-cancel", "tcx-build"):
        assert skills[skill_id]["user_visible"] is True
        assert skills[skill_id]["available_in_codex"] is True
        assert "startable" not in skills[skill_id]


def test_viewer_skill_detail_is_read_only_projected_guidance(tmp_path: Path) -> None:
    workspace = tmp_path / "viewer-skill-detail"
    bootstrap_workspace(workspace)

    detail = viewer.get_skill_detail(workspace, "tcx-workflow")

    assert detail["id"] == "tcx-workflow"
    assert detail["preview"]["html"]
    assert "startable" not in detail


def test_viewer_routes_are_get_only_and_old_workbench_routes_are_gone(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "viewer-routes"
    bootstrap_workspace(workspace)
    monkeypatch.setenv("TRADINGCODEX_WORKSPACE_ROOT", str(workspace))
    client = Client(REMOTE_ADDR="127.0.0.1", enforce_csrf_checks=True)

    response = client.get("/api/viewer/")
    assert response.status_code == 200
    assert response.json()["sections"]["workspace"]["ok"] is True
    for method in ("POST", "PUT", "DELETE"):
        for path in (
            "/api/viewer/",
            "/api/viewer/skills/tcx-workflow/",
            "/api/viewer/artifacts/example/",
            "/api/viewer/datasets/dataset-example/",
            "/api/viewer/calculations/calc-run-example/",
            "/api/viewer/wiki-pages/",
            "/api/viewer/wiki-pages/local/pages/example.md/",
        ):
            response = client.generic(method, path, data=b"{}", content_type="application/json")
            assert response.status_code == 405
            assert response.headers["Allow"] == "GET"
    assert client.get("/api/workbench/").status_code == 404


def test_wiki_viewer_api_searches_and_reads_sanitized_pages(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "viewer-wiki"
    bootstrap_workspace(workspace)
    page = workspace / "wikis/local/pages/material.md"
    page.write_text(
        "---\ntitle: Example Material\ntype: material\nsummary: Scientific background.\n"
        "aliases: [Material X]\ntags: [science]\nstatus: current\nupdated_at: 2026-07-19\n"
        "sources: [https://example.com/material]\n---\n\n# Example Material\n\n"
        "<script>alert(1)</script> Stable chemistry.\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("TRADINGCODEX_WORKSPACE_ROOT", str(workspace))
    client = Client(REMOTE_ADDR="127.0.0.1")

    response = client.get("/api/viewer/wiki-pages/?wiki=local&q=material&type=material&status=current")
    assert response.status_code == 200
    assert response.json()["pages"][0]["title"] == "Example Material"
    detail = client.get("/api/viewer/wiki-pages/local/pages/material.md/")
    assert detail.status_code == 200
    assert detail.json()["origin"] == "local"
    assert "<script" not in detail.json()["html"]
    assert client.get("/api/viewer/wiki-pages/local/../secret.md/").status_code in {400, 404}


def test_invalid_workspace_renders_in_spa_without_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "viewer-invalid-selection"
    bootstrap_workspace(workspace)
    monkeypatch.setenv("TRADINGCODEX_WORKSPACE_ROOT", str(workspace))
    client = Client(REMOTE_ADDR="127.0.0.1")
    workspace_id = str(workspace_context_payload(workspace)["workspace_id"])

    assert client.get(f"/?workspace={workspace_id}#/library").status_code == 200
    assert client.get("/?workspace=not-registered#/library").status_code == 200
    response = client.get("/api/viewer/?workspace=not-registered")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "invalid_workspace"

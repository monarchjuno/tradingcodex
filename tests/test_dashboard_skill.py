from __future__ import annotations

import json
import tomllib
from pathlib import Path

import yaml

from tradingcodex_cli.generator import bootstrap_workspace
from tradingcodex_service.application.agents import EXPECTED_SUBAGENTS, build_projection_state
from tradingcodex_service.application.viewer import skill_catalog


SKILL_SOURCE = (
    Path(__file__).resolve().parents[1]
    / "workspace_templates/modules/repo-skills/files/.agents/skills/tcx-dashboard"
)


def test_dashboard_skill_metadata_and_read_only_boundary() -> None:
    skill_text = (SKILL_SOURCE / "SKILL.md").read_text(encoding="utf-8")
    metadata = yaml.safe_load((SKILL_SOURCE / "agents/openai.yaml").read_text(encoding="utf-8"))
    module = json.loads((SKILL_SOURCE.parents[3] / "module.json").read_text(encoding="utf-8"))

    assert "name: tcx-dashboard" in skill_text
    assert "$tcx-dashboard" in metadata["interface"]["default_prompt"]
    assert metadata["policy"]["allow_implicit_invocation"] is True
    assert "Do not call `begin_analysis_run`" in skill_text
    assert "Do not draft, approve, submit, cancel, retry, or reconcile an order" in skill_text
    assert "Do not mutate workspace" in skill_text
    assert "Codex in-app browser by default" in skill_text
    assert "external browser only when the user explicitly asks" in skill_text
    assert "Never use shell commands" in skill_text
    assert "skill.workspace.dashboard" in module["provides"]["capabilities"]


def test_dashboard_skill_projects_only_to_head_manager(tmp_path: Path) -> None:
    workspace = tmp_path / "dashboard-skill"
    bootstrap_workspace(workspace)
    state = build_projection_state(workspace)
    root_config = tomllib.loads((workspace / ".codex/config.toml").read_text(encoding="utf-8"))

    dashboard = state["skills"]["tcx-dashboard"]
    assert dashboard["user_visible"] is True
    assert dashboard["installed"] is True
    assert dashboard["owner_roles"] == ["head-manager"]
    assert dashboard["scope"] == "mainagent"
    assert dashboard["implicit_invocation"] is True
    assert "tcx-dashboard" in state["agents"]["head-manager"]["effective_skills"]
    root_paths = {item["path"] for item in root_config["skills"]["config"]}
    assert "../.agents/skills/tcx-dashboard/SKILL.md" in root_paths
    for role in EXPECTED_SUBAGENTS:
        assert "tcx-dashboard" not in state["agents"][role]["effective_skills"]

    generated = workspace / ".agents/skills/tcx-dashboard"
    assert (generated / "SKILL.md").is_file()
    assert (generated / "agents/openai.yaml").is_file()

    catalog = {item["id"]: item for item in skill_catalog(workspace)}
    assert catalog["tcx-dashboard"]["available_in_codex"] is True
    assert catalog["tcx-dashboard"]["user_visible"] is True

    assert root_config["features"]["browser_use"] is True
    assert root_config["features"]["in_app_browser"] is True
    assert root_config["features"]["browser_use_external"] is True
    assert root_config["features"]["browser_use_full_cdp_access"] is False
    assert root_config["features"]["computer_use"] is False

from __future__ import annotations

import json
import tomllib
from pathlib import Path

import yaml

from tradingcodex_cli.generator import bootstrap_workspace
from tradingcodex_service.application.agents import EXPECTED_SUBAGENTS, build_projection_state
from tradingcodex_service.application.viewer import skill_catalog


ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = (
    ROOT
    / "workspace_templates/modules/repo-skills/files/.agents/skills/tcx-brain"
)
HEAD_MANAGER_PROMPT = (
    ROOT
    / "workspace_templates/modules/codex-base/files/.codex/prompts/base_instructions/head-manager.md"
)


def _flat(text: str) -> str:
    return " ".join(text.split())


def test_tcx_brain_covers_private_source_crud_and_managed_lifecycle() -> None:
    skill = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
    reference = (SKILL_ROOT / "references/bundle-contract.md").read_text(encoding="utf-8")
    metadata = yaml.safe_load((SKILL_ROOT / "agents/openai.yaml").read_text(encoding="utf-8"))
    head_manager = HEAD_MANAGER_PROMPT.read_text(encoding="utf-8")
    flat_skill = _flat(skill)

    assert "[bundle-contract.md](references/bundle-contract.md)" in skill
    assert "exact physical first line `$tcx-brain`" in flat_skill
    assert "Do not combine it with `$tcx-build`" in flat_skill
    assert "normal `trading-research` profile" in flat_skill
    assert "Source actions create, inspect, revise, validate, or explicitly delete" in flat_skill
    assert "Plugin actions list, inspect, install, update, activate, deactivate" in flat_skill
    assert "select the exact Decision Memory episodes" in flat_skill
    assert "Require counterexamples and scope limits" in flat_skill
    assert "Do not copy private cases" in flat_skill
    assert "investment-brains/<investment-brain-id>" in skill
    assert ".tradingcodex/investment-brains" in skill
    assert "Stop after any source create, revise, or delete action" in flat_skill
    assert "proof-protected `manage_investment_brain` MCP tool" in flat_skill
    assert "Install always starts inactive" in flat_skill
    for action_snippet in (
        "action=list",
        "action=inspect",
        "action=validate",
        "action=install",
        "action=update",
        "action=activate|deactivate",
        "action=rollback",
        "action=remove",
    ):
        assert action_snippet in flat_skill
    assert "remove retains all installed versions for provenance" in flat_skill
    assert "Do not stage, commit, configure a remote, push, publish" in flat_skill

    assert '"format": "tradingcodex.investment-brain"' in reference
    assert "allow_implicit_invocation: false" in reference
    assert "No install, activation, Git, or publication action occurred during source" in _flat(reference)
    assert "manage_investment_brain action=validate" in _flat(reference)
    assert metadata["policy"]["allow_implicit_invocation"] is False
    assert metadata["interface"]["default_prompt"].startswith("$tcx-brain\n")
    assert "$tcx-build" not in metadata["interface"]["default_prompt"]
    assert "$tcx-brain" in head_manager
    assert "single management entrypoint" in _flat(head_manager)
    assert "$tcx-brain-create" not in head_manager


def test_tcx_brain_projects_only_to_head_manager_without_legacy_alias(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    bootstrap_workspace(workspace)

    state = build_projection_state(workspace)
    skill = state["skills"]["tcx-brain"]
    assert skill["owner_roles"] == ["head-manager"]
    assert skill["scope"] == "mainagent"
    assert skill["user_visible"] is True
    assert skill["implicit_invocation"] is False
    assert "tcx-brain" in state["agents"]["head-manager"]["effective_skills"]
    assert "tcx-brain" in state["agents"]["head-manager"]["projected_skills"]
    assert "tcx-brain-create" not in state["skills"]

    root_config = tomllib.loads((workspace / ".codex/config.toml").read_text(encoding="utf-8"))
    root_paths = {item["path"] for item in root_config["skills"]["config"]}
    assert "../.agents/skills/tcx-brain/SKILL.md" in root_paths
    assert "../.agents/skills/tcx-brain-create/SKILL.md" not in root_paths
    for role in EXPECTED_SUBAGENTS:
        assert "tcx-brain" not in state["agents"][role]["effective_skills"]
        assert "tcx-brain" not in (
            workspace / f".codex/agents/{role}.toml"
        ).read_text(encoding="utf-8")

    generated_skill = workspace / ".agents/skills/tcx-brain"
    assert (generated_skill / "SKILL.md").is_file()
    assert (generated_skill / "agents/openai.yaml").is_file()
    assert (generated_skill / "references/bundle-contract.md").is_file()

    catalog = {item["id"]: item for item in skill_catalog(workspace)}
    assert not (workspace / ".agents/skills/tcx-brain-create/SKILL.md").exists()
    assert catalog["tcx-brain"]["available_in_codex"] is True
    module = json.loads((ROOT / "workspace_templates/modules/repo-skills/module.json").read_text())
    assert "skill.brain_manager" in module["provides"]["capabilities"]

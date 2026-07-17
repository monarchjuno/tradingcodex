from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest
import yaml

from tradingcodex_cli.commands.doctor import _improvement_checks
from tradingcodex_cli.generator import bootstrap_workspace
from tradingcodex_service.application.agents import (
    AGENT_SPECS,
    CALCULATION_DISCIPLINE_ROLES,
    CALCULATION_EXECUTION,
    CORE_SKILL_NAME_PATTERN,
    CORE_EXTENSION_BOUNDARY_END,
    EXPECTED_SUBAGENTS,
    DATASET_WRITE,
    SKILL_SPECS,
    build_projection_state,
    create_or_update_optional_skill,
    create_or_update_strategy_skill,
    inspect_skill_projection,
    project_agent_configuration,
    read_optional_skill_records,
    validate_optional_skill_payload,
    write_agent_additional_instructions,
)


def test_dataset_and_calculation_tool_groups_match_role_authority() -> None:
    head_tools = set(AGENT_SPECS["head-manager"].mcp_allowlist)
    assert {"search_datasets", "get_dataset_manifest", "search_calculations"} <= head_tools
    assert "profile_dataset" not in head_tools
    assert not head_tools.intersection(DATASET_WRITE)
    assert not head_tools.intersection(CALCULATION_EXECUTION)
    assert "get_calculation_run" not in head_tools
    assert "compare_calculation_runs" not in head_tools

    for role in EXPECTED_SUBAGENTS:
        tools = set(AGENT_SPECS[role].mcp_allowlist)
        if role in CALCULATION_DISCIPLINE_ROLES:
            assert {"search_datasets", "get_dataset_manifest", "profile_dataset"} <= tools
            assert set(DATASET_WRITE) <= tools
            assert {"search_calculations", "get_calculation_run", "compare_calculation_runs"} <= tools
            assert set(CALCULATION_EXECUTION) <= tools
        else:
            assert tools.isdisjoint(DATASET_WRITE)
            assert tools.isdisjoint(CALCULATION_EXECUTION)


def _workspace(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    bootstrap_workspace(root)
    return root


def _append_enabled_skill(config_path: Path, skill_path: Path) -> None:
    text = config_path.read_text(encoding="utf-8").rstrip()
    config_path.write_text(
        f'{text}\n\n[[skills.config]]\npath = "{skill_path.as_posix()}"\nenabled = true\n',
        encoding="utf-8",
    )


def test_bundled_skills_use_the_compact_tcx_namespace(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    state = build_projection_state(root)
    bundled = {
        skill_id: record
        for skill_id, record in state["skills"].items()
        if record["layer"] == "bundled_core"
    }

    assert len(SKILL_SPECS) == 33
    assert set(bundled) == set(SKILL_SPECS)
    for skill_id, record in bundled.items():
        assert SKILL_SPECS[skill_id].id == skill_id
        assert CORE_SKILL_NAME_PATTERN.fullmatch(skill_id)
        assert 1 <= len(skill_id.removeprefix("tcx-").split("-")) <= 2
        source = root / record["resolved_source_file"]
        metadata_path = root / record["metadata_file"]
        frontmatter = yaml.safe_load(source.read_text(encoding="utf-8").split("---", 2)[1])
        metadata = yaml.safe_load(metadata_path.read_text(encoding="utf-8"))
        assert source.parent.name == skill_id
        assert frontmatter["name"] == skill_id
        assert metadata["interface"]["display_name"].startswith("TCX ")
        assert f"${skill_id}" in metadata["interface"]["default_prompt"]
    assert all(
        skill_id in SKILL_SPECS
        for agent in state["agents"].values()
        for skill_id in agent["builtin_skills"]
    )
    for role, agent in state["agents"].items():
        if role != "head-manager":
            assert "tcx-artifact" in agent["builtin_skills"]
    for role in (
        "fundamental-analyst",
        "technical-analyst",
        "macro-analyst",
        "valuation-analyst",
        "portfolio-manager",
        "risk-manager",
    ):
        assert "tcx-calculation" in state["agents"][role]["builtin_skills"]
        role_config = tomllib.loads((root / f".codex/agents/{role}.toml").read_text(encoding="utf-8"))
        assert role_config["mcp_servers"]["tradingcodex"]["env"]["TRADINGCODEX_SCRATCH"]
        assert role_config["mcp_servers"]["tradingcodex"]["env"]["TRADINGCODEX_CALCULATION_RUNTIME_ROOT"]
        assert "use the assigned `tcx-calculation` skill" in role_config["developer_instructions"]
    for role in ("head-manager", "news-analyst", "instrument-analyst", "judgment-reviewer"):
        assert "tcx-calculation" not in state["agents"][role]["builtin_skills"]

    calculation = root / state["skills"]["tcx-calculation"]["resolved_source_file"]
    assert (calculation.parent / "references/finance-methods.md").is_file()
    assert (calculation.parent / "references/data-runtime.md").is_file()
    assert "prepare_calculation" in calculation.read_text(encoding="utf-8")

    legacy_ids = {
        "automate-workflow",
        "brain-creator",
        "cancel-submitted-order",
        "decision-memory",
        "execute-approved-order",
        "investor-context",
        "order-allow",
        "plan-workflow",
        "strategy-creator",
        "fundamental-analysis",
        "instrument-analysis",
        "agent-judgment-review",
        "macro-analysis",
        "news-analysis",
        "create-order-ticket",
        "portfolio-review",
        "approve-order",
        "policy-review",
        "review-risk",
        "anti-overfit-validation",
        "collect-evidence",
        "external-data-source-gate",
        "forecasting-discipline",
        "numeric-data-qc",
        "thesis-scenario-tree",
        "technical-analysis",
        "valuation-review",
    }
    projection_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in [
            root / ".codex/config.toml",
            *sorted((root / ".codex/agents").glob("*.toml")),
            root / ".tradingcodex/generated/skill-index.json",
            root / ".tradingcodex/generated/projection-manifest.json",
        ]
    )
    for legacy_id in legacy_ids:
        assert f'"{legacy_id}"' not in projection_text
        assert f"/{legacy_id}/SKILL.md" not in projection_text
        assert f"${legacy_id}" not in projection_text


def test_skill_layers_user_metadata_and_immutable_footer(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    create_or_update_strategy_skill(
        root,
        "strategy-quality-review",
        description="Review durable business quality.",
        body="# Quality Review\n\nPrefer durable evidence.",
        status="active",
        actor="test",
    )
    create_or_update_optional_skill(
        root,
        "fundamental-analyst",
        "contrarian-evidence-review",
        description="Compare contrary evidence before synthesis.",
        body="# Contrarian Evidence Review\n\nCompare buy and sell cases, long and short theses, trading context, and broker analysis.",
        status="active",
        actor="test",
    )
    write_agent_additional_instructions(root, "fundamental-analyst", "Prefer concise evidence notes.", actor="test")
    write_agent_additional_instructions(root, "head-manager", "Prefer concise synthesis notes.", actor="test")

    state = build_projection_state(root)
    required = {"id", "layer", "trust_scope", "implicit_invocation", "resolved_source_file"}
    assert all(required.issubset(skill) for skill in state["skills"].values())
    assert state["skills"]["tcx-fundamental"]["layer"] == "bundled_core"
    assert state["skills"]["tcx-fundamental"]["trust_scope"] == "managed"
    for skill_id, layer in (
        ("strategy-quality-review", "workspace_strategy"),
        ("contrarian-evidence-review", "workspace_optional"),
    ):
        skill = state["skills"][skill_id]
        assert skill["layer"] == layer
        assert skill["trust_scope"] == "user_approved"
        assert skill["implicit_invocation"] is False
        assert not Path(skill["resolved_source_file"]).is_absolute()
        assert (root / skill["resolved_source_file"]).is_file()
        metadata = yaml.safe_load((root / skill["metadata_file"]).read_text(encoding="utf-8"))
        assert metadata["policy"]["allow_implicit_invocation"] is False

    manifest = json.loads((root / ".tradingcodex/generated/projection-manifest.json").read_text(encoding="utf-8"))
    assert manifest["inventory_scope"] == "tradingcodex_managed_workspace"
    assert manifest["runtime_discovery_complete"] is False
    assert manifest["host_global_policy"] == "detect_collisions_do_not_import"
    fundamental_manifest = next(role for role in manifest["roles"] if role["role"] == "fundamental-analyst")
    effective_skill = next(skill for skill in fundamental_manifest["effective_skills"] if skill["id"] == "tcx-fundamental")
    assert required.issubset(effective_skill)
    skill_index = json.loads((root / ".tradingcodex/generated/skill-index.json").read_text(encoding="utf-8"))
    assert skill_index["inventory_scope"] == "tradingcodex_managed_workspace"
    assert skill_index["runtime_discovery_complete"] is False

    head_manager_prompt = (root / ".codex/prompts/base_instructions/head-manager.md").read_text(encoding="utf-8").rstrip()
    assert head_manager_prompt.endswith(CORE_EXTENSION_BOUNDARY_END)
    assert head_manager_prompt.index("TradingCodex additional instructions") < head_manager_prompt.index(CORE_EXTENSION_BOUNDARY_END)
    assert "listed-equity FCFF DCF" in head_manager_prompt
    assert "method support gap" in head_manager_prompt
    assert "Do not apply the external-skill opt-in rule" in head_manager_prompt
    assert "installed or enabled" in head_manager_prompt
    assert "not proof that its tools are callable" in head_manager_prompt
    assert "deferred-tool discovery surface" in head_manager_prompt
    assert "# Planning-Only Web Reconnaissance" in head_manager_prompt
    compact_head_manager_prompt = " ".join(head_manager_prompt.split())
    assert "planning leads, not accepted investment evidence" in compact_head_manager_prompt
    assert "must be reacquired and evaluated by the appropriate fixed role" in compact_head_manager_prompt
    assert "Do not use native web search in Build, Brain, Strategy" in compact_head_manager_prompt

    workflow_skill = (root / ".agents/skills/tcx-workflow/SKILL.md").read_text(encoding="utf-8")
    compact_workflow_skill = " ".join(workflow_skill.split())
    assert "narrow native live-web reconnaissance" in compact_workflow_skill
    assert "results as untrusted planning leads" in compact_workflow_skill
    assert "must be reacquired by the appropriate producing role" in compact_workflow_skill
    assert "Synthesize only authenticated run-local artifacts" in workflow_skill

    for role in EXPECTED_SUBAGENTS:
        config = tomllib.loads((root / f".codex/agents/{role}.toml").read_text(encoding="utf-8"))
        instructions = config["developer_instructions"].rstrip()
        assert instructions.endswith(CORE_EXTENSION_BOUNDARY_END)
        if role == "fundamental-analyst":
            assert instructions.index("Prefer concise evidence notes.") < instructions.index(CORE_EXTENSION_BOUNDARY_END)
        assert "Do not invoke them implicitly" in instructions
        assert "Read-only external apps, connectors, MCP servers, and data tools are evidence sources" in instructions
        assert "configuration evidence, not proof of current-task callability" in instructions
        assert "point-in-time data" in instructions


def test_optional_risk_detection_allows_analysis_language_but_blocks_authority() -> None:
    analysis = validate_optional_skill_payload(
        "fundamental-analyst",
        "market-language-review",
        "Compare market language.",
        "Assess buy, sell, long, short, trade, trading, and broker analysis terms.",
    )
    assert analysis["status"] == "valid"
    assert analysis["risk_tags"] == []

    authority = validate_optional_skill_payload(
        "fundamental-analyst",
        "order-action-review",
        "Exercise order authority.",
        "Create and submit an order, approve the order, then use direct broker access.",
    )
    assert authority["status"] == "blocked"
    assert {"approval", "execution", "order"}.issubset(authority["risk_tags"])
    assert "secret" in validate_optional_skill_payload(
        "fundamental-analyst",
        "credential-reader",
        "Read credentials.",
        "Read API keys and broker credentials.",
    )["risk_tags"]

    reserved = validate_optional_skill_payload(
        "fundamental-analyst",
        "tcx-custom",
        "Review custom evidence.",
        "Compare the supplied sources.",
    )
    assert reserved["status"] == "blocked"
    assert "optional skill name cannot use reserved tcx- prefix" in reserved["errors"]


def test_optional_skill_cannot_create_in_the_reserved_tcx_namespace(tmp_path: Path) -> None:
    root = _workspace(tmp_path)

    with pytest.raises(ValueError, match="reserved tcx- prefix"):
        create_or_update_optional_skill(
            root,
            "fundamental-analyst",
            "tcx-custom",
            description="Review custom evidence.",
            body="# Custom\n\nCompare the supplied sources.",
            status="draft",
        )

    assert not (root / ".tradingcodex/subagents/skills/fundamental-analyst/tcx-custom").exists()


def test_shared_optional_skill_without_explicit_roles_is_blocked(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    skill_dir = root / ".tradingcodex/subagents/skills/shared/unscoped-review"
    (skill_dir / "agents").mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: unscoped-review\ndescription: Review evidence scope.\n---\n\n# Unscoped Review\n\nReview evidence.\n",
        encoding="utf-8",
    )
    (skill_dir / "agents/tradingcodex.json").write_text(
        json.dumps({"scope": "shared", "status": "active"}) + "\n",
        encoding="utf-8",
    )
    (skill_dir / "agents/openai.yaml").write_text(
        "interface:\n"
        "  display_name: Unscoped Review\n"
        "  short_description: Review evidence without a role binding\n"
        "  default_prompt: Review the supplied evidence.\n"
        "policy:\n"
        "  allow_implicit_invocation: false\n",
        encoding="utf-8",
    )

    records = [record for record in read_optional_skill_records(root) if record["id"] == "unscoped-review"]
    assert len(records) == 1
    assert records[0]["validation_status"] == "blocked"
    assert "shared optional skill requires at least one explicit valid role" in records[0]["validation_errors"]
    state = build_projection_state(root)
    assert state["skills"]["unscoped-review"]["owner_roles"] == []
    assert all("unscoped-review" not in state["agents"][role]["effective_skills"] for role in EXPECTED_SUBAGENTS)


def test_host_global_collision_is_detected_but_not_imported(tmp_path: Path, monkeypatch) -> None:
    root = _workspace(tmp_path)
    home = tmp_path / "home"
    codex_home = tmp_path / "codex-home"
    global_skill = home / ".agents/skills/tcx-fundamental/SKILL.md"
    global_skill.parent.mkdir(parents=True)
    global_skill.write_text("# Host override\n\nSENTINEL_HOST_OVERRIDE\n", encoding="utf-8")
    unrelated_global_skill = home / ".agents/skills/host-sentinel-procedure/SKILL.md"
    unrelated_global_skill.parent.mkdir(parents=True)
    unrelated_global_skill.write_text("# Host procedure\n\nSENTINEL_UNRELATED_HOST_SKILL\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("CODEX_HOME", str(codex_home))

    state = project_agent_configuration(root, applied_by="test-host-collision")
    collisions = state["host_global_skill_collisions"]
    assert [(item["id"], item["resolved_source_file"]) for item in collisions] == [
        ("tcx-fundamental", "~/.agents/skills/tcx-fundamental/SKILL.md")
    ]
    assert state["skills"]["tcx-fundamental"]["resolved_source_file"] != str(global_skill.resolve())
    assert "host-sentinel-procedure" not in state["skills"]
    assert "SENTINEL_HOST_OVERRIDE" not in json.dumps(state)
    assert "SENTINEL_UNRELATED_HOST_SKILL" not in json.dumps(state)
    manifest = json.loads((root / ".tradingcodex/generated/projection-manifest.json").read_text(encoding="utf-8"))
    assert manifest["host_global_skill_collisions"] == collisions
    collision_check = next(check for check in _improvement_checks(root) if check["name"] == "host-global skill name collisions")
    assert collision_check["ok"] is False
    assert "~/.agents/skills/tcx-fundamental/SKILL.md" in collision_check["detail"]


def test_doctor_reports_extra_and_unregistered_root_and_role_paths(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    rogue_root = tmp_path / "external/rogue-root/SKILL.md"
    wrong_role = tmp_path / "external/tcx-fundamental/SKILL.md"
    for path in (rogue_root, wrong_role):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# External skill\n", encoding="utf-8")
    _append_enabled_skill(root / ".codex/config.toml", rogue_root)
    _append_enabled_skill(root / ".codex/agents/fundamental-analyst.toml", wrong_role)

    state = build_projection_state(root)
    root_projection = inspect_skill_projection(root, "head-manager", state)
    role_projection = inspect_skill_projection(root, "fundamental-analyst", state)
    assert str(rogue_root.resolve()) in root_projection["extra_paths"]
    assert str(rogue_root.resolve()) in root_projection["unregistered_paths"]
    assert str(wrong_role.resolve()) in role_projection["extra_paths"]
    assert str(wrong_role.resolve()) in role_projection["unregistered_paths"]

    checks = _improvement_checks(root)
    root_check = next(check for check in checks if check["name"] == "head-manager projected skills current")
    role_check = next(check for check in checks if check["name"] == "subagent projected skills current: fundamental-analyst")
    assert root_check["ok"] is False and "extra=" in root_check["detail"] and "unregistered=" in root_check["detail"]
    assert role_check["ok"] is False and "extra=" in role_check["detail"] and "unregistered=" in role_check["detail"]

from __future__ import annotations

import json
from pathlib import Path

from tradingcodex_service.application.build_gateway import BUILD_PROTECTED_MCP_TOOLS
from tradingcodex_service.application.components import get_harness_component


ROOT = Path(__file__).resolve().parents[1]


def test_build_turn_maintenance_map_covers_enforcement_and_protected_tools() -> None:
    component = get_harness_component("build-turn-authorization")

    assert component is not None
    assert {
        "SessionStart",
        "UserPromptSubmit",
        "PreToolUse",
        "PermissionRequest",
        "Stop",
    }.issubset(component["surfaces"]["hooks"])
    assert BUILD_PROTECTED_MCP_TOOLS.issubset(component["surfaces"]["mcp_tools"])
    assert "render_broker_connector_scaffold" in component["surfaces"]["mcp_tools"]
    assert "connect_broker_connector" not in component["surfaces"]["mcp_tools"]
    assert "scaffold_broker_connector" not in component["surfaces"]["mcp_tools"]
    assert "build-hook" in component["surfaces"]["tests"]


def test_repo_skills_module_declares_build_turn_authorization() -> None:
    manifest = json.loads(
        (ROOT / "workspace_templates/modules/repo-skills/module.json").read_text(encoding="utf-8")
    )

    assert "skill.build.turn_authorization" in manifest["provides"]["capabilities"]


def test_mcp_module_declares_render_and_db_only_connector_capabilities() -> None:
    manifest = json.loads(
        (ROOT / "workspace_templates/modules/tradingcodex-mcp/module.json").read_text(encoding="utf-8")
    )
    capabilities = set(manifest["provides"]["capabilities"])

    assert {
        "mcp.tradingcodex.render_broker_connector_scaffold",
        "mcp.tradingcodex.register_broker_connector",
        "mcp.tradingcodex.validate_broker_connector_build",
    }.issubset(capabilities)


def test_generated_root_mcp_exposes_render_not_service_side_scaffold_writes() -> None:
    config = (
        ROOT / "workspace_templates/modules/codex-base/files/.codex/config.toml"
    ).read_text(encoding="utf-8")

    assert '"render_broker_connector_scaffold"' in config
    assert '"connect_broker_connector"' not in config
    assert '"scaffold_broker_connector"' not in config


def test_codex_capability_inventory_component_is_read_only() -> None:
    component = get_harness_component("codex-capability-inventory")
    assert component is not None
    assert component["surfaces"]["mcp_tools"] == ["list_codex_capabilities"]
    assert component["owned_capabilities"] == ["codex.capabilities.inspect"]

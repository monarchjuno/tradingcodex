from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

from tradingcodex_service.application import codex_capabilities


def test_inventory_lists_native_and_plugin_components_without_launch_details(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    standalone = tmp_path / ".agents" / "skills" / "market-helper"
    standalone.mkdir(parents=True)
    (standalone / "SKILL.md").write_text(
        "---\nname: market-helper\ndescription: helper\n---\nDo work.\n",
        encoding="utf-8",
    )
    reserved = tmp_path / ".agents" / "skills" / "tcx-shadow"
    reserved.mkdir(parents=True)
    (reserved / "SKILL.md").write_text("---\nname: tcx-shadow\n---\n", encoding="utf-8")

    plugin = tmp_path / "plugin"
    (plugin / ".codex-plugin").mkdir(parents=True)
    (plugin / "skills" / "plugin-skill").mkdir(parents=True)
    (plugin / "skills" / "plugin-skill" / "SKILL.md").write_text(
        "---\nname: plugin-skill\n---\n",
        encoding="utf-8",
    )
    (plugin / ".mcp.json").write_text(json.dumps({"mcpServers": {"quotes": {"env": {"TOKEN": "secret"}}}}), encoding="utf-8")
    (plugin / ".app.json").write_text(
        json.dumps({"apps": {"market-app": {"token": "secret"}}}),
        encoding="utf-8",
    )
    (plugin / ".hooks.json").write_text(json.dumps({"hooks": {"before-tool": {"command": "secret"}}}), encoding="utf-8")
    (plugin / ".codex-plugin" / "plugin.json").write_text(
        json.dumps(
            {
                "name": "market-plugin",
                "skills": "./skills",
                "mcpServers": "./.mcp.json",
                "apps": "./.app.json",
                "hooks": "./.hooks.json",
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(codex_capabilities.shutil, "which", lambda name: "/usr/bin/codex")

    def fake_run(argv, **kwargs):
        if argv[1:3] == ["mcp", "list"]:
            payload = [
                {"name": "tradingcodex", "enabled": True, "transport": {"env": {"TOKEN": "secret"}}},
                {"name": "quotes", "enabled": False, "transport": {"command": "secret", "env": {"TOKEN": "secret"}}},
            ]
        else:
            payload = {
                "installed": [
                    {
                        "pluginId": "market-plugin@test",
                        "name": "market-plugin",
                        "installed": True,
                        "enabled": True,
                        "source": {"path": str(plugin)},
                    }
                ]
            }
        return SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr="")

    monkeypatch.setattr(codex_capabilities.subprocess, "run", fake_run)
    result = codex_capabilities.list_codex_capabilities(tmp_path)

    assert result["status"] == "complete"
    kinds = {item["kind"] for item in result["capabilities"]}
    assert {"mcp", "skill", "plugin", "app", "hook"}.issubset(kinds)
    assert any(item["id"] == "quotes" and item["availability"] == "disabled" for item in result["capabilities"])
    assert any(item["id"] == "market-plugin@test:mcp:quotes" for item in result["capabilities"])
    assert any(item["id"] == "market-plugin@test:app:market-app" for item in result["capabilities"])
    assert any(item["id"] == "market-plugin@test:hook:before-tool" for item in result["capabilities"])
    assert any(item["id"] == "repo:market-helper" for item in result["capabilities"])
    assert not any("tcx-shadow" in item["id"] for item in result["capabilities"])
    serialized = json.dumps(result)
    assert "TOKEN" not in serialized
    assert "secret" not in serialized
    assert str(plugin) not in serialized


def test_inventory_keeps_duplicate_names_by_scope_and_merges_disabled_skill_state(
    monkeypatch, tmp_path: Path
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("CODEX_HOME", str(home / ".codex"))
    repo_skill = tmp_path / ".agents" / "skills" / "same-name"
    user_skill = home / ".agents" / "skills" / "same-name"
    legacy_user_skill = home / ".codex" / "skills" / "same-name"
    repo_skill.mkdir(parents=True)
    user_skill.mkdir(parents=True)
    legacy_user_skill.mkdir(parents=True)
    (repo_skill / "SKILL.md").write_text("secret body must not be inventoried", encoding="utf-8")
    (user_skill / "SKILL.md").write_text("user secret body", encoding="utf-8")
    (legacy_user_skill / "SKILL.md").write_text("legacy user secret body", encoding="utf-8")
    (tmp_path / ".codex").mkdir()
    (tmp_path / ".codex" / "config.toml").write_text(
        f'[[skills.config]]\npath = "{repo_skill / "SKILL.md"}"\nenabled = false\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(codex_capabilities.shutil, "which", lambda name: "/usr/bin/codex")

    def fake_run(argv, **kwargs):
        payload = [
            {"name": "duplicate", "scope": "user", "enabled": True},
            {"name": "duplicate", "scope": "repo", "enabled": False},
        ] if argv[1] == "mcp" else {"installed": []}
        return SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr="")

    monkeypatch.setattr(codex_capabilities.subprocess, "run", fake_run)
    result = codex_capabilities.list_codex_capabilities(tmp_path)

    duplicates = [item for item in result["capabilities"] if item["id"] == "duplicate"]
    assert {item["scope"] for item in duplicates} == {"repo", "user"}
    skills = [item for item in result["capabilities"] if item["label"] == "same-name"]
    assert {(item["scope"], item["enabled"]) for item in skills} == {
        ("repo", False),
        ("user", True),
        ("user-legacy", True),
    }
    assert "secret body" not in json.dumps(result)


def test_inventory_marks_broken_plugin_manifest_partial_without_reading_components(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    plugin = tmp_path / "broken-plugin"
    (plugin / ".codex-plugin").mkdir(parents=True)
    (plugin / ".codex-plugin" / "plugin.json").write_text("{broken", encoding="utf-8")
    monkeypatch.setattr(codex_capabilities.shutil, "which", lambda name: "/usr/bin/codex")

    def fake_run(argv, **kwargs):
        payload = [] if argv[1] == "mcp" else {
            "installed": [{"pluginId": "broken@test", "enabled": True, "source": {"path": str(plugin)}}]
        }
        return SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr="")

    monkeypatch.setattr(codex_capabilities.subprocess, "run", fake_run)
    result = codex_capabilities.list_codex_capabilities(tmp_path)

    assert result["status"] == "partial"
    assert any(item["kind"] == "plugin" and item["id"] == "broken@test" for item in result["capabilities"])
    assert result["warnings"] == ["Installed plugin metadata is unavailable for broken@test"]


def test_inventory_degrades_without_codex_cli(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(codex_capabilities.shutil, "which", lambda name: None)
    result = codex_capabilities.list_codex_capabilities(tmp_path)
    assert result["status"] == "unavailable"
    assert len(result["warnings"]) == 2


def test_inventory_handles_timeout_without_stderr_leak(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(codex_capabilities.shutil, "which", lambda name: "/usr/bin/codex")

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="codex", timeout=5, stderr="TOKEN=secret")

    monkeypatch.setattr(codex_capabilities.subprocess, "run", timeout)
    result = codex_capabilities.list_codex_capabilities(tmp_path)
    assert result["status"] == "unavailable"
    assert "secret" not in json.dumps(result)


def test_inventory_marks_broken_plugin_component_metadata_partial_without_leaking_details(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    plugin = tmp_path / "plugin"
    (plugin / ".codex-plugin").mkdir(parents=True)
    (plugin / ".codex-plugin" / "plugin.json").write_text(
        json.dumps({"name": "market-plugin", "apps": "./.app.json"}),
        encoding="utf-8",
    )
    (plugin / ".app.json").write_text("{broken TOKEN=secret", encoding="utf-8")
    monkeypatch.setattr(codex_capabilities.shutil, "which", lambda name: "/usr/bin/codex")

    def fake_run(argv, **kwargs):
        payload = [] if argv[1] == "mcp" else {
            "installed": [{"pluginId": "market-plugin@test", "enabled": True, "source": {"path": str(plugin)}}]
        }
        return SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr="")

    monkeypatch.setattr(codex_capabilities.subprocess, "run", fake_run)
    result = codex_capabilities.list_codex_capabilities(tmp_path)

    assert result["status"] == "partial"
    assert result["warnings"] == ["Installed plugin apps metadata is unavailable for market-plugin@test"]
    assert "TOKEN" not in json.dumps(result)
    assert "secret" not in json.dumps(result)

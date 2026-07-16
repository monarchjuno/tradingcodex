from __future__ import annotations

import copy
import json
import hashlib
import importlib.util
import os
import stat
import shutil
import subprocess
import sys
import threading
import tomllib
import venv
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml
from packaging.version import Version

import tradingcodex_cli.generator as generator
from tradingcodex_cli.generator import (
    bootstrap_workspace,
    generated_python_path_is_ephemeral,
    load_module_registry,
    managed_python_runtime_path,
    read_module_lock,
    render_template,
    render_template_modules,
    resolve_package_runner,
    serialized_template_context,
    templates_dir,
    write_rendered_templates,
)
from tradingcodex_cli.package_source import (
    LOCAL_EXECUTABLE_SOURCE_PROVENANCE,
    PACKAGE_SOURCE_KIND_ENV,
    PRIOR_RUNTIME_PYTHON_ENV,
    canonical_executable_source,
    executable_source_is_local,
    validate_executable_source,
)
from tradingcodex_cli.commands.doctor import (
    _codex_mcp_config_checks,
    _required_scratch_permission_paths,
)
from tradingcodex_cli.service_autostart import (
    _detached_process_kwargs,
    _version_mismatch_next_action,
)
from tradingcodex_service.application.common import (
    atomic_write_text,
    exclusive_file_lock,
    safe_workspace_path,
    workspace_launcher_command,
)
from tradingcodex_service.version import TRADINGCODEX_VERSION


ROOT = Path(__file__).resolve().parents[1]


def _next_minor_version() -> str:
    current = Version(TRADINGCODEX_VERSION)
    return f"{current.major}.{current.minor + 1}.0"


def _workspace_launcher_argv(workspace: Path, *args: str) -> list[str]:
    if os.name == "nt":
        command = subprocess.list2cmdline([str(workspace / "tcx.cmd"), *args])
        return [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/s", "/c", command]
    return [str(workspace / "tcx"), *args]


def _copy_package_source_fixture(destination: Path) -> Path:
    destination.mkdir()
    for directory in (
        "tradingcodex_cli",
        "tradingcodex_service",
        "apps",
        "workspace_templates",
    ):
        shutil.copytree(
            ROOT / directory,
            destination / directory,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store"),
        )
    for filename in (
        "pyproject.toml",
        "uv.lock",
        "MANIFEST.in",
        "README.md",
        "LICENSE",
        "NOTICE",
    ):
        source_file = ROOT / filename
        if source_file.is_file():
            shutil.copy2(source_file, destination / filename)
    return destination


def test_attach_target_is_new_only_even_for_dry_run(tmp_path: Path) -> None:
    occupied = tmp_path / "occupied"
    occupied.mkdir()
    marker = occupied / "user-file.txt"
    marker.write_text("keep", encoding="utf-8")

    with pytest.raises(ValueError, match="empty directory"):
        bootstrap_workspace(occupied, dry_run=True)

    assert marker.read_text(encoding="utf-8") == "keep"
    assert not (occupied / ".tradingcodex").exists()

    git_only = tmp_path / "git-only"
    (git_only / ".git").mkdir(parents=True)
    (git_only / ".gitignore").write_text("*.tmp\n", encoding="utf-8")
    bootstrap_workspace(git_only)
    assert (git_only / ".tradingcodex/workspace.json").is_file()


def test_bootstrap_result_uses_one_snake_case_v1_contract(tmp_path: Path) -> None:
    result = bootstrap_workspace(tmp_path / "dry-run", dry_run=True)

    assert set(result) == {
        "target_dir",
        "workspace_id",
        "modules",
        "capabilities",
        "tradingcodex_home",
        "home_source",
        "tradingcodex_db_path",
        "db_source",
    }


def test_dry_run_does_not_create_workspace_scratch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "user-home"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local-app-data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg-cache"))
    monkeypatch.setenv("TRADINGCODEX_HOME", str(tmp_path / "runtime-home"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))

    result = bootstrap_workspace(tmp_path / "dry-run", dry_run=True)

    scratch = generator._workspace_scratch_display_path(result["workspace_id"])
    assert not scratch.exists()
    assert not scratch.parent.exists()


def test_workspace_scratch_directories_are_real_and_private(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "user-home"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local-app-data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg-cache"))
    monkeypatch.setenv("TRADINGCODEX_HOME", str(tmp_path / "runtime-home"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))

    result = bootstrap_workspace(tmp_path / "workspace")

    scratch = generator._workspace_scratch_display_path(result["workspace_id"])
    provider_sources = scratch / "provider-sources"
    assert scratch.is_dir()
    assert not scratch.is_symlink()
    assert provider_sources.is_dir()
    assert not provider_sources.is_symlink()
    if os.name != "nt":
        assert stat.S_IMODE(scratch.stat().st_mode) == 0o700
        assert stat.S_IMODE(provider_sources.stat().st_mode) == 0o700


@pytest.mark.parametrize("unsafe_leaf", ["scratch-root", "scratch", "provider-sources"])
def test_update_rejects_scratch_leaf_symlink_without_touching_victim(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    unsafe_leaf: str,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "user-home"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local-app-data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg-cache"))
    monkeypatch.setenv("TRADINGCODEX_HOME", str(tmp_path / "runtime-home"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    workspace = tmp_path / "workspace"
    result = bootstrap_workspace(workspace)
    scratch = generator._workspace_scratch_display_path(result["workspace_id"])
    unsafe_path = {
        "scratch-root": scratch.parent,
        "scratch": scratch,
        "provider-sources": scratch / "provider-sources",
    }[unsafe_leaf]
    shutil.rmtree(unsafe_path)
    victim = tmp_path / f"{unsafe_leaf}-victim"
    victim.mkdir()
    marker = victim / "user-data.txt"
    marker.write_text("do not touch\n", encoding="utf-8")
    unsafe_path.symlink_to(victim, target_is_directory=True)
    protected = {
        relative: (workspace / relative).read_bytes()
        for relative in (
            ".tradingcodex/generated/module-lock.json",
            ".tradingcodex/workspace.json",
            ".codex/config.toml",
            "tcx",
        )
    }

    expected_error = {
        "scratch-root": "scratch parent",
        "scratch": "scratch path",
        "provider-sources": "provider source staging path",
    }[unsafe_leaf]
    with pytest.raises(ValueError, match=expected_error):
        bootstrap_workspace(workspace, update=True)

    assert marker.read_text(encoding="utf-8") == "do not touch\n"
    assert sorted(path.name for path in victim.iterdir()) == ["user-data.txt"]
    assert {relative: (workspace / relative).read_bytes() for relative in protected} == protected


@pytest.mark.skipif(os.name == "nt", reason="XDG cache paths apply to POSIX platforms")
@pytest.mark.parametrize("symlink_location", ["xdg-cache", "tradingcodex"])
def test_bootstrap_rejects_xdg_cache_ancestor_symlink_without_touching_victim(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    symlink_location: str,
) -> None:
    monkeypatch.setattr(generator.sys, "platform", "linux")
    monkeypatch.setenv("HOME", str(tmp_path / "user-home"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local-app-data"))
    monkeypatch.setenv("TRADINGCODEX_HOME", str(tmp_path / "runtime-home"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    victim = tmp_path / f"{symlink_location}-victim"
    victim.mkdir()
    marker = victim / "user-data.txt"
    marker.write_text("do not touch\n", encoding="utf-8")
    xdg_cache = tmp_path / "xdg-cache"
    if symlink_location == "xdg-cache":
        xdg_cache.symlink_to(victim, target_is_directory=True)
    else:
        xdg_cache.mkdir()
        (xdg_cache / "tradingcodex").symlink_to(victim, target_is_directory=True)
    monkeypatch.setenv("XDG_CACHE_HOME", str(xdg_cache))
    workspace = tmp_path / "workspace"

    with pytest.raises(ValueError, match="scratch ancestors"):
        bootstrap_workspace(workspace)

    assert not workspace.exists()
    assert marker.read_text(encoding="utf-8") == "do not touch\n"
    assert sorted(path.name for path in victim.iterdir()) == ["user-data.txt"]


def test_scratch_ancestor_rejects_mocked_windows_reparse_attribute(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    cache_root = tmp_path / "cache-root"
    cache_root.mkdir()
    scratch_display = cache_root / "tradingcodex/scratch-v1/tcxw_reparse_probe"
    original_lstat = Path.lstat

    def mocked_lstat(path: Path) -> os.stat_result | SimpleNamespace:
        status = original_lstat(path)
        if path == cache_root:
            return SimpleNamespace(
                st_mode=status.st_mode,
                st_file_attributes=generator.WINDOWS_REPARSE_POINT_ATTRIBUTE,
            )
        return status

    monkeypatch.setattr(Path, "lstat", mocked_lstat)

    with pytest.raises(ValueError, match="scratch ancestors"):
        generator._validated_workspace_scratch_location(
            scratch_display,
            protected_paths={},
        )


@pytest.mark.skipif(os.name != "nt", reason="requires a native Windows junction")
def test_native_windows_localappdata_junction_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    victim = tmp_path / "junction-victim"
    victim.mkdir()
    marker = victim / "user-data.txt"
    marker.write_text("do not touch\n", encoding="utf-8")
    junction = tmp_path / "local-app-data-junction"
    command = f'mklink /J "{junction}" "{victim}"'
    result = subprocess.run(
        [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/s", "/c", command],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        pytest.skip("Windows junction creation is unavailable in this environment")

    try:
        assert generator._path_is_link_or_reparse_point(junction)
        monkeypatch.setenv("LOCALAPPDATA", str(junction))
        monkeypatch.setenv("TRADINGCODEX_HOME", str(tmp_path / "runtime-home"))
        monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))

        with pytest.raises(ValueError, match="scratch ancestors"):
            bootstrap_workspace(tmp_path / "workspace", dry_run=True)

        assert marker.read_text(encoding="utf-8") == "do not touch\n"
        assert sorted(path.name for path in victim.iterdir()) == ["user-data.txt"]
    finally:
        os.rmdir(junction)


@pytest.mark.skipif(os.name == "nt", reason="XDG cache paths apply to POSIX platforms")
@pytest.mark.parametrize(
    ("overlap_name", "error_label"),
    [
        ("workspace", "generated workspace"),
        ("runtime", "TRADINGCODEX_HOME"),
        ("codex", "CODEX_HOME"),
    ],
)
def test_dry_run_rejects_scratch_overlap_with_protected_roots(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    overlap_name: str,
    error_label: str,
) -> None:
    monkeypatch.setattr(generator.sys, "platform", "linux")
    monkeypatch.setenv("HOME", str(tmp_path / "user-home"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local-app-data"))
    workspace = tmp_path / "workspace"
    runtime_home = tmp_path / "runtime-home"
    codex_home = tmp_path / "codex-home"
    monkeypatch.setenv("TRADINGCODEX_HOME", str(runtime_home))
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    protected_root = {
        "workspace": workspace,
        "runtime": runtime_home,
        "codex": codex_home,
    }[overlap_name]
    monkeypatch.setenv("XDG_CACHE_HOME", str(protected_root))

    with pytest.raises(ValueError, match=error_label):
        bootstrap_workspace(workspace, dry_run=True)

    assert not workspace.exists()
    assert not runtime_home.exists()
    assert not codex_home.exists()


@pytest.mark.skipif(os.name == "nt", reason="macOS cache aliases apply to POSIX paths")
def test_macos_home_alias_remains_supported_when_scratch_is_separate(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(generator.sys, "platform", "darwin")
    real_home = tmp_path / "real-home"
    real_home.mkdir()
    alias_home = tmp_path / "alias-home"
    alias_home.symlink_to(real_home, target_is_directory=True)
    monkeypatch.setenv("HOME", str(alias_home))
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local-app-data"))
    monkeypatch.setenv("TRADINGCODEX_HOME", str(tmp_path / "runtime-home"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))

    result = bootstrap_workspace(tmp_path / "workspace")

    scratch_display = generator._workspace_scratch_display_path(result["workspace_id"])
    scratch_resolved = scratch_display.resolve(strict=True)
    assert scratch_display != scratch_resolved
    assert scratch_display.is_dir()
    assert scratch_resolved.is_dir()
    config = tomllib.loads(
        (tmp_path / "workspace/.codex/config.toml").read_text(encoding="utf-8")
    )
    research_filesystem = config["permissions"]["trading-research"]["filesystem"]
    assert research_filesystem[str(scratch_display)] == "write"
    assert research_filesystem[str(scratch_resolved)] == "write"
    assert _required_scratch_permission_paths(str(scratch_resolved)) == {
        str(scratch_display),
        str(scratch_resolved),
    }


def test_canonical_private_var_scratch_does_not_imply_synthetic_var_alias(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scratch = Path(
        "/private/var/folders/tradingcodex-canonical/Library/Caches/TradingCodex/"
        "scratch-v1/tcxw_canonical_probe"
    )
    monkeypatch.setattr(
        generator,
        "_workspace_scratch_display_path",
        lambda workspace_id: scratch,
    )

    assert generator.workspace_scratch_permission_aliases(scratch.name, scratch) == ()
    required = _required_scratch_permission_paths(str(scratch))
    assert required == {str(scratch)}
    assert str(scratch).removeprefix("/private") not in required


def test_update_preserves_user_codex_capabilities(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    monkeypatch.setenv("TRADINGCODEX_HOME", str(tmp_path / "home"))
    bootstrap_workspace(workspace)
    config_path = workspace / ".codex/config.toml"
    config_path.write_text(
        config_path.read_text(encoding="utf-8")
        + '\n[mcp_servers.user_quotes]\ncommand = "quotes"\nenabled = true\n'
        + '\n[plugins."market@test"]\nenabled = false\n'
        + '\n[[skills.config]]\npath = "/tmp/user-skill/SKILL.md"\nenabled = false\n',
        encoding="utf-8",
    )
    user_skill = workspace / ".agents/skills/user-market/SKILL.md"
    plugin_state = workspace / ".codex/user-plugins/market/plugin.json"
    user_skill.parent.mkdir(parents=True)
    plugin_state.parent.mkdir(parents=True)
    user_skill.write_text("user-owned skill bytes\n", encoding="utf-8")
    plugin_state.write_text('{"user_owned": true}\n', encoding="utf-8")

    bootstrap_workspace(workspace, update=True)

    config = config_path.read_text(encoding="utf-8")
    assert "# BEGIN User Codex capabilities" in config
    assert "[mcp_servers.user_quotes]" in config
    assert '[plugins."market@test"]' in config
    assert 'path = "/tmp/user-skill/SKILL.md"' in config
    assert "TradingCodex managed Codex MCP" not in config
    assert user_skill.read_text(encoding="utf-8") == "user-owned skill bytes\n"
    assert plugin_state.read_text(encoding="utf-8") == '{"user_owned": true}\n'
    lock = json.loads((workspace / ".tradingcodex/generated/module-lock.json").read_text(encoding="utf-8"))
    assert lock["generated_files"][".codex/config.toml"]["sha256"] == hashlib.sha256(config_path.read_bytes()).hexdigest()


def test_v1_update_respects_workspace_ownership_boundaries(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    monkeypatch.setenv("TRADINGCODEX_HOME", str(tmp_path / "home"))
    bootstrap_workspace(workspace)

    generated_agents = workspace / "AGENTS.md"
    expected_generated_agents = generated_agents.read_bytes()
    generated_agents.write_text("user edit in a generated file\n", encoding="utf-8")

    preserved = {
        ".tradingcodex/agent-instructions/head-manager.md": "Prefer concise user summaries.\n",
        ".tradingcodex/user/customization.json": '{"version": 1, "theme": "user"}\n',
        "investment-brains/investment-brain-user/notes.md": "User-authored Brain source notes.\n",
        "trading/research/user-artifact.md": "Research artifact.\n",
        "trading/decisions/user-decision.md": "Decision artifact.\n",
        "trading/forecasts/user-forecast.json": "{}\n",
        ".tradingcodex/mainagent/runs/run-user/run.json": "{}\n",
        "user-notes.md": "Ordinary user file.\n",
    }
    for relative, body in preserved.items():
        path = workspace / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    preserved_bytes = {
        relative: (workspace / relative).read_bytes()
        for relative in preserved
    }

    bootstrap_workspace(workspace, update=True)

    assert generated_agents.read_bytes() == expected_generated_agents
    for relative, expected in preserved_bytes.items():
        assert (workspace / relative).read_bytes() == expected, relative
    projected_prompt = (
        workspace / ".codex/prompts/base_instructions/head-manager.md"
    ).read_text(encoding="utf-8")
    assert "Prefer concise user summaries." in projected_prompt
    lock = read_module_lock(workspace)
    assert set(preserved).isdisjoint(lock["generated_files"])


def test_v1_update_preflights_retired_generated_file_conflicts(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    bootstrap_workspace(workspace)
    retired_rel = "retired-generated.txt"
    retired = workspace / retired_rel
    retired.write_text("generated\n", encoding="utf-8")
    lock_path = workspace / ".tradingcodex/generated/module-lock.json"
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    lock["generated_files"][retired_rel] = {
        "sha256": hashlib.sha256(retired.read_bytes()).hexdigest(),
        "owner": "template",
    }
    lock_path.write_text(json.dumps(lock, indent=2) + "\n", encoding="utf-8")
    retired.write_text("user modified\n", encoding="utf-8")
    config_path = workspace / ".codex/config.toml"
    config_before = config_path.read_bytes()
    lock_before = lock_path.read_bytes()

    with pytest.raises(ValueError, match="retired generated file was modified"):
        bootstrap_workspace(workspace, update=True)

    assert config_path.read_bytes() == config_before
    assert lock_path.read_bytes() == lock_before
    assert retired.read_text(encoding="utf-8") == "user modified\n"

    retired.write_text("generated\n", encoding="utf-8")
    bootstrap_workspace(workspace, update=True)
    assert not retired.exists()
    updated_lock = json.loads(lock_path.read_text(encoding="utf-8"))
    assert retired_rel not in updated_lock["generated_files"]


def test_v1_update_migrates_legacy_core_skill_paths_without_aliases(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    bootstrap_workspace(workspace)
    migrations = {
        ".agents/skills/plan-workflow/SKILL.md": ".agents/skills/tcx-plan/SKILL.md",
        ".agents/skills/decision-memory/SKILL.md": ".agents/skills/tcx-memory/SKILL.md",
        ".agents/skills/order-allow/SKILL.md": ".agents/skills/tcx-order-allow/SKILL.md",
        ".agents/skills/tcx-brain-create/SKILL.md": ".agents/skills/tcx-brain/SKILL.md",
        ".agents/skills/tcx-brain-create/agents/openai.yaml": ".agents/skills/tcx-brain/agents/openai.yaml",
        ".agents/skills/tcx-brain-create/references/bundle-contract.md": (
            ".agents/skills/tcx-brain/references/bundle-contract.md"
        ),
        ".tradingcodex/subagents/skills/shared/collect-evidence/SKILL.md": (
            ".tradingcodex/subagents/skills/shared/tcx-evidence/SKILL.md"
        ),
    }
    lock_path = workspace / ".tradingcodex/generated/module-lock.json"
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    original_bytes: dict[str, bytes] = {}
    for legacy_rel, current_rel in migrations.items():
        legacy_path = workspace / legacy_rel
        current_path = workspace / current_rel
        legacy_path.parent.mkdir(parents=True, exist_ok=True)
        original_bytes[legacy_rel] = current_path.read_bytes()
        current_path.replace(legacy_path)
        lock["generated_files"][legacy_rel] = lock["generated_files"].pop(current_rel)
    lock_path.write_text(json.dumps(lock, indent=2) + "\n", encoding="utf-8")

    modified_legacy = workspace / ".agents/skills/plan-workflow/SKILL.md"
    modified_legacy.write_bytes(original_bytes[".agents/skills/plan-workflow/SKILL.md"] + b"\nuser edit\n")
    with pytest.raises(ValueError, match="retired generated file was modified"):
        bootstrap_workspace(workspace, update=True)

    modified_legacy.write_bytes(original_bytes[".agents/skills/plan-workflow/SKILL.md"])
    bootstrap_workspace(workspace, update=True)

    for legacy_rel, current_rel in migrations.items():
        assert not (workspace / legacy_rel).is_file()
        assert (workspace / current_rel).is_file()
    updated_lock = json.loads(lock_path.read_text(encoding="utf-8"))
    assert set(migrations).isdisjoint(updated_lock["generated_files"])


@pytest.mark.parametrize(
    "generated_rel,ancestor_rel",
    [
        (".codex/config.toml", ""),
        (".codex/agents/fundamental-analyst.toml", ".codex/agents"),
    ],
)
def test_update_rejects_generated_symlink_or_ancestor_before_any_write(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    generated_rel: str,
    ancestor_rel: str,
) -> None:
    workspace = tmp_path / "workspace"
    monkeypatch.setenv("TRADINGCODEX_HOME", str(tmp_path / "home"))
    bootstrap_workspace(workspace)
    protected = {
        rel: (workspace / rel).read_bytes()
        for rel in (
            ".gitignore",
            ".tradingcodex/generated/module-lock.json",
            ".tradingcodex/workspace.json",
            "tcx",
        )
    }

    if ancestor_rel:
        ancestor = workspace / ancestor_rel
        external = tmp_path / "external-ancestor"
        shutil.copytree(ancestor, external)
        shutil.rmtree(ancestor)
        ancestor.symlink_to(external, target_is_directory=True)
        external_before = {
            path.relative_to(external).as_posix(): path.read_bytes()
            for path in external.rglob("*")
            if path.is_file()
        }
    else:
        destination = workspace / generated_rel
        external_file = tmp_path / "external-user-data.txt"
        external_file.write_text("do not overwrite\n", encoding="utf-8")
        destination.unlink()
        destination.symlink_to(external_file)
        external_before = {"external-user-data.txt": external_file.read_bytes()}

    with pytest.raises(ValueError, match="cannot be a symlink or traverse one"):
        bootstrap_workspace(workspace, update=True)

    assert {rel: (workspace / rel).read_bytes() for rel in protected} == protected
    if ancestor_rel:
        assert {
            path.relative_to(external).as_posix(): path.read_bytes()
            for path in external.rglob("*")
            if path.is_file()
        } == external_before
    else:
        assert external_file.read_bytes() == external_before["external-user-data.txt"]


@pytest.mark.parametrize(
    "unsafe_source",
    [
        "https://user:UniquePassword@example.test/tradingcodex.whl",
        "https://example.test/tradingcodex.whl?X-Amz-Signature=UniqueSignature",
        "tradingcodex @ https://example.test/tradingcodex.whl#UniqueFragment",
        "tradingcodex==1.0.0; token=UniqueInlineToken",
        "--extra-index-url",
        "ftp://example.test/tradingcodex.whl",
        "git://example.test/tradingcodex.git",
        "git+http://example.test/tradingcodex.git",
        "file://example.test/share/tradingcodex.whl",
        "git@example.test:tradingcodex/repository.git",
    ],
)
def test_unsafe_executable_source_is_rejected_before_persistence(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    unsafe_source: str,
) -> None:
    workspace = tmp_path / "workspace"
    monkeypatch.setenv("TRADINGCODEX_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("TRADINGCODEX_MCP_PACKAGE_SPEC", unsafe_source)

    with pytest.raises(ValueError, match="executable source|source URL"):
        bootstrap_workspace(workspace)

    assert not workspace.exists()
    with pytest.raises(ValueError):
        validate_executable_source(unsafe_source)


def test_bare_package_name_never_becomes_local_from_cwd(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    (tmp_path / "tradingcodex").mkdir()
    monkeypatch.chdir(tmp_path)

    assert executable_source_is_local("tradingcodex") is False
    assert resolve_package_runner("tradingcodex") == ("uvx", ("--refresh", "--from"))
    assert executable_source_is_local("./tradingcodex") is True
    assert resolve_package_runner("./tradingcodex") == (
        "uv",
        ("run", "--no-project", "--with-editable"),
    )


def test_native_windows_file_url_is_canonicalized_without_a_rooted_drive_path() -> None:
    assert canonical_executable_source(
        "file:///C:/Program%20Files/TradingCodex/tradingcodex.whl",
        platform_name="nt",
    ) == r"C:\Program Files\TradingCodex\tradingcodex.whl"
    with pytest.raises(ValueError, match="file URL must be local"):
        canonical_executable_source(
            "file://server/share/tradingcodex.whl",
            platform_name="nt",
        )


def test_remote_runtime_key_binds_current_installed_bytes_without_locator_output(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    installed_root = tmp_path / "installed-package"
    package_file = installed_root / "tradingcodex_cli/runtime_marker.py"
    package_file.parent.mkdir(parents=True)
    package_file.write_text("MARKER = 'one'\n", encoding="utf-8")
    monkeypatch.setattr(generator, "repo_root", lambda: installed_root)

    first = generator._managed_python_runtime_key(
        "https://downloads.example.test/tradingcodex.whl"
    )
    package_file.write_text("MARKER = 'two'\n", encoding="utf-8")
    changed_bytes = generator._managed_python_runtime_key(
        "https://downloads.example.test/tradingcodex.whl"
    )
    moved_source = generator._managed_python_runtime_key(
        "https://mirror.example.test/tradingcodex.whl"
    )

    assert len({first, changed_bytes, moved_source}) == 3
    assert "downloads.example.test" not in first
    assert "mirror.example.test" not in moved_source


def test_generated_workspace_serializes_spaces_and_package_metacharacters(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "Workspace With Spaces"
    home = tmp_path / "Application Support" / "Trader's Home"
    package_spec = str(tmp_path / "Wheel Package & Cache" / "tradingcodex-1.0.0-py3-none-any.whl")
    codex_home = tmp_path / "Codex State With Spaces"
    monkeypatch.setenv("TRADINGCODEX_HOME", str(home))
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.delenv("TRADINGCODEX_HOME_SOURCE", raising=False)
    monkeypatch.delenv("TRADINGCODEX_DB_NAME", raising=False)
    monkeypatch.setenv("TRADINGCODEX_MCP_PACKAGE_SPEC", package_spec)
    attached_runtime = tmp_path / "Attached Runtime"
    venv.EnvBuilder(with_pip=False, system_site_packages=True).create(attached_runtime)
    attached_python = attached_runtime / (
        "Scripts/python.exe" if os.name == "nt" else "bin/python"
    )
    site_packages = Path(
        subprocess.run(
            [attached_python, "-c", "import site; print(site.getsitepackages()[0])"],
            text=True,
            capture_output=True,
            check=True,
        ).stdout.strip()
    )
    (site_packages / "tradingcodex-source.pth").write_text(str(ROOT) + "\n", encoding="utf-8")
    monkeypatch.setenv("TRADINGCODEX_PYTHON", str(attached_python))

    bootstrap_workspace(workspace)
    configs = [workspace / ".codex" / "config.toml", *sorted((workspace / ".codex" / "agents").glob("*.toml"))]
    parsed = [tomllib.loads(path.read_text(encoding="utf-8")) for path in configs]
    assert len(parsed) == 10
    root_mcp = parsed[0]["mcp_servers"]["tradingcodex"]
    assert parsed[0]["features"]["hooks"] is True
    assert parsed[0]["features"]["network_proxy"] is True
    assert parsed[0]["default_permissions"] == "trading-research"
    assert "sandbox_mode" not in parsed[0]
    assert all("sandbox_mode" not in config for config in parsed)
    assert parsed[0]["permissions"]["trading-research"]["extends"] == ":workspace"
    assert parsed[0]["permissions"]["trading-build"]["extends"] == ":workspace"
    research_workspace = parsed[0]["permissions"]["trading-research"]["filesystem"][":workspace_roots"]
    assert research_workspace["."] == "write"
    assert research_workspace[".git"] == "read"
    assert research_workspace[".gitignore"] == "read"
    assert research_workspace[".agents"] == "read"
    assert research_workspace["AGENTS.md"] == "read"
    assert research_workspace["tcx"] == "read"
    assert research_workspace["tcx.cmd"] == "read"
    assert research_workspace["trading"] == "read"
    assert research_workspace[".tradingcodex"] == "deny"
    assert ".tradingcodex/cli.py" not in research_workspace
    assert parsed[0]["web_search"] == "disabled"
    assert parsed[0]["permissions"]["trading-build"]["filesystem"][":workspace_roots"][".tradingcodex/cli.py"] == "read"
    assert parsed[0]["permissions"]["trading-build"]["filesystem"][":workspace_roots"][".tradingcodex/workspace.json"] == "read"
    assert parsed[0]["permissions"]["trading-build"]["filesystem"][":workspace_roots"]["."] == "write"
    assert {"manage_strategy", "manage_investment_brain"}.issubset(root_mcp["enabled_tools"])
    scratch = parsed[0]["shell_environment_policy"]["set"]["TRADINGCODEX_SCRATCH"]
    provider_sources = Path(scratch) / "provider-sources"
    assert provider_sources.is_dir()
    assert not provider_sources.is_symlink()
    scratch_display = generator._workspace_scratch_display_path(Path(scratch).name)
    if str(scratch_display) != scratch:
        assert parsed[0]["permissions"]["trading-research"]["filesystem"][str(scratch_display)] == "write"
        assert parsed[0]["permissions"]["trading-build"]["filesystem"][str(scratch_display)] == "write"
    assert parsed[0]["permissions"]["trading-research"]["filesystem"][scratch] == "write"
    assert parsed[0]["permissions"]["trading-research"]["filesystem"][":tmpdir"] == "deny"
    assert parsed[0]["permissions"]["trading-research"]["filesystem"][":slash_tmp"] == "deny"
    assert parsed[0]["permissions"]["trading-build"]["filesystem"][":tmpdir"] == "deny"
    assert parsed[0]["permissions"]["trading-build"]["filesystem"][":slash_tmp"] == "deny"
    assert parsed[0]["shell_environment_policy"]["set"]["TMPDIR"] == scratch
    assert parsed[0]["shell_environment_policy"]["set"]["TEMP"] == scratch
    assert parsed[0]["shell_environment_policy"]["set"]["TMP"] == scratch
    assert parsed[0]["shell_environment_policy"]["set"]["GIT_CONFIG_GLOBAL"] == os.devnull
    assert parsed[0]["shell_environment_policy"]["set"]["GIT_CONFIG_SYSTEM"] == os.devnull
    git_overrides = (
        ("core.hooksPath", os.devnull),
        ("core.fsmonitor", "false"),
        ("core.askPass", ""),
        ("credential.helper", ""),
        ("credential.interactive", "never"),
        ("http.extraHeader", ""),
        ("http.version", "HTTP/1.1"),
        ("http.cookieFile", ""),
        ("http.saveCookies", "false"),
        ("http.followRedirects", "false"),
        ("http.sslVerify", "true"),
        ("protocol.allow", "never"),
        ("protocol.https.allow", "always"),
        ("protocol.http.allow", "never"),
        ("protocol.ssh.allow", "never"),
        ("protocol.git.allow", "never"),
        ("protocol.file.allow", "never"),
        ("protocol.ext.allow", "never"),
    )
    shell_set = parsed[0]["shell_environment_policy"]["set"]
    assert shell_set["GIT_CONFIG_COUNT"] == str(len(git_overrides))
    assert shell_set["GIT_CEILING_DIRECTORIES"] == scratch
    assert shell_set["GIT_PROTOCOL_FROM_USER"] == "0"
    for index, (key, value) in enumerate(git_overrides):
        assert shell_set[f"GIT_CONFIG_KEY_{index}"] == key
        assert shell_set[f"GIT_CONFIG_VALUE_{index}"] == value
    assert parsed[0]["shell_environment_policy"]["set"]["GIT_OPTIONAL_LOCKS"] == "0"
    assert parsed[0]["shell_environment_policy"]["set"]["GIT_PAGER"] == "cat"
    assert parsed[0]["shell_environment_policy"]["set"]["GIT_TERMINAL_PROMPT"] == "0"
    assert {
        "CURL_HOME",
        "WGETRC",
        "GIT_CONFIG_GLOBAL",
        "GIT_CONFIG_SYSTEM",
        "GIT_CONFIG_NOSYSTEM",
        "GIT_CONFIG_COUNT",
        "GIT_CEILING_DIRECTORIES",
        "GIT_PROTOCOL_FROM_USER",
        "GIT_OPTIONAL_LOCKS",
        "GIT_PAGER",
        "GIT_TERMINAL_PROMPT",
        "GCM_INTERACTIVE",
    }.issubset(parsed[0]["shell_environment_policy"]["include_only"])
    assert {
        value
        for index in range(len(git_overrides))
        for value in (f"GIT_CONFIG_KEY_{index}", f"GIT_CONFIG_VALUE_{index}")
    }.issubset(parsed[0]["shell_environment_policy"]["include_only"])
    shell_visible = set(parsed[0]["shell_environment_policy"]["include_only"]) | set(
        parsed[0]["shell_environment_policy"]["set"]
    )
    assert {
        "TRADINGCODEX_HOME",
        "TRADINGCODEX_HOME_SOURCE",
        "TRADINGCODEX_DB_NAME",
        "TRADINGCODEX_PYTHON",
        "TRADINGCODEX_SERVICE_ADDR",
        "TRADINGCODEX_WORKSPACE_ROOT",
    }.isdisjoint(shell_visible)
    assert parsed[0]["permissions"]["trading-research"]["filesystem"]["~/.codex"] == "deny"
    assert parsed[0]["permissions"]["trading-research"]["filesystem"]["~/.codex/proxy"] == "read"
    assert parsed[0]["permissions"]["trading-research"]["filesystem"]["~/.codex/packages/standalone"] == "read"
    assert parsed[0]["permissions"]["trading-research"]["filesystem"][str(codex_home.resolve())] == "deny"
    assert parsed[0]["permissions"]["trading-research"]["filesystem"][str(codex_home.resolve() / "proxy")] == "read"
    assert parsed[0]["permissions"]["trading-research"]["filesystem"][str(codex_home.resolve() / "packages" / "standalone")] == "read"
    assert parsed[0]["permissions"]["trading-research"]["filesystem"]["~/.ssh"] == "deny"
    assert parsed[0]["permissions"]["trading-build"]["filesystem"][str(codex_home.resolve())] == "deny"
    assert parsed[0]["permissions"]["trading-build"]["filesystem"][str(codex_home.resolve() / "proxy")] == "read"
    assert parsed[0]["permissions"]["trading-build"]["filesystem"]["~/.codex/packages/standalone"] == "read"
    build_network = parsed[0]["permissions"]["trading-build"]["network"]
    assert build_network == {
        "enabled": True,
        "mode": "full",
        "allow_local_binding": False,
        "allow_upstream_proxy": False,
        "dangerously_allow_all_unix_sockets": False,
        "domains": {"*": "allow"},
    }
    assert all(
        config["mcp_servers"]["tradingcodex"]["cwd"] == str(workspace.resolve())
        for config in parsed
    )
    assert all(
        config["mcp_servers"]["tradingcodex"]["env"]["TRADINGCODEX_WORKSPACE_ROOT"]
        == str(workspace.resolve())
        for config in parsed
    )
    assert root_mcp["env"]["TRADINGCODEX_HOME"] == str(home.resolve())
    assert root_mcp["env"]["TRADINGCODEX_HOME_SOURCE"] == "environment_override"
    assert root_mcp["env"]["TRADINGCODEX_WORKSPACE_ROOT"] == str(workspace.resolve())
    assert root_mcp["env"]["TRADINGCODEX_SERVICE_ADDR"]
    assert Path(root_mcp["command"]).absolute() == attached_python.absolute()
    assert parsed[0]["permissions"]["trading-research"]["filesystem"][str(attached_python)] == "deny"
    assert parsed[0]["permissions"]["trading-build"]["filesystem"][str(attached_python.parent.parent)] == "read"
    assert parsed[0]["permissions"]["trading-build"]["filesystem"][str(attached_python)] == "read"
    assert root_mcp["args"] == ["-m", "tradingcodex_cli", "mcp", "stdio"]
    assert all(config["mcp_servers"]["tradingcodex"]["required"] is True for config in parsed)
    workspace_config = yaml.safe_load((workspace / ".tradingcodex" / "config.yaml").read_text(encoding="utf-8"))
    assert workspace_config["service"]["default_db"] == str(home.resolve() / "state" / "tradingcodex.sqlite3")
    hooks = json.loads((workspace / ".codex" / "hooks.json").read_text(encoding="utf-8"))
    expected_hook = r".\tcx.cmd __hook session-start" if os.name == "nt" else "./tcx __hook session-start"
    assert hooks["hooks"]["SessionStart"][0]["hooks"][0]["command"] == expected_hook
    assert hooks["hooks"]["PreToolUse"][0]["matcher"] == ".*"
    generated_git = generator.resolve_generated_git_command()
    hook_source = (workspace / ".codex/hooks/tradingcodex_hook.py").read_text(encoding="utf-8")
    assert f"GENERATED_GIT_COMMAND = {json.dumps(generated_git)}" in hook_source
    if sys.platform == "darwin":
        assert generated_git and generated_git != "/usr/bin/git"
    module_lock = json.loads((workspace / ".tradingcodex" / "generated" / "module-lock.json").read_text(encoding="utf-8"))
    assert module_lock["tradingcodex_home"] == str(home.resolve())
    assert module_lock["home_source"] == "environment_override"
    assert (workspace / "tcx").is_file()
    assert (workspace / "tcx.cmd").is_file()
    assert 'if [ "$(pwd -P)" != "$TRADINGCODEX_ROOT" ]; then' in (
        workspace / "tcx"
    ).read_text(encoding="utf-8")
    assert b"\r\n" not in (workspace / "tcx").read_bytes()
    if os.name != "nt":
        assert os.access(workspace / "tcx", os.X_OK)


def test_local_source_mcp_uses_attached_python_without_uv_cache(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    source = Path(__file__).resolve().parents[1]
    runtime_home = tmp_path / "home"
    monkeypatch.setenv("TRADINGCODEX_HOME", str(runtime_home))
    monkeypatch.setenv("TRADINGCODEX_MCP_PACKAGE_SPEC", str(source))
    monkeypatch.delenv("TRADINGCODEX_PYTHON", raising=False)

    bootstrap_workspace(workspace)

    configs = [workspace / ".codex/config.toml", *sorted((workspace / ".codex/agents").glob("*.toml"))]
    expected_python = managed_python_runtime_path(runtime_home, str(source))
    for path in configs:
        mcp = tomllib.loads(path.read_text(encoding="utf-8"))["mcp_servers"]["tradingcodex"]
        assert Path(mcp["command"]).absolute() == expected_python.absolute()
        assert Path(mcp["command"]).is_file()
        assert mcp["args"] == ["-m", "tradingcodex_cli", "mcp", "stdio"]
        assert "TRADINGCODEX_MCP_PACKAGE_SPEC" not in mcp["env"]
        assert "PYTHONPATH" not in mcp["env"]
        assert mcp["env"][PACKAGE_SOURCE_KIND_ENV] == "local-explicit"
    lock = json.loads(
        (workspace / ".tradingcodex/generated/module-lock.json").read_text(encoding="utf-8")
    )
    assert lock["tradingcodex_package_spec"] == LOCAL_EXECUTABLE_SOURCE_PROVENANCE
    cli_text = (workspace / ".tradingcodex/cli.py").read_text(encoding="utf-8")
    assert 'PACKAGE_SPEC = ""' in cli_text
    assert 'PACKAGE_SOURCE_KIND = "local-explicit"' in cli_text
    generated_text = "\n".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in workspace.rglob("*")
        if path.is_file() and not path.is_symlink()
    )
    assert str(source) not in generated_text


def test_local_source_update_carries_prior_runtime_privately(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    source = Path(__file__).resolve().parents[1]
    monkeypatch.setenv("TRADINGCODEX_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("TRADINGCODEX_MCP_PACKAGE_SPEC", str(source))
    monkeypatch.delenv("TRADINGCODEX_PYTHON", raising=False)
    bootstrap_workspace(workspace)
    config = tomllib.loads((workspace / ".codex/config.toml").read_text(encoding="utf-8"))
    prior_python = config["mcp_servers"]["tradingcodex"]["command"]

    generated_cli = workspace / ".tradingcodex/cli.py"
    spec = importlib.util.spec_from_file_location("generated_tcx_update_fixture", generated_cli)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    captured: dict[str, object] = {}

    def capture_execve(executable: str, args: list[str], env: dict[str, str]) -> None:
        captured.update({"executable": executable, "args": args, "env": env})
        raise RuntimeError("captured package-runner reexec")

    monkeypatch.setattr(module.sys, "argv", [str(generated_cli), "update", ".", "--from", str(source)])
    monkeypatch.setattr(module.shutil, "which", lambda _name: str(tmp_path / "uv"))
    monkeypatch.setattr(module.os, "execve", capture_execve)
    with pytest.raises(RuntimeError, match="captured package-runner reexec"):
        module._reexec_package_runner()
    assert "TRADINGCODEX_PYTHON" not in captured["env"]
    assert captured["env"][PRIOR_RUNTIME_PYTHON_ENV] == prior_python

    ephemeral_python = tmp_path / ".cache/uv/builds-v0/.tmpEditable/bin/python"
    monkeypatch.setenv(PRIOR_RUNTIME_PYTHON_ENV, prior_python)
    monkeypatch.setattr(generator.sys, "executable", str(ephemeral_python))
    bootstrap_workspace(workspace, update=True)

    configs = [workspace / ".codex/config.toml", *sorted((workspace / ".codex/agents").glob("*.toml"))]
    for path in configs:
        command = tomllib.loads(path.read_text(encoding="utf-8"))["mcp_servers"]["tradingcodex"]["command"]
        assert command == prior_python
        assert Path(command).is_file()
        assert generated_python_path_is_ephemeral(command) is False
    for path in (workspace / "tcx", workspace / "tcx.cmd", workspace / ".tradingcodex/cli.py"):
        assert str(ephemeral_python) not in path.read_text(encoding="utf-8")


def test_generated_cli_status_and_help_never_refresh_local_source(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    monkeypatch.setenv("TRADINGCODEX_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("TRADINGCODEX_MCP_PACKAGE_SPEC", str(ROOT))
    monkeypatch.delenv("TRADINGCODEX_PYTHON", raising=False)
    bootstrap_workspace(workspace)
    generated_cli = workspace / ".tradingcodex/cli.py"
    spec = importlib.util.spec_from_file_location("generated_tcx_read_only_fixture", generated_cli)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    import tradingcodex_cli.__main__ as cli_main

    calls: list[list[str]] = []
    monkeypatch.delenv("TRADINGCODEX_MCP_PACKAGE_SPEC", raising=False)
    monkeypatch.setenv(PACKAGE_SOURCE_KIND_ENV, "local-explicit")
    monkeypatch.setattr(
        module,
        "_reexec_package_runner",
        lambda: (_ for _ in ()).throw(AssertionError("read-only command reexeced")),
    )
    monkeypatch.setattr(cli_main, "main", lambda: calls.append(list(module.sys.argv[1:])))

    original_cwd = Path.cwd()
    try:
        for argv in (["update", "status", "--json"], ["update", "--help"]):
            monkeypatch.setattr(module.sys, "argv", [str(generated_cli), *argv])
            module._run()
    finally:
        os.chdir(original_cwd)

    assert calls == [["update", "status", "--json"], ["update", "--help"]]


def test_generated_cli_rejects_unsafe_sources_and_missing_runner(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    monkeypatch.setenv("TRADINGCODEX_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("TRADINGCODEX_MCP_PACKAGE_SPEC", str(ROOT))
    monkeypatch.delenv("TRADINGCODEX_PYTHON", raising=False)
    bootstrap_workspace(workspace)
    generated_cli = workspace / ".tradingcodex/cli.py"
    spec = importlib.util.spec_from_file_location("generated_tcx_source_fixture", generated_cli)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    unsafe_sources = (
        "--extra-index-url",
        "ftp://example.test/tradingcodex.whl",
        "git://example.test/tradingcodex.git",
        "git+http://example.test/tradingcodex.git",
        "file://example.test/share/tradingcodex.whl",
        "git@example.test:tradingcodex/repository.git",
    )
    monkeypatch.delenv("TRADINGCODEX_MCP_PACKAGE_SPEC", raising=False)
    for unsafe_source in unsafe_sources:
        monkeypatch.setattr(
            module.sys,
            "argv",
            [str(generated_cli), "update", ".", "--from", unsafe_source],
        )
        with pytest.raises(SystemExit, match="package source"):
            module._declared_package_source()

    protected = {
        path: path.read_bytes()
        for path in (
            workspace / ".codex/config.toml",
            workspace / ".tradingcodex/generated/module-lock.json",
            workspace / "tcx",
            workspace / "tcx.cmd",
        )
    }
    module.PACKAGE_SPEC = "tradingcodex"
    module.PACKAGE_SOURCE_KIND = "persistent"
    monkeypatch.setattr(module.sys, "argv", [str(generated_cli), "update", "."])
    monkeypatch.setattr(module.shutil, "which", lambda _name: None)
    original_cwd = Path.cwd()
    try:
        with pytest.raises(SystemExit, match="requires uv or uvx"):
            module._run()
    finally:
        os.chdir(original_cwd)
    assert {path: path.read_bytes() for path in protected} == protected


def test_runtime_provisioning_failure_precedes_git_and_gitignore_mutation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    gitignore = workspace / ".gitignore"
    gitignore.write_text("user-rule\n", encoding="utf-8")
    before = gitignore.read_bytes()
    monkeypatch.setenv("TRADINGCODEX_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("TRADINGCODEX_MCP_PACKAGE_SPEC", str(ROOT))
    monkeypatch.delenv("TRADINGCODEX_PYTHON", raising=False)
    monkeypatch.setattr(
        generator,
        "ensure_managed_python_runtime",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            ValueError("fixture runtime provisioning failed")
        ),
    )

    with pytest.raises(ValueError, match="fixture runtime provisioning failed"):
        bootstrap_workspace(workspace)

    assert gitignore.read_bytes() == before
    assert not (workspace / ".git").exists()
    assert not (workspace / ".tradingcodex").exists()


def test_local_runtime_snapshot_excludes_stale_build_products(tmp_path: Path) -> None:
    source = tmp_path / "source"
    (source / "apps/harness/migrations").mkdir(parents=True)
    (source / "apps/harness/migrations/0001_v1_initial.py").write_text(
        "INITIAL = True\n",
        encoding="utf-8",
    )
    (source / "build/lib/apps/harness/migrations").mkdir(parents=True)
    (source / "build/lib/apps/harness/migrations/0002_stale.py").write_text(
        "STALE = True\n",
        encoding="utf-8",
    )
    (source / "pyproject.toml").write_text("[build-system]\n", encoding="utf-8")

    snapshot = generator._snapshot_local_runtime_source(source, tmp_path)

    assert (snapshot / "apps/harness/migrations/0001_v1_initial.py").is_file()
    assert not (snapshot / "build").exists()
    assert not any(path.name == "0002_stale.py" for path in snapshot.rglob("*.py"))


def test_missing_uv_fails_before_workspace_mutation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    monkeypatch.setenv("TRADINGCODEX_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("TRADINGCODEX_MCP_PACKAGE_SPEC", str(ROOT))
    monkeypatch.delenv("TRADINGCODEX_PYTHON", raising=False)
    monkeypatch.setattr(generator.shutil, "which", lambda _name: None)

    with pytest.raises(ValueError, match="uv is required"):
        bootstrap_workspace(workspace)

    assert not workspace.exists()


def test_service_version_mismatch_never_exposes_explicit_local_source(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    local_source = tmp_path / "Private TradingCodex Source"
    local_source.mkdir()
    monkeypatch.setenv("TRADINGCODEX_MCP_PACKAGE_SPEC", str(local_source))
    monkeypatch.setenv(PACKAGE_SOURCE_KIND_ENV, "local-explicit")

    action = _version_mismatch_next_action(_next_minor_version(), "127.0.0.1:48267")

    assert str(local_source) not in action
    assert "<package-spec>" in action


def test_real_custom_source_attach_patch_update_and_same_version_refresh(
    tmp_path: Path,
) -> None:
    uvx = shutil.which("uvx")
    if not uvx:
        pytest.skip("uvx is required for the real custom-source update smoke")
    source = _copy_package_source_fixture(tmp_path / "custom-source")
    workspace = tmp_path / "workspace"
    environment = {
        key: value
        for key, value in os.environ.items()
        if key
        not in {
            "PYTHONPATH",
            "TRADINGCODEX_MCP_PACKAGE_SPEC",
            "TRADINGCODEX_PYTHON",
            PACKAGE_SOURCE_KIND_ENV,
            PRIOR_RUNTIME_PYTHON_ENV,
        }
    }
    environment["TRADINGCODEX_HOME"] = str(tmp_path / "home")
    environment["TRADINGCODEX_DB_NAME"] = str(tmp_path / "home/state/runtime.sqlite3")
    missing_provenance_workspace = tmp_path / "missing-provenance-workspace"
    missing_provenance = subprocess.run(
        [
            uvx,
            "--refresh",
            "--from",
            str(source),
            "tcx",
            "attach",
            str(missing_provenance_workspace),
        ],
        cwd=tmp_path,
        env=environment,
        text=True,
        capture_output=True,
        timeout=600,
    )
    assert missing_provenance.returncode == 1
    assert "package source provenance is required" in missing_provenance.stderr
    assert not missing_provenance_workspace.exists()
    attach = subprocess.run(
        [
            uvx,
            "--refresh",
            "--from",
            str(source),
            "tcx",
            "attach",
            str(workspace),
            "--from",
            str(source),
        ],
        cwd=tmp_path,
        env=environment,
        text=True,
        capture_output=True,
        timeout=600,
        check=True,
    )
    assert "workspace attached" in attach.stdout
    first_config = tomllib.loads((workspace / ".codex/config.toml").read_text(encoding="utf-8"))
    first_python = Path(first_config["mcp_servers"]["tradingcodex"]["command"])
    assert first_python.is_file()

    version_path = source / "tradingcodex_service/version.py"
    next_version = _next_minor_version()
    version_path.write_text(
        f'TRADINGCODEX_VERSION = "{next_version}"\n',
        encoding="utf-8",
    )
    before_explicit_override = {
        path: path.read_bytes()
        for path in (
            workspace / ".codex/config.toml",
            workspace / ".tradingcodex/generated/module-lock.json",
            workspace / "tcx",
            workspace / "tcx.cmd",
        )
    }
    explicit_override_environment = {
        **environment,
        "TRADINGCODEX_PYTHON": str(first_python),
    }
    rejected_override = subprocess.run(
        _workspace_launcher_argv(
            workspace,
            "update",
            ".",
            "--from",
            str(source),
            "--no-doctor",
        ),
        cwd=workspace,
        env=explicit_override_environment,
        text=True,
        capture_output=True,
        timeout=600,
    )
    assert rejected_override.returncode == 1
    assert "different TradingCodex version" in rejected_override.stderr
    assert {path: path.read_bytes() for path in before_explicit_override} == before_explicit_override
    subprocess.run(
        _workspace_launcher_argv(
            workspace,
            "update",
            ".",
            "--from",
            str(source),
            "--no-doctor",
        ),
        cwd=workspace,
        env=environment,
        text=True,
        capture_output=True,
        timeout=600,
        check=True,
    )
    second_config = tomllib.loads((workspace / ".codex/config.toml").read_text(encoding="utf-8"))
    second_python = Path(second_config["mcp_servers"]["tradingcodex"]["command"])
    assert second_python != first_python
    assert second_python.is_file()
    lock = json.loads(
        (workspace / ".tradingcodex/generated/module-lock.json").read_text(encoding="utf-8")
    )
    assert lock["tradingcodex_version"] == next_version
    assert lock["tradingcodex_package_spec"] == LOCAL_EXECUTABLE_SOURCE_PROVENANCE

    main_path = source / "tradingcodex_cli/__main__.py"
    main_text = main_path.read_text(encoding="utf-8")
    marker = "same-version-source-refresh"
    main_path.write_text(
        main_text.replace(
            "print(TRADINGCODEX_VERSION)",
            f'print("{marker}:" + TRADINGCODEX_VERSION)',
            1,
        ),
        encoding="utf-8",
    )
    subprocess.run(
        _workspace_launcher_argv(
            workspace,
            "update",
            ".",
            "--from",
            str(source),
            "--no-doctor",
        ),
        cwd=workspace,
        env=environment,
        text=True,
        capture_output=True,
        timeout=600,
        check=True,
    )
    third_config = tomllib.loads((workspace / ".codex/config.toml").read_text(encoding="utf-8"))
    third_python = Path(third_config["mcp_servers"]["tradingcodex"]["command"])
    assert third_python not in {first_python, second_python}
    assert third_python.is_file()
    version = subprocess.run(
        _workspace_launcher_argv(workspace, "--version"),
        cwd=workspace,
        env=environment,
        text=True,
        capture_output=True,
        timeout=60,
        check=True,
    )
    assert version.stdout.strip() == f"{marker}:{next_version}"

    request = '{"jsonrpc":"2.0","id":1,"method":"tools/list"}\n'
    config_paths = [
        workspace / ".codex/config.toml",
        *sorted((workspace / ".codex/agents").glob("*.toml")),
    ]
    assert len(config_paths) == 10
    for config_path in config_paths:
        mcp = tomllib.loads(config_path.read_text(encoding="utf-8"))["mcp_servers"][
            "tradingcodex"
        ]
        projected_python = Path(mcp["command"])
        assert projected_python == third_python
        assert projected_python.is_file()
        mcp_environment = {**environment, **mcp["env"]}
        mcp_environment["TRADINGCODEX_MCP_AUTOSTART_SERVICE"] = "0"
        response = subprocess.run(
            [mcp["command"], *mcp["args"]],
            cwd=mcp["cwd"],
            env=mcp_environment,
            input=request,
            text=True,
            capture_output=True,
            timeout=60,
            check=True,
        )
        assert json.loads(response.stdout)["result"]["tools"]

    generated_text = "\n".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in workspace.rglob("*")
        if path.is_file() and not path.is_symlink()
    )
    assert str(source) not in generated_text


def test_uv_cache_removal_leaves_launchers_and_all_role_mcp_runnable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    runtime_home = tmp_path / "home"
    disposable_python = tmp_path / "uv-cache/archive-v0/build/bin/python"
    stable_python = sys.executable
    monkeypatch.setenv("TRADINGCODEX_HOME", str(runtime_home))
    monkeypatch.setenv("TRADINGCODEX_MCP_PACKAGE_SPEC", str(ROOT))
    monkeypatch.delenv("TRADINGCODEX_PYTHON", raising=False)

    managed_python = managed_python_runtime_path(runtime_home, str(ROOT))
    monkeypatch.setattr(generator.sys, "executable", str(disposable_python))
    bootstrap_workspace(workspace)
    monkeypatch.setattr(generator.sys, "executable", stable_python)
    assert not disposable_python.exists()

    configs = [workspace / ".codex/config.toml", *sorted((workspace / ".codex/agents").glob("*.toml"))]
    assert len(configs) == 10
    parsed = [tomllib.loads(path.read_text(encoding="utf-8")) for path in configs]
    for config in parsed:
        mcp = config["mcp_servers"]["tradingcodex"]
        assert Path(mcp["command"]).absolute() == managed_python.absolute()
        assert Path(mcp["command"]).is_file()
        assert generated_python_path_is_ephemeral(mcp["command"]) is False

    version = subprocess.run(
        _workspace_launcher_argv(workspace, "--version"),
        cwd=tmp_path,
        env={key: value for key, value in os.environ.items() if key != "TRADINGCODEX_PYTHON"},
        text=True,
        capture_output=True,
        timeout=30,
        check=True,
    )
    assert version.stdout.strip() == TRADINGCODEX_VERSION

    request = '{"jsonrpc":"2.0","id":1,"method":"tools/list"}\n'
    for config in parsed:
        mcp = config["mcp_servers"]["tradingcodex"]
        environment = {**os.environ, **{str(key): str(value) for key, value in mcp["env"].items()}}
        environment.pop("TRADINGCODEX_PYTHON", None)
        environment["TRADINGCODEX_MCP_AUTOSTART_SERVICE"] = "0"
        response = subprocess.run(
            [mcp["command"], *mcp["args"]],
            cwd=mcp["cwd"],
            env=environment,
            input=request,
            text=True,
            capture_output=True,
            timeout=30,
            check=True,
        )
        assert json.loads(response.stdout)["result"]["tools"]


def test_update_rejects_missing_or_ephemeral_generated_python_before_render(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    monkeypatch.setenv("TRADINGCODEX_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("TRADINGCODEX_PYTHON", raising=False)
    bootstrap_workspace(workspace)
    protected_paths = (
        workspace / ".codex/config.toml",
        workspace / ".tradingcodex/generated/module-lock.json",
        workspace / "tcx",
        workspace / "tcx.cmd",
    )
    before = {path: path.read_bytes() for path in protected_paths}

    invalid_cases = (
        (tmp_path / "missing/python", "does not exist or is not a file"),
        (
            tmp_path / ".cache/uv/builds-v0/.tmpDisposable/bin/python",
            "cannot persist a uv cache or ephemeral package-runner Python",
        ),
        (
            tmp_path / ".tmpNoCache/archive-v0/environment/bin/python",
            "cannot persist a uv cache or ephemeral package-runner Python",
        ),
        (
            tmp_path / ".cache/uv/archive-v0/cached/bin/python",
            "cannot persist a uv cache or ephemeral package-runner Python",
        ),
    )
    for invalid_python, message in invalid_cases:
        monkeypatch.setenv("TRADINGCODEX_PYTHON", str(invalid_python))
        with pytest.raises(ValueError, match=message):
            bootstrap_workspace(workspace, update=True)
        assert {path: path.read_bytes() for path in protected_paths} == before

    monkeypatch.delenv("TRADINGCODEX_PYTHON", raising=False)
    monkeypatch.setenv(PRIOR_RUNTIME_PYTHON_ENV, str(tmp_path / "missing-prior/python"))
    with pytest.raises(ValueError, match="stable Python 3.11 through 3.14"):
        bootstrap_workspace(workspace, update=True)
    assert {path: path.read_bytes() for path in protected_paths} == before
    monkeypatch.delenv(PRIOR_RUNTIME_PYTHON_ENV, raising=False)

    bare_environment = tmp_path / "bare-python"
    venv.EnvBuilder(with_pip=False).create(bare_environment)
    bare_python = bare_environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    monkeypatch.setenv("TRADINGCODEX_MCP_PACKAGE_SPEC", str(ROOT))
    monkeypatch.setenv("TRADINGCODEX_PYTHON", str(bare_python))
    with pytest.raises(ValueError, match="cannot import the TradingCodex MCP runtime"):
        bootstrap_workspace(workspace, update=True)
    assert {path: path.read_bytes() for path in protected_paths} == before


def test_doctor_rejects_ephemeral_or_missing_python_after_update(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    monkeypatch.setenv("TRADINGCODEX_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("TRADINGCODEX_PYTHON", raising=False)
    bootstrap_workspace(workspace)
    bootstrap_workspace(workspace, update=True)
    config_path = workspace / ".codex/config.toml"
    original = config_path.read_text(encoding="utf-8")
    stable_python = tomllib.loads(original)["mcp_servers"]["tradingcodex"]["command"]
    command_literal = f"command = {json.dumps(stable_python)}"
    assert command_literal in original

    ephemeral = tmp_path / ".cache/uv/builds-v0/.tmpDoctor/bin/python"
    ephemeral.parent.mkdir(parents=True)
    ephemeral.write_text("disposable", encoding="utf-8")
    for invalid in (ephemeral, tmp_path / "missing/python"):
        config_path.write_text(
            original.replace(command_literal, f"command = {json.dumps(str(invalid))}", 1),
            encoding="utf-8",
        )
        binding = next(
            check
            for check in _codex_mcp_config_checks(workspace)
            if check["name"] == "TradingCodex MCP uses attached Python runtime"
        )
        assert binding["ok"] is False
        assert "head-manager" in binding["detail"]


def test_doctor_rejects_relative_mcp_workspace_binding(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    monkeypatch.setenv("TRADINGCODEX_HOME", str(tmp_path / "home"))
    bootstrap_workspace(workspace)
    config_path = workspace / ".codex" / "config.toml"
    text = config_path.read_text(encoding="utf-8")
    absolute_cwd = f"cwd = {json.dumps(str(workspace.resolve()))}"
    assert absolute_cwd in text
    config_path.write_text(text.replace(absolute_cwd, 'cwd = "."', 1), encoding="utf-8")

    binding = next(
        check
        for check in _codex_mcp_config_checks(workspace)
        if check["name"] == "TradingCodex MCP workspace binding configured"
    )

    assert binding["ok"] is False
    assert "head-manager" in binding["detail"]


def test_doctor_requires_mcp_initialization_for_every_fixed_role(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    monkeypatch.setenv("TRADINGCODEX_HOME", str(tmp_path / "home"))
    bootstrap_workspace(workspace)
    role_path = workspace / ".codex/agents/fundamental-analyst.toml"
    role_config = role_path.read_text(encoding="utf-8")
    assert "required = true" in role_config
    role_path.write_text(role_config.replace("required = true\n", "", 1), encoding="utf-8")

    required = next(
        check
        for check in _codex_mcp_config_checks(workspace)
        if check["name"] == "TradingCodex MCP initialization is required for every role"
    )

    assert required["ok"] is False
    assert "root and fixed-role configs" in required["detail"]


def test_windows_drive_paths_render_as_valid_toml_yaml_json(tmp_path: Path) -> None:
    raw = {
        "PROJECT_NAME": "portable-test",
        "WORKSPACE_ID": "tcxw_portable",
        "GENERATED_AT": "2026-01-01T00:00:00Z",
        "TRADINGCODEX_VERSION": "1.0.0",
        "TRADINGCODEX_MCP_PACKAGE_SPEC": "tradingcodex==1.0.0",
        "TRADINGCODEX_PACKAGE_SOURCE_KIND": "persistent",
        "TRADINGCODEX_PYTHON": r"C:\Users\Ada Lovelace\AppData\Local\TradingCodex\runtime\python\tcx\Scripts\python.exe",
        "TRADINGCODEX_WORKSPACE_ROOT": r"C:\Workspaces\Trading Codex",
        "TRADINGCODEX_SCRATCH_PATH": r"C:\Users\Ada Lovelace\AppData\Local\Temp\tradingcodex-scratch-v1\tcxw_portable",
        "TRADINGCODEX_HOME": r"C:\Users\Ada Lovelace\AppData\Local\TradingCodex",
        "TRADINGCODEX_HOME_SOURCE": "platform_default",
        "TRADINGCODEX_DB_PATH": r"C:\Users\Ada Lovelace\AppData\Local\TradingCodex\state\tradingcodex.sqlite3",
        "TRADINGCODEX_DB_SOURCE": "home_default",
        "CODEX_HOME_PATH": r"C:\Users\Ada Lovelace\.codex",
        "CODEX_HOME_PROXY_PATH": r"C:\Users\Ada Lovelace\.codex\proxy",
        "CODEX_HOME_STANDALONE_PATH": r"C:\Users\Ada Lovelace\.codex\packages\standalone",
        "TRADINGCODEX_SERVICE_ADDR": "127.0.0.1:48267",
        "TRADINGCODEX_NULL_DEVICE": "NUL",
        "TRADINGCODEX_HOOK_COMMAND": r".\tcx.cmd __hook",
        "TRADINGCODEX_WORKSPACE_LAUNCHER": r".\tcx.cmd",
    }
    context = serialized_template_context(raw)
    registry = load_module_registry(templates_dir())
    modules = [registry[module_id] for module_id in ("codex-base", "fixed-subagents", "repo-skills")]
    write_rendered_templates(tmp_path, render_template_modules(modules, context))
    configs = [tmp_path / ".codex" / "config.toml", *sorted((tmp_path / ".codex" / "agents").glob("*.toml"))]
    parsed = [tomllib.loads(path.read_text(encoding="utf-8")) for path in configs]
    assert parsed[0]["mcp_servers"]["tradingcodex"]["env"]["TRADINGCODEX_HOME"] == raw["TRADINGCODEX_HOME"]
    assert all(config["mcp_servers"]["tradingcodex"]["cwd"] == raw["TRADINGCODEX_WORKSPACE_ROOT"] for config in parsed)
    assert all(
        config["mcp_servers"]["tradingcodex"]["env"]["TRADINGCODEX_WORKSPACE_ROOT"]
        == raw["TRADINGCODEX_WORKSPACE_ROOT"]
        for config in parsed
    )
    assert yaml.safe_load((tmp_path / ".tradingcodex" / "config.yaml").read_text(encoding="utf-8"))["service"]["default_db"] == raw["TRADINGCODEX_DB_PATH"]
    assert json.loads((tmp_path / ".codex" / "hooks.json").read_text(encoding="utf-8"))["hooks"]["Stop"][0]["hooks"][0]["command"] == r".\tcx.cmd __hook stop"
    rendered_agent_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in [
            tmp_path / ".codex" / "prompts" / "base_instructions" / "head-manager.md",
            *sorted((tmp_path / ".agents" / "skills").glob("*/SKILL.md")),
        ]
    )
    assert r".\tcx.cmd" in rendered_agent_text
    assert "./tcx" not in rendered_agent_text
    hook_text = (tmp_path / ".codex" / "hooks" / "tradingcodex_hook.py").read_text(encoding="utf-8")
    assert json.dumps(raw["TRADINGCODEX_PYTHON"]) in hook_text
    assert '"py_compile_interpreter": GENERATED_PYTHON_COMMAND' in hook_text
    assert 'GENERATED_PYTHON_COMMAND = GENERATED_PYTHON.replace("\\\\", "/")' in hook_text


def test_template_rendering_is_single_pass_and_cmd_values_are_quoted() -> None:
    assert render_template("value={{X}}", {"X": "{{Y}}", "Y": "rewritten"}) == "value={{Y}}"
    context = serialized_template_context({"X": "foo&bar|baz^qux%TEMP%"})
    assert context["X_CMD"].startswith('"') and context["X_CMD"].endswith('"')
    assert "foo&bar|baz^qux%%TEMP%%" in context["X_CMD_SET"]
    assert workspace_launcher_command("win32") == r".\tcx.cmd"


def test_resolve_generated_git_command_prefers_real_xcode_binary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    developer = tmp_path / "Xcode Developer"
    selected_git = developer / "usr" / "bin" / "git"
    selected_git.parent.mkdir(parents=True)
    selected_git.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    selected_git.chmod(0o755)
    original_is_file = Path.is_file

    monkeypatch.setattr(generator.sys, "platform", "darwin")
    monkeypatch.setattr(generator.shutil, "which", lambda _name: "/usr/bin/git")
    monkeypatch.setattr(
        Path,
        "is_file",
        lambda path: True if path == Path("/usr/bin/xcode-select") else original_is_file(path),
    )
    monkeypatch.setattr(
        generator.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(stdout=f"{developer}\n"),
    )

    assert generator.resolve_generated_git_command() == str(selected_git.absolute())


def test_resolve_generated_git_command_rejects_macos_shim_when_xcode_selection_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_is_file = Path.is_file

    monkeypatch.setattr(generator.sys, "platform", "darwin")
    monkeypatch.setattr(generator.shutil, "which", lambda _name: "/usr/bin/git")
    monkeypatch.setattr(
        Path,
        "is_file",
        lambda path: True if path == Path("/usr/bin/xcode-select") else original_is_file(path),
    )
    monkeypatch.setattr(
        generator.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(subprocess.CalledProcessError(1, "xcode-select")),
    )

    assert generator.resolve_generated_git_command() == ""


def test_resolve_generated_git_command_keeps_nonshim_macos_git_fallback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fallback = tmp_path / "Homebrew Tools" / "bin" / "git"
    fallback.parent.mkdir(parents=True)
    fallback.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fallback.chmod(0o755)

    monkeypatch.setattr(generator.sys, "platform", "darwin")
    monkeypatch.setattr(generator.shutil, "which", lambda _name: str(fallback))
    monkeypatch.setattr(Path, "is_file", lambda path: False if path == Path("/usr/bin/xcode-select") else path.exists())

    assert generator.resolve_generated_git_command() == str(fallback.absolute())


def test_serialized_template_context_preserves_supplied_git_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    supplied = "/opt/trusted tools/git"
    monkeypatch.setattr(
        generator,
        "resolve_generated_git_command",
        lambda: (_ for _ in ()).throw(AssertionError("resolver should not run")),
    )

    context = serialized_template_context({"TRADINGCODEX_GIT_COMMAND": supplied})

    assert context["TRADINGCODEX_GIT_COMMAND"] == supplied
    assert context["TRADINGCODEX_GIT_COMMAND_PYTHON"] == json.dumps(supplied)


def test_explicit_db_override_is_projected_into_launchers_and_mcp(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "db-override-workspace"
    home = tmp_path / "home"
    db_path = tmp_path / "Custom Database" / "ledger.sqlite3"
    monkeypatch.setenv("TRADINGCODEX_HOME", str(home))
    monkeypatch.setenv("TRADINGCODEX_DB_NAME", str(db_path))
    bootstrap_workspace(workspace)
    lock = json.loads((workspace / ".tradingcodex" / "generated" / "module-lock.json").read_text(encoding="utf-8"))
    assert lock["tradingcodex_db_path"] == str(db_path.resolve())
    assert lock["db_source"] == "environment_override"
    configs = [workspace / ".codex" / "config.toml", *sorted((workspace / ".codex" / "agents").glob("*.toml"))]
    for path in configs:
        config = tomllib.loads(path.read_text(encoding="utf-8"))
        assert config["mcp_servers"]["tradingcodex"]["env"]["TRADINGCODEX_DB_NAME"] == str(db_path.resolve())

    env = {**os.environ, "PYTHONPATH": str(ROOT), "TRADINGCODEX_PYTHON": sys.executable}
    env.pop("TRADINGCODEX_HOME", None)
    env.pop("TRADINGCODEX_HOME_SOURCE", None)
    env.pop("TRADINGCODEX_DB_NAME", None)
    subprocess.run(
        [str(workspace / "tcx"), "db", "migrate"],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    status = subprocess.run(
        [str(workspace / "tcx"), "db", "status"],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    payload = json.loads(status.stdout)
    assert payload["db_path"] == str(db_path.resolve())
    assert payload["db_source"] == "environment_override"
    assert db_path.exists()
    assert not (home / "state" / "tradingcodex.sqlite3").exists()


def test_doctor_does_not_open_a_mismatched_projected_ledger(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    generated_home = tmp_path / "generated-home"
    other_home = tmp_path / "other-home"
    monkeypatch.setenv("TRADINGCODEX_HOME", str(generated_home))
    monkeypatch.delenv("TRADINGCODEX_DB_NAME", raising=False)
    bootstrap_workspace(workspace)
    env = {**os.environ, "PYTHONPATH": str(ROOT), "TRADINGCODEX_HOME": str(other_home)}
    env.pop("TRADINGCODEX_HOME_SOURCE", None)
    env.pop("TRADINGCODEX_DB_NAME", None)
    for layer in ("service", "guidance", "improvement"):
        result = subprocess.run(
            [sys.executable, "-m", "tradingcodex_cli", "doctor", "--layer", layer],
            cwd=workspace,
            env=env,
            text=True,
            capture_output=True,
        )
        assert result.returncode == 1
        assert "generated home/DB projection matches runtime" in result.stdout
    assert not (other_home / "state" / "tradingcodex.sqlite3").exists()


def test_update_rejects_pre_v1_module_lock_before_writing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("TRADINGCODEX_HOME", str(tmp_path / "home"))
    workspace = tmp_path / "workspace"
    bootstrap_workspace(workspace)
    lock_path = workspace / ".tradingcodex" / "generated" / "module-lock.json"
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    lock.pop("format")
    lock.pop("schema_version")
    lock.pop("generated_files")
    lock_path.write_text(json.dumps(lock, indent=2) + "\n", encoding="utf-8")
    before = {path.relative_to(workspace).as_posix(): path.read_bytes() for path in workspace.rglob("*") if path.is_file()}

    with pytest.raises(ValueError, match="unsupported pre-v1"):
        bootstrap_workspace(workspace, update=True)

    after = {path.relative_to(workspace).as_posix(): path.read_bytes() for path in workspace.rglob("*") if path.is_file()}
    assert after == before


def test_module_lock_reader_requires_exact_v1_top_level_fields(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("TRADINGCODEX_HOME", str(tmp_path / "home"))
    workspace = tmp_path / "workspace"
    bootstrap_workspace(workspace)
    lock_path = workspace / ".tradingcodex/generated/module-lock.json"
    original = json.loads(lock_path.read_text(encoding="utf-8"))

    for invalid, allow_newer in (
        ({key: value for key, value in original.items() if key != "generated_at"}, False),
        ({**original, "tradingcodex_version": "1.0.1", "legacy_version": "0.9.0"}, True),
    ):
        lock_path.write_text(json.dumps(invalid, indent=2) + "\n", encoding="utf-8")
        with pytest.raises(ValueError, match="fields do not match the v1 schema"):
            read_module_lock(workspace, allow_newer=allow_newer)


def test_module_lock_reader_rejects_malformed_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("TRADINGCODEX_HOME", str(tmp_path / "home"))
    workspace = tmp_path / "workspace"
    bootstrap_workspace(workspace)
    lock_path = workspace / ".tradingcodex/generated/module-lock.json"
    original = json.loads(lock_path.read_text(encoding="utf-8"))
    cases = (
        ("generated_at", "2026-01-01T00:00:00", "timezone"),
        ("workspace_id", "legacy-workspace", "workspace_id"),
        ("tradingcodex_version", "v1.0.0", "canonical PEP 440"),
        ("tradingcodex_package_spec", "", "package spec"),
        ("tradingcodex_home", "relative/home", "absolute path"),
        ("home_source", "legacy_fallback", "home_source"),
        ("tradingcodex_db_path", "relative.sqlite3", "absolute path"),
        ("db_source", "legacy_fallback", "db_source"),
    )

    for field, value, message in cases:
        invalid = copy.deepcopy(original)
        invalid[field] = value
        lock_path.write_text(json.dumps(invalid, indent=2) + "\n", encoding="utf-8")
        with pytest.raises(ValueError, match=message):
            read_module_lock(workspace)


def test_module_lock_reader_rejects_malformed_nested_records(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("TRADINGCODEX_HOME", str(tmp_path / "home"))
    workspace = tmp_path / "workspace"
    bootstrap_workspace(workspace)
    lock_path = workspace / ".tradingcodex/generated/module-lock.json"
    original = json.loads(lock_path.read_text(encoding="utf-8"))
    generated_path = next(iter(original["generated_files"]))

    invalid_locks: list[tuple[dict[str, Any], str]] = []
    invalid = copy.deepcopy(original)
    invalid["modules"][0]["legacy"] = True
    invalid_locks.append((invalid, "module fields"))
    invalid = copy.deepcopy(original)
    invalid["modules"].append(copy.deepcopy(invalid["modules"][0]))
    invalid_locks.append((invalid, "duplicated"))
    invalid = copy.deepcopy(original)
    invalid["modules"][0]["capabilities"] = "not-a-list"
    invalid_locks.append((invalid, "capabilities"))
    invalid = copy.deepcopy(original)
    invalid["generated_files"][generated_path]["legacy"] = True
    invalid_locks.append((invalid, "generated file fields"))
    invalid = copy.deepcopy(original)
    invalid["generated_files"][generated_path]["sha256"] = "A" * 64
    invalid_locks.append((invalid, "generated file hash"))
    invalid = copy.deepcopy(original)
    invalid["generated_files"][generated_path]["owner"] = "legacy"
    invalid_locks.append((invalid, "generated file owner"))
    invalid = copy.deepcopy(original)
    record = invalid["generated_files"].pop(generated_path)
    invalid["generated_files"][str((workspace / generated_path).resolve())] = record
    invalid_locks.append((invalid, "generated file path"))

    for invalid, message in invalid_locks:
        lock_path.write_text(json.dumps(invalid, indent=2) + "\n", encoding="utf-8")
        with pytest.raises(ValueError, match=message):
            read_module_lock(workspace)


def test_module_lock_writer_validates_before_writing_completion_marker(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("TRADINGCODEX_HOME", str(tmp_path / "home"))
    original_validator = generator._validate_module_lock
    validated: dict[str, object] = {}

    def reject_after_validation(target: Path, lock: Any, *, allow_newer: bool = False) -> None:
        original_validator(target, lock, allow_newer=allow_newer)
        validated.update(lock)
        raise ValueError("stop before completion marker")

    monkeypatch.setattr(generator, "_validate_module_lock", reject_after_validation)
    workspace = tmp_path / "workspace"
    with pytest.raises(ValueError, match="stop before completion marker"):
        bootstrap_workspace(workspace)

    assert validated["generated_files"]
    assert not (workspace / ".tradingcodex/generated/module-lock.json").exists()


def test_update_rejects_workspace_downgrade_before_writing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("TRADINGCODEX_HOME", str(tmp_path / "home"))
    workspace = tmp_path / "workspace"
    bootstrap_workspace(workspace)
    lock_path = workspace / ".tradingcodex/generated/module-lock.json"
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    lock["tradingcodex_version"] = _next_minor_version()
    lock_path.write_text(json.dumps(lock, indent=2) + "\n", encoding="utf-8")
    before = {path.relative_to(workspace).as_posix(): path.read_bytes() for path in workspace.rglob("*") if path.is_file()}

    with pytest.raises(ValueError, match="newer than runtime"):
        bootstrap_workspace(workspace, update=True)

    after = {path.relative_to(workspace).as_posix(): path.read_bytes() for path in workspace.rglob("*") if path.is_file()}
    assert after == before


def test_v1_update_keeps_an_explicit_home_override(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    selected = tmp_path / "explicit-home"
    monkeypatch.setenv("TRADINGCODEX_HOME", str(selected))
    monkeypatch.delenv("TRADINGCODEX_HOME_SOURCE", raising=False)
    workspace = tmp_path / "workspace"
    bootstrap_workspace(workspace)

    bootstrap_workspace(workspace, update=True)

    lock_path = workspace / ".tradingcodex" / "generated" / "module-lock.json"
    updated = json.loads(lock_path.read_text(encoding="utf-8"))
    assert updated["tradingcodex_home"] == str(selected.resolve())
    assert updated["home_source"] == "environment_override"


def test_package_runner_update_restores_recorded_explicit_runtime_identity(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from tradingcodex_cli.commands.bootstrap import _configure_recorded_update_runtime

    workspace = tmp_path / "workspace"
    selected_home = tmp_path / "custom-home"
    selected_db = tmp_path / "custom-ledger" / "tradingcodex.sqlite3"
    selected_addr = "127.0.0.1:49123"
    monkeypatch.setenv("TRADINGCODEX_HOME", str(selected_home))
    monkeypatch.setenv("TRADINGCODEX_DB_NAME", str(selected_db))
    monkeypatch.setenv("TRADINGCODEX_SERVICE_ADDR", selected_addr)
    bootstrap_workspace(workspace)
    lock = read_module_lock(workspace)

    for name in (
        "TRADINGCODEX_HOME",
        "TRADINGCODEX_HOME_SOURCE",
        "TRADINGCODEX_DB_NAME",
        "TRADINGCODEX_SERVICE_ADDR",
    ):
        monkeypatch.delenv(name, raising=False)

    _configure_recorded_update_runtime(workspace, lock)

    assert os.environ["TRADINGCODEX_HOME"] == str(selected_home.resolve())
    assert os.environ["TRADINGCODEX_HOME_SOURCE"] == "environment_override"
    assert os.environ["TRADINGCODEX_DB_NAME"] == str(selected_db.resolve())
    assert os.environ["TRADINGCODEX_SERVICE_ADDR"] == selected_addr


def test_package_runner_update_does_not_pin_a_platform_default_home(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from tradingcodex_cli.commands.bootstrap import _configure_recorded_update_runtime

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    lock = {
        "home_source": "platform_default",
        "tradingcodex_home": str(tmp_path / "recorded-platform-home"),
        "db_source": "home_default",
        "tradingcodex_db_path": str(tmp_path / "recorded-platform-home" / "state" / "tradingcodex.sqlite3"),
    }
    monkeypatch.delenv("TRADINGCODEX_HOME", raising=False)
    monkeypatch.delenv("TRADINGCODEX_HOME_SOURCE", raising=False)

    _configure_recorded_update_runtime(workspace, lock)

    assert "TRADINGCODEX_HOME" not in os.environ
    assert "TRADINGCODEX_HOME_SOURCE" not in os.environ


def test_native_process_kwargs() -> None:
    assert _detached_process_kwargs("posix") == {"start_new_session": True}
    assert "creationflags" in _detached_process_kwargs("nt")


def test_native_lock_atomic_write_and_portable_workspace_paths(tmp_path: Path) -> None:
    lock_target = tmp_path / "state" / "ledger"
    entered = threading.Event()
    release = threading.Event()

    def hold_lock() -> None:
        with exclusive_file_lock(lock_target, timeout_seconds=2):
            entered.set()
            release.wait(timeout=2)

    thread = threading.Thread(target=hold_lock)
    thread.start()
    assert entered.wait(timeout=2)
    with pytest.raises(TimeoutError):
        with exclusive_file_lock(lock_target, timeout_seconds=0.1):
            pass
    release.set()
    thread.join(timeout=2)
    assert not thread.is_alive()

    text_path = tmp_path / "atomic" / "note.txt"
    atomic_write_text(text_path, "one\ntwo\n")
    assert text_path.read_bytes() == b"one\ntwo\n"
    for unsafe in ("trading/research/CON.md", "trading/research/note.md:stream", "trading/research/trailing. "):
        with pytest.raises(ValueError):
            safe_workspace_path(tmp_path, unsafe)

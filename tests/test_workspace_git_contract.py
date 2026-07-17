from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from tradingcodex_cli.commands.doctor import _central_service_checks, _workspace_git_checks
from tradingcodex_cli.generator import bootstrap_workspace
from tradingcodex_service.application.runtime import RuntimeHomeResolutionError, workspace_context_payload
from tradingcodex_service.application.git_subprocess import isolated_git_environment
from tradingcodex_service.application.workspace_git import (
    GITIGNORE_END,
    GITIGNORE_START,
    ensure_workspace_git,
    workspace_git_status,
)


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def _bootstrap(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, workspace: Path) -> None:
    monkeypatch.setenv("TRADINGCODEX_HOME", str(tmp_path / "home"))
    bootstrap_workspace(workspace)


def test_standalone_attach_initializes_local_git_without_repository_actions(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"

    _bootstrap(monkeypatch, tmp_path, workspace)

    status = workspace_git_status(workspace)
    assert status["is_worktree"] is True
    assert status["git_root"] == str(workspace.resolve())
    assert status["git_dirty"] is True
    assert _git(workspace, "for-each-ref", "--format=%(refname)", "refs/heads").stdout == ""
    assert _git(workspace, "ls-files", "--stage").stdout == ""
    assert _git(workspace, "remote").stdout == ""
    assert _git(workspace, "rev-list", "--all", "--count").stdout.strip() == "0"


def test_attach_preserves_parent_worktree_without_nested_git(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    parent = tmp_path / "parent"
    parent.mkdir()
    assert _git(parent, "init", "--quiet").returncode == 0
    assert _git(parent, "remote", "add", "origin", "https://example.invalid/user-owned.git").returncode == 0
    config_before = (parent / ".git" / "config").read_bytes()
    workspace = parent / "workspace"

    _bootstrap(monkeypatch, tmp_path, workspace)

    assert not (workspace / ".git").exists()
    assert (parent / ".git" / "config").read_bytes() == config_before
    status = workspace_git_status(workspace)
    assert status["git_root"] == str(parent.resolve())
    assert status["git_remote"] == "https://example.invalid/user-owned.git"
    assert _git(parent, "ls-files", "--stage").stdout == ""
    assert _git(parent, "for-each-ref", "--format=%(refname)", "refs/heads").stdout == ""


def test_attach_and_update_merge_only_the_delimited_gitignore_block(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    assert _git(workspace, "init", "--quiet").returncode == 0
    user_prefix = "# User rules\n*.user-only\n"
    (workspace / ".gitignore").write_text(user_prefix, encoding="utf-8")

    _bootstrap(monkeypatch, tmp_path, workspace)
    first = (workspace / ".gitignore").read_text(encoding="utf-8")
    assert first.startswith(user_prefix)
    assert first.count(GITIGNORE_START) == 1
    assert first.count(GITIGNORE_END) == 1

    bootstrap_workspace(workspace, update=True)
    second = (workspace / ".gitignore").read_text(encoding="utf-8")
    assert second == first


def test_privacy_ignore_keeps_user_brain_strategy_research_and_memory_eligible(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    _bootstrap(monkeypatch, tmp_path, workspace)

    ignored = (
        "state/tradingcodex.sqlite3",
        ".tradingcodex/state/tradingcodex.sqlite3-wal",
        ".tradingcodex/mainagent/server-status.json",
        ".tradingcodex/mainagent/session-start.json",
        ".tradingcodex/mainagent/subagent-session-state.json",
        ".tradingcodex/mainagent/session-workflow-runs.json",
        ".tradingcodex/mainagent/runs/analysis-a/web-run.json",
        ".tradingcodex/mainagent/runs/analysis-a/web-run-events.jsonl",
        ".tradingcodex/mainagent/runs/analysis-a/investor-context-snapshot.md",
        ".tradingcodex/user/investor-context.md",
        "trading/audit/codex-hooks.jsonl",
        "trading/research/.index/research-index.json",
        "trading/research/datasets/objects/2d711642b726b04401627ca9fbac32f5da7e5c3c.parquet",
        ".env",
        "broker.pem",
    )
    eligible = (
        ".agents/skills/investment-brain-local/SKILL.md",
        ".agents/skills/strategy-local/SKILL.md",
        "trading/research/company-thesis.md",
        "trading/research/datasets/manifests/dataset-1.json",
        "trading/research/datasets/withdrawals/withdrawal-1.json",
        "trading/reports/postmortem/lesson.md",
        "trading/decisions/decision-1.md",
        ".tradingcodex/mainagent/improve.jsonl",
        ".tradingcodex/mainagent/runs/analysis-a/run.json",
        ".tradingcodex/mainagent/runs/analysis-a/strategy-snapshot.md",
    )
    for path in ignored:
        assert _git(workspace, "check-ignore", "--no-index", "--quiet", path).returncode == 0, path
    for path in eligible:
        assert _git(workspace, "check-ignore", "--no-index", "--quiet", path).returncode == 1, path


def test_git_root_dirty_state_and_ignore_contract_are_diagnostic(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    _bootstrap(monkeypatch, tmp_path, workspace)

    context = workspace_context_payload(workspace)
    assert context["git_root"] == str(workspace.resolve())
    assert context["git_dirty"] is True
    checks = {check["name"]: check for check in _workspace_git_checks(workspace)}
    assert checks["workspace Git worktree and dirty state"]["ok"] is True
    assert f"root={workspace.resolve()}" in checks["workspace Git worktree and dirty state"]["detail"]
    assert "workspace_dirty=true" in checks["workspace Git worktree and dirty state"]["detail"]
    assert checks["workspace privacy-first Git ignore contract"]["ok"] is True


def test_attach_rejects_workspace_contained_runtime_home_before_mutation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    runtime_home = workspace / ".tradingcodex-home"
    monkeypatch.setenv("TRADINGCODEX_HOME", str(runtime_home))

    with pytest.raises(RuntimeHomeResolutionError, match="must be outside the generated workspace"):
        bootstrap_workspace(workspace)

    assert not (workspace / ".gitignore").exists()
    assert not (workspace / ".git").exists()


def test_doctor_reports_workspace_contained_runtime_home_as_failed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    external_home = tmp_path / "runtime-home"
    monkeypatch.setenv("TRADINGCODEX_HOME", str(external_home))
    bootstrap_workspace(workspace)
    monkeypatch.setenv("TRADINGCODEX_HOME", str(workspace / ".tradingcodex-home"))
    monkeypatch.delenv("TRADINGCODEX_WORKSPACE_ROOT", raising=False)

    with pytest.raises(RuntimeHomeResolutionError, match="must be outside"):
        workspace_context_payload(workspace)

    monkeypatch.setenv("TRADINGCODEX_WORKSPACE_ROOT", str(workspace))

    checks = {check["name"]: check for check in _central_service_checks(workspace)}

    assert checks["global home selection"]["ok"] is False
    assert checks["runtime home is outside workspace"]["ok"] is False
    assert "must be outside" in checks["runtime home is outside workspace"]["detail"]


def test_external_sibling_runtime_home_preserves_versionable_workspace_files(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    external_home = tmp_path / "runtime-home"
    monkeypatch.setenv("TRADINGCODEX_HOME", str(external_home))
    bootstrap_workspace(workspace)

    checks = {check["name"]: check for check in _central_service_checks(workspace)}

    assert checks["runtime home is outside workspace"]["ok"] is True
    assert _git(
        workspace,
        "check-ignore",
        "--no-index",
        "--quiet",
        ".agents/skills/investment-brain-local/SKILL.md",
    ).returncode == 1


def test_malformed_managed_gitignore_block_fails_without_rewriting_user_content(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    assert _git(workspace, "init", "--quiet").returncode == 0
    malformed = f"user-rule\n{GITIGNORE_START}\nunfinished\n"
    (workspace / ".gitignore").write_text(malformed, encoding="utf-8")

    with pytest.raises(ValueError, match="managed .gitignore block is malformed"):
        ensure_workspace_git(workspace)

    assert (workspace / ".gitignore").read_text(encoding="utf-8") == malformed


def test_symlinked_gitignore_is_rejected_without_reading_or_rewriting_target(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    assert _git(workspace, "init", "--quiet").returncode == 0
    external = tmp_path / "external-ignore"
    external.write_text("private-user-rule\n", encoding="utf-8")
    (workspace / ".gitignore").symlink_to(external)

    with pytest.raises(ValueError, match="regular file, not a symlink"):
        ensure_workspace_git(workspace)

    assert external.read_text(encoding="utf-8") == "private-user-rule\n"


def test_existing_workspace_check_does_not_initialize_missing_git(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    with pytest.raises(ValueError, match="must already belong to a Git worktree"):
        ensure_workspace_git(workspace, initialize_if_missing=False)

    assert not (workspace / ".git").exists()
    assert not (workspace / ".gitignore").exists()


def test_workspace_git_ignores_inherited_repository_and_config_overrides(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    poison = tmp_path / "poison"
    poison.mkdir()
    assert _git(poison, "init", "--quiet").returncode == 0
    workspace = tmp_path / "workspace"

    monkeypatch.setenv("GIT_DIR", str(poison / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(poison))
    monkeypatch.setenv("GIT_INDEX_FILE", str(tmp_path / "poison-index"))
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.fsmonitor")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "unsafe-helper")

    status = ensure_workspace_git(workspace)

    assert status["initialized"] is True
    assert status["git_root"] == str(workspace.resolve())
    assert (workspace / ".git").is_dir()
    assert _git(poison, "config", "--get", "remote.origin.url").stdout == ""


def test_workspace_remote_diagnostic_redacts_credentials_and_suffix_data(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    assert _git(workspace, "init", "--quiet").returncode == 0
    remote = "https://diagnostic-user:super-secret@example.invalid/repo.git?token=secret#private"
    assert _git(workspace, "remote", "add", "origin", remote).returncode == 0

    status = workspace_git_status(workspace)

    assert status["git_remote"] == "https://example.invalid/repo.git"
    assert "diagnostic-user" not in status["git_remote"]
    assert "super-secret" not in status["git_remote"]
    assert "token" not in status["git_remote"]

    assert _git(workspace, "remote", "set-url", "origin", "deploy-token@example.invalid:repo.git").returncode == 0
    assert workspace_git_status(workspace)["git_remote"] == "example.invalid:repo.git"


def test_isolated_git_ssh_command_does_not_read_user_configuration() -> None:
    command = isolated_git_environment()["GIT_SSH_COMMAND"]

    assert f"-F {os.devnull}" in command
    assert "-oProxyCommand=none" in command
    assert "-oPermitLocalCommand=no" in command

from __future__ import annotations

import json
import io
import re
import runpy
import subprocess
import sys
import tomllib
from pathlib import Path, PureWindowsPath

import pytest
import yaml
from packaging.version import Version

from tradingcodex_cli import startup_status
from tradingcodex_cli.__main__ import main, print_help
from tradingcodex_cli.commands.build import build, print_build_help
from tradingcodex_cli.commands.forecast import forecast
from tradingcodex_cli.commands.investor_context import investor_context
from tradingcodex_cli.commands.mcp import mcp, print_mcp_help
from tradingcodex_service.version import TRADINGCODEX_VERSION


ROOT = Path(__file__).resolve().parents[1]


def _next_minor_version() -> str:
    current = Version(TRADINGCODEX_VERSION)
    return f"{current.major}.{current.minor + 1}.0"


def test_native_windows_smoke_calls_spaced_batch_launcher_without_escaped_quotes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = runpy.run_path(str(ROOT / "tests/platform_wheel_smoke.py"))
    monkeypatch.setenv("COMSPEC", r"C:\Windows\System32\cmd.exe")

    argv = module["windows_launcher_argv"](
        PureWindowsPath(r"C:\Workspace With Spaces"),
        "update",
        "--skip-refresh",
        "--no-doctor",
    )

    assert argv == [
        r"C:\Windows\System32\cmd.exe",
        "/d",
        "/s",
        "/c",
        "call",
        r"C:\Workspace With Spaces\tcx.cmd",
        "update",
        "--skip-refresh",
        "--no-doctor",
    ]
    assert r'\"' not in subprocess.list2cmdline(argv)


def test_cli_hook_dispatch_preserves_standard_input_and_output(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    hook = tmp_path / ".codex/hooks/tradingcodex_hook.py"
    hook.parent.mkdir(parents=True)
    hook.write_text(
        "import json, sys\n"
        "payload = json.loads(sys.stdin.read())\n"
        "print(json.dumps({'event': sys.argv[1], 'payload': payload}))\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("TRADINGCODEX_WORKSPACE_ROOT", raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "stdin", io.StringIO('{"platform":"windows"}\n'))

    main(["__hook", "session-start"])

    assert json.loads(capsys.readouterr().out) == {
        "event": "session-start",
        "payload": {"platform": "windows"},
    }


def test_v1_package_metadata_has_one_stable_version_source() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert TRADINGCODEX_VERSION == "1.1.1"
    assert str(Version(TRADINGCODEX_VERSION)) == TRADINGCODEX_VERSION
    assert project["project"]["dynamic"] == ["version"]
    assert "version" not in project["project"]
    assert project["tool"]["setuptools"]["dynamic"]["version"] == {
        "attr": "tradingcodex_service.version.TRADINGCODEX_VERSION"
    }
    assert "Development Status :: 5 - Production/Stable" in project["project"]["classifiers"]
    assert project["project"]["urls"]["Repository"] == "https://github.com/monarchjuno/tradingcodex.git"

    frontend = json.loads((ROOT / "frontend/package.json").read_text(encoding="utf-8"))
    frontend_lock = json.loads((ROOT / "frontend/package-lock.json").read_text(encoding="utf-8"))
    assert frontend["private"] is True
    assert "version" not in frontend
    assert "version" not in frontend_lock
    assert "version" not in frontend_lock["packages"][""]

    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "## Unreleased" in changelog
    assert f"## {TRADINGCODEX_VERSION} - " in changelog
    assert "include CHANGELOG.md" in (ROOT / "MANIFEST.in").read_text(encoding="utf-8")


def test_committed_workbench_uses_content_hashed_vite_assets() -> None:
    build_root = ROOT / "tradingcodex_service/static/tradingcodex_web"
    index = (build_root / "index.html").read_text(encoding="utf-8")
    assets = re.findall(
        r'(?:href|src)="(/static/tradingcodex_web/assets/[^"/]+-[A-Za-z0-9_-]{8,}\.(?:js|css))"',
        index,
    )

    assert any(asset.endswith(".js") for asset in assets)
    assert any(asset.endswith(".css") for asset in assets)
    assert not (build_root / "app.js").exists()
    assert not (build_root / "app.css").exists()
    for asset in assets:
        relative = asset.removeprefix("/static/tradingcodex_web/")
        assert (build_root / relative).is_file()

    vite_config = (ROOT / "frontend/vite.config.ts").read_text(encoding="utf-8")
    assert 'entryFileNames: "app.js"' not in vite_config
    assert 'assetFileNames: "app[extname]"' not in vite_config


@pytest.mark.parametrize("command", ["--version", "version"])
def test_cli_prints_the_canonical_version_without_a_workspace(command: str) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "tradingcodex_cli", command],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )

    assert result.stdout.strip() == TRADINGCODEX_VERSION
    assert result.stderr == ""


def test_v1_release_checklist_does_not_preapprove_gates() -> None:
    deployment = (ROOT / "docs/deployment.md").read_text(encoding="utf-8")
    checklist = (ROOT / "docs/release-readiness.md").read_text(encoding="utf-8")

    assert re.search(r"(?<![\d.])v?0\.\d+\.\d+\b", deployment) is None
    assert re.search(r"(?<![\d.])v?0\.\d+\.\d+\b", checklist) is None
    assert "- [x]" not in checklist.lower()
    assert f"release_version={TRADINGCODEX_VERSION}" in checklist


def test_attach_contract_has_no_overwrite_or_init_compatibility() -> None:
    active_contract = "\n".join(
        (ROOT / path).read_text(encoding="utf-8")
        for path in (
            "AGENTS.md",
            "README.md",
            "installation.md",
            "install.sh",
            "tradingcodex_cli/__main__.py",
            "tradingcodex_cli/generator.py",
            "docs/README.md",
            "docs/generated-workspaces.md",
            "docs/interfaces-and-surfaces.md",
            "docs/validation-and-test-plan.md",
            "openwiki/generated-workspaces.md",
            "openwiki/quickstart.md",
            "tests/README.md",
        )
    )

    assert "--overwrite" not in active_contract
    assert "tcx init" not in active_contract


def test_posix_installer_preserves_durable_uvx_runtime_cache() -> None:
    installer = (ROOT / "install.sh").read_text(encoding="utf-8")

    assert "unset UV_NO_CACHE" in installer
    assert "UV_NO_CACHE=1" not in installer
    assert 'attach "$WORKSPACE" --from "$PACKAGE_SPEC"' in installer
    assert 'update "$WORKSPACE" --from "$PACKAGE_SPEC"' in installer
    assert "package spec: $PACKAGE_SPEC" not in installer


def test_posix_installer_rejects_credentials_without_echoing_them(tmp_path: Path) -> None:
    secret = "UniqueInstallerPassword"
    result = subprocess.run(
        [
            "sh",
            str(ROOT / "install.sh"),
            "--from",
            f"https://user:{secret}@example.test/tradingcodex.whl",
            str(tmp_path / "workspace"),
        ],
        cwd=tmp_path,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 2
    assert secret not in result.stdout
    assert secret not in result.stderr
    assert not (tmp_path / "workspace").exists()


def test_installation_research_create_example_supplies_required_universe() -> None:
    installation = (ROOT / "installation.md").read_text(encoding="utf-8")
    command = next(line for line in installation.splitlines() if line.startswith("./tcx research create "))

    assert "--markdown-file " in command
    assert "--universe " in command


def test_startup_snapshot_does_not_hide_configuration_errors(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        startup_status,
        "build_server_status",
        lambda root: (_ for _ in ()).throw(RuntimeError("invalid v1 configuration")),
    )

    with pytest.raises(RuntimeError, match="invalid v1 configuration"):
        startup_status.write_server_status_snapshot(tmp_path)

    assert not (tmp_path / ".tradingcodex/mainagent/server-status.json").exists()


def test_startup_status_exposes_only_inert_build_compatibility(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    home = tmp_path.parent / f"{tmp_path.name}-home"
    monkeypatch.setenv("TRADINGCODEX_HOME", str(home))
    monkeypatch.setenv("TRADINGCODEX_CODEX_PERMISSION", "unrestricted")
    monkeypatch.setattr(
        startup_status,
        "inspect_service_status",
        lambda addr: {
            "service": "tradingcodex",
            "addr": addr,
            "reachable": False,
            "compatible": False,
            "issue": "not_running",
            "next_action": "./tcx service ensure",
        },
    )
    lock_path = tmp_path / ".tradingcodex/generated/module-lock.json"
    lock_path.parent.mkdir(parents=True)
    lock_path.write_text(
        json.dumps(
            {
                "format": "tradingcodex.module-lock",
                "schema_version": 1,
                "generated_at": "2026-07-11T00:00:00Z",
                "workspace_id": "tcxw_" + "a" * 32,
                "tradingcodex_version": TRADINGCODEX_VERSION,
                "tradingcodex_package_spec": "tradingcodex",
                "tradingcodex_home": str(home.resolve()),
                "home_source": "environment_override",
                "tradingcodex_db_path": str((home / "state/tradingcodex.sqlite3").resolve()),
                "db_source": "home_default",
                "modules": [],
                "generated_files": {},
            }
        ),
        encoding="utf-8",
    )
    legacy = tmp_path / ".tradingcodex/runtime/mode.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_text('{"mode":"build","build_enabled":true}', encoding="utf-8")

    status = startup_status.build_server_status(tmp_path)

    assert status["build_authorization"] == {
        "status": "exact_turn_required",
        "authority": "user_prompt_submit_hook",
        "exact_first_line": "$tcx-build",
        "invocation_position": "first_meaningful_line",
        "accepted_forms": ["plain_token", "matching_workspace_skill_markdown_link"],
        "same_line_request_allowed": True,
        "root_native_turn_only": True,
        "persistent_mode": False,
        "active": False,
        "permission_is_advisory": True,
        "recommended_profile": "trading-build",
        "full_access_detected": True,
        "workspace_writable": True,
    }
    assert status["managed_skill_authorization"] == {
        "status": "exact_capability_turn_required",
        "authority": "user_prompt_submit_hook",
        "exact_first_lines": {
            "brain": "$tcx-brain",
            "strategy": "$tcx-strategy",
        },
        "invocation_position": "first_meaningful_line",
        "accepted_forms": ["plain_token", "matching_workspace_skill_markdown_link"],
        "same_line_request_allowed": True,
        "root_native_turn_only": True,
        "persistent_mode": False,
        "active": False,
        "recommended_profile": "trading-research",
        "lifecycle_transport": "proof_protected_mcp",
        "runtime_filesystem_access": False,
        "cross_scope": False,
        "plan_mode_allowed": False,
        "ordinary_workspace_writable": True,
    }
    assert status["mode_status"]["status"] == "retired"
    assert status["mode_status"]["authority"] == "none"
    assert status["mode_status"]["build_enabled"] is False
    assert status["mode_status"]["legacy_mode_file_ignored"] is True
    assert status["update_status"]["can_self_update"] is False
    assert status["update_status"]["head_manager_update_allowed"] is False


@pytest.mark.parametrize(
    (
        "raw_permission",
        "normalized",
        "workspace_writable",
        "ordinary_workspace_writable",
        "full_access",
    ),
    [
        ("workspace-write", "workspace_write", True, True, False),
        ("trading-build", "workspace_write", True, True, False),
        ("read-only", "read_only", False, False, False),
        ("trading-research", "restricted", False, True, False),
        ("danger-full-access", "full_access", True, True, True),
    ],
)
def test_permission_status_preserves_least_privilege_workspace_write(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    raw_permission: str,
    normalized: str,
    workspace_writable: bool,
    ordinary_workspace_writable: bool,
    full_access: bool,
) -> None:
    monkeypatch.setenv("TRADINGCODEX_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("TRADINGCODEX_CODEX_PERMISSION", raw_permission)

    status = startup_status.detect_codex_permission_status(tmp_path)

    assert status["codex_permission"] == normalized
    assert status["workspace_writable"] is workspace_writable
    assert status["managed_workspace_writable"] is workspace_writable
    assert status["ordinary_workspace_writable"] is ordinary_workspace_writable
    assert status["workspace_write_detected"] is (normalized == "workspace_write")
    assert status["full_access_detected"] is full_access


def test_permission_status_reads_custom_project_default(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("TRADINGCODEX_HOME", str(tmp_path / "home"))
    (tmp_path / ".codex").mkdir()
    (tmp_path / ".codex/config.toml").write_text(
        'default_permissions = "trading-research"\n',
        encoding="utf-8",
    )

    status = startup_status.detect_codex_permission_status(tmp_path)

    assert status["codex_permission"] == "restricted"
    assert status["raw_permission"] == "trading-research"
    assert status["workspace_writable"] is False
    assert status["ordinary_workspace_writable"] is True
    assert status["detection_source"] == "project_config"


def test_update_status_requires_package_refresh_for_newer_workspace(monkeypatch, tmp_path: Path) -> None:
    lock_path = tmp_path / ".tradingcodex/generated/module-lock.json"
    lock_path.parent.mkdir(parents=True)
    lock_path.write_text(
        json.dumps({
            "format": "tradingcodex.module-lock",
            "schema_version": 1,
            "generated_at": "2026-07-11T00:00:00Z",
            "workspace_id": "tcxw_" + "a" * 32,
            "tradingcodex_version": _next_minor_version(),
            "tradingcodex_package_spec": "tradingcodex",
            "tradingcodex_home": str((tmp_path / "home").resolve()),
            "home_source": "environment_override",
            "tradingcodex_db_path": str((tmp_path / "home/state/tradingcodex.sqlite3").resolve()),
            "db_source": "home_default",
            "modules": [],
            "generated_files": {},
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        startup_status,
        "latest_release_info",
        lambda: {
            "latest_release_version": "unknown",
            "latest_release_status": "unavailable",
            "latest_release_source": "test",
        },
    )

    status = startup_status.build_update_status(
        tmp_path,
        permission_status={"full_access_detected": True},
        mode_status={"build_enabled": True},
    )

    assert status["workspace_is_newer_than_installed"] is True
    assert status["workspace_update_available"] is False
    assert status["package_update_required_first"] is True
    terminal_command = (
        "uvx --refresh --from tradingcodex tcx update . --from tradingcodex"
    )
    assert status["command"] == ""
    assert status["interactive_user_terminal_command"] == terminal_command
    assert status["package_refresh_user_terminal_required"] is True
    assert status["workspace_build_update_supported"] is False
    assert status["workspace_build_update_eligible"] is False
    assert status["update_execution_surface"] == "interactive_user_terminal"
    assert status["restart_required_after_update"] is True
    assert status["can_self_update"] is False
    assert status["head_manager_update_allowed"] is False
    assert status["head_manager_update_command"] == ""
    assert status["self_update_requires"] == [
        "interactive_user_terminal",
        "explicit_user_request",
    ]
    assert "interactive user-terminal" in status["head_manager_update_blocked_reason"]
    assert "$tcx-build" not in status["recommended_action"]
    assert terminal_command in status["recommended_action"]
    next_actions = startup_status.build_allowed_next_actions(
        permission_status={"full_access_detected": True},
        update_status=status,
        service_status="ok",
    )
    assert all("$tcx-build" not in action for action in next_actions)
    assert next_actions == [
        f"From an interactive user terminal only, run: {terminal_command}"
    ]


def test_workspace_local_update_is_the_only_build_turn_update(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    lock_path = tmp_path / ".tradingcodex/generated/module-lock.json"
    lock_path.parent.mkdir(parents=True)
    lock_path.write_text(
        json.dumps(
            {
                "format": "tradingcodex.module-lock",
                "schema_version": 1,
                "generated_at": "2026-07-11T00:00:00Z",
                "workspace_id": "tcxw_" + "a" * 32,
                "tradingcodex_version": "1.0.0rc1",
                "tradingcodex_package_spec": "tradingcodex",
                "tradingcodex_home": str((tmp_path / "home").resolve()),
                "home_source": "environment_override",
                "tradingcodex_db_path": str(
                    (tmp_path / "home/state/tradingcodex.sqlite3").resolve()
                ),
                "db_source": "home_default",
                "modules": [],
                "generated_files": {},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        startup_status,
        "latest_release_info",
        lambda: {
            "latest_release_version": "unknown",
            "latest_release_status": "unavailable",
            "latest_release_source": "test",
        },
    )

    status = startup_status.build_update_status(
        tmp_path,
        permission_status={"workspace_writable": True},
    )

    local_command = "./tcx update --skip-refresh"
    assert status["workspace_update_allowed"] is True
    assert status["workspace_update_recommended"] is True
    assert status["workspace_build_update_supported"] is True
    assert status["workspace_build_update_eligible"] is True
    assert status["package_refresh_user_terminal_required"] is False
    assert status["command"] == local_command
    assert status["head_manager_update_command"] == local_command
    assert status["head_manager_update_allowed"] is False
    assert status["update_execution_surface"] == "workspace_local_build_or_user_terminal"
    assert status["self_update_requires"] == [
        "codex_writable_session",
        "exact_tcx_build_turn",
        "explicit_user_request",
    ]
    assert "$tcx-build" in status["recommended_action"]
    assert "uvx" not in status["recommended_action"]
    assert local_command in status["recommended_action"]

    next_actions = startup_status.build_allowed_next_actions(
        permission_status={"workspace_writable": True},
        update_status=status,
        service_status="ok",
    )
    assert any("$tcx-build" in action for action in next_actions)
    assert f"Within that Build turn, run only: {local_command}" in next_actions
    assert all("uvx" not in action for action in next_actions)

    read_only_status = startup_status.build_update_status(
        tmp_path,
        permission_status={"workspace_writable": False},
    )
    assert read_only_status["workspace_build_update_supported"] is True
    assert read_only_status["workspace_build_update_eligible"] is False
    assert "trading-build" in read_only_status["head_manager_update_blocked_reason"]
    read_only_actions = startup_status.build_allowed_next_actions(
        permission_status={"workspace_writable": False},
        update_status=read_only_status,
        service_status="ok",
    )
    assert any("Select the trading-build permission profile" in action for action in read_only_actions)
    assert f"Within that Build turn, run only: {local_command}" in read_only_actions


def test_update_status_never_treats_local_provenance_as_an_executable_spec(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    lock_path = tmp_path / ".tradingcodex/generated/module-lock.json"
    lock_path.parent.mkdir(parents=True)
    lock_path.write_text(
        json.dumps(
            {
                "format": "tradingcodex.module-lock",
                "schema_version": 1,
                "generated_at": "2026-07-11T00:00:00Z",
                "workspace_id": "tcxw_" + "a" * 32,
                "tradingcodex_version": _next_minor_version(),
                "tradingcodex_package_spec": "local-explicit",
                "tradingcodex_home": str((tmp_path / "home").resolve()),
                "home_source": "environment_override",
                "tradingcodex_db_path": str(
                    (tmp_path / "home/state/tradingcodex.sqlite3").resolve()
                ),
                "db_source": "home_default",
                "modules": [],
                "generated_files": {},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        startup_status,
        "latest_release_info",
        lambda: {
            "latest_release_version": "unknown",
            "latest_release_status": "unavailable",
            "latest_release_source": "test",
        },
    )

    status = startup_status.build_update_status(
        tmp_path,
        permission_status={"full_access_detected": False},
        mode_status={"build_enabled": True},
    )

    assert status["package_spec"] == ""
    assert status["package_source_kind"] == "local-explicit"
    assert status["package_source_requires_explicit"] is True
    terminal_command = (
        "uvx --refresh --from <package-spec> tcx update . --from <package-spec>"
    )
    assert status["command"] == ""
    assert status["interactive_user_terminal_command"] == terminal_command
    assert status["package_refresh_user_terminal_required"] is True
    assert status["can_self_update"] is False


def test_removed_cli_aliases_fail_instead_of_dispatching(tmp_path: Path) -> None:
    for command in ("show", "reset"):
        with pytest.raises(ValueError):
            investor_context(tmp_path, [command])
    with pytest.raises(ValueError, match="Unknown forecast command"):
        forecast(tmp_path, ["calibration-report"])
    with pytest.raises(ValueError, match="Usage"):
        mcp(tmp_path, ["calls"])
    with pytest.raises(ValueError, match="Usage"):
        mcp(tmp_path, ["external", "list"])
    with pytest.raises(ValueError, match="Usage"):
        mcp(tmp_path, ["permission", "list"])
    with pytest.raises(ValueError, match="Usage: tcx build status"):
        build(tmp_path, ["permission", "list"])


def test_cli_help_exposes_only_tradingcodex_mcp_and_build_status(
    capsys: pytest.CaptureFixture[str],
) -> None:
    print_help()
    main_help = capsys.readouterr().out
    assert "tcx mode" not in main_help
    assert "tcx build status" in main_help
    assert "tcx mcp stdio|external|permission" not in main_help

    print_build_help()
    build_help = capsys.readouterr().out
    assert "$tcx-build" in build_help
    assert "external" not in build_help.lower()

    print_mcp_help()
    mcp_help = capsys.readouterr().out
    assert "stdio" in mcp_help
    assert "external" not in mcp_help.lower()
    assert "permission" not in mcp_help.lower()


def test_release_publish_is_tag_bound_and_reuses_one_verified_build() -> None:
    workflow = yaml.safe_load((ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8"))
    trigger = workflow.get("on") or workflow.get(True)
    assert trigger["workflow_dispatch"]["inputs"]["release_version"]["required"] is True
    jobs = workflow["jobs"]
    validation = next(
        step
        for step in jobs["build"]["steps"]
        if step["name"] == "Validate release identity and publish ref"
    )
    assert 'version("tradingcodex")' in validation["run"]
    assert "TRADINGCODEX_VERSION" in validation["run"]
    assert "refs/tags/v${RELEASE_VERSION}" in validation["run"]
    assert 'git cat-file -t "$GITHUB_REF_NAME"' in validation["run"]
    assert "git merge-base --is-ancestor HEAD refs/remotes/origin/main" in validation["run"]
    distribution = next(
        step
        for step in jobs["build"]["steps"]
        if step["name"] == "Validate distribution metadata and filenames"
    )
    assert "parse_wheel_filename" in distribution["run"]
    assert "actual_artifacts != expected_artifacts" in distribution["run"]
    assert "unexpected distribution files" in distribution["run"]
    assert set(jobs) == {"build", "publish-pypi"}
    assert jobs["publish-pypi"]["needs"] == "build"
    assert any(
        "platform_wheel_smoke.py" in step.get("run", "")
        for step in jobs["build"]["steps"]
    )
    release_steps = "\n".join(str(step.get("run", "")) for step in jobs["build"]["steps"])
    assert "release_upgrade_smoke.py --wheel-dir dist --from-version 1.0.2" in release_steps
    assert "--from-version 1.0.0" not in release_steps
    assert "--from-version 1.0.1" not in release_steps
    assert (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8").count("python -m build") == 1
    assert any("download-artifact" in step.get("uses", "") for step in jobs["publish-pypi"]["steps"])
    assert not any("python -m build" in step.get("run", "") for step in jobs["publish-pypi"]["steps"])


def test_github_actions_keep_the_normal_and_pages_paths_to_one_job_each() -> None:
    ci = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
    pages = yaml.safe_load(
        (ROOT / ".github/workflows/deploy-user-guide.yml").read_text(encoding="utf-8")
    )
    assert set(ci["jobs"]) == {"quality"}
    ci_steps = "\n".join(str(step.get("run", "")) for step in ci["jobs"]["quality"]["steps"])
    assert "release_upgrade_smoke.py --wheel-dir dist --from-version 1.0.2" in ci_steps
    assert "--from-version 1.0.0" not in ci_steps
    assert "--from-version 1.0.1" not in ci_steps
    assert set(pages["jobs"]) == {"deploy"}
    assert len(ci["jobs"]["quality"]["steps"]) <= 7
    assert len(pages["jobs"]["deploy"]["steps"]) == 4

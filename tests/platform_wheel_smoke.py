from __future__ import annotations

import argparse
import ast
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import tomllib
import venv
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urljoin
from urllib.request import build_opener, ProxyHandler


LOOPBACK_HTTP_OPENER = build_opener(ProxyHandler({}))


def run(
    argv: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    input_text: str | None = None,
    timeout: int = 180,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        argv,
        cwd=cwd,
        env=env,
        input=input_text,
        text=True,
        encoding="utf-8",
        errors="strict",
        capture_output=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"command failed ({result.returncode}): {subprocess.list2cmdline(argv)}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


def windows_launcher_argv(workspace: Path, *args: str) -> list[str]:
    return [
        os.environ.get("COMSPEC", "cmd.exe"),
        "/d",
        "/s",
        "/c",
        "call",
        str(workspace / "tcx.cmd"),
        *args,
    ]


def launcher_argv(workspace: Path, *args: str) -> list[str]:
    if os.name == "nt":
        return windows_launcher_argv(workspace, *args)
    return [str(workspace / "tcx"), *args]


def calculation_launcher_argv(workspace: Path, script_name: str) -> list[str]:
    if os.name == "nt":
        command = subprocess.list2cmdline([str(workspace / "tcx-calc.cmd"), script_name])
        return [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/s", "/c", command]
    return [str(workspace / "tcx-calc"), script_name]


def free_loopback_port() -> int:
    # Exercise the release-default address first and stay below the default
    # macOS ephemeral range. A bind-to-zero port can be recycled as the source
    # side of a probe before the detached service claims it on hosted runners.
    for port in range(48267, 47999, -1):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
                listener.bind(("127.0.0.1", port))
                return port
        except OSError:
            continue
    raise RuntimeError("no free product-range loopback port for native wheel smoke")


def fetch_text(url: str) -> str:
    with LOOPBACK_HTTP_OPENER.open(url, timeout=15) as response:
        assert response.status == 200
        return response.read().decode("utf-8")


def platform_environment(root: Path) -> tuple[dict[str, str], Path]:
    env = os.environ.copy()
    for key in (
        "PYTHONPATH",
        "TRADINGCODEX_HOME",
        "TRADINGCODEX_HOME_SOURCE",
        "TRADINGCODEX_DB_NAME",
        "TRADINGCODEX_WORKSPACE_ROOT",
        "TRADINGCODEX_SERVICE_ADDR",
        "TRADINGCODEX_MCP_AUTOSTART_SERVICE",
        "TRADINGCODEX_PYTHON",
        "TRADINGCODEX_LAUNCHED_BY_UVX",
        "_TRADINGCODEX_PRIOR_RUNTIME_PYTHON",
        "CODEX_HOME",
        "DJANGO_SETTINGS_MODULE",
        "XDG_CACHE_HOME",
        "XDG_DATA_HOME",
    ):
        env.pop(key, None)
    user_home = root / "User Home With Spaces"
    env["HOME"] = str(user_home)
    if os.name == "nt":
        local_app_data = root / "Local App Data With Spaces"
        env["USERPROFILE"] = str(user_home)
        env["LOCALAPPDATA"] = str(local_app_data)
        expected_home = local_app_data / "TradingCodex"
    elif sys.platform == "darwin":
        expected_home = user_home / "Library" / "Application Support" / "TradingCodex"
    else:
        xdg_data = root / "XDG Data With Spaces"
        xdg_cache = root / "XDG Cache With Spaces"
        env["XDG_DATA_HOME"] = str(xdg_data)
        env["XDG_CACHE_HOME"] = str(xdg_cache)
        expected_home = xdg_data / "tradingcodex"
    env["TRADINGCODEX_DISABLE_LATEST_RELEASE_CHECK"] = "1"
    return env, expected_home.resolve(strict=False)


def generated_git_command_from_hook(path: Path) -> Path:
    module = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    values: list[str] = []
    for node in module.body:
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Constant):
            continue
        if not isinstance(node.value.value, str):
            continue
        if any(isinstance(target, ast.Name) and target.id == "GENERATED_GIT_COMMAND" for target in node.targets):
            values.append(node.value.value)
    if len(values) != 1:
        raise AssertionError(f"expected exactly one generated Git command in {path}, found {len(values)}")
    command = Path(values[0])
    assert command.is_absolute(), f"generated Git command is not absolute: {command}"
    assert command.is_file(), f"generated Git command is missing: {command}"
    assert os.access(command, os.X_OK), f"generated Git command is not executable: {command}"
    if sys.platform == "darwin":
        assert os.path.normcase(os.path.abspath(command)) != os.path.normcase("/usr/bin/git")
    return command


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--wheel-dir", type=Path, required=True)
    args = parser.parse_args()
    wheels = sorted(args.wheel_dir.resolve().glob("*.whl"))
    if len(wheels) != 1:
        raise SystemExit(f"expected exactly one wheel in {args.wheel_dir}, found {len(wheels)}")

    with tempfile.TemporaryDirectory(prefix="tradingcodex-native-wheel-") as temporary:
        root = Path(temporary).resolve()
        wheel_dir = root / "Wheel Package With Spaces"
        wheel_dir.mkdir()
        wheel = wheel_dir / wheels[0].name
        shutil.copy2(wheels[0], wheel)
        environment, expected_home = platform_environment(root)
        virtualenv = root / "Clean Wheel Environment"
        venv.EnvBuilder(with_pip=True).create(virtualenv)
        scripts = virtualenv / ("Scripts" if os.name == "nt" else "bin")
        python = scripts / ("python.exe" if os.name == "nt" else "python")
        tcx = scripts / ("tcx.exe" if os.name == "nt" else "tcx")
        run([str(python), "-m", "pip", "install", "--disable-pip-version-check", str(wheel)], cwd=root, env=environment)
        metadata = run(
            [str(python), "-c", "from importlib.metadata import version; from tradingcodex_service import __version__; assert version('tradingcodex') == __version__; print(__version__)"],
            cwd=root,
            env=environment,
        ).stdout.strip()

        initial_home = json.loads(run([str(tcx), "home", "status", "--json"], cwd=root, env=environment).stdout)
        assert initial_home["home"] == str(expected_home)
        assert initial_home["home_source"] == "platform_default"
        run([str(tcx), "home", "check"], cwd=root, env=environment)

        workspace = root / "Workspace With Spaces"
        run(
            [str(tcx), "attach", str(workspace), "--from", str(wheel)],
            cwd=root,
            env=environment,
        )
        lock = json.loads((workspace / ".tradingcodex" / "generated" / "module-lock.json").read_text(encoding="utf-8"))
        assert lock["tradingcodex_home"] == str(expected_home)
        assert lock["home_source"] == "platform_default"
        assert lock["tradingcodex_package_spec"] == "local-explicit"
        assert (workspace / "tcx").is_file() and (workspace / "tcx.cmd").is_file()
        assert (workspace / "tcx-calc").is_file() and (workspace / "tcx-calc.cmd").is_file()
        assert str(wheel) not in (workspace / "tcx").read_text(encoding="utf-8")
        cmd_text = (workspace / "tcx.cmd").read_text(encoding="utf-8")
        assert 'set "TRADINGCODEX_PACKAGE_SPEC="' in cmd_text
        assert str(wheel) not in cmd_text
        assert 'set "TRADINGCODEX_WORKSPACE_ROOT=%TRADINGCODEX_ROOT%"' in cmd_text

        config_paths = [workspace / ".codex" / "config.toml", *sorted((workspace / ".codex" / "agents").glob("*.toml"))]
        configs = [tomllib.loads(path.read_text(encoding="utf-8")) for path in config_paths]
        assert len(configs) == 10
        assert configs[0]["mcp_servers"]["tradingcodex"]["env"]["TRADINGCODEX_HOME"] == str(expected_home)
        assert configs[0]["mcp_servers"]["tradingcodex"]["env"]["TRADINGCODEX_HOME_SOURCE"] == "platform_default"
        assert configs[0]["default_permissions"] == "trading-research"
        assert configs[0]["features"]["network_proxy"] is True
        assert "sandbox_mode" not in configs[0]
        assert "sandbox_workspace_write" not in configs[0]
        permissions = configs[0]["permissions"]
        assert set(permissions) == {"trading-research", "trading-build"}
        assert permissions["trading-research"]["extends"] == ":workspace"
        assert permissions["trading-build"]["extends"] == ":workspace"
        research_filesystem = permissions["trading-research"]["filesystem"]
        build_filesystem = permissions["trading-build"]["filesystem"]
        scratch = configs[0]["shell_environment_policy"]["set"]["TRADINGCODEX_SCRATCH"]
        provider_sources = Path(scratch) / "provider-sources"
        assert provider_sources.is_dir()
        assert not provider_sources.is_symlink()
        if sys.platform == "darwin":
            scratch_display = (
                Path(environment["HOME"])
                / "Library"
                / "Caches"
                / "TradingCodex"
                / "scratch-v1"
                / str(lock["workspace_id"])
            ).absolute()
            scratch_resolved = scratch_display.resolve(strict=False)
            assert scratch == str(scratch_resolved)
            if str(scratch_display) != str(scratch_resolved):
                assert research_filesystem[str(scratch_display)] == "write"
                assert build_filesystem[str(scratch_display)] == "write"
        research_workspace = research_filesystem[":workspace_roots"]
        assert research_workspace["."] == "write"
        assert research_workspace[".git"] == "read"
        assert research_workspace[".gitignore"] == "read"
        assert ".codex/proxy" not in research_workspace
        assert research_workspace[".agents"] == "read"
        assert research_workspace["AGENTS.md"] == "read"
        assert research_workspace["tcx"] == "read"
        assert research_workspace["tcx.cmd"] == "read"
        assert research_workspace["tcx-calc"] == "read"
        assert research_workspace["tcx-calc.cmd"] == "read"
        calculation_roots = [
            path
            for path, permission in research_filesystem.items()
            if isinstance(path, str)
            and "calculation" in path.casefold()
            and permission == "read"
        ]
        assert len(calculation_roots) == 1
        assert build_filesystem[calculation_roots[0]] == "deny"
        assert research_workspace["trading"] == "read"
        assert research_workspace[".tradingcodex"] == "deny"
        assert ".tradingcodex/cli.py" not in research_workspace
        assert build_filesystem[":workspace_roots"][".tradingcodex/cli.py"] == "read"
        assert build_filesystem[":workspace_roots"][".tradingcodex/workspace.json"] == "read"
        assert build_filesystem[":workspace_roots"]["."] == "write"
        assert {"manage_strategy", "manage_investment_brain"}.issubset(
            configs[0]["mcp_servers"]["tradingcodex"]["enabled_tools"]
        )
        assert research_filesystem[scratch] == "write"
        assert build_filesystem[scratch] == "write"
        assert research_filesystem[":tmpdir"] == "deny"
        assert research_filesystem[":slash_tmp"] == "deny"
        assert build_filesystem[":tmpdir"] == "deny"
        assert build_filesystem[":slash_tmp"] == "deny"
        assert configs[0]["shell_environment_policy"]["set"]["TMPDIR"] == scratch
        assert configs[0]["shell_environment_policy"]["set"]["TEMP"] == scratch
        assert configs[0]["shell_environment_policy"]["set"]["TMP"] == scratch
        assert configs[0]["shell_environment_policy"]["set"]["GIT_CONFIG_GLOBAL"] == os.devnull
        assert configs[0]["shell_environment_policy"]["set"]["GIT_CONFIG_SYSTEM"] == os.devnull
        git_overrides = (
            ("core.hooksPath", os.devnull), ("core.fsmonitor", "false"), ("core.askPass", ""),
            ("credential.helper", ""), ("credential.interactive", "never"), ("http.extraHeader", ""),
            ("http.version", "HTTP/1.1"), ("http.cookieFile", ""), ("http.saveCookies", "false"),
            ("http.followRedirects", "false"), ("http.sslVerify", "true"), ("protocol.allow", "never"),
            ("protocol.https.allow", "always"), ("protocol.http.allow", "never"),
            ("protocol.ssh.allow", "never"), ("protocol.git.allow", "never"),
            ("protocol.file.allow", "never"), ("protocol.ext.allow", "never"),
        )
        shell_set = configs[0]["shell_environment_policy"]["set"]
        assert shell_set["GIT_CONFIG_COUNT"] == str(len(git_overrides))
        assert shell_set["GIT_CEILING_DIRECTORIES"] == scratch
        assert shell_set["GIT_PROTOCOL_FROM_USER"] == "0"
        for index, (key, value) in enumerate(git_overrides):
            assert shell_set[f"GIT_CONFIG_KEY_{index}"] == key
            assert shell_set[f"GIT_CONFIG_VALUE_{index}"] == value
        assert configs[0]["shell_environment_policy"]["set"]["GIT_OPTIONAL_LOCKS"] == "0"
        assert configs[0]["shell_environment_policy"]["set"]["GIT_PAGER"] == "cat"
        assert configs[0]["shell_environment_policy"]["set"]["GIT_TERMINAL_PROMPT"] == "0"
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
        }.issubset(configs[0]["shell_environment_policy"]["include_only"])
        assert {
            value
            for index in range(len(git_overrides))
            for value in (f"GIT_CONFIG_KEY_{index}", f"GIT_CONFIG_VALUE_{index}")
        }.issubset(configs[0]["shell_environment_policy"]["include_only"])
        shell_visible = set(configs[0]["shell_environment_policy"]["include_only"]) | set(
            configs[0]["shell_environment_policy"]["set"]
        )
        assert {
            "TRADINGCODEX_HOME",
            "TRADINGCODEX_HOME_SOURCE",
            "TRADINGCODEX_DB_NAME",
            "TRADINGCODEX_PYTHON",
            "TRADINGCODEX_SERVICE_ADDR",
            "TRADINGCODEX_WORKSPACE_ROOT",
        }.isdisjoint(shell_visible)
        user_home = Path(environment["USERPROFILE" if os.name == "nt" else "HOME"]).resolve()
        assert str(user_home) not in research_filesystem
        assert str(user_home) not in build_filesystem
        assert research_filesystem["~/.codex"] == "deny"
        assert research_filesystem["~/.codex/proxy"] == "read"
        assert research_filesystem["~/.codex/packages/standalone"] == "read"
        codex_home = (user_home / ".codex").resolve()
        assert research_filesystem[str(codex_home)] == "deny"
        assert research_filesystem[str(codex_home / "proxy")] == "read"
        assert research_filesystem[str(codex_home / "packages" / "standalone")] == "read"
        assert build_filesystem[str(codex_home)] == "deny"
        assert build_filesystem[str(codex_home / "proxy")] == "read"
        assert build_filesystem["~/.ssh"] == "deny"
        assert build_filesystem["~/.codex/packages/standalone"] == "read"
        assert research_filesystem[str(expected_home)] == "deny"
        assert build_filesystem[str(expected_home)] == "deny"
        assert configs[0]["web_search"] == "live"
        assert permissions["trading-research"]["network"]["enabled"] is True
        assert permissions["trading-research"]["network"]["allow_local_binding"] is False
        assert permissions["trading-build"]["network"] == {
            "enabled": True,
            "mode": "full",
            "allow_local_binding": False,
            "allow_upstream_proxy": False,
            "dangerously_allow_all_unix_sockets": False,
            "domains": {"*": "allow"},
        }
        attached_python = Path(configs[0]["mcp_servers"]["tradingcodex"]["command"])
        assert attached_python.is_file()
        assert attached_python.is_relative_to(expected_home / "runtime" / "python")
        assert research_filesystem[str(attached_python)] == "deny"
        assert build_filesystem[str(attached_python.parent.parent)] == "read"
        assert build_filesystem[str(attached_python)] == "read"
        for config in configs:
            assert "sandbox_mode" not in config
            mcp = config["mcp_servers"]["tradingcodex"]
            assert Path(mcp["command"]).absolute() == attached_python.absolute()
            assert mcp["args"] == ["-m", "tradingcodex_cli", "mcp", "stdio"]
            assert mcp["cwd"] == str(workspace.resolve())
            assert mcp["env"]["TRADINGCODEX_WORKSPACE_ROOT"] == str(workspace.resolve())
            assert "TRADINGCODEX_MCP_PACKAGE_SPEC" not in mcp["env"]
            assert "PYTHONPATH" not in mcp["env"]

        run(
            launcher_argv(workspace, "update", "--skip-refresh", "--no-doctor"),
            cwd=root,
            env=environment,
        )
        for path in config_paths:
            updated_mcp = tomllib.loads(path.read_text(encoding="utf-8"))["mcp_servers"]["tradingcodex"]
            updated_python = Path(updated_mcp["command"])
            assert updated_python.absolute() == attached_python.absolute()
            assert updated_python.is_file()
            assert "builds-v0/.tmp" not in updated_python.as_posix()

        config_yaml = json.loads(
            run(
                [
                    str(python),
                    "-c",
                    "import json,pathlib,sys,yaml; print(json.dumps(yaml.safe_load(pathlib.Path(sys.argv[1]).read_text(encoding='utf-8'))))",
                    str(workspace / ".tradingcodex" / "config.yaml"),
                ],
                cwd=root,
                env=environment,
            ).stdout
        )
        assert config_yaml["service"]["default_db"] == str(expected_home / "state" / "tradingcodex.sqlite3")
        hooks = json.loads((workspace / ".codex" / "hooks.json").read_text(encoding="utf-8"))
        root_codex_config = tomllib.loads((workspace / ".codex" / "config.toml").read_text(encoding="utf-8"))
        assert root_codex_config["features"]["hooks"] is True
        expected_hook = r".\tcx.cmd __hook session-start" if os.name == "nt" else "./tcx __hook session-start"
        assert hooks["hooks"]["SessionStart"][0]["hooks"][0]["command"] == expected_hook
        generated_git_command_from_hook(workspace / ".codex/hooks/tradingcodex_hook.py")
        shell_hook = run(
            launcher_argv(workspace, "__hook", "session-start"),
            cwd=workspace,
            env=environment,
            input_text="{}\n",
        )
        assert json.loads(shell_hook.stdout)["hookSpecificOutput"]["hookEventName"] == "SessionStart"

        other_cwd = root / "Other Working Directory"
        other_cwd.mkdir()
        run(launcher_argv(workspace, "doctor"), cwd=other_cwd, env=environment)
        scratch = Path(root_codex_config["shell_environment_policy"]["set"]["TRADINGCODEX_SCRATCH"])
        calculation_script = scratch / "platform-smoke.py"
        calculation_script.write_text(
            """from decimal import Decimal
import importlib.util
import os
import numpy
import numpy_financial
import pandas
import pyarrow
import scipy
import statsmodels
print(Decimal('100') * Decimal('1.1') ** 2)
print(','.join((numpy.__version__, pandas.__version__, scipy.__version__, statsmodels.__version__, numpy_financial.__version__, pyarrow.__version__)))
print(importlib.util.find_spec('django'))
print(importlib.util.find_spec('tradingcodex_service'))
print(os.environ.get('TRADINGCODEX_DB_NAME'))
print(os.environ.get('BROKER_API_SECRET'))
print(os.environ['TMPDIR'])
""",
            encoding="utf-8",
        )
        calculation = run(
            calculation_launcher_argv(workspace, calculation_script.name),
            cwd=workspace,
            env={**environment, "BROKER_API_SECRET": "must-not-leak"},
        )
        assert calculation.stdout.splitlines() == [
            "121.00",
            "2.3.5,2.3.3,1.16.3,0.14.6,1.0.0,25.0.0",
            "None",
            "None",
            "None",
            "None",
            str(scratch),
        ]
        technical_mcp = next(
            config["mcp_servers"]["tradingcodex"]
            for config in configs[1:]
            if config["mcp_servers"]["tradingcodex"]["env"].get(
                "TRADINGCODEX_MCP_PRINCIPAL"
            )
            == "technical-analyst"
        )
        runtime_root = Path(
            technical_mcp["env"]["TRADINGCODEX_CALCULATION_RUNTIME_ROOT"]
        )
        runtime_manifest = runtime_root / "runtime-manifest.json"
        assert runtime_manifest.is_file()
        prepared_script = scratch / "platform-prepared.py"
        prepared_script.write_text(
            """tcx_emit_result({
    'metrics': [{'name': 'discounted_value', 'value': 100.0, 'value_type': 'number', 'unit': 'USD', 'currency': 'USD', 'precision': 2}],
    'diagnostics': {'matrix_smoke': True},
    'assumptions': ['deterministic constant'],
    'warnings': [],
    'output_files': [],
})
""",
            encoding="utf-8",
        )
        prepare_args = {
            "calculation_type": "platform_matrix_smoke",
            "calculation_version": "1",
            "script_name": prepared_script.name,
            "workflow_run_id": "platform-wheel-prepared",
            "knowledge_cutoff": "2025-01-01T00:00:00Z",
            "principal_id": "technical-analyst",
            "inputs": [],
            "parameters": {"constant": 100},
            "output_schema": {
                "metrics": [
                    {
                        "name": "discounted_value",
                        "value_type": "number",
                        "unit": "USD",
                        "currency": "USD",
                        "precision": 2,
                    }
                ]
            },
            "outputs": [],
        }
        prepare_code = (
            "import json,sys; from pathlib import Path; "
            "from tradingcodex_service.application.calculations import prepare_calculation; "
            "print(json.dumps(prepare_calculation(Path(sys.argv[1]), json.loads(sys.argv[2]), "
            "scratch_root=Path(sys.argv[3]), runtime_manifest_path=Path(sys.argv[4]))))"
        )
        prepared = json.loads(
            run(
                [
                    str(attached_python),
                    "-c",
                    prepare_code,
                    str(workspace),
                    json.dumps(prepare_args),
                    str(scratch),
                    str(runtime_manifest),
                ],
                cwd=workspace,
                env=environment,
            ).stdout
        )
        assert prepared["status"] == "prepared"
        run(
            calculation_launcher_argv(workspace, prepared_script.name),
            cwd=workspace,
            env=environment,
        )
        record_code = (
            "import json,sys; from pathlib import Path; "
            "from tradingcodex_service.application.calculations import record_calculation_run; "
            "print(json.dumps(record_calculation_run(Path(sys.argv[1]), json.loads(sys.argv[2]), "
            "scratch_root=Path(sys.argv[3]), runtime_manifest_path=Path(sys.argv[4]))))"
        )
        recorded = json.loads(
            run(
                [
                    str(attached_python),
                    "-c",
                    record_code,
                    str(workspace),
                    json.dumps(
                        {
                            "calculation_spec_id": prepared["calculation_spec_id"],
                            "workflow_run_id": prepare_args["workflow_run_id"],
                            "result_file": prepared["result_file"],
                            "principal_id": "technical-analyst",
                        }
                    ),
                    str(scratch),
                    str(runtime_manifest),
                ],
                cwd=workspace,
                env=environment,
            ).stdout
        )
        assert recorded["artifact"]["status"] == "succeeded"
        assert recorded["artifact"]["metrics"][0]["value"] == 100.0

        reuse_args = {**prepare_args, "workflow_run_id": "platform-wheel-reuse"}
        reused = json.loads(
            run(
                [
                    str(attached_python),
                    "-c",
                    prepare_code,
                    str(workspace),
                    json.dumps(reuse_args),
                    str(scratch),
                    str(runtime_manifest),
                ],
                cwd=workspace,
                env=environment,
            ).stdout
        )
        assert reused["status"] == "reused"
        assert reused["original_run_id"] == recorded["artifact"]["calculation_run_id"]
        db_status = json.loads(run(launcher_argv(workspace, "db", "status"), cwd=other_cwd, env=environment).stdout)
        assert db_status["home"] == str(expected_home)
        assert db_status["home_source"] == "platform_default"
        db_path = run(launcher_argv(workspace, "db", "path"), cwd=other_cwd, env=environment).stdout.strip()
        assert db_path == str(expected_home / "state" / "tradingcodex.sqlite3")
        hook = run(
            launcher_argv(workspace, "__hook", "user-prompt-submit"),
            cwd=other_cwd,
            env=environment,
            input_text='{"prompt":"Analyze NVDA. No order, no trading."}\n',
        )
        assert json.loads(hook.stdout)["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"
        mcp = run(
            launcher_argv(workspace, "mcp", "stdio"),
            cwd=other_cwd,
            env=environment,
            input_text='{"jsonrpc":"2.0","id":1,"method":"tools/list"}\n',
        )
        assert json.loads(mcp.stdout)["result"]["tools"]
        port = free_loopback_port()
        addr = f"127.0.0.1:{port}"
        try:
            try:
                run(launcher_argv(workspace, "service", "ensure", addr), cwd=other_cwd, env=environment)
            except RuntimeError as exc:
                try:
                    diagnostic = json.loads(
                        run(
                            launcher_argv(workspace, "service", "status", addr, "--json"),
                            cwd=other_cwd,
                            env=environment,
                        ).stdout
                    )
                    safe_diagnostic = {
                        key: diagnostic.get(key)
                        for key in ("reachable", "compatible", "ready", "issue", "reason_codes", "next_action", "log")
                    }
                except Exception as diagnostic_exc:
                    safe_diagnostic = {"diagnostic_error": type(diagnostic_exc).__name__}
                raise RuntimeError(
                    f"{exc}\nservice diagnostic:\n{json.dumps(safe_diagnostic, ensure_ascii=False)}"
                ) from exc
            service = json.loads(run(launcher_argv(workspace, "service", "status", addr, "--json"), cwd=other_cwd, env=environment).stdout)
            assert service["compatible"] and service["ready"]
            workbench_url = f"http://{addr}/"
            workbench = fetch_text(workbench_url)
            assert '<div id="root"></div>' in workbench
            try:
                fetch_text(urljoin(workbench_url, "skills/"))
            except HTTPError as exc:
                assert exc.code == 404
            else:
                raise AssertionError("retired SPA paths must return 404")
            assets = re.findall(r'(?:href|src)="([^"]*tradingcodex_web/[^"]+)"', workbench)
            assert any(asset.endswith(".js") for asset in assets)
            assert any(asset.endswith(".css") for asset in assets)
            for asset in assets:
                assert re.fullmatch(
                    r"/static/tradingcodex_web/assets/[^/]+-[A-Za-z0-9_-]{8,}\.(?:js|css)",
                    asset,
                ), asset
                assert fetch_text(urljoin(workbench_url, asset))
        finally:
            status = run(launcher_argv(workspace, "service", "status", addr, "--json"), cwd=other_cwd, env=environment)
            if json.loads(status.stdout)["reachable"]:
                run(launcher_argv(workspace, "service", "stop", addr), cwd=other_cwd, env=environment)

        print(json.dumps({"status": "ok", "platform": sys.platform, "version": metadata, "home": str(expected_home)}))


if __name__ == "__main__":
    main()

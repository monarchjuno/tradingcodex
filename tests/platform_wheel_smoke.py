from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import textwrap
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
        "CODEX_HOME",
        "DJANGO_SETTINGS_MODULE",
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
        env["XDG_DATA_HOME"] = str(xdg_data)
        expected_home = xdg_data / "tradingcodex"
    env["TRADINGCODEX_DISABLE_LATEST_RELEASE_CHECK"] = "1"
    return env, expected_home.resolve(strict=False)


def external_mcp_fixture(python: Path, cwd: Path, env: dict[str, str]) -> None:
    env = {**env, "DJANGO_SETTINGS_MODULE": "tradingcodex_service.settings", "TRADINGCODEX_WORKSPACE_ROOT": str(cwd)}
    script = textwrap.dedent(
        """
        import json
        import sys
        from types import SimpleNamespace
        import django
        django.setup()
        from apps.mcp.services import _stdio_mcp_rpc

        server = '''
        import json, sys
        for line in sys.stdin:
            request = json.loads(line)
            if 'id' not in request:
                continue
            method = request.get('method')
            result = {'protocolVersion':'2025-03-26','serverInfo':{'name':'native-wheel-fixture','version':'1'}} if method == 'initialize' else {'tools':[]}
            print(json.dumps({'jsonrpc':'2.0','id':request['id'],'result':result}), flush=True)
        '''
        router = SimpleNamespace(name='native-wheel-fixture', command=sys.executable, args=['-u','-c',server], env={}, credential_ref='')
        responses = _stdio_mcp_rpc(router, ['initialize', 'tools/list'], timeout=10)
        assert responses['initialize']['result']['serverInfo']['name'] == 'native-wheel-fixture'
        assert responses['tools/list']['result']['tools'] == []
        print(json.dumps({'external_mcp_stdio':'ok'}))
        """
    )
    run([str(python), "-c", script], cwd=cwd, env=env)


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
        research_workspace = research_filesystem[":workspace_roots"]
        assert research_workspace["."] == "write"
        assert research_workspace[".git"] == "read"
        assert research_workspace[".gitignore"] == "read"
        assert ".codex/proxy" not in research_workspace
        assert research_workspace[".agents"] == "read"
        assert research_workspace["AGENTS.md"] == "read"
        assert research_workspace["tcx"] == "read"
        assert research_workspace["tcx.cmd"] == "read"
        assert research_workspace["trading"] == "read"
        assert research_workspace[".tradingcodex"] == "deny"
        assert ".tradingcodex/cli.py" not in research_workspace
        assert build_filesystem[":workspace_roots"][".tradingcodex/cli.py"] == "read"
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
        assert permissions["trading-research"]["network"]["enabled"] is True
        assert permissions["trading-research"]["network"]["allow_local_binding"] is False
        assert permissions["trading-build"]["network"]["enabled"] is False
        attached_python = Path(configs[0]["mcp_servers"]["tradingcodex"]["command"])
        assert attached_python.is_file()
        assert attached_python.is_relative_to(expected_home / "runtime" / "python")
        assert research_filesystem[str(attached_python)] == "deny"
        assert build_filesystem[str(attached_python)] == "deny"
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
        external_mcp_fixture(python, workspace, environment)

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

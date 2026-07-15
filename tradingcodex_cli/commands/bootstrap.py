from __future__ import annotations

import argparse
import hashlib
import os
import tomllib
from pathlib import Path

from tradingcodex_cli.commands.doctor import doctor
from tradingcodex_cli.commands.utils import print_json
from tradingcodex_cli.generator import (
    bootstrap_workspace,
    repo_root,
    validate_generated_workspace,
)
from tradingcodex_cli.package_source import (
    EXECUTABLE_SOURCE_ENV,
    LOCAL_EXECUTABLE_SOURCE_KIND,
    PACKAGE_SOURCE_KIND_ENV,
    PERSISTENT_EXECUTABLE_SOURCE_KIND,
    canonical_executable_source,
    configured_executable_source,
    executable_source_is_local,
    runtime_has_direct_source,
)
from tradingcodex_cli.service_autostart import configured_service_addr
from tradingcodex_service.application.common import workspace_launcher_command
from tradingcodex_service.application.runtime import resolve_tradingcodex_home


PROGRAM_NAME = "tcx"
DEVELOPMENT_SOURCE_ENV = "_TRADINGCODEX_DEV_SOURCE_ROOT"
DEVELOPMENT_SERVICE_PORT_BASE = 20000
DEVELOPMENT_SERVICE_PORT_SPAN = 10000


def attach(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(prog=f"{PROGRAM_NAME} attach", allow_abbrev=False)
    parser.add_argument("project_dir", nargs="?", default=".")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--from", dest="package_spec")
    source.add_argument(
        "--dev",
        action="store_true",
        help="bootstrap from the source checkout running this command",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    target = Path(args.project_dir).resolve()
    if args.dev:
        development_source = _development_source_root()
        _configure_development_runtime(development_source)
        _configure_bootstrap_source(development_source)
    else:
        _configure_bootstrap_source(args.package_spec)
    result = bootstrap_workspace(target, dry_run=args.dry_run)
    if args.dry_run:
        print(f"TradingCodex attach dry run: {result['target_dir']}")
        print(f"Modules: {', '.join(result['modules'])}")
        return
    _finish_bootstrap(result)
    print(f"TradingCodex workspace attached: {result['target_dir']}")
    print(f"Modules: {', '.join(result['modules'])}")
    print_workspace_summary(Path(result["target_dir"]))
    print("\nNext:")
    print(f"  {_workspace_launcher()} doctor")
    print("  Open this workspace in Codex and trust it so project-scoped TradingCodex MCP is loaded.")


def update(argv: list[str]) -> None:
    if argv and argv[0] == "status":
        update_status(argv[1:])
        return
    parser = argparse.ArgumentParser(prog=f"{PROGRAM_NAME} update", allow_abbrev=False)
    parser.add_argument("project_dir", nargs="?", default=".")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--from", dest="package_spec")
    source.add_argument(
        "--dev",
        action="store_true",
        help="refresh from the source checkout running this command",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-doctor", action="store_true", help=f"skip {_workspace_launcher()} doctor after update")
    parser.add_argument("--skip-refresh", action="store_true", help="wrapper-only: use the recorded Python package instead of refreshing through uvx")
    args = parser.parse_args(argv)
    target = Path(args.project_dir).resolve()
    validated_workspace = validate_generated_workspace(target)
    existing_lock = validated_workspace["module_lock"]
    if args.dev:
        development_source = _development_source_root()
        _configure_development_runtime(
            development_source,
            existing_lock=existing_lock,
        )
        explicit_source = development_source
    else:
        _configure_recorded_update_runtime(target, existing_lock)
        explicit_source = args.package_spec
    _configure_bootstrap_source(
        explicit_source,
        allow_recorded=(
            args.skip_refresh
            or os.environ.get("TRADINGCODEX_UPDATE_SKIP_REFRESH") == "1"
        ),
    )
    result = bootstrap_workspace(target, dry_run=args.dry_run, update=True)
    if args.dry_run:
        print(f"TradingCodex update dry run: {result['target_dir']}")
        print(f"Modules: {', '.join(result['modules'])}")
        return
    _finish_bootstrap(result)
    print(f"TradingCodex workspace updated: {result['target_dir']}")
    print(f"Modules: {', '.join(result['modules'])}")
    print_workspace_summary(Path(result["target_dir"]))
    if args.no_doctor:
        print("\nNext:")
        print(f"  {_workspace_launcher()} doctor")
        print("  Fully quit and restart Codex, then start from a new thread so project MCP config is reloaded.")
        return
    print("\nRunning doctor:")
    doctor(Path(result["target_dir"]), "all")
    print("\nNext:")
    print("  Fully quit and restart Codex, then start from a new thread so project MCP config is reloaded.")


def update_status(argv: list[str]) -> None:
    from tradingcodex_cli.startup_status import build_update_status

    parser = argparse.ArgumentParser(prog=f"{PROGRAM_NAME} update status")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("project_dir", nargs="?", default=".")
    args = parser.parse_args(argv)
    root = configure_workspace_env(Path(args.project_dir).resolve(), force=True)
    status = build_update_status(root, check_latest_release=True)
    if args.json:
        print_json(status)
        return
    print(f"Workspace version: {status['workspace_version']}")
    print(f"Installed package: {status['installed_version']}")
    print(f"Latest release: {status['latest_release_version']} ({status['latest_release_status']})")
    print(f"Update available: {status['update_available']}")
    print(f"Self-update allowed: {status['can_self_update']}")
    if status.get("recommended_action"):
        print(f"Next: {status['recommended_action']}")


def _configure_bootstrap_source(explicit_source: str | None, *, allow_recorded: bool = False) -> None:
    environment_source = str(os.environ.get(EXECUTABLE_SOURCE_ENV) or "")
    recorded_kind = str(os.environ.get(PACKAGE_SOURCE_KIND_ENV) or "")
    if explicit_source is None and not environment_source:
        if allow_recorded and recorded_kind in {
            LOCAL_EXECUTABLE_SOURCE_KIND,
            PERSISTENT_EXECUTABLE_SOURCE_KIND,
        }:
            return
        if runtime_has_direct_source():
            configured_executable_source(None, require_explicit=True)
    source = canonical_executable_source(
        configured_executable_source(explicit_source),
        require_local_exists=True,
    )
    os.environ[EXECUTABLE_SOURCE_ENV] = source
    os.environ[PACKAGE_SOURCE_KIND_ENV] = (
        LOCAL_EXECUTABLE_SOURCE_KIND
        if executable_source_is_local(source)
        else PERSISTENT_EXECUTABLE_SOURCE_KIND
    )


def _development_source_root() -> str:
    declared = str(os.environ.get(DEVELOPMENT_SOURCE_ENV) or "").strip()
    root = Path(declared).expanduser().resolve() if declared else repo_root().resolve()
    required = (
        root / "pyproject.toml",
        root / "tradingcodex_cli" / "__main__.py",
        root / "tradingcodex_service" / "version.py",
        root / "workspace_templates" / "modules",
    )
    if not all(path.exists() for path in required):
        raise ValueError(
            "--dev requires tcx to run directly from a TradingCodex source checkout; "
            "run `uv run python -m tradingcodex_cli ... --dev` in the checkout, "
            "or use --from <package-spec>"
        )
    return str(root)


def _configure_development_runtime(
    source_root: str,
    *,
    existing_lock: dict[str, object] | None = None,
) -> None:
    if existing_lock and existing_lock.get("tradingcodex_package_spec") != "local-explicit":
        raise ValueError(
            "--dev update requires a workspace already attached from an explicit local source; "
            "use a separate development workspace"
        )
    if not str(os.environ.get("TRADINGCODEX_HOME") or "").strip():
        if existing_lock:
            os.environ["TRADINGCODEX_HOME"] = str(existing_lock["tradingcodex_home"])
            os.environ["TRADINGCODEX_HOME_SOURCE"] = str(existing_lock["home_source"])
        else:
            default_home = resolve_tradingcodex_home().home
            if not isinstance(default_home, Path):
                raise ValueError("TradingCodex development home did not resolve to a native path")
            source_key = hashlib.sha256(
                os.path.normcase(str(Path(source_root).resolve())).encode("utf-8")
            ).hexdigest()[:12]
            os.environ["TRADINGCODEX_HOME"] = str(
                default_home / "development" / f"source-{source_key}"
            )
            os.environ["TRADINGCODEX_HOME_SOURCE"] = "environment_override"
    if (
        existing_lock
        and existing_lock.get("db_source") == "environment_override"
        and not str(os.environ.get("TRADINGCODEX_DB_NAME") or "").strip()
    ):
        os.environ["TRADINGCODEX_DB_NAME"] = str(existing_lock["tradingcodex_db_path"])
    if not str(os.environ.get("TRADINGCODEX_SERVICE_ADDR") or "").strip():
        resolution = resolve_tradingcodex_home()
        if not isinstance(resolution.home, Path):
            raise ValueError("TradingCodex development home did not resolve to a native path")
        os.environ["TRADINGCODEX_SERVICE_ADDR"] = _development_service_addr(resolution.home)


def _configure_recorded_update_runtime(
    target: Path,
    existing_lock: dict[str, object],
) -> None:
    """Restore explicit release-workspace runtime identity for package-runner updates.

    ``uvx ... tcx update`` does not execute the generated workspace wrapper, so
    custom home, database, and service-address values would otherwise fall back
    to platform defaults. Explicit process values remain authoritative so users
    can deliberately move a workspace to a new runtime identity.
    """

    if (
        existing_lock.get("home_source") == "environment_override"
        and not str(os.environ.get("TRADINGCODEX_HOME") or "").strip()
    ):
        os.environ["TRADINGCODEX_HOME"] = str(existing_lock["tradingcodex_home"])
        os.environ["TRADINGCODEX_HOME_SOURCE"] = "environment_override"
    if (
        existing_lock.get("db_source") == "environment_override"
        and not str(os.environ.get("TRADINGCODEX_DB_NAME") or "").strip()
    ):
        os.environ["TRADINGCODEX_DB_NAME"] = str(existing_lock["tradingcodex_db_path"])
    if not str(os.environ.get("TRADINGCODEX_SERVICE_ADDR") or "").strip():
        recorded_addr = _recorded_service_addr(target)
        if recorded_addr:
            os.environ["TRADINGCODEX_SERVICE_ADDR"] = recorded_addr


def _recorded_service_addr(target: Path) -> str:
    config_path = target / ".codex" / "config.toml"
    try:
        config = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return ""
    servers = config.get("mcp_servers")
    tradingcodex = servers.get("tradingcodex") if isinstance(servers, dict) else None
    environment = tradingcodex.get("env") if isinstance(tradingcodex, dict) else None
    value = environment.get("TRADINGCODEX_SERVICE_ADDR") if isinstance(environment, dict) else None
    return str(value or "").strip()


def _development_service_addr(home: Path | str) -> str:
    identity = os.path.normcase(str(Path(home).expanduser().resolve(strict=False)))
    offset = int(hashlib.sha256(identity.encode("utf-8")).hexdigest()[:8], 16)
    port = DEVELOPMENT_SERVICE_PORT_BASE + (offset % DEVELOPMENT_SERVICE_PORT_SPAN)
    return f"127.0.0.1:{port}"


def service(argv: list[str]) -> None:
    sub = argv[0] if argv else "runserver"
    if sub == "status":
        from tradingcodex_cli.service_autostart import service_status

        args = argv[1:]
        json_output = "--json" in args
        addr = next((arg for arg in args if arg != "--json"), configured_service_addr())
        status = service_status(addr)
        if json_output:
            print_json(status)
            return
        state = "compatible" if status["compatible"] else "attention needed" if status["reachable"] else "not running"
        print(f"TradingCodex service status: {state}")
        print(f"URL: {status['url']}")
        print(f"Reachable: {status['reachable']}")
        print(f"Compatible: {status['compatible']}")
        print(f"Ready: {status.get('ready', False)}")
        if status.get("service"):
            print(f"Service: {status['service']}")
        if status.get("version") or status.get("package_version"):
            print(f"Version: service={status.get('version') or 'unknown'} package={status['package_version']}")
        if status.get("db_path") or status.get("expected_db_path"):
            print(f"DB: service={status.get('db_path') or 'unknown'} package={status['expected_db_path']}")
        if status.get("issue"):
            print(f"Issue: {status['issue']}")
        if status.get("log"):
            print(f"Log: {status['log']['path']} ({status['log']['size_bytes']} bytes, {status['log']['backup_count']} backups)")
            if status["log"].get("last_error"):
                print(f"Recent log error: {status['log']['last_error']}")
        print(f"Next: {status['next_action']}")
        return
    if sub == "ensure":
        from tradingcodex_cli.service_autostart import ensure_service_up, service_http_url

        root = configure_workspace_env(Path.cwd())
        addr = argv[1] if len(argv) > 1 else configured_service_addr()
        os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tradingcodex_service.settings")
        started = ensure_service_up(root, addr=addr)
        dashboard_url = service_http_url(addr)
        print(f"TradingCodex service {'started' if started else 'ready'} at {dashboard_url}")
        print(f"Health: {dashboard_url.rstrip('/')}/api/health/ready")
        return
    if sub == "stop":
        from tradingcodex_cli.service_autostart import service_http_url, stop_service

        addr = argv[1] if len(argv) > 1 else configured_service_addr()
        stopped = stop_service(addr)
        print(f"TradingCodex service {'stopped' if stopped else 'not running'} at {service_http_url(addr)}")
        return
    if sub != "runserver":
        raise ValueError(f"Usage: {PROGRAM_NAME} service runserver [addrport] [django runserver args]\n       {PROGRAM_NAME} service ensure [addrport]\n       {PROGRAM_NAME} service stop [addrport]\n       {PROGRAM_NAME} service status [addrport] [--json]")
    from django.core.management import execute_from_command_line
    from django.core.management.commands.runserver import Command as DjangoRunserverCommand
    from tradingcodex_cli.service_autostart import (
        NonResolvingWSGIServer,
        compatible_service_running,
        service_http_url,
    )

    configure_workspace_env(Path.cwd())
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tradingcodex_service.settings")
    runserver_args = argv[1:]
    if not runserver_args or runserver_args[0].startswith("-"):
        runserver_args = [configured_service_addr(), *runserver_args]
    addr = runserver_args[0]
    if compatible_service_running(addr):
        print(f"TradingCodex service already running at {service_http_url(addr)}")
        return
    DjangoRunserverCommand.server_cls = NonResolvingWSGIServer
    execute_from_command_line(["manage.py", "runserver", *runserver_args])


def configure_workspace_env(root: Path, *, force: bool = False) -> Path:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tradingcodex_service.settings")
    candidate = root.expanduser().resolve()
    if force:
        selected = candidate
    else:
        selected = _find_workspace_root(candidate)
        if selected is None:
            configured = str(os.environ.get("TRADINGCODEX_WORKSPACE_ROOT") or "").strip()
            selected = _find_workspace_root(Path(configured).expanduser().resolve()) if configured else None
        selected = selected or candidate
    os.environ["TRADINGCODEX_WORKSPACE_ROOT"] = str(selected)
    return selected


def _find_workspace_root(path: Path) -> Path | None:
    from tradingcodex_service.application.runtime import read_workspace_manifest

    for candidate in (path, *path.parents):
        if read_workspace_manifest(candidate):
            return candidate
    return None


def print_workspace_summary(root: Path) -> None:
    from tradingcodex_service.application.runtime import ensure_workspace_manifest, tradingcodex_db_path
    from tradingcodex_service.application.workspace_git import workspace_git_status

    manifest = ensure_workspace_manifest(root)
    git = workspace_git_status(root)
    profile = manifest["active_profile"]
    print(f"Workspace: {manifest['project_name']}")
    print(f"Workspace ID: {manifest['workspace_id']}")
    print(f"Paper Account Scope: {profile['label']} ({profile['portfolio_id']}/{profile['account_id']}/{profile['strategy_id']})")
    print(f"Central DB: {tradingcodex_db_path()}")
    print(f"MCP Scope: {manifest['mcp_scope']}")
    print(f"Execution Mode: {manifest['execution_mode']}")
    print(f"Git Root: {git['git_root']}")
    print(f"Workspace Dirty: {str(bool(git['git_dirty'])).lower()}")


def is_generated_workspace(path: Path) -> bool:
    try:
        validate_generated_workspace(path)
    except ValueError:
        return False
    return True


def _finish_bootstrap(result: dict[str, object]) -> None:
    root = configure_workspace_env(Path(str(result["target_dir"])), force=True)
    from tradingcodex_service.application.runtime import migrate_runtime_database, persist_workspace_context_if_available
    from tradingcodex_cli.startup_status import write_server_status_snapshot

    projected = {
        "TRADINGCODEX_HOME": str(result["tradingcodex_home"]),
        "TRADINGCODEX_HOME_SOURCE": str(result["home_source"]),
    }
    if result.get("db_source") == "environment_override":
        projected["TRADINGCODEX_DB_NAME"] = str(result["tradingcodex_db_path"])
    missing = object()
    previous = {key: os.environ.get(key, missing) for key in projected}
    try:
        os.environ.update(projected)
        migrate_runtime_database(root)
        persist_workspace_context_if_available(root)
        write_server_status_snapshot(root)
    finally:
        for key, value in previous.items():
            if value is missing:
                os.environ.pop(key, None)
            else:
                os.environ[key] = str(value)


def _workspace_launcher() -> str:
    return workspace_launcher_command()

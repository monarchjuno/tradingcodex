from __future__ import annotations

import argparse
import os
from pathlib import Path

from tradingcodex_cli.commands.doctor import doctor
from tradingcodex_cli.commands.utils import print_json
from tradingcodex_cli.generator import (
    DEFAULT_MODULE_IDS,
    bootstrap_workspace,
    load_module_registry,
    resolve_module_graph,
    templates_dir,
)
from tradingcodex_cli.service_autostart import DEFAULT_SERVICE_ADDR


PROGRAM_NAME = "tcx"


def init(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(prog=f"{PROGRAM_NAME} init")
    parser.add_argument("project_dir", nargs="?")
    parser.add_argument("--overwrite", action="store_true", help="overwrite files at matching generated workspace paths")
    parser.add_argument("--force", "-f", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--list-modules", action="store_true")
    args = parser.parse_args(argv)
    if args.list_modules:
        registry = load_module_registry(templates_dir())
        for module in resolve_module_graph(registry, DEFAULT_MODULE_IDS):
            print(f"{module.id}: {module.description}")
        return
    if not args.project_dir:
        parser.print_help()
        raise SystemExit(1)
    result = bootstrap_workspace(args.project_dir, force=args.overwrite or args.force, dry_run=args.dry_run)
    if args.dry_run:
        print(f"TradingCodex dry run: {result['targetDir']}")
        print(f"Modules: {', '.join(result['modules'])}")
        print(f"Capabilities: {', '.join(result['capabilities'])}")
        return
    _finish_bootstrap(result["targetDir"])
    print(f"TradingCodex workspace created: {result['targetDir']}")
    print(f"Modules: {', '.join(result['modules'])}")
    print_workspace_summary(Path(result["targetDir"]))
    print("\nNext:")
    print(f"  cd {result['targetDir']}")
    print("  ./tcx doctor")
    print(f"  Open the workspace in Codex and trust it; TradingCodex MCP will start the local dashboard service at http://{DEFAULT_SERVICE_ADDR}/")
    print("  Fully quit and restart Codex, then start from a new thread in this generated workspace so project MCP config is reloaded.")


def attach(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(prog=f"{PROGRAM_NAME} attach")
    parser.add_argument("project_dir", nargs="?", default=".")
    parser.add_argument("--overwrite", action="store_true", help="overwrite matching generated workspace files")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    target = Path(args.project_dir).resolve()
    force = args.overwrite or (target / ".tradingcodex" / "generated" / "module-lock.json").exists()
    result = bootstrap_workspace(target, force=force, dry_run=args.dry_run)
    if args.dry_run:
        print(f"TradingCodex attach dry run: {result['targetDir']}")
        print(f"Modules: {', '.join(result['modules'])}")
        return
    _finish_bootstrap(result["targetDir"])
    print(f"TradingCodex workspace attached: {result['targetDir']}")
    print(f"Modules: {', '.join(result['modules'])}")
    print_workspace_summary(Path(result["targetDir"]))
    print("\nNext:")
    print("  ./tcx doctor")
    print("  Open this workspace in Codex and trust it so project-scoped TradingCodex MCP is loaded.")


def update(argv: list[str]) -> None:
    if argv and argv[0] == "status":
        update_status(argv[1:])
        return
    parser = argparse.ArgumentParser(prog=f"{PROGRAM_NAME} update")
    parser.add_argument("project_dir", nargs="?", default=".")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-doctor", action="store_true", help="skip ./tcx doctor after update")
    parser.add_argument("--skip-refresh", action="store_true", help="wrapper-only: use the recorded Python package instead of refreshing through uvx")
    args = parser.parse_args(argv)
    target = Path(args.project_dir).resolve()
    if not is_generated_workspace(target):
        raise ValueError(f"Not a TradingCodex generated workspace: {target}. Use tcx attach for first-time setup.")
    result = bootstrap_workspace(target, force=True, dry_run=args.dry_run)
    if args.dry_run:
        print(f"TradingCodex update dry run: {result['targetDir']}")
        print(f"Modules: {', '.join(result['modules'])}")
        return
    _finish_bootstrap(result["targetDir"])
    print(f"TradingCodex workspace updated: {result['targetDir']}")
    print(f"Modules: {', '.join(result['modules'])}")
    print_workspace_summary(Path(result["targetDir"]))
    if args.no_doctor:
        print("\nNext:")
        print("  ./tcx doctor")
        print("  Fully quit and restart Codex, then start from a new thread so project MCP config is reloaded.")
        return
    print("\nRunning doctor:")
    doctor(Path(result["targetDir"]), "all")
    print("\nNext:")
    print("  Fully quit and restart Codex, then start from a new thread so project MCP config is reloaded.")


def update_status(argv: list[str]) -> None:
    from tradingcodex_cli.startup_status import build_update_status

    parser = argparse.ArgumentParser(prog=f"{PROGRAM_NAME} update status")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("project_dir", nargs="?", default=".")
    args = parser.parse_args(argv)
    root = configure_workspace_env(Path(args.project_dir).resolve(), force=True)
    status = build_update_status(root)
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


def service(argv: list[str]) -> None:
    sub = argv[0] if argv else "runserver"
    if sub == "status":
        from tradingcodex_cli.service_autostart import service_status

        args = argv[1:]
        json_output = "--json" in args
        addr = next((arg for arg in args if arg != "--json"), DEFAULT_SERVICE_ADDR)
        status = service_status(addr)
        if json_output:
            print_json(status)
            return
        state = "compatible" if status["compatible"] else "attention needed" if status["reachable"] else "not running"
        print(f"TradingCodex service status: {state}")
        print(f"URL: {status['url']}")
        print(f"Reachable: {status['reachable']}")
        print(f"Compatible: {status['compatible']}")
        if status.get("service"):
            print(f"Service: {status['service']}")
        if status.get("version") or status.get("package_version"):
            print(f"Version: service={status.get('version') or 'unknown'} package={status['package_version']}")
        if status.get("db_path") or status.get("expected_db_path"):
            print(f"DB: service={status.get('db_path') or 'unknown'} package={status['expected_db_path']}")
        if status.get("issue"):
            print(f"Issue: {status['issue']}")
        print(f"Next: {status['next_action']}")
        return
    if sub == "ensure":
        from tradingcodex_cli.service_autostart import ensure_service_up, service_http_url

        root = configure_workspace_env(Path.cwd())
        addr = argv[1] if len(argv) > 1 else DEFAULT_SERVICE_ADDR
        os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tradingcodex_service.settings")
        started = ensure_service_up(root, addr=addr)
        dashboard_url = service_http_url(addr)
        print(f"TradingCodex service {'started' if started else 'ready'} at {dashboard_url}")
        print(f"Health: {dashboard_url.rstrip('/')}/api/health")
        return
    if sub == "stop":
        from tradingcodex_cli.service_autostart import service_http_url, stop_service

        addr = argv[1] if len(argv) > 1 else DEFAULT_SERVICE_ADDR
        stopped = stop_service(addr)
        print(f"TradingCodex service {'stopped' if stopped else 'not running'} at {service_http_url(addr)}")
        return
    if sub != "runserver":
        raise ValueError(f"Usage: {PROGRAM_NAME} service runserver [addrport] [django runserver args]\n       {PROGRAM_NAME} service ensure [addrport]\n       {PROGRAM_NAME} service stop [addrport]\n       {PROGRAM_NAME} service status [addrport] [--json]")
    from django.core.management import execute_from_command_line
    from tradingcodex_cli.service_autostart import compatible_service_running, service_http_url

    configure_workspace_env(Path.cwd())
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tradingcodex_service.settings")
    runserver_args = argv[1:]
    if not runserver_args or runserver_args[0].startswith("-"):
        runserver_args = [DEFAULT_SERVICE_ADDR, *runserver_args]
    addr = runserver_args[0]
    if compatible_service_running(addr):
        print(f"TradingCodex service already running at {service_http_url(addr)}")
        return
    execute_from_command_line(["manage.py", "runserver", *runserver_args])


def configure_workspace_env(root: Path, *, force: bool = False) -> Path:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tradingcodex_service.settings")
    if force or not os.environ.get("TRADINGCODEX_WORKSPACE_ROOT"):
        os.environ["TRADINGCODEX_WORKSPACE_ROOT"] = str(root.resolve())
    return Path(os.environ["TRADINGCODEX_WORKSPACE_ROOT"]).resolve()


def print_workspace_summary(root: Path) -> None:
    from tradingcodex_service.application.runtime import ensure_workspace_manifest, tradingcodex_db_path

    manifest = ensure_workspace_manifest(root)
    profile = manifest["active_profile"]
    print(f"Workspace: {manifest['project_name']}")
    print(f"Workspace ID: {manifest['workspace_id']}")
    print(f"Active Profile: {profile['label']} ({profile['portfolio_id']}/{profile['account_id']}/{profile['strategy_id']})")
    print(f"Central DB: {tradingcodex_db_path()}")
    print(f"MCP Scope: {manifest['mcp_scope']}")
    print(f"Execution Mode: {manifest['execution_mode']}")


def is_generated_workspace(path: Path) -> bool:
    return (
        (path / ".tradingcodex" / "workspace.json").is_file()
        and (path / ".tradingcodex" / "generated" / "module-lock.json").is_file()
        and (path / "tcx").is_file()
    )


def _finish_bootstrap(target_dir: str) -> None:
    root = configure_workspace_env(Path(target_dir), force=True)
    from tradingcodex_service.application.runtime import ensure_runtime_database, persist_workspace_context_if_available
    from tradingcodex_cli.startup_status import write_server_status_snapshot

    ensure_runtime_database(root)
    persist_workspace_context_if_available(root)
    write_server_status_snapshot(root)

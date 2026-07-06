from __future__ import annotations

import argparse
import json
from pathlib import Path

from tradingcodex_cli.commands.utils import print_json
from tradingcodex_cli.startup_status import detect_codex_permission_status
from tradingcodex_service.application.customization import (
    build_customization_status,
    discover_codex_mcp_servers,
    import_codex_mcp_server,
    write_codex_mcp_server_config,
)
from tradingcodex_service.application.runtime import ensure_runtime_database


def build(root: Path, argv: list[str]) -> None:
    if not argv or argv[0] in {"--help", "-h", "help"}:
        print_build_help()
        return
    section = argv[0]
    rest = argv[1:]
    if section == "status":
        _build_status(root, rest)
        return
    if section == "codex-mcp":
        _codex_mcp(root, rest)
        return
    if section == "permission":
        _permission(root, rest)
        return
    raise ValueError("Usage: tcx build status|codex-mcp|permission")


def _build_status(root: Path, argv: list[str]) -> None:
    parser = argparse.ArgumentParser(prog="tcx build status")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    permission = detect_codex_permission_status(root)
    status = build_customization_status(root, full_access_detected=bool(permission.get("full_access_detected")))
    status["permission_status"] = permission
    if args.json:
        print_json(status)
        return
    print(f"TradingCodex build mode: {status['mode_status']['mode']}")
    print(f"Build enabled: {status['mode_status']['build_enabled']}")
    print(f"Codex MCP servers: {status['codex_mcp']['count']}")
    if status["mode_status"].get("build_blocked_reason"):
        print(f"Blocked: {status['mode_status']['build_blocked_reason']}")


def _codex_mcp(root: Path, argv: list[str]) -> None:
    if not argv:
        raise ValueError("Usage: tcx build codex-mcp discover|import|add")
    action = argv[0]
    rest = argv[1:]
    if action == "discover":
        parser = argparse.ArgumentParser(prog="tcx build codex-mcp discover")
        parser.add_argument("--json", action="store_true")
        parser.add_argument("--workspace-only", action="store_true")
        args = parser.parse_args(rest)
        result = discover_codex_mcp_servers(root, include_global=not args.workspace_only, record=True)
        print_json(result)
        return
    if action == "import":
        parser = argparse.ArgumentParser(prog="tcx build codex-mcp import")
        parser.add_argument("--source", choices=["workspace", "global", "any"], default="workspace")
        parser.add_argument("--name", required=True)
        args = parser.parse_args(rest)
        print_json(import_codex_mcp_server(root, name=args.name, source=args.source, actor="build-cli"))
        return
    if action == "add":
        parser = argparse.ArgumentParser(prog="tcx build codex-mcp add")
        parser.add_argument("--scope", choices=["workspace", "global"], default="workspace")
        parser.add_argument("--name", required=True)
        parser.add_argument("--transport", default="stdio")
        parser.add_argument("--command", default="")
        parser.add_argument("--url", default="")
        parser.add_argument("--args-json", default="")
        parser.add_argument("--arg", action="append", default=[])
        parser.add_argument("--env-key", action="append", default=[])
        parser.add_argument("--credential-ref", default="")
        parser.add_argument("--dry-run", action="store_true")
        args = parser.parse_args(rest)
        permission = detect_codex_permission_status(root)
        parsed_args = json.loads(args.args_json) if args.args_json else args.arg
        if not isinstance(parsed_args, list):
            raise ValueError("--args-json must be a JSON array")
        print_json(
            write_codex_mcp_server_config(
                root,
                name=args.name,
                scope=args.scope,
                transport=args.transport,
                command=args.command,
                args=[str(item) for item in parsed_args],
                url=args.url,
                env_keys=[str(item) for item in args.env_key],
                credential_ref=args.credential_ref,
                dry_run=args.dry_run,
                full_access_detected=bool(permission.get("full_access_detected")),
            )
        )
        return
    raise ValueError("Usage: tcx build codex-mcp discover|import|add")


def _permission(root: Path, argv: list[str]) -> None:
    if not argv:
        raise ValueError("Usage: tcx build permission list|approve|deny")
    ensure_runtime_database(root)
    from apps.mcp.services import approve_external_mcp_permission_request, deny_external_mcp_permission_request, list_external_mcp_permission_requests

    action = argv[0]
    rest = argv[1:]
    if action == "list":
        parser = argparse.ArgumentParser(prog="tcx build permission list")
        parser.add_argument("--status", default="pending")
        parser.add_argument("--limit", type=int, default=50)
        args = parser.parse_args(rest)
        print_json(list_external_mcp_permission_requests(root, {"status": args.status, "limit": args.limit}))
        return
    parser = argparse.ArgumentParser(prog=f"tcx build permission {action}")
    parser.add_argument("--request-id", "--id", dest="request_id", required=True)
    parser.add_argument("--reason", default="")
    args = parser.parse_args(rest)
    payload = {"request_id": args.request_id, "principal_id": "user", "reason": args.reason}
    if action == "approve":
        print_json(approve_external_mcp_permission_request(root, payload))
        return
    if action == "deny":
        print_json(deny_external_mcp_permission_request(root, payload))
        return
    raise ValueError("Usage: tcx build permission list|approve|deny")


def print_build_help() -> None:
    print("""TradingCodex Build

Usage:
  ./tcx build status [--json]
  ./tcx build codex-mcp discover [--workspace-only] [--json]
  ./tcx build codex-mcp import --source workspace|global|any --name <server>
  ./tcx build codex-mcp add --name <server> [--scope workspace|global] [--command <cmd>] [--arg <arg>] [--args-json <json>] [--env-key KEY] [--dry-run]
  ./tcx build permission list [--status pending|approved|denied|all]
  ./tcx build permission approve|deny --request-id <id> [--reason <text>]
""")

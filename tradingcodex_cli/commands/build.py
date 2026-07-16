from __future__ import annotations

import argparse
from pathlib import Path

from tradingcodex_cli.commands.utils import print_json
from tradingcodex_cli.startup_status import detect_codex_permission_status
from tradingcodex_service.application.common import workspace_launcher_command


def build(root: Path, argv: list[str]) -> None:
    if not argv or argv[0] in {"--help", "-h", "help"}:
        print_build_help()
        return
    section = argv[0]
    rest = argv[1:]
    if section == "status":
        _build_status(root, rest)
        return
    raise ValueError("Usage: tcx build status")


def _build_status(root: Path, argv: list[str]) -> None:
    parser = argparse.ArgumentParser(prog="tcx build status", allow_abbrev=False)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    permission = detect_codex_permission_status(root)
    status = {
        "authorization_contract": {
            "status": "exact_turn_required",
            "authority": "user_prompt_submit_hook",
            "exact_first_line": "$tcx-build",
            "invocation_position": "first_meaningful_line",
            "root_native_turn_only": True,
            "persistent_mode": False,
            "active": False,
        },
        "permission_status": permission,
    }
    if args.json:
        print_json(status)
        return
    contract = status["authorization_contract"]
    print("Build authorization: current root native Codex turn")
    print(f"Canonical invocation: {contract['exact_first_line']}")
    print("Invocation position: first meaningful line (same-line request allowed)")
    display_permission = str(permission["codex_permission"]).replace("_", "-")
    print(f"Codex permission: {display_permission} (advisory)")


def print_build_help() -> None:
    launcher = workspace_launcher_command()
    print(f"""TradingCodex Build

Usage:
  {launcher} build status [--json]

Agent-driven TradingCodex mutation requires a root native turn whose first
meaningful line invokes `$tcx-build`. User-installed Codex capabilities remain
owned and configured by Codex rather than this command.
""")

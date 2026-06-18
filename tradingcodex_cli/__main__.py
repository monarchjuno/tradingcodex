from __future__ import annotations

import sys
from pathlib import Path

from tradingcodex_cli.commands.bootstrap import attach, configure_workspace_env, init, service, update
from tradingcodex_cli.commands.db import db
from tradingcodex_cli.commands.doctor import doctor
from tradingcodex_cli.commands.mcp import mcp
from tradingcodex_cli.commands.orders import approve, audit, postmortem, quality_check, risk_check, validate
from tradingcodex_cli.commands.policy import policy
from tradingcodex_cli.commands.profile import profile
from tradingcodex_cli.commands.research import research
from tradingcodex_cli.commands.skills import skills
from tradingcodex_cli.commands.strategies import strategies
from tradingcodex_cli.commands.subagents import subagents
from tradingcodex_cli.commands.utils import _safe_read
from tradingcodex_cli.commands.workspaces import workspace


def main(argv: list[str] | None = None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in {"--help", "-h", "help"}:
        print_help()
        return
    command = argv.pop(0)
    try:
        if command == "init":
            init(argv)
        elif command == "attach":
            attach(argv)
        elif command == "update":
            update(argv)
        elif command == "doctor":
            root = configure_workspace_env(Path.cwd())
            doctor(root, _option_value(argv, "--layer") or "all")
        elif command == "service":
            service(argv)
        elif command in {"subagents", "skills", "strategies", "policy", "mcp", "db", "workspace", "profile", "validate", "risk-check", "approve", "quality-check", "audit", "postmortem", "research", "explain-policy"}:
            root = configure_workspace_env(Path.cwd())
            dispatch_workspace_command(root, command, argv)
        else:
            raise ValueError(f"Unknown command: {command}")
    except Exception as exc:
        print(f"{program_name()}: {exc}", file=sys.stderr)
        sys.exit(1)


def dispatch_workspace_command(root: Path, command: str, argv: list[str]) -> None:
    if command == "subagents":
        subagents(root, argv)
    elif command == "skills":
        skills(root, argv)
    elif command == "strategies":
        strategies(root, argv)
    elif command == "policy":
        policy(root, argv)
    elif command == "mcp":
        mcp(root, argv)
    elif command == "db":
        db(root, argv)
    elif command == "workspace":
        workspace(root, argv)
    elif command == "profile":
        profile(root, argv)
    elif command == "validate":
        validate(root, argv)
    elif command == "risk-check":
        risk_check(root, argv)
    elif command == "approve":
        approve(root, argv)
    elif command == "quality-check":
        quality_check(root, argv)
    elif command == "audit":
        audit(root, argv)
    elif command == "postmortem":
        postmortem(root, argv)
    elif command == "research":
        research(root, argv)
    elif command == "explain-policy":
        print("TradingCodex policy model:")
        print("Principal -> Role -> Policy -> Action -> Resource -> Condition\n")
        print("Explicit deny wins. TradingCodex MCP is the only executable trading boundary.\n")
        print(_safe_read(root / ".tradingcodex" / "policies" / "access-policies.yaml"))
    else:
        raise ValueError(f"Unknown command: {command}")


def _option_value(args: list[str], name: str) -> str | None:
    try:
        return args[args.index(name) + 1]
    except Exception:
        return None


def program_name() -> str:
    name = Path(sys.argv[0]).name
    return name if name == "tcx" else "tcx"


def print_help() -> None:
    print("""TradingCodex Python/Django

Usage:
  tcx attach [workspace] [--overwrite]
  tcx init <workspace> [--overwrite]
  tcx update [workspace] [--no-doctor]
  tcx init --list-modules
  tcx doctor [--layer <layer>]
  tcx workspace status|list
  tcx profile status|list|create|select
  tcx subagents list|status|inspect|diff|project|state|context-audit|plan|skills|prompt
  tcx skills list [--all]|inspect|propose-add|propose-update|apply-proposal
  tcx skills optional list|inspect|create|update|activate|archive|delete
  tcx strategies list|inspect|create|update|activate|archive|delete
  tcx db path|status|migrate
  tcx research list
  tcx mcp stdio|external
  tcx service runserver [addrport] [django runserver args]
  tcx service ensure [addrport]
""")


if __name__ == "__main__":
    main()

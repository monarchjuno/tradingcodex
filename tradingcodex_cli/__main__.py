from __future__ import annotations

import sys
from pathlib import Path

from tradingcodex_cli.commands.bootstrap import attach, configure_workspace_env, init, service, update
from tradingcodex_cli.commands.connectors import connectors
from tradingcodex_cli.commands.db import db
from tradingcodex_cli.commands.decision import decision
from tradingcodex_cli.commands.doctor import doctor
from tradingcodex_cli.commands.mcp import mcp
from tradingcodex_cli.commands.mode import mode
from tradingcodex_cli.commands.orders import approve, audit, postmortem, quality_check, risk_check, validate
from tradingcodex_cli.commands.policy import policy
from tradingcodex_cli.commands.profile import profile
from tradingcodex_cli.commands.research import research
from tradingcodex_cli.commands.skills import skills
from tradingcodex_cli.commands.strategies import strategies
from tradingcodex_cli.commands.subagents import subagents
from tradingcodex_cli.commands.utils import _option_value, _safe_read
from tradingcodex_cli.commands.workflow import workflow
from tradingcodex_cli.commands.workspaces import workspace


def explain_policy(root: Path, argv: list[str]) -> None:
    print("TradingCodex policy model:")
    print("Requester -> Role -> Policy -> Action -> Resource -> Condition\n")
    print("Explicit deny wins. Execution-sensitive work must use the approved action boundary.\n")
    print(_safe_read(root / ".tradingcodex" / "policies" / "access-policies.yaml"))


TOP_LEVEL_COMMANDS = {
    "init": init,
    "attach": attach,
    "update": update,
    "service": service,
}

WORKSPACE_COMMANDS = {
    "subagents": subagents,
    "workflow": workflow,
    "decision": decision,
    "skills": skills,
    "strategies": strategies,
    "policy": policy,
    "mcp": mcp,
    "db": db,
    "workspace": workspace,
    "profile": profile,
    "mode": mode,
    "connectors": connectors,
    "validate": validate,
    "risk-check": risk_check,
    "approve": approve,
    "quality-check": quality_check,
    "audit": audit,
    "postmortem": postmortem,
    "research": research,
    "explain-policy": explain_policy,
}


def main(argv: list[str] | None = None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in {"--help", "-h", "help"}:
        print_help()
        return
    command = argv.pop(0)
    try:
        if command in TOP_LEVEL_COMMANDS:
            TOP_LEVEL_COMMANDS[command](argv)
        elif command == "doctor":
            root = configure_workspace_env(Path.cwd())
            doctor(root, _option_value(argv, "--layer") or "all")
        elif command in WORKSPACE_COMMANDS:
            root = configure_workspace_env(Path.cwd())
            dispatch_workspace_command(root, command, argv)
        else:
            raise ValueError(f"Unknown command: {command}")
    except Exception as exc:
        print(f"{program_name()}: {exc}", file=sys.stderr)
        sys.exit(1)


def dispatch_workspace_command(root: Path, command: str, argv: list[str]) -> None:
    handler = WORKSPACE_COMMANDS.get(command)
    if handler is None:
        raise ValueError(f"Unknown command: {command}")
    handler(root, argv)


def program_name() -> str:
    name = Path(sys.argv[0]).name
    return name if name == "tcx" else "tcx"


def print_help() -> None:
    print("""TradingCodex Python/Django

Usage:
  tcx attach [workspace] [--overwrite]
  tcx init <workspace> [--overwrite]
  tcx update [workspace] [--no-doctor] [--skip-refresh]
  tcx update status [--json]
  tcx init --list-modules
  tcx doctor [--layer <layer>]
  tcx mode status|set
  tcx connectors status|connect|scaffold|register|validate
  tcx workspace status|list
  tcx profile status|list|create|select|update
  tcx workflow intake|validate|record|plan|preview|run ...
  tcx decision list|show|export
  tcx subagents list|status|inspect|diff|project|state|context-audit|plan|skills|prompt
  tcx skills list [--all]|inspect|propose-add|propose-update|apply-proposal
  tcx skills optional list|inspect|create|update|activate|archive|delete
  tcx strategies list|inspect|create|update|activate|archive|delete
  tcx db path|status|migrate
  tcx research list
  tcx mcp stdio|external
  tcx service runserver [addrport] [django runserver args]
  tcx service ensure [addrport]
  tcx service stop [addrport]
  tcx service status [addrport] [--json]
""")


if __name__ == "__main__":
    main()

from __future__ import annotations

import importlib
import json
import os
import runpy
import sys
from pathlib import Path

from tradingcodex_service.version import TRADINGCODEX_VERSION


def explain_policy(root: Path, argv: list[str]) -> None:
    from tradingcodex_service.application.policy import EXPLICIT_DENY_ACTIONS, read_runtime_policy

    policy = read_runtime_policy(root)
    print(json.dumps({
        "action_boundary": "requester -> permission -> policy -> payload validation -> approval -> idempotency/effect reservation -> intent audit -> connection -> finalized audit",
        "explicit_deny_actions": sorted(EXPLICIT_DENY_ACTIONS),
        "execution": {
            "max_single_order_base": policy.max_single_order_base,
            "enabled_adapters": sorted(policy.allowed_adapters),
            "enabled_execution_postures": sorted(policy.allowed_execution_postures),
            "live_enabled": policy.live_enabled,
        },
        "policy_source": list(policy.source),
    }, indent=2))


def _lazy_command(module: str, name: str):
    def handler(*args):
        return getattr(importlib.import_module(module), name)(*args)

    return handler


TOP_LEVEL_COMMANDS = {
    "attach": _lazy_command("tradingcodex_cli.commands.bootstrap", "attach"),
    "update": _lazy_command("tradingcodex_cli.commands.bootstrap", "update"),
    "service": _lazy_command("tradingcodex_cli.commands.bootstrap", "service"),
    "home": _lazy_command("tradingcodex_cli.commands.home", "home"),
}

WORKSPACE_COMMANDS = {
    "subagents": _lazy_command("tradingcodex_cli.commands.subagents", "subagents"),
    "workflow": _lazy_command("tradingcodex_cli.commands.workflow", "workflow"),
    "decision": _lazy_command("tradingcodex_cli.commands.decision", "decision"),
    "skills": _lazy_command("tradingcodex_cli.commands.skills", "skills"),
    "strategies": _lazy_command("tradingcodex_cli.commands.strategies", "strategies"),
    "investment-brains": _lazy_command("tradingcodex_cli.commands.investment_brains", "investment_brains"),
    "policy": _lazy_command("tradingcodex_cli.commands.policy", "policy"),
    "mcp": _lazy_command("tradingcodex_cli.commands.mcp", "mcp"),
    "db": _lazy_command("tradingcodex_cli.commands.db", "db"),
    "workspace": _lazy_command("tradingcodex_cli.commands.workspaces", "workspace"),
    "profile": _lazy_command("tradingcodex_cli.commands.profile", "profile"),
    "investor-context": _lazy_command("tradingcodex_cli.commands.investor_context", "investor_context"),
    "mode": _lazy_command("tradingcodex_cli.commands.mode", "mode"),
    "build": _lazy_command("tradingcodex_cli.commands.build", "build"),
    "connectors": _lazy_command("tradingcodex_cli.commands.connectors", "connectors"),
    "validate": _lazy_command("tradingcodex_cli.commands.orders", "validate"),
    "risk-check": _lazy_command("tradingcodex_cli.commands.orders", "risk_check"),
    "approve": _lazy_command("tradingcodex_cli.commands.orders", "approve"),
    "quality-check": _lazy_command("tradingcodex_cli.commands.orders", "quality_check"),
    "audit": _lazy_command("tradingcodex_cli.commands.orders", "audit"),
    "postmortem": _lazy_command("tradingcodex_cli.commands.orders", "postmortem"),
    "research": _lazy_command("tradingcodex_cli.commands.research", "research"),
    "forecast": _lazy_command("tradingcodex_cli.commands.forecast", "forecast"),
    "evaluation": _lazy_command("tradingcodex_cli.commands.evaluation", "evaluation"),
    "explain-policy": explain_policy,
}


def main(argv: list[str] | None = None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv in (["--version"], ["version"]):
        print(TRADINGCODEX_VERSION)
        return
    if not argv or argv[0] in {"--help", "-h", "help"}:
        print_help()
        return
    command = argv.pop(0)
    try:
        if command in TOP_LEVEL_COMMANDS:
            TOP_LEVEL_COMMANDS[command](argv)
        elif command == "__hook":
            from tradingcodex_cli.commands.bootstrap import configure_workspace_env

            root = configure_workspace_env(Path.cwd())
            hook_path = root / ".codex" / "hooks" / "tradingcodex_hook.py"
            if not hook_path.is_file():
                raise ValueError(f"TradingCodex hook is missing: {hook_path}")
            original_argv = sys.argv
            try:
                sys.argv = [str(hook_path), *argv]
                runpy.run_path(str(hook_path), run_name="__main__")
            finally:
                sys.argv = original_argv
        elif command == "doctor":
            from tradingcodex_cli.commands.bootstrap import configure_workspace_env
            from tradingcodex_cli.commands.doctor import doctor
            from tradingcodex_cli.commands.utils import _option_value

            root = configure_workspace_env(Path.cwd())
            doctor(
                root,
                _option_value(argv, "--layer") or "all",
                verbose="--verbose" in argv,
            )
        elif command in WORKSPACE_COMMANDS:
            from tradingcodex_cli.commands.bootstrap import configure_workspace_env

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
  tcx --version | tcx version
  tcx attach [workspace] [--from <package-spec> | --dev]
  tcx update [workspace] [--from <package-spec> | --dev] [--no-doctor] [--skip-refresh]
  tcx update status [--json]
  tcx doctor [--layer <layer>] [--verbose]
  tcx home status|check [--json]
  tcx build status
  tcx connectors status|providers|inspect-provider|approve-provider|revoke-provider|connect|scaffold|register|validate
  tcx connectors inspect-provider <provider-id>
  tcx connectors approve-provider|revoke-provider <provider-id>  # interactive operator terminal only
  tcx workspace status|list
  tcx profile status|list|create|select|update
  tcx investor-context status|update|enable|disable|clear
  tcx workflow begin <request>
  tcx workflow show <analysis-run-id>
  tcx decision list|show|export|snapshot
  tcx subagents list|status|inspect|diff|project|state|context-audit|plan|skills|prompt
  tcx skills list [--all]|inspect|propose-add|propose-update|apply-proposal
  tcx skills optional list|inspect|create|update|activate|archive|delete
  tcx strategies list|inspect|create|update|activate|archive|delete
  tcx investment-brains list|inspect|validate|install|update|activate|deactivate|rollback|remove
  tcx db path|status|migrate
  tcx research list|get|search|export|create|append|run-card|validation-card|spec|replay|experiment|causal-analysis|judgment-prior|judgment-review|index|catalog
  tcx forecast issue|revise|resolve|score ... --principal <role> | get|list|calibration
  tcx postmortem list|process-review|create|show ... (lesson promotion requires judgment-reviewer MCP)
  tcx evaluation corpus|run|assign-review|review-packet|blind-review|compare ... --principal <id>
  tcx mcp stdio|ledger|install-global
  tcx service runserver [addrport] [django runserver args]
  tcx service ensure [addrport]
  tcx service stop [addrport]
  tcx service status [addrport] [--json]
""")


if __name__ == "__main__":
    main()

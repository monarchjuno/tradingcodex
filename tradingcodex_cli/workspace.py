from __future__ import annotations

import os
import sys
from pathlib import Path

from tradingcodex_cli.commands.db import db
from tradingcodex_cli.commands.doctor import (
    _central_service_checks,
    _codex_mcp_config_checks,
    _enforcement_checks,
    _guidance_checks,
    _improvement_checks,
    _information_barrier_checks,
    _mcp_checks,
    _read_codex_mcp_config,
    doctor,
)
from tradingcodex_cli.commands.mcp import mcp, mcp_ledger, print_mcp_help
from tradingcodex_cli.commands.orders import approve, audit, postmortem, quality_check, risk_check, validate
from tradingcodex_cli.commands.policy import policy
from tradingcodex_cli.commands.profile import profile
from tradingcodex_cli.commands.research import research
from tradingcodex_cli.commands.skills import skills
from tradingcodex_cli.commands.subagents import subagents
from tradingcodex_cli.commands.utils import (
    _option_value,
    _parse_agent_list,
    _read_json,
    _regex,
    _safe_read,
    _toml_string,
    _yaml_value,
    apply_skill_proposal,
    classify_artifact_path,
    list_skills,
    list_subagents,
    path_check,
    print_json,
    read_subagent_state,
    read_thread_policy,
    skills_for_role,
    text_check,
    write_skill_proposal,
)
from tradingcodex_cli.commands.workspaces import workspace


def workspace_root() -> Path:
    return Path(os.environ.get("TRADINGCODEX_WORKSPACE_ROOT", os.getcwd())).resolve()


def main(argv: list[str] | None = None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in {"--help", "-h", "help"}:
        print_help()
        return
    command = argv.pop(0)
    root = workspace_root()
    try:
        if command == "doctor":
            doctor(root, _option_value(argv, "--layer") or "all")
        elif command == "subagents":
            subagents(root, argv)
        elif command == "skills":
            skills(root, argv)
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
    except Exception as exc:
        print(f"TradingCodex: {exc}", file=sys.stderr)
        sys.exit(1)


def print_help() -> None:
    print("""TradingCodex

Usage:
  tcx doctor [--layer service|guidance|enforcement|information-barrier|improvement|mcp|codex-native]
  tcx workspace status|list
  tcx profile status|list|create|select
  tcx subagents list|status|state|plan|skills|prompt
  tcx skills list [--all]|inspect|propose-add|propose-update|apply-proposal
  tcx policy simulate --principal <id> --action <action> --resource <resource>
  tcx db status|path|migrate
  tcx mcp call <tool>
  tcx mcp ledger [--tool <name>] [--principal <id>] [--status ok]
  tcx mcp stdio
  tcx research create|get|list|search|export
  tcx quality-check <artifact>
""")


if __name__ == "__main__":
    main()

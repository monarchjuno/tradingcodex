from __future__ import annotations

import sys
from pathlib import Path

from tradingcodex_service.domain import call_tool
from tradingcodex_cli.commands.utils import _option_value, print_json

def policy(root: Path, argv: list[str]) -> None:
    if not argv or argv[0] != "simulate":
        raise ValueError("Usage: tcx policy simulate --principal <id> --action <action> --resource <resource>")
    args = argv[1:]
    result = call_tool(root, "simulate_policy", {
        "principal_id": _option_value(args, "--principal") or "unknown",
        "action": _option_value(args, "--action") or "unknown",
        "resource": _option_value(args, "--resource") or "*",
        "require_approval_check": (_option_value(args, "--action") == "mcp.tradingcodex.submit_approved_order"),
    })
    print_json(result)
    if result.get("decision") != "allow":
        sys.exit(1)

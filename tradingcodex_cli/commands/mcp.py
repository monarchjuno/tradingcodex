from __future__ import annotations

import os
import json
import sys
from pathlib import Path
from typing import Any

from tradingcodex_service.domain import call_tool, ensure_runtime_database, tradingcodex_db_path
from tradingcodex_service.mcp_runtime import SAFE_HOME_TOOL_NAMES
from tradingcodex_cli.commands.utils import _option_value, print_json

def mcp(root: Path, argv: list[str]) -> None:
    if not argv or argv[0] in {"--help", "-h", "help"}:
        print_mcp_help()
        return
    if argv and argv[0] == "stdio":
        from tradingcodex_cli.mcp_stdio import run_stdio
        from tradingcodex_cli.service_autostart import maybe_autostart_service

        maybe_autostart_service(root)
        run_stdio(root)
        return
    if argv and argv[0] in {"ledger", "calls"}:
        mcp_ledger(root, argv[1:])
        return
    if argv and argv[0] == "install-global":
        install_global_mcp(argv[1:])
        return
    if not argv or argv[0] != "call":
        raise ValueError("Usage: tcx mcp call <tool> [--order-intent file] [--approval-receipt file] [--order-id id] | tcx mcp ledger [--tool name] | tcx mcp stdio")
    tool = argv[1] if len(argv) > 1 else ""
    args = argv[2:]
    order_path = _option_value(args, "--order-intent")
    receipt_path = _option_value(args, "--approval-receipt")
    principal_id = _option_value(args, "--principal")
    payload: dict[str, Any] = {}
    if principal_id:
        payload["principal_id"] = principal_id
    payload.update({
        "order_intent_id": _option_value(args, "--order-intent-id"),
        "order_id": _option_value(args, "--order-id"),
        "artifact_id": _option_value(args, "--artifact-id") or _option_value(args, "--id"),
        "artifact_type": _option_value(args, "--type"),
        "universe": _option_value(args, "--universe"),
        "workflow_type": _option_value(args, "--workflow-type"),
        "symbol": _option_value(args, "--symbol"),
        "title": _option_value(args, "--title"),
        "markdown": _option_value(args, "--markdown"),
        "markdown_path": _option_value(args, "--markdown-file") or _option_value(args, "--file"),
        "source_as_of": _option_value(args, "--source-as-of"),
        "readiness_label": _option_value(args, "--readiness"),
        "query": _option_value(args, "--query") or _option_value(args, "--q"),
        "limit": _option_value(args, "--limit"),
    })
    payload = {key: value for key, value in payload.items() if value not in (None, "")}
    for raw in args:
        if raw.startswith("{"):
            parsed = json.loads(raw)
            if not isinstance(parsed, dict):
                raise ValueError("positional JSON MCP payload must be an object")
            payload.update(parsed)
    if order_path:
        payload["order_intent"] = json.loads((root / order_path).read_text(encoding="utf-8"))
    if receipt_path:
        payload["approval_receipt"] = json.loads((root / receipt_path).read_text(encoding="utf-8"))
    result = call_tool(root, tool, payload)
    print_json(result)
    if result.get("status") in {"rejected", "not_supported"} or result.get("decision") == "deny" or result.get("valid") is False:
        sys.exit(1)


def mcp_ledger(root: Path, args: list[str]) -> None:
    ensure_runtime_database(root)
    from apps.mcp.models import McpToolCall

    queryset = McpToolCall.objects.all()
    tool = _option_value(args, "--tool")
    principal = _option_value(args, "--principal")
    status = _option_value(args, "--status")
    if tool:
        queryset = queryset.filter(tool_name=tool)
    if principal:
        queryset = queryset.filter(principal_id=principal)
    if status:
        queryset = queryset.filter(status=status)
    limit = max(1, min(int(_option_value(args, "--limit") or 20), 200))
    print_json({
        "count": queryset.count(),
        "db_path": str(tradingcodex_db_path()),
        "central_ledger": True,
        "calls": [
            {
                "created_at": call.created_at.isoformat(),
                "tool_name": call.tool_name,
                "principal_id": call.principal_id,
                "status": call.status,
                "workspace_context": call.workspace_context,
                "request_hash": call.request_hash,
                "result_hash": call.result_hash,
                "error": call.error,
                "duration_ms": call.duration_ms,
            }
            for call in queryset[:limit]
        ],
    })


def install_global_mcp(args: list[str]) -> None:
    if "--safe" not in args:
        raise ValueError("Usage: tcx mcp install-global --safe [--config <path>] [--print]")
    config_path = Path(_option_value(args, "--config") or Path.home() / ".codex" / "config.toml").expanduser().resolve()
    block = global_home_mcp_config_block()
    if "--print" in args:
        print(block)
        return
    config_path.parent.mkdir(parents=True, exist_ok=True)
    existing = config_path.read_text(encoding="utf-8") if config_path.exists() else ""
    updated = replace_managed_block(existing, block)
    config_path.write_text(updated, encoding="utf-8")
    print_json({
        "status": "installed",
        "server_name": "tradingcodex-home",
        "config_path": str(config_path),
        "safe_tools": sorted(SAFE_HOME_TOOL_NAMES),
    })


def global_home_mcp_config_block() -> str:
    tools = ",\n  ".join(json.dumps(tool) for tool in sorted(SAFE_HOME_TOOL_NAMES))
    return f"""# BEGIN TradingCodex home MCP
[mcp_servers.tradingcodex-home]
command = "uvx"
args = ["--refresh", "--python", "3.14", "--from", "{os.environ.get("TRADINGCODEX_MCP_PACKAGE_SPEC", "tradingcodex")}", "python", "-m", "tradingcodex_cli", "mcp", "stdio"]
enabled = true
env = {{ TRADINGCODEX_MCP_SAFE_TOOLS = "1", TRADINGCODEX_MCP_SCOPE = "global-home" }}
enabled_tools = [
  {tools}
]
default_tools_approval_mode = "prompt"
startup_timeout_sec = 20
# END TradingCodex home MCP
"""


def replace_managed_block(existing: str, block: str) -> str:
    start = "# BEGIN TradingCodex home MCP"
    end = "# END TradingCodex home MCP"
    if start in existing and end in existing:
        before, rest = existing.split(start, 1)
        _, after = rest.split(end, 1)
        return before.rstrip() + "\n\n" + block.rstrip() + "\n" + after
    prefix = existing.rstrip() + "\n\n" if existing.strip() else ""
    return prefix + block


def print_mcp_help() -> None:
    print("""TradingCodex MCP

Usage:
  ./tcx mcp call <tool> [--principal <role>] [tool args]
  ./tcx mcp ledger [--tool <name>] [--principal <role>] [--status ok]
  ./tcx mcp install-global --safe
  ./tcx mcp stdio

Examples:
  ./tcx mcp call create_research_artifact --principal fundamental-analyst --artifact-id note-1 --title "Note" --markdown "# Note" --symbol MSFT
  ./tcx mcp call submit_approved_order --order-intent-id approved-order-id
  ./tcx mcp ledger --tool create_research_artifact --status ok
""")

from __future__ import annotations

import os
import json
import sys
from pathlib import Path
from typing import Any

from tradingcodex_service.application.common import atomic_write_text, workspace_launcher_command
from tradingcodex_service.application.runtime import ensure_runtime_database, tradingcodex_db_path
from tradingcodex_service.mcp_runtime import SAFE_HOME_TOOL_NAMES, TOOL_REGISTRY, call_mcp_tool, default_principal_for_tool
from tradingcodex_cli.commands.utils import _list_option, _option_value, print_json
from tradingcodex_cli.generator import resolve_package_runner
from tradingcodex_cli.package_source import (
    EXECUTABLE_SOURCE_ENV,
    LOCAL_EXECUTABLE_SOURCE_KIND,
    PACKAGE_SOURCE_KIND_ENV,
    configured_executable_source,
)

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
    if argv and argv[0] == "ledger":
        mcp_ledger(root, argv[1:])
        return
    if argv and argv[0] == "install-global":
        install_global_mcp(argv[1:])
        return
    if not argv or argv[0] != "call":
        raise ValueError("Usage: tcx mcp call <tool> [tool args] | tcx mcp ledger [--tool name] | tcx mcp stdio")
    tool = argv[1] if len(argv) > 1 else ""
    if tool == "promote_lesson":
        raise PermissionError(
            "lesson promotion is unavailable from generic CLI calls; use the role-scoped judgment-reviewer stdio MCP"
        )
    args = argv[2:]
    principal_id = _option_value(args, "--principal")
    payload: dict[str, Any] = {}
    payload.update({
        "ticket_id": _option_value(args, "--ticket-id"),
        "approval_receipt_id": _option_value(args, "--approval-receipt-id"),
        "natural_language": _option_value(args, "--natural-language"),
        "provider": _option_value(args, "--provider"),
        "provider_id": _option_value(args, "--provider-id"),
        "label": _option_value(args, "--label"),
        "display_name": _option_value(args, "--display-name"),
        "credential_ref": _option_value(args, "--credential-ref"),
        "environment": _option_value(args, "--environment"),
        "region": _option_value(args, "--region"),
        "family": _option_value(args, "--family"),
        "asset_class": _option_value(args, "--asset-class"),
        "product_type": _option_value(args, "--product-type"),
        "instrument": _option_value(args, "--instrument"),
        "market": _option_value(args, "--market"),
        "venue_symbol": _option_value(args, "--venue-symbol"),
        "side": _option_value(args, "--side"),
        "quantity": _float_option(args, "--quantity"),
        "quantity_mode": _option_value(args, "--quantity-mode"),
        "quote_notional": _float_option(args, "--quote-notional"),
        "order_type": _option_value(args, "--order-type"),
        "limit_price": _float_option(args, "--limit-price"),
        "stop_price": _float_option(args, "--stop-price"),
        "time_in_force": _option_value(args, "--time-in-force"),
        "currency": _option_value(args, "--currency"),
        "broker_id": _option_value(args, "--broker-id"),
        "broker_order_id": _option_value(args, "--broker-order-id"),
        "broker_account_id": _option_value(args, "--broker-account-id"),
        "portfolio_id": _option_value(args, "--portfolio-id"),
        "account_id": _option_value(args, "--account-id"),
        "strategy_id": _option_value(args, "--strategy-id"),
        "client_order_id": _option_value(args, "--client-order-id"),
        "conid": _option_value(args, "--conid"),
        "margin_mode": _option_value(args, "--margin-mode"),
        "position_side": _option_value(args, "--position-side"),
        "leverage": _float_option(args, "--leverage"),
        "artifact_id": _option_value(args, "--artifact-id"),
        "artifact_type": _option_value(args, "--artifact-type"),
        "universe": _option_value(args, "--universe"),
        "workflow_type": _option_value(args, "--workflow-type"),
        "workflow_run_id": _option_value(args, "--workflow-run-id"),
        "symbol": _option_value(args, "--symbol"),
        "role": _option_value(args, "--role"),
        "title": _option_value(args, "--title"),
        "markdown": _option_value(args, "--markdown"),
        "markdown_path": _option_value(args, "--markdown-file"),
        "source_as_of": _option_value(args, "--source-as-of"),
        "readiness_label": _option_value(args, "--readiness"),
        "context_summary": _option_value(args, "--context-summary"),
        "reader_summary": _option_value(args, "--reader-summary"),
        "handoff_state": _option_value(args, "--handoff-state"),
        "confidence": _option_value(args, "--confidence"),
        "missing_evidence": _list_option(args, "--missing-evidence"),
        "next_recipient": _option_value(args, "--next-recipient"),
        "next_action": _option_value(args, "--next-action"),
        "blocked_actions": _list_option(args, "--blocked-actions"),
        "source_snapshot_ids": _list_option(args, "--source-snapshot-ids"),
        "follow_up_requests": _list_option(args, "--follow-up-requests"),
        "query": _option_value(args, "--query"),
        "limit": _int_option(args, "--limit"),
        "source_category": _option_value(args, "--source-category"),
        "source_locator": _option_value(args, "--source-locator"),
        "as_of": _option_value(args, "--as-of"),
        "observed_at": _option_value(args, "--observed-at"),
        "effective_at": _option_value(args, "--effective-at"),
        "published_at": _option_value(args, "--published-at"),
        "retrieved_at": _option_value(args, "--retrieved-at"),
        "known_at": _option_value(args, "--known-at"),
        "recorded_at": _option_value(args, "--recorded-at"),
        "revision": _option_value(args, "--revision"),
        "vintage": _option_value(args, "--vintage"),
        "timezone": _option_value(args, "--timezone"),
        "schema_hash": _option_value(args, "--schema-hash"),
        "corporate_action_policy": _option_value(args, "--corporate-action-policy"),
        "price_adjustment_policy": _option_value(args, "--price-adjustment-policy"),
        "delisting_policy": _option_value(args, "--delisting-policy"),
        "coverage_note": _option_value(args, "--coverage-note"),
        "live_confirmation": _option_value(args, "--live-confirmation"),
    })
    if "--reduce-only" in args:
        payload["reduce_only"] = True
    payload_json = _option_value(args, "--payload")
    if payload_json:
        parsed_payload = json.loads(payload_json)
        if not isinstance(parsed_payload, dict):
            raise ValueError("--payload must be a JSON object")
        payload["payload"] = parsed_payload
    warnings_json = _option_value(args, "--warnings")
    if warnings_json:
        parsed_warnings = json.loads(warnings_json)
        if not isinstance(parsed_warnings, list):
            raise ValueError("--warnings must be a JSON array")
        payload["warnings"] = parsed_warnings
    for option, field in (("--provider-query", "provider_query"), ("--universe-membership", "universe_membership")):
        raw_value = _option_value(args, option)
        if raw_value:
            parsed_value = json.loads(raw_value)
            if not isinstance(parsed_value, dict):
                raise ValueError(f"{option} must be a JSON object")
            payload[field] = parsed_value
    payload = {key: value for key, value in payload.items() if value not in (None, "")}
    option_value_indices = {
        index + 1
        for index, raw in enumerate(args[:-1])
        if raw.startswith("--") and raw not in {"--reduce-only"}
    }
    for index, raw in enumerate(args):
        if index in option_value_indices:
            continue
        if raw.startswith("{"):
            parsed = json.loads(raw)
            if not isinstance(parsed, dict):
                raise ValueError("positional JSON MCP payload must be an object")
            payload.update(parsed)
    tool_spec = TOOL_REGISTRY.get(tool)
    if tool_spec is None:
        raise ValueError(f"Unknown TradingCodex tool: {tool}")
    if tool_spec.risk_level != "read" and not principal_id:
        raise ValueError(f"--principal is required for {tool_spec.risk_level} MCP tool: {tool}")
    result = call_mcp_tool(
        root,
        tool,
        payload,
        transport_principal=principal_id or default_principal_for_tool(tool_spec),
    )
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


def _int_option(args: list[str], name: str) -> int | None:
    value = _option_value(args, name)
    return int(value) if value not in (None, "") else None


def _float_option(args: list[str], name: str) -> float | None:
    value = _option_value(args, name)
    return float(value) if value not in (None, "") else None


def _replace_managed_block(existing: str, block: str, name: str) -> str:
    start = f"# BEGIN {name}"
    end = f"# END {name}"
    if start in existing and end in existing:
        prefix, remainder = existing.split(start, 1)
        _, suffix = remainder.split(end, 1)
        return f"{prefix.rstrip()}\n\n{block.strip()}\n{suffix.lstrip()}"
    return f"{existing.rstrip()}\n\n{block.strip()}\n"


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
    updated = _replace_managed_block(existing, block, "TradingCodex home MCP")
    atomic_write_text(config_path, updated)
    print_json({
        "status": "installed",
        "server_name": "tradingcodex-home",
        "config_path": str(config_path),
        "safe_tools": sorted(SAFE_HOME_TOOL_NAMES),
    })


def global_home_mcp_config_block() -> str:
    tools = ",\n  ".join(json.dumps(tool) for tool in sorted(SAFE_HOME_TOOL_NAMES))
    if (
        not str(os.environ.get(EXECUTABLE_SOURCE_ENV) or "")
        and os.environ.get(PACKAGE_SOURCE_KIND_ENV) == LOCAL_EXECUTABLE_SOURCE_KIND
    ):
        package_runner = sys.executable
        rendered_args = '"-m", "tradingcodex_cli", "mcp", "stdio"'
    else:
        raw_package_spec = configured_executable_source(None)
        package_spec = json.dumps(raw_package_spec, ensure_ascii=False)
        package_runner, package_prefix = resolve_package_runner(raw_package_spec)
        rendered_prefix = ", ".join(json.dumps(item) for item in package_prefix)
        rendered_args = (
            f'{rendered_prefix}, {package_spec}, "python", "-m", '
            '"tradingcodex_cli", "mcp", "stdio"'
        )
    return f"""# BEGIN TradingCodex home MCP
[mcp_servers.tradingcodex-home]
command = {json.dumps(package_runner)}
args = [{rendered_args}]
enabled = true
env = {{ TRADINGCODEX_MCP_SAFE_TOOLS = "1", TRADINGCODEX_MCP_SCOPE = "global-home" }}
enabled_tools = [
  {tools}
]
default_tools_approval_mode = "prompt"
startup_timeout_sec = 20
# END TradingCodex home MCP
"""


def print_mcp_help() -> None:
    launcher = workspace_launcher_command()
    print(f"""TradingCodex MCP

Usage:
  {launcher} mcp call <tool> [--principal <role>] [tool args]
  {launcher} mcp ledger [--tool <name>] [--principal <role>] [--status ok]
  {launcher} mcp install-global --safe
  {launcher} mcp stdio

Examples:
  {launcher} mcp call create_research_artifact --principal fundamental-analyst --artifact-id note-1 --title "Note" --markdown "# Note" --symbol MSFT
  {launcher} mcp call list_broker_adapter_providers --principal head-manager
  {launcher} mcp call preview_order_translation --principal head-manager --broker-id <broker-id> --symbol <symbol> --side buy --order-type market --quote-notional 25
  {launcher} mcp call create_order_ticket --principal portfolio-manager --natural-language "buy 5 AAPL limit 180"
  {launcher} mcp call run_order_checks --principal portfolio-manager --ticket-id ticket-id
  {launcher} mcp ledger --tool create_research_artifact --status ok

Connector registration is Build-protected and intentionally unavailable from
generic `mcp call`. Start a root turn whose first meaningful line invokes
`$tcx-build` instead.
""")

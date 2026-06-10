from __future__ import annotations

import os
import sys
import tomllib
from pathlib import Path
from typing import Any

from tradingcodex_service.domain import (
    EXPECTED_SKILLS,
    EXPECTED_SUBAGENTS,
    ROLE_PERMISSION_PROFILES,
    read_workspace_manifest,
    ensure_runtime_database,
    tradingcodex_db_path,
)
from tradingcodex_cli.commands.utils import (
    _safe_read,
    list_subagents,
    path_check,
    read_thread_policy,
    text_check,
)

def doctor(root: Path, layer: str) -> None:
    layer = "improvement" if layer == "task-harness" else layer
    allowed = {"all", "codex-native", "guidance", "enforcement", "information-barrier", "improvement", "mcp", "service"}
    if layer not in allowed:
        raise ValueError(f'unknown layer "{layer}"')
    checks = []
    checks.extend(_central_service_checks(root))
    checks.extend(_guidance_checks(root))
    checks.extend(_enforcement_checks(root))
    checks.extend(_information_barrier_checks(root))
    checks.extend(_improvement_checks(root))
    checks.extend(_mcp_checks(root))
    checks = [check for check in checks if layer == "all" or check["layer"] == layer or (layer == "codex-native" and check.get("codexNative"))]
    failed = 0
    print("TradingCodex Harness\n")
    for check in checks:
        status = "WARN" if check.get("warn") else "PASS" if check["ok"] else "FAIL"
        if not check["ok"] and not check.get("warn"):
            failed += 1
        print(f"{status.ljust(4)} {check['layer'].ljust(20)} {check['name']} - {check['detail']}")
    if failed:
        print(f"TradingCodex doctor failed: {failed} check(s) failed", file=sys.stderr)
        sys.exit(1)
    print("\nTradingCodex doctor passed")

def _guidance_checks(root: Path) -> list[dict[str, Any]]:
    return [
        path_check(root, "guidance", "AGENTS.md installed", "AGENTS.md", True),
        text_check(root, "guidance", "head-manager model instructions file configured", ".codex/config.toml", 'model_instructions_file = "prompts/base_instructions/head-manager.md"', True),
        text_check(root, "guidance", "head-manager instructions installed", ".codex/prompts/base_instructions/head-manager.md", "You are the `head-manager` agent", True),
        path_check(root, "guidance", "local CLI wrapper installed", "tcx", False),
        text_check(root, "guidance", "hooks configured", ".codex/hooks.json", "\"PreToolUse\"", True),
        text_check(root, "guidance", "scenario quality gates configured", ".codex/prompts/base_instructions/head-manager.md", "scenario-quality-gates", True),
        text_check(root, "guidance", "investment workflow map configured", ".codex/prompts/base_instructions/head-manager.md", "investment-workflow-map", True),
        {"layer": "guidance", "name": "subagent max_threads matches roster", "ok": read_thread_policy(root)["max_threads"] == len(list_subagents(root)), "codexNative": True, "detail": f"max_threads={read_thread_policy(root)['max_threads']}, subagents={len(list_subagents(root))}"},
    ]


def _central_service_checks(root: Path) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    try:
        ensure_runtime_database(root)
        db_path = tradingcodex_db_path()
        checks.append({"layer": "service", "name": "central DB reachable", "ok": db_path.exists(), "codexNative": False, "detail": str(db_path)})
        checks.append({
            "layer": "service",
            "name": "workspace root is provenance only",
            "ok": db_path != (root / ".tradingcodex" / "state" / "tradingcodex.sqlite3").resolve(),
            "codexNative": False,
            "detail": f"workspace={root}",
        })
        manifest = read_workspace_manifest(root)
        has_workspace_id = bool(str(manifest.get("workspace_id", "")).startswith("tcxw_"))
        checks.append({
            "layer": "service",
            "name": "workspace identity manifest installed",
            "ok": has_workspace_id,
            "warn": not has_workspace_id,
            "codexNative": False,
            "detail": str(manifest.get("workspace_id") or "missing .tradingcodex/workspace.json"),
        })
        has_profile = bool((manifest.get("active_profile") or {}).get("portfolio_id"))
        checks.append({
            "layer": "service",
            "name": "active profile configured",
            "ok": has_profile,
            "warn": not has_profile,
            "codexNative": False,
            "detail": (manifest.get("active_profile") or {}).get("label", "missing active profile"),
        })
        from apps.mcp.models import McpToolCall

        McpToolCall.objects.count()
        checks.append({"layer": "service", "name": "central MCP ledger reachable", "ok": True, "codexNative": False, "detail": "McpToolCall table available"})
    except Exception as exc:
        checks.append({"layer": "service", "name": "central DB reachable", "ok": False, "codexNative": False, "detail": str(exc)})
    export_dirs = ["trading/research", "trading/reports", "trading/audit", "trading/orders", "trading/approvals"]
    for rel in export_dirs:
        path = root / rel
        checks.append({"layer": "service", "name": f"workspace export/cache writable: {rel}", "ok": path.exists() and os.access(path, os.W_OK), "codexNative": False, "detail": "writable" if path.exists() and os.access(path, os.W_OK) else "missing or not writable"})
    return checks


def _enforcement_checks(root: Path) -> list[dict[str, Any]]:
    schemas = ["evidence_pack.schema.json", "fundamental_report.schema.json", "technical_report.schema.json", "news_report.schema.json", "thesis.schema.json", "valuation.schema.json", "portfolio_review.schema.json", "risk_report.schema.json", "order_intent.schema.json", "approval_receipt.schema.json", "execution_result.schema.json", "postmortem_report.schema.json", "audit_event.schema.json"]
    return [
        text_check(root, "enforcement", "command rules configured", ".codex/rules/tradingcodex.rules", "prefix_rule(", True),
        *_codex_mcp_config_checks(root),
        path_check(root, "enforcement", "TradingCodex MCP installed", ".tradingcodex/mcp/server.py", False),
        {"layer": "enforcement", "name": "live broker disabled by default", "ok": not (root / ".tradingcodex" / "mcp" / "adapters" / "live.py").exists(), "detail": "live.py adapter absent"},
        *[path_check(root, "enforcement", f"schema installed: {schema}", f".tradingcodex/schemas/{schema}", False) for schema in schemas],
    ]


def _codex_mcp_config_checks(root: Path) -> list[dict[str, Any]]:
    root_mcp = _read_codex_mcp_config(root / ".codex" / "config.toml")
    execution_mcp = _read_codex_mcp_config(root / ".codex" / "agents" / "execution-operator.toml")
    risk_mcp = _read_codex_mcp_config(root / ".codex" / "agents" / "risk-manager.toml")
    root_tools = set(root_mcp.get("enabled_tools") or [])
    execution_tools = set(execution_mcp.get("enabled_tools") or [])
    risk_tools = set(risk_mcp.get("enabled_tools") or [])
    return [
        {
            "layer": "enforcement",
            "name": "TradingCodex MCP root server configured",
            "ok": bool(root_mcp.get("enabled") is True and root_mcp.get("command") and root_mcp.get("args")),
            "codexNative": True,
            "detail": "enabled with command/args" if root_mcp else "missing mcp_servers.tradingcodex",
        },
        {
            "layer": "enforcement",
            "name": "TradingCodex MCP autostarts local service",
            "ok": root_mcp.get("env", {}).get("TRADINGCODEX_MCP_AUTOSTART_SERVICE") == "1",
            "codexNative": True,
            "detail": "MCP env enables dashboard/service autostart" if root_mcp.get("env", {}).get("TRADINGCODEX_MCP_AUTOSTART_SERVICE") == "1" else "missing TRADINGCODEX_MCP_AUTOSTART_SERVICE=1",
        },
        {
            "layer": "enforcement",
            "name": "head-manager MCP execution submit excluded",
            "ok": "submit_approved_order" not in root_tools,
            "codexNative": True,
            "detail": "root allowlist excludes submit_approved_order" if "submit_approved_order" not in root_tools else "root allowlist includes submit_approved_order",
        },
        {
            "layer": "enforcement",
            "name": "execution-operator MCP execution allowlist configured",
            "ok": "submit_approved_order" in execution_tools,
            "codexNative": True,
            "detail": "execution-operator allowlist includes submit_approved_order" if "submit_approved_order" in execution_tools else "missing submit_approved_order",
        },
        {
            "layer": "enforcement",
            "name": "risk-manager MCP approval allowlist configured",
            "ok": "create_approval_receipt" in risk_tools and "submit_approved_order" not in risk_tools,
            "codexNative": True,
            "detail": "risk-manager can approve but not submit" if "create_approval_receipt" in risk_tools and "submit_approved_order" not in risk_tools else "risk-manager approval/submit allowlist mismatch",
        },
    ]


def _read_codex_mcp_config(path: Path) -> dict[str, Any]:
    try:
        parsed = tomllib.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return parsed.get("mcp_servers", {}).get("tradingcodex", {})


def _information_barrier_checks(root: Path) -> list[dict[str, Any]]:
    return [
        path_check(root, "information-barrier", "capabilities installed", ".tradingcodex/capabilities.yaml", False),
        path_check(root, "information-barrier", "information barriers installed", ".tradingcodex/policies/information-barriers.yaml", False),
        path_check(root, "information-barrier", "restricted list installed", ".tradingcodex/policies/restricted-list.yaml", False),
        path_check(root, "information-barrier", "approvals directory installed", "trading/approvals", False),
    ]


def _improvement_checks(root: Path) -> list[dict[str, Any]]:
    checks = []
    for subagent in EXPECTED_SUBAGENTS:
        checks.append(path_check(root, "improvement", f"subagent installed: {subagent}", f".codex/agents/{subagent}.toml", True))
        checks.append(text_check(root, "improvement", f"subagent permissions profile: {subagent}", f".codex/agents/{subagent}.toml", f'default_permissions = "{ROLE_PERMISSION_PROFILES[subagent]}"', True))
    for skill in EXPECTED_SKILLS:
        checks.append(path_check(root, "improvement", f"skill installed: {skill}", f".agents/skills/{skill}/SKILL.md", False))
    checks.append(path_check(root, "improvement", "head-manager interview profile installed", ".tradingcodex/mainagent/head-manager-interview.md", False))
    checks.append(path_check(root, "improvement", "postmortem workflow installed", ".tradingcodex/workflows/postmortem.yaml", False))
    return checks


def _mcp_checks(root: Path) -> list[dict[str, Any]]:
    return [
        path_check(root, "mcp", "stub execution adapter installed", ".tradingcodex/mcp/adapters/stub-execution.py", False),
        path_check(root, "mcp", "paper trading adapter installed", ".tradingcodex/mcp/adapters/paper-trading.py", False),
        path_check(root, "mcp", "live adapter contract installed", ".tradingcodex/mcp/adapters/live-adapter.contract.md", False),
        text_check(root, "mcp", "MCP server instructions installed", ".tradingcodex/mcp/server.py", "approved action gateway", False),
    ]

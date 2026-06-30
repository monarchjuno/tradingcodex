from __future__ import annotations

import os
import sys
import tomllib
from pathlib import Path
from typing import Any

from tradingcodex_service.application.agents import (
    EXPECTED_SKILLS,
    EXPECTED_SUBAGENTS,
    ROLE_PERMISSION_PROFILES,
    SKILL_SPECS,
    inspect_agent_configuration,
)
from tradingcodex_service.application.runtime import (
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
        text_check(root, "guidance", "session context configured", ".codex/hooks/tradingcodex_hook.py", "tradingcodex-session-context", True),
        text_check(root, "guidance", "three-plane routing configured", ".codex/prompts/base_instructions/head-manager.md", "TradingCodex has three planes", True),
        text_check(root, "guidance", "build gate configured", ".codex/prompts/base_instructions/head-manager.md", "Codex permission is full access", True),
        text_check(root, "guidance", "compact context discipline configured", ".codex/prompts/base_instructions/head-manager.md", "# Context Discipline", True),
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
    schemas = ["research_artifact.schema.json", "evidence_pack.schema.json", "fundamental_report.schema.json", "technical_report.schema.json", "news_report.schema.json", "thesis.schema.json", "valuation.schema.json", "portfolio_review.schema.json", "risk_report.schema.json", "order_ticket.schema.json", "approval_receipt.schema.json", "execution_result.schema.json", "postmortem_report.schema.json", "audit_event.schema.json"]
    return [
        text_check(root, "enforcement", "command rules configured", ".codex/rules/tradingcodex.rules", "prefix_rule(", True),
        *_codex_mcp_config_checks(root),
        path_check(root, "enforcement", "TradingCodex MCP installed", ".tradingcodex/mcp/server.py", False),
        {"layer": "enforcement", "name": "live broker disabled by default", "ok": not (root / ".tradingcodex" / "mcp" / "adapters" / "live.py").exists(), "detail": "no generated live adapter override; live provider gates remain service-controlled"},
        *[path_check(root, "enforcement", f"schema installed: {schema}", f".tradingcodex/schemas/{schema}", False) for schema in schemas],
    ]


def _codex_mcp_config_checks(root: Path) -> list[dict[str, Any]]:
    root_mcp = _read_codex_mcp_config(root / ".codex" / "config.toml")
    execution_mcp = _read_codex_mcp_config(root / ".codex" / "agents" / "execution-operator.toml")
    risk_mcp = _read_codex_mcp_config(root / ".codex" / "agents" / "risk-manager.toml")
    root_tools = set(root_mcp.get("enabled_tools") or [])
    execution_tools = set(execution_mcp.get("enabled_tools") or [])
    risk_tools = set(risk_mcp.get("enabled_tools") or [])
    raw_broker_tools = {"place_order", "replace_order", "cancel_order", "withdraw", "transfer"}
    broker_connector_tools = {
        "list_broker_adapter_providers",
        "scaffold_broker_connector",
        "register_broker_connector",
        "validate_broker_connector_build",
        "get_broker_capability_profile",
        "get_broker_instrument_constraints",
        "preview_order_translation",
    }
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
            "name": "head-manager External MCP Gate tools configured",
            "ok": {"list_external_mcp_connections", "discover_external_mcp_connection", "review_external_mcp_tool"}.issubset(root_tools),
            "codexNative": True,
            "detail": "root allowlist includes External MCP Gate lifecycle tools" if {"list_external_mcp_connections", "discover_external_mcp_connection", "review_external_mcp_tool"}.issubset(root_tools) else "missing External MCP Gate lifecycle tools",
        },
        {
            "layer": "enforcement",
            "name": "head-manager broker connector tools configured",
            "ok": broker_connector_tools.issubset(root_tools),
            "codexNative": True,
            "detail": "root allowlist includes native connector management tools" if broker_connector_tools.issubset(root_tools) else "missing native connector management tools",
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
            "name": "execution-operator raw broker MCP tools excluded",
            "ok": raw_broker_tools.isdisjoint(execution_tools),
            "codexNative": True,
            "detail": "execution-operator uses TradingCodex execution tools only" if raw_broker_tools.isdisjoint(execution_tools) else f"raw broker tools exposed: {', '.join(sorted(raw_broker_tools & execution_tools))}",
        },
        {
            "layer": "enforcement",
            "name": "risk-manager MCP approval allowlist configured",
            "ok": "request_order_approval" in risk_tools and "submit_approved_order" not in risk_tools,
            "codexNative": True,
            "detail": "risk-manager can approve but not submit" if "request_order_approval" in risk_tools and "submit_approved_order" not in risk_tools else "risk-manager approval/submit allowlist mismatch",
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
        text_check(root, "information-barrier", "information barrier ownership contract installed", ".tradingcodex/policies/information-barriers.yaml", "future_role_change_requires", False),
        path_check(root, "information-barrier", "restricted list installed", ".tradingcodex/policies/restricted-list.yaml", False),
        path_check(root, "information-barrier", "approvals directory installed", "trading/approvals", False),
    ]


def _improvement_checks(root: Path) -> list[dict[str, Any]]:
    checks = []
    for subagent in EXPECTED_SUBAGENTS:
        checks.append(path_check(root, "improvement", f"subagent installed: {subagent}", f".codex/agents/{subagent}.toml", True))
        checks.append(text_check(root, "improvement", f"subagent permissions profile: {subagent}", f".codex/agents/{subagent}.toml", f'default_permissions = "{ROLE_PERMISSION_PROFILES[subagent]}"', True))
        try:
            projected = set(inspect_agent_configuration(root, subagent)["projected_skills"])
            effective = set(inspect_agent_configuration(root, subagent)["effective_skills"])
            checks.append({
                "layer": "improvement",
                "name": f"subagent projected skills current: {subagent}",
                "ok": effective.issubset(projected),
                "codexNative": True,
                "detail": "projected TOML matches effective skills" if effective.issubset(projected) else f"missing {sorted(effective - projected)}",
            })
        except Exception as exc:
            checks.append({"layer": "improvement", "name": f"subagent projected skills current: {subagent}", "ok": False, "codexNative": True, "detail": str(exc)})
    for skill in EXPECTED_SKILLS:
        checks.append(path_check(root, "improvement", f"skill installed: {skill}", _skill_check_path(skill), False))
    checks.append(path_check(root, "improvement", "agent index projected", ".tradingcodex/generated/agent-index.json", False))
    checks.append(path_check(root, "improvement", "skill index projected", ".tradingcodex/generated/skill-index.json", False))
    checks.append(path_check(root, "improvement", "projection manifest projected", ".tradingcodex/generated/projection-manifest.json", False))
    checks.append(text_check(root, "improvement", "no-overlap handoff contract installed", ".codex/prompts/base_instructions/head-manager.md", "Only accepted role artifacts move downstream", False))
    checks.append(text_check(root, "improvement", "decision quality spine installed", ".codex/prompts/base_instructions/head-manager.md", "Decision Quality Spine", False))
    checks.append(text_check(root, "improvement", "workflow skill installed", ".agents/skills/tcx-workflow/SKILL.md", "compact hook context", False))
    checks.append(text_check(root, "improvement", "artifact supervisor loop skill installed", ".agents/skills/tcx-workflow/SKILL.md", "Artifact Supervisor Loop", False))
    checks.append(text_check(root, "improvement", "loop state hook installed", ".codex/hooks/tradingcodex_hook.py", "workflow-loop-state.json", True))
    checks.append(text_check(root, "improvement", "run-specific loop state hook installed", ".codex/hooks/tradingcodex_hook.py", "session-workflow-runs.json", True))
    checks.append(text_check(root, "improvement", "artifact follow-up contract schema installed", ".tradingcodex/schemas/research_artifact.schema.json", "follow_up_requests", False))
    checks.append({
        "layer": "improvement",
        "name": "loop state file current or not yet started",
        "ok": True,
        "warn": not (root / ".tradingcodex" / "mainagent" / "workflow-loop-state.json").exists(),
        "codexNative": True,
        "detail": "found .tradingcodex/mainagent/workflow-loop-state.json" if (root / ".tradingcodex" / "mainagent" / "workflow-loop-state.json").exists() else "no workflow-loop-state.json until the next routed workflow",
    })
    checks.append(path_check(root, "improvement", "forecast ledger directory installed", "trading/forecasts", False))
    checks.append(text_check(root, "improvement", "build skill installed", ".agents/skills/tcx-build/SKILL.md", "Build mode may create live-capable providers", False))
    checks.append(text_check(root, "improvement", "strategy root skill config installed", ".codex/config.toml", "# BEGIN TradingCodex strategy skills", True))
    checks.append(path_check(root, "improvement", "postmortem workflow installed", ".tradingcodex/workflows/postmortem.yaml", False))
    return checks


def _skill_check_path(skill: str) -> str:
    spec = SKILL_SPECS[skill]
    if spec.scope == "subagent_shared":
        return f".tradingcodex/subagents/skills/shared/{skill}/SKILL.md"
    if spec.scope == "subagent_role":
        role = spec.owner_roles[0]
        return f".tradingcodex/subagents/skills/{role}/{skill}/SKILL.md"
    return f".agents/skills/{skill}/SKILL.md"


def _mcp_checks(root: Path) -> list[dict[str, Any]]:
    return [
        text_check(root, "mcp", "MCP server instructions installed", ".tradingcodex/mcp/server.py", "approved action gateway", False),
    ]

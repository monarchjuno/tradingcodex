from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import sys
import tomllib
from pathlib import Path

from django.contrib.auth import get_user_model
from django.test import Client

from tradingcodex_cli.generator import (
    DEFAULT_MODULE_IDS,
    bootstrap_workspace,
    copy_template_tree,
    load_module_registry,
    resolve_module_graph,
    templates_dir,
)
from tradingcodex_service.domain import (
    build_subagent_starter_prompt,
    call_tool,
    ensure_runtime_database,
    mcp_handle_rpc,
    validate_order_intent,
)
from tradingcodex_service.mcp_runtime import static_mcp_tools
from tradingcodex_service.version import TRADINGCODEX_VERSION


ROOT = Path(__file__).resolve().parents[1]


def run(
    args: list[str],
    cwd: Path,
    input_text: str | None = None,
    expect_ok: bool = True,
    env_extra: dict[str, str | None] | None = None,
) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    for key, value in (env_extra or {}).items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value
    result = subprocess.run(args, cwd=cwd, input=input_text, text=True, capture_output=True, env=env, timeout=120)
    if expect_ok and result.returncode != 0:
        raise AssertionError(f"{args} failed\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}")
    if not expect_ok and result.returncode == 0:
        raise AssertionError(f"{args} unexpectedly succeeded\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}")
    return result


def make_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    result = bootstrap_workspace(workspace, force=True)
    assert result["modules"]
    return workspace


def test_template_copy_skips_python_bytecode_cache(tmp_path: Path) -> None:
    source = tmp_path / "source"
    target = tmp_path / "target"
    cache = source / "__pycache__"
    cache.mkdir(parents=True)
    (source / "script.py").write_text("print('{{PROJECT_NAME}}')\n", encoding="utf-8")
    (cache / "script.cpython-314.pyc").write_bytes(b"\x94\x00\x00\x00binary-bytecode")
    (source / ".DS_Store").write_bytes(b"\x00\x00\x00\x01Bud1\x00binary-finder-metadata")

    copy_template_tree(source, target, {"PROJECT_NAME": "demo"})

    assert (target / "script.py").read_text(encoding="utf-8") == "print('demo')\n"
    assert not (target / "__pycache__").exists()
    assert not (target / ".DS_Store").exists()
    assert not list(target.rglob("*.pyc"))


def test_workspace_template_module_contracts(tmp_path: Path) -> None:
    registry = load_module_registry(templates_dir())
    assert set(DEFAULT_MODULE_IDS).issubset(registry)
    for module_id, module in registry.items():
        assert module.id == module_id
        assert module.dir.name == module_id
        for dependency in module.manifest.get("requires", {}).get("modules", []):
            assert dependency in registry

    resolved = resolve_module_graph(registry, DEFAULT_MODULE_IDS)
    assert [module.id for module in resolved]

    workspace = make_workspace(tmp_path)
    for rel in [
        "AGENTS.md",
        ".codex/config.toml",
        ".codex/prompts/base_instructions/head-manager.md",
        ".codex/hooks/tradingcodex_hook.py",
        ".agents/skills/orchestrate-workflow/SKILL.md",
        ".tradingcodex/config.yaml",
        "trading/research/.gitkeep",
        "tcx",
    ]:
        assert (workspace / rel).exists(), rel
    assert not (workspace / "package.json").exists()
    assert not list(workspace.rglob("__pycache__"))
    assert not list(workspace.rglob("*.pyc"))
    assert not (workspace / ".tradingcodex" / "state" / "tradingcodex.sqlite3").exists()


def test_python_generator_creates_workspace_contract(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    assert (workspace / "pyproject.toml").exists()
    assert f'version = "{TRADINGCODEX_VERSION}"' in (workspace / "pyproject.toml").read_text(encoding="utf-8")
    assert not (workspace / "package.json").exists()
    assert (workspace / "tcx").exists()
    assert not (workspace / "tradingcodex").exists()
    assert (workspace / ".tradingcodex" / "cli.py").exists()
    assert (workspace / ".tradingcodex" / "mcp" / "server.py").exists()
    assert (workspace / ".codex" / "hooks" / "tradingcodex_hook.py").exists()
    generated_text = "\n".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in workspace.rglob("*")
        if path.is_file() and path.suffix.lower() in {"", ".md", ".toml", ".yaml", ".yml", ".json", ".py"}
    )
    forbidden_disclosure_system_names = ["k-" + "da" + "rt", "".join(["D", "A", "R", "T"])]
    for forbidden in forbidden_disclosure_system_names:
        assert forbidden.lower() not in generated_text.lower()
    assert "official regulator or exchange disclosure sources" in generated_text
    assert "./tradingcodex" not in generated_text
    orchestrate_guidance = (workspace / ".agents" / "skills" / "orchestrate-workflow" / "SKILL.md").read_text(encoding="utf-8")
    manage_guidance = (workspace / ".agents" / "skills" / "manage-subagents" / "SKILL.md").read_text(encoding="utf-8")
    orchestration_guidance = orchestrate_guidance + "\n" + manage_guidance
    assert "fork_context=false" in orchestration_guidance
    assert "routing-unverified" in orchestration_guidance
    assert "This skill owns workflow sequencing" in orchestration_guidance
    assert "This skill owns fixed-role subagent mechanics" in orchestration_guidance
    assert "Subagent briefs are assignment envelopes" in manage_guidance
    assert "Workflow consent:" in manage_guidance
    assert "ROLE CARD:" not in manage_guidance
    assert "fork_turns" not in orchestration_guidance
    assert "task_name" not in orchestration_guidance
    hook_text = (workspace / ".codex" / "hooks" / "tradingcodex_hook.py").read_text(encoding="utf-8")
    assert 'payload.get("agent_type")' in hook_text
    assert not (workspace / ".tradingcodex" / "state" / "tradingcodex.sqlite3").exists()
    assert not (workspace / ".tradingcodex" / "state" / "paper-portfolio.json").exists()
    db_path = run(["./tcx", "db", "path"], workspace).stdout.strip()
    assert db_path != str((workspace / ".tradingcodex" / "state" / "tradingcodex.sqlite3").resolve())
    status = json.loads(run(["./tcx", "subagents", "status"], workspace).stdout)
    assert status["installed_count"] == 9
    assert status["fixed_roster_ok"] is True
    assert status["skills_installed"] == 21
    doctor = run(["./tcx", "doctor"], workspace).stdout
    assert "TradingCodex doctor passed" in doctor
    assert "improvement" in doctor
    assert "TradingCodex MCP autostarts local service" in doctor
    assert "head-manager MCP execution submit excluded" in doctor
    assert "execution-operator MCP execution allowlist configured" in doctor
    assert "risk-manager MCP approval allowlist configured" in doctor
    improvement_doctor = run(["./tcx", "doctor", "--layer", "improvement"], workspace).stdout
    assert "TradingCodex doctor passed" in improvement_doctor
    assert "skill installed: orchestrate-workflow" in improvement_doctor
    legacy_doctor = run(["./tcx", "doctor", "--layer", "task-harness"], workspace).stdout
    assert "TradingCodex doctor passed" in legacy_doctor
    assert "improvement" in legacy_doctor
    hooks = json.loads((workspace / ".codex" / "hooks.json").read_text(encoding="utf-8"))["hooks"]
    expected_hook_events = {
        "SessionStart",
        "PreToolUse",
        "PermissionRequest",
        "PostToolUse",
        "UserPromptSubmit",
        "SubagentStart",
        "SubagentStop",
        "Stop",
    }
    assert set(hooks) == expected_hook_events
    assert "matcher" not in hooks["UserPromptSubmit"][0]
    assert "matcher" not in hooks["Stop"][0]
    assert hooks["PreToolUse"][0]["matcher"] == "Bash|mcp__.*"
    assert hooks["SubagentStart"][0]["matcher"]
    service_usage = run(["./tcx", "service", "nope"], workspace, expect_ok=False)
    assert "Usage: tcx service runserver [addrport] [django runserver args]" in service_usage.stderr
    agent_files = sorted((workspace / ".codex" / "agents").glob("*.toml"))
    assert len(agent_files) == 9
    actual_mcp_tools = {tool["name"] for tool in static_mcp_tools()}
    stale_mcp_tool_names = {"evaluate_policy", "get_positions_snapshot", "write_audit_event"}
    root_config = tomllib.loads((workspace / ".codex" / "config.toml").read_text(encoding="utf-8"))
    assert root_config["default_permissions"] == "tradingcodex"
    assert root_config["model_instructions_file"] == "prompts/base_instructions/head-manager.md"
    assert "developer_instructions" not in root_config
    head_manager_instructions = (workspace / ".codex" / "prompts" / "base_instructions" / "head-manager.md").read_text(encoding="utf-8")
    assert "You are the `head-manager` agent" in head_manager_instructions
    assert "Codex-based local trading harness" in head_manager_instructions
    assert "asset-management workflow team" in head_manager_instructions
    assert "not an autonomous trading bot" not in head_manager_instructions
    assert "# How you work" in head_manager_instructions
    assert "# TradingCodex guardrails" in head_manager_instructions
    assert "# Tool guidelines" in head_manager_instructions
    assert not re.search(r"[\uac00-\ud7a3]", head_manager_instructions)
    assert "Use repo skills for repeatable workflow procedures" in head_manager_instructions
    assert "This base instruction owns" not in head_manager_instructions
    assert "## Operating style" in head_manager_instructions
    assert "Head-manager skill routing" in head_manager_instructions
    assert "apply_patch" in head_manager_instructions
    assert "investment dispatch gate" in head_manager_instructions
    workspace_agents = (workspace / "AGENTS.md").read_text(encoding="utf-8")
    assert "Codex agent working expectations" in workspace_agents
    assert "Follow every applicable `AGENTS.md`" in workspace_agents
    assert "Keep prompts lean" in workspace_agents
    assert root_config["permissions"]["tradingcodex"]["extends"] == ":workspace"
    assert root_config["permissions"]["tradingcodex"]["network"]["enabled"] is False
    expected_tcx_mcp_args = ["--refresh", "--python", "3.14", "--from", "tradingcodex", "python", "-m", "tradingcodex_cli", "mcp", "stdio"]
    root_mcp = root_config["mcp_servers"]["tradingcodex"]
    assert root_mcp["command"] == "uvx"
    assert root_mcp["args"] == expected_tcx_mcp_args
    assert root_mcp["enabled"] is True
    assert root_mcp["env"]["TRADINGCODEX_MCP_AUTOSTART_SERVICE"] == "1"
    assert root_mcp["env"]["TRADINGCODEX_SERVICE_ADDR"] == "127.0.0.1:8000"
    assert root_mcp["env"]["TRADINGCODEX_WORKSPACE_ROOT"] == str(workspace)
    assert set(root_mcp["enabled_tools"]).issubset(actual_mcp_tools)
    assert stale_mcp_tool_names.isdisjoint(root_mcp["enabled_tools"])
    assert "simulate_policy" in root_mcp["enabled_tools"]
    assert "record_audit_event" in root_mcp["enabled_tools"]
    assert "get_portfolio_snapshot" in root_mcp["enabled_tools"]
    assert "submit_approved_order" not in root_mcp["enabled_tools"]
    assert "cancel_approved_order" not in root_mcp["enabled_tools"]
    for agent_file in agent_files:
        agent_config = agent_file.read_text(encoding="utf-8")
        agent_toml = tomllib.loads(agent_config)
        assert agent_toml["name"] == agent_file.stem
        assert agent_toml["description"]
        assert agent_toml["developer_instructions"]
        assert 'model = "gpt-5.5"' in agent_config
        assert 'model_reasoning_effort = "high"' in agent_config
        agent_mcp = agent_toml["mcp_servers"]["tradingcodex"]
        assert agent_mcp["command"] == "uvx"
        assert agent_mcp["args"] == expected_tcx_mcp_args
        assert agent_mcp["env"]["TRADINGCODEX_MCP_AUTOSTART_SERVICE"] == "1"
        assert agent_mcp["env"]["TRADINGCODEX_WORKSPACE_ROOT"] == str(workspace)
        configured_tools = set(agent_mcp.get("enabled_tools", [])) | set(agent_mcp.get("disabled_tools", []))
        assert configured_tools.issubset(actual_mcp_tools), agent_file
        assert stale_mcp_tool_names.isdisjoint(configured_tools), agent_file
        if agent_file.stem == "risk-manager":
            assert "create_approval_receipt" in agent_mcp["enabled_tools"]
            assert "submit_approved_order" in agent_mcp["disabled_tools"]
        if agent_file.stem == "execution-operator":
            assert "submit_approved_order" in agent_mcp["enabled_tools"]
            assert "create_approval_receipt" not in agent_mcp["enabled_tools"]
    assert run(["./tcx", "skills", "list"], workspace).stdout.splitlines() == [
        "orchestrate-workflow",
        "head-manager-interview",
        "postmortem",
    ]
    assert len(run(["./tcx", "skills", "list", "--all"], workspace).stdout.splitlines()) == 21


def test_init_prepares_central_django_runtime(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    home = tmp_path / "tc-home"
    result = run(
        [sys.executable, "-m", "tradingcodex_cli", "init", str(workspace)],
        ROOT,
        env_extra={"TRADINGCODEX_DB_NAME": None, "TRADINGCODEX_HOME": str(home)},
    )
    db_path = home / "state" / "tradingcodex.sqlite3"

    assert f"Django DB: {db_path}" in result.stdout
    assert "./tcx doctor" in result.stdout
    assert db_path.exists()
    assert not (workspace / ".tradingcodex" / "state" / "tradingcodex.sqlite3").exists()
    assert 'DJANGO_SETTINGS_MODULE="${DJANGO_SETTINGS_MODULE:-tradingcodex_service.settings}"' in (workspace / "tcx").read_text(encoding="utf-8")
    generated_cli = (workspace / ".tradingcodex" / "cli.py").read_text(encoding="utf-8")
    assert 'os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tradingcodex_service.settings")' in generated_cli
    assert "from tradingcodex_cli.__main__ import main" in generated_cli
    generated_mcp_server = (workspace / ".tradingcodex" / "mcp" / "server.py").read_text(encoding="utf-8")
    assert 'os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tradingcodex_service.settings")' in generated_mcp_server
    assert "maybe_autostart_service" in generated_mcp_server

    with sqlite3.connect(db_path) as connection:
        table_names = {row[0] for row in connection.execute("select name from sqlite_master where type = 'table'")}
        assert "harness_workspacecontext" in table_names
        assert "mcp_mcptooldefinition" in table_names
        assert connection.execute("select count(*) from django_migrations where app = 'orders' and name = '0001_initial'").fetchone()[0] == 1
        assert connection.execute("select count(*) from harness_workspacecontext where path = ?", (str(workspace.resolve()),)).fetchone()[0] == 1


def test_init_current_directory_and_overwrite_language(tmp_path: Path) -> None:
    workspace = tmp_path / "current-workspace"
    workspace.mkdir()
    home = tmp_path / "tc-home-current"
    env_extra = {"TRADINGCODEX_DB_NAME": None, "TRADINGCODEX_HOME": str(home)}

    result = run([sys.executable, "-m", "tradingcodex_cli", "init", "."], workspace, env_extra=env_extra)

    assert f"TradingCodex workspace created: {workspace.resolve()}" in result.stdout
    assert (workspace / "tcx").exists()
    assert json.loads((workspace / ".tradingcodex" / "generated" / "module-lock.json").read_text(encoding="utf-8"))["tradingcodex_version"] == TRADINGCODEX_VERSION

    repeated = run([sys.executable, "-m", "tradingcodex_cli", "init", "."], workspace, expect_ok=False, env_extra=env_extra)
    assert "--overwrite" in repeated.stderr
    assert "--force" not in repeated.stderr

    overwrite = run([sys.executable, "-m", "tradingcodex_cli", "init", ".", "--overwrite"], workspace, env_extra=env_extra)
    assert f"TradingCodex workspace created: {workspace.resolve()}" in overwrite.stdout

    help_text = run([sys.executable, "-m", "tradingcodex_cli", "init", "--help"], workspace, env_extra=env_extra).stdout
    assert "--overwrite" in help_text
    assert "--force" not in help_text


def test_init_allows_git_initialized_empty_current_directory(tmp_path: Path) -> None:
    workspace = tmp_path / "git-workspace"
    (workspace / ".git").mkdir(parents=True)
    home = tmp_path / "tc-home-git-current"
    env_extra = {"TRADINGCODEX_DB_NAME": None, "TRADINGCODEX_HOME": str(home)}

    result = run([sys.executable, "-m", "tradingcodex_cli", "init", "."], workspace, env_extra=env_extra)

    assert f"TradingCodex workspace created: {workspace.resolve()}" in result.stdout
    assert (workspace / ".git").is_dir()
    assert (workspace / "AGENTS.md").exists()
    assert (workspace / ".codex" / "config.toml").exists()
    assert (workspace / "tcx").exists()


def test_generated_tcx_wrapper_uses_recorded_workspace_root_from_other_cwd(tmp_path: Path) -> None:
    workspace = tmp_path / "absolute-wrapper-workspace"
    home = tmp_path / "tc-home-absolute-wrapper"
    env_extra = {"TRADINGCODEX_DB_NAME": None, "TRADINGCODEX_HOME": str(home)}
    run([sys.executable, "-m", "tradingcodex_cli", "init", str(workspace)], ROOT, env_extra=env_extra)

    doctor = run([str(workspace / "tcx"), "doctor"], ROOT, env_extra=env_extra)

    assert "TradingCodex doctor passed" in doctor.stdout
    assert f"workspace={workspace.resolve()}" in doctor.stdout


def test_starter_prompt_keeps_negated_actions_out_of_execution() -> None:
    macro = build_subagent_starter_prompt("rates oil impact on NVDA position no order")
    assert "Workflow lane: portfolio_risk_review" in macro
    assert "macro-analyst" in macro
    assert "execution-operator" not in macro
    meta_macro = build_subagent_starter_prompt("rates oil impact on my NVDA position, no order. Verify routing and blocked order/approval/execution actions.")
    assert "Workflow lane: portfolio_risk_review" in meta_macro
    assert "macro-analyst" in meta_macro
    assert "execution-operator" not in meta_macro
    blocked_wording = build_subagent_starter_prompt("rates and oil impact on my NVDA position, no order. Do not place trades. Even with blocked action wording like execute, submit, approve, or order, verify portfolio_risk_review routing and no execution-operator.")
    assert "Workflow lane: portfolio_risk_review" in blocked_wording
    assert "macro-analyst" in blocked_wording
    blocked_spawn_line = next(line for line in blocked_wording.splitlines() if line.startswith("Spawn these fixed role subagents"))
    assert "execution-operator" not in blocked_spawn_line
    earnings = build_subagent_starter_prompt("NVDA earnings preview and catalyst review, no order and no trading")
    assert "Workflow lane: thesis_review" in earnings
    assert "fundamental-analyst" in earnings
    assert "news-analyst" in earnings
    assert "valuation-analyst" in earnings
    assert "execution-operator" not in earnings
    crypto = build_subagent_starter_prompt("BTC trend review no trading")
    assert "Investment universe: public_crypto" in crypto
    assert "instrument-analyst" in crypto
    assert "fundamental-analyst" not in crypto
    assert "execution-operator" not in crypto


def test_workspace_cli_order_policy_and_execution(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    order = {
        "id": "smoke-order-2",
        "symbol": "AAPL",
        "side": "buy",
        "quantity": 1,
        "limit_price": 1000,
        "currency": "KRW",
        "broker": "paper-trading",
        "estimated_notional_krw": 1000,
        "created_by": "portfolio-manager",
        "created_at": "2026-01-01T00:00:00Z",
    }
    order_path = workspace / "trading" / "orders" / "draft" / "smoke-order-2.order_intent.json"
    order_path.write_text(json.dumps(order), encoding="utf-8")
    assert json.loads(run(["./tcx", "validate", "order", str(order_path.relative_to(workspace))], workspace).stdout)["valid"] is True
    approval = json.loads(run(["./tcx", "approve", str(order_path.relative_to(workspace)), "--approved-by", "risk-manager"], workspace).stdout)
    assert approval["status"] == "approved"
    execution = json.loads(run(["./tcx", "mcp", "call", "submit_approved_order", "--order-intent-id", order["id"]], workspace).stdout)
    assert execution["status"] == "accepted"
    assert execution["db_canonical"] is True
    assert execution["idempotency_key"].startswith("submit:")
    assert execution["result"]["portfolio_id"] == "default-paper"
    assert (workspace / "trading" / "orders" / "executed" / "smoke-order-2.execution_result.json").exists()
    duplicate = json.loads(run(["./tcx", "mcp", "call", "submit_approved_order", "--order-intent-id", order["id"]], workspace, expect_ok=False).stdout)
    assert duplicate["status"] == "rejected"
    assert "already has an execution result" in "\n".join(duplicate["reasons"])
    snapshot = json.loads(run(["./tcx", "mcp", "call", "get_portfolio_snapshot"], workspace).stdout)
    assert snapshot["positions"]["AAPL"]["quantity"] == 1.0


def test_restricted_and_live_orders_are_blocked(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    blocked = {
        "id": "blocked",
        "symbol": "BLOCKED",
        "side": "buy",
        "quantity": 1,
        "limit_price": 1000,
        "currency": "KRW",
        "broker": "paper-trading",
        "estimated_notional_krw": 1000,
        "created_by": "portfolio-manager",
        "created_at": "2026-01-01T00:00:00Z",
    }
    result = validate_order_intent(workspace, {"principal_id": "portfolio-manager", "order_intent": blocked})
    assert result["valid"] is False
    assert "symbol is restricted: BLOCKED" in "\n".join(result["reasons"])
    live = {**blocked, "id": "live", "symbol": "TSLA", "broker": "live"}
    live_result = validate_order_intent(workspace, {"principal_id": "portfolio-manager", "order_intent": live})
    assert live_result["valid"] is False
    assert "live broker adapter is not installed" in "\n".join(live_result["reasons"])
    self_approval = call_tool(workspace, "simulate_policy", {
        "principal_id": "head-manager",
        "action": "approval.self_issue",
        "resource": "*",
    })
    assert self_approval["decision"] == "deny"
    approval_create = call_tool(workspace, "simulate_policy", {
        "principal_id": "head-manager",
        "action": "approval_receipt.create",
        "resource": "*",
    })
    assert approval_create["decision"] == "deny"
    assert "only risk-manager can create approval receipts" in "\n".join(approval_create["reasons"])
    direct_broker = call_tool(workspace, "simulate_policy", {
        "principal_id": "head-manager",
        "action": "broker_api.call_direct",
        "resource": "live_broker_api",
    })
    assert direct_broker["decision"] == "deny"
    live_submit = call_tool(workspace, "simulate_policy", {
        "principal_id": "head-manager",
        "action": "execution.submit_live_order",
        "resource": "live_broker_adapter",
    })
    assert live_submit["decision"] == "deny"
    execute_order = call_tool(workspace, "simulate_policy", {
        "principal_id": "head-manager",
        "action": "execute_order",
        "resource": "TSLA",
    })
    assert execute_order["decision"] == "deny"


def test_capabilities_are_enforced_before_mcp_and_policy(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    ensure_runtime_database(workspace)
    from apps.policy.models import Capability, Principal
    from apps.policy.services import sync_builtin_principals_and_capabilities

    sync_builtin_principals_and_capabilities()
    capability = Capability.objects.get(principal__principal_id="fundamental-analyst", action="research_artifact.write")
    capability.effect = "deny"
    capability.save(update_fields=["effect"])
    forbidden = mcp_handle_rpc(workspace, {
        "jsonrpc": "2.0",
        "id": 10,
        "method": "tools/call",
        "params": {
            "name": "create_research_artifact",
            "arguments": {
                "principal_id": "fundamental-analyst",
                "artifact_id": "capability-denied-note",
                "title": "Denied",
                "markdown": "# Denied",
            },
        },
    })
    assert forbidden and "capability denied" in forbidden["error"]["message"]

    capability.effect = "allow"
    capability.save(update_fields=["effect"])
    Principal.objects.filter(principal_id="fundamental-analyst").update(active=False)
    inactive = mcp_handle_rpc(workspace, {
        "jsonrpc": "2.0",
        "id": 11,
        "method": "tools/call",
        "params": {
            "name": "create_research_artifact",
            "arguments": {
                "principal_id": "fundamental-analyst",
                "artifact_id": "inactive-principal-note",
                "title": "Inactive",
                "markdown": "# Inactive",
            },
        },
    })
    assert inactive and "not allowed" in inactive["error"]["message"]
    Principal.objects.filter(principal_id="fundamental-analyst").update(active=True)

    Capability.objects.filter(principal__principal_id="portfolio-manager", action="order_intent.validate").update(effect="deny")
    order = {
        "id": "capability-order",
        "symbol": "AAPL",
        "side": "buy",
        "quantity": 1,
        "limit_price": 1000,
        "currency": "KRW",
        "broker": "paper-trading",
        "estimated_notional_krw": 1000,
        "created_by": "portfolio-manager",
        "created_at": "2026-01-01T00:00:00Z",
    }
    result = validate_order_intent(workspace, {"principal_id": "portfolio-manager", "order_intent": order})
    assert result["valid"] is False
    assert "capability denied" in "\n".join(result["reasons"])


def test_mcp_stdio_and_http_minimum_surface(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    initialized = mcp_handle_rpc(workspace, {"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert initialized and initialized["result"]["serverInfo"]["name"] == "tradingcodex"
    tools = mcp_handle_rpc(workspace, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    assert tools and any(tool["name"] == "submit_approved_order" for tool in tools["result"]["tools"])
    assert any(tool["name"] == "create_research_artifact" for tool in tools["result"]["tools"])
    submit_tool = next(tool for tool in tools["result"]["tools"] if tool["name"] == "submit_approved_order")
    assert submit_tool["annotations"]["risk_level"] == "execution"
    assert submit_tool["annotations"]["allowed_roles"] == ["execution-operator"]
    assert submit_tool["annotations"]["experimental"] is True
    tool_names = {tool["name"] for tool in tools["result"]["tools"]}
    assert "index_research_artifact_embedding" not in tool_names
    assert "semantic_search_research_artifacts" not in tool_names
    assert "ai_review_research_artifact" not in tool_names
    assert "evaluate_policy" not in tool_names
    assert "get_positions_snapshot" not in tool_names
    assert "write_audit_event" not in tool_names
    assert "simulate_policy" in tool_names
    assert "get_portfolio_snapshot" in tool_names
    assert "record_audit_event" in tool_names
    stdio_input = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}) + "\n"
    stdio = run(["./tcx", "mcp", "stdio"], workspace, input_text=stdio_input)
    assert "submit_approved_order" in stdio.stdout
    client = Client(REMOTE_ADDR="127.0.0.1")
    response = client.get("/mcp")
    assert response.status_code == 200
    assert response.json()["endpoint"] == "/mcp"
    batch = client.post(
        "/mcp",
        data=json.dumps([
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 2, "method": "resources/list"},
        ]),
        content_type="application/json",
    )
    assert batch.status_code == 200
    assert isinstance(batch.json(), list)


def test_django_ninja_control_api() -> None:
    client = Client(REMOTE_ADDR="127.0.0.1")
    assert client.get("/api/health").json()["status"] == "ok"
    status = client.get("/api/harness/status").json()
    assert status["expected_count"] == 9
    assert status["skills_installed"] == 21
    assert status["user_visible_skills"] == ["orchestrate-workflow", "head-manager-interview", "postmortem"]
    assert client.get("/api/harness/skills").json()["skills"] == status["user_visible_skills"]
    assert len(client.get("/api/harness/skills?include_internal=true").json()["skills"]) == 21
    assert len(client.get("/api/subagents").json()) == 9
    assert "portfolio-review" in client.get("/api/subagents/portfolio-manager/skills").json()["skills"]
    response = client.post(
        "/api/policy/simulate",
        data=json.dumps({"principal_id": "execution-operator", "action": "mcp.tradingcodex.submit_approved_order"}),
        content_type="application/json",
    )
    assert response.status_code == 200
    assert response.json()["decision"] in {"allow", "deny"}


def test_custom_django_admin_dashboard() -> None:
    ensure_runtime_database(ROOT)
    User = get_user_model()
    user, _ = User.objects.get_or_create(username="admin-ui-test", defaults={"is_staff": True, "is_superuser": True})
    user.is_staff = True
    user.is_superuser = True
    user.set_password("admin")
    user.save()

    anonymous = Client(REMOTE_ADDR="127.0.0.1")
    login_response = anonymous.get("/admin/login/")
    assert login_response.status_code == 200
    assert "tcx Control Plane" in login_response.content.decode()
    assert "tradingcodex_admin/admin.css" in login_response.content.decode()

    client = Client(REMOTE_ADDR="127.0.0.1")
    client.force_login(user)
    response = client.get("/admin/")
    body = response.content.decode()
    assert response.status_code == 200
    assert "What do you want to check?" in body
    assert "Open research memory" in body
    assert "Check current state" in body
    assert "Review drafts and approvals" in body
    assert "Review restrictions and blocks" in body
    assert "Start with this flow" in body
    assert "Advanced admin tables" in body
    assert "Central investment ledger connected" in body
    assert "tcx Home" in body
    assert "Capabilities" in body
    assert "Capabilitys" not in body
    assert "tradingcodex_admin/admin.css" in body

    favicon_response = client.get("/favicon.ico")
    assert favicon_response.status_code == 302
    assert favicon_response["Location"].endswith("/static/tradingcodex_admin/favicon.svg")


def test_mcp_admin_service_actions_write_audit_events() -> None:
    ensure_runtime_database(ROOT)
    from apps.audit.models import AuditEvent
    from apps.mcp.models import McpToolDefinition
    from apps.mcp.services import set_mcp_tools_enabled, sync_builtin_mcp_registry
    from tradingcodex_service.mcp_runtime import prepare_mcp_runtime

    prepare_mcp_runtime(ROOT)
    before = AuditEvent.objects.count()
    queryset = McpToolDefinition.objects.filter(name="submit_approved_order")
    assert queryset.exists()
    set_mcp_tools_enabled(queryset, False, "admin-service-test")
    set_mcp_tools_enabled(queryset, True, "admin-service-test")
    sync_builtin_mcp_registry("admin-service-test")
    assert AuditEvent.objects.count() >= before + 3
    assert AuditEvent.objects.filter(action="mcp_tool.disabled", actor_principal="admin-service-test").exists()
    assert AuditEvent.objects.filter(action="mcp_tool.enabled", actor_principal="admin-service-test").exists()
    assert AuditEvent.objects.filter(action="mcp_tool_registry.synced", actor_principal="admin-service-test").exists()


def test_product_web_dashboard_routes_render_english_canvas() -> None:
    ensure_runtime_database(ROOT)
    client = Client(REMOTE_ADDR="127.0.0.1")

    dashboard = client.get("/")
    assert dashboard.status_code == 200
    body = dashboard.content.decode()
    assert "Harness-first dashboard" in body
    assert "Guardrails and Improvement" in body
    assert "Guidance" in body
    assert "Enforcement" in body
    assert "Validation feedback" in body
    assert "head-manager" in body
    assert "fundamental-analyst" in body
    assert "execution-operator" in body
    assert "MCP execution boundary" in body
    assert "static/tradingcodex_web/app.css" in body
    assert "static/vendor/htmx/htmx.min.js" in body
    assert "static/vendor/alpine/alpine.min.js" in body
    assert not re.search(r"[\uac00-\ud7a3]", body)
    assert "TRADINGCODEX_API_KEY" not in body

    harness = client.get("/harness/")
    harness_body = harness.content.decode()
    assert harness.status_code == 200
    assert harness_body.count("tc-node") >= 10
    assert "Head Manager" in harness_body
    assert "Guardrails and Improvement sit under the harness" in harness_body
    assert "Not bypassable from web" in harness_body
    assert not re.search(r"[\uac00-\ud7a3]", harness_body)

    for route in ["/research/", "/portfolio/", "/orders/", "/policy/", "/activity/", "/workflow/starter-prompt/"]:
        response = client.get(route)
        assert response.status_code == 200
        assert "tcx" in response.content.decode()
        assert not re.search(r"[\uac00-\ud7a3]", response.content.decode())

    admin_response = client.get("/admin/login/")
    assert admin_response.status_code == 200
    assert "tcx Control Plane" in admin_response.content.decode()


def test_product_web_role_inspector_and_topology_helpers() -> None:
    client = Client(REMOTE_ADDR="127.0.0.1")

    response = client.get("/harness/roles/portfolio-manager/")
    body = response.content.decode()
    assert response.status_code == 200
    assert "Portfolio Manager" in body
    assert "portfolio-review" in body
    assert "create-order-intent" in body
    assert "validate_order_intent" in body
    assert "No self-approval" in body

    from tradingcodex_service.domain import get_harness_topology, get_role_detail

    topology = get_harness_topology(ROOT)
    roles = {node["role"] for node in topology["nodes"]}
    node_by_role = {node["role"]: node for node in topology["nodes"]}
    assert "head-manager" in roles
    assert len(roles - {"head-manager"}) == 9
    assert node_by_role["head-manager"]["y"] < node_by_role["fundamental-analyst"]["y"]
    assert node_by_role["fundamental-analyst"]["y"] < node_by_role["valuation-analyst"]["y"]
    assert node_by_role["valuation-analyst"]["y"] < node_by_role["portfolio-manager"]["y"]
    assert node_by_role["portfolio-manager"]["y"] < node_by_role["risk-manager"]["y"]
    assert node_by_role["risk-manager"]["y"] < node_by_role["execution-operator"]["y"]
    assert topology["boundary"]["x"] < node_by_role["execution-operator"]["x"]
    assert [layer["label"] for layer in topology["layers"]] == [
        "Coordinator",
        "Research roles",
        "Valuation",
        "Portfolio fit",
        "Risk approval",
        "MCP execution",
    ]
    assert {edge["group"] for edge in topology["edges"]} == {
        "dispatch",
        "research-handoff",
        "portfolio-risk-gate",
        "approval-gate",
        "execution-gate",
    }
    detail = get_role_detail("execution-operator", ROOT)
    assert any(tool["name"] == "submit_approved_order" for tool in detail["allowed_tools"])
    assert "No raw broker API." in detail["forbidden_actions"]


def test_product_web_does_not_create_approvals_or_executions(monkeypatch) -> None:
    ensure_runtime_database(ROOT)
    from apps.audit.models import AuditEvent
    from apps.mcp.models import McpToolCall
    from apps.orders.models import ApprovalReceipt, ExecutionResult, OrderIntent

    def forbidden_execution(*args, **kwargs):
        raise AssertionError("product web route attempted an execution-sensitive action")

    monkeypatch.setattr("tradingcodex_service.domain.submit_approved_order", forbidden_execution)
    monkeypatch.setattr("tradingcodex_service.domain.create_approval_receipt", forbidden_execution)

    before = (
        OrderIntent.objects.count(),
        ApprovalReceipt.objects.count(),
        ExecutionResult.objects.count(),
        McpToolCall.objects.count(),
        AuditEvent.objects.count(),
    )
    client = Client(REMOTE_ADDR="127.0.0.1")
    for route in [
        "/",
        "/harness/",
        "/research/",
        "/portfolio/",
        "/orders/",
        "/policy/",
        "/activity/",
        "/workflow/starter-prompt/",
        "/workflow/starter-prompt/preview/?q=NVDA%20earnings%20review%20no%20order",
    ]:
        response = client.get(route)
        assert response.status_code == 200
    after = (
        OrderIntent.objects.count(),
        ApprovalReceipt.objects.count(),
        ExecutionResult.objects.count(),
        McpToolCall.objects.count(),
        AuditEvent.objects.count(),
    )
    assert after == before

    preview = client.get("/workflow/starter-prompt/preview/?q=BTC%20trend%20review%20no%20trading")
    body = preview.content.decode()
    assert "Investment universe: public_crypto" in body
    assert "execution-operator" not in body


def test_central_db_env_overrides(tmp_path: Path) -> None:
    home = tmp_path / "tc-home"
    home_path = run(
        [sys.executable, "-m", "tradingcodex_cli", "db", "path"],
        ROOT,
        env_extra={"TRADINGCODEX_DB_NAME": None, "TRADINGCODEX_HOME": str(home)},
    ).stdout.strip()
    assert home_path == str(home / "state" / "tradingcodex.sqlite3")

    explicit = tmp_path / "explicit.sqlite3"
    explicit_path = run(
        [sys.executable, "-m", "tradingcodex_cli", "db", "path"],
        ROOT,
        env_extra={"TRADINGCODEX_DB_NAME": str(explicit), "TRADINGCODEX_HOME": str(home / "ignored")},
    ).stdout.strip()
    assert explicit_path == str(explicit)


def test_generated_mcp_server_uses_central_db_default(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    home = tmp_path / "tc-home"
    stdio_input = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}) + "\n"

    stdio = run(
        [sys.executable, str(workspace / ".tradingcodex" / "mcp" / "server.py")],
        workspace,
        input_text=stdio_input,
        env_extra={"TRADINGCODEX_DB_NAME": None, "TRADINGCODEX_HOME": str(home)},
    )

    assert "submit_approved_order" in stdio.stdout
    assert (home / "state" / "tradingcodex.sqlite3").exists()
    assert not (workspace / ".tradingcodex" / "state" / "tradingcodex.sqlite3").exists()


def test_db_backed_research_artifacts_via_mcp_api_and_cli(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    stored = call_tool(workspace, "create_research_artifact", {
        "artifact_id": "nvda-evidence-1",
        "artifact_type": "evidence_pack",
        "universe": "public_equity",
        "workflow_type": "issuer_baseline",
        "symbol": "NVDA",
        "title": "NVDA Evidence Pack",
        "markdown": "# NVDA Evidence\n\n[factual] Gross margin expanded in the cited period.",
        "metadata": {"role": "fundamental"},
        "readiness_label": "research-grade",
        "created_by": "fundamental-analyst",
    })
    assert stored["db_canonical"] is True
    assert stored["export_path"] == "trading/research/nvda-evidence-1.evidence.md"
    assert (workspace / stored["export_path"]).exists()
    fetched = call_tool(workspace, "get_research_artifact", {"artifact_id": "nvda-evidence-1"})
    assert "Gross margin" in fetched["markdown"]
    searched = call_tool(workspace, "search_research_artifacts", {"query": "gross margin"})
    assert any(item["artifact_id"] == "nvda-evidence-1" for item in searched["artifacts"])
    from apps.mcp.models import McpToolCall, McpToolDefinition

    assert McpToolDefinition.objects.filter(name="create_research_artifact", category="research").exists()
    assert McpToolCall.objects.filter(tool_name="create_research_artifact", status="ok").exists()
    forbidden = mcp_handle_rpc(workspace, {
        "jsonrpc": "2.0",
        "id": 9,
        "method": "tools/call",
        "params": {"name": "submit_approved_order", "arguments": {"principal_id": "head-manager"}},
    })
    assert forbidden and "not allowed" in forbidden["error"]["message"]

    previous_root = os.environ.get("TRADINGCODEX_WORKSPACE_ROOT")
    os.environ["TRADINGCODEX_WORKSPACE_ROOT"] = str(workspace)
    try:
        client = Client(REMOTE_ADDR="127.0.0.1")
        response = client.post(
            "/api/research/artifacts",
            data=json.dumps({
                "artifact_id": "btc-note-1",
                "artifact_type": "research_memo",
                "universe": "public_crypto",
                "title": "BTC Note",
                "markdown": "# BTC Note\n\n[inference] Trend work remains research-only.",
                "created_by": "instrument-analyst",
            }),
            content_type="application/json",
        )
        assert response.status_code == 200
        assert response.json()["artifact_id"] == "btc-note-1"
        assert client.get("/api/research/artifacts/btc-note-1").json()["universe"] == "public_crypto"
        search_response = client.post(
            "/api/research/search",
            data=json.dumps({"query": "trend", "limit": 5}),
            content_type="application/json",
        )
        assert search_response.status_code == 200
        assert any(item["artifact_id"] == "btc-note-1" for item in search_response.json()["artifacts"])
    finally:
        if previous_root is None:
            os.environ.pop("TRADINGCODEX_WORKSPACE_ROOT", None)
        else:
            os.environ["TRADINGCODEX_WORKSPACE_ROOT"] = previous_root

    note_path = workspace / "note.md"
    note_path.write_text("# CLI Note\n\n[factual] Stored through generated workspace CLI.", encoding="utf-8")
    cli_stored = json.loads(run([
        "./tcx",
        "research",
        "create",
        "--id",
        "cli-note-1",
        "--title",
        "CLI Note",
        "--markdown-file",
        "note.md",
        "--symbol",
        "AAPL",
    ], workspace).stdout)
    assert cli_stored["db_canonical"] is True
    assert (workspace / cli_stored["export_path"]).exists()

    mcp_cli_stored = json.loads(run([
        "./tcx",
        "mcp",
        "call",
        "create_research_artifact",
        "--principal",
        "fundamental-analyst",
        "--artifact-id",
        "mcp-cli-note-1",
        "--title",
        "MCP CLI Note",
        "--markdown",
        "# MCP CLI Note\n\n[factual] Stored through generated MCP CLI.",
        "--symbol",
        "MSFT",
    ], workspace).stdout)
    assert mcp_cli_stored["db_canonical"] is True
    assert McpToolCall.objects.filter(tool_name="create_research_artifact", principal_id="fundamental-analyst", status="ok").exists()
    mcp_help = run(["./tcx", "mcp", "--help"], workspace).stdout
    assert "create_research_artifact" in mcp_help
    assert "mcp ledger" in mcp_help
    ledger = json.loads(run([
        "./tcx",
        "mcp",
        "ledger",
        "--tool",
        "create_research_artifact",
        "--principal",
        "fundamental-analyst",
        "--status",
        "ok",
    ], workspace).stdout)
    assert ledger["count"] >= 1
    assert ledger["calls"][0]["tool_name"] == "create_research_artifact"
    assert ledger["central_ledger"] is True
    assert ledger["calls"][0]["workspace_context"]["path"] == str(workspace)


def test_central_db_is_shared_across_generated_workspaces(tmp_path: Path) -> None:
    workspace_a = tmp_path / "workspace-a"
    workspace_b = tmp_path / "workspace-b"
    bootstrap_workspace(workspace_a, force=True)
    bootstrap_workspace(workspace_b, force=True)

    db_a = run(["./tcx", "db", "path"], workspace_a).stdout.strip()
    db_b = run(["./tcx", "db", "path"], workspace_b).stdout.strip()
    assert db_a == db_b
    assert db_a != str((workspace_a / ".tradingcodex" / "state" / "tradingcodex.sqlite3").resolve())
    assert db_b != str((workspace_b / ".tradingcodex" / "state" / "tradingcodex.sqlite3").resolve())

    note = workspace_a / "shared-note.md"
    note.write_text("# Shared Note\n\n[factual] Central DB research is shared.", encoding="utf-8")
    created = json.loads(run([
        "./tcx",
        "research",
        "create",
        "--id",
        "central-shared-note",
        "--title",
        "Central Shared Note",
        "--markdown-file",
        "shared-note.md",
        "--symbol",
        "AAPL",
    ], workspace_a).stdout)
    assert created["db_canonical"] is True
    assert created["workspace_context"]["path"] == str(workspace_a)

    searched = json.loads(run(["./tcx", "research", "search", "Central DB research"], workspace_b).stdout)
    assert any(item["artifact_id"] == "central-shared-note" for item in searched["artifacts"])

    order = {
        "id": "central-cross-workspace-order",
        "symbol": "AAPL",
        "side": "buy",
        "quantity": 1,
        "limit_price": 1000,
        "currency": "KRW",
        "broker": "paper-trading",
        "estimated_notional_krw": 1000,
        "created_by": "portfolio-manager",
        "created_at": "2026-01-01T00:00:00Z",
    }
    order_path = workspace_a / "trading" / "orders" / "draft" / "central-cross-workspace-order.order_intent.json"
    order_path.write_text(json.dumps(order), encoding="utf-8")
    assert json.loads(run(["./tcx", "approve", str(order_path.relative_to(workspace_a)), "--approved-by", "risk-manager"], workspace_a).stdout)["status"] == "approved"
    executed = json.loads(run(["./tcx", "mcp", "call", "submit_approved_order", "--order-intent-id", order["id"]], workspace_b).stdout)
    assert executed["status"] == "accepted"
    assert executed["workspace_context"]["path"] == str(workspace_b)

    portfolio_a = json.loads(run(["./tcx", "mcp", "call", "get_portfolio_snapshot"], workspace_a).stdout)
    portfolio_b = json.loads(run(["./tcx", "mcp", "call", "get_portfolio_snapshot"], workspace_b).stdout)
    assert portfolio_a["positions"]["AAPL"]["quantity"] >= 1
    assert portfolio_a["positions"] == portfolio_b["positions"]

    ledger_b = json.loads(run(["./tcx", "mcp", "ledger", "--tool", "submit_approved_order", "--status", "ok"], workspace_b).stdout)
    assert ledger_b["central_ledger"] is True
    assert any(call["workspace_context"]["path"] == str(workspace_b) for call in ledger_b["calls"])


def test_django_project_check() -> None:
    result = run([sys.executable, "manage.py", "check"], ROOT)
    assert "System check identified no issues" in result.stdout

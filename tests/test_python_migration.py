from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tomllib
import zipfile
from pathlib import Path

import pytest
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
from tradingcodex_service.application.components import (
    count_harness_component_tags,
    get_harness_component,
    list_components_by_tag,
    list_harness_components,
)
from tradingcodex_service.application.artifact_quality import estimate_tokens, evaluate_artifact_quality, evaluate_decision_quality
from tradingcodex_service.application.harness import (
    build_compact_dispatch_context,
    build_subagent_starter_prompt,
    build_workflow_intake_summary,
    classify_starter_request,
    evaluate_artifact_supervisor_loop,
    is_connector_build_request,
    is_investment_workflow_request,
    is_secret_only_request,
    normalize_investment_intent,
    plan_follow_up_request,
)
from tradingcodex_service.application.decision_packages import (
    build_workflow_plan,
    create_decision_package,
    get_decision_package,
    list_decision_packages,
)
from tradingcodex_service.application.brokers import BrokerAdapterProvider, register_broker_adapter_provider
from tradingcodex_service.application.orders import validate_order_ticket_payload
from tradingcodex_service.application.runtime import ensure_runtime_database, workspace_context_payload
from tradingcodex_service.mcp_runtime import call_mcp_tool, handle_mcp_rpc
from tradingcodex_service.application.agents import (
    AGENT_SPECS,
    EXPECTED_SKILLS,
    EXPECTED_SUBAGENTS,
    ROLE_FORBIDDEN_ACTIONS,
    ROLE_HANDOFF_CONTRACTS,
    ROLE_PURPOSES,
    SKILL_SPECS,
    create_or_update_strategy_skill,
    project_agent_configuration,
    read_strategy_skill_records,
    validate_skill_assignment,
)
from tradingcodex_service.mcp_runtime import SAFE_HOME_TOOL_NAMES, static_mcp_tools
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


def test_service_autostart_reuses_compatible_singleton(monkeypatch, tmp_path: Path) -> None:
    from tradingcodex_cli import service_autostart

    checked: list[tuple[str, int]] = []
    monkeypatch.setattr(service_autostart, "_tcp_open", lambda host, port: True)
    monkeypatch.setattr(service_autostart, "_assert_compatible_service", lambda host, port: checked.append((host, port)))
    monkeypatch.setattr(service_autostart, "_start_service", lambda *args: (_ for _ in ()).throw(AssertionError("started duplicate service")))

    started = service_autostart.ensure_service_up(tmp_path, timeout=0.01)

    assert started is False
    assert checked == [("127.0.0.1", 48267)]


def test_service_autostart_rejects_incompatible_port_owner(monkeypatch, tmp_path: Path) -> None:
    from tradingcodex_cli import service_autostart

    monkeypatch.setattr(service_autostart, "_tcp_open", lambda host, port: True)
    monkeypatch.setattr(service_autostart, "_service_health", lambda host, port: {})
    monkeypatch.setattr(service_autostart, "_start_service", lambda *args: (_ for _ in ()).throw(AssertionError("started over occupied port")))

    with pytest.raises(RuntimeError, match="non-TradingCodex service") as occupied:
        service_autostart.ensure_service_up(tmp_path, timeout=0.01)
    assert "tcx service ensure 127.0.0.1:48267" in str(occupied.value)


def test_service_autostart_rejects_version_and_db_mismatch(monkeypatch) -> None:
    from tradingcodex_cli import service_autostart

    monkeypatch.setattr(
        service_autostart,
        "_service_health",
        lambda host, port: {"service": "tradingcodex", "version": "999.0.0", "db_path": str(Path("/tmp/current.sqlite3"))},
    )
    with pytest.raises(RuntimeError, match="version mismatch") as version_mismatch:
        service_autostart._assert_compatible_service("127.0.0.1", 48267)
    assert "Stop the older TradingCodex service" in str(version_mismatch.value)

    monkeypatch.setattr(service_autostart, "tradingcodex_db_path", lambda: Path("/tmp/current.sqlite3"))
    monkeypatch.setattr(
        service_autostart,
        "_service_health",
        lambda host, port: {"service": "tradingcodex", "version": TRADINGCODEX_VERSION, "db_path": str(Path("/tmp/other.sqlite3"))},
    )
    with pytest.raises(RuntimeError, match="DB mismatch") as db_mismatch:
        service_autostart._assert_compatible_service("127.0.0.1", 48267)
    assert "same central DB" in str(db_mismatch.value)


def test_service_status_reports_actionable_state(monkeypatch) -> None:
    from tradingcodex_cli import service_autostart

    monkeypatch.setattr(service_autostart, "_tcp_open", lambda host, port: False)
    stopped = service_autostart.service_status()
    assert stopped["reachable"] is False
    assert stopped["compatible"] is False
    assert stopped["issue"] == "not_running"
    assert "tcx service ensure 127.0.0.1:48267" in stopped["next_action"]

    monkeypatch.setattr(service_autostart, "_tcp_open", lambda host, port: True)
    monkeypatch.setattr(
        service_autostart,
        "_service_health",
        lambda host, port: {"service": "tradingcodex", "version": "0.0.1", "db_path": str(Path("/tmp/current.sqlite3"))},
    )
    mismatch = service_autostart.service_status("127.0.0.1:48267")
    assert mismatch["reachable"] is True
    assert mismatch["compatible"] is False
    assert mismatch["issue"] == "version_mismatch"
    assert mismatch["package_version"] == TRADINGCODEX_VERSION

    monkeypatch.setattr(service_autostart, "tradingcodex_db_path", lambda: Path("/tmp/current.sqlite3"))
    monkeypatch.setattr(
        service_autostart,
        "_service_health",
        lambda host, port: {"service": "tradingcodex", "version": TRADINGCODEX_VERSION, "db_path": str(Path("/tmp/current.sqlite3"))},
    )
    compatible = service_autostart.service_status()
    assert compatible["compatible"] is True
    assert compatible["issue"] == ""
    assert compatible["next_action"] == "No action needed."


def test_service_runserver_uses_fixed_port_and_skips_duplicate(monkeypatch, tmp_path: Path, capsys) -> None:
    from django.core import management
    from tradingcodex_cli import __main__ as cli_main
    from tradingcodex_cli import service_autostart

    calls: list[list[str]] = []
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("TRADINGCODEX_WORKSPACE_ROOT", raising=False)
    monkeypatch.setattr(service_autostart, "compatible_service_running", lambda addr: False)
    monkeypatch.setattr(management, "execute_from_command_line", lambda args: calls.append(args))

    cli_main.service(["runserver", "--noreload"])

    assert calls == [["manage.py", "runserver", "127.0.0.1:48267", "--noreload"]]

    calls.clear()
    monkeypatch.setattr(service_autostart, "compatible_service_running", lambda addr: True)

    cli_main.service(["runserver"])

    assert calls == []
    assert "TradingCodex service already running at http://127.0.0.1:48267/" in capsys.readouterr().out


def test_service_ensure_uses_autostart_helper(monkeypatch, tmp_path: Path, capsys) -> None:
    from tradingcodex_cli import __main__ as cli_main
    from tradingcodex_cli import service_autostart

    calls: list[tuple[Path, str]] = []
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("TRADINGCODEX_WORKSPACE_ROOT", raising=False)
    monkeypatch.setattr(service_autostart, "ensure_service_up", lambda root, addr: calls.append((root, addr)) or True)

    cli_main.service(["ensure"])

    assert calls == [(tmp_path.resolve(), "127.0.0.1:48267")]
    output = capsys.readouterr().out
    assert "TradingCodex service started at http://127.0.0.1:48267/" in output
    assert "Health: http://127.0.0.1:48267/api/health" in output


def test_service_status_cli_supports_plain_and_json(monkeypatch, tmp_path: Path, capsys) -> None:
    from tradingcodex_cli import __main__ as cli_main
    from tradingcodex_cli import service_autostart

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        service_autostart,
        "service_status",
        lambda addr: {
            "addr": addr,
            "url": f"http://{addr}/",
            "reachable": True,
            "compatible": False,
            "service": "tradingcodex",
            "version": "0.0.1",
            "package_version": TRADINGCODEX_VERSION,
            "db_path": "/tmp/old.sqlite3",
            "expected_db_path": "/tmp/current.sqlite3",
            "issue": "version_mismatch",
            "next_action": "Stop the older TradingCodex service.",
        },
    )

    cli_main.service(["status"])
    output = capsys.readouterr().out
    assert "TradingCodex service status: attention needed" in output
    assert "Issue: version_mismatch" in output
    assert "Next: Stop the older TradingCodex service." in output

    cli_main.service(["status", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["compatible"] is False
    assert payload["issue"] == "version_mismatch"


def test_startup_status_preserves_service_mismatch_details(monkeypatch, tmp_path: Path) -> None:
    from tradingcodex_cli import startup_status

    workspace = make_workspace(tmp_path)
    old_db = tmp_path / "old.sqlite3"
    current_db = tmp_path / "current.sqlite3"
    service_detail = {
        "addr": "127.0.0.1:48267",
        "url": "http://127.0.0.1:48267/",
        "reachable": True,
        "compatible": False,
        "service": "tradingcodex",
        "version": "0.2.1",
        "package_version": TRADINGCODEX_VERSION,
        "db_path": str(old_db),
        "expected_db_path": str(current_db),
        "issue": "version_mismatch",
        "next_action": "Stop the older TradingCodex service or choose a free service address, then restart Codex if MCP uses the default address.",
    }
    monkeypatch.setattr(startup_status, "inspect_service_status", lambda addr: service_detail)
    monkeypatch.setattr(
        startup_status,
        "_read_health",
        lambda url: {"service": "tradingcodex", "version": "0.2.1", "db_path": str(old_db)},
    )

    status = startup_status.build_server_status(workspace)

    assert status["service_status"] == "incompatible"
    assert status["service_detail"]["issue"] == "version_mismatch"
    assert status["service_detail"]["version"] == "0.2.1"
    assert status["service_detail"]["package_version"] == TRADINGCODEX_VERSION
    assert "service=0.2.1" in status["startup_notice"]
    assert f"package={TRADINGCODEX_VERSION}" in status["startup_notice"]
    assert status["recommended_action"] == service_detail["next_action"]
    assert status["allowed_next_actions"][0] == service_detail["next_action"]


def test_manage_runserver_uses_fixed_port_and_skips_duplicate(monkeypatch, capsys) -> None:
    from tradingcodex_cli import service_autostart

    spec = importlib.util.spec_from_file_location("tradingcodex_test_manage", ROOT / "manage.py")
    assert spec and spec.loader
    manage = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(manage)

    monkeypatch.setattr(service_autostart, "compatible_service_running", lambda addr: False)

    assert manage._prepare_runserver_argv(["manage.py", "runserver", "--noreload"]) == ["manage.py", "runserver", "127.0.0.1:48267", "--noreload"]
    assert manage._prepare_runserver_argv(["manage.py", "check"]) == ["manage.py", "check"]

    monkeypatch.setattr(service_autostart, "compatible_service_running", lambda addr: True)

    assert manage._prepare_runserver_argv(["manage.py", "runserver"]) is None
    assert "TradingCodex service already running at http://127.0.0.1:48267/" in capsys.readouterr().out


def write_optional_skill_fixture(workspace: Path, role: str, name: str) -> dict:
    display_name = "Filing Red Flag Review"
    description = "Review filings for accounting and disclosure red flags."
    skill_dir = workspace / ".tradingcodex" / "subagents" / "skills" / role / name
    metadata_dir = skill_dir / "agents"
    skill_dir.mkdir(parents=True, exist_ok=True)
    metadata_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        "\n".join(
            [
                "---",
                f"name: {name}",
                f'description: "{description}"',
                "---",
                "",
                f"# {display_name}",
                "",
                "Review filing excerpts for role-local red flags and cite source/as-of posture.",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (metadata_dir / "openai.yaml").write_text(
        "\n".join(
            [
                "interface:",
                f'  display_name: "{display_name}"',
                '  short_description: "Review filings for red flags"',
                f'  default_prompt: "Use ${name} to review filing excerpts for red flags."',
                "policy:",
                "  allow_implicit_invocation: false",
                "",
            ]
        ),
        encoding="utf-8",
    )
    status = {
        "role": role,
        "name": name,
        "status": "active",
        "created_by": "test-head-manager",
        "created_at": "2026-01-01T00:00:00Z",
        "updated_by": "test-head-manager",
        "updated_at": "2026-01-01T00:00:00Z",
    }
    (metadata_dir / "tradingcodex.json").write_text(json.dumps(status, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return project_agent_configuration(workspace, role=role, applied_by="test-head-manager")


def write_strategy_skill_fixture(workspace: Path, skill_id: str = "strategy-quality-compounder") -> dict:
    skill_dir = workspace / ".agents" / "skills" / skill_id
    metadata_dir = skill_dir / "agents"
    skill_dir.mkdir(parents=True, exist_ok=True)
    metadata_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        "\n".join(
            [
                "---",
                f"name: {skill_id}",
                'description: "Apply a quality compounder strategy with evidence discipline."',
                "type: strategy",
                "status: active",
                "language: ko-KR",
                "owner: user",
                "last_reviewed: 2026-06-12",
                "---",
                "",
                "# Quality Compounder",
                "",
                "## Thesis",
                "Prefer durable business quality with valuation discipline.",
                "",
                "## Eligible Universe",
                "Public equities only.",
                "",
                "## Preferred Setups",
                "Quality companies with visible reinvestment runway.",
                "",
                "## Entry Criteria",
                "Evidence-backed quality and valuation support.",
                "",
                "## Exit Criteria",
                "Thesis break, valuation excess, or better opportunity cost.",
                "",
                "## Evidence Requirements",
                "Use source/as-of posture and mark missing evidence.",
                "",
                "## Decision-Ready Standard",
                "Evidence, valuation, and risk assumptions must be explicit.",
                "",
                "## Sizing Guidance",
                "Start small when uncertainty is high.",
                "",
                "## Risk Controls",
                "Limit exposure when evidence is incomplete.",
                "",
                "## Block Conditions",
                "Block when evidence is stale or restricted.",
                "",
                "## Change Log",
                "- 2026-06-12: Test fixture.",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (metadata_dir / "openai.yaml").write_text(
        "\n".join(
            [
                "interface:",
                '  display_name: "Quality Compounder"',
                '  short_description: "Apply a quality strategy"',
                f'  default_prompt: "Use ${skill_id} to apply this user-approved strategy."',
                "policy:",
                "  allow_implicit_invocation: true",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return project_agent_configuration(workspace, applied_by="test-strategy")


def run_user_prompt_hook(workspace: Path, prompt: str, extra_payload: dict | None = None) -> dict | None:
    payload = {"prompt": prompt}
    if extra_payload:
        payload.update(extra_payload)
    result = run(
        [sys.executable, str(workspace / ".codex" / "hooks" / "tradingcodex_hook.py"), "user-prompt-submit"],
        workspace,
        input_text=json.dumps(payload),
    )
    if not result.stdout.strip():
        return None
    output = json.loads(result.stdout)
    return json.loads(output["hookSpecificOutput"]["additionalContext"])


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
        assert not list(module.dir.rglob("__pycache__"))
        assert not list(module.dir.rglob("*.pyc"))
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
        ".agents/skills/tcx-workflow/SKILL.md",
        ".agents/skills/tcx-server/SKILL.md",
        ".agents/skills/tcx-build/SKILL.md",
        ".tradingcodex/config.yaml",
        ".tradingcodex/workspace.json",
        "trading/research/.gitkeep",
        "tcx",
    ]:
        assert (workspace / rel).exists(), rel
    assert "future_role_change_requires" in (workspace / ".tradingcodex" / "policies" / "information-barriers.yaml").read_text(encoding="utf-8")
    assert "context-efficiency-contract" in {component["id"] for component in list_harness_components()}
    import yaml

    workspace_config = yaml.safe_load((workspace / ".tradingcodex" / "config.yaml").read_text(encoding="utf-8"))
    assert workspace_config["execution"]["enabled_adapters"] == ["stub-execution", "paper-trading"]
    assert workspace_config["execution"]["enabled_execution_postures"] == ["paper_only", "broker_validation_only"]
    access_policy_text = (workspace / ".tradingcodex" / "policies" / "access-policies.yaml").read_text(encoding="utf-8")
    assert 'order.execution_posture in ["paper_only", "broker_validation_only"]' in access_policy_text
    reusable_agent_surfaces = "\n".join(
        [
            (workspace / ".codex" / "prompts" / "base_instructions" / "head-manager.md").read_text(encoding="utf-8"),
            (workspace / ".codex" / "agents" / "execution-operator.toml").read_text(encoding="utf-8"),
            (workspace / ".agents" / "skills" / "tcx-server" / "SKILL.md").read_text(encoding="utf-8"),
            (workspace / ".agents" / "skills" / "tcx-build" / "SKILL.md").read_text(encoding="utf-8"),
            (workspace / ".tradingcodex" / "subagents" / "skills" / "execution-operator" / "execute-paper-order" / "SKILL.md").read_text(encoding="utf-8"),
            (workspace / ".tradingcodex" / "config.yaml").read_text(encoding="utf-8"),
            access_policy_text,
        ]
    )
    assert "binance-spot-testnet" not in reusable_agent_surfaces.lower()
    assert "order.submit.testnet" not in reusable_agent_surfaces
    assert "broker_validation_only" in reusable_agent_surfaces
    assert "submit or cancel approved orders" in reusable_agent_surfaces
    assert not (workspace / "package.json").exists()
    assert not list(workspace.rglob("__pycache__"))
    assert not list(workspace.rglob("*.pyc"))
    assert not (workspace / ".tradingcodex" / "state" / "tradingcodex.sqlite3").exists()


def test_package_wheel_includes_web_static_assets(tmp_path: Path) -> None:
    source = tmp_path / "source"
    wheel_dir = tmp_path / "wheel"
    shutil.copytree(
        ROOT,
        source,
        ignore=shutil.ignore_patterns(".git", ".pytest_cache", "__pycache__", "*.pyc", "dist", "build", "*.egg-info"),
    )
    wheel_dir.mkdir()

    run([sys.executable, "-m", "pip", "wheel", ".", "--no-deps", "--wheel-dir", str(wheel_dir)], source)

    wheels = list(wheel_dir.glob("tradingcodex-*.whl"))
    assert len(wheels) == 1
    with zipfile.ZipFile(wheels[0]) as wheel:
        names = set(wheel.namelist())
    assert "tradingcodex_service/static/tradingcodex_web/app.css" in names
    assert "tradingcodex_service/static/tradingcodex_web/app.js" in names
    assert "tradingcodex_service/static/vendor/htmx/htmx.min.js" in names
    assert "tradingcodex_service/static/tradingcodex_admin/favicon.svg" in names


def test_investment_request_detection_avoids_repository_work() -> None:
    assert is_investment_workflow_request("Analyze NVDA")
    assert is_investment_workflow_request("Analyze Apple stock")
    assert is_investment_workflow_request("$tcx-workflow analyze Apple")
    assert not is_investment_workflow_request("Analyze AGENTS.md for stale guidance")
    assert not is_investment_workflow_request("Update the docs table")
    assert not is_investment_workflow_request("Create a quality income strategy for dividend stocks")


def test_harness_component_registry_contract() -> None:
    components = list_harness_components()
    component_ids = [component["id"] for component in components]
    required = {
        "investment-request-routing",
        "fixed-role-dispatch",
        "research-memory",
        "workflow-quality-gates",
        "decision-package",
        "artifact-quality-contract",
        "context-efficiency-contract",
        "responsibility-boundary-contract",
        "external-data-source-gate",
        "external-mcp-proxy-gate",
        "secret-wall",
        "policy-and-restricted-list",
        "approval-gate",
        "execution-boundary",
        "audit-ledger",
        "skill-improvement-loop",
        "postmortem-loop",
        "paper-execution",
    }

    assert len(component_ids) == len(set(component_ids))
    assert required.issubset(set(component_ids))
    for component in components:
        assert component["tags"], component["id"]
        assert component["surfaces"], component["id"]
        assert component["validation"], component["id"]
        for dependency in component["depends_on"]:
            assert dependency in component_ids

    assert get_harness_component("investment-request-routing")["label"] == "Investment Request Routing"
    assert get_harness_component("missing-component") is None
    guidance = list_components_by_tag("guardrail.guidance")
    assert "investment-request-routing" in {component["id"] for component in guidance}
    tag_counts = count_harness_component_tags()
    assert tag_counts["guardrail"] > 0
    assert tag_counts["improvement"] > 0


def test_file_native_agent_skill_registry_contract() -> None:
    assert "head-manager" in AGENT_SPECS
    assert len(EXPECTED_SUBAGENTS) == 9
    assert len(AGENT_SPECS) == 10
    assert len(EXPECTED_SKILLS) == 23
    assert "strategy-creator" in EXPECTED_SKILLS
    assert "tcx-workflow" in EXPECTED_SKILLS
    assert "tcx-server" in EXPECTED_SKILLS
    assert "tcx-build" in EXPECTED_SKILLS
    assert set(EXPECTED_SKILLS) == set(SKILL_SPECS)
    for role in AGENT_SPECS:
        assert ROLE_PURPOSES[role]
        assert ROLE_HANDOFF_CONTRACTS[role]["receives"]
        assert ROLE_HANDOFF_CONTRACTS[role]["returns"]
        assert ROLE_FORBIDDEN_ACTIONS[role]
    project_scope_errors = validate_skill_assignment("fundamental-analyst", "postmortem")
    assert project_scope_errors
    assert "project-scope mainagent skill" in project_scope_errors[0]
    errors = validate_skill_assignment("fundamental-analyst", "execute-paper-order")
    assert errors
    assert "blocked risk tags" in errors[0]
    assert "execution" in errors[0]
    assert "order" in errors[0]


def test_user_prompt_hook_auto_routes_plain_investment_requests(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)

    direct_hook = run(
        [str(workspace / ".codex" / "hooks" / "tradingcodex_hook.py"), "user-prompt-submit"],
        workspace,
        input_text=json.dumps({"prompt": "Analyze Microsoft stock"}),
    )
    direct_output = json.loads(direct_hook.stdout)
    direct_gate = json.loads(direct_output["hookSpecificOutput"]["additionalContext"])
    assert direct_gate["workflow_lane"] == "thesis_review"
    assert direct_gate["required_subagents"] == ["fundamental-analyst", "technical-analyst", "news-analyst", "valuation-analyst"]

    gate = run_user_prompt_hook(workspace, "Analyze Apple stock")
    assert gate
    assert gate["activation_source"] == "auto_routed_investment_request"
    assert gate["auto_dispatch_allowed"] is True
    assert gate["confirmation_required"] is False
    assert gate["requires_subagent_dispatch"] is True
    assert gate["workflow_lane"] == "thesis_review"
    assert gate["required_subagents"] == ["fundamental-analyst", "technical-analyst", "news-analyst", "valuation-analyst"]
    assert gate["deep_thesis_default"] is True
    assert gate["decision_quality_required"] is True
    assert gate["context_mode"] == "compact_workflow_gate"
    assert gate["starter_prompt_path"] == ".tradingcodex/mainagent/latest-user-prompt-gate.json"
    assert "starter_prompt" not in gate
    assert "dispatch_or_reuse_selected_subagents_before_substantive_analysis" in gate["dispatch_rules"]
    persisted_gate = json.loads((workspace / ".tradingcodex" / "mainagent" / "latest-user-prompt-gate.json").read_text(encoding="utf-8"))
    assert "This selected team is binding for the current lane" in persisted_gate["starter_prompt"]
    assert "Deep thesis default" in persisted_gate["starter_prompt"]
    assert persisted_gate["compact_additional_context"]["context_mode"] == "compact_workflow_gate"

    negated_scope_gate = run_user_prompt_hook(
        workspace,
        "Routing smoke test for NVDA. No order, no trading, no valuation. Use selected subagents only.",
    )
    assert negated_scope_gate
    assert negated_scope_gate["workflow_lane"] == "research_only"
    assert negated_scope_gate["required_subagents"] == ["fundamental-analyst", "technical-analyst", "news-analyst"]
    negated_persisted_gate = json.loads((workspace / ".tradingcodex" / "mainagent" / "latest-user-prompt-gate.json").read_text(encoding="utf-8"))
    negated_spawn_line = next(line for line in negated_persisted_gate["starter_prompt"].splitlines() if line.startswith("Spawn these fixed role subagents"))
    assert "valuation-analyst" not in negated_spawn_line

    explicit_gate = run_user_prompt_hook(workspace, "$tcx-workflow analyze Apple stock")
    assert explicit_gate
    assert explicit_gate["activation_source"] == "explicit_subagent"
    assert explicit_gate["auto_dispatch_allowed"] is True

    secret_gate = run_user_prompt_hook(workspace, "Please inspect the .env file")
    assert secret_gate
    assert secret_gate["activation_source"] == "secret_warning_only"
    assert secret_gate["secret_warning"] is True
    assert secret_gate["requires_subagent_dispatch"] is False
    assert secret_gate["required_subagents"] == []

    broker_secret_gate = run_user_prompt_hook(workspace, "Here is my broker API key secret, save it to .env")
    assert broker_secret_gate
    assert broker_secret_gate["activation_source"] == "secret_warning_only"
    assert broker_secret_gate["workflow_lane"] == "secret_warning"
    assert broker_secret_gate["requires_subagent_dispatch"] is False
    assert "starter_prompt" not in broker_secret_gate
    broker_secret_persisted_gate = json.loads((workspace / ".tradingcodex" / "mainagent" / "latest-user-prompt-gate.json").read_text(encoding="utf-8"))
    assert broker_secret_persisted_gate["starter_prompt"] == ""
    assert is_secret_only_request("Here is my broker API key secret, save it to .env") is True
    assert is_investment_workflow_request("Here is my broker API key secret, save it to .env") is False
    assert is_secret_only_request("Use my broker API key to execute an AAPL order") is False
    assert is_investment_workflow_request("Use my broker API key to execute an AAPL order") is True

    run([
        "./tcx",
        "profile",
        "update",
        "--objective",
        "medium-term quality compounder",
        "--horizon",
        "3 to 5 years",
        "--risk-tolerance",
        "moderate drawdown tolerance",
    ], workspace)
    profile_aware_gate = run_user_prompt_hook(workspace, "TSLA fair value and whether it fits my portfolio, no order")
    assert profile_aware_gate
    assert profile_aware_gate["workflow_lane"] == "thesis_review_then_portfolio_risk_review"
    assert profile_aware_gate["profile_known"] == ["obj", "hor", "risk"]
    assert len(json.dumps(profile_aware_gate, ensure_ascii=False)) < 1600
    profile_aware_persisted_gate = json.loads((workspace / ".tradingcodex" / "mainagent" / "latest-user-prompt-gate.json").read_text(encoding="utf-8"))
    assert "Known investor profile context from the active profile" in profile_aware_persisted_gate["starter_prompt"]
    assert "medium-term quality compounder" in profile_aware_persisted_gate["starter_prompt"]
    assert "What outcome are you trying to achieve with this idea?" not in profile_aware_persisted_gate["starter_prompt"]

    subagent_brief_gate = run_user_prompt_hook(
        workspace,
        "Risk role brief: no order, no trading, no approval, no execution. Return a blocked-actions handoff.",
        {"agent_type": "risk-manager"},
    )
    assert subagent_brief_gate is None


def test_session_start_update_recommendation_respects_home_preference(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    home = tmp_path / "tc-home"
    module_lock_path = workspace / ".tradingcodex" / "generated" / "module-lock.json"
    module_lock = json.loads(module_lock_path.read_text(encoding="utf-8"))
    module_lock["tradingcodex_version"] = "0.0.1"
    module_lock_path.write_text(json.dumps(module_lock, indent=2) + "\n", encoding="utf-8")

    run(
        [sys.executable, str(workspace / ".codex" / "hooks" / "tradingcodex_hook.py"), "session-start"],
        workspace,
        input_text=json.dumps({}),
        env_extra={"TRADINGCODEX_HOME": str(home), "TRADINGCODEX_LATEST_RELEASE_VERSION": TRADINGCODEX_VERSION},
    )
    server_status = json.loads((workspace / ".tradingcodex" / "mainagent" / "server-status.json").read_text(encoding="utf-8"))
    update_status = server_status["update_status"]
    assert update_status["workspace_version"] == "0.0.1"
    assert update_status["installed_version"] == TRADINGCODEX_VERSION
    assert update_status["package_version"] == TRADINGCODEX_VERSION
    assert update_status["package_spec"] == "tradingcodex"
    assert update_status["latest_release_version"] == TRADINGCODEX_VERSION
    assert update_status["latest_release_status"] == "ok"
    assert update_status["versions_match"] is False
    assert update_status["workspace_update_available"] is True
    assert update_status["workspace_update_allowed"] is True
    assert update_status["workspace_update_recommended"] is True
    assert update_status["package_update_required_first"] is False
    assert update_status["workspace_update_command"] == "./tcx update --skip-refresh"
    assert update_status["head_manager_update_allowed"] is False
    assert update_status["head_manager_update_command"] == ""
    assert "Codex full access" in update_status["head_manager_update_blocked_reason"]
    assert update_status["can_self_update"] is False
    assert update_status["command"] == "./tcx update --skip-refresh"
    assert update_status["recommended_action"] == "Use build mode with full access for self-update, or ask the user to run from a terminal: ./tcx update --skip-refresh"
    preference_path = home / "preferences" / "update.json"
    assert update_status["preference_path"] == str(preference_path)

    run(
        [sys.executable, str(workspace / ".codex" / "hooks" / "tradingcodex_hook.py"), "session-start"],
        workspace,
        input_text=json.dumps({}),
        env_extra={"TRADINGCODEX_HOME": str(home), "TRADINGCODEX_LATEST_RELEASE_VERSION": "999.0.0"},
    )
    blocked_status = json.loads((workspace / ".tradingcodex" / "mainagent" / "server-status.json").read_text(encoding="utf-8"))
    blocked_update = blocked_status["update_status"]
    assert blocked_update["workspace_update_available"] is True
    assert blocked_update["workspace_update_allowed"] is False
    assert blocked_update["workspace_update_recommended"] is False
    assert blocked_update["package_update_required_first"] is True
    assert blocked_update["head_manager_update_allowed"] is False
    assert blocked_update["head_manager_update_command"] == ""
    assert blocked_update["workspace_update_command"] == "./tcx update --skip-refresh"
    assert blocked_update["user_update_command"] == "uvx --refresh --from tradingcodex tcx update ."
    assert blocked_update["recommended_action"].startswith("Use build mode with full access for self-update")
    assert "older than the latest release" in blocked_update["blocked_reason"]

    preference_path.parent.mkdir(parents=True)
    preference_path.write_text(json.dumps({"suppress_update_recommendation": True}) + "\n", encoding="utf-8")
    run(
        [sys.executable, str(workspace / ".codex" / "hooks" / "tradingcodex_hook.py"), "session-start"],
        workspace,
        input_text=json.dumps({}),
        env_extra={"TRADINGCODEX_HOME": str(home), "TRADINGCODEX_LATEST_RELEASE_VERSION": TRADINGCODEX_VERSION},
    )
    suppressed_status = json.loads((workspace / ".tradingcodex" / "mainagent" / "server-status.json").read_text(encoding="utf-8"))
    suppressed_update = suppressed_status["update_status"]
    assert suppressed_update["workspace_update_available"] is True
    assert suppressed_update["workspace_update_allowed"] is True
    assert suppressed_update["workspace_update_recommended"] is False
    assert suppressed_update["workspace_update_command"] == "./tcx update --skip-refresh"
    assert suppressed_update["head_manager_update_allowed"] is False
    assert suppressed_update["head_manager_update_command"] == ""
    assert suppressed_update["recommended_action"] == ""
    assert suppressed_update["update_recommendation_suppressed"] is True

    assert run_user_prompt_hook(workspace, "Update the docs table") is None
    assert run_user_prompt_hook(workspace, "Create a quality income strategy for dividend stocks") is None


def test_repo_skill_templates_keep_instruction_boundary() -> None:
    skill_root = ROOT / "workspace_templates" / "modules" / "repo-skills" / "files" / ".agents" / "skills"
    subagent_skill_root = ROOT / "workspace_templates" / "modules" / "repo-skills" / "files" / ".tradingcodex" / "subagents" / "skills"
    skill_paths = sorted([*skill_root.glob("*/SKILL.md"), *subagent_skill_root.glob("*/*/SKILL.md")])
    skill_names = {path.parent.name for path in skill_paths}
    forbidden_phrases = [
        "Role ownership:",
        "This skill owns",
        "Use by ",
        "only inside",
        "must not use this skill",
        "should assign",
        "does not grant permission",
        "TradingCodex quality floor",
        "Portfolio And Risk Handoff",
        ".tradingcodex/strategies",
        "Handoff question",
        "handoff gap",
    ]
    role_ids = {
        "head-manager",
        "fundamental-analyst",
        "technical-analyst",
        "news-analyst",
        "macro-analyst",
        "instrument-analyst",
        "valuation-analyst",
        "portfolio-manager",
        "risk-manager",
        "execution-operator",
    }
    policy_principal_mentions = {
        "create-order-ticket": {"portfolio-manager"},
        "approve-order": {"risk-manager"},
        "execute-paper-order": {"execution-operator"},
    }

    for path in skill_paths:
        text = path.read_text(encoding="utf-8")
        skill_name = path.parent.name
        for phrase in forbidden_phrases:
            assert phrase not in text, f"{phrase!r} leaked into {path}"
        for other_skill in skill_names - {skill_name}:
            assert f"`{other_skill}`" not in text, f"{skill_name} directly references {other_skill}"
        allowed_roles = policy_principal_mentions.get(skill_name, set())
        for role_id in role_ids:
            if role_id in text and role_id not in allowed_roles:
                raise AssertionError(f"{skill_name} should not encode role-specific instruction for {role_id}")

    for metadata in sorted([*skill_root.glob("*/agents/openai.yaml"), *subagent_skill_root.glob("*/*/agents/openai.yaml")]):
        text = metadata.read_text(encoding="utf-8")
        assert "only inside" not in text
    metadata_paths = sorted([*skill_root.glob("*/agents/openai.yaml"), *subagent_skill_root.glob("*/*/agents/openai.yaml")])
    assert {path.parent.parent.name for path in metadata_paths} == skill_names
    import yaml

    for metadata in metadata_paths:
        skill_name = metadata.parent.parent.name
        data = yaml.safe_load(metadata.read_text(encoding="utf-8"))
        interface = data.get("interface", {})
        policy = data.get("policy", {})
        short_description = interface.get("short_description", "")
        default_prompt = interface.get("default_prompt", "")
        assert 25 <= len(short_description) <= 64, metadata
        assert f"${skill_name}" in default_prompt, metadata
        assert isinstance(policy.get("allow_implicit_invocation"), bool), metadata

    server_skill = (skill_root / "tcx-server" / "SKILL.md").read_text(encoding="utf-8")
    assert "tcx service status" in server_skill
    assert "tcx update status --json" in server_skill
    assert "service_issue=version_mismatch" in server_skill
    assert "Do not run `tcx update`" in server_skill
    build_skill = (skill_root / "tcx-build" / "SKILL.md").read_text(encoding="utf-8")
    assert "Build mode may create live-capable providers" in build_skill
    assert "tcx connectors connect" in build_skill
    assert "tcx connectors scaffold" in build_skill
    head_manager_prompt = (
        ROOT / "workspace_templates/modules/codex-base/files/.codex/prompts/base_instructions/head-manager.md"
    ).read_text(encoding="utf-8")
    assert "$tcx-build" in head_manager_prompt
    assert "$tcx-server" in head_manager_prompt


def test_install_docs_tell_agents_not_to_invent_workspace_paths() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    installation = (ROOT / "installation.md").read_text(encoding="utf-8")
    generated_workspaces = (ROOT / "docs" / "generated-workspaces.md").read_text(encoding="utf-8")

    for text in [readme, installation, generated_workspaces]:
        normalized = re.sub(r"\s+", " ", text)
        assert "do not invent" in normalized.lower()
        assert "ask" in normalized.lower()

    assert "tradingcodex-workspace" in installation
    assert "tradingcodex-workspace" in generated_workspaces


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
    workspace_manifest = json.loads((workspace / ".tradingcodex" / "workspace.json").read_text(encoding="utf-8"))
    assert workspace_manifest["workspace_id"].startswith("tcxw_")
    assert workspace_manifest["active_profile"]["label"] == "shared central paper profile"
    assert workspace_manifest["mcp_scope"] == "project-scoped"
    assert workspace_manifest["execution_mode"] == "non-live: paper/validation-only/broker-validation"
    module_lock = json.loads((workspace / ".tradingcodex" / "generated" / "module-lock.json").read_text(encoding="utf-8"))
    capability_index = json.loads((workspace / ".tradingcodex" / "generated" / "capability-index.json").read_text(encoding="utf-8"))
    component_index = json.loads((workspace / ".tradingcodex" / "generated" / "component-index.json").read_text(encoding="utf-8"))
    agent_index = json.loads((workspace / ".tradingcodex" / "generated" / "agent-index.json").read_text(encoding="utf-8"))
    skill_index = json.loads((workspace / ".tradingcodex" / "generated" / "skill-index.json").read_text(encoding="utf-8"))
    projection_manifest = json.loads((workspace / ".tradingcodex" / "generated" / "projection-manifest.json").read_text(encoding="utf-8"))
    assert "modules" in module_lock
    assert module_lock["tradingcodex_package_spec"] == "tradingcodex"
    assert module_lock["tradingcodex_home"].endswith(".tradingcodex")
    assert "capabilities" in capability_index
    assert {component["id"] for component in component_index["components"]} == {component["id"] for component in list_harness_components()}
    assert component_index["source"] == "tradingcodex_service.application.components"
    assert agent_index["source"] == "tradingcodex_service.application.agents"
    assert skill_index["source"] == "workspace-files"
    assert projection_manifest["source"] == "file-native-agent-skill-projection"
    assert agent_index["projection_hash"] == skill_index["projection_hash"] == projection_manifest["projection_hash"]
    assert len(agent_index["agents"]) == 10
    assert len(skill_index["skills"]) == 23
    assert skill_index["skills"]["strategy-creator"]["source"] == "core"
    assert skill_index["skills"]["tcx-workflow"]["installed"] is True
    assert skill_index["skills"]["tcx-server"]["installed"] is True
    assert skill_index["skills"]["tcx-build"]["installed"] is True
    assert skill_index["skills"]["tcx-build"]["user_visible"] is True
    assert "external-data-source-gate" in agent_index["agents"]["fundamental-analyst"]["effective_skills"]
    assert agent_index["agents"]["portfolio-manager"]["purpose"] == ROLE_PURPOSES["portfolio-manager"]
    assert agent_index["agents"]["portfolio-manager"]["handoff_contract"] == ROLE_HANDOFF_CONTRACTS["portfolio-manager"]
    assert "No self-approval." in agent_index["agents"]["portfolio-manager"]["forbidden_actions"]
    fundamental_toml_text = (workspace / ".codex" / "agents" / "fundamental-analyst.toml").read_text(encoding="utf-8")
    assert "external-data-source-gate" in fundamental_toml_text
    assert ".tradingcodex/subagents/skills/shared/external-data-source-gate/SKILL.md" in fundamental_toml_text
    assert "BEGIN TradingCodex role skill sources" in fundamental_toml_text
    assert "Do not read or apply head-manager, strategy, or out-of-role TradingCodex skill files." in fundamental_toml_text
    assert "If asked to inspect, test, list, or prove access to those forbidden skill files" in fundamental_toml_text
    generated_text = "\n".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in workspace.rglob("*")
        if path.is_file() and path.suffix.lower() in {"", ".md", ".toml", ".yaml", ".yml", ".json", ".py"}
    )
    forbidden_disclosure_system_names = ["k-" + "da" + "rt", "".join(["D", "A", "R", "T"])]
    for forbidden in forbidden_disclosure_system_names:
        assert forbidden.lower() not in generated_text.lower()
    assert "vibe investing" not in generated_text.lower()
    assert "official regulator or exchange disclosure sources" in generated_text
    assert "./tradingcodex" not in generated_text
    workflow_guidance = (workspace / ".agents" / "skills" / "tcx-workflow" / "SKILL.md").read_text(encoding="utf-8")
    server_guidance = (workspace / ".agents" / "skills" / "tcx-server" / "SKILL.md").read_text(encoding="utf-8")
    build_guidance = (workspace / ".agents" / "skills" / "tcx-build" / "SKILL.md").read_text(encoding="utf-8")
    assert "compact hook context" in workflow_guidance
    assert "selected fixed-role subagents" in workflow_guidance
    assert "Do not widen the selected team" in workflow_guidance
    assert "tcx update" in server_guidance
    assert "Do not scaffold or edit connector code in operate mode" in server_guidance
    assert "Build mode may create live-capable providers" in build_guidance
    assert "tcx connectors connect" in build_guidance
    assert "tcx connectors scaffold" in build_guidance
    hook_text = (workspace / ".codex" / "hooks" / "tradingcodex_hook.py").read_text(encoding="utf-8")
    assert 'payload.get("agent_type")' in hook_text
    assert "server-status.json" in hook_text
    initial_server_status = json.loads((workspace / ".tradingcodex" / "mainagent" / "server-status.json").read_text(encoding="utf-8"))
    assert initial_server_status["service_addr"] == "127.0.0.1:48267"
    assert initial_server_status["mcp_config_present"] is True
    assert initial_server_status["update_status"]["versions_match"] is True
    assert initial_server_status["recommended_action"]
    session_start = run(
        [sys.executable, str(workspace / ".codex" / "hooks" / "tradingcodex_hook.py"), "session-start"],
        workspace,
        input_text=json.dumps({}),
    )
    session_start_output = json.loads(session_start.stdout)
    assert session_start_output["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    assert "tradingcodex-session-context" in session_start_output["hookSpecificOutput"]["additionalContext"]
    server_status = json.loads((workspace / ".tradingcodex" / "mainagent" / "server-status.json").read_text(encoding="utf-8"))
    assert server_status["service_addr"] == "127.0.0.1:48267"
    assert server_status["dashboard_url"] == "http://127.0.0.1:48267/"
    assert server_status["health_url"] == "http://127.0.0.1:48267/api/health"
    assert server_status["mcp_config_present"] is True
    assert server_status["restart_codex_required"] is False
    assert server_status["update_status"]["versions_match"] is True
    assert server_status["update_status"]["workspace_update_available"] is False
    assert server_status["update_status"]["workspace_update_recommended"] is False
    assert server_status["recommended_action"]
    session_start_payload = json.loads((workspace / ".tradingcodex" / "mainagent" / "session-start.json").read_text(encoding="utf-8"))
    assert session_start_payload["marker"] == "tradingcodex-session-context"
    assert session_start_payload["mode_status"]["mode"] == "operate"
    assert session_start_payload["permission_status"]["codex_permission"] == "restricted"
    assert session_start_payload["update_status"]["can_self_update"] is False
    assert "service_issue" in session_start_payload["server_status"]
    assert "startup_notice" in session_start_payload["server_status"]
    assert "next_action" in session_start_payload["server_status"]
    assert "service_detail" not in session_start_payload["server_status"]
    assert estimate_tokens(json.dumps(session_start_payload, ensure_ascii=False)) <= 800
    assert not (workspace / ".tradingcodex" / "state" / "tradingcodex.sqlite3").exists()
    assert not (workspace / ".tradingcodex" / "state" / "paper-portfolio.json").exists()
    db_path = run(["./tcx", "db", "path"], workspace).stdout.strip()
    assert db_path != str((workspace / ".tradingcodex" / "state" / "tradingcodex.sqlite3").resolve())
    status = json.loads(run(["./tcx", "subagents", "status"], workspace).stdout)
    assert status["installed_count"] == 9
    assert status["fixed_roster_ok"] is True
    assert status["skills_installed"] == 23
    execution_status = next(agent for agent in status["agents"] if agent["name"] == "execution-operator")
    assert execution_status["description"] == "Request approved execution through the workspace service boundary only."
    assert "MCP execution boundary" not in execution_status["description"]
    inspect = json.loads(run(["./tcx", "subagents", "inspect", "fundamental-analyst"], workspace).stdout)
    assert inspect["effective_skills"] == [
        "external-data-source-gate",
        "collect-evidence",
        "numeric-data-qc",
        "thesis-scenario-tree",
        "forecasting-discipline",
        "fundamental-analysis",
    ]
    execution_inspect = json.loads(run(["./tcx", "subagents", "inspect", "execution-operator"], workspace).stdout)
    assert execution_inspect["effective_skills"] == ["execute-paper-order"]
    diff = json.loads(run(["./tcx", "subagents", "diff", "fundamental-analyst"], workspace).stdout)
    assert diff["missing_from_projected"] == []
    assert diff["extra_projected"] == []
    projected = json.loads(run(["./tcx", "subagents", "project", "--role", "fundamental-analyst"], workspace).stdout)
    assert projected["projection_hash"] == projection_manifest["projection_hash"]
    mainagent_metadata = list((workspace / ".agents" / "skills").glob("*/agents/openai.yaml"))
    subagent_metadata = list((workspace / ".tradingcodex" / "subagents" / "skills").glob("*/*/agents/openai.yaml"))
    assert len(mainagent_metadata) >= 5
    assert len(subagent_metadata) + len(skill_index["skills"]) >= 23
    assert not (workspace / ".tradingcodex" / "user" / "profile.md").exists()
    assert not (workspace / ".tradingcodex" / "mainagent" / "head-manager-interview.md").exists()
    assert not (workspace / ".agents" / "skills" / "head-manager-interview").exists()
    workspace_status = json.loads(run(["./tcx", "workspace", "status"], workspace).stdout)
    assert workspace_status["workspace_id"] == workspace_manifest["workspace_id"]
    assert workspace_status["active_profile"]["portfolio_id"] == "default-paper"
    profile_status = json.loads(run(["./tcx", "profile", "status"], workspace).stdout)
    assert profile_status["active_profile"]["label"] == "shared central paper profile"
    doctor = run(["./tcx", "doctor"], workspace).stdout
    assert "TradingCodex doctor passed" in doctor
    assert "improvement" in doctor
    assert "TradingCodex MCP autostarts local service" in doctor
    assert "head-manager MCP execution submit excluded" in doctor
    assert "execution-operator MCP execution allowlist configured" in doctor
    assert "risk-manager MCP approval allowlist configured" in doctor
    improvement_doctor = run(["./tcx", "doctor", "--layer", "improvement"], workspace).stdout
    assert "TradingCodex doctor passed" in improvement_doctor
    assert "skill installed: tcx-workflow" in improvement_doctor
    assert "no-overlap handoff contract installed" in improvement_doctor
    removed_layer = run(["./tcx", "doctor", "--layer", "task-harness"], workspace, expect_ok=False)
    assert 'unknown layer "task-harness"' in removed_layer.stderr
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
    assert "tcx service ensure [addrport]" in service_usage.stderr
    assert "tcx service status [addrport] [--json]" in service_usage.stderr
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
    assert "TradingCodex has three planes" in head_manager_instructions
    assert "# Startup Context" in head_manager_instructions
    assert "tradingcodex-session-context" in head_manager_instructions
    assert ".tradingcodex/mainagent/server-status.json" in head_manager_instructions
    assert "Do not open the dashboard unless the user asks" in head_manager_instructions
    assert "server_status.service_issue" in head_manager_instructions
    assert "update_status.can_self_update=true" in head_manager_instructions
    assert "fully quit and restart Codex" in head_manager_instructions
    assert "not an autonomous trading bot" not in head_manager_instructions
    assert "# Plane Routing" in head_manager_instructions
    assert "# Build Gate" in head_manager_instructions
    assert "# Execution Boundary" in head_manager_instructions
    assert not re.search(r"[\uac00-\ud7a3]", head_manager_instructions)
    assert "Use repo skills as short procedures" in head_manager_instructions
    assert "They do not grant role eligibility" in head_manager_instructions
    assert "This base instruction owns" not in head_manager_instructions
    assert "strategy-creator" in head_manager_instructions
    assert "Only accepted role artifacts move downstream" in head_manager_instructions
    assert "apply_patch" in head_manager_instructions
    assert "Build mode allows product/code/template/provider changes" in head_manager_instructions
    workspace_agents = (workspace / "AGENTS.md").read_text(encoding="utf-8")
    assert "generated workspace guide" in workspace_agents
    assert "Follow every applicable `AGENTS.md`" in workspace_agents
    assert "Keep prompts lean" in workspace_agents
    tradingcodex_home = root_config["sandbox_workspace_write"]["writable_roots"][0]
    assert tradingcodex_home.endswith(".tradingcodex")
    assert root_config["sandbox_workspace_write"]["network_access"] is True
    assert root_config["permissions"]["tradingcodex"]["extends"] == ":workspace"
    assert root_config["permissions"]["tradingcodex"]["network"]["enabled"] is True
    home_rules = root_config["permissions"]["tradingcodex"]["filesystem"][tradingcodex_home]
    assert home_rules["secrets/**"] == "deny"
    assert home_rules["**/*.env"] == "deny"
    root_config_text = (workspace / ".codex" / "config.toml").read_text(encoding="utf-8")
    assert "tcx-workflow/SKILL.md" in root_config_text
    assert "tcx-server/SKILL.md" in root_config_text
    assert "tcx-build/SKILL.md" in root_config_text
    assert "strategy-creator/SKILL.md" in root_config_text
    assert "postmortem/SKILL.md" in root_config_text
    assert ".tradingcodex/subagents/skills/shared/collect-evidence/SKILL.md" not in root_config_text
    for profile_name in [
        "tradingcodex-fundamental",
        "tradingcodex-technical",
        "tradingcodex-news",
        "tradingcodex-macro",
        "tradingcodex-instrument",
        "tradingcodex-valuation",
        "tradingcodex-portfolio",
        "tradingcodex-risk",
        "tradingcodex-execution",
    ]:
        filesystem_rules = root_config["permissions"][profile_name]["filesystem"][":workspace_roots"]
        assert ".tradingcodex/user/**" not in filesystem_rules
        assert filesystem_rules[".agents/skills/strategy-*/**"] == "deny"
        assert filesystem_rules[".agents/skills/tcx-workflow/**"] == "deny"
        assert filesystem_rules[".agents/skills/strategy-creator/**"] == "deny"
    expected_tcx_mcp_args = ["--refresh", "--from", "tradingcodex", "python", "-m", "tradingcodex_cli", "mcp", "stdio"]
    root_mcp = root_config["mcp_servers"]["tradingcodex"]
    assert root_mcp["command"] == "uvx"
    assert root_mcp["args"] == expected_tcx_mcp_args
    assert root_mcp["enabled"] is True
    assert root_mcp["env"]["TRADINGCODEX_MCP_AUTOSTART_SERVICE"] == "1"
    assert root_mcp["env"]["TRADINGCODEX_SERVICE_ADDR"] == "127.0.0.1:48267"
    assert root_mcp["env"]["TRADINGCODEX_WORKSPACE_ROOT"] == str(workspace)
    assert set(root_mcp["enabled_tools"]).issubset(actual_mcp_tools)
    assert stale_mcp_tool_names.isdisjoint(root_mcp["enabled_tools"])
    assert "simulate_policy" in root_mcp["enabled_tools"]
    assert "get_tradingcodex_status" in root_mcp["enabled_tools"]
    assert "get_runtime_mode" in root_mcp["enabled_tools"]
    assert "get_update_status" in root_mcp["enabled_tools"]
    assert "get_connector_build_status" in root_mcp["enabled_tools"]
    assert "record_audit_event" in root_mcp["enabled_tools"]
    assert "get_portfolio_snapshot" in root_mcp["enabled_tools"]
    assert "list_external_mcp_connections" in root_mcp["enabled_tools"]
    assert "discover_external_mcp_connection" in root_mcp["enabled_tools"]
    assert "review_external_mcp_tool" in root_mcp["enabled_tools"]
    assert "list_broker_adapter_providers" in root_mcp["enabled_tools"]
    assert "connect_broker_connector" in root_mcp["enabled_tools"]
    assert "scaffold_broker_connector" in root_mcp["enabled_tools"]
    assert "register_broker_connector" in root_mcp["enabled_tools"]
    assert "validate_broker_connector_build" in root_mcp["enabled_tools"]
    assert "get_broker_capability_profile" in root_mcp["enabled_tools"]
    assert "get_broker_instrument_constraints" in root_mcp["enabled_tools"]
    assert "preview_order_translation" in root_mcp["enabled_tools"]
    assert "submit_approved_order" not in root_mcp["enabled_tools"]
    assert "cancel_approved_order" not in root_mcp["enabled_tools"]
    for agent_file in agent_files:
        agent_config = agent_file.read_text(encoding="utf-8")
        agent_toml = tomllib.loads(agent_config)
        assert agent_toml["name"] == agent_file.stem
        assert agent_toml["nickname_candidates"] == [agent_file.stem]
        assert agent_toml["description"]
        assert agent_toml["developer_instructions"]
        assert "request revision from the owning role" in agent_toml["developer_instructions"]
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
            assert "request_order_approval" in agent_mcp["enabled_tools"]
            assert "submit_approved_order" in agent_mcp["disabled_tools"]
            assert "create_order_ticket" not in agent_mcp["enabled_tools"]
        if agent_file.stem == "instrument-analyst":
            assert "get_broker_instrument_constraints" in agent_mcp["enabled_tools"]
            assert "create_order_ticket" not in agent_mcp["enabled_tools"]
        if agent_file.stem == "execution-operator":
            assert "submit_approved_order" in agent_mcp["enabled_tools"]
            assert "request_order_approval" not in agent_mcp["enabled_tools"]
            assert {"place_order", "replace_order", "withdraw"}.isdisjoint(set(agent_mcp["enabled_tools"]))
    assert run(["./tcx", "skills", "list"], workspace).stdout.splitlines() == [
        "tcx-workflow",
        "tcx-server",
        "tcx-build",
        "strategy-creator",
        "postmortem",
    ]
    assert len(run(["./tcx", "skills", "list", "--all"], workspace).stdout.splitlines()) == 23


def test_runtime_mode_update_and_connector_build_cli(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    mode_status = json.loads(run(["./tcx", "mode", "status", "--json"], workspace).stdout)
    assert mode_status["mode"] == "operate"
    assert mode_status["build_enabled"] is False
    missing_reason = run(["./tcx", "mode", "set", "build"], workspace, expect_ok=False)
    assert "requires --reason" in missing_reason.stderr

    run(["./tcx", "mode", "set", "build", "--reason", "connector test"], workspace)
    build_status = json.loads(run(["./tcx", "mode", "status", "--json"], workspace).stdout)
    assert build_status["mode"] == "build"
    assert build_status["tcx_build_mode_active"] is True
    assert build_status["build_enabled"] is False
    assert "full access" in build_status["build_blocked_reason"]

    module_lock_path = workspace / ".tradingcodex" / "generated" / "module-lock.json"
    module_lock = json.loads(module_lock_path.read_text(encoding="utf-8"))
    module_lock["tradingcodex_version"] = "0.0.1"
    module_lock_path.write_text(json.dumps(module_lock, indent=2) + "\n", encoding="utf-8")
    update_status = json.loads(
        run(
            ["./tcx", "update", "status", "--json"],
            workspace,
            env_extra={"TRADINGCODEX_CODEX_PERMISSION": "danger-full-access", "TRADINGCODEX_LATEST_RELEASE_VERSION": TRADINGCODEX_VERSION},
        ).stdout
    )
    assert update_status["update_available"] is True
    assert update_status["can_self_update"] is True
    assert update_status["command"] == "./tcx update --skip-refresh"

    providers = json.loads(run(["./tcx", "connectors", "providers"], workspace).stdout)
    provider_ids = {provider["provider_id"] for provider in providers["providers"]}
    assert "paper" in provider_ids
    assert "binance_spot" not in provider_ids

    raw_ref = run(["./tcx", "connectors", "connect", "raw-secret-test", "--credential-ref", "unit-secret"], workspace, expect_ok=False)
    assert "raw secrets are not accepted" in raw_ref.stderr

    missing_connect = json.loads(
        run(
            [
                "./tcx",
                "connectors",
                "connect",
                "binance-spot-testnet",
                "--provider",
                "binance",
                "--credential-ref",
                "env:BINANCE_TESTNET",
                "--environment",
                "testnet",
            ],
            workspace,
        ).stdout
    )
    assert missing_connect["status"] == "provider_missing"
    assert missing_connect["lifecycle_state"] == "provider_missing"
    assert missing_connect["live_order_enabled"] is False

    scaffold = json.loads(
        run(
            [
                "./tcx",
                "connectors",
                "scaffold",
                "binance-spot-testnet",
                "--provider",
                "binance",
                "--credential-ref",
                "env:BINANCE_TESTNET",
                "--environment",
                "testnet",
            ],
            workspace,
        ).stdout
    )
    assert scaffold["status"] == "scaffolded"
    assert scaffold["live_order_enabled"] is False
    assert scaffold["provider_development_required"] is True
    assert "unit-secret" not in str(scaffold)
    assert (workspace / scaffold["files"]["profile"]).exists()
    profile = json.loads((workspace / scaffold["files"]["profile"]).read_text(encoding="utf-8"))
    assert profile["credential_ref"] == "env:BINANCE_TESTNET"
    assert profile["build_lane"]["live_order_enabled"] is False

    failed_register = run(
        [
            "./tcx",
            "connectors",
            "register",
            "--provider",
            "binance",
            "--broker-id",
            "binance-spot-testnet",
            "--credential-ref",
            "env:BINANCE_TESTNET",
            "--environment",
            "testnet",
        ],
        workspace,
        expect_ok=False,
    )
    assert "unknown broker provider" in failed_register.stderr
    registered = json.loads(
        run(
            [
                "./tcx",
                "connectors",
                "register",
                "--provider",
                "paper",
                "--broker-id",
                "paper-provider",
                "--credential-ref",
                "env:PAPER_PROVIDER",
                "--environment",
                "paper",
            ],
            workspace,
        ).stdout
    )
    assert registered["broker_id"] == "paper-provider"
    assert registered["provider_id"] == "paper"
    assert registered["connection"]["metadata"]["execution_enabled"] is False
    validated = json.loads(run(["./tcx", "connectors", "validate", "binance-spot-testnet"], workspace).stdout)
    assert validated["registered"] is False
    assert validated["live_order_enabled"] is False
    mcp_mode = call_mcp_tool(workspace, "get_runtime_mode", {"principal_id": "head-manager"})
    assert mcp_mode["mode"] == "build"
    mcp_update = call_mcp_tool(workspace, "get_update_status", {"principal_id": "head-manager"})
    assert "update_available" in mcp_update
    mcp_connectors = call_mcp_tool(workspace, "get_connector_build_status", {"principal_id": "head-manager"})
    assert mcp_connectors["count"] == 1


def test_file_native_skill_proposal_and_projection_cli(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)

    proposed = json.loads(
        run(["./tcx", "skills", "propose-add", "--to", "fundamental-analyst", "--skill", "postmortem"], workspace).stdout
    )
    assert proposed["status"] == "blocked"
    assert "project-scope mainagent skill" in proposed["validation_errors"][0]
    blocked_project_scope = run(["./tcx", "skills", "apply-proposal", proposed["path"]], workspace, expect_ok=False)
    assert "project-scope mainagent skill" in blocked_project_scope.stderr
    assert not (workspace / ".tradingcodex" / "mainagent" / "applied-skill-changes.jsonl").exists()

    blocked = json.loads(
        run(["./tcx", "skills", "propose-add", "--to", "fundamental-analyst", "--skill", "execute-paper-order"], workspace).stdout
    )
    assert blocked["status"] == "blocked"
    assert "blocked risk tags" in blocked["validation_errors"][0]
    blocked_apply = run(["./tcx", "skills", "apply-proposal", blocked["path"]], workspace, expect_ok=False)
    assert "cannot receive execute-paper-order" in blocked_apply.stderr


def test_strategy_skills_are_root_visible_but_not_subagent_projected(tmp_path: Path, monkeypatch) -> None:
    workspace = make_workspace(tmp_path)
    strategy_name = "strategy-quality-compounder"
    state = write_strategy_skill_fixture(workspace, strategy_name)
    state = project_agent_configuration(workspace, applied_by="test-filesystem-strategy")

    assert state["skills"][strategy_name]["source"] == "strategy"
    assert state["skills"][strategy_name]["active"] is True
    assert {record["name"] for record in read_strategy_skill_records(workspace)} == {strategy_name}
    root_config_text = (workspace / ".codex" / "config.toml").read_text(encoding="utf-8")
    assert "# BEGIN TradingCodex strategy skills" in root_config_text
    assert f".agents/skills/{strategy_name}/SKILL.md" in root_config_text
    for agent_file in sorted((workspace / ".codex" / "agents").glob("*.toml")):
        agent_toml = tomllib.loads(agent_file.read_text(encoding="utf-8"))
        strategy_entries = [
            block
            for block in agent_toml.get("skills", {}).get("config", [])
            if str(block.get("path", "")).endswith(f".agents/skills/{strategy_name}/SKILL.md")
        ]
        assert strategy_entries
        assert all(block.get("enabled") is False for block in strategy_entries)

    assert strategy_name in run(["./tcx", "skills", "list"], workspace).stdout.splitlines()
    assert strategy_name in run(["./tcx", "skills", "list", "--all"], workspace).stdout.splitlines()

    monkeypatch.setenv("TRADINGCODEX_WORKSPACE_ROOT", str(workspace))
    client = Client(REMOTE_ADDR="127.0.0.1")
    assert strategy_name in client.get("/api/harness/skills").json()["skills"]
    assert strategy_name in client.get("/api/harness/skills?include_internal=true").json()["skills"]
    strategy_api_records = client.get("/api/harness/strategies").json()["strategies"]
    assert {
        (record["name"], record["source_file"])
        for record in strategy_api_records
    } == {
        (strategy_name, f".agents/skills/{strategy_name}/SKILL.md"),
    }
    strategy_web_body = client.get("/harness/strategies/").content.decode()
    assert "Quality Compounder" in strategy_web_body


def test_init_prepares_central_django_runtime(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    home = tmp_path / "tc-home"
    result = run(
        [sys.executable, "-m", "tradingcodex_cli", "init", str(workspace)],
        ROOT,
        env_extra={"TRADINGCODEX_DB_NAME": None, "TRADINGCODEX_HOME": str(home)},
    )
    db_path = home / "state" / "tradingcodex.sqlite3"

    assert f"Central DB: {db_path}" in result.stdout
    assert "Workspace ID: tcxw_" in result.stdout
    assert "Active Profile: shared central paper profile" in result.stdout
    assert "MCP Scope: project-scoped" in result.stdout
    assert "Execution Mode: non-live: paper/validation-only/broker-validation" in result.stdout
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
        assert "harness_skillproposal" not in table_names
        assert "harness_roleskillassignment" not in table_names
        assert "mcp_mcptooldefinition" in table_names
        assert "research_researchartifact" not in table_names
        assert "research_researchartifactversion" not in table_names
        assert "research_sourcesnapshot" not in table_names
        assert "research_evidencepack" not in table_names
        assert connection.execute("select count(*) from django_migrations where app = 'orders' and name = '0001_initial'").fetchone()[0] == 1
        assert connection.execute("select count(*) from django_migrations where app = 'research'").fetchone()[0] == 0
        assert connection.execute("select count(*) from harness_workspacecontext where path = ?", (str(workspace.resolve()),)).fetchone()[0] == 1
        assert connection.execute("select workspace_id from harness_workspacecontext where path = ?", (str(workspace.resolve()),)).fetchone()[0].startswith("tcxw_")


def test_workspace_context_normalizes_legacy_execution_mode(tmp_path: Path) -> None:
    workspace = tmp_path / "legacy-execution-mode"
    workspace.mkdir()
    manifest_dir = workspace / ".tradingcodex"
    manifest_dir.mkdir()
    (manifest_dir / "workspace.json").write_text(
        json.dumps({
            "workspace_id": "tcxw_legacy",
            "project_name": "legacy-execution-mode",
            "active_profile": {"profile_id": "default-paper"},
            "mcp_scope": "project-scoped",
            "execution_mode": "non-live: paper/stub/broker-validation",
        }, indent=2)
        + "\n",
        encoding="utf-8",
    )

    context = workspace_context_payload(workspace)
    assert context["execution_mode"] == "non-live: paper/validation-only/broker-validation"


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


def test_attach_current_directory_preserves_workspace_identity(tmp_path: Path) -> None:
    workspace = tmp_path / "attach-workspace"
    workspace.mkdir()
    home = tmp_path / "tc-home-attach"
    env_extra = {"TRADINGCODEX_DB_NAME": None, "TRADINGCODEX_HOME": str(home)}

    attached = run([sys.executable, "-m", "tradingcodex_cli", "attach", "."], workspace, env_extra=env_extra)
    manifest = json.loads((workspace / ".tradingcodex" / "workspace.json").read_text(encoding="utf-8"))
    workspace_id = manifest["workspace_id"]

    assert f"TradingCodex workspace attached: {workspace.resolve()}" in attached.stdout
    assert "MCP Scope: project-scoped" in attached.stdout
    assert workspace_id.startswith("tcxw_")

    refreshed = run([sys.executable, "-m", "tradingcodex_cli", "attach", "."], workspace, env_extra=env_extra)
    refreshed_manifest = json.loads((workspace / ".tradingcodex" / "workspace.json").read_text(encoding="utf-8"))
    assert refreshed_manifest["workspace_id"] == workspace_id
    assert f"TradingCodex workspace attached: {workspace.resolve()}" in refreshed.stdout


def test_update_refreshes_workspace_contract_and_preserves_identity(tmp_path: Path) -> None:
    workspace = tmp_path / "update-workspace"
    home = tmp_path / "tc-home-update"
    env_extra = {"TRADINGCODEX_DB_NAME": None, "TRADINGCODEX_HOME": str(home)}
    run([sys.executable, "-m", "tradingcodex_cli", "init", str(workspace)], ROOT, env_extra=env_extra)
    selected_profile = json.loads(run(["./tcx", "profile", "create", "strategy-lab"], workspace, env_extra=env_extra).stdout)
    assert selected_profile["profile"]["profile_id"] == "strategy-lab"
    run(["./tcx", "profile", "select", "strategy-lab"], workspace, env_extra=env_extra)
    manifest_before = json.loads((workspace / ".tradingcodex" / "workspace.json").read_text(encoding="utf-8"))
    legacy_manifest = {**manifest_before, "execution_mode": "non-live: paper/stub/broker-validation"}
    (workspace / ".tradingcodex" / "workspace.json").write_text(json.dumps(legacy_manifest, indent=2) + "\n", encoding="utf-8")
    (workspace / ".tradingcodex" / "generated" / "module-lock.json").write_text('{"tradingcodex_version":"stale"}\n', encoding="utf-8")

    updated = run([sys.executable, "-m", "tradingcodex_cli", "update", ".", "--no-doctor"], workspace, env_extra=env_extra)
    manifest_after = json.loads((workspace / ".tradingcodex" / "workspace.json").read_text(encoding="utf-8"))
    module_lock = json.loads((workspace / ".tradingcodex" / "generated" / "module-lock.json").read_text(encoding="utf-8"))
    wrapper = (workspace / "tcx").read_text(encoding="utf-8")

    assert f"TradingCodex workspace updated: {workspace.resolve()}" in updated.stdout
    assert manifest_after["workspace_id"] == manifest_before["workspace_id"]
    assert manifest_after["active_profile"]["profile_id"] == "strategy-lab"
    assert manifest_after["execution_mode"] == "non-live: paper/validation-only/broker-validation"
    assert module_lock["tradingcodex_version"] == TRADINGCODEX_VERSION
    assert module_lock["tradingcodex_package_spec"] == "tradingcodex"
    assert 'TRADINGCODEX_UPDATE_SKIP_REFRESH="${TRADINGCODEX_UPDATE_SKIP_REFRESH:-0}"' in wrapper
    assert 'if [ "${1:-}" = "update" ] && [ "$TRADINGCODEX_UPDATE_SKIP_REFRESH" != "1" ] && command -v uvx >/dev/null 2>&1; then' in wrapper
    assert '"--skip-refresh"' in wrapper
    assert "  ./tcx doctor" in updated.stdout

    (workspace / ".tradingcodex" / "generated" / "module-lock.json").write_text('{"tradingcodex_version":"stale-again"}\n', encoding="utf-8")
    skip_refresh_update = run(["./tcx", "update", "--skip-refresh", "--no-doctor"], workspace, env_extra=env_extra)
    skip_refresh_lock = json.loads((workspace / ".tradingcodex" / "generated" / "module-lock.json").read_text(encoding="utf-8"))
    assert f"TradingCodex workspace updated: {workspace.resolve()}" in skip_refresh_update.stdout
    assert skip_refresh_lock["tradingcodex_version"] == TRADINGCODEX_VERSION

    doctor = run(["./tcx", "doctor"], workspace, env_extra=env_extra)
    assert "TradingCodex doctor passed" in doctor.stdout

    not_workspace = tmp_path / "not-workspace"
    not_workspace.mkdir()
    rejected = run([sys.executable, "-m", "tradingcodex_cli", "update", "."], not_workspace, expect_ok=False, env_extra=env_extra)
    assert "Not a TradingCodex generated workspace" in rejected.stderr


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
    assert "Research artifact language: same language as the original user request unless explicitly overridden" in macro
    assert "Use handoff states: accepted, revise, blocked, waiting." in macro
    assert "Context budget:" in macro
    assert "context_summary" in macro
    assert "reader_summary, next_action" in macro
    assert "Reader mode: open with a plain-English answer" in macro
    assert "Artifact memory:" in macro
    assert "improvement proposals for reuse" in macro
    assert "Do not let downstream roles redo missing upstream work" in macro
    assert "Artifact memory: write artifacts in the research artifact language" in macro
    language_neutral = build_subagent_starter_prompt("Analyze Samsung Electronics. No order.")
    assert "Research artifact language: same language as the original user request unless explicitly overridden" in language_neutral
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
    earnings_no_valuation = build_subagent_starter_prompt("NVDA earnings preview and catalyst review, no valuation, no order and no trading")
    assert "Workflow lane: thesis_review" in earnings_no_valuation
    earnings_no_valuation_spawn_line = next(line for line in earnings_no_valuation.splitlines() if line.startswith("Spawn these fixed role subagents"))
    assert "valuation-analyst" not in earnings_no_valuation_spawn_line
    fair_value_portfolio = build_subagent_starter_prompt("TSLA fair value and whether it fits my portfolio, no order")
    assert "Workflow lane: thesis_review_then_portfolio_risk_review" in fair_value_portfolio
    assert "Workflow stage order: Intake -> Evidence -> Valuation -> Portfolio fit -> Risk review -> Challenge review -> Synthesis" in fair_value_portfolio
    assert "valuation-analyst" in fair_value_portfolio
    assert "portfolio-manager" in fair_value_portfolio
    assert "risk-manager" in fair_value_portfolio
    assert "Investor profile gaps to request before recommendation, sizing, approval, or execution" in fair_value_portfolio
    assert "Investor profile questions to ask if unanswered" in fair_value_portfolio
    assert "Method lenses for this lane: Suitability/profile gate (FINRA Rule 2111; SEC Reg BI)" in fair_value_portfolio
    assert "Iteration controls: stay within the selected lane" in fair_value_portfolio
    assert "lane controls: Decision loop:" in fair_value_portfolio
    assert "Stop condition: Stop at decision support" in fair_value_portfolio
    assert "Judgment controls:" in fair_value_portfolio
    assert "Challenge review: before final synthesis" in fair_value_portfolio
    assert "Strategy baseline: Strategy library not inspected" in fair_value_portfolio
    assert "What outcome are you trying to achieve with this idea?" in fair_value_portfolio
    assert "risk tolerance and loss capacity" in fair_value_portfolio
    assert "instrument-analyst" not in fair_value_portfolio
    assert "execution-operator" not in fair_value_portfolio
    fair_value_summary = build_workflow_intake_summary("TSLA fair value and whether it fits my portfolio, no order")
    assert fair_value_summary["label"] == "Decision support"
    assert fair_value_summary["plain_language_output"] is True
    assert fair_value_summary["idea_translation"]["label"] == "Idea translated"
    assert "candidate thesis" in fair_value_summary["idea_translation"]["working_hypothesis"]
    assert "accepted role artifacts" in fair_value_summary["idea_translation"]["safety_boundary"]
    assert "risk tolerance and loss capacity" in fair_value_summary["investor_profile_inputs"]
    assert [item["label"] for item in fair_value_summary["method_lenses"]] == [
        "Suitability/profile gate",
        "Portfolio risk lens",
        "Factor/exposure lens",
    ]
    assert [item["label"] for item in fair_value_summary["loop_controls"]] == [
        "Decision loop",
        "Stop condition",
        "Verification budget",
    ]
    assert "order tickets, approvals, and execution" in fair_value_summary["loop_controls"][1]["detail"]
    assert "instead of widening the lane" in fair_value_summary["loop_controls"][2]["detail"]
    assert fair_value_summary["method_lenses"][0]["plain"].startswith("The system needs to know")
    assert "Markowitz" in fair_value_summary["method_lenses"][1]["reference"]
    assert [stage["key"] for stage in fair_value_summary["workflow_stages"]] == [
        "intake",
        "evidence",
        "valuation",
        "portfolio_fit",
        "risk_review",
        "challenge_review",
        "synthesis",
    ]
    assert "source/as-of posture recorded" in fair_value_summary["workflow_stages"][1]["exit_criteria"]
    assert fair_value_summary["workflow_stages"][-2]["label"] == "Challenge review"
    assert "counterarguments named" in fair_value_summary["workflow_stages"][-2]["exit_criteria"]
    assert [item["label"] for item in fair_value_summary["judgment_controls"]] == [
        "Fixed rule baseline",
        "Challenge review",
    ]
    assert [item["label"] for item in fair_value_summary["review_highlights"]] == [
        "Pressure test",
        "Profile gap",
        "Stop before action",
    ]
    assert "contrary evidence" in fair_value_summary["review_highlights"][0]["detail"]
    assert "risk tolerance and loss capacity" in fair_value_summary["review_highlights"][1]["detail"]
    assert "separate gates" in fair_value_summary["review_highlights"][2]["detail"]
    assert fair_value_summary["strategy_baseline"]["mode"] == "not_inspected"
    assert fair_value_summary["blocked_action_details"][0]["label"] == "Order ticket"
    assert "workflow reaches portfolio/order readiness" in fair_value_summary["blocked_action_details"][0]["reason"]
    assert fair_value_summary["next_allowed_actions"][0]["label"] == "Answer missing profile questions"
    assert "before recommendation or sizing" in fair_value_summary["next_allowed_actions"][0]["detail"]
    assert fair_value_summary["questions_to_answer"][0]["field"] == "investment objective"
    assert fair_value_summary["questions_to_answer"][0]["question"] == "What outcome are you trying to achieve with this idea?"
    assert "objective before risk" in fair_value_summary["questions_to_answer"][0]["why_required"]
    assert "business, financial drivers" in fair_value_summary["subagents"][0]["why_selected"]
    assert {agent["role"] for agent in fair_value_summary["subagents"]} >= {"valuation-analyst", "portfolio-manager", "risk-manager"}
    assert is_investment_workflow_request("TSLA feels interesting, no order") is True
    idea_summary = build_workflow_intake_summary("TSLA feels interesting, no order")
    assert idea_summary["label"] == "Thesis review"
    assert idea_summary["workflow_lane"] == "thesis_review"
    assert idea_summary["routing_flags"]["deep_thesis_default"] is True
    assert "thesis check" in idea_summary["idea_translation"]["working_hypothesis"]
    assert idea_summary["next_allowed_actions"][0]["label"] == "Dispatch thesis roles"
    assert any(agent["role"] == "fundamental-analyst" for agent in idea_summary["subagents"])
    assert is_investment_workflow_request("AAPL seems cheap") is True
    assert is_investment_workflow_request("This repo feels interesting") is False
    assert is_investment_workflow_request("CSS seems cheap") is False
    crypto = build_subagent_starter_prompt("BTC trend review no trading")
    assert "Investment universe: Crypto assets" in crypto
    assert "Investment universe: public_crypto" not in crypto
    assert "instrument-analyst" in crypto
    assert "fundamental-analyst" not in crypto
    assert "execution-operator" not in crypto
    crypto_research = build_subagent_starter_prompt("BTC feels strong but I do not want to trade. Just research trend and risks.")
    assert "Workflow lane: research_only" in crypto_research
    assert "Spawn these fixed role subagents in parallel: technical-analyst, instrument-analyst" in crypto_research
    assert "portfolio-manager" not in next(line for line in crypto_research.splitlines() if line.startswith("Spawn these fixed role subagents"))
    assert "execution-operator" not in crypto_research
    connector_only_request = "Configure a reviewed test or sandbox broker connector only. No order, no approval, no execution, do not read secrets."
    assert is_investment_workflow_request(connector_only_request) is False
    connector_only = build_subagent_starter_prompt(connector_only_request)
    assert "Workflow lane: connector_build" in connector_only
    assert "No fixed-role subagent dispatch is required" in connector_only
    assert "$tcx-build" in connector_only
    assert "execution-operator" not in connector_only
    connector_build_request = "binance. No order, no approval, no execution, do not read secrets."
    assert is_connector_build_request(connector_build_request) is True
    assert is_investment_workflow_request(connector_build_request) is False
    connector_hook_preview_request = (
        "Run .codex/hooks/tradingcodex_hook.py user-prompt-submit for this connector prompt. "
        "Connect mock-json and mock-csv brokers, sync broker accounts, and preview an AAPL buy quantity 1 translation. "
        "No order, no trading, do not read secrets."
    )
    assert is_connector_build_request(connector_hook_preview_request) is True
    assert is_investment_workflow_request(connector_hook_preview_request) is False
    assert classify_starter_request(connector_hook_preview_request)["lane"] == "connector_build"
    connector_build = build_subagent_starter_prompt(connector_build_request)
    assert "Workflow lane: connector_build" in connector_build
    assert "Operational universe: Broker/API connector build" in connector_build
    assert "$tcx-build" in connector_build
    assert "live submit" in connector_build
    connector_context = build_compact_dispatch_context(connector_build_request)
    assert connector_context["routing_status"]["lane"] == "connector_build"
    assert connector_context["required_subagents"] == []
    strategy_authoring_request = "Create a fixed quality strategy for dividend stocks. Do not analyze any ticker yet."
    assert is_investment_workflow_request(strategy_authoring_request) is False
    strategy_authoring = build_subagent_starter_prompt(strategy_authoring_request)
    assert "Workflow lane: head_manager_strategy_authoring" in strategy_authoring
    assert "Operational universe: Strategy authoring" in strategy_authoring
    assert "No fixed-role subagent dispatch is required" in strategy_authoring
    assert "$strategy-creator" in strategy_authoring
    assert "$tcx-server" not in strategy_authoring
    assert "Blocked actions: ticker analysis, order ticket, approval, execution, direct broker API, secret read" in strategy_authoring
    strategy_context = build_compact_dispatch_context(strategy_authoring_request)
    assert strategy_context["selected_team_binding"] is False
    assert strategy_context["required_subagents"] == []
    assert strategy_context["dispatch_rules"] == [
        "handle_in_head_manager_lane",
        "do_not_dispatch_fixed_role_subagents",
        "do_not_create_blocked_artifacts",
    ]
    broad = build_subagent_starter_prompt("Analyze NVDA for me. No order and no trading.")
    assert "Workflow lane: thesis_review" in broad
    assert "Spawn these fixed role subagents in parallel: fundamental-analyst, technical-analyst, news-analyst, valuation-analyst" in broad
    assert "This selected team is binding for the current lane" in broad
    assert "Deep thesis default" in broad
    assert "do not set `fork_context` to true" in broad
    analyze_no_valuation = build_subagent_starter_prompt("Analyze NVDA. No valuation.")
    assert "Workflow lane: thesis_review" in analyze_no_valuation
    analyze_no_valuation_spawn = next(line for line in analyze_no_valuation.splitlines() if line.startswith("Spawn these fixed role subagents"))
    assert "valuation-analyst" not in analyze_no_valuation_spawn
    profile_only = classify_starter_request("Analyze NVDA. Company facts only.")
    assert profile_only["lane"] == "research_only"
    assert profile_only["subagents"] == ["fundamental-analyst"]
    chart_only = classify_starter_request("NVDA chart only. No trading.")
    assert chart_only["lane"] == "research_only"
    assert chart_only["subagents"] == ["technical-analyst"]
    technical_news = classify_starter_request("NVDA technical and news review no trading")
    assert technical_news["lane"] == "research_only"
    assert technical_news["subagents"] == ["technical-analyst", "news-analyst"]
    chart_fundamentals = classify_starter_request("NVDA chart plus fundamentals no trading")
    assert chart_fundamentals["lane"] == "research_only"
    assert chart_fundamentals["subagents"] == ["fundamental-analyst", "technical-analyst"]
    profile_question = classify_starter_request("What does NVDA do?")
    assert is_investment_workflow_request("What does NVDA do?") is True
    assert profile_question["lane"] == "research_only"
    assert profile_question["subagents"] == ["fundamental-analyst"]
    facts_question = classify_starter_request("NVDA facts")
    assert is_investment_workflow_request("NVDA facts") is True
    assert facts_question["lane"] == "research_only"
    assert facts_question["subagents"] == ["fundamental-analyst"]
    chart_intent = normalize_investment_intent("NVDA chart only. No trading.")
    assert chart_intent.technical_only is True
    no_valuation = build_subagent_starter_prompt("Routing smoke test for NVDA. No order, no trading, no valuation. Use selected subagents only.")
    assert "Workflow lane: research_only" in no_valuation
    no_valuation_spawn_line = next(line for line in no_valuation.splitlines() if line.startswith("Spawn these fixed role subagents"))
    assert "valuation-analyst" not in no_valuation_spawn_line


def test_decision_workflow_plan_and_package_are_codex_native(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    plan = build_workflow_plan(workspace, "NVDA should I buy?")

    assert plan["lane"] == "thesis_review_then_portfolio_risk_review"
    assert plan["universe"] == "public_equity"
    assert {"valuation-analyst", "portfolio-manager", "risk-manager"} <= set(plan["selected_roles"])
    assert "risk tolerance and loss capacity" in plan["missing_profile"]
    assert "execution" in plan["blocked_actions"]
    assert "Explicitly use Codex subagents." in plan["starter_prompt"]

    ensure_runtime_database(workspace)
    from apps.orders.models import OrderTicket
    from apps.workflows.models import WorkflowRun

    before_tickets = OrderTicket.objects.count()
    package = create_decision_package(workspace, "NVDA earnings after results, prepare order draft only, no execution")

    assert package["status"] == "planned"
    assert package["plan"]["lane"] == "order_ticket_draft_gate"
    assert "execution" in package["plan"]["blocked_actions"]
    assert OrderTicket.objects.count() == before_tickets
    assert WorkflowRun.objects.filter(run_id=package["run_id"], status="planned", readiness_label="waiting").exists()
    assert (workspace / package["decision_package_path"]).exists()
    assert (workspace / package["workflow_run_path"]).exists()

    stored = get_decision_package(workspace, package["decision_id"])
    assert stored["handoff_state"] == "waiting"
    assert stored["readiness_label"] == "waiting"
    assert "accepted role artifacts" in stored["missing_evidence"]
    assert "## Codex Starter Prompt" in stored["markdown"]
    assert "Source/as-of posture: pending accepted artifacts" in stored["markdown"]
    assert list_decision_packages(workspace)["packages"][0]["decision_id"] == package["decision_id"]


def test_workflow_and_decision_cli_surfaces(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)

    plan = json.loads(run(["./tcx", "workflow", "plan", "NVDA should I buy?"], workspace).stdout)
    assert plan["lane"] == "thesis_review_then_portfolio_risk_review"
    assert "execution" in plan["blocked_actions"]
    assert plan["missing_profile"]

    package = json.loads(run(["./tcx", "workflow", "run", "Connect Upbit broker. No order, no execution."], workspace).stdout)
    assert package["plan"]["lane"] == "connector_build"
    assert package["plan"]["selected_roles"] == []
    assert (workspace / package["decision_package_path"]).exists()

    listed = json.loads(run(["./tcx", "decision", "list"], workspace).stdout)
    assert listed["packages"][0]["decision_id"] == package["decision_id"]
    shown = json.loads(run(["./tcx", "decision", "show", package["decision_id"]], workspace).stdout)
    assert shown["workflow_lane"] == "connector_build"
    assert "live submit" in shown["blocked_actions"]


def test_decisions_web_review_surface(tmp_path: Path, monkeypatch) -> None:
    workspace = make_workspace(tmp_path)
    package = create_decision_package(workspace, "AAPL seems cheap, but I am not sure why. No order.")
    monkeypatch.setenv("TRADINGCODEX_WORKSPACE_ROOT", str(workspace))

    client = Client(REMOTE_ADDR="127.0.0.1")
    list_page = client.get("/decisions/")
    list_body = list_page.content.decode()
    assert list_page.status_code == 200
    assert "Decisions" in list_body
    assert "Decision Workflow Alpha" in list_body
    assert package["decision_id"] in list_body
    assert "Thesis review" in list_body

    detail = client.get(f"/decisions/?decision={package['decision_id']}")
    detail_body = detail.content.decode()
    assert detail.status_code == 200
    assert "Decision package" in detail_body
    assert "Codex Starter Prompt" in detail_body
    assert "Blocked actions" in detail_body
    assert "execution" in detail_body


def test_subagents_prompt_cli_and_api_expose_intake_summary(tmp_path: Path, monkeypatch) -> None:
    workspace = make_workspace(tmp_path)
    request = "TSLA fair value and whether it fits my portfolio, no order"

    cli_json = json.loads(run(["./tcx", "subagents", "prompt", "--json", request], workspace).stdout)
    assert cli_json["intake_summary"]["label"] == "Decision support"
    assert cli_json["intake_summary"]["investment_universe"] == "public_equity"
    assert cli_json["intake_summary"]["investment_universe_label"] == "Public equities"
    assert cli_json["intake_summary"]["workflow_lane"] == "thesis_review_then_portfolio_risk_review"
    assert cli_json["intake_summary"]["idea_translation"]["plain_english"].startswith("Decision support:")
    assert cli_json["intake_summary"]["workflow_stages"][0]["label"] == "Intake"
    assert cli_json["intake_summary"]["workflow_stages"][-1]["label"] == "Synthesis"
    assert cli_json["intake_summary"]["method_lenses"][0]["label"] == "Suitability/profile gate"
    assert "before advice can be useful" in cli_json["intake_summary"]["method_lenses"][0]["plain"]
    assert "FINRA Rule 2111" in cli_json["intake_summary"]["method_lenses"][0]["reference"]
    assert cli_json["intake_summary"]["loop_controls"][0]["label"] == "Decision loop"
    assert "profile gaps and artifact handoffs" in cli_json["intake_summary"]["loop_controls"][0]["detail"]
    assert cli_json["intake_summary"]["loop_controls"][2]["label"] == "Verification budget"
    assert "source freshness" in cli_json["intake_summary"]["loop_controls"][2]["detail"]
    assert cli_json["intake_summary"]["judgment_controls"][1]["label"] == "Challenge review"
    assert [item["label"] for item in cli_json["intake_summary"]["review_highlights"]] == [
        "Pressure test",
        "Profile gap",
        "Stop before action",
    ]
    assert cli_json["intake_summary"]["strategy_baseline"]["mode"] == "no_saved_strategy"
    assert "No active user-approved strategy" in cli_json["intake_summary"]["strategy_baseline"]["summary"]
    assert "accepted artifacts cited" in cli_json["intake_summary"]["workflow_stages"][-1]["exit_criteria"]
    assert cli_json["intake_summary"]["blocked_action_details"][1]["label"] == "Approval"
    assert cli_json["intake_summary"]["next_allowed_actions"][1]["label"] == "Dispatch research, valuation, portfolio, and risk roles"
    assert "risk tolerance and loss capacity" in cli_json["intake_summary"]["investor_profile_inputs"]
    assert cli_json["intake_summary"]["questions_to_answer"][2]["question"] == "How much downside or temporary loss would be unacceptable?"
    assert "valuation range" in next(
        agent["why_selected"]
        for agent in cli_json["intake_summary"]["subagents"]
        if agent["role"] == "valuation-analyst"
    )
    assert "Reader mode: open with a plain-English answer" in cli_json["starter_prompt"]
    assert "Investor profile questions to ask if unanswered" in cli_json["starter_prompt"]
    assert "Method lenses for this lane" in cli_json["starter_prompt"]

    explained = run(["./tcx", "subagents", "prompt", "--explain", request], workspace).stdout
    assert "Workflow: Decision support" in explained
    assert "Universe: Public equities" in explained
    assert "Universe: public_equity" not in explained
    assert "Idea translated: Decision support:" in explained
    assert "Working hypothesis: Treat the idea as a candidate thesis" in explained
    assert "Why these roles:" in explained
    assert "Portfolio Manager: Tests portfolio fit" in explained
    assert "Why blocked:" in explained
    assert "Order ticket:" in explained
    assert "Next allowed actions:" in explained
    assert "Decision checks:" in explained
    assert "Pressure test: Before synthesis" in explained
    assert "Profile gap: Recommendation and sizing stay weak" in explained
    assert "Stop before action: A useful answer can end at decision support" in explained
    assert "Answer missing profile questions:" in explained
    assert "Method lenses:" in explained
    assert "Suitability/profile gate:" in explained
    assert "before advice can be useful" in explained
    assert "Plain meaning: The system needs to know" in explained
    assert "Reference: FINRA Rule 2111; SEC Reg BI" in explained
    assert "Iteration controls:" in explained
    assert "Decision loop: Iterate through evidence, valuation, portfolio fit, and risk review" in explained
    assert "Stop condition: Stop at decision support" in explained
    assert "Verification budget: After each pass" in explained
    assert "Judgment controls:" in explained
    assert "Challenge review: Before synthesis" in explained
    assert "Strategy baseline: No active user-approved strategy" in explained
    assert "Workflow steps:" in explained
    assert "2. Evidence:" in explained
    assert "Needs: artifact paths written" in explained
    assert "Profile needed before advice:" in explained
    assert "Questions to answer:" in explained
    assert "What outcome are you trying to achieve with this idea?" in explained
    assert "Codex prompt:" in explained

    updated_profile = json.loads(run([
        "./tcx",
        "profile",
        "update",
        "--objective",
        "medium-term compounder review",
        "--horizon",
        "3 to 5 years",
        "--risk-tolerance",
        "moderate drawdown tolerance",
    ], workspace).stdout)
    assert updated_profile["active_profile"]["investor_profile"]["investment_objective"] == "medium-term compounder review"
    assert updated_profile["active_profile"]["investor_profile"]["time_horizon"] == "3 to 5 years"
    profile_aware = json.loads(run(["./tcx", "subagents", "prompt", "--json", request], workspace).stdout)
    known_fields = {item["field"]: item["answer"] for item in profile_aware["intake_summary"]["investor_profile"]["known_fields"]}
    assert known_fields["investment objective"] == "medium-term compounder review"
    assert known_fields["time horizon"] == "3 to 5 years"
    assert known_fields["risk tolerance and loss capacity"] == "moderate drawdown tolerance"
    assert "investment objective" not in profile_aware["intake_summary"]["investor_profile_inputs"]
    assert "risk tolerance and loss capacity" not in profile_aware["intake_summary"]["investor_profile_inputs"]
    assert "liquidity or cash needs" in profile_aware["intake_summary"]["investor_profile_inputs"]
    assert profile_aware["intake_summary"]["next_allowed_actions"][0]["label"] == "Answer remaining profile questions"
    assert "liquidity or cash needs" in profile_aware["intake_summary"]["next_allowed_actions"][0]["detail"]
    assert "Known investor profile context from the active profile" in profile_aware["starter_prompt"]
    assert "medium-term compounder review" in profile_aware["starter_prompt"]
    assert "What outcome are you trying to achieve with this idea?" not in "\n".join(
        item["question"] for item in profile_aware["intake_summary"]["questions_to_answer"]
    )
    profile_registry = json.loads((workspace / ".tradingcodex" / "profiles.json").read_text(encoding="utf-8"))
    assert profile_registry["profiles"]["default-paper"]["investor_profile"]["investment_objective"] == "medium-term compounder review"

    monkeypatch.setenv("TRADINGCODEX_WORKSPACE_ROOT", str(workspace))
    web_client = Client(REMOTE_ADDR="127.0.0.1")
    web_response = web_client.post(
        "/workflow/starter-prompt/profile/",
        {
            "q": request,
            "liquidity_needs": "no near-term cash need",
            "constraints": "taxable account, no restricted symbols",
        },
    )
    assert web_response.status_code == 200
    web_body = web_response.content.decode()
    assert "Investor profile context saved" in web_body
    assert "no near-term cash need" in web_body
    assert "Method lenses" in web_body
    assert "Decision checks" in web_body
    assert "Pressure test" in web_body
    assert "Stop before action" in web_body
    assert "Review logic" in web_body
    assert "Codex handoff" in web_body
    assert "Iteration controls" in web_body
    assert "Decision loop" in web_body
    assert "Stop at decision support" in web_body
    assert "Judgment controls" in web_body
    assert "Strategy baseline" in web_body
    assert "No active user-approved strategy" in web_body
    assert "Portfolio risk lens" in web_body
    assert "A single idea should be judged by how it changes the whole portfolio." in web_body
    assert "Do you need cash from this account soon" not in web_body
    web_profile_aware = json.loads(run(["./tcx", "subagents", "prompt", "--json", request], workspace).stdout)
    web_known_fields = {item["field"]: item["answer"] for item in web_profile_aware["intake_summary"]["investor_profile"]["known_fields"]}
    assert web_known_fields["liquidity or cash needs"] == "no near-term cash need"
    assert web_known_fields["tax, account, or jurisdiction constraints"] == "taxable account, no restricted symbols"
    assert web_profile_aware["intake_summary"]["next_allowed_actions"][0]["label"] == "Answer remaining profile questions"
    assert web_profile_aware["intake_summary"]["next_allowed_actions"][0]["detail"] == "Still missing: current holdings and concentration."

    write_strategy_skill_fixture(workspace, "strategy-quality-compounder")
    active_strategy_payload = json.loads(run(["./tcx", "subagents", "prompt", "--json", request], workspace).stdout)
    assert active_strategy_payload["intake_summary"]["strategy_baseline"]["mode"] == "active_user_strategy"
    assert "strategy-quality-compounder" in active_strategy_payload["intake_summary"]["strategy_baseline"]["summary"]

    idea_request = "TSLA feels interesting, no order"
    idea_explained = run(["./tcx", "subagents", "prompt", "--explain", idea_request], workspace).stdout
    assert "Workflow: Thesis review" in idea_explained
    assert "Idea translated: Thesis review:" in idea_explained
    assert "Working hypothesis: Treat the idea as a thesis check" in idea_explained
    assert "Dispatch thesis roles:" in idea_explained
    assert "Thesis loop:" in idea_explained
    assert "Stop condition: Stop before recommendation or sizing" in idea_explained
    assert "execution-operator" not in idea_explained

    client = Client(REMOTE_ADDR="127.0.0.1")
    response = client.get("/api/harness/subagents/prompt", {"q": request})
    assert response.status_code == 200
    payload = response.json()
    assert payload["intake_summary"]["label"] == "Decision support"
    assert payload["intake_summary"]["plain_language_output"] is True
    assert "Recommendation, sizing, approval" in payload["intake_summary"]["idea_translation"]["safety_boundary"]
    assert payload["intake_summary"]["workflow_stages"][2]["key"] == "valuation"
    assert payload["intake_summary"]["method_lenses"][2]["label"] == "Factor/exposure lens"
    assert "market forces around it" in payload["intake_summary"]["method_lenses"][2]["plain"]
    assert payload["intake_summary"]["blocked_action_details"][2]["label"] == "Execution"
    assert "not an order" in payload["intake_summary"]["next_allowed_actions"][1]["detail"]
    assert [item["field"] for item in payload["intake_summary"]["questions_to_answer"]] == ["current holdings and concentration"]
    assert payload["intake_summary"]["investor_profile"]["completion"] == 0.83
    assert payload["intake_summary"]["next_allowed_actions"][0]["label"] == "Answer remaining profile questions"
    assert "policy constraints" in next(
        agent["why_selected"]
        for agent in payload["intake_summary"]["subagents"]
        if agent["role"] == "risk-manager"
    )
    assert "starter_prompt" not in payload
    assert "prompt" in payload

    run(["./tcx", "profile", "update", "--holdings", "cash 30%, broad equity ETF 40%, single-name ideas under 5%"], workspace)
    complete_profile = json.loads(run(["./tcx", "subagents", "prompt", "--json", request], workspace).stdout)
    assert complete_profile["intake_summary"]["investor_profile_inputs"] == []
    assert complete_profile["intake_summary"]["questions_to_answer"] == []
    assert complete_profile["intake_summary"]["investor_profile"]["completion"] == 1.0
    assert complete_profile["intake_summary"]["next_allowed_actions"][0]["label"] == "Use saved profile context"


def test_artifact_supervisor_loop_contract(monkeypatch, tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    monkeypatch.setenv("TRADINGCODEX_WORKSPACE_ROOT", str(workspace))
    request = "Analyze NVDA. No order, no trading."
    plan = classify_starter_request(request)
    assert plan["loopStatePath"] == ".tradingcodex/mainagent/workflow-loop-state.json"
    assert plan["allowedFollowupTeam"] == plan["subagents"]
    assert "portfolio-manager" in plan["escalationTeam"]
    assert "accepted" in plan["artifactHandoffStates"]
    assert "accepted" not in plan["terminalWorkflowActions"]

    allowed_decision = plan_follow_up_request(plan, {
        "trigger": "material_driver",
        "suggested_role": "valuation-analyst",
        "question": "Assess whether the driver changes scenario assumptions.",
        "reason": "News artifact found a material driver.",
        "materiality": "high",
        "within_current_lane": False,
        "requires_user_consent": True,
    })
    assert allowed_decision["planner_decision"] == "follow_up_existing_team"
    assert allowed_decision["policy_within_current_lane"] is True
    assert allowed_decision["policy_requires_user_consent"] is False

    escalation_decision = plan_follow_up_request(plan, {
        "trigger": "scope_boundary",
        "suggested_role": "portfolio-manager",
        "question": "Check portfolio fit.",
        "reason": "The useful next question is outside thesis review.",
        "materiality": "medium",
    })
    assert escalation_decision["planner_decision"] == "lane_escalation_proposal"
    assert escalation_decision["policy_requires_user_consent"] is True

    hook_context = run_user_prompt_hook(workspace, request)
    assert hook_context
    assert hook_context["loop_contract"]["state_path"].startswith(".tradingcodex/mainagent/workflows/")
    assert hook_context["loop_contract"]["state_path"].endswith("/loop-state.json")
    latest_loop_state = json.loads((workspace / ".tradingcodex" / "mainagent" / "workflow-loop-state.json").read_text(encoding="utf-8"))
    assert latest_loop_state["state_path"] == hook_context["loop_contract"]["state_path"]
    loop_state = json.loads((workspace / hook_context["loop_contract"]["state_path"]).read_text(encoding="utf-8"))
    assert loop_state["workflow_run_id"] == hook_context["workflow_run_id"]
    assert loop_state["state_path"] == hook_context["loop_contract"]["state_path"]
    assert loop_state["selected_team"] == hook_context["required_subagents"]
    assert loop_state["allowed_followup_team"] == hook_context["required_subagents"]
    assert loop_state["state_mode"] == "inspectable_assisted_loop"
    assert loop_state["auto_spawn"] is False
    assert loop_state["pending_tasks"][0]["status"] == "pending"

    plan_cli = json.loads(run(["./tcx", "subagents", "plan", request], workspace).stdout)
    assert plan_cli["workflow_lane"] == "thesis_review"
    assert plan_cli["initial_dispatch"] == hook_context["required_subagents"]
    assert plan_cli["allowed_followup_team"] == hook_context["required_subagents"]
    assert plan_cli["workflow_loop_state_path"] == ".tradingcodex/mainagent/workflow-loop-state.json"
    assert plan_cli["pending_tasks"]

    thread_a = run_user_prompt_hook(workspace, "Analyze MSFT. No order, no trading.", {"session_id": "thread-a"})
    thread_b = run_user_prompt_hook(workspace, "Analyze AAPL. No order, no trading.", {"session_id": "thread-b"})
    assert thread_a and thread_b
    thread_a_path = thread_a["loop_contract"]["state_path"]
    thread_b_path = thread_b["loop_contract"]["state_path"]
    assert thread_a_path != thread_b_path
    assert (workspace / thread_a_path).exists()
    assert (workspace / thread_b_path).exists()
    run(
        [sys.executable, str(workspace / ".codex" / "hooks" / "tradingcodex_hook.py"), "subagent-start"],
        workspace,
        input_text=json.dumps({"session_id": "thread-a", "agent_type": "fundamental-analyst", "task_name": "fundamental thread a"}),
    )
    thread_a_state = json.loads((workspace / thread_a_path).read_text(encoding="utf-8"))
    thread_b_state = json.loads((workspace / thread_b_path).read_text(encoding="utf-8"))
    assert thread_a_state["pending_tasks"][0]["status"] == "active"
    assert thread_b_state["pending_tasks"][0]["status"] == "pending"
    session_state = json.loads((workspace / ".tradingcodex" / "mainagent" / "subagent-session-state.json").read_text(encoding="utf-8"))
    assert any(record["run_id"] == thread_a["workflow_run_id"] for record in session_state["active"].values())

    artifact = workspace / "trading" / "reports" / "news" / "nvda-news.md"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text(
        """---
artifact_id: nvda-news
artifact_type: news_report
role: news-analyst
title: NVDA news
source_as_of: "2026-06-30"
readiness_label: ready-for-valuation
context_summary: News found a material driver.
reader_summary: Driver may affect valuation assumptions.
next_action: Ask valuation to assess scenario assumptions.
handoff_state: accepted
confidence: medium
next_recipient: valuation-analyst
missing_evidence: []
blocked_actions: [order_execution]
source_snapshot_ids: [news-source-001]
follow_up_requests:
  - trigger: material_driver
    suggested_role: valuation-analyst
    question: Assess whether the material driver changes existing scenario assumptions.
    reason: News artifact found a material driver not yet reflected in valuation work.
    materiality: high
    requested_by_role: news-analyst
    source_artifact_id: nvda-news
    source_artifact_path: trading/reports/news/nvda-news.md
    source_artifact_version: 1
    source_artifact_content_hash: sha256:unit
    trigger_evidence_refs:
      - source_snapshot_id: news-source-001
    required_inputs: [accepted news artifact]
    suggested_consent_posture: no_consent_expected
    blocked_actions: [order_execution]
---

[factual] News source posture is recorded.
""",
        encoding="utf-8",
    )
    quality = evaluate_artifact_quality(workspace, "trading/reports/news/nvda-news.md", strict=True)
    assert quality["status"] == "pass"
    assert quality["frontmatter"]["follow_up_requests"][0]["suggested_role"] == "valuation-analyst"

    loop_preview = evaluate_artifact_supervisor_loop(workspace, request, ["trading/reports/news/nvda-news.md"])
    assert loop_preview["pending_tasks"][0]["role"] == "valuation-analyst"
    assert loop_preview["pending_tasks"][0]["planner_action"] == "follow_up_existing_team"
    assert loop_preview["terminal_action"] == "waiting"
    assert loop_preview["auto_spawn"] is False
    assert loop_preview["recursive_hook_dispatch"] is False

    cli_loop = json.loads(run(["./tcx", "subagents", "loop", "--request", request, "--artifact", "trading/reports/news/nvda-news.md"], workspace).stdout)
    assert cli_loop["pending_tasks"][0]["task_type"] == "artifact_follow_up"

    client = Client(REMOTE_ADDR="127.0.0.1")
    response = client.post(
        "/api/harness/subagents/loop",
        data=json.dumps({"original_request": request, "artifact_paths": ["trading/reports/news/nvda-news.md"], "record": False}),
        content_type="application/json",
    )
    assert response.status_code == 200
    assert response.json()["pending_tasks"][0]["role"] == "valuation-analyst"

    recorded_loop = evaluate_artifact_supervisor_loop(workspace, request, ["trading/reports/news/nvda-news.md"], record=True)
    recorded_state = json.loads((workspace / ".tradingcodex" / "mainagent" / "workflow-loop-state.json").read_text(encoding="utf-8"))
    assert recorded_loop["pending_tasks"]
    assert any(task.get("task_type") == "artifact_follow_up" for task in recorded_state["pending_tasks"])
    assert recorded_state["auto_spawn"] is False

    negated_loop = evaluate_artifact_supervisor_loop(workspace, "NVDA news only. No valuation, no order, no trading.", ["trading/reports/news/nvda-news.md"])
    assert negated_loop["terminal_action"] == "blocked"
    assert any(decision["planner_action"] == "blocked" for decision in negated_loop["loop_decisions"])

    challenge_artifact = workspace / "trading" / "reports" / "news" / "challenge-news.md"
    challenge_artifact.write_text(
        artifact.read_text(encoding="utf-8")
        .replace("artifact_id: nvda-news", "artifact_id: challenge-news")
        .replace("trigger: material_driver", "trigger: contradiction"),
        encoding="utf-8",
    )
    challenge_loop = evaluate_artifact_supervisor_loop(workspace, request, ["trading/reports/news/challenge-news.md"])
    assert challenge_loop["pending_tasks"][0]["planner_action"] == "challenge_conflict"
    assert challenge_loop["pending_tasks"][0]["role"] == "valuation-analyst"

    blocked_artifact = workspace / "trading" / "reports" / "news" / "blocked-news.md"
    blocked_artifact.write_text(
        artifact.read_text(encoding="utf-8").replace("artifact_id: nvda-news", "artifact_id: blocked-news").replace("handoff_state: accepted", "handoff_state: blocked"),
        encoding="utf-8",
    )
    blocked_loop = evaluate_artifact_supervisor_loop(workspace, request, ["trading/reports/news/blocked-news.md"])
    assert blocked_loop["terminal_action"] == "blocked"
    assert "order_execution" in blocked_loop["blocked_actions"]

    (workspace / ".tradingcodex" / "mainagent" / "workflow-loop-state.json").write_text(
        json.dumps(
            {
                "workflow_run_id": "budget-unit",
                "lane": "thesis_review",
                "loop_policy": {"max_loop_subagent_tasks": 0, "max_followups_per_iteration": 1},
                "selected_team": hook_context["required_subagents"],
                "allowed_followup_team": hook_context["required_subagents"],
                "escalation_team": ["portfolio-manager", "risk-manager"],
                "pending_tasks": [],
                "blocked_actions": ["order ticket", "approval", "execution"],
            }
        ),
        encoding="utf-8",
    )
    budget_loop = evaluate_artifact_supervisor_loop(workspace, "", ["trading/reports/news/nvda-news.md"])
    assert budget_loop["terminal_action"] == "waiting"
    assert budget_loop["pending_tasks"] == []
    assert any((decision.get("detail") or {}).get("budget_exhausted") for decision in budget_loop["loop_decisions"])

    revise_artifact = workspace / "trading" / "reports" / "news" / "revise-news.md"
    revise_artifact.write_text(
        artifact.read_text(encoding="utf-8").replace("artifact_id: nvda-news", "artifact_id: revise-news").replace("handoff_state: accepted", "handoff_state: revise"),
        encoding="utf-8",
    )
    revise_loop = evaluate_artifact_supervisor_loop(workspace, request, ["trading/reports/news/revise-news.md"])
    assert revise_loop["pending_tasks"][0]["planner_action"] == "revise_same_role"
    assert revise_loop["pending_tasks"][0]["role"] == "news-analyst"

    bad_artifact = workspace / "trading" / "reports" / "news" / "bad-followup.md"
    bad_artifact.write_text(
        artifact.read_text(encoding="utf-8").replace(
            "suggested_consent_posture: no_consent_expected",
            "requires_user_consent: false",
        ),
        encoding="utf-8",
    )
    bad_quality = evaluate_artifact_quality(workspace, "trading/reports/news/bad-followup.md", strict=True)
    assert bad_quality["status"] == "fail"
    assert "follow_up_requests" in bad_quality["required_fields_missing"]


def test_workspace_cli_order_policy_and_execution(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    order_id = "smoke-order-2"
    created = json.loads(run(["./tcx", "mcp", "call", "create_order_ticket", "--principal", "portfolio-manager", "--ticket-id", order_id, "--symbol", "AAPL", "--side", "buy", "--quantity", "1", "--limit-price", "1000"], workspace).stdout)
    assert created["ticket"]["ticket_id"] == order_id
    assert created["ticket"]["canonical_order"]["instrument"]["symbol"] == "AAPL"
    assert json.loads(run(["./tcx", "validate", "order", order_id], workspace).stdout)["approval_ready"] is True
    approval = json.loads(run(["./tcx", "approve", order_id, "--approved-by", "risk-manager"], workspace).stdout)
    assert approval["status"] == "approved"
    execution = json.loads(run(["./tcx", "mcp", "call", "submit_approved_order", "--ticket-id", order_id], workspace).stdout)
    assert execution["status"] == "accepted"
    assert execution["db_canonical"] is True
    assert execution["idempotency_key"].startswith("submit:")
    assert execution["result"]["portfolio_id"] == "default-paper"
    broker_order_id = execution["result"]["broker_order_id"]
    duplicate = json.loads(run(["./tcx", "mcp", "call", "submit_approved_order", "--ticket-id", order_id], workspace, expect_ok=False).stdout)
    assert duplicate["status"] == "rejected"
    assert "already has an execution result" in "\n".join(duplicate["reasons"])
    snapshot = json.loads(run(["./tcx", "mcp", "call", "get_portfolio_snapshot"], workspace).stdout)
    assert snapshot["positions"]["AAPL"]["quantity"] == 1.0
    default_order_list = json.loads(run(["./tcx", "mcp", "call", "list_order_tickets"], workspace).stdout)
    assert default_order_list["portfolio_id"] == "default-paper"
    assert any(ticket["ticket_id"] == order_id for ticket in default_order_list["tickets"])
    mutable_order_id = "mutable-profile-isolation-order"
    mutable_created = json.loads(run(["./tcx", "mcp", "call", "create_order_ticket", "--principal", "portfolio-manager", "--ticket-id", mutable_order_id, "--symbol", "MSFT", "--side", "buy", "--quantity", "1", "--limit-price", "1000"], workspace).stdout)
    assert mutable_created["status"] == "created"

    created_profile = json.loads(run(["./tcx", "profile", "create", "strategy-lab"], workspace).stdout)
    assert created_profile["profile"]["portfolio_id"] == "strategy-lab"
    selected_profile = json.loads(run(["./tcx", "profile", "select", "strategy-lab"], workspace).stdout)
    assert selected_profile["active_profile"]["portfolio_id"] == "strategy-lab"
    isolated_snapshot = json.loads(run(["./tcx", "mcp", "call", "get_portfolio_snapshot"], workspace).stdout)
    assert isolated_snapshot["portfolio_id"] == "strategy-lab"
    assert isolated_snapshot["positions"] == {}
    isolated_order_list = json.loads(run(["./tcx", "mcp", "call", "list_order_tickets"], workspace).stdout)
    assert isolated_order_list["portfolio_id"] == "strategy-lab"
    assert not any(ticket["ticket_id"] == order_id for ticket in isolated_order_list["tickets"])
    wrong_profile_duplicate = run(["./tcx", "mcp", "call", "create_order_ticket", "--principal", "portfolio-manager", "--ticket-id", mutable_order_id, "--symbol", "MSFT", "--side", "buy", "--quantity", "2", "--limit-price", "1000"], workspace, expect_ok=False)
    assert "order ticket id already exists for another active profile" in wrong_profile_duplicate.stderr
    wrong_profile_read = run(["./tcx", "mcp", "call", "get_order_ticket", "--ticket-id", order_id], workspace, expect_ok=False)
    assert "unknown order ticket for active profile" in wrong_profile_read.stderr
    wrong_profile_checks = run(["./tcx", "mcp", "call", "run_order_checks", "--principal", "portfolio-manager", "--ticket-id", order_id], workspace, expect_ok=False)
    assert "unknown order ticket for active profile" in wrong_profile_checks.stderr
    wrong_profile_submit = run(["./tcx", "mcp", "call", "submit_approved_order", "--ticket-id", order_id], workspace, expect_ok=False)
    wrong_profile_submit_payload = json.loads(wrong_profile_submit.stdout)
    assert wrong_profile_submit_payload["status"] == "rejected"
    assert "unknown order ticket for active profile" in "\n".join(wrong_profile_submit_payload["reasons"])
    wrong_profile_status_by_broker = json.loads(run(["./tcx", "mcp", "call", "get_order_status", "--order-id", broker_order_id], workspace).stdout)
    assert wrong_profile_status_by_broker["status"] == "unknown"
    assert "no local order ticket or broker order matched" in "\n".join(wrong_profile_status_by_broker["reasons"])
    wrong_profile_refresh = run(["./tcx", "mcp", "call", "refresh_broker_order_status", json.dumps({"broker_order_id": broker_order_id})], workspace, expect_ok=False)
    assert "ticket_id or known broker_order_id is required" in wrong_profile_refresh.stderr
    wrong_profile_cancel = run(["./tcx", "mcp", "call", "cancel_approved_order", "--order-id", broker_order_id], workspace, expect_ok=False)
    assert "ticket_id or known broker_order_id is required" in wrong_profile_cancel.stderr
    run(["./tcx", "profile", "select", "default"], workspace)
    default_profile_status_by_broker = json.loads(run(["./tcx", "mcp", "call", "get_order_status", "--order-id", broker_order_id], workspace).stdout)
    assert default_profile_status_by_broker["status"] == "filled"
    assert default_profile_status_by_broker["ticket_id"] == order_id
    from tradingcodex_service.web import portfolio_overview

    web_portfolio = portfolio_overview(workspace)
    assert web_portfolio["portfolio_id"] == "default-paper"
    assert web_portfolio["positions"]


def test_web_portfolio_overview_reports_load_warnings(monkeypatch) -> None:
    from tradingcodex_service import web as web_module

    def broken_positions(root):
        raise RuntimeError("metadata unavailable")

    monkeypatch.setattr(web_module, "list_positions", broken_positions)
    overview = web_module.portfolio_overview(ROOT)
    assert overview["positions"] == []
    assert overview["warnings"] == ["Portfolio state could not be loaded: metadata unavailable"]


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
    result = validate_order_ticket_payload(workspace, {"principal_id": "portfolio-manager", "order": blocked})
    assert result["valid"] is False
    assert "symbol is restricted: BLOCKED" in "\n".join(result["reasons"])
    live = {**blocked, "id": "live", "symbol": "TSLA", "broker": "live"}
    live_result = validate_order_ticket_payload(workspace, {"principal_id": "portfolio-manager", "order": live})
    assert live_result["valid"] is False
    assert "adapter not enabled: live" in "\n".join(live_result["reasons"])
    self_approval = call_mcp_tool(workspace, "simulate_policy", {
        "principal_id": "head-manager",
        "action": "approval.self_issue",
        "resource": "*",
    })
    assert self_approval["decision"] == "deny"
    approval_create = call_mcp_tool(workspace, "simulate_policy", {
        "principal_id": "head-manager",
        "action": "approval_receipt.create",
        "resource": "*",
    })
    assert approval_create["decision"] == "deny"
    assert "only risk-manager can create approval receipts" in "\n".join(approval_create["reasons"])
    direct_broker = call_mcp_tool(workspace, "simulate_policy", {
        "principal_id": "head-manager",
        "action": "broker_api.call_direct",
        "resource": "live_broker_api",
    })
    assert direct_broker["decision"] == "deny"
    live_submit = call_mcp_tool(workspace, "simulate_policy", {
        "principal_id": "head-manager",
        "action": "execution.submit_live_order",
        "resource": "live_broker_adapter",
    })
    assert live_submit["decision"] == "deny"
    execute_order = call_mcp_tool(workspace, "simulate_policy", {
        "principal_id": "head-manager",
        "action": "execute_order",
        "resource": "TSLA",
    })
    assert execute_order["decision"] == "deny"


def test_policy_config_parse_failures_fail_closed(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    order = {
        "id": "policy-invalid",
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

    (workspace / ".tradingcodex" / "policies" / "access-policies.yaml").write_text("allow: [\n", encoding="utf-8")
    access_result = validate_order_ticket_payload(workspace, {"principal_id": "portfolio-manager", "order": order})
    assert access_result["valid"] is False
    assert "runtime policy invalid" in "\n".join(access_result["reasons"])

    workspace = make_workspace(tmp_path / "restricted")
    (workspace / ".tradingcodex" / "policies" / "restricted-list.yaml").write_text("restricted_symbols: [\n", encoding="utf-8")
    restricted_result = validate_order_ticket_payload(workspace, {"principal_id": "portfolio-manager", "order": order})
    assert restricted_result["valid"] is False
    assert "restricted-list policy invalid" in "\n".join(restricted_result["reasons"])


def test_workspace_path_inputs_are_contained(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    outside_markdown = tmp_path / "outside.md"
    outside_markdown.write_text("# Outside\n", encoding="utf-8")

    from tradingcodex_service.application.research import create_research_artifact

    with pytest.raises(ValueError, match="relative"):
        create_research_artifact(workspace, {"artifact_id": "abs-read", "markdown_path": str(outside_markdown)})
    with pytest.raises(ValueError, match="relative"):
        create_research_artifact(workspace, {"artifact_id": "drive-read", "markdown_path": "C:/outside.md"})
    with pytest.raises(ValueError, match="forward-slash"):
        create_research_artifact(workspace, {"artifact_id": "backslash-read", "markdown_path": r"trading\research\..\outside.md"})

    with pytest.raises(ValueError, match="must stay under"):
        create_research_artifact(workspace, {"artifact_id": "root-read", "markdown_path": "AGENTS.md"})

    with pytest.raises(ValueError, match="must not contain"):
        create_research_artifact(workspace, {"artifact_id": "dotdot-write", "markdown": "# Safe\n", "export_path": "trading/research/../outside.md"})

    symlink_path = workspace / "trading" / "research" / "outside-link.md"
    try:
        symlink_path.symlink_to(outside_markdown)
    except OSError:
        symlink_path = None
    if symlink_path is not None:
        with pytest.raises(ValueError, match="escapes"):
            create_research_artifact(workspace, {"artifact_id": "symlink-read", "markdown_path": "trading/research/outside-link.md"})

def test_mcp_runtime_rejects_schema_type_and_extra_fields(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    extra = handle_mcp_rpc(workspace, {
        "jsonrpc": "2.0",
        "id": 20,
        "method": "tools/call",
        "params": {
            "name": "run_order_checks",
            "arguments": {"principal_id": "portfolio-manager", "ticket_id": "schema-order", "unexpected": "x"},
        },
    })
    assert extra and "additional properties" in extra["error"]["message"]

    wrong_type = handle_mcp_rpc(workspace, {
        "jsonrpc": "2.0",
        "id": 21,
        "method": "tools/call",
        "params": {
            "name": "create_order_ticket",
            "arguments": {"principal_id": "portfolio-manager", "symbol": "AAPL", "side": "buy", "quantity": "1", "limit_price": 1000},
        },
    })
    assert wrong_type and "quantity must be number" in wrong_type["error"]["message"]


def test_web_workspace_open_and_create_are_separate(tmp_path: Path) -> None:
    client = Client(REMOTE_ADDR="127.0.0.1")
    non_workspace = tmp_path / "not-workspace"
    non_workspace.mkdir()
    (non_workspace / "existing.txt").write_text("keep me\n", encoding="utf-8")

    open_response = client.post("/workspaces/open/", {"workspace_path": str(non_workspace), "next": "/research/"})
    assert open_response.status_code == 302
    assert not (non_workspace / ".tradingcodex" / "workspace.json").exists()

    create_non_empty_response = client.post("/workspaces/create/", {"workspace_path": str(non_workspace), "next": "/research/"})
    assert create_non_empty_response.status_code == 302
    assert not (non_workspace / ".tradingcodex" / "workspace.json").exists()

    empty_workspace = tmp_path / "empty-workspace"
    empty_workspace.mkdir()
    create_response = client.post("/workspaces/create/", {"workspace_path": str(empty_workspace), "next": "/research/"})
    assert create_response.status_code == 302
    assert (empty_workspace / ".tradingcodex" / "workspace.json").exists()


def test_capabilities_are_enforced_before_mcp_and_policy(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    ensure_runtime_database(workspace)
    from apps.policy.models import Capability, Principal
    from apps.policy.services import sync_builtin_principals_and_capabilities

    sync_builtin_principals_and_capabilities()
    capability = Capability.objects.get(principal__principal_id="fundamental-analyst", action="research_artifact.write")
    capability.effect = "deny"
    capability.save(update_fields=["effect"])
    forbidden = handle_mcp_rpc(workspace, {
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
    inactive = handle_mcp_rpc(workspace, {
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

    Capability.objects.filter(principal__principal_id="portfolio-manager", action="order_ticket.check").update(effect="deny")
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
    result = validate_order_ticket_payload(workspace, {"principal_id": "portfolio-manager", "order": order})
    assert result["valid"] is False
    assert "capability denied" in "\n".join(result["reasons"])


def test_mcp_stdio_minimum_surface(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    initialized = handle_mcp_rpc(workspace, {"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert initialized and initialized["result"]["serverInfo"]["name"] == "tradingcodex"
    tools = handle_mcp_rpc(workspace, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    assert tools and any(tool["name"] == "submit_approved_order" for tool in tools["result"]["tools"])
    assert any(tool["name"] == "create_research_artifact" for tool in tools["result"]["tools"])
    for tool in tools["result"]["tools"]:
        annotations = tool["annotations"]
        assert isinstance(annotations["title"], str)
        assert isinstance(annotations["readOnlyHint"], bool)
        assert isinstance(annotations["destructiveHint"], bool)
        assert isinstance(annotations["idempotentHint"], bool)
        assert isinstance(annotations["openWorldHint"], bool)
        assert isinstance(annotations["category"], str)
        assert isinstance(annotations["risk_level"], str)
        assert isinstance(annotations["allowed_roles"], list)
        assert isinstance(annotations["requires_approval"], bool)
        assert isinstance(annotations["audit_required"], bool)
    submit_tool = next(tool for tool in tools["result"]["tools"] if tool["name"] == "submit_approved_order")
    assert submit_tool["annotations"]["risk_level"] == "execution"
    assert submit_tool["annotations"]["allowed_roles"] == ["execution-operator"]
    assert submit_tool["annotations"]["audit_required"] is True
    assert submit_tool["annotations"]["experimental"] is True
    assert submit_tool["annotations"]["readOnlyHint"] is False
    assert submit_tool["annotations"]["destructiveHint"] is True
    assert submit_tool["annotations"]["openWorldHint"] is True
    status_tool = next(tool for tool in tools["result"]["tools"] if tool["name"] == "get_tradingcodex_status")
    assert status_tool["annotations"]["audit_required"] is True
    assert status_tool["annotations"]["readOnlyHint"] is True
    assert status_tool["annotations"]["destructiveHint"] is False
    research_tool = next(tool for tool in tools["result"]["tools"] if tool["name"] == "create_research_artifact")
    assert "context_summary" in research_tool["inputSchema"]["properties"]
    create_ticket_tool = next(tool for tool in tools["result"]["tools"] if tool["name"] == "create_order_ticket")
    assert create_ticket_tool["annotations"]["allowed_roles"] == ["portfolio-manager"]
    constraints_tool = next(tool for tool in tools["result"]["tools"] if tool["name"] == "get_broker_instrument_constraints")
    assert "instrument-analyst" in constraints_tool["annotations"]["allowed_roles"]
    tool_names = {tool["name"] for tool in tools["result"]["tools"]}
    assert {
        "list_broker_adapter_providers",
        "scaffold_broker_connector",
        "register_broker_connector",
        "validate_broker_connector_build",
        "get_broker_capability_profile",
        "get_broker_instrument_constraints",
        "preview_order_translation",
    }.issubset(tool_names)
    assert {
        "alpaca_place_order",
        "ibkr_submit_order",
        "binance_new_order",
        "upbit_order",
        "kis_order_cash",
    }.isdisjoint(tool_names)
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


def test_head_manager_connector_tools_stop_before_execution(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    providers = call_mcp_tool(workspace, "list_broker_adapter_providers", {"principal_id": "head-manager"})
    assert {provider["provider_id"] for provider in providers["providers"]} == {"paper"}

    scaffold = call_mcp_tool(
        workspace,
        "scaffold_broker_connector",
        {
            "principal_id": "head-manager",
            "provider": "binance",
            "broker_id": "binance-preview",
            "credential_ref": "env:BINANCE_READONLY",
        },
    )
    assert scaffold["provider_development_required"] is True
    assert scaffold["live_order_enabled"] is False
    validated = call_mcp_tool(workspace, "validate_broker_connector_build", {"principal_id": "head-manager", "broker_id": "binance-preview"})
    assert validated["status"] == "blocked"
    assert validated["registered"] is False

    with pytest.raises(ValueError):
        call_mcp_tool(
            workspace,
            "register_broker_connector",
            {
                "principal_id": "head-manager",
                "provider": "binance",
                "broker_id": "binance-preview",
                "credential_ref": "env:BINANCE_READONLY",
            },
        )

    registered = call_mcp_tool(
        workspace,
        "register_broker_connector",
        {"principal_id": "head-manager", "provider": "paper", "broker_id": "paper-preview", "environment": "paper"},
    )
    assert registered["connection"]["broker_id"] == "paper-preview"
    assert registered["connection"]["enabled_trade_scopes"] == []
    assert registered["connection"]["metadata"]["execution_enabled"] is False

    preview = call_mcp_tool(
        workspace,
        "preview_order_translation",
        {
            "principal_id": "head-manager",
            "broker_id": "paper-preview",
            "symbol": "AAPL",
            "side": "buy",
            "quantity": 1,
            "order_type": "limit",
            "limit_price": 100,
            "time_in_force": "day",
        },
    )
    assert preview["valid"] is True
    assert preview["payload"]["adapter"] == "paper-trading"

    with pytest.raises(PermissionError):
        call_mcp_tool(workspace, "register_broker_connector", {"principal_id": "execution-operator", "provider": "paper"})
    with pytest.raises(PermissionError):
        call_mcp_tool(workspace, "scaffold_broker_connector", {"principal_id": "execution-operator", "provider": "paper"})
    with pytest.raises(PermissionError):
        call_mcp_tool(workspace, "submit_approved_order", {"principal_id": "head-manager", "ticket_id": "not-allowed"})
    with pytest.raises(PermissionError):
        call_mcp_tool(workspace, "cancel_approved_order", {"principal_id": "head-manager", "order_id": "not-allowed"})


def test_order_translation_profiles_cover_multi_asset_shapes(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    register_broker_adapter_provider(
        BrokerAdapterProvider(
            provider_id="shape-provider",
            display_name="Shape Provider",
            family="shape-test",
            venue="broker",
            region="test",
            asset_classes=("equity", "option", "future", "crypto", "forex"),
            products=("spot", "option_multileg", "future", "forex"),
            default_environment="sandbox",
            auth_model={"type": "credential_ref", "credential_ref_required": True},
            account_model={"multi_account": True, "balances": "cash", "positions": True},
            instrument_model={"identity": "symbol", "examples": []},
            order_model={"sides": ["buy", "sell"], "order_types": ["market", "limit"], "time_in_force": ["day", "GTC", "FOK", "ioc"], "quantity_modes": ["quantity", "contracts", "units", "quote_notional"]},
            validation_model={"preview": True, "dry_run": True},
            event_model={"polling": True, "streaming": False},
            execution_posture="broker_validation_only",
            adapter_type="shape-provider",
            live=False,
        )
    )
    call_mcp_tool(
        workspace,
        "register_broker_connector",
        {"principal_id": "head-manager", "provider": "shape-provider", "broker_id": "shape", "credential_ref": "env:SHAPE_READONLY"},
    )

    equity = call_mcp_tool(
        workspace,
        "preview_order_translation",
        {"principal_id": "head-manager", "broker_id": "shape", "symbol": "AAPL", "side": "buy", "quantity": 1, "order_type": "limit", "limit_price": 180, "time_in_force": "day"},
    )
    assert equity["translation"]["canonical_order"]["asset_class"] == "equity"

    option_multileg = call_mcp_tool(
        workspace,
        "preview_order_translation",
        {
            "principal_id": "head-manager",
            "broker_id": "shape",
            "symbol": "AAPL",
            "asset_class": "option",
            "product_type": "option_multileg",
            "side": "buy",
            "quantity": 1,
            "order_type": "limit",
            "limit_price": 1.25,
            "time_in_force": "day",
            "legs": [
                {"symbol": ".AAPL260117C180", "side": "buy", "quantity": 1},
                {"symbol": ".AAPL260117C190", "side": "sell", "quantity": 1},
            ],
        },
    )
    assert len(option_multileg["translation"]["canonical_order"]["legs"]) == 2

    future = call_mcp_tool(
        workspace,
        "preview_order_translation",
        {
            "principal_id": "head-manager",
            "broker_id": "shape",
            "symbol": "/ESZ6",
            "asset_class": "future",
            "product_type": "future",
            "side": "buy",
            "quantity": 1,
            "quantity_mode": "contracts",
            "order_type": "limit",
            "limit_price": 5500,
            "time_in_force": "day",
        },
    )
    assert future["translation"]["canonical_order"]["product_type"] == "future"
    assert future["translation"]["canonical_order"]["quantity_mode"] == "contracts"

    crypto = call_mcp_tool(
        workspace,
        "preview_order_translation",
        {"principal_id": "head-manager", "broker_id": "shape", "symbol": "BTCUSDT", "asset_class": "crypto", "side": "buy", "order_type": "market", "quote_notional": 25, "time_in_force": "GTC"},
    )
    assert crypto["translation"]["canonical_order"]["quantity_mode"] == "quote_notional"

    fx = call_mcp_tool(
        workspace,
        "preview_order_translation",
        {"principal_id": "head-manager", "broker_id": "shape", "instrument": "EUR_USD", "asset_class": "forex", "side": "buy", "quantity_mode": "units", "quantity": 1000, "order_type": "market", "time_in_force": "FOK"},
    )
    assert fx["translation"]["canonical_order"]["asset_class"] == "forex"
    assert fx["translation"]["canonical_order"]["quantity_mode"] == "units"


def test_global_home_mcp_safe_config_excludes_sensitive_tools(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    config_path = tmp_path / "codex-config.toml"

    installed = json.loads(run(["./tcx", "mcp", "install-global", "--safe", "--config", str(config_path)], workspace).stdout)
    config = config_path.read_text(encoding="utf-8")

    assert installed["server_name"] == "tradingcodex-home"
    assert "mcp_servers.tradingcodex-home" in config
    assert "TRADINGCODEX_MCP_SAFE_TOOLS" in config
    assert "submit_approved_order" not in installed["safe_tools"]
    assert "request_order_approval" not in installed["safe_tools"]
    assert "cancel_approved_order" not in installed["safe_tools"]
    assert set(installed["safe_tools"]) == set(SAFE_HOME_TOOL_NAMES)

    previous = os.environ.get("TRADINGCODEX_MCP_SAFE_TOOLS")
    os.environ["TRADINGCODEX_MCP_SAFE_TOOLS"] = "1"
    try:
        initialized = handle_mcp_rpc(workspace, {"jsonrpc": "2.0", "id": 1, "method": "initialize"})
        tools = handle_mcp_rpc(workspace, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        tool_names = {tool["name"] for tool in tools["result"]["tools"]}
        forbidden = handle_mcp_rpc(workspace, {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "submit_approved_order", "arguments": {}},
        })
    finally:
        if previous is None:
            os.environ.pop("TRADINGCODEX_MCP_SAFE_TOOLS", None)
        else:
            os.environ["TRADINGCODEX_MCP_SAFE_TOOLS"] = previous

    assert initialized and initialized["result"]["serverInfo"]["name"] == "tradingcodex-home"
    assert tool_names == set(SAFE_HOME_TOOL_NAMES)
    assert forbidden and "safe scope" in forbidden["error"]["message"]


def test_django_ninja_control_api(monkeypatch) -> None:
    monkeypatch.setenv("TRADINGCODEX_WORKSPACE_ROOT", str(ROOT))
    ensure_runtime_database(ROOT)
    from apps.audit.models import AuditEvent

    current_context = workspace_context_payload(ROOT)
    other_context = workspace_context_payload(ROOT / ".other-api-workspace")
    AuditEvent.objects.create(action="api.audit_events.old", actor_principal="api-test", source="test", workspace_context=current_context)
    AuditEvent.objects.create(action="api.audit_events.new", actor_principal="api-test", source="test", workspace_context=current_context)
    AuditEvent.objects.create(action="api.audit_events.other_workspace", actor_principal="api-test", source="test", workspace_context=other_context)

    client = Client(REMOTE_ADDR="127.0.0.1")
    assert client.get("/api/health").json()["status"] == "ok"
    status = client.get("/api/harness/status").json()
    assert status["expected_count"] == 9
    assert status["skills_installed"] == 23
    assert status["core_skills_installed"] == 23
    assert status["optional_skills_active"] >= 0
    assert status["user_visible_skills"] == ["tcx-workflow", "tcx-server", "tcx-build", "strategy-creator", "postmortem"]
    assert status["components_total"] == len(list_harness_components())
    assert status["component_tag_counts"]["guardrail"] > 0
    assert client.get("/api/harness/skills").json()["skills"] == status["user_visible_skills"]
    assert len(client.get("/api/harness/skills?include_internal=true").json()["skills"]) == 23
    components = client.get("/api/harness/components").json()
    assert {component["id"] for component in components["components"]} == {component["id"] for component in list_harness_components()}
    component = client.get("/api/harness/components/investment-request-routing")
    assert component.status_code == 200
    assert component.json()["surfaces"]["hooks"] == ["UserPromptSubmit"]
    assert client.get("/api/harness/components/not-real").status_code == 404
    assert len(client.get("/api/subagents").json()) == 9
    assert "portfolio-review" in client.get("/api/subagents/portfolio-manager/skills").json()["skills"]
    assert client.get("/api/harness/subagents").status_code == 404
    assert client.get("/api/harness/subagents/portfolio-manager/skills").status_code == 404
    assert client.post("/api/orders/approvals", data="{}", content_type="application/json").status_code == 404
    assert client.post("/api/orders/executions/submit-approved", data="{}", content_type="application/json").status_code == 404
    audit_events = client.get("/api/audit/events").json()
    api_test_actions = [event["action"] for event in audit_events if event["actor_principal"] == "api-test"]
    assert api_test_actions[:2] == ["api.audit_events.new", "api.audit_events.old"]
    assert "api.audit_events.other_workspace" not in api_test_actions
    response = client.post(
        "/api/policy/simulate",
        data=json.dumps({"principal_id": "execution-operator", "action": "mcp.tradingcodex.submit_approved_order"}),
        content_type="application/json",
    )
    assert response.status_code == 200
    assert response.json()["decision"] in {"allow", "deny"}


def test_default_django_admin() -> None:
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
    login_body = login_response.content.decode()
    assert "Django administration" in login_body
    assert "tcx Control Plane" not in login_body
    assert "tradingcodex_admin/admin.css" not in login_body

    client = Client(REMOTE_ADDR="127.0.0.1")
    client.force_login(user)
    response = client.get("/admin/")
    body = response.content.decode()
    assert response.status_code == 200
    assert "Site administration" in body
    assert "Django administration" in body
    assert "What do you want to check?" not in body
    assert "Open research memory" not in body
    assert "research_researchartifact" not in body
    assert "Check current state" not in body
    assert "Review drafts and approvals" not in body
    assert "Review restrictions and blocks" not in body
    assert "Start with this flow" not in body
    assert "Advanced admin tables" not in body
    assert "Central investment ledger connected" not in body
    assert "tcx Home" not in body
    assert "Capabilities" in body
    assert "Capabilitys" not in body
    assert "MCP tool definitions" in body
    assert "Workspace contexts" in body
    assert "tradingcodex_admin/admin.css" not in body

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


def test_external_mcp_router_classifies_tools_and_gates_proxy() -> None:
    ensure_runtime_database(ROOT)
    from apps.mcp.models import McpExternalTool, McpRouter
    from apps.mcp.services import (
        create_or_update_router,
        evaluate_external_mcp_proxy_call,
        import_external_mcp_discovery,
        set_external_tool_policy,
    )

    McpRouter.objects.filter(name="test-broker-mcp").delete()
    router = create_or_update_router(name="test-broker-mcp", label="Test Broker MCP", transport="http", url="https://broker.test/mcp", enabled=True, actor="test")
    imported = import_external_mcp_discovery(
        router,
        {
            "tools": [
                {"name": "get_market_quote", "description": "Get market data quote", "inputSchema": {"type": "object", "properties": {"symbol": {"type": "string"}}}},
                {"name": "get_positions", "description": "Read account positions", "inputSchema": {"type": "object"}},
                {"name": "place_order", "description": "Submit broker order", "inputSchema": {"type": "object"}},
            ]
        },
        actor="test",
    )
    assert imported["imported"] == 3
    assert imported["router"] == "test-broker-mcp"
    quote = McpExternalTool.objects.get(router=router, external_name="get_market_quote")
    positions = McpExternalTool.objects.get(router=router, external_name="get_positions")
    order = McpExternalTool.objects.get(router=router, external_name="place_order")
    assert quote.category == "market_data"
    assert quote.proxy_mode == "read_only"
    assert positions.category == "account_read"
    assert positions.proxy_mode == "summary_only"
    assert order.category == "execution"
    assert order.enabled is False

    set_external_tool_policy(quote, enabled=True, review_status="reviewed", actor="test")
    quote_decision = evaluate_external_mcp_proxy_call(ROOT, quote, principal_id="head-manager", arguments={"symbol": "AAPL"}, actor="test")
    assert quote_decision["decision"] == "allow"
    assert quote_decision["router"] == "test-broker-mcp"
    assert quote_decision["direct_proxy_allowed"] is True

    try:
        set_external_tool_policy(order, proxy_mode="direct", allowed_roles=["execution-operator"], enabled=True, review_status="reviewed", actor="test")
    except ValueError as exc:
        assert "direct raw proxy mode is not allowed" in str(exc)
    else:
        raise AssertionError("direct execution proxy should be blocked")

    set_external_tool_policy(order, proxy_mode="service_adapter", allowed_roles=["execution-operator"], enabled=True, review_status="reviewed", actor="test")
    denied = evaluate_external_mcp_proxy_call(ROOT, order, principal_id="head-manager", arguments={"symbol": "AAPL"}, actor="test")
    assert denied["decision"] == "deny"
    allowed = evaluate_external_mcp_proxy_call(ROOT, order, principal_id="execution-operator", arguments={"symbol": "AAPL"}, actor="test")
    assert allowed["decision"] == "allow"
    assert allowed["adapter_call_allowed"] is True

    import_external_mcp_discovery(
        router,
        {"tools": [{"name": "get_market_quote", "description": "Get market data quote", "inputSchema": {"type": "object", "properties": {"ticker": {"type": "string"}}}}]},
        actor="test",
    )
    quote.refresh_from_db()
    assert quote.enabled is False
    assert quote.drift_detected is True
    assert quote.review_status == "schema_changed"


def test_external_mcp_lifecycle_tools_manage_stdio_discovery(tmp_path: Path) -> None:
    ensure_runtime_database(ROOT)
    from apps.mcp.models import McpExternalTool, McpRouter

    server = tmp_path / "stdio_mcp_server.py"
    server.write_text(
        """
import json
import sys

for line in sys.stdin:
    if not line.strip():
        continue
    message = json.loads(line)
    method = message.get("method")
    if method == "notifications/initialized":
        continue
    result = {}
    if method == "initialize":
        result = {"protocolVersion": "2025-06-18", "serverInfo": {"name": "fixture-broker", "version": "1"}}
    elif method == "tools/list":
        result = {"tools": [
            {"name": "get_balances", "description": "Read account balances and buying power", "inputSchema": {"type": "object"}},
            {"name": "get_market_quote", "description": "Read public market quote", "inputSchema": {"type": "object"}},
            {"name": "place_order", "description": "Submit broker order", "inputSchema": {"type": "object"}}
        ]}
    elif method == "resources/list":
        result = {"resources": []}
    elif method == "prompts/list":
        result = {"prompts": []}
    else:
        print(json.dumps({"jsonrpc": "2.0", "id": message.get("id"), "error": {"code": -32601, "message": "not found"}}), flush=True)
        continue
    print(json.dumps({"jsonrpc": "2.0", "id": message.get("id"), "result": result}), flush=True)
""".strip()
        + "\n",
        encoding="utf-8",
    )
    McpRouter.objects.filter(name="stdio-fixture-broker").delete()

    registered = call_mcp_tool(
        ROOT,
        "register_external_mcp_connection",
        {
            "principal_id": "head-manager",
            "name": "stdio-fixture-broker",
            "label": "Fixture Broker",
            "transport": "stdio",
            "command": sys.executable,
            "args": [str(server)],
            "enabled": False,
        },
    )
    assert registered["status"] == "registered"

    disabled_check = call_mcp_tool(ROOT, "check_external_mcp_connection", {"principal_id": "head-manager", "name": "stdio-fixture-broker"})
    assert disabled_check["status"] == "disabled"

    call_mcp_tool(ROOT, "register_external_mcp_connection", {"principal_id": "head-manager", "name": "stdio-fixture-broker", "transport": "stdio", "command": sys.executable, "args": [str(server)], "enabled": True})
    checked = call_mcp_tool(ROOT, "check_external_mcp_connection", {"principal_id": "head-manager", "name": "stdio-fixture-broker"})
    assert checked["status"] == "checked"
    assert checked["payload"]["result"]["serverInfo"]["name"] == "fixture-broker"

    discovered = call_mcp_tool(ROOT, "discover_external_mcp_connection", {"principal_id": "head-manager", "name": "stdio-fixture-broker"})
    assert discovered["status"] == "discovered"
    assert discovered["imported"]["imported"] == 3

    router = McpRouter.objects.get(name="stdio-fixture-broker")
    balances = McpExternalTool.objects.get(router=router, external_name="get_balances")
    quote = McpExternalTool.objects.get(router=router, external_name="get_market_quote")
    order = McpExternalTool.objects.get(router=router, external_name="place_order")
    assert balances.category == "account_read"
    assert quote.category == "market_data"
    assert order.category == "execution"

    reviewed = call_mcp_tool(
        ROOT,
        "review_external_mcp_tool",
        {
            "principal_id": "head-manager",
            "tool_id": balances.id,
            "proxy_mode": "summary_only",
            "allowed_roles": ["head-manager", "portfolio-manager", "risk-manager"],
            "enabled": True,
        },
    )
    assert reviewed["tool"]["enabled"] is True
    assert reviewed["connection"]["last_status"] == "enabled_read_only"

    execution_review = call_mcp_tool(
        ROOT,
        "review_external_mcp_tool",
        {
            "principal_id": "head-manager",
            "tool_id": order.id,
            "proxy_mode": "service_adapter",
            "allowed_roles": ["execution-operator"],
            "enabled": True,
        },
    )
    assert execution_review["tool"]["enabled"] is False
    assert execution_review["tool"]["review_status"] == "adapter_mapping_required"


def test_external_mcp_gate_web_lifecycle_and_review(tmp_path: Path) -> None:
    ensure_runtime_database(ROOT)
    from apps.mcp.models import McpExternalTool, McpRouter

    server = tmp_path / "web_stdio_mcp_server.py"
    server.write_text(
        """
import json
import sys

for line in sys.stdin:
    if not line.strip():
        continue
    message = json.loads(line)
    method = message.get("method")
    if method == "notifications/initialized":
        continue
    result = {}
    if method == "initialize":
        result = {"protocolVersion": "2025-06-18", "serverInfo": {"name": "web-fixture-broker", "version": "1"}}
    elif method == "tools/list":
        result = {"tools": [
            {"name": "get_balances", "description": "Read account balances", "inputSchema": {"type": "object"}},
            {"name": "place_order", "description": "Submit broker order", "inputSchema": {"type": "object"}}
        ]}
    elif method == "resources/list":
        result = {"resources": []}
    elif method == "prompts/list":
        result = {"prompts": []}
    else:
        print(json.dumps({"jsonrpc": "2.0", "id": message.get("id"), "error": {"code": -32601, "message": "not found"}}), flush=True)
        continue
    print(json.dumps({"jsonrpc": "2.0", "id": message.get("id"), "result": result}), flush=True)
""".strip()
        + "\n",
        encoding="utf-8",
    )

    McpRouter.objects.filter(name="web-stdio-fixture-broker").delete()
    client = Client(REMOTE_ADDR="127.0.0.1")
    create_response = client.post(
        "/integrations/mcp/routers/create/",
        {
            "name": "web-stdio-fixture-broker",
            "label": "Web Fixture Broker",
            "transport": "stdio",
            "command": sys.executable,
            "args": json.dumps([str(server)]),
            "enabled": "true",
        },
    )
    assert create_response.status_code == 302
    router = McpRouter.objects.get(name="web-stdio-fixture-broker")
    assert router.args == [str(server)]

    check_response = client.post(f"/integrations/mcp/routers/{router.id}/check/")
    assert check_response.status_code == 302
    router.refresh_from_db()
    assert router.last_status == "checked"

    discover_response = client.post(f"/integrations/mcp/routers/{router.id}/discover/")
    assert discover_response.status_code == 302
    router.refresh_from_db()
    assert router.last_status == "discovered"
    balances = McpExternalTool.objects.get(router=router, external_name="get_balances")
    order = McpExternalTool.objects.get(router=router, external_name="place_order")

    review_response = client.post(
        f"/integrations/mcp/tools/{balances.id}/update/",
        {
            "category": "account_read",
            "risk_level": "read",
            "sensitivity": "private",
            "canonical_capability": "account.positions.read",
            "proxy_mode": "summary_only",
            "allowed_roles": "head-manager,portfolio-manager,risk-manager",
            "enabled": "true",
        },
    )
    assert review_response.status_code == 302
    balances.refresh_from_db()
    assert balances.enabled is True
    assert balances.review_status == "reviewed"

    execution_review = client.post(
        f"/integrations/mcp/tools/{order.id}/update/",
        {
            "category": "execution",
            "risk_level": "execution",
            "sensitivity": "private",
            "canonical_capability": "order.submit",
            "proxy_mode": "service_adapter",
            "allowed_roles": "execution-operator",
            "enabled": "true",
        },
    )
    assert execution_review.status_code == 302
    order.refresh_from_db()
    assert order.enabled is False
    assert order.review_status == "adapter_mapping_required"

    page_response = client.get("/integrations/mcp/")
    page_body = page_response.content.decode()
    assert page_response.status_code == 200
    assert "Review status" in page_body
    assert "Source gate" not in page_body
    assert "Access mode" in page_body
    assert "Selected action" in page_body
    assert "Requester role" in page_body
    assert "Dry-run input" in page_body
    assert "Action mapping" in page_body
    assert "Account Read" in page_body
    assert "Adapter Mapping Required" in page_body
    assert "Summary only" in page_body
    assert "Approved service path" in page_body
    assert "Selected tool" not in page_body
    assert "Principal" not in page_body
    assert "Proxy dry-run" not in page_body
    assert ">summary_only<" not in page_body
    assert ">account_read<" not in page_body
    assert ">adapter_mapping_required<" not in page_body


def test_product_web_agents_first_routes_render_skill_preview() -> None:
    ensure_runtime_database(ROOT)
    client = Client(REMOTE_ADDR="127.0.0.1")

    dashboard = client.get("/")
    assert dashboard.status_code == 302
    assert dashboard["Location"].endswith("/workflow/starter-prompt/")

    harness = client.get("/harness/")
    assert harness.status_code == 302
    assert harness["Location"].endswith("/harness/agents/")

    agents = client.get("/harness/agents/")
    assert agents.status_code == 200
    body = agents.content.decode()
    assert "Agents" in body
    assert "TradingCodex" in body
    assert "tcx tcx" not in body
    assert 'href="/workflow/starter-prompt/"' in body
    assert ">Plan</a>" in body
    assert body.find(">Plan</a>") < body.find(">Agents</a>")
    assert "Head Manager" in body
    assert "Required skills" in body
    assert "Optional skills" in body
    assert "Additional instructions" in body or "Project-local instructions" in body
    assert "Additional instructions" in body or "Role notes" in body
    assert "Skill preview" in body
    assert "head-manager" in body
    assert "fundamental-analyst" in body
    assert "execution-operator" in body
    assert "tcx-workflow" in body
    assert "tcx-server" in body
    assert "tcx-build" in body
    assert "strategy-creator" in body
    assert "postmortem" in body
    internal_agents = client.get("/harness/agents/?include_internal=true")
    assert internal_agents.status_code == 200
    internal_body = internal_agents.content.decode()
    assert "tcx-workflow" in internal_body
    assert "tcx-build" in internal_body
    assert 'href="/policy/"' not in body
    assert 'href="/activity/"' not in body
    assert 'href="/portfolio/"' not in body
    assert 'href="/orders/"' not in body
    assert 'href="/harness/"' not in body
    assert 'href="/"' not in body
    assert "tc-workspace-card" in body
    assert 'aria-label="Open workspace folder"' in body
    assert "Open path" not in body
    assert "tc-sidebar-context" not in body
    assert "Runtime details" not in body
    assert "Central DB" not in body
    assert "close" in body
    assert "Remove ref" not in body
    assert '<span class="tc-section-title">Boundary</span>' not in body
    assert "tc-sidebar-resizer" in body
    assert "static/tradingcodex_web/app.css" in body
    assert "?v=tcx-" in body
    assert "static/vendor/htmx/htmx.min.js" in body
    assert "TRADINGCODEX_API_KEY" not in body

    selected = client.get("/harness/agents/?role=fundamental-analyst&skill=fundamental-analysis")
    selected_body = selected.content.decode()
    assert selected.status_code == 200
    assert "fundamental-analysis" in selected_body
    assert "Fundamental Analysis" in selected_body
    assert "Skill details" in selected_body
    assert "Description" in selected_body
    assert client.get("/harness/agents/fundamental-analyst/skills/").status_code == 404

    notes = client.get("/harness/agents/?role=fundamental-analyst&panel=notes")
    notes_body = notes.content.decode()
    assert notes.status_code == 200
    assert "Keep summaries concise for this project" in notes_body
    assert "action permissions" in notes_body
    assert "MCP permissions" not in notes_body
    assert "locale-specific summaries" not in notes_body

    for route in ["/harness/agents/", "/harness/agents/?role=fundamental-analyst", "/harness/strategies/", "/research/", "/portfolio/", "/orders/", "/policy/", "/activity/", "/integrations/mcp/", "/workflow/starter-prompt/"]:
        response = client.get(route)
        assert response.status_code == 200
        route_body = response.content.decode()
        assert "tcx" in route_body
        assert "Runtime details" not in route_body
        assert "Central DB" not in route_body
        assert "TRADINGCODEX_API_KEY" not in route_body

    mcp_router = client.get("/integrations/mcp/")
    mcp_router_body = mcp_router.content.decode()
    assert "Data Sources" in mcp_router_body
    assert "Review status" in mcp_router_body
    assert "Source gate" not in mcp_router_body
    assert "Source connections" in mcp_router_body
    assert "Available actions" in mcp_router_body
    assert "Safety review" in mcp_router_body
    assert "Add source" in mcp_router_body
    assert "Add source connection" in mcp_router_body
    mcp_router_template = (ROOT / "tradingcodex_service" / "templates" / "web" / "mcp_router.html").read_text(encoding="utf-8")
    assert "discover available read-only actions" in mcp_router_template
    assert "reviewed before it can reach a TradingCodex workflow" in mcp_router_template
    assert "Save connection" in mcp_router_body
    assert "Check" in mcp_router_template
    assert "Discover" in mcp_router_template
    assert "/integrations/mcp/routers/create/" in mcp_router_body
    assert "/integrations/mcp/routers/" in mcp_router_body

    policy = client.get("/policy/")
    policy_body = policy.content.decode()
    assert "Actor" in policy_body
    assert "<th>Principal</th>" not in policy_body

    research = client.get("/research/")
    research_body = research.content.decode()
    assert '<div class="tc-page-ribbon">' in research_body
    assert "Workspace research" not in research_body
    assert "Select an artifact to open its markdown preview page" not in research_body
    assert "{{ workspace_context.git_branch" not in research_body
    assert "<span>local</span>" not in research_body

    portfolio = client.get("/portfolio/")
    portfolio_body = portfolio.content.decode()
    assert portfolio.status_code == 200
    assert "Plan portfolio review" in portfolio_body
    assert "Plan risk check" in portfolio_body
    assert "Plan rebalance review" in portfolio_body
    assert "Browse reports" in portfolio_body
    assert "/workflow/starter-prompt/?q=Review%20my%20current%20portfolio" in portfolio_body
    assert "Explain with Codex" not in portfolio_body
    assert "Draft rebalance" not in portfolio_body

    strategies = client.get("/harness/strategies/")
    strategies_body = strategies.content.decode()
    assert strategies.status_code == 200
    assert "fixed, user-approved judgment guides" in strategies_body
    assert "never grant approval or execution authority" in strategies_body
    assert "$strategy-creator" not in strategies_body
    assert 'href="/harness/strategies/?mode=new"' not in strategies_body
    assert 'action="/harness/strategies/create/"' not in strategies_body
    assert "/update/" not in strategies_body
    assert "/delete/" not in strategies_body
    assert "Strategy Creator" not in strategies_body

    admin_response = client.get("/admin/login/")
    assert admin_response.status_code == 200
    assert "Django administration" in admin_response.content.decode()
    assert "tcx Control Plane" not in admin_response.content.decode()


def test_product_web_agent_skill_and_strategy_mutation(tmp_path: Path, monkeypatch) -> None:
    workspace = make_workspace(tmp_path)
    monkeypatch.setenv("TRADINGCODEX_WORKSPACE_ROOT", str(workspace))
    projected = write_optional_skill_fixture(workspace, "fundamental-analyst", "filing-red-flag-review")
    assert "filing-red-flag-review" in projected["agents"]["fundamental-analyst"]["effective_skills"]
    client = Client(REMOTE_ADDR="127.0.0.1")

    index = client.get("/harness/agents/")
    assert index.status_code == 200
    index_body = index.content.decode()
    assert "Head Manager" in index_body
    assert "Required skills" in index_body
    assert "Optional skills" in index_body
    assert "Additional instructions" in index_body or "Project-local instructions" in index_body
    assert "Diagnostics" not in index_body
    assert "Projection hash" not in index_body
    assert "Workspace file" not in index_body

    additional = client.post(
        "/harness/agents/fundamental-analyst/instructions/update/",
        data={
            "body": "Project preference: keep source notes terse.\nMention approval authority only when warning about boundaries.",
            "next": "/harness/agents/?role=fundamental-analyst",
        },
    )
    assert additional.status_code == 302
    additional_path = workspace / ".tradingcodex" / "agent-instructions" / "fundamental-analyst.md"
    assert additional_path.exists()
    assert "approval authority" in additional_path.read_text(encoding="utf-8")
    projected_agent = (workspace / ".codex" / "agents" / "fundamental-analyst.toml").read_text(encoding="utf-8")
    assert "BEGIN TradingCodex additional instructions" in projected_agent
    assert "Project preference: keep source notes terse." in projected_agent
    assert "approval authority" in tomllib.loads(projected_agent)["developer_instructions"]
    projected_state = json.loads((workspace / ".tradingcodex" / "generated" / "agent-index.json").read_text(encoding="utf-8"))
    assert projected_state["agents"]["fundamental-analyst"]["additional_instructions"]["line_count"] == 2

    special_body = 'Project path: C:\\data\\reports\nLiteral triple quote marker: """ should stay readable.'
    special = client.post(
        "/harness/agents/fundamental-analyst/instructions/update/",
        data={"body": special_body, "next": "/harness/agents/?role=fundamental-analyst"},
    )
    assert special.status_code == 302
    special_projected_agent = (workspace / ".codex" / "agents" / "fundamental-analyst.toml").read_text(encoding="utf-8")
    parsed_special_agent = tomllib.loads(special_projected_agent)
    assert "C:\\data\\reports" in parsed_special_agent["developer_instructions"]
    assert '""" should stay readable' in parsed_special_agent["developer_instructions"]
    repeated = client.post(
        "/harness/agents/fundamental-analyst/instructions/update/",
        data={"body": special_body, "next": "/harness/agents/?role=fundamental-analyst"},
    )
    assert repeated.status_code == 302
    assert (workspace / ".codex" / "agents" / "fundamental-analyst.toml").read_text(encoding="utf-8") == special_projected_agent

    cleared = client.post(
        "/harness/agents/fundamental-analyst/instructions/update/",
        data={"body": "", "next": "/harness/agents/?role=fundamental-analyst"},
    )
    assert cleared.status_code == 302
    assert not additional_path.exists()
    assert "BEGIN TradingCodex additional instructions" not in (workspace / ".codex" / "agents" / "fundamental-analyst.toml").read_text(encoding="utf-8")

    head_manager_instruction = client.post(
        "/harness/agents/head-manager/instructions/update/",
        data={"body": "Project preference: synthesize in the user's request language.", "next": "/harness/agents/?role=head-manager"},
    )
    assert head_manager_instruction.status_code == 302
    head_manager_prompt = (workspace / ".codex" / "prompts" / "base_instructions" / "head-manager.md").read_text(encoding="utf-8")
    assert "BEGIN TradingCodex additional instructions" in head_manager_prompt
    assert "synthesize in the user's request language" in head_manager_prompt

    detail = client.get("/harness/agents/?role=fundamental-analyst&skill=filing-red-flag-review")
    detail_body = detail.content.decode()
    assert detail.status_code == 200
    assert "filing-red-flag-review" in detail_body
    assert "Filing Red Flag Review" in detail_body
    assert "Review filing excerpts" in detail_body
    assert "Diagnostics" not in detail_body
    assert "Current TOML projection" not in detail_body

    created = client.post(
        "/harness/agents/fundamental-analyst/optional-skills/create/",
        data={
            "name": "source-quality-check",
            "description": "Check whether cited evidence is fresh and source-tagged.",
            "body": "# Source Quality Check\n\nReview assigned evidence for source quality.",
            "status": "active",
        },
    )
    assert created.status_code == 302
    optional_path = workspace / ".tradingcodex" / "subagents" / "skills" / "fundamental-analyst" / "source-quality-check" / "SKILL.md"
    assert optional_path.exists()
    agent_toml = (workspace / ".codex" / "agents" / "fundamental-analyst.toml").read_text(encoding="utf-8")
    assert ".tradingcodex/subagents/skills/fundamental-analyst/source-quality-check/SKILL.md" in agent_toml

    strategy_web_create = client.post(
        "/harness/strategies/create/",
        data={
            "name": "strategy-quality-income",
            "description": "Apply a quality income strategy.",
            "language": "ko-KR",
            "body": "# Quality Income\n\nFocus on durable income quality.",
            "status": "active",
        },
    )
    assert strategy_web_create.status_code == 404
    create_or_update_strategy_skill(
        workspace,
        "strategy-quality-income",
        description="Apply a quality income strategy.",
        language="ko-KR",
        body="# Quality Income\n\nFocus on durable income quality.",
        status="active",
        actor="test",
    )
    strategy_path = workspace / ".agents" / "skills" / "strategy-quality-income" / "SKILL.md"
    assert strategy_path.exists()
    strategy_text = strategy_path.read_text(encoding="utf-8")
    assert "## Risk Controls" in strategy_text
    assert "## Portfolio And Risk Handoff" not in strategy_text
    assert "portfolio-manager" not in strategy_text
    assert "risk-manager" not in strategy_text
    assert "handoff" not in strategy_text.lower()
    original_strategy_text = strategy_text
    root_config = (workspace / ".codex" / "config.toml").read_text(encoding="utf-8")
    agent_toml = (workspace / ".codex" / "agents" / "fundamental-analyst.toml").read_text(encoding="utf-8")
    assert ".agents/skills/strategy-quality-income/SKILL.md" in root_config
    agent_strategy_blocks = [
        block
        for block in tomllib.loads(agent_toml).get("skills", {}).get("config", [])
        if str(block.get("path", "")).endswith(".agents/skills/strategy-quality-income/SKILL.md")
    ]
    assert agent_strategy_blocks
    assert all(block.get("enabled") is False for block in agent_strategy_blocks)
    strategy_list = client.get("/harness/strategies/")
    strategy_list_body = strategy_list.content.decode()
    assert strategy_list.status_code == 200
    assert "Quality Income" in strategy_list_body
    assert "Strategy rules" not in strategy_list_body
    strategy_detail = client.get("/harness/strategies/?name=strategy-quality-income")
    strategy_detail_body = strategy_detail.content.decode()
    assert strategy_detail.status_code == 200
    assert "Strategy rules" in strategy_detail_body
    assert "Fixed judgment guide" in strategy_detail_body
    assert "does not approve orders, execute trades, or change policy" in strategy_detail_body
    assert "Strategy details" in strategy_detail_body
    assert "Markdown preview" not in strategy_detail_body
    assert "Frontmatter" not in strategy_detail_body
    assert "<dt>Status</dt>" in strategy_detail_body
    assert "<dd>active</dd>" in strategy_detail_body
    assert "<h2>Strategy detail</h2>" not in strategy_detail_body
    assert 'href="/harness/strategies/?name=strategy-quality-income&mode=edit"' not in strategy_detail_body
    assert "Delete" not in strategy_detail_body
    assert "Activate" not in strategy_detail_body
    strategy_edit = client.get("/harness/strategies/?name=strategy-quality-income&mode=edit")
    strategy_edit_body = strategy_edit.content.decode()
    assert strategy_edit.status_code == 200
    assert "Focus on durable income quality." in strategy_edit_body
    assert 'action="/harness/strategies/strategy-quality-income/update/"' not in strategy_edit_body
    assert "Save strategy" not in strategy_edit_body

    coupled_body = "\n".join(
        [
            "# Coupled Strategy",
            "",
            "## Thesis",
            "Test.",
            "",
            "## Eligible Universe",
            "Test.",
            "",
            "## Preferred Setups",
            "Test.",
            "",
            "## Entry Criteria",
            "Test.",
            "",
            "## Exit Criteria",
            "Test.",
            "",
            "## Evidence Requirements",
            "Test.",
            "",
            "## Decision-Ready Standard",
            "Test.",
            "",
            "## Sizing Guidance",
            "not specified. Position size, leverage, maximum loss, scaling in, and scaling out require separate portfolio-manager and risk-manager handoff review.",
            "",
            "## Risk Controls",
            "Test.",
            "",
            "## Block Conditions",
            "Test.",
            "",
            "## Change Log",
            "- Test.",
            "",
        ]
    )
    with pytest.raises(ValueError, match="standalone"):
        create_or_update_strategy_skill(
            workspace,
            "strategy-coupled",
            description="Should fail when strategy content names platform roles.",
            language="ko-KR",
            body=coupled_body,
            status="active",
            actor="test",
        )
    assert not (workspace / ".agents" / "skills" / "strategy-coupled" / "SKILL.md").exists()
    assert "strategy-coupled" not in (workspace / ".codex" / "config.toml").read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="standalone"):
        create_or_update_strategy_skill(
            workspace,
            "strategy-quality-income",
            description="Apply a quality income strategy.",
            language="ko-KR",
            body=coupled_body.replace("# Coupled Strategy", "# Quality Income"),
            status="active",
            actor="test",
        )
    assert strategy_path.read_text(encoding="utf-8") == original_strategy_text

    api_status = client.get("/api/harness/optional-skills?role=fundamental-analyst").json()
    assert {record["name"] for record in api_status["optional_skills"]} >= {"filing-red-flag-review", "source-quality-check"}
    source_quality_record = next(record for record in api_status["optional_skills"] if record["name"] == "source-quality-check")
    assert source_quality_record["source_file"] == ".tradingcodex/subagents/skills/fundamental-analyst/source-quality-check/SKILL.md"
    assert "skill_id" not in source_quality_record
    assert "title" not in source_quality_record
    assert "strategy-quality-income" in {record["name"] for record in client.get("/api/harness/strategies").json()["strategies"]}
    api_optional = client.post(
        "/api/subagents/fundamental-analyst/optional-skills",
        data=json.dumps({
            "name": "evidence-freshness-check",
            "description": "Check source timestamps before handoff.",
            "body": "# Evidence Freshness Check\n\nCheck source timestamps.",
            "status": "active",
        }),
        content_type="application/json",
    )
    assert api_optional.status_code == 200
    assert "evidence-freshness-check" in (workspace / ".codex" / "agents" / "fundamental-analyst.toml").read_text(encoding="utf-8")
    api_strategy = client.post(
        "/api/harness/strategies",
        data=json.dumps({
            "name": "strategy-catalyst-watch",
            "description": "Track catalyst-driven setups.",
            "body": "# Catalyst Watch\n\nTrack catalyst-driven setups.",
            "status": "active",
        }),
        content_type="application/json",
    )
    assert api_strategy.status_code == 200
    assert "strategy-catalyst-watch" in (workspace / ".codex" / "config.toml").read_text(encoding="utf-8")
    mcp_tool_names = {tool["name"] for tool in client.get("/api/integrations/mcp-tools").json()["tools"]}
    assert "create_optional_role_skill" not in mcp_tool_names
    assert "update_optional_role_skill" not in mcp_tool_names
    assert "delete_optional_role_skill" not in mcp_tool_names
    assert "list_external_mcp_connections" in mcp_tool_names
    assert "discover_external_mcp_connection" in mcp_tool_names
    assert "review_external_mcp_tool" in mcp_tool_names


def test_product_web_research_artifact_markdown_preview(tmp_path: Path, monkeypatch) -> None:
    workspace = make_workspace(tmp_path)
    monkeypatch.setenv("TRADINGCODEX_WORKSPACE_ROOT", str(workspace))
    from tradingcodex_service.application.markdown_preview import render_markdown_preview
    from tradingcodex_service.application.research import create_research_artifact, get_research_artifact

    stored = create_research_artifact(
        workspace,
        {
            "artifact_id": "web-preview-note",
            "artifact_type": "research_memo",
            "title": "Web Preview Note",
            "symbol": "NVDA",
            "markdown": "# Web Preview Note\n\n[factual] Preview body.\n\n<script>alert('x')</script>",
            "readiness_label": "research-grade",
            "reader_summary": "Plain-English preview summary.",
            "next_action": "Use this as evidence only.",
            "export": False,
        },
    )
    assert stored["db_canonical"] is False
    assert stored["file_sot"] is True
    assert (workspace / stored["export_path"]).exists()
    source_with_frontmatter = workspace / "trading" / "research" / "source-frontmatter.md"
    source_with_frontmatter.write_text(
        "---\nartifact_id: source-frontmatter-note\ntitle: Source Frontmatter Note\nsource_as_of: 2026-06-03\n---\n\n# Source Body\n",
        encoding="utf-8",
    )
    frontmatter_stored = create_research_artifact(
        workspace,
        {
            "markdown_path": "trading/research/source-frontmatter.md",
            "artifact_type": "research_memo",
            "created_by": "fundamental-analyst",
        },
    )
    frontmatter_text = (workspace / frontmatter_stored["export_path"]).read_text(encoding="utf-8")
    assert frontmatter_text.count("---") == 2
    assert get_research_artifact(workspace, {"artifact_id": "source-frontmatter-note"})["markdown"] == "# Source Body\n"
    client = Client(REMOTE_ADDR="127.0.0.1")

    list_response = client.get("/research/")
    list_body = list_response.content.decode()
    assert list_response.status_code == 200
    assert "Web Preview Note" in list_body
    assert "Public equities" in list_body
    assert "public_equity" not in list_body
    assert "Plain-English preview summary." in list_body
    assert "Next: Use this as evidence only." in list_body
    assert "<h1>Web Preview Note</h1>" not in list_body

    response = client.get("/research/?artifact=web-preview-note")
    body = response.content.decode()
    assert response.status_code == 200
    assert "Web Preview Note" in body
    assert "Research note" in body
    assert "Public equities" in body
    assert "public_equity" not in body
    assert "Use this note" in body
    assert "Plain read" in body
    assert "Next action" in body
    assert "Document details" in body
    assert "Markdown preview" not in body
    assert "Frontmatter" not in body
    assert "Artifact Id" in body
    assert "web-preview-note" in body
    assert "<dt>Artifact Id</dt>" in body
    assert "<dd>web-preview-note</dd>" in body
    assert "11 fields" not in body
    assert "Readiness Label" in body
    assert "Reader Summary" in body
    assert "Plain-English preview summary." in body
    assert "Next Action" in body
    assert "Use this as evidence only." in body
    assert "<h1>Web Preview Note</h1>" in body
    assert "Preview body" in body
    assert "<script>alert" not in body
    assert "<hr" not in body

    rendered = render_markdown_preview("# Safe\n\n<script>alert('x')</script>")
    assert "<script" not in rendered.html
    frontmatter_rendered = render_markdown_preview("---\ntitle: Frontmatter Title\n---\n\n# Body Only\n")
    assert frontmatter_rendered.frontmatter["title"] == "Frontmatter Title"
    assert "<h1>Body Only</h1>" in frontmatter_rendered.html
    assert "title:" not in frontmatter_rendered.html


def test_workspace_sidebar_keeps_recent_list_compact() -> None:
    from tradingcodex_service.web import _workspace_sidebar_options

    options = [
        {
            "workspace_id": f"workspace-{index}",
            "project_name": f"Workspace {index}",
            "path": f"/tmp/workspace-{index}",
            "selected": index == 6,
        }
        for index in range(8)
    ]
    grouped = _workspace_sidebar_options(options, visible_limit=5)

    assert [item["workspace_id"] for item in grouped["visible"]] == [
        "workspace-6",
        "workspace-0",
        "workspace-1",
        "workspace-2",
        "workspace-3",
    ]
    assert [item["workspace_id"] for item in grouped["hidden"]] == ["workspace-4", "workspace-5", "workspace-7"]


def test_product_web_workspace_selector_uses_session(tmp_path: Path, monkeypatch) -> None:
    workspace_a = tmp_path / "workspace-a"
    workspace_b = tmp_path / "workspace-b"
    bootstrap_workspace(workspace_a, force=True)
    bootstrap_workspace(workspace_b, force=True)
    monkeypatch.setenv("TRADINGCODEX_WORKSPACE_ROOT", str(workspace_a))

    from tradingcodex_service.application.runtime import persist_workspace_context_if_available
    from tradingcodex_service.application.harness import get_role_detail, list_recent_activity
    from tradingcodex_service.web import WORKSPACE_SESSION_KEY
    from apps.audit.models import AuditEvent
    from apps.mcp.models import McpToolCall
    from apps.workflows.models import WorkflowRun

    context_a = persist_workspace_context_if_available(workspace_a)
    context_b = persist_workspace_context_if_available(workspace_b)
    McpToolCall.objects.create(tool_name="workspace-a-tool", principal_id="fundamental-analyst", status="ok", workspace_context=context_a)
    McpToolCall.objects.create(tool_name="workspace-b-tool", principal_id="fundamental-analyst", status="ok", workspace_context=context_b)
    AuditEvent.objects.create(action="workspace-a-audit", actor_principal="head-manager", source="test", workspace_context=context_a)
    AuditEvent.objects.create(action="workspace-b-audit", actor_principal="head-manager", source="test", workspace_context=context_b)
    WorkflowRun.objects.create(run_id="workspace-a-run", lane="workspace-a-lane", universe="public_equity", workspace_context=context_a)
    WorkflowRun.objects.create(run_id="workspace-b-run", lane="workspace-b-lane", universe="public_equity", workspace_context=context_b)

    activity_a = list_recent_activity(workspace_a, limit=10)
    activity_a_titles = {item["title"] for item in activity_a}
    assert {"workspace-a-tool", "workspace-a-audit", "workspace-a-lane"} <= activity_a_titles
    assert "workspace-b-tool" not in activity_a_titles
    assert "workspace-b-audit" not in activity_a_titles
    assert "workspace-b-lane" not in activity_a_titles
    role_a = get_role_detail("fundamental-analyst", workspace_a)
    assert [item["title"] for item in role_a["latest_activity"]] == ["workspace-a-tool"]

    client = Client(REMOTE_ADDR="127.0.0.1")

    landing = client.get("/harness/agents/")
    landing_body = landing.content.decode()
    assert landing.status_code == 200
    assert "tc-workspace-card" in landing_body
    assert 'aria-label="Open workspace folder"' in landing_body
    assert "Open path" not in landing_body

    assert "tc-sidebar-context" not in landing_body
    assert "Runtime details" not in landing_body
    assert "Central DB" not in landing_body
    assert "close" in landing_body
    assert "Remove ref" not in landing_body
    assert '<span class="tc-section-title">Boundary</span>' not in landing_body
    assert context_a["workspace_id"] in landing_body
    assert context_b["workspace_id"] in landing_body

    selected = client.get(f"/harness/agents/?workspace={context_b['workspace_id']}")
    selected_body = selected.content.decode()
    assert selected.status_code == 200
    assert client.session[WORKSPACE_SESSION_KEY] == context_b["workspace_id"]
    assert str(workspace_b.resolve()) in selected_body

    for route in ["/harness/agents/?role=fundamental-analyst", "/research/"]:
        response = client.get(route)
        assert response.status_code == 200
        assert str(workspace_b.resolve()) in response.content.decode()

    fallback = client.get("/harness/agents/?workspace=missing-workspace")
    assert fallback.status_code == 200
    assert str(workspace_a.resolve()) in fallback.content.decode()
    assert WORKSPACE_SESSION_KEY not in client.session

    unbootstrapped = tmp_path / "unbootstrapped-repo"
    unbootstrapped.mkdir()
    (unbootstrapped / "README.md").write_text("# Existing repo\n", encoding="utf-8")
    from tradingcodex_service import web as web_module

    monkeypatch.setattr(web_module, "_choose_workspace_directory", lambda: unbootstrapped.resolve())
    opened = client.post("/workspaces/browse/", {"next": "/research/"})
    assert opened.status_code == 302
    assert not (unbootstrapped / ".tradingcodex" / "workspace.json").exists()
    opened_body = client.get("/research/").content.decode()
    assert str(unbootstrapped.resolve()) not in opened_body
    assert "Could not open workspace" in opened_body

    created_workspace = tmp_path / "created-web-workspace"
    created_workspace.mkdir()
    created = client.post("/workspaces/create/", {"workspace_path": str(created_workspace), "next": "/research/"})
    assert created.status_code == 302
    assert (created_workspace / ".tradingcodex" / "workspace.json").exists()
    created_body = client.get("/research/").content.decode()
    assert str(created_workspace.resolve()) in created_body
    assert "Workspace created and opened." in created_body

    from apps.harness.models import WorkspaceContext

    opened_context = WorkspaceContext.objects.get(path=str(created_workspace.resolve()))
    removed = client.post(f"/workspaces/{opened_context.workspace_id}/remove/", {"next": "/research/"})
    assert removed.status_code == 302
    assert not WorkspaceContext.objects.filter(workspace_id=opened_context.workspace_id).exists()
    assert (created_workspace / ".tradingcodex" / "workspace.json").exists()


def test_order_transition_audit_uses_ticket_workspace_context(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / "ticket-workspace"
    other_workspace = tmp_path / "other-workspace"
    bootstrap_workspace(workspace, force=True)
    bootstrap_workspace(other_workspace, force=True)
    monkeypatch.chdir(ROOT)
    monkeypatch.setenv("TRADINGCODEX_WORKSPACE_ROOT", str(other_workspace))

    from apps.audit.models import AuditEvent
    from tradingcodex_service.application.harness import list_recent_activity
    from tradingcodex_service.application.orders import create_order_ticket, run_order_checks

    ticket_id = "transition-audit-workspace-ticket"
    create_order_ticket(
        workspace,
        {
            "principal_id": "portfolio-manager",
            "ticket_id": ticket_id,
            "symbol": "AAPL",
            "side": "buy",
            "quantity": 1,
            "limit_price": 1000,
        },
    )
    run_order_checks(workspace, {"principal_id": "portfolio-manager", "ticket_id": ticket_id})

    expected_context = workspace_context_payload(workspace)
    transition_event = AuditEvent.objects.filter(action="order_ticket.transition", resource=ticket_id).order_by("-id").first()
    assert transition_event is not None
    assert transition_event.workspace_context["workspace_id"] == expected_context["workspace_id"]
    assert transition_event.workspace_context["path"] == str(workspace.resolve())

    activity_titles = {item["title"] for item in list_recent_activity(workspace, limit=20)}
    other_activity_titles = {item["title"] for item in list_recent_activity(other_workspace, limit=20)}
    assert "order_ticket.transition" in activity_titles
    assert "order_ticket.transition" not in other_activity_titles


def test_product_web_role_inspector_and_topology_helpers() -> None:
    client = Client(REMOTE_ADDR="127.0.0.1")
    from django.template.loader import render_to_string
    from tradingcodex_service.application.harness import get_harness_topology, get_role_detail

    response = client.get("/harness/roles/portfolio-manager/")
    body = response.content.decode()
    assert response.status_code == 200
    assert "Portfolio Manager" in body
    assert "portfolio-review" in body
    assert "create-order-ticket" in body
    assert "run_order_checks" in body
    assert "Allowed Actions" in body
    assert "Allowed MCP Tools" not in body
    assert "No self-approval" in body
    assert "No-overlap" in body
    assert "Does not self-approve, execute, or repair missing research/valuation work." in body

    topology = get_harness_topology(ROOT)
    topology_html = render_to_string(
        "web/fragments/topology_canvas.html",
        {"topology": topology, "selected_role": get_role_detail("portfolio-manager", ROOT)},
    )
    assert "Roles, review gates, and approved action path" in topology_html
    assert " skills · " in topology_html
    assert " actions" in topology_html

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
    assert "permission" in topology["boundary"]["summary"]
    assert "duplicate-request" in topology["boundary"]["summary"]
    assert [layer["label"] for layer in topology["layers"]] == [
        "Coordinator",
        "Research roles",
        "Valuation",
        "Portfolio fit",
        "Risk approval",
        "Approved submission",
    ]
    assert {edge["group"] for edge in topology["edges"]} == {
        "dispatch",
        "research-handoff",
        "portfolio-risk-gate",
        "approval-gate",
        "execution-gate",
    }
    assert topology["handoff_states"] == ["accepted", "revise", "blocked", "waiting"]
    assert all(group["contract"] for group in topology["edge_groups"])
    detail = get_role_detail("execution-operator", ROOT)
    assert any(tool["name"] == "submit_approved_order" for tool in detail["allowed_tools"])
    assert "No raw broker API." in detail["forbidden_actions"]
    assert detail["handoff_contract"]["receives"] == "Approved order ticket, matching approval receipt, and policy allow state."


def test_workflow_artifact_refs_store_handoff_state() -> None:
    ensure_runtime_database(ROOT)
    from apps.workflows.models import ArtifactRef, WorkflowRun

    run_obj = WorkflowRun.objects.create(
        run_id=f"handoff-state-{os.getpid()}",
        lane="research_only",
        universe="public_equity",
        readiness_label="factual-baseline",
    )
    ref = ArtifactRef.objects.create(
        workflow=run_obj,
        path="trading/reports/fundamental/NVDA.fundamental.md",
        artifact_type="fundamental_report",
        role="fundamental-analyst",
        handoff_state="accepted",
    )

    assert ref.handoff_state == "accepted"
    assert ArtifactRef.objects.get(pk=ref.pk).handoff_state == "accepted"


def test_product_web_does_not_create_approvals_or_executions(monkeypatch) -> None:
    ensure_runtime_database(ROOT)
    from apps.audit.models import AuditEvent
    from apps.mcp.models import McpToolCall
    from apps.orders.models import ApprovalReceipt, ExecutionResult

    def forbidden_execution(*args, **kwargs):
        raise AssertionError("product web route attempted an execution-sensitive action")

    monkeypatch.setattr("tradingcodex_service.application.orders.submit_approved_order", forbidden_execution)
    monkeypatch.setattr("tradingcodex_service.application.orders.request_order_approval", forbidden_execution)

    before = (
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
        response = client.get(route, follow=route in {"/", "/harness/"})
        assert response.status_code == 200
    after = (
        ApprovalReceipt.objects.count(),
        ExecutionResult.objects.count(),
        McpToolCall.objects.count(),
        AuditEvent.objects.count(),
    )
    assert after == before

    starter_page = client.get("/workflow/starter-prompt/")
    starter_body = starter_page.content.decode()
    assert "Workflow Planner" in starter_body
    assert "Plan workflow" in starter_body
    assert "What are you considering?" in starter_body
    assert "A rough hunch is enough" in starter_body
    assert "blocked actions, and next questions" in starter_body
    assert "rough idea into evidence" in starter_body
    assert "Preview workflow" in starter_body
    assert "Generate starter prompt" not in starter_body
    assert "Idea research" in starter_body
    assert "TSLA feels interesting, no order" in starter_body
    assert "Rough hunch" in starter_body
    assert "AAPL seems cheap, but I am not sure why. No order." in starter_body
    assert "Decision support" in starter_body
    assert "BTC trend review, no trading" in starter_body

    starter_example = client.get("/workflow/starter-prompt/?q=TSLA%20feels%20interesting%2C%20no%20order")
    starter_example_body = starter_example.content.decode()
    assert "Idea translated" in starter_example_body
    assert "thesis check" in starter_example_body
    assert "Public equities" in starter_example_body
    assert "<code>public_equity</code>" not in starter_example_body

    preview = client.get("/workflow/starter-prompt/preview/?q=BTC%20trend%20review%20no%20trading")
    body = preview.content.decode()
    assert "Workflow" in body
    assert "Research check" in body
    assert "Workflow steps" in body
    assert "Needs:" in body
    assert "Still blocked" in body
    assert "Investment universe: Crypto assets" in body
    assert "Investment universe: public_crypto" not in body
    assert "execution-operator" not in body

    decision_preview = client.get("/workflow/starter-prompt/preview/?q=TSLA%20fair%20value%20and%20whether%20it%20fits%20my%20portfolio%2C%20no%20order")
    decision_body = decision_preview.content.decode()
    assert "Decision support" in decision_body
    assert "Idea translated" in decision_body
    assert "candidate thesis" in decision_body
    assert "Portfolio fit" in decision_body
    assert "Reviews the business, financial drivers" in decision_body
    assert "workflow reaches portfolio/order readiness" in decision_body
    assert "Next allowed actions" in decision_body
    assert "Answer missing profile questions" in decision_body
    assert "Profile needed before advice" in decision_body
    assert "Questions to answer" in decision_body
    assert "What outcome are you trying to achieve with this idea?" in decision_body
    assert "risk tolerance and loss capacity" in decision_body

    idea_preview = client.get("/workflow/starter-prompt/preview/?q=TSLA%20feels%20interesting%2C%20no%20order")
    idea_body = idea_preview.content.decode()
    assert "Thesis review" in idea_body
    assert "Idea translated" in idea_body
    assert "thesis check" in idea_body
    assert "Dispatch thesis roles" in idea_body
    assert "execution-operator" not in idea_body


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


def test_file_native_research_artifacts_via_mcp_api_and_cli(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    stored = call_mcp_tool(workspace, "create_research_artifact", {
        "artifact_id": "nvda-evidence-1",
        "artifact_type": "evidence_pack",
        "universe": "public_equity",
        "workflow_type": "issuer_baseline",
        "symbol": "NVDA",
        "title": "NVDA Evidence Pack",
        "markdown": "# NVDA Evidence\n\n[factual] Gross margin expanded in the cited period.",
        "metadata": {"role": "fundamental"},
        "source_as_of": "2026-06-01",
        "readiness_label": "research-grade",
        "context_summary": "NVDA evidence pack smoke summary for downstream reuse.",
        "reader_summary": "Plain-English first read: NVDA evidence is research-grade but not an order signal.",
        "handoff_state": "accepted",
        "confidence": "medium",
        "missing_evidence": ["updated filing snapshot"],
        "next_recipient": "head-manager",
        "next_action": "Wait for valuation before any portfolio fit discussion.",
        "blocked_actions": ["order_drafting"],
        "source_snapshot_ids": ["unit-test-filing"],
        "created_by": "fundamental-analyst",
    })
    assert stored["db_canonical"] is False
    assert stored["file_sot"] is True
    assert stored["workspace_native"] is True
    assert stored["export_path"] == "trading/research/nvda-evidence-1.evidence.md"
    assert (workspace / stored["export_path"]).exists()
    fetched = call_mcp_tool(workspace, "get_research_artifact", {"artifact_id": "nvda-evidence-1"})
    assert "Gross margin" in fetched["markdown"]
    assert fetched["source_as_of"] == "2026-06-01"
    assert fetched["role"] == "fundamental"
    assert fetched["context_summary"] == "NVDA evidence pack smoke summary for downstream reuse."
    assert fetched["reader_summary"] == "Plain-English first read: NVDA evidence is research-grade but not an order signal."
    assert fetched["handoff_state"] == "accepted"
    assert fetched["confidence"] == "medium"
    assert fetched["missing_evidence"] == ["updated filing snapshot"]
    assert fetched["next_recipient"] == "head-manager"
    assert fetched["next_action"] == "Wait for valuation before any portfolio fit discussion."
    assert fetched["blocked_actions"] == ["order_drafting"]
    assert fetched["source_snapshot_ids"] == ["unit-test-filing"]
    assert 'source_as_of: "2026-06-01"' in (workspace / stored["export_path"]).read_text(encoding="utf-8")
    assert 'role: "fundamental"' in (workspace / stored["export_path"]).read_text(encoding="utf-8")
    strict_quality = json.loads(run(["./tcx", "quality-check", stored["export_path"], "--strict"], workspace).stdout)
    assert strict_quality["status"] == "pass"
    assert strict_quality["claim_tags"]["factual"] == 1
    assert strict_quality["context_efficiency"]["context_summary_present"] is True
    assert strict_quality["context_efficiency"]["body_estimated_tokens"] > 0
    assert strict_quality["frontmatter"]["reader_summary"].startswith("Plain-English first read")
    assert strict_quality["frontmatter"]["next_action"] == "Wait for valuation before any portfolio fit discussion."
    assert not any("non-expert first-read UX" in warning for warning in strict_quality["warnings"])
    weak_path = workspace / "trading" / "research" / "weak.md"
    weak_path.write_text("# Weak\n\nUntyped claim.\n", encoding="utf-8")
    weak_quality = json.loads(run(["./tcx", "quality-check", "trading/research/weak.md", "--strict"], workspace, expect_ok=False).stdout)
    assert weak_quality["status"] == "fail"
    assert "claim_tags" in weak_quality["required_fields_missing"]
    assert "missing reader_summary for non-expert first-read UX" in weak_quality["warnings"]
    assert "missing next_action for non-expert first-read UX" in weak_quality["warnings"]
    forecast_dir = workspace / "trading" / "forecasts"
    forecast_dir.mkdir(parents=True, exist_ok=True)
    forecast_record = {
        "forecast_id": "fcst_unit_nvda_001",
        "workflow_run_id": "workflow-unit",
        "artifact_id": "valuation-nvda",
        "role": "valuation-analyst",
        "instrument": "NVDA",
        "forecast_target": "NVDA total return exceeds benchmark by 10 percentage points",
        "horizon": "2026-12-31",
        "probability": 0.35,
        "probability_range": "30-40%",
        "base_rate": {"value": 0.22, "source": "unit"},
        "evidence_ids": ["unit-test-filing"],
        "contrary_evidence": ["valuation embeds high growth"],
        "invalidation_conditions": ["margin guide-down"],
        "resolution_source": "total_return_dataset",
        "review_date": "2026-12-31",
        "status": "open",
    }
    ledger = forecast_dir / "forecast-ledger.jsonl"
    ledger.write_text(json.dumps(forecast_record) + "\n", encoding="utf-8")
    ledger_quality = json.loads(run(["./tcx", "quality-check", "trading/forecasts/forecast-ledger.jsonl", "--strict"], workspace).stdout)
    assert ledger_quality["status"] == "pass"
    bad_ledger = forecast_dir / "bad-ledger.jsonl"
    bad_record = {**forecast_record, "forecast_id": "fcst_bad", "probability": 0.9}
    bad_ledger.write_text(json.dumps(bad_record) + "\n", encoding="utf-8")
    bad_ledger_quality = json.loads(run(["./tcx", "quality-check", "trading/forecasts/bad-ledger.jsonl", "--strict"], workspace, expect_ok=False).stdout)
    assert bad_ledger_quality["status"] == "fail"
    assert any("probability must fall inside probability_range" in warning for warning in bad_ledger_quality["warnings"])
    decision_artifact = workspace / "trading" / "reports" / "valuation" / "decision-quality.md"
    decision_artifact.parent.mkdir(parents=True, exist_ok=True)
    decision_artifact.write_text(
        """---
artifact_id: valuation-nvda
artifact_type: valuation
role: valuation-analyst
title: NVDA valuation
source_as_of: "2026-06-01"
readiness_label: not-decision-ready
context_summary: Forecast blocked by missing base rate.
reader_summary: Forecast not decision-ready.
next_action: Gather current source support.
handoff_state: accepted
confidence: low
next_recipient: head-manager
missing_evidence: [base rate]
blocked_actions: [order_drafting]
source_snapshot_ids: [unit-test-filing]
workflow_lane: thesis_review_then_portfolio_risk_review
forecast_required: true
forecast_allowed: false
forecast_block_reason: missing base rate
missing_base_rate_note: base-rate evidence not available yet
scenario_cases: [bull, base, bear]
contrary_evidence: [valuation embeds high growth]
update_triggers: [new guidance]
invalidation_conditions: [margin guide-down]
current_price_as_of: "2026-06-01"
---

[factual] Source posture exists.
""",
        encoding="utf-8",
    )
    decision_quality = evaluate_decision_quality(workspace, "trading/reports/valuation/decision-quality.md", "thesis_review_then_portfolio_risk_review")
    assert decision_quality["status"] == "pass"
    missing_base_rate_artifact = workspace / "trading" / "reports" / "valuation" / "missing-base-rate.md"
    missing_base_rate_artifact.write_text(
        decision_artifact.read_text(encoding="utf-8")
        .replace("readiness_label: not-decision-ready", "readiness_label: ready-for-portfolio-risk")
        .replace(
            "forecast_allowed: false\nforecast_block_reason: missing base rate\nmissing_base_rate_note: base-rate evidence not available yet\n",
            (
                "forecast_allowed: true\n"
                "forecast_target: NVDA total return exceeds benchmark by 10 percentage points\n"
                "forecast_horizon: \"2026-12-31\"\n"
                "probability: 0.35\n"
                "evidence_ids: [unit-test-filing]\n"
                "resolution_source: total_return_dataset\n"
                "review_date: \"2026-12-31\"\n"
            ),
        ),
        encoding="utf-8",
    )
    missing_base_rate_quality = evaluate_decision_quality(workspace, "trading/reports/valuation/missing-base-rate.md", "thesis_review_then_portfolio_risk_review")
    assert missing_base_rate_quality["status"] == "fail"
    assert "decision_quality.base_rate" in missing_base_rate_quality["required_fields_missing"]
    searched = call_mcp_tool(workspace, "search_research_artifacts", {"query": "gross margin"})
    assert any(item["artifact_id"] == "nvda-evidence-1" for item in searched["artifacts"])
    from apps.mcp.models import McpToolCall, McpToolDefinition

    assert McpToolDefinition.objects.filter(name="create_research_artifact", category="research").exists()
    assert not McpToolCall.objects.filter(tool_name__in=["create_research_artifact", "get_research_artifact", "search_research_artifacts"]).exists()
    snapshot = call_mcp_tool(workspace, "record_source_snapshot", {
        "provider": "unit-test",
        "source_category": "filing",
        "as_of": "2026-06-01",
        "artifact_id": "nvda-evidence-1",
        "warnings": ["stale after 7 days"],
        "payload": {"url": "https://example.test/nvda"},
    })
    assert snapshot["db_canonical"] is False
    assert snapshot["file_sot"] is True
    assert snapshot["export_path"].startswith("trading/research/source-snapshots/")
    assert (workspace / snapshot["export_path"]).exists()
    forbidden = handle_mcp_rpc(workspace, {
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
                "reader_summary": "BTC note is a research-only trend read.",
                "next_action": "Keep it out of order flow until portfolio/risk review.",
                "created_by": "instrument-analyst",
            }),
            content_type="application/json",
        )
        assert response.status_code == 200
        assert response.json()["artifact_id"] == "btc-note-1"
        assert response.json()["file_sot"] is True
        api_fetched = client.get("/api/research/artifacts/btc-note-1").json()
        assert api_fetched["universe"] == "public_crypto"
        assert api_fetched["reader_summary"] == "BTC note is a research-only trend read."
        assert api_fetched["next_action"] == "Keep it out of order flow until portfolio/risk review."
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

    note_path = workspace / "trading" / "research" / "note.md"
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
        "trading/research/note.md",
        "--symbol",
        "AAPL",
        "--source-as-of",
        "2026-06-02",
        "--reader-summary",
        "AAPL CLI note is factual context only.",
        "--next-action",
        "Send to head-manager synthesis, not order drafting.",
    ], workspace).stdout)
    assert cli_stored["db_canonical"] is False
    assert cli_stored["file_sot"] is True
    assert (workspace / cli_stored["export_path"]).exists()
    cli_export = (workspace / cli_stored["export_path"]).read_text(encoding="utf-8")
    assert 'source_as_of: "2026-06-02"' in cli_export
    assert 'reader_summary: "AAPL CLI note is factual context only."' in cli_export
    assert 'next_action: "Send to head-manager synthesis, not order drafting."' in cli_export
    cli_fetched = json.loads(run(["./tcx", "research", "get", "cli-note-1"], workspace).stdout)
    assert cli_fetched["artifact_id"] == "cli-note-1"
    assert cli_fetched["file_sot"] is True
    assert "updated_at" in cli_fetched
    frontmatter_cli_path = workspace / "trading" / "research" / "frontmatter-cli-note.md"
    frontmatter_cli_path.write_text(
        "---\nartifact_id: frontmatter-cli-note\ntitle: Frontmatter CLI Note\n---\n\n# Frontmatter CLI Body\n",
        encoding="utf-8",
    )
    frontmatter_cli_stored = json.loads(run([
        "./tcx",
        "research",
        "create",
        "--markdown-file",
        "trading/research/frontmatter-cli-note.md",
        "--created-by",
        "fundamental-analyst",
    ], workspace).stdout)
    assert frontmatter_cli_stored["artifact_id"] == "frontmatter-cli-note"
    assert json.loads(run(["./tcx", "research", "get", "frontmatter-cli-note"], workspace).stdout)["title"] == "Frontmatter CLI Note"

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
        "--reader-summary",
        "MSFT MCP CLI note is evidence for non-expert first read.",
        "--next-action",
        "Use as support evidence only.",
    ], workspace).stdout)
    assert mcp_cli_stored["db_canonical"] is False
    assert mcp_cli_stored["file_sot"] is True
    mcp_cli_fetched = json.loads(run(["./tcx", "research", "get", "mcp-cli-note-1"], workspace).stdout)
    assert mcp_cli_fetched["reader_summary"] == "MSFT MCP CLI note is evidence for non-expert first read."
    assert mcp_cli_fetched["next_action"] == "Use as support evidence only."
    assert not McpToolCall.objects.filter(tool_name="create_research_artifact", principal_id="fundamental-analyst", status="ok").exists()
    mcp_cli_snapshot = json.loads(run([
        "./tcx",
        "mcp",
        "call",
        "record_source_snapshot",
        "--principal",
        "fundamental-analyst",
        "--provider",
        "cli-test",
        "--source-category",
        "filing",
        "--as-of",
        "2026-06-12",
        "--artifact-id",
        "mcp-cli-note-1",
        "--payload",
        '{"url":"https://example.test/source"}',
        "--warnings",
        '["stale after 7 days"]',
    ], workspace).stdout)
    assert mcp_cli_snapshot["provider"] == "cli-test"
    assert mcp_cli_snapshot["source_category"] == "filing"
    assert mcp_cli_snapshot["db_canonical"] is False
    assert mcp_cli_snapshot["file_sot"] is True
    assert (workspace / mcp_cli_snapshot["export_path"]).exists()
    assert not McpToolCall.objects.filter(tool_name="record_source_snapshot", principal_id="fundamental-analyst", status="ok").exists()
    mcp_help = run(["./tcx", "mcp", "--help"], workspace).stdout
    assert "create_research_artifact" in mcp_help
    assert "mcp external" in mcp_help
    assert "mcp ledger" in mcp_help
    external_list = json.loads(run(["./tcx", "mcp", "external", "list"], workspace).stdout)
    assert isinstance(external_list["connections"], list)
    assert external_list["db_canonical"] is True
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
    assert ledger["count"] == 0
    assert ledger["calls"] == []
    assert ledger["central_ledger"] is True


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
    manifest_a = json.loads((workspace_a / ".tradingcodex" / "workspace.json").read_text(encoding="utf-8"))
    manifest_b = json.loads((workspace_b / ".tradingcodex" / "workspace.json").read_text(encoding="utf-8"))
    assert manifest_a["workspace_id"] != manifest_b["workspace_id"]
    artifact_id = f"central-shared-note-{manifest_a['workspace_id'][-8:]}"
    order_id = f"central-cross-workspace-order-{manifest_a['workspace_id'][-8:]}"

    note = workspace_a / "trading" / "research" / "shared-note.md"
    note.write_text("# Shared Note\n\n[factual] Workspace research is local to workspace A.", encoding="utf-8")
    created = json.loads(run([
        "./tcx",
        "research",
        "create",
        "--id",
        artifact_id,
        "--title",
        "Central Shared Note",
        "--markdown-file",
        "trading/research/shared-note.md",
        "--symbol",
        "AAPL",
    ], workspace_a).stdout)
    assert created["db_canonical"] is False
    assert created["file_sot"] is True
    assert created["workspace_context"]["path"] == str(workspace_a)

    searched = json.loads(run(["./tcx", "research", "search", "Workspace research"], workspace_b).stdout)
    assert not any(item["artifact_id"] == artifact_id for item in searched["artifacts"])

    conflicting_note = workspace_b / "trading" / "research" / "shared-note-conflict.md"
    conflicting_note.write_text("# Shared Note\n\n[factual] Same artifact id in workspace B is a separate file-native artifact.", encoding="utf-8")
    duplicate = json.loads(run([
        "./tcx",
        "research",
        "create",
        "--id",
        artifact_id,
        "--title",
        "Central Shared Note Conflict",
        "--markdown-file",
        "trading/research/shared-note-conflict.md",
    ], workspace_b).stdout)
    assert duplicate["artifact_id"] == artifact_id
    assert duplicate["workspace_context"]["path"] == str(workspace_b)
    assert duplicate["file_sot"] is True

    appended_note = workspace_b / "trading" / "research" / "shared-note-update.md"
    appended_note.write_text("# Shared Note Update\n\n[factual] Explicit version append from workspace B.", encoding="utf-8")
    appended = json.loads(run(["./tcx", "research", "append", artifact_id, "--markdown-file", "trading/research/shared-note-update.md"], workspace_b).stdout)
    assert appended["version"] == 2
    assert appended["workspace_context"]["path"] == str(workspace_b)

    assert json.loads(run(["./tcx", "mcp", "call", "create_order_ticket", "--principal", "portfolio-manager", "--ticket-id", order_id, "--symbol", "AAPL", "--side", "buy", "--quantity", "1", "--limit-price", "1000"], workspace_a).stdout)["status"] == "created"
    assert json.loads(run(["./tcx", "approve", order_id, "--approved-by", "risk-manager"], workspace_a).stdout)["status"] == "approved"
    order_conflict = run(["./tcx", "mcp", "call", "create_order_ticket", "--principal", "portfolio-manager", "--ticket-id", order_id, "--symbol", "AAPL", "--side", "buy", "--quantity", "2", "--limit-price", "1000"], workspace_b, expect_ok=False)
    assert "order ticket cannot be mutated after approval or submission" in order_conflict.stderr
    executed = json.loads(run(["./tcx", "mcp", "call", "submit_approved_order", "--ticket-id", order_id], workspace_b).stdout)
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

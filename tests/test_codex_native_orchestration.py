from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from tradingcodex_cli.generator import bootstrap_workspace
from tradingcodex_service.application.agents import AGENT_SPECS, EXPECTED_SUBAGENTS
from tradingcodex_service.application.analysis_runs import begin_analysis_run, read_analysis_run
from tradingcodex_service.application.runtime import ensure_workspace_manifest
from tradingcodex_service.mcp_runtime import TOOL_REGISTRY, call_mcp_tool


ROOT = Path(__file__).resolve().parents[1]
RETIRED_TOOLS = {
    "record_workflow_intake",
    "record_workflow_plan",
    "record_artifact_supervisor_loop",
}
ARTIFACT_DISCOVERY_TOOLS = frozenset(
    {
        "list_workflow_artifacts",
        "list_research_artifacts",
        "search_research_artifacts",
        "list_artifact_catalog",
        "search_artifact_catalog",
    }
)
FIXED_ROLE_MODEL_INSTRUCTIONS = "../prompts/base_instructions/fixed-role.md"
@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    bootstrap_workspace(root)
    return root


def test_korean_request_creates_only_lightweight_analysis_provenance(tmp_path: Path) -> None:
    ensure_workspace_manifest(tmp_path)
    run = begin_analysis_run(
        tmp_path,
        "월요일 국장 예상해봐",
        run_id="analysis-korean-market",
        apply_investor_context=False,
    )

    assert read_analysis_run(tmp_path, run["workflow_run_id"]) == run
    assert run["marker"] == "tradingcodex-analysis-run"
    assert run["orchestration_owner"] == "codex-head-manager"
    assert run["service_authority"] == "persistence-policy-execution"
    assert run["request_sha256"]
    assert "월요일" not in json.dumps(run, ensure_ascii=False)
    assert {"lane", "selected_team", "plan", "stages", "pending_tasks"}.isdisjoint(run)


def test_artifact_discovery_is_head_managed_and_fixed_roles_keep_exact_reads() -> None:
    fixed_roles = set(EXPECTED_SUBAGENTS)
    head_tools = set(AGENT_SPECS["head-manager"].mcp_allowlist)
    assert "list_research_artifacts" in head_tools
    assert "get_research_artifact" in head_tools
    assert "head-manager" in TOOL_REGISTRY["list_research_artifacts"].allowed_roles

    for role in fixed_roles:
        role_tools = set(AGENT_SPECS[role].mcp_allowlist)
        assert "get_research_artifact" in role_tools
        assert ARTIFACT_DISCOVERY_TOOLS.isdisjoint(role_tools)

    for tool_name in ARTIFACT_DISCOVERY_TOOLS:
        assert fixed_roles.isdisjoint(TOOL_REGISTRY[tool_name].allowed_roles)

    role_templates = (
        ROOT / "workspace_templates/modules/fixed-subagents/files/.codex/agents"
    )
    for role in fixed_roles:
        role_text = (role_templates / f"{role}.toml").read_text(encoding="utf-8")
        enabled_line = next(
            line for line in role_text.splitlines() if line.startswith("enabled_tools = ")
        )
        enabled = set(json.loads(enabled_line.partition(" = ")[2]))
        assert "get_research_artifact" in enabled
        assert ARTIFACT_DISCOVERY_TOOLS.isdisjoint(enabled)

    numeric_roles = {
        "fundamental-analyst",
        "technical-analyst",
        "macro-analyst",
        "valuation-analyst",
        "portfolio-manager",
        "risk-manager",
    }
    retained_discovery = {
        "search_datasets",
        "get_dataset_manifest",
        "profile_dataset",
        "search_calculations",
        "get_calculation_run",
        "compare_calculation_runs",
    }
    for role in numeric_roles:
        assert retained_discovery.issubset(AGENT_SPECS[role].mcp_allowlist)


def test_mcp_surface_has_one_lightweight_run_tool_and_no_server_orchestrator() -> None:
    assert "begin_analysis_run" in TOOL_REGISTRY
    assert not RETIRED_TOOLS.intersection(TOOL_REGISTRY)
    tool = TOOL_REGISTRY["begin_analysis_run"]
    assert tool.allowed_roles == frozenset({"head-manager"})
    assert tool.input_schema["required"] == ["request"]
    assert "structured_intent" not in tool.input_schema["properties"]

    artifact_properties = TOOL_REGISTRY["create_research_artifact"].input_schema[
        "properties"
    ]
    follow_up_item = artifact_properties["follow_up_requests"]["items"]
    assert follow_up_item["type"] == "object"
    assert follow_up_item["additionalProperties"] is False
    assert set(follow_up_item["required"]) == {
        "trigger",
        "suggested_role",
        "question",
        "reason",
        "materiality",
    }
    improvement_item = artifact_properties["improvements"]["items"]
    assert improvement_item["type"] == "object"
    assert improvement_item["additionalProperties"] is False
    lifecycle = artifact_properties["thesis_lifecycle"]
    assert lifecycle["required"] == ["state"]
    assert lifecycle["properties"]["state"]["enum"] == [
        "exploring",
        "testing",
        "validated",
        "rejected",
        "monitoring",
    ]
    assert {"monitoring_artifact", "review_cadence"} <= set(lifecycle["properties"])
    forecast_properties = TOOL_REGISTRY["issue_forecast"].input_schema["properties"]
    assert forecast_properties["horizon"]["format"] == "date-time"
    assert forecast_properties["issued_at"]["description"].startswith("Optional")
    assert forecast_properties["base_rate"]["required"] == [
        "cohort",
        "source_snapshot_id",
        "sample_size",
        "selection_rule",
    ]


def test_authenticated_artifacts_bind_run_local_lineage(tmp_path: Path) -> None:
    ensure_workspace_manifest(tmp_path)
    run_id = "analysis-lineage"
    begin_analysis_run(tmp_path, "Analyze ACME facts.", run_id=run_id, apply_investor_context=False)
    producer = call_mcp_tool(
        tmp_path,
        "create_research_artifact",
        {
            "artifact_id": "acme-facts",
            "artifact_type": "research_memo",
            "universe": "public_equity",
            "workflow_type": "factual_profile",
            "title": "ACME facts",
            "markdown": "# ACME facts\n\n[factual] ACME is the subject of this bounded fixture.\n",
            "source_as_of": "2026-07-12",
            "readiness_label": "factual-baseline",
            "context_summary": "Bounded ACME facts.",
            "reader_summary": "ACME fixture facts.",
            "handoff_state": "accepted",
            "confidence": "high",
            "missing_evidence": [],
            "next_recipient": "head-manager",
            "next_action": "Head Manager review.",
            "blocked_actions": ["order", "execution"],
            "source_snapshot_ids": [],
            "workflow_run_id": run_id,
            "input_artifact_ids": [],
        },
        transport_principal="fundamental-analyst",
    )
    synthesis = call_mcp_tool(
        tmp_path,
        "create_research_artifact",
        {
            "artifact_id": "acme-synthesis",
            "artifact_type": "synthesis_report",
            "universe": "public_equity",
            "workflow_type": "factual_profile",
            "title": "ACME synthesis",
            "markdown": "# ACME synthesis\n\n[factual] This synthesis consumes the role artifact.\n",
            "source_as_of": "2026-07-12",
            "readiness_label": "accepted",
            "context_summary": "Synthesis of ACME facts.",
            "reader_summary": "ACME synthesis.",
            "handoff_state": "accepted",
            "confidence": "high",
            "missing_evidence": [],
            "next_recipient": "user",
            "next_action": "No action requested.",
            "blocked_actions": ["order", "execution"],
            "source_snapshot_ids": [],
            "workflow_run_id": run_id,
            "input_artifact_ids": ["acme-facts"],
        },
        transport_principal="head-manager",
    )
    stored = call_mcp_tool(
        tmp_path,
        "get_research_artifact",
        {"artifact_id": "acme-synthesis", "include_markdown": False},
        transport_principal="head-manager",
    )
    assert synthesis["status"] == "stored"
    assert stored["input_artifact_ids"] == ["acme-facts"]
    assert stored["input_artifact_hashes"] == {"acme-facts": producer["content_hash"]}

    with pytest.raises(ValueError, match="plan_hash, stage_id, or task_id"):
        call_mcp_tool(
            tmp_path,
            "create_research_artifact",
            {
                "artifact_id": "old-binding",
                "universe": "public_equity",
                "title": "Old binding",
                "markdown": "# Old binding\n",
                "workflow_run_id": run_id,
                "plan_hash": "a" * 64,
            },
            transport_principal="fundamental-analyst",
        )


def test_generated_hook_is_transport_only_and_checks_exact_v2_spawn(workspace: Path) -> None:
    hook = workspace / ".codex/hooks/tradingcodex_hook.py"
    source = hook.read_text(encoding="utf-8")
    assert "begin_analysis_run" in source
    assert "classify_starter_request" not in source
    assert "record_workflow_plan" not in source
    assert "dispatch_tasks_for_state" not in source
    assert "fork_turns=none" in source

    prompt = subprocess.run(
        [sys.executable, str(hook), "user-prompt-submit"],
        cwd=workspace,
        input=json.dumps({"prompt": "월요일 국장 예상해봐"}),
        text=True,
        capture_output=True,
        env={**os.environ, "PYTHONPATH": str(ROOT)},
        check=True,
    )
    context = json.loads(json.loads(prompt.stdout)["hookSpecificOutput"]["additionalContext"])
    assert context["orchestration_owner"] == "codex-head-manager"
    assert context["run_start_tool"] == "begin_analysis_run"
    assert "lane" not in context
    assert "selected_team" not in context

    run = begin_analysis_run(
        workspace,
        "Analyze ACME company facts only. No valuation, order, or execution.",
        run_id="analysis-native-v2-hook",
        apply_investor_context=False,
    )

    def pre_spawn(tool_input: dict[str, object]) -> dict[str, object] | None:
        result = subprocess.run(
            [sys.executable, str(hook), "pre-tool-use"],
            cwd=workspace,
            input=json.dumps({
                "tool_name": "agentsspawn_agent",
                "workflow_run_id": run["workflow_run_id"],
                "tool_input": tool_input,
            }),
            text=True,
            capture_output=True,
            env={**os.environ, "PYTHONPATH": str(ROOT)},
            check=True,
        )
        return json.loads(result.stdout) if result.stdout else None

    valid_input = {
        "agent_type": "fundamental-analyst",
        "task_name": "acme_company_facts",
        "fork_turns": "none",
        "message": f"Run {run['workflow_run_id']}: return only role readiness.",
    }
    assert pre_spawn(valid_input) is None
    audit_events = [
        json.loads(line)
        for line in (workspace / "trading/audit/codex-hooks.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    dispatch_audit = next(
        item
        for item in reversed(audit_events)
        if item.get("event") == "pre-tool-use" and item.get("tool_name") == "agentsspawn_agent"
    )
    assert dispatch_audit["decision"] == "allow"
    assert dispatch_audit["agent_type"] == "fundamental-analyst"
    assert dispatch_audit["task_name"] == "acme_company_facts"
    assert dispatch_audit["fork_turns"] == "none"
    assert dispatch_audit["message_sha256"] == hashlib.sha256(valid_input["message"].encode("utf-8")).hexdigest()
    assert dispatch_audit["message_bytes"] == len(valid_input["message"].encode("utf-8"))
    assert "message" not in dispatch_audit
    assert pre_spawn({**valid_input, "agent_type": "default"})["decision"] == "block"
    assert pre_spawn({**valid_input, "fork_turns": "all"})["decision"] == "block"
    assert pre_spawn({**valid_input, "task_name": "ACME facts"})["decision"] == "block"
    for override in (
        {"reasoning_effort": "high"},
        {"model": "gpt-5.6-terra"},
        {"sandbox_mode": "read-only"},
    ):
        blocked = pre_spawn({**valid_input, **override})
        assert blocked["decision"] == "block"
        assert "overrides are forbidden" in blocked["reason"]


def test_generated_contract_inherits_models_and_keeps_role_profiles_optional(workspace: Path) -> None:
    config = (workspace / ".codex/config.toml").read_text(encoding="utf-8")
    head = (workspace / ".codex/prompts/base_instructions/head-manager.md").read_text(encoding="utf-8")
    fixed_role_path = workspace / ".codex/prompts/base_instructions/fixed-role.md"
    assert fixed_role_path.is_file()
    fixed_role = fixed_role_path.read_text(encoding="utf-8")
    role = (workspace / ".codex/agents/fundamental-analyst.toml").read_text(encoding="utf-8")
    skill = (workspace / ".agents/skills/tcx-workflow/SKILL.md").read_text(encoding="utf-8")
    assert "Model and reasoning settings inherit the user's Codex defaults" in config
    assert "model =" not in config
    assert "model_reasoning_effort" not in config
    assert not (workspace / ".tradingcodex/generated/model-policy-manifest.json").exists()
    assert 'model_instructions_file = "prompts/base_instructions/head-manager.md"' in config
    assert '[features.multi_agent_v2]' in config
    assert 'enabled = true' in config
    assert 'max_concurrent_threads_per_session = 7' in config
    assert 'max_threads = 6' not in config
    assert "required = true" in config
    assert '"begin_analysis_run"' in config
    assert "record_workflow_plan" not in config
    assert "record_artifact_supervisor_loop" not in config
    assert "model =" not in role
    assert "model_reasoning_effort" not in role
    assert "required = true" in role
    assert len(fixed_role.encode("utf-8")) <= 7_000
    assert len(fixed_role.encode("utf-8")) < len(head.encode("utf-8")) // 4
    assert "You are the `head-manager` agent" not in fixed_role
    assert "# Role And Safety" in fixed_role
    assert "authenticated TradingCodex MCP" in fixed_role
    assert "depth-1 child" in fixed_role
    assert "never spawn, coordinate" in fixed_role
    assert "$tcx-source-gate" in fixed_role
    assert "Snapshot/Dataset/Artifact IDs" in fixed_role
    assert "do not duplicate or invent provider policy here" in fixed_role
    assert "at most one targeted correction" in fixed_role
    assert "Django workflow plan" in head
    assert "server-generated DAG" in head
    assert "Answer narrow factual questions and simple recorded-status requests directly" in head
    assert "Use `followup_task` to correct or clarify" in head
    assert "generic fallback" in head
    assert "wait_agent` timeout alone is not a reason to message" in head
    assert "## Fast Path" in skill
    assert "Otherwise a generic child may" in skill
    assert "followup_task" in skill
    assert "Save an authenticated research artifact when the result will support" in skill
    assert "$tcx-source-gate" in skill
    assert "optional direct OpenBB" in skill
    assert "ALL_TOOLS.filter" not in head + fixed_role + skill
    assert "record_workflow_plan" not in head + role + skill

    roles_root = workspace / ".codex/agents"
    for role_path in roles_root.glob("*.toml"):
        role_instructions = role_path.read_text(encoding="utf-8")
        role_config = tomllib.loads(role_instructions)
        enabled_tools = set(role_config["mcp_servers"]["tradingcodex"]["enabled_tools"])
        assert "get_research_artifact" in enabled_tools
        assert ARTIFACT_DISCOVERY_TOOLS.isdisjoint(enabled_tools)
        assert role_config["model_instructions_file"] == FIXED_ROLE_MODEL_INSTRUCTIONS
        resolved_instructions = role_path.parent / role_config["model_instructions_file"]
        assert resolved_instructions.resolve() == fixed_role_path.resolve()
        assert resolved_instructions.is_file()
        assert "never send a date-only value" in role_instructions
        assert "Never use end-of-day or another future time" in role_instructions


def test_doctor_requires_fixed_role_base_and_per_role_override(workspace: Path) -> None:
    doctor = subprocess.run(
        [str(workspace / "tcx"), "doctor", "--verbose"],
        cwd=workspace,
        text=True,
        capture_output=True,
        check=False,
    )
    assert doctor.returncode == 0, doctor.stdout + doctor.stderr
    assert "fixed-role base instructions installed" in doctor.stdout
    for role in (
        "fundamental-analyst",
        "technical-analyst",
        "news-analyst",
        "macro-analyst",
        "instrument-analyst",
        "valuation-analyst",
        "portfolio-manager",
        "risk-manager",
        "judgment-reviewer",
    ):
        assert f"subagent compact base configured: {role}" in doctor.stdout

from __future__ import annotations

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
from tradingcodex_service.mcp_runtime import (
    RESEARCH_ARTIFACT_METADATA_FIELDS,
    TOOL_REGISTRY,
    call_mcp_tool,
)


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


def test_fixed_role_prompts_use_natural_evidence_distinctions() -> None:
    base = (
        ROOT
        / "workspace_templates/modules/codex-base/files/.codex/prompts/base_instructions/fixed-role.md"
    ).read_text(encoding="utf-8")
    role_templates = ROOT / "workspace_templates/modules/fixed-subagents/files/.codex/agents"
    role_prompts = [path.read_text(encoding="utf-8") for path in role_templates.glob("*.toml")]
    role_skill_root = (
        ROOT
        / "workspace_templates/modules/repo-skills/files/.tradingcodex/subagents/skills"
    )
    role_skills = [
        path.read_text(encoding="utf-8") for path in role_skill_root.rglob("SKILL.md")
    ]

    assert "natural prose" in base
    for prompt in [base, *role_prompts, *role_skills]:
        assert "[factual]" not in prompt
        assert "[inference]" not in prompt
        assert "[assumption]" not in prompt
    for prompt in role_prompts:
        assert "Instruction preservation:" not in prompt
        assert "Artifact handoff:" not in prompt
        assert "Narrative evidence discipline:" not in prompt

    workflow = (ROOT / "workspace_templates/modules/repo-skills/files/.agents/skills/tcx-workflow/SKILL.md").read_text(encoding="utf-8")
    flat_workflow = " ".join(workflow.split())
    assert "verified facts and sources, analysis and implications, key" in workflow
    assert "Do not require per-sentence tags" in " ".join(workflow.split())
    assert "relevant base\n  rate or comparison" in workflow
    assert "current action/readiness\n  limit" in workflow
    assert "Frame research as a provisional causal map, not a request-shaped checklist" in workflow
    assert "outside-in peers, substitutes, industry structure" in workflow
    assert "upstream inputs and suppliers, downstream customers" in workflow
    assert "These are examples, not mandatory coverage" in workflow
    assert "Preserve the requested outcome, explicit scope, and exclusions" in flat_workflow
    assert "important points the user may not know to ask about" in flat_workflow
    assert "never widen the outcome or action authority" in flat_workflow
    assert "Find the causal cruxes" in flat_workflow
    assert "Route unresolved cruxes rather than a preset role checklist" in flat_workflow
    assert "Each accepted result may confirm or weaken a link" in flat_workflow
    assert "when another specialty owns the unknown" in flat_workflow
    assert "exact artifact ID, causal question, and missing evidence" in flat_workflow
    assert "structural from cyclical, leading from lagging" in flat_workflow
    assert "Compare live explanations and prefer evidence that distinguishes them" in flat_workflow
    assert "Judge corroboration by independence, diagnostic value, and position in the causal chain" in flat_workflow
    assert "dependent repetition is not confirmation" in flat_workflow
    assert "Turn each material uncertainty into an observable update" in flat_workflow
    assert "fundamentally unknowable ones" in flat_workflow
    assert "Research quality and decision relevance take priority over resource economy" in flat_workflow
    assert "Tool-call count, context size, and latency alone are not stop conditions" in flat_workflow
    assert "deduplicated calls, compact artifact handoffs" in flat_workflow
    assert "explicit user scope or deadline requires it" in flat_workflow


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
        assert "tcx-calculation" in AGENT_SPECS[role].builtin_skills
    for role in fixed_roles - numeric_roles:
        assert "tcx-calculation" not in AGENT_SPECS[role].builtin_skills


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
    append_properties = TOOL_REGISTRY["append_research_artifact_version"].input_schema[
        "properties"
    ]
    assert set(RESEARCH_ARTIFACT_METADATA_FIELDS) <= set(artifact_properties)
    assert set(RESEARCH_ARTIFACT_METADATA_FIELDS) <= set(append_properties)
    artifact_schema = TOOL_REGISTRY["create_research_artifact"].input_schema
    assert artifact_schema["$defs"]["antiOverfitCheck"]["required"] == [
        "status",
        "reason",
        "evidence_refs",
    ]
    assert artifact_properties["anti_overfit_checks"]["properties"]["leakage"] == {
        "$ref": "#/$defs/antiOverfitCheck"
    }
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

    standard_annotations = {
        "title",
        "readOnlyHint",
        "destructiveHint",
        "idempotentHint",
        "openWorldHint",
    }
    assert set(tool.public_definition()["annotations"]) == standard_annotations
    assert len(
        json.dumps(
            TOOL_REGISTRY["create_research_artifact"].public_definition(),
            separators=(",", ":"),
        )
    ) < 9_000


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


def test_generated_hook_leaves_native_child_lifecycle_to_codex(workspace: Path) -> None:
    hook = workspace / ".codex/hooks/tradingcodex_hook.py"
    source = hook.read_text(encoding="utf-8")
    hooks = json.loads((workspace / ".codex/hooks.json").read_text(encoding="utf-8"))["hooks"]
    assert set(hooks) == {"SessionStart", "UserPromptSubmit", "PreToolUse", "PermissionRequest", "Stop"}
    assert "begin_analysis_run" in source
    assert "classify_starter_request" not in source
    assert "record_workflow_plan" not in source
    assert "dispatch_tasks_for_state" not in source
    assert "native_codex_transport_normalized" not in source

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
    assert "run_status" not in context
    assert "workflow_run_id" not in context
    assert "reuse its existing workflow_run_id" in context["planning_instruction"]
    assert "lane" not in context
    assert "selected_team" not in context
    assert not (workspace / ".tradingcodex/mainagent/session-workflow-runs.json").exists()

    def pre_spawn(tool_input: dict[str, object]) -> dict[str, object] | None:
        result = subprocess.run(
            [sys.executable, str(hook), "pre-tool-use"],
            cwd=workspace,
            input=json.dumps({
                "tool_name": "agentsspawn_agent",
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
        "message": "Return only role readiness.",
    }
    assert pre_spawn(valid_input) is None
    assert pre_spawn({**valid_input, "agent_type": "default"}) is None
    assert pre_spawn({**valid_input, "fork_turns": "all"}) is None
    assert pre_spawn({**valid_input, "task_name": "ACME facts"}) is None
    for override in (
        {"reasoning_effort": "high"},
        {"model": "gpt-5.6-terra"},
        {"sandbox_mode": "read-only"},
    ):
        assert pre_spawn({**valid_input, **override}) is None

    followup_message = "Clarify whether the periods and schemas align."
    followup_result = subprocess.run(
        [sys.executable, str(hook), "pre-tool-use"],
        cwd=workspace,
        input=json.dumps({
            "tool_name": "agentsfollowup_task",
            "tool_input": {
                "target": "technical_evidence",
                "message": followup_message,
            },
        }),
        text=True,
        capture_output=True,
        env={**os.environ, "PYTHONPATH": str(ROOT)},
        check=True,
    )
    assert followup_result.stdout == ""
    assert not (workspace / "trading/audit/codex-hooks.jsonl").exists()


def test_generated_contract_pins_models_and_keeps_role_profiles_optional(workspace: Path) -> None:
    config = (workspace / ".codex/config.toml").read_text(encoding="utf-8")
    head = (workspace / ".codex/prompts/base_instructions/head-manager.md").read_text(encoding="utf-8")
    fixed_role_path = workspace / ".codex/prompts/base_instructions/fixed-role.md"
    assert fixed_role_path.is_file()
    fixed_role = fixed_role_path.read_text(encoding="utf-8")
    role = (workspace / ".codex/agents/fundamental-analyst.toml").read_text(encoding="utf-8")
    skill = (workspace / ".agents/skills/tcx-workflow/SKILL.md").read_text(encoding="utf-8")
    flat_head = " ".join(head.split())
    flat_skill = " ".join(skill.split())
    config_values = tomllib.loads(config)
    assert config_values["model"] == "gpt-5.6-sol"
    assert config_values["model_reasoning_effort"] == "xhigh"
    assert not (workspace / ".tradingcodex/generated/model-policy-manifest.json").exists()
    assert 'model_instructions_file = "prompts/base_instructions/head-manager.md"' in config
    assert '[features.multi_agent_v2]' in config
    assert 'enabled = true' in config
    assert "max_concurrent_threads_per_session" not in config
    assert "max_depth = 1" in config
    assert 'max_threads = 6' not in config
    assert "required = true" in config
    assert '"begin_analysis_run"' in config
    assert "record_workflow_plan" not in config
    assert "record_artifact_supervisor_loop" not in config
    assert tomllib.loads(role)["model"] == "gpt-5.6-terra"
    assert tomllib.loads(role)["model_reasoning_effort"] == "high"
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
    assert "Django workflow plan" in head
    assert "server-generated DAG" in head
    assert "Answer narrow" in head
    assert "Load\n`$tcx-workflow`" in head
    assert "research-framing" in head
    assert "Use `followup_task` to correct or clarify" in head
    assert "$tcx-workflow` before using any fallback" in head
    assert len(head.encode("utf-8")) <= 12_000
    assert "causal crux" not in flat_head.lower()
    assert "dependent repetition" not in flat_head.lower()
    assert '`fork_turns="none"`' not in head
    assert "Native wait may be targetless" not in flat_head
    assert "## Fast Path" in skill
    assert "Only an unavailable\n   evidence-producing role may use a generic child" in skill
    assert "Do not replace an independent\n   `risk-manager` or `judgment-reviewer` review" in skill
    assert "followup_task" in skill
    assert '`fork_turns="none"`' in skill
    assert "let its role TOML supply\n   the fixed model settings" in skill
    assert "native wait may be targetless because it waits for any child" in flat_skill.lower()
    assert "child-lifecycle results in this run" in flat_skill
    assert "Save an authenticated research artifact when external evidence changes" in skill
    assert "$tcx-source-gate" in skill
    assert "current-workflow Snapshot/Dataset candidates" in skill
    assert "order/execution readiness" in skill
    assert len(skill.encode("utf-8")) <= 10_000
    assert "provisional causal map" in flat_skill
    assert "coverage is underspecified" in flat_skill
    assert "causal cruxes" in flat_skill
    assert "dependent repetition is not confirmation" in flat_skill
    assert "likely to change the answer or readiness" in flat_skill
    assert "ALL_TOOLS.filter" not in head + fixed_role + skill
    assert "record_workflow_plan" not in head + role + skill

    roles_root = workspace / ".codex/agents"
    for role_path in roles_root.glob("*.toml"):
        role_instructions = role_path.read_text(encoding="utf-8")
        role_config = tomllib.loads(role_instructions)
        enabled_tools = set(role_config["mcp_servers"]["tradingcodex"]["enabled_tools"])
        assert role_config["model"] == "gpt-5.6-terra"
        assert role_config["model_reasoning_effort"] == "high"
        assert "get_research_artifact" in enabled_tools
        assert ARTIFACT_DISCOVERY_TOOLS.isdisjoint(enabled_tools)
        assert role_config["model_instructions_file"] == FIXED_ROLE_MODEL_INSTRUCTIONS
        resolved_instructions = role_path.parent / role_config["model_instructions_file"]
        assert resolved_instructions.resolve() == fixed_role_path.resolve()
        assert resolved_instructions.is_file()
        assert len(role_instructions.encode("utf-8")) <= 7_000
        assert "Artifact handoff:" not in role_instructions


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

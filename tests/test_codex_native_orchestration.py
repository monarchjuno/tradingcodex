from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from tradingcodex_cli.generator import bootstrap_workspace
from tradingcodex_service.application.agents import AGENT_SPECS, EXPECTED_SUBAGENTS
from tradingcodex_service.application.analysis_runs import begin_analysis_run, read_analysis_run
from tradingcodex_service.application.artifact_bindings import verify_authenticated_artifact_binding
from tradingcodex_service.application.research import find_workspace_research_artifact_read_only
from tradingcodex_service.application.runtime import ensure_workspace_manifest
from tradingcodex_service.mcp_runtime import (
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
    flat_base = " ".join(base.split())
    assert "Head Manager/Build/Brain/Strategy/order-turn authority" in flat_base
    assert "or emulate another role" in flat_base
    assert "or emulate a role" not in flat_base
    assert "Keep disposable work under `$TRADINGCODEX_SCRATCH`" in flat_base
    assert "never read audit records" in flat_base
    assert "treat the new request as a bounded delta" in flat_base
    assert "retrieve only missing coverage" in flat_base
    assert "answers the exact assigned question" in flat_base
    assert "If the requested delta belongs to another specialty" in flat_base
    for prompt in [base, *role_prompts, *role_skills]:
        assert "[factual]" not in prompt
        assert "[inference]" not in prompt
        assert "[assumption]" not in prompt
    for prompt in role_prompts:
        assert "Instruction preservation:" not in prompt
        assert "Artifact handoff:" not in prompt
        assert "Narrative evidence discipline:" not in prompt

    workflow = (ROOT / "workspace_templates/modules/repo-skills/files/.agents/skills/tcx-workflow/SKILL.md").read_text(encoding="utf-8")
    framing = (
        ROOT
        / "workspace_templates/modules/repo-skills/files/.agents/skills/tcx-workflow/playbooks/research-framing.md"
    ).read_text(encoding="utf-8")
    flat_workflow = " ".join(workflow.split())
    flat_framing = " ".join(framing.split())
    assert "verified facts and sources, analysis and implications, key" in workflow
    assert "Do not require per-sentence tags" in " ".join(workflow.split())
    assert "relevant base\n  rate or comparison" in workflow
    assert "current action/readiness\n  limit" in workflow
    assert "[Research Framing playbook](playbooks/research-framing.md)" in workflow
    assert "Frame research as a provisional causal map, not a request-shaped checklist" in framing
    assert "outside-in peers, substitutes, industry structure" in framing
    assert "upstream inputs and suppliers, downstream customers" in framing
    assert "These are examples, not mandatory coverage" in framing
    assert "Preserve the requested outcome, explicit scope, and exclusions" in flat_framing
    assert "important points the user may not know to ask about" in flat_framing
    assert "never widen the outcome or action authority" in flat_framing
    assert "Find the causal cruxes" in flat_framing
    assert "Route unresolved cruxes rather than a preset role checklist" in flat_framing
    assert "Compare the result with its brief and causal map" in flat_workflow
    assert "New specialty: dispatch its role with the Artifact ID" in flat_workflow
    assert "Recheck the changed link" in flat_workflow
    assert "## Feedback Loop" in workflow
    assert "use one bounded follow-up via `followup_task`" in flat_workflow
    assert "Illustrative ownership examples, not a mandatory sequence" in flat_workflow
    assert "material FX exposure missing" in flat_workflow
    assert "structural from cyclical, leading from lagging" in flat_framing
    assert "Compare live explanations and prefer evidence that distinguishes them" in flat_framing
    assert "Judge corroboration by independence, diagnostic value, and position in the causal chain" in flat_framing
    assert "dependent repetition is not confirmation" in flat_framing
    assert "Turn each material uncertainty into an observable update" in flat_framing
    assert "fundamentally unknowable ones" in flat_framing
    assert "Research quality and decision relevance take priority over resource economy" in flat_framing
    assert "Tool-call count, context size, and latency alone are not stop conditions" in flat_framing
    assert "deduplicated calls, compact artifact handoffs" in flat_framing
    assert "explicit user scope or deadline requires it" in flat_framing


def test_child_briefs_and_artifact_skills_keep_capabilities_and_lineage_distinct() -> None:
    template_root = ROOT / "workspace_templates/modules"
    fixed_role = (
        template_root
        / "codex-base/files/.codex/prompts/base_instructions/fixed-role.md"
    ).read_text(encoding="utf-8")
    workflow = (
        template_root
        / "repo-skills/files/.agents/skills/tcx-workflow/SKILL.md"
    ).read_text(encoding="utf-8")
    skill_root = (
        template_root
        / "repo-skills/files/.tradingcodex/subagents/skills"
    )
    artifact = (skill_root / "shared/tcx-artifact/SKILL.md").read_text(
        encoding="utf-8"
    )
    evidence = (skill_root / "shared/tcx-evidence/SKILL.md").read_text(
        encoding="utf-8"
    )
    evidence_interface = (
        skill_root / "shared/tcx-evidence/agents/openai.yaml"
    ).read_text(encoding="utf-8")
    judgment = (
        skill_root / "judgment-reviewer/tcx-judgment/SKILL.md"
    ).read_text(encoding="utf-8")
    flat_fixed = " ".join(fixed_role.split())
    flat_workflow = " ".join(workflow.split())
    flat_artifact = " ".join(artifact.split())
    flat_evidence = " ".join(evidence.split())
    flat_judgment = " ".join(judgment.split())
    durable_docs = (
        ROOT / "docs/roles-skills-and-workflows.md"
    ).read_text(encoding="utf-8")
    flat_docs = " ".join(durable_docs.split())

    assert "Evidence roles and `judgment-reviewer` cannot access orders/approvals" in flat_fixed
    assert "Only `portfolio-manager` may use projected portfolio and draft-ticket/check capabilities" in flat_fixed
    assert "only `risk-manager` may use projected policy, check, and service-gated approval capabilities" in flat_fixed
    assert "Never submit/cancel an order or mutate a broker" in flat_fixed
    assert "target owner Artifact ID (append/revise)" in flat_fixed
    assert "triggering cross-role Artifact IDs (inputs)" in flat_fixed
    assert "create a new artifact only when the brief explicitly says so" in flat_fixed
    assert "Honor the brief's data-family owner" in flat_fixed
    assert "do not recollect another role's complete family" in flat_fixed
    assert "Do not load `tcx-calculation` merely to quote or compare source-reported figures" in flat_fixed

    assert "brief every child with that ownership, exact reusable IDs, and the missing slice" in flat_workflow
    assert "no child recollects another owner's complete family" in flat_workflow
    assert "target owner Artifact ID (append/revise) separately from triggering cross-role Artifact IDs" in flat_workflow
    assert "If the target should exist but its ID is missing, return `waiting`" in flat_workflow
    assert "no target ID and an explicit `create new artifact` instruction" in flat_workflow
    assert "Never print full tool records, scan descriptions, or repeat a schema lookup" in flat_workflow

    assert "compact TradingCodex v2 research artifacts" in flat_artifact
    assert "keep the target `artifact_id` separate from consumed input IDs" in flat_artifact
    assert "The receipt owns hashes and exact input versions" in flat_artifact
    assert "one evidence-backed corrected retry" in flat_artifact
    assert "stop as `waiting` with the bounded error and owner" in flat_artifact
    assert "Omit empty optional blocks" in flat_artifact
    assert "`evidence_pack` under `trading/research/` only when" in flat_evidence
    assert "`role_report` under `trading/reports/<role>/`" in flat_evidence
    assert "an evidence pack is not a prerequisite or default duplicate" in flat_evidence
    assert "Artifact ID in the role report's `input_artifact_ids`" in flat_evidence
    assert "Do not copy the same body into both artifacts" in flat_evidence
    assert "collect the smallest source-backed evidence set and choose only a justified evidence pack or role report when persistence is needed" in evidence_interface
    assert "exact conflict or review question" in flat_judgment
    assert "accepted, authenticated Artifact IDs with their service receipts/content hashes" in flat_judgment
    assert "paths and compact summaries as navigation aids, never substitutes" in flat_judgment
    assert "briefs that name that owner, exact reusable IDs, and the needed or missing slice" in flat_docs
    assert "target owner's Artifact ID to append or revise from triggering cross-role Artifact IDs consumed as inputs" in flat_docs
    assert "when the evidence only supports that report, the role creates no duplicate evidence pack" in flat_docs
    assert "report consumes the pack's authenticated Artifact ID through `input_artifact_ids`" in flat_docs

    role_quality_skills = (
        "fundamental-analyst/tcx-fundamental",
        "instrument-analyst/tcx-instrument",
        "macro-analyst/tcx-macro",
        "news-analyst/tcx-news",
        "portfolio-manager/tcx-portfolio",
        "risk-manager/tcx-policy",
        "risk-manager/tcx-risk",
        "technical-analyst/tcx-technical",
        "valuation-analyst/tcx-valuation",
    )
    for relative in role_quality_skills:
        role_skill = (skill_root / relative / "SKILL.md").read_text(encoding="utf-8")
        assert "Role-specific quality:" in role_skill
        assert "Apply the shared artifact quality floor" not in role_skill
        assert "Distinguish sourced facts, analysis, and assumptions in natural prose where it matters" not in role_skill


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
    artifact_schema = TOOL_REGISTRY["create_research_artifact"].input_schema
    expected_properties = {
        "principal_id",
        "artifact_id",
        "artifact_type",
        "title",
        "universe",
        "symbol",
        "markdown",
        "summary",
        "status",
        "lineage",
        "requirements",
        "decision_quality",
        "memory",
        "forecast",
        "valuation",
        "anti_overfit",
        "follow_up_requests",
    }
    assert set(artifact_properties) == expected_properties
    assert set(append_properties) == expected_properties
    assert artifact_schema["additionalProperties"] is False
    assert artifact_properties["status"]["additionalProperties"] is False
    assert artifact_properties["lineage"]["additionalProperties"] is False
    assert artifact_properties["memory"]["additionalProperties"] is False
    assert artifact_properties["status"]["required"] == [
        "handoff",
        "evidence_readiness",
        "action_readiness",
        "confidence",
        "confidence_basis",
    ]
    assert artifact_properties["requirements"]["items"]["enum"] == [
        "decision_quality",
        "forecast",
        "investor_context",
        "anti_overfit",
    ]
    follow_up_item = artifact_properties["follow_up_requests"]["items"]
    assert follow_up_item["type"] == "object"
    assert "improvements" not in artifact_properties
    assert "thesis_lifecycle" not in artifact_properties
    assert "anti_overfit_checks" not in artifact_properties
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
            "title": "ACME facts",
            "markdown": "# ACME facts\n\n[factual] ACME is the subject of this bounded fixture.\n",
            "summary": "Bounded ACME fixture facts.",
            "status": {
                "handoff": "accepted",
                "evidence_readiness": "factual",
                "action_readiness": "research-only",
                "confidence": "high",
                "confidence_basis": "Authenticated bounded fixture evidence.",
                "missing_evidence": [],
                "blocked_actions": ["order", "execution"],
            },
            "lineage": {
                "workflow_run_id": run_id,
                "knowledge_cutoff": "2026-07-12T00:00:00Z",
                "input_artifact_ids": [],
                "source_snapshot_ids": [],
                "dataset_ids": [],
                "calculation_run_ids": [],
            },
            "requirements": [],
        },
        transport_principal="fundamental-analyst",
    )
    synthesis = call_mcp_tool(
        tmp_path,
        "create_research_artifact",
        {
            "artifact_type": "synthesis_report",
            "universe": "public_equity",
            "title": "ACME synthesis",
            "markdown": "# ACME synthesis\n\n[factual] This synthesis consumes the role artifact.\n",
            "summary": "Synthesis of ACME facts.",
            "status": {
                "handoff": "accepted",
                "evidence_readiness": "factual",
                "action_readiness": "research-only",
                "confidence": "high",
                "confidence_basis": "One authenticated current-run input.",
                "missing_evidence": [],
                "blocked_actions": ["order", "execution"],
            },
            "lineage": {
                "workflow_run_id": run_id,
                "knowledge_cutoff": "2026-07-12T00:00:00Z",
                "input_artifact_ids": ["acme-facts"],
                "source_snapshot_ids": [],
                "dataset_ids": [],
                "calculation_run_ids": [],
            },
            "requirements": [],
        },
        transport_principal="head-manager",
    )
    stored = call_mcp_tool(
        tmp_path,
        "get_research_artifact",
        {"artifact_id": f"synthesis-{run_id}", "include_markdown": False},
        transport_principal="head-manager",
    )
    assert synthesis["artifact"]["id"] == f"synthesis-{run_id}"
    assert stored["artifact"]["lineage"]["input_artifacts"] == [
        {
            "id": "acme-facts",
            "version": 1,
                "content_hash": producer["artifact"]["content_hash"],
        }
    ]
    canonical = find_workspace_research_artifact_read_only(tmp_path, f"synthesis-{run_id}")
    assert canonical is not None
    verification = verify_authenticated_artifact_binding(tmp_path, canonical)
    receipt = json.loads((tmp_path / verification["path"]).read_text(encoding="utf-8"))
    assert receipt["input_artifact_hashes"] == {
        "acme-facts": producer["artifact"]["content_hash"]
    }

    with pytest.raises(ValueError, match="does not allow additional properties: plan_hash"):
        call_mcp_tool(
            tmp_path,
            "create_research_artifact",
            {
                "artifact_id": "old-binding",
                "artifact_type": "research_memo",
                "universe": "public_equity",
                "title": "Old binding",
                "markdown": "# Old binding\n",
                "summary": "Old workflow binding must be rejected.",
                "status": {
                    "handoff": "accepted",
                    "evidence_readiness": "factual",
                    "action_readiness": "research-only",
                    "confidence": "low",
                    "confidence_basis": "Schema rejection fixture.",
                },
                "lineage": {
                    "workflow_run_id": run_id,
                    "knowledge_cutoff": "2026-07-12T00:00:00Z",
                },
                "requirements": [],
                "plan_hash": "a" * 64,
            },
            transport_principal="fundamental-analyst",
        )


def test_generated_hook_leaves_native_child_lifecycle_to_codex(workspace: Path) -> None:
    hook = workspace / ".codex/hooks/tradingcodex_hook.py"
    source = hook.read_text(encoding="utf-8")
    hooks = json.loads((workspace / ".codex/hooks.json").read_text(encoding="utf-8"))["hooks"]
    assert set(hooks) == {"SessionStart", "UserPromptSubmit", "PreToolUse", "PermissionRequest", "Stop"}
    assert "analysis_prompt_context" not in source
    assert "tradingcodex-agentic-analysis" not in source
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
    assert prompt.stdout == ""
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


def test_generated_contract_inherits_root_model_and_keeps_role_profiles_optional(workspace: Path) -> None:
    config = (workspace / ".codex/config.toml").read_text(encoding="utf-8")
    head = (workspace / ".codex/prompts/base_instructions/head-manager.md").read_text(encoding="utf-8")
    fixed_role_path = workspace / ".codex/prompts/base_instructions/fixed-role.md"
    assert fixed_role_path.is_file()
    fixed_role = fixed_role_path.read_text(encoding="utf-8")
    role = (workspace / ".codex/agents/fundamental-analyst.toml").read_text(encoding="utf-8")
    skill = (workspace / ".agents/skills/tcx-workflow/SKILL.md").read_text(encoding="utf-8")
    framing = (
        workspace
        / ".agents/skills/tcx-workflow/playbooks/research-framing.md"
    ).read_text(encoding="utf-8")
    flat_head = " ".join(head.split())
    flat_skill = " ".join(skill.split())
    flat_framing = " ".join(framing.split())
    config_values = tomllib.loads(config)
    assert "model" not in config_values
    assert "model_reasoning_effort" not in config_values
    assert not (workspace / ".tradingcodex/generated/model-policy-manifest.json").exists()
    assert 'model_instructions_file = "prompts/base_instructions/head-manager.md"' in config
    assert '[features.multi_agent_v2]' in config
    assert 'enabled = true' in config
    assert "max_concurrent_threads_per_session" not in config
    assert "max_depth" not in config_values.get("agents", {})
    assert "max_threads" not in config_values.get("agents", {})
    assert "required = true" in config
    assert '"begin_analysis_run"' in config
    assert "record_workflow_plan" not in config
    assert "record_artifact_supervisor_loop" not in config
    assert tomllib.loads(role)["model"] == "gpt-5.6-terra"
    assert tomllib.loads(role)["model_reasoning_effort"] == "high"
    assert "required = true" in role
    assert "You are the `head-manager` agent" not in fixed_role
    assert "# Role And Safety" in fixed_role
    assert "authenticated TradingCodex MCP" in fixed_role
    assert "canonical bundle" not in fixed_role
    assert "required taxonomy" not in fixed_role
    assert "Whether to delegate" not in fixed_role
    assert "All work performed on your behalf remains subject" in fixed_role
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
    assert "canonical bundle" not in head
    assert "required taxonomy" not in head
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
    assert "creates no child. never wait for it" in flat_skill.lower()
    assert "call `wait` only after `spawn_agent` or `followup_task` has returned a live target" in flat_skill.lower()
    assert "otherwise do not call it" in flat_skill.lower()
    assert "targetless native wait" not in flat_skill.lower()
    assert "an empty target list alone is not failure" not in flat_skill.lower()
    assert "child-lifecycle results in this run" in flat_skill
    assert "Save an authenticated role artifact when evidence changes a conclusion" in skill
    assert "Do not save simple facts, status" in flat_skill
    assert "Never narrate the report's table of contents" in flat_skill
    assert "executive-report-quality chat answer" in flat_skill
    assert "$tcx-source-gate" in skill
    assert "current-workflow Snapshot/Dataset candidates" in skill
    assert "both readiness axes" in skill
    assert "Analysis cannot create policy, approval, broker, or execution authority" in skill
    assert "[Research Framing playbook](playbooks/research-framing.md)" in skill
    assert "provisional causal map" in flat_framing
    assert "coverage is underspecified" in flat_framing
    assert "causal cruxes" in flat_framing
    assert "dependent repetition is not confirmation" in flat_framing
    assert "likely to change the answer or readiness" in flat_framing
    assert "obtainable gap could change the result" in flat_skill
    assert "one bounded follow-up" in flat_skill
    assert "resolve the relevant market session" in flat_framing
    assert "separate instrument-specific from market-wide drivers" in flat_framing
    assert "At one market session or less" in flat_framing
    assert "change direction, range, or scenario weights" in flat_framing
    assert "fixed forecast roster" in flat_framing
    assert "ALL_TOOLS.filter" not in head + fixed_role + skill
    assert "record_workflow_plan" not in head + role + skill

    roles_root = workspace / ".codex/agents"
    for role_path in roles_root.glob("*.toml"):
        role_instructions = role_path.read_text(encoding="utf-8")
        role_config = tomllib.loads(role_instructions)
        enabled_tools = set(role_config["mcp_servers"]["tradingcodex"]["enabled_tools"])
        assert role_config["model"] == "gpt-5.6-terra"
        assert role_config["model_reasoning_effort"] == "high"
        assert role_config.get("agents", {}).get("enabled") is not False
        assert (
            role_config.get("features", {})
            .get("multi_agent_v2", {})
            .get("enabled")
            is not False
        )
        assert "get_research_artifact" in enabled_tools
        assert ARTIFACT_DISCOVERY_TOOLS.isdisjoint(enabled_tools)
        assert role_config["model_instructions_file"] == FIXED_ROLE_MODEL_INSTRUCTIONS
        resolved_instructions = role_path.parent / role_config["model_instructions_file"]
        assert resolved_instructions.resolve() == fixed_role_path.resolve()
        assert resolved_instructions.is_file()
        assert "Artifact handoff:" not in role_instructions


@pytest.mark.parametrize(
    ("template_relative", "generated_relative"),
    (
        (
            "workspace_templates/modules/repo-skills/files/.agents/skills",
            ".agents/skills",
        ),
        (
            "workspace_templates/modules/repo-skills/files/.tradingcodex/subagents/skills",
            ".tradingcodex/subagents/skills",
        ),
    ),
)
def test_generated_skill_bundles_project_every_resource_recursively(
    workspace: Path,
    template_relative: str,
    generated_relative: str,
) -> None:
    template_root = ROOT / template_relative
    generated_root = workspace / generated_relative
    template_files = {
        path.relative_to(template_root): path
        for path in template_root.rglob("*")
        if path.is_file()
    }

    assert template_files
    for relative, template_path in template_files.items():
        generated_path = generated_root / relative
        assert generated_path.is_file(), relative.as_posix()
        template_text = template_path.read_text(encoding="utf-8")
        generated_text = generated_path.read_text(encoding="utf-8")
        if "{{" not in template_text:
            assert generated_text == template_text
        else:
            assert "{{" not in generated_text

    for template_skill in template_root.rglob("SKILL.md"):
        generated_skill = generated_root / template_skill.relative_to(template_root)
        for raw_target in re.findall(
            r"\[[^\]]+\]\(([^)]+)\)",
            template_skill.read_text(encoding="utf-8"),
        ):
            target = raw_target.strip().strip("<>")
            if target.startswith(("/", "#")) or "://" in target:
                continue
            assert (template_skill.parent / target).is_file(), (
                template_skill.relative_to(template_root).as_posix(),
                target,
            )
            assert (generated_skill.parent / target).is_file(), (
                generated_skill.relative_to(generated_root).as_posix(),
                target,
            )

    assert (
        workspace
        / ".agents/skills/tcx-workflow/playbooks/research-framing.md"
    ).is_file()


def test_generated_mcp_allowlists_reference_only_registered_tools(workspace: Path) -> None:
    config_paths = [
        workspace / ".codex/config.toml",
        *(workspace / ".codex/agents").glob("*.toml"),
    ]

    for config_path in config_paths:
        config = tomllib.loads(config_path.read_text(encoding="utf-8"))
        enabled_tools = set(
            config["mcp_servers"]["tradingcodex"]["enabled_tools"]
        )
        assert enabled_tools <= TOOL_REGISTRY.keys(), (
            f"{config_path.relative_to(workspace)} references unregistered "
            f"TradingCodex MCP tools: {sorted(enabled_tools - TOOL_REGISTRY.keys())}"
        )


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

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tradingcodex_cli.generator import bootstrap_workspace
from tradingcodex_service.application import viewer
from tradingcodex_service.application.analysis_runs import (
    begin_analysis_run,
    explicit_investment_brain_invocation,
)
from tradingcodex_service.application.decision_packages import (
    get_decision_snapshot,
    record_decision_snapshot,
)
from tradingcodex_service.application.investment_brains import (
    install_investment_brain,
    set_investment_brain_status,
)
from tradingcodex_service.mcp_runtime import call_mcp_tool


ROOT = Path(__file__).resolve().parents[1]
BRAIN_ID = "investment-brain-quality-growth"


def _bundle(tmp_path: Path) -> Path:
    root = tmp_path / "quality-growth-brain"
    (root / ".tradingcodex").mkdir(parents=True)
    (root / "skill" / "agents").mkdir(parents=True)
    (root / "skill" / "references").mkdir(parents=True)
    (root / ".tradingcodex" / "plugin.json").write_text(
        json.dumps(
            {
                "format": "tradingcodex.investment-brain",
                "schema_version": 1,
                "type": "investment-brain",
                "id": BRAIN_ID,
                "version": "1.2.3",
                "skill": "skill",
                "source": {
                    "publisher": "Example Research Collective",
                    "repository": "https://example.com/quality-growth",
                    "license": "MIT",
                },
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (root / "skill" / "SKILL.md").write_text(
        "---\n"
        f"name: {BRAIN_ID}\n"
        "description: Frame questions through durable quality and market expectations.\n"
        "---\n\n"
        "# Quality Growth\n\n"
        "Prioritize durable reinvestment economics, embedded expectations, contrary evidence, falsifiers, and abstention.\n",
        encoding="utf-8",
    )
    (root / "skill" / "agents" / "openai.yaml").write_text(
        "interface:\n"
        '  display_name: "Quality Growth Brain"\n'
        '  short_description: "Frame quality-growth judgments"\n'
        f'  default_prompt: "Use ${BRAIN_ID} for this investment analysis."\n'
        "policy:\n"
        "  allow_implicit_invocation: false\n",
        encoding="utf-8",
    )
    (root / "skill" / "references" / "falsifiers.md").write_text(
        "# Falsifiers\n\nTreat deteriorating economics and unsupported expectations as contrary evidence.\n",
        encoding="utf-8",
    )
    return root


@pytest.fixture
def brain_workspace(tmp_path: Path) -> tuple[Path, dict[str, object]]:
    workspace = tmp_path / "workspace"
    bootstrap_workspace(workspace)
    record = install_investment_brain(workspace, local_source=_bundle(tmp_path), actor="test")
    return workspace, record


def _research_args(artifact_id: str, artifact_type: str, run_id: str, *, inputs: list[str]) -> dict[str, object]:
    return {
        "artifact_id": artifact_id,
        "artifact_type": artifact_type,
        "universe": "public_equity",
        "workflow_type": "investment_research",
        "title": artifact_id.replace("-", " ").title(),
        "markdown": f"# {artifact_id}\n\n[factual] Bounded fixture evidence for lineage verification.\n",
        "source_as_of": "2026-07-12",
        "knowledge_cutoff": "2026-07-12T00:00:00Z",
        "evidence_lane": "live_forward",
        "readiness_label": "accepted",
        "context_summary": "Bounded lineage fixture.",
        "reader_summary": "Lineage fixture.",
        "handoff_state": "accepted",
        "confidence": "high",
        "missing_evidence": [],
        "next_recipient": "head-manager",
        "next_action": "Review the recorded lineage.",
        "blocked_actions": ["order", "execution"],
        "source_snapshot_ids": [],
        "workflow_run_id": run_id,
        "input_artifact_ids": inputs,
    }


def test_explicit_brain_invocation_is_exact_and_single() -> None:
    assert explicit_investment_brain_invocation(f"Use ${BRAIN_ID}.") == BRAIN_ID
    assert explicit_investment_brain_invocation(f"Use {BRAIN_ID}.") == ""
    assert explicit_investment_brain_invocation(f"${BRAIN_ID} then ${BRAIN_ID}") == BRAIN_ID
    with pytest.raises(ValueError, match="exactly one"):
        explicit_investment_brain_invocation(
            f"${BRAIN_ID} $investment-brain-deep-value"
        )


def test_analysis_run_seals_resolved_brain_and_baseline_without_raw_request(
    brain_workspace: tuple[Path, dict[str, object]],
) -> None:
    workspace, installed = brain_workspace
    request = f"${BRAIN_ID}\nAnalyze ACME without orders."
    run = begin_analysis_run(
        workspace,
        request,
        run_id="analysis-brain-bound",
        apply_investor_context=False,
    )

    binding = run["investment_brain_binding"]
    assert binding["brain_id"] == BRAIN_ID
    assert binding["version"] == "1.2.3"
    assert binding["content_digest"] == installed["content_digest"]
    assert binding["skill_digest"] == installed["skill_digest"]
    assert binding["source"]["declared"]["publisher"] == "Example Research Collective"
    assert binding["source"]["location"] == ""
    assert binding["source_file"] == installed["source_file"]
    assert binding["projected_skill_path"] == installed["projected_skill_path"]
    assert request not in json.dumps(run)
    external_source = (workspace.parent / "quality-growth-brain").resolve()
    run_record = workspace / ".tradingcodex/mainagent/runs/analysis-brain-bound/run.json"
    assert str(external_source) not in run_record.read_text(encoding="utf-8")

    baseline = begin_analysis_run(
        workspace,
        "Analyze ACME without an extension.",
        run_id="analysis-brain-baseline",
        apply_investor_context=False,
    )["investment_brain_binding"]
    assert baseline["brain_id"] == ""
    assert baseline["version"] == ""
    assert baseline["content_digest"] == ""
    assert baseline["skill_digest"] == ""
    assert BRAIN_ID not in {item["id"] for item in viewer.skill_catalog(workspace)}


def test_unresolved_inactive_and_multiple_brains_fail_before_run_creation(
    brain_workspace: tuple[Path, dict[str, object]],
) -> None:
    workspace, _installed = brain_workspace
    with pytest.raises(ValueError, match="unknown investment brain"):
        begin_analysis_run(
            workspace,
            "$investment-brain-unknown Analyze ACME.",
            run_id="analysis-brain-unknown",
            apply_investor_context=False,
        )
    with pytest.raises(ValueError, match="exactly one"):
        begin_analysis_run(
            workspace,
            f"${BRAIN_ID} $investment-brain-deep-value Analyze ACME.",
            run_id="analysis-brain-multiple",
            apply_investor_context=False,
        )
    set_investment_brain_status(workspace, BRAIN_ID, "inactive", actor="test")
    with pytest.raises(ValueError, match="not active"):
        begin_analysis_run(
            workspace,
            f"${BRAIN_ID} Analyze ACME.",
            run_id="analysis-brain-inactive",
            apply_investor_context=False,
        )
    assert not (workspace / ".tradingcodex/mainagent/runs/analysis-brain-unknown").exists()
    assert not (workspace / ".tradingcodex/mainagent/runs/analysis-brain-multiple").exists()
    assert not (workspace / ".tradingcodex/mainagent/runs/analysis-brain-inactive").exists()


def test_artifacts_and_decision_memory_derive_immutable_brain_lineage(
    brain_workspace: tuple[Path, dict[str, object]],
) -> None:
    workspace, installed = brain_workspace
    run_id = "analysis-brain-lineage"
    begin_analysis_run(
        workspace,
        f"${BRAIN_ID} Analyze ACME.",
        run_id=run_id,
        apply_investor_context=False,
    )
    producer = call_mcp_tool(
        workspace,
        "create_research_artifact",
        _research_args("brain-source", "research_memo", run_id, inputs=[]),
        transport_principal="fundamental-analyst",
    )
    synthesis = call_mcp_tool(
        workspace,
        "create_research_artifact",
        _research_args("brain-synthesis", "synthesis_report", run_id, inputs=["brain-source"]),
        transport_principal="head-manager",
    )
    assert producer["status"] == "stored"
    assert synthesis["status"] == "stored"
    stored = call_mcp_tool(
        workspace,
        "get_research_artifact",
        {"artifact_id": "brain-synthesis", "include_markdown": False},
        transport_principal="head-manager",
    )
    assert stored["investment_brain_id"] == BRAIN_ID
    assert stored["investment_brain_version"] == "1.2.3"
    assert stored["investment_brain_content_digest"] == installed["content_digest"]
    assert stored["strategy_name"] == ""
    assert stored["investor_context_applied"] is False

    forged = _research_args("brain-forged", "research_memo", run_id, inputs=[])
    forged["investment_brain_version"] = "9.9.9"
    with pytest.raises(ValueError, match="service-derived"):
        call_mcp_tool(
            workspace,
            "create_research_artifact",
            forged,
            transport_principal="fundamental-analyst",
        )

    snapshot = record_decision_snapshot(
        workspace,
        {
            "decision_id": "brain-decision",
            "workflow_run_id": run_id,
            "decision_artifact_path": stored["path"],
            "knowledge_cutoff": stored["knowledge_cutoff"],
            "decided_at": stored["recorded_at"],
            "created_by": "head-manager",
            "evidence_lane": "live_forward",
            "forecast_block_reason": "No forecast was required for this lineage fixture.",
        },
    )["decision_snapshot"]
    brain_ref = snapshot["investment_brain_ref"]
    assert brain_ref["brain_id"] == BRAIN_ID
    assert brain_ref["version"] == "1.2.3"
    assert brain_ref["content_digest"] == installed["content_digest"]
    assert get_decision_snapshot(workspace, "brain-decision")["verification_status"] == "verified"


def test_generated_hook_reports_explicit_syntax_without_semantic_routing(
    brain_workspace: tuple[Path, dict[str, object]],
) -> None:
    workspace, _installed = brain_workspace
    hook = workspace / ".codex/hooks/tradingcodex_hook.py"
    result = subprocess.run(
        [sys.executable, str(hook), "user-prompt-submit"],
        cwd=workspace,
        input=json.dumps({"prompt": f"${BRAIN_ID} Analyze ACME."}),
        text=True,
        capture_output=True,
        env={**os.environ, "PYTHONPATH": str(ROOT)},
        check=True,
    )
    context = json.loads(json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"])
    assert context["investment_brain_selection"] == {
        "status": "explicit",
        "brain_id": BRAIN_ID,
        "validation": "begin_analysis_run",
    }
    assert "selected_team" not in context
    assert "DAG" not in context


def test_generated_hook_allows_only_session_bound_sealed_brain_references(
    brain_workspace: tuple[Path, dict[str, object]],
) -> None:
    workspace, installed = brain_workspace
    hook = workspace / ".codex/hooks/tradingcodex_hook.py"
    session_id = "brain-reference-session"
    prompt = f"${BRAIN_ID} Analyze ACME."
    submitted = subprocess.run(
        [sys.executable, str(hook), "user-prompt-submit"],
        cwd=workspace,
        input=json.dumps({"prompt": prompt, "session_id": session_id}),
        text=True,
        capture_output=True,
        env={**os.environ, "PYTHONPATH": str(ROOT)},
        check=True,
    )
    context = json.loads(json.loads(submitted.stdout)["hookSpecificOutput"]["additionalContext"])
    run_id = context["workflow_run_id"]
    run = begin_analysis_run(
        workspace,
        prompt,
        run_id=run_id,
        apply_investor_context=False,
    )
    assert run["investment_brain_binding"]["skill_digest"] == installed["skill_digest"]

    selected_reference = f".agents/skills/{BRAIN_ID}/references/falsifiers.md"

    def pre_tool(command: str, *, current_session: str = session_id, explicit_run_id: str = "") -> dict[str, object] | None:
        payload: dict[str, object] = {
            "tool_name": "Bash",
            "tool_input": {"command": command},
        }
        if current_session:
            payload["session_id"] = current_session
        if explicit_run_id:
            payload["workflow_run_id"] = explicit_run_id
        result = subprocess.run(
            [sys.executable, str(hook), "pre-tool-use"],
            cwd=workspace,
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            env={**os.environ, "PYTHONPATH": str(ROOT)},
            check=True,
        )
        return json.loads(result.stdout) if result.stdout.strip() else None

    assert pre_tool(f"cat {selected_reference}") is None
    assert pre_tool(f"cat {selected_reference}", current_session="unknown-session")["decision"] == "block"
    assert pre_tool(f"cat {selected_reference}", current_session="", explicit_run_id=run_id)["decision"] == "block"

    unselected_reference = workspace / ".agents/skills/investment-brain-unselected/references/decoy.md"
    unselected_reference.parent.mkdir(parents=True)
    unselected_reference.write_text("# Decoy\n", encoding="utf-8")
    blocked_commands = (
        "cat .agents/skills/investment-brain-unselected/references/decoy.md",
        f"cat .agents/skills/{BRAIN_ID}/agents/openai.yaml",
        f"cat .agents/skills/{BRAIN_ID}/SKILL.md",
        f"cat {selected_reference} && cat .tradingcodex/investment-brains/registry.json",
        "cat .tradingcodex/investment-brains/registry.json",
    )
    for command in blocked_commands:
        assert pre_tool(command)["decision"] == "block", command

    (workspace / selected_reference).write_text("# Changed after run binding\n", encoding="utf-8")
    assert pre_tool(f"cat {selected_reference}")["decision"] == "block"

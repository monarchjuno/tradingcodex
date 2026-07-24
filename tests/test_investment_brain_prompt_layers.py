from __future__ import annotations

from pathlib import Path

from tradingcodex_cli.generator import bootstrap_workspace


ROOT = Path(__file__).resolve().parents[1]
HEAD = ROOT / "workspace_templates/modules/codex-base/files/.codex/prompts/base_instructions/head-manager.md"
SKILL_ROOT = ROOT / "workspace_templates/modules/repo-skills/files/.agents/skills/tcx-workflow"


def _flat(text: str) -> str:
    return " ".join(text.split())


def test_head_manager_owns_typed_brain_translation_and_conflicts() -> None:
    prompt = HEAD.read_text(encoding="utf-8")
    flat_prompt = _flat(prompt)

    for layer in (
        "TradingCodex Core",
        "current user mandate",
        "Investor Context",
        "Strategy",
        "Investment Brain",
        "Method skills",
        "current-run evidence",
        "Decision Memory",
    ):
        assert layer in prompt

    assert "one exact projected" in flat_prompt
    assert "waiting_for_investment_brain" in flat_prompt
    assert "Translate the selected Brain's platform-neutral questions" in flat_prompt
    assert "Do not let the Brain name the team" in flat_prompt
    assert "Strategy's decision rule" in flat_prompt
    assert "authenticated evidence control factual claims" in flat_prompt
    assert "independent current-run evidence view" in flat_prompt
    assert "post-memory decision" in flat_prompt
    assert "caller-authored Brain lineage" in flat_prompt
    assert "compact frame derived from the mandate" in flat_prompt
    assert "may overturn a conclusion under that frame but cannot replace the frame" in flat_prompt


def test_tcx_workflow_keeps_context_and_routing_native() -> None:
    skill = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
    framing = (SKILL_ROOT / "playbooks/research-framing.md").read_text(
        encoding="utf-8"
    )
    flat_skill = _flat(skill)
    flat_framing = _flat(framing)

    assert "investment research, valuation, forecasts, recommendations, portfolio or risk review, order preparation, approval review, and execution status" in flat_skill
    assert "Apply one explicitly selected Investment Brain or Strategy" in flat_skill
    assert "Use an exact fixed role when one is available" in flat_skill
    assert "Only an unavailable evidence-producing role may use a generic child" in flat_skill
    assert "Do not replace an independent `risk-manager` or `judgment-reviewer` review" in flat_skill
    assert "Reuse a live child's session with `followup_task`" in flat_skill
    assert "## Feedback Loop" in skill
    assert "use one bounded follow-up via `followup_task`" in flat_skill
    assert "Illustrative ownership examples, not a mandatory sequence" in flat_skill
    assert "material FX exposure missing" in flat_skill
    assert "Add `macro-analyst`; do not make the current owner emulate it" in flat_skill
    assert "Add `judgment-reviewer` or `risk-manager`" in flat_skill
    assert "load `$tcx-source-gate`" in flat_skill
    assert "current-workflow Snapshot/Dataset candidates" in flat_skill
    assert "Preserve an independent current view before Decision Memory" in flat_skill
    assert "compact judgment frame derived from the user mandate" in flat_skill
    assert "not replace the frame" in flat_skill
    assert "[Research Framing playbook](playbooks/research-framing.md)" in skill
    assert "Skip the playbook for a narrow fact or recorded order" in flat_skill
    assert "Frame research as a provisional causal map, not a request-shaped checklist" in flat_framing
    assert "These are examples, not mandatory coverage" in flat_framing
    assert "Preserve the requested outcome, explicit scope, and exclusions" in flat_framing
    assert "important points the user may not know to ask about" in flat_framing
    assert "never widen the outcome or action authority" in flat_framing
    assert "Find the causal cruxes" in flat_framing
    assert "Route unresolved cruxes rather than a preset role checklist" in flat_framing
    assert "Compare the result with its brief and causal map" in flat_skill
    assert "New specialty: dispatch its role with the Artifact ID" in flat_skill
    assert "Recheck the changed link" in flat_skill
    assert "structural from cyclical, leading from lagging" in flat_framing
    assert "Compare live explanations and prefer evidence that distinguishes them" in flat_framing
    assert "Judge corroboration by independence, diagnostic value, and position in the causal chain" in flat_framing
    assert "dependent repetition is not confirmation" in flat_framing
    assert "Turn each material uncertainty into an observable update" in flat_framing
    assert "Research quality and decision relevance take priority over resource economy" in flat_framing
    assert "Tool-call count, context size, and latency alone are not stop conditions" in flat_framing
    assert "explicit user scope or deadline requires it" in flat_framing
    assert "After an authenticated Head Manager `synthesis_report` receipt" in skill
    assert "Resolve the returned `path` against the current workspace root" in flat_skill
    assert "[Open final research report](/absolute/path/to/report.md)" in skill

    stale = (
        "workflow intake",
        "recorded workflow plan",
        "allowed_followup_team",
        "escalation_team",
        "loop_policy",
        "lane_escalation_proposal",
        "terminal workflow actions",
    )
    assert all(token not in skill for token in stale)
    assert "Do not create a server-owned lane, team, DAG, or task queue" in flat_skill


def test_generated_workspace_projects_brain_context_contract(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    bootstrap_workspace(workspace)

    generated_head = (workspace / ".codex/prompts/base_instructions/head-manager.md").read_text(encoding="utf-8")
    generated_skill = (workspace / ".agents/skills/tcx-workflow/SKILL.md").read_text(encoding="utf-8")
    generated_framing = (
        workspace
        / ".agents/skills/tcx-workflow/playbooks/research-framing.md"
    ).read_text(encoding="utf-8")

    assert "investment_brain_binding" in generated_head
    assert "waiting_for_investment_brain" in generated_head
    assert "Apply one explicitly selected Investment Brain or Strategy" in generated_skill
    assert "## Feedback Loop" in generated_skill
    assert "Illustrative ownership examples" in generated_skill
    assert "[Open final research report](/absolute/path/to/report.md)" in generated_skill
    assert "[Research Framing playbook](playbooks/research-framing.md)" in generated_skill
    assert "Frame research as a provisional causal map" in generated_framing

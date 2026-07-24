from __future__ import annotations

from pathlib import Path

import pytest

from tradingcodex_service.application.investor_context import (
    INVESTOR_CONTEXT_PATH,
    clear_investor_context,
    investor_context_binding,
    read_investor_context,
    set_investor_context_enabled,
    update_investor_context,
)
from tradingcodex_service.application.runtime import (
    ensure_workspace_manifest,
)
from tradingcodex_service.application.analysis_runs import (
    begin_analysis_run,
    explicit_strategy_invocation,
)


@pytest.fixture(autouse=True)
def attached_workspace(tmp_path: Path) -> None:
    ensure_workspace_manifest(tmp_path)


def test_context_is_lazy_file_native_and_can_be_disabled_per_run(tmp_path: Path) -> None:
    empty = read_investor_context(tmp_path)
    assert empty["configured"] is False
    assert not (tmp_path / INVESTOR_CONTEXT_PATH).exists()

    saved = update_investor_context(
        tmp_path,
        {
            "investment_objective": "long-term capital growth",
            "time_horizon": "more than five years",
            "risk_tolerance_and_loss_capacity": "can tolerate a 20% drawdown",
            "constraints": ["taxable account", "no leverage"],
        },
    )
    assert saved["source"] == "workspace_file"
    assert saved["configured"] is True
    assert saved["content_hash"]

    applied = investor_context_binding(tmp_path)
    skipped = investor_context_binding(tmp_path, apply=False)
    assert applied["applied"] is True
    assert applied["fields"]["investment_objective"] == "long-term capital growth"
    assert skipped["applied"] is False
    assert skipped["fields"] == {}
    assert read_investor_context(tmp_path)["enabled_by_default"] is True


def test_context_default_toggle_clear_and_fail_closed_parsing(tmp_path: Path) -> None:
    update_investor_context(tmp_path, {"investment_objective": "income"})
    disabled = set_investor_context_enabled(tmp_path, False)
    assert disabled["enabled_by_default"] is False
    assert investor_context_binding(tmp_path)["applied"] is False

    cleared = clear_investor_context(tmp_path)
    assert cleared["status"] == "cleared"
    assert cleared["configured"] is False

    (tmp_path / INVESTOR_CONTEXT_PATH).write_text("# missing frontmatter\n", encoding="utf-8")
    with pytest.raises(ValueError, match="schema_version 1"):
        read_investor_context(tmp_path)
    assert clear_investor_context(tmp_path)["configured"] is False


def test_investor_context_has_no_profile_compatibility_source(tmp_path: Path) -> None:
    context = read_investor_context(tmp_path)
    assert context["source"] == "none"
    assert context["fields"] == {}

    saved = update_investor_context(tmp_path, {"liquidity_needs": "none expected"})
    assert saved["source"] == "workspace_file"
    assert saved["fields"] == {"liquidity_needs": "none expected"}


def test_context_rejects_unknown_or_oversized_fields(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unknown investor context"):
        update_investor_context(tmp_path, {"broker_password": "secret"})
    with pytest.raises(ValueError, match="exceeds"):
        update_investor_context(tmp_path, {"investment_objective": "x" * 2001})


def test_analysis_run_seals_enabled_context_and_respects_workspace_default(tmp_path: Path) -> None:
    update_investor_context(tmp_path, {"investment_objective": "growth"})

    run = begin_analysis_run(
        tmp_path,
        "월요일 국장 예상해봐",
        run_id="analysis-native-context",
    )
    applied = run["investor_context_binding"]
    assert applied["applied"] is True
    assert "fields" not in applied
    assert applied["snapshot_path"] == (
        ".tradingcodex/mainagent/runs/analysis-native-context/investor-context-snapshot.md"
    )
    assert (tmp_path / applied["snapshot_path"]).read_text(encoding="utf-8") == (
        tmp_path / INVESTOR_CONTEXT_PATH
    ).read_text(encoding="utf-8")
    assert "investment_objective: growth" not in (
        tmp_path / ".tradingcodex/mainagent/runs/analysis-native-context/run.json"
    ).read_text(encoding="utf-8")

    sealed = (tmp_path / applied["snapshot_path"]).read_text(encoding="utf-8")
    update_investor_context(tmp_path, {"investment_objective": "income"})
    assert begin_analysis_run(
        tmp_path,
        "월요일 국장 예상해봐",
        run_id="analysis-native-context",
    )["record_hash"] == run["record_hash"]
    assert (tmp_path / applied["snapshot_path"]).read_text(encoding="utf-8") == sealed

    with pytest.raises(ValueError, match="different request"):
        begin_analysis_run(tmp_path, "다른 요청", run_id="analysis-native-context")

    set_investor_context_enabled(tmp_path, False)
    disabled = begin_analysis_run(
        tmp_path,
        "Analyze MSFT.",
        run_id="analysis-native-context-disabled",
    )["investor_context_binding"]
    assert disabled["configured"] is True
    assert disabled["applied"] is False
    assert disabled["snapshot_path"] == ""


def test_explicit_strategy_invocation_never_guesses_from_natural_language() -> None:
    assert explicit_strategy_invocation("Use $strategy-quality-watch for this review.") == "strategy-quality-watch"
    assert explicit_strategy_invocation(
        "$Strategy-Quality-Watch\nReview this portfolio."
    ) == "strategy-quality-watch"
    assert explicit_strategy_invocation(
        "$investment-brain-quality-growth\n$strategy-quality-watch\nReview this portfolio."
    ) == "strategy-quality-watch"
    assert explicit_strategy_invocation("Use strategy-quality-watch for this review.") == ""
    assert explicit_strategy_invocation("Use $tcx-strategy to create a reusable strategy.") == ""
    with pytest.raises(ValueError, match="exactly one"):
        explicit_strategy_invocation("Use $strategy-quality-watch and $strategy-value-watch.")


def test_explicit_strategy_invocation_accepts_only_the_projected_skill_link(tmp_path: Path) -> None:
    skill = tmp_path / ".agents/skills/strategy-quality-watch/SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("# Quality Watch\n", encoding="utf-8")
    prompt = f"Use [$strategy-quality-watch]({str(skill).replace('/', chr(92))})."

    assert explicit_strategy_invocation(prompt, tmp_path) == "strategy-quality-watch"
    assert explicit_strategy_invocation(f"{prompt} $strategy-quality-watch", tmp_path) == "strategy-quality-watch"
    assert explicit_strategy_invocation(
        f"[$Strategy-Quality-Watch]({skill})\nReview it.",
        tmp_path,
    ) == "strategy-quality-watch"
    with pytest.raises(ValueError, match="must target"):
        explicit_strategy_invocation(
            f"Use [$strategy-quality-watch]({tmp_path / 'other/SKILL.md'}).",
            tmp_path,
        )


@pytest.mark.parametrize(
    "prompt",
    [
        "Use $strategy-qualіty for this review.",
        "Use $strategy-qualityα for this review.",
        "Use $strategy-quality‐growth for this review.",
        "Use $strategy-quality―growth for this review.",
        "Use $strategy-quality−growth for this review.",
        "Use $strategy-quality\u200b-growth for this review.",
        "Use \u200b$strategy-quality for this review.",
        "Use [$strategy-qualіty](/wrong/SKILL.md).",
        "Use [$strategy-quality‐growth](/wrong/SKILL.md).",
        "Use [$strategy-quality\u200b-growth](/wrong/SKILL.md).",
    ],
)
def test_explicit_strategy_invocation_rejects_confusable_or_hidden_ids(prompt: str) -> None:
    with pytest.raises(ValueError):
        explicit_strategy_invocation(prompt)


def test_analysis_run_always_reconciles_prebound_and_prompt_strategy_selection(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="exactly one"):
        begin_analysis_run(
            tmp_path,
            "Use $strategy-quality-watch and $strategy-value-watch.",
            strategy_id="strategy-quality-watch",
        )

    with pytest.raises(ValueError, match="does not match"):
        begin_analysis_run(
            tmp_path,
            "Use $strategy-value-watch.",
            strategy_id="strategy-quality-watch",
        )

    request = "Analyze ACME with the baseline strategy."
    begin_analysis_run(
        tmp_path,
        request,
        run_id="analysis-reconcile-retry",
        apply_investor_context=False,
    )
    with pytest.raises(ValueError, match="existing analysis run"):
        begin_analysis_run(
            tmp_path,
            request,
            run_id="analysis-reconcile-retry",
            strategy_id="strategy-other",
            apply_investor_context=False,
        )
    with pytest.raises(ValueError, match="investor context choice"):
        begin_analysis_run(
            tmp_path,
            request,
            run_id="analysis-reconcile-retry",
            apply_investor_context=True,
        )


def test_analysis_run_hash_binds_request_and_context_choice(tmp_path: Path) -> None:
    update_investor_context(tmp_path, {"investment_objective": "growth"})
    run = begin_analysis_run(
        tmp_path,
        "Analyze NVDA.",
        run_id="analysis-hash-binding",
        apply_investor_context=False,
    )
    assert run["investor_context_binding"]["applied"] is False
    assert run["orchestration_owner"] == "codex-head-manager"
    assert "selected_team" not in run
    assert "lane" not in run
    assert "plan" not in run
    assert run["record_hash"]

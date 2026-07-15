from __future__ import annotations

import json
import os
import re
import shutil
import tomllib
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from tradingcodex_service.application.common import (
    _safe_read,
    atomic_write_text as _atomic_write_text,
    file_hash as _file_hash,
    now_iso,
    read_json,
    sanitize_id,
    stable_hash,
    write_json,
)
from tradingcodex_service.application.markdown_preview import split_markdown_frontmatter


@dataclass(frozen=True)
class SkillSpec:
    id: str
    label: str
    owner_roles: tuple[str, ...]
    risk_tags: tuple[str, ...] = ()
    user_visible: bool = False
    scope: str = "mainagent"


@dataclass(frozen=True)
class AgentSpec:
    role: str
    label: str
    group: str
    builtin_skills: tuple[str, ...]
    mcp_allowlist: tuple[str, ...] = ()
    forbidden_skill_tags: tuple[str, ...] = ()
    model_tier: str = "terra"


@dataclass(frozen=True)
class ModelPolicy:
    tier: str
    primary_model: str
    reasoning_effort: str
    required_capabilities: tuple[str, ...]


MODEL_POLICY_REVISION = "v1-role-policy-v3"
MODEL_PROMPT_REVISION = "2026-07-gpt56-v1"
MODEL_TOOL_PROFILE_REVISION = "2026-07-role-allowlists-v1"
MINIMUM_CODEX_VERSION = "0.144.1"
REFERENCE_CODEX_VERSION = "0.144.4"
MODEL_POLICIES = {
    "orchestrator": ModelPolicy(
        "orchestrator",
        "gpt-5.6-sol",
        "xhigh",
        ("named_agent_model_selector", "reasoning_effort_xhigh", "tool_calling"),
    ),
    "terra": ModelPolicy("terra", "gpt-5.6-terra", "high", ("named_agent_model_selector", "reasoning_effort_high", "tool_calling")),
    "terra-low": ModelPolicy("terra-low", "gpt-5.6-terra", "low", ("named_agent_model_selector", "reasoning_effort_low", "tool_calling")),
}
CODEX_REASONING_EFFORTS = {"minimal", "low", "medium", "high", "xhigh"}
MODEL_POLICY_MANIFEST_PATH = Path(".tradingcodex/generated/model-policy-manifest.json")


def resolve_agent_model_policy(role: str) -> dict[str, Any]:
    spec = AGENT_SPECS.get(role)
    if spec is None:
        raise ValueError(f"unknown role: {role}")
    policy = MODEL_POLICIES[spec.model_tier]
    rollout = os.environ.get("TRADINGCODEX_MODEL_ROLLOUT", "active").strip().lower()
    if rollout != "active":
        raise ValueError("TRADINGCODEX_MODEL_ROLLOUT rollback is no longer supported")
    supported_raw = os.environ.get("TRADINGCODEX_CODEX_SUPPORTED_MODELS", "")
    supported = {item.strip() for item in supported_raw.split(",") if item.strip()}
    if supported and policy.primary_model not in supported:
        raise ValueError(f"required Codex model is unavailable: {policy.primary_model}")
    return {
        "policy_revision": MODEL_POLICY_REVISION,
        "runtime_surface": "codex_project_toml",
        "minimum_codex_version": MINIMUM_CODEX_VERSION,
        "reference_codex_version": REFERENCE_CODEX_VERSION,
        "tier": policy.tier,
        "primary_model": policy.primary_model,
        "resolved_model": policy.primary_model,
        "reasoning_effort": policy.reasoning_effort,
        "required_capabilities": list(policy.required_capabilities),
        "known_unsupported_settings": ["reasoning.mode", "reasoning.context"],
        "prompt_revision": MODEL_PROMPT_REVISION,
        "tool_profile_revision": MODEL_TOOL_PROFILE_REVISION,
        "support_status": "verified" if supported else "unverified",
        "capability_source": "TRADINGCODEX_CODEX_SUPPORTED_MODELS" if supported else "runtime-unverified",
        "evaluation_required_for_release": True,
        "evaluation_comparison_ref": os.environ.get("TRADINGCODEX_MODEL_EVALUATION_COMPARISON", ""),
    }


RESEARCH_ROLES = (
    "fundamental-analyst",
    "technical-analyst",
    "news-analyst",
    "macro-analyst",
    "instrument-analyst",
    "valuation-analyst",
)
FORECASTING_DISCIPLINE_ROLES = RESEARCH_ROLES + ("portfolio-manager", "risk-manager")
JUDGMENT_REVIEW_ROLE = "judgment-reviewer"
JUDGMENT_REVIEW_ROLES = (JUDGMENT_REVIEW_ROLE,)
THESIS_SCENARIO_TREE_ROLES = (
    "fundamental-analyst",
    "news-analyst",
    "macro-analyst",
    "instrument-analyst",
    "valuation-analyst",
)
NUMERIC_DATA_QC_ROLES = (
    "fundamental-analyst",
    "technical-analyst",
    "macro-analyst",
    "valuation-analyst",
    "portfolio-manager",
    "risk-manager",
)
ANTI_OVERFIT_VALIDATION_ROLES = (
    "technical-analyst",
    "valuation-analyst",
    "portfolio-manager",
    "risk-manager",
)

HEAD_MANAGER_SKILLS = (
    "tcx-plan",
    "tcx-workflow",
    "tcx-memory",
    "tcx-automate",
    "tcx-dashboard",
    "tcx-server",
    "tcx-build",
    "tcx-investor-context",
    "tcx-strategy",
    "tcx-brain",
    "tcx-order-allow",
    "tcx-order-submit",
    "tcx-order-cancel",
)

NATIVE_EXECUTION_SKILLS = frozenset({"tcx-order-allow", "tcx-order-submit", "tcx-order-cancel"})

AGENT_SPECS: dict[str, AgentSpec] = {
    "head-manager": AgentSpec(
        role="head-manager",
        label="Head Manager",
        group="coordination",
        builtin_skills=HEAD_MANAGER_SKILLS,
        mcp_allowlist=(
            "get_tradingcodex_status",
            "get_update_status",
            "manage_strategy",
            "manage_investment_brain",
            "begin_analysis_run",
            "simulate_policy",
            "get_order_status",
            "list_broker_connections",
            "get_broker_connection_status",
            "list_reconciliation_runs",
            "get_order_ticket",
            "list_order_tickets",
            "use_order_turn_grant",
            "record_broker_mapping_review",
            "get_positions",
            "get_portfolio_snapshot",
            "list_external_mcp_connections",
            "list_external_mcp_permission_requests",
            "list_broker_adapter_providers",
            "render_broker_connector_scaffold",
            "register_broker_connector",
            "validate_broker_connector_build",
            "get_broker_capability_profile",
            "get_broker_instrument_constraints",
            "preview_order_translation",
            "get_connector_build_status",
            "list_workflow_artifacts",
            "create_research_artifact",
            "get_research_artifact",
            "list_research_artifacts",
            "search_research_artifacts",
            "append_research_artifact_version",
            "export_research_artifact_md",
            "record_source_snapshot",
            "create_research_spec",
            "get_research_spec",
            "list_research_specs",
            "create_replay_manifest",
            "record_experiment_run",
            "rebuild_research_index",
            "get_forecast",
            "list_forecasts",
            "score_forecast",
            "get_forecast_calibration_report",
            "create_evaluation_corpus",
            "record_evaluation_run",
            "create_blind_review_assignment",
            "compare_evaluation_runs",
            "record_audit_event",
        ),
        model_tier="orchestrator",
    ),
    "fundamental-analyst": AgentSpec(
        role="fundamental-analyst",
        label="Fundamental Analyst",
        group="research",
        builtin_skills=("tcx-source-gate", "tcx-evidence", "tcx-data-qc", "tcx-scenarios", "tcx-forecast", "tcx-fundamental"),
        mcp_allowlist=(
            "list_workflow_artifacts",
            "create_research_artifact",
            "get_research_artifact",
            "list_research_artifacts",
            "search_research_artifacts",
            "append_research_artifact_version",
            "export_research_artifact_md",
            "record_source_snapshot",
            "create_research_spec",
            "get_research_spec",
            "list_research_specs",
            "create_replay_manifest",
            "record_experiment_run",
            "issue_forecast",
            "revise_forecast",
            "get_forecast",
            "list_forecasts",
            "get_forecast_calibration_report",
            "record_audit_event",
        ),
        forbidden_skill_tags=("approval", "execution", "order", "secret"),
    ),
    "technical-analyst": AgentSpec(
        role="technical-analyst",
        label="Technical Analyst",
        group="research",
        builtin_skills=("tcx-source-gate", "tcx-evidence", "tcx-data-qc", "tcx-anti-overfit", "tcx-forecast", "tcx-technical"),
        mcp_allowlist=(
            "list_workflow_artifacts",
            "create_research_artifact",
            "get_research_artifact",
            "list_research_artifacts",
            "search_research_artifacts",
            "append_research_artifact_version",
            "export_research_artifact_md",
            "record_source_snapshot",
            "create_research_spec",
            "get_research_spec",
            "list_research_specs",
            "create_replay_manifest",
            "record_experiment_run",
            "issue_forecast",
            "revise_forecast",
            "get_forecast",
            "list_forecasts",
            "get_forecast_calibration_report",
            "record_audit_event",
        ),
        forbidden_skill_tags=("approval", "execution", "order", "secret"),
    ),
    "news-analyst": AgentSpec(
        role="news-analyst",
        label="News Analyst",
        group="research",
        builtin_skills=("tcx-source-gate", "tcx-evidence", "tcx-scenarios", "tcx-forecast", "tcx-news"),
        mcp_allowlist=(
            "list_workflow_artifacts",
            "create_research_artifact",
            "get_research_artifact",
            "list_research_artifacts",
            "search_research_artifacts",
            "append_research_artifact_version",
            "export_research_artifact_md",
            "record_source_snapshot",
            "create_research_spec",
            "get_research_spec",
            "list_research_specs",
            "create_replay_manifest",
            "record_experiment_run",
            "issue_forecast",
            "revise_forecast",
            "get_forecast",
            "list_forecasts",
            "get_forecast_calibration_report",
            "record_audit_event",
        ),
        forbidden_skill_tags=("approval", "execution", "order", "secret"),
    ),
    "macro-analyst": AgentSpec(
        role="macro-analyst",
        label="Macro Analyst",
        group="research",
        builtin_skills=("tcx-source-gate", "tcx-evidence", "tcx-data-qc", "tcx-scenarios", "tcx-forecast", "tcx-macro"),
        mcp_allowlist=(
            "list_workflow_artifacts",
            "create_research_artifact",
            "get_research_artifact",
            "list_research_artifacts",
            "search_research_artifacts",
            "append_research_artifact_version",
            "export_research_artifact_md",
            "record_source_snapshot",
            "create_research_spec",
            "get_research_spec",
            "list_research_specs",
            "create_replay_manifest",
            "record_experiment_run",
            "issue_forecast",
            "revise_forecast",
            "get_forecast",
            "list_forecasts",
            "get_forecast_calibration_report",
            "record_audit_event",
        ),
        forbidden_skill_tags=("approval", "execution", "order", "secret"),
    ),
    "instrument-analyst": AgentSpec(
        role="instrument-analyst",
        label="Instrument Analyst",
        group="research",
        builtin_skills=("tcx-source-gate", "tcx-evidence", "tcx-scenarios", "tcx-forecast", "tcx-instrument"),
        mcp_allowlist=(
            "list_workflow_artifacts",
            "create_research_artifact",
            "get_research_artifact",
            "list_research_artifacts",
            "search_research_artifacts",
            "append_research_artifact_version",
            "export_research_artifact_md",
            "record_source_snapshot",
            "create_research_spec",
            "get_research_spec",
            "list_research_specs",
            "create_replay_manifest",
            "record_experiment_run",
            "issue_forecast",
            "revise_forecast",
            "get_forecast",
            "list_forecasts",
            "get_forecast_calibration_report",
            "get_broker_instrument_constraints",
            "record_audit_event",
        ),
        forbidden_skill_tags=("approval", "execution", "order", "secret"),
    ),
    "valuation-analyst": AgentSpec(
        role="valuation-analyst",
        label="Valuation Analyst",
        group="research",
        builtin_skills=("tcx-source-gate", "tcx-data-qc", "tcx-scenarios", "tcx-forecast", "tcx-anti-overfit", "tcx-valuation"),
        mcp_allowlist=(
            "list_workflow_artifacts",
            "create_research_artifact",
            "get_research_artifact",
            "list_research_artifacts",
            "search_research_artifacts",
            "append_research_artifact_version",
            "export_research_artifact_md",
            "record_source_snapshot",
            "create_research_spec",
            "get_research_spec",
            "list_research_specs",
            "create_replay_manifest",
            "record_experiment_run",
            "create_causal_equity_analysis",
            "issue_forecast",
            "revise_forecast",
            "get_forecast",
            "list_forecasts",
            "get_forecast_calibration_report",
            "record_audit_event",
        ),
        forbidden_skill_tags=("approval", "execution", "order", "secret"),
    ),
    "portfolio-manager": AgentSpec(
        role="portfolio-manager",
        label="Portfolio Manager",
        group="portfolio",
        builtin_skills=("tcx-data-qc", "tcx-forecast", "tcx-anti-overfit", "tcx-portfolio", "tcx-order-draft"),
        mcp_allowlist=(
            "list_workflow_artifacts",
            "create_research_artifact",
            "get_research_artifact",
            "append_research_artifact_version",
            "export_research_artifact_md",
            "list_broker_connections",
            "get_broker_connection_status",
            "sync_broker_account",
            "list_reconciliation_runs",
            "get_positions",
            "get_portfolio_snapshot",
            "create_order_ticket",
            "run_order_checks",
            "discard_draft_order",
            "get_order_ticket",
            "list_order_tickets",
            "issue_forecast",
            "revise_forecast",
            "get_forecast",
            "list_forecasts",
            "get_forecast_calibration_report",
            "record_audit_event",
        ),
        forbidden_skill_tags=("execution", "secret"),
    ),
    "risk-manager": AgentSpec(
        role="risk-manager",
        label="Risk Manager",
        group="risk",
        builtin_skills=("tcx-data-qc", "tcx-forecast", "tcx-anti-overfit", "tcx-risk", "tcx-policy", "tcx-order-approve"),
        mcp_allowlist=(
            "simulate_policy",
            "validate_approval_receipt",
            "list_broker_connections",
            "get_broker_connection_status",
            "list_reconciliation_runs",
            "get_positions",
            "get_portfolio_snapshot",
            "run_order_checks",
            "request_order_approval",
            "get_order_ticket",
            "list_order_tickets",
            "list_workflow_artifacts",
            "create_research_artifact",
            "get_research_artifact",
            "append_research_artifact_version",
            "export_research_artifact_md",
            "issue_forecast",
            "revise_forecast",
            "get_forecast",
            "list_forecasts",
            "get_forecast_calibration_report",
            "record_audit_event",
        ),
        forbidden_skill_tags=("execution", "secret"),
    ),
    "judgment-reviewer": AgentSpec(
        role="judgment-reviewer",
        label="Judgment Reviewer",
        group="review",
        builtin_skills=("tcx-judgment",),
        mcp_allowlist=(
            "list_workflow_artifacts",
            "create_research_artifact",
            "get_research_artifact",
            "list_research_artifacts",
            "search_research_artifacts",
            "append_research_artifact_version",
            "export_research_artifact_md",
            "get_research_spec",
            "list_research_specs",
            "record_blind_judgment_prior",
            "complete_judgment_review",
            "get_forecast",
            "list_forecasts",
            "resolve_forecast",
            "score_forecast",
            "promote_lesson",
            "get_forecast_calibration_report",
            "get_blind_review_packet",
            "record_blind_human_review",
            "compare_evaluation_runs",
            "record_audit_event",
        ),
        forbidden_skill_tags=("approval", "execution", "order", "secret"),
    ),
}

ROLE_PURPOSES: dict[str, str] = {
    "head-manager": "Interprets the request, dynamically coordinates fixed subagents, waits for authenticated artifacts, and synthesizes the result.",
    "fundamental-analyst": "Reviews business quality, financial statements, official disclosures, and competitive position.",
    "technical-analyst": "Reviews price action, trend, volatility, liquidity, volume, and market setup.",
    "news-analyst": "Reviews news, official disclosures, event risk, catalysts, and narrative change.",
    "macro-analyst": "Reviews rates, FX, commodities, liquidity, policy, and cross-asset transmission.",
    "instrument-analyst": "Reviews ETF/index, options, derivatives, crypto public markets, credit signals, and instrument mechanics.",
    "valuation-analyst": "Builds valuation, scenario, multiple, DCF, reverse DCF, and expected-return views.",
    "portfolio-manager": "Reviews portfolio fit, sizing, cash, concentration, and draft order-ticket readiness.",
    "risk-manager": "Reviews risk, restricted list, downside, policy readiness, and approval receipt eligibility.",
    "judgment-reviewer": "Independently challenges accepted investment artifacts for contrary evidence, weak source trust, overconfidence, update triggers, and invalidation conditions before synthesis or downstream action gates.",
}

ROLE_DISPLAY_GROUPS: dict[str, str] = {
    "head-manager": "main",
    "fundamental-analyst": "research",
    "technical-analyst": "research",
    "news-analyst": "research",
    "macro-analyst": "research",
    "instrument-analyst": "research",
    "valuation-analyst": "analysis",
    "portfolio-manager": "portfolio",
    "risk-manager": "risk",
    "judgment-reviewer": "review",
}

ROLE_FORBIDDEN_ACTIONS: dict[str, tuple[str, ...]] = {
    "head-manager": (
        "Do not replace specialist analysis with direct analysis.",
        "Do not call broker APIs directly.",
        "Do not bypass policy, approval, adapter, or audit checks.",
    ),
    "fundamental-analyst": ("No order ticket.", "No approval.", "No execution.", "No secret access."),
    "technical-analyst": ("No order ticket.", "No execution.", "No standalone investment conclusion."),
    "news-analyst": ("No unverified rumor claims.", "No execution.", "No secret access."),
    "macro-analyst": ("No order ticket.", "No execution.", "No unsupported implementation claims."),
    "instrument-analyst": ("No order ticket.", "No execution.", "No unsupported instrument execution claims."),
    "valuation-analyst": ("No approval.", "No execution.", "No broker API calls."),
    "portfolio-manager": ("No self-approval.", "No execution.", "No arbitrary policy changes."),
    "risk-manager": ("No order drafting.", "No execution.", "No arbitrary policy changes."),
    "judgment-reviewer": ("No original analyst work.", "No portfolio sizing.", "No order ticket.", "No approval.", "No execution.", "No policy change."),
}

ROLE_HANDOFF_CONTRACTS: dict[str, dict[str, str]] = {
    "head-manager": {
        "receives": "User request, authenticated role artifacts, and bounded service state.",
        "returns": "Dynamic role decisions, compact briefs, accepted artifacts, conflicts, and next allowed action.",
        "quality_gate": "Reviews handoffs as accepted, revise, blocked, or waiting before choosing the next useful step.",
        "overlap_rule": "Coordinates and synthesizes; does not replace specialist role analysis.",
    },
    "fundamental-analyst": {
        "receives": "Assigned evidence and source references.",
        "returns": "Fundamental report with source/as-of posture, confidence, and missing evidence.",
        "quality_gate": "Business, financial, filing, and economics claims stay evidence-backed and role-owned.",
        "overlap_rule": "Does not create valuation, order-ticket, approval, or execution posture.",
    },
    "technical-analyst": {
        "receives": "Assigned market-data references and user-stated technical constraints.",
        "returns": "Technical report with setup observations, data posture, confidence, and invalidation gaps.",
        "quality_gate": "Price, volume, volatility, and liquidity claims show data timing and uncertainty.",
        "overlap_rule": "Does not turn a setup into a standalone investment conclusion or order ticket.",
    },
    "news-analyst": {
        "receives": "Assigned filings, disclosures, news, and source references.",
        "returns": "Dated event report with source-quality caveats and unresolved claims.",
        "quality_gate": "Events separate verified facts, source claims, narrative inference, and rumor risk.",
        "overlap_rule": "Does not approve execution or assert unverified rumors as facts.",
    },
    "macro-analyst": {
        "receives": "Assigned macro sources and relevant accepted role artifacts.",
        "returns": "Macro transmission report with source/as-of posture and regime uncertainty.",
        "quality_gate": "Transmission channels distinguish factual data, assumptions, and inference.",
        "overlap_rule": "Does not imply futures, FX, commodity, rates, options, or live execution support.",
    },
    "instrument-analyst": {
        "receives": "Assigned instrument sources, contract/methodology references, and support questions.",
        "returns": "Instrument support report with mechanics, liquidity/support gaps, and blocked execution implications.",
        "quality_gate": "Instrument facts, market-structure claims, and support gaps are source-backed.",
        "overlap_rule": "Does not infer execution eligibility from data availability or router coverage.",
    },
    "valuation-analyst": {
        "receives": "Accepted research artifacts and user-stated method constraints.",
        "returns": "Valuation report with assumptions, sensitivity, confidence, and readiness for portfolio/risk review.",
        "quality_gate": "Valuation assumptions and current-price implications are explicit and source-aware.",
        "overlap_rule": "Does not approve, execute, or replace missing research evidence.",
    },
    "portfolio-manager": {
        "receives": "Accepted research/valuation artifacts and portfolio state.",
        "returns": "Portfolio fit report and, only when allowed, draft order-ticket readiness or draft OrderTicket.",
        "quality_gate": "Sizing, cash, concentration, liquidity, and opportunity-cost assumptions are visible.",
        "overlap_rule": "Does not self-approve, execute, or repair missing research/valuation work.",
    },
    "risk-manager": {
        "receives": "Accepted portfolio/order artifacts, policy state, restricted-list state, and audit evidence.",
        "returns": "Risk/policy report, approval readiness state, approval receipt when allowed, or blocked reasons.",
        "quality_gate": "Downside, limits, restricted-list, and approval-readiness checks are explicit.",
        "overlap_rule": "Does not draft orders, submit execution, or loosen policy in the same workflow.",
    },
    "judgment-reviewer": {
        "receives": "Accepted upstream artifacts, source/as-of metadata, source trust notes, forecast fields, and explicit user constraints from head-manager.",
        "returns": "Independent judgment review artifact with strongest support, strongest contrary evidence, weak source posture, overconfidence risk, update triggers, invalidation conditions, and accepted/revise/blocked/waiting outcome.",
        "quality_gate": "A favorable thesis cannot move to synthesis, portfolio, risk, order, approval, or execution when material contrary evidence, stale source posture, or invalidation conditions are unresolved.",
        "overlap_rule": "Challenges and gates artifacts; does not create primary research, valuation, portfolio sizing, risk approval, order tickets, or execution posture.",
    },
}


CORE_SKILL_PREFIX = "tcx-"
CORE_SKILL_NAME_PATTERN = re.compile(r"^tcx-[a-z0-9]+(?:-[a-z0-9]+)?$")


SKILL_SPECS: dict[str, SkillSpec] = {
    "tcx-plan": SkillSpec("tcx-plan", "TCX Plan", ("head-manager",), user_visible=True),
    "tcx-workflow": SkillSpec("tcx-workflow", "TCX Workflow", ("head-manager",), user_visible=True),
    "tcx-memory": SkillSpec("tcx-memory", "TCX Memory", ("head-manager",), user_visible=True),
    "tcx-automate": SkillSpec("tcx-automate", "TCX Automate", ("head-manager",), user_visible=True),
    "tcx-dashboard": SkillSpec("tcx-dashboard", "TCX Dashboard", ("head-manager",), user_visible=True),
    "tcx-server": SkillSpec("tcx-server", "TCX Server", ("head-manager",), user_visible=True),
    "tcx-build": SkillSpec(
        "tcx-build",
        "TCX Build",
        ("head-manager",),
        risk_tags=("build", "native-only"),
        user_visible=True,
    ),
    "tcx-source-gate": SkillSpec("tcx-source-gate", "TCX Source Gate", RESEARCH_ROLES, scope="subagent_shared"),
    "tcx-investor-context": SkillSpec("tcx-investor-context", "TCX Investor Context", ("head-manager",), user_visible=True),
    "tcx-strategy": SkillSpec("tcx-strategy", "TCX Strategy", ("head-manager",), user_visible=True),
    "tcx-brain": SkillSpec(
        "tcx-brain",
        "TCX Brain",
        ("head-manager",),
        user_visible=True,
    ),
    "tcx-order-allow": SkillSpec(
        "tcx-order-allow",
        "TCX Order Allow",
        ("head-manager",),
        risk_tags=("execution", "order", "native-only"),
        user_visible=True,
    ),
    "tcx-order-submit": SkillSpec(
        "tcx-order-submit",
        "TCX Order Submit",
        ("head-manager",),
        risk_tags=("execution", "order", "native-only"),
        user_visible=True,
    ),
    "tcx-order-cancel": SkillSpec(
        "tcx-order-cancel",
        "TCX Order Cancel",
        ("head-manager",),
        risk_tags=("execution", "order", "native-only"),
        user_visible=True,
    ),
    "tcx-evidence": SkillSpec("tcx-evidence", "TCX Evidence", RESEARCH_ROLES, scope="subagent_shared"),
    "tcx-forecast": SkillSpec("tcx-forecast", "TCX Forecast", FORECASTING_DISCIPLINE_ROLES, scope="subagent_shared"),
    "tcx-judgment": SkillSpec("tcx-judgment", "TCX Judgment", JUDGMENT_REVIEW_ROLES, scope="subagent_role"),
    "tcx-scenarios": SkillSpec("tcx-scenarios", "TCX Scenarios", THESIS_SCENARIO_TREE_ROLES, scope="subagent_shared"),
    "tcx-data-qc": SkillSpec("tcx-data-qc", "TCX Data QC", NUMERIC_DATA_QC_ROLES, scope="subagent_shared"),
    "tcx-anti-overfit": SkillSpec("tcx-anti-overfit", "TCX Anti-Overfit", ANTI_OVERFIT_VALIDATION_ROLES, scope="subagent_shared"),
    "tcx-fundamental": SkillSpec("tcx-fundamental", "TCX Fundamental", ("fundamental-analyst",), scope="subagent_role"),
    "tcx-technical": SkillSpec("tcx-technical", "TCX Technical", ("technical-analyst",), scope="subagent_role"),
    "tcx-news": SkillSpec("tcx-news", "TCX News", ("news-analyst",), scope="subagent_role"),
    "tcx-macro": SkillSpec("tcx-macro", "TCX Macro", ("macro-analyst",), scope="subagent_role"),
    "tcx-instrument": SkillSpec("tcx-instrument", "TCX Instrument", ("instrument-analyst",), scope="subagent_role"),
    "tcx-valuation": SkillSpec("tcx-valuation", "TCX Valuation", ("valuation-analyst",), scope="subagent_role"),
    "tcx-portfolio": SkillSpec("tcx-portfolio", "TCX Portfolio", ("portfolio-manager",), scope="subagent_role"),
    "tcx-order-draft": SkillSpec("tcx-order-draft", "TCX Order Draft", ("portfolio-manager",), risk_tags=("order",), scope="subagent_role"),
    "tcx-risk": SkillSpec("tcx-risk", "TCX Risk", ("risk-manager",), scope="subagent_role"),
    "tcx-policy": SkillSpec("tcx-policy", "TCX Policy", ("risk-manager",), risk_tags=("approval",), scope="subagent_role"),
    "tcx-order-approve": SkillSpec("tcx-order-approve", "TCX Order Approve", ("risk-manager",), risk_tags=("approval", "order"), scope="subagent_role"),
}

INVALID_CORE_SKILL_NAMES = sorted(skill_id for skill_id in SKILL_SPECS if not CORE_SKILL_NAME_PATTERN.fullmatch(skill_id))
MISMATCHED_CORE_SKILL_IDS = sorted(skill_id for skill_id, spec in SKILL_SPECS.items() if spec.id != skill_id)
UNKNOWN_BUILTIN_SKILLS = sorted(
    {
        skill_id
        for agent in AGENT_SPECS.values()
        for skill_id in agent.builtin_skills
        if skill_id not in SKILL_SPECS
    }
)
if INVALID_CORE_SKILL_NAMES or MISMATCHED_CORE_SKILL_IDS or UNKNOWN_BUILTIN_SKILLS:
    raise RuntimeError(
        "invalid TradingCodex core skill registry: "
        f"bad_names={INVALID_CORE_SKILL_NAMES}, "
        f"mismatched_ids={MISMATCHED_CORE_SKILL_IDS}, "
        f"unknown_builtins={UNKNOWN_BUILTIN_SKILLS}"
    )


ROLE_SKILL_MAP: dict[str, list[str]] = {role: list(spec.builtin_skills) for role, spec in AGENT_SPECS.items()}
USER_VISIBLE_SKILLS = [skill.id for skill in SKILL_SPECS.values() if skill.user_visible]
EXPECTED_SUBAGENTS = [role for role in AGENT_SPECS if role != "head-manager"]
EXPECTED_SKILLS = sorted(SKILL_SPECS)

PROPOSAL_DIR = Path(".tradingcodex/mainagent/skill-change-proposals")
MAINAGENT_SKILL_DIR = Path(".agents/skills")
STRATEGY_SKILL_DIR = MAINAGENT_SKILL_DIR
SUBAGENT_SKILL_DIR = Path(".tradingcodex/subagents/skills")
SUBAGENT_SHARED_SKILL_DIR = SUBAGENT_SKILL_DIR / "shared"
OPTIONAL_SKILL_STATUS_FILE = Path("agents/tradingcodex.json")
ADDITIONAL_INSTRUCTION_DIR = Path(".tradingcodex/agent-instructions")
ADDITIONAL_INSTRUCTION_START = "## BEGIN TradingCodex additional instructions"
ADDITIONAL_INSTRUCTION_END = "## END TradingCodex additional instructions"
ROLE_SKILL_SOURCE_START = "## BEGIN TradingCodex role skill sources"
ROLE_SKILL_SOURCE_END = "## END TradingCodex role skill sources"
CORE_EXTENSION_BOUNDARY_START = "## BEGIN TradingCodex immutable core and extension boundary"
CORE_EXTENSION_BOUNDARY_END = "## END TradingCodex immutable core and extension boundary"
GENERATED_DIR = Path(".tradingcodex/generated")
MANIFEST_PATH = GENERATED_DIR / "projection-manifest.json"
AGENT_INDEX_PATH = GENERATED_DIR / "agent-index.json"
SKILL_INDEX_PATH = GENERATED_DIR / "skill-index.json"
SKILL_INVENTORY_SCOPE = "tradingcodex_managed_workspace"
HOST_GLOBAL_SKILL_POLICY = "detect_collisions_do_not_import"
STRATEGY_SKILL_PREFIX = "strategy-"
STRATEGY_ROOT_CONFIG_START = "# BEGIN TradingCodex strategy skills"
STRATEGY_ROOT_CONFIG_END = "# END TradingCodex strategy skills"
INVESTMENT_BRAIN_SKILL_PREFIX = "investment-brain-"
INVESTMENT_BRAIN_ROOT_CONFIG_START = "# BEGIN TradingCodex investment brain skills"
INVESTMENT_BRAIN_ROOT_CONFIG_END = "# END TradingCodex investment brain skills"
STRATEGY_REQUIRED_FRONTMATTER = {
    "name",
    "description",
    "type",
    "status",
    "language",
    "owner",
    "last_reviewed",
}
STRATEGY_REQUIRED_SECTIONS = (
    "## Thesis",
    "## Eligible Universe",
    "## Preferred Setups",
    "## Entry Criteria",
    "## Exit Criteria",
    "## Evidence Requirements",
    "## Decision-Ready Standard",
    "## Sizing Guidance",
    "## Risk Controls",
    "## Block Conditions",
    "## Change Log",
)
STRATEGY_FORBIDDEN_COUPLING_PATTERN = re.compile(
    r"\b("
    r"TradingCodex|head-manager|subagent|subagents|portfolio-manager|risk-manager|"
    r"MCP|handoff|handoffs"
    r")\b|"
    r"\b(approval[\s_-]*gates?|execution[\s_-]*gates?|role[\s_-]*boundar(?:y|ies)|broker[\s_-]*authority)\b",
    re.I,
)
OPTIONAL_SKILL_STATUSES = {"draft", "active", "archived"}
SKILL_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{2,63}$")
OPTIONAL_SKILL_RISK_PATTERNS = {
    "approval": re.compile(
        r"\b((approve|reject)\s+((an?|the)\s+)?orders?|request\s+order\s+approval|"
        r"(create|issue|validate)\s+((an?|the)\s+)?approval\s+receipts?|self[- ]approve)\b",
        re.I,
    ),
    "execution": re.compile(
        r"\b((execute|submit|cancel|replace)\s+((an?|the)\s+)?orders?|"
        r"order\s+(execution|submission|cancellation)|adapter\s+submission|direct\s+broker\s+access)\b",
        re.I,
    ),
    "order": re.compile(
        r"\b((create|draft|submit|approve|execute|cancel|replace)\s+((an?|the)\s+)?"
        r"(orders?|order\s+tickets?)|order\s+(creation|submission|approval|execution|cancellation))\b",
        re.I,
    ),
    "secret": re.compile(r"\b(secrets?|credentials?|tokens?|api[\s_-]*keys?|passwords?|\\.env)\b", re.I),
}
OPTIONAL_SKILL_LOCKED_SURFACE_PATTERN = re.compile(
    r"\b("
    r"mcp allowlist|sandbox|raw broker|live broker|direct broker|"
    r"bypass|ignore policy|disable guardrail|weaken guardrail|self-approve|"
    r"change policy|change capability|read secret|secret access"
    r")\b",
    re.I,
)


def registry_summary() -> dict[str, Any]:
    return {
        "source": "tradingcodex_service.application.agents",
        "agents": {role: _agent_spec_payload(spec) for role, spec in AGENT_SPECS.items()},
        "skills": {skill_id: asdict(spec) for skill_id, spec in SKILL_SPECS.items()},
        "expected_subagents": EXPECTED_SUBAGENTS,
        "expected_skills": EXPECTED_SKILLS,
    }


def inspect_agent_configuration(root: Path | str, role: str) -> dict[str, Any]:
    root = Path(root).resolve()
    if role not in AGENT_SPECS:
        raise ValueError(f"Unknown subagent or role: {role}")
    state = build_projection_state(root)
    return state["agents"][role]


def list_optional_role_skills(root: Path | str, role: str | None = None, include_archived: bool = True) -> dict[str, Any]:
    root = Path(root).resolve()
    records = read_optional_skill_records(root, role=role, include_archived=include_archived)
    return {
        "status": "ok",
        "source": "file-native-optional-skills",
        "read_only": True,
        "optional_skills": records,
        "roles": sorted({record["role"] for record in records}),
    }


def list_user_visible_skills(root: Path | str) -> list[str]:
    root = Path(root).resolve()
    if not (root / MAINAGENT_SKILL_DIR).exists():
        return list(USER_VISIBLE_SKILLS)
    installed = _installed_skill_index(root)
    visible = [skill for skill in USER_VISIBLE_SKILLS if installed.get(skill, {}).get("installed")]
    visible.extend(skill["name"] for skill in read_strategy_skill_records(root, active_only=True))
    visible.extend(
        skill_id
        for skill_id, skill in installed.items()
        if skill.get("layer") == "workspace_investment_brain" and skill.get("active")
    )
    return list(dict.fromkeys(visible))


def read_strategy_skill_records(root: Path | str, *, active_only: bool = False) -> list[dict[str, Any]]:
    root = Path(root).resolve()
    records: list[dict[str, Any]] = []
    seen_names: set[str] = set()
    strategy_root = root / STRATEGY_SKILL_DIR
    if strategy_root.exists():
        for skill_path in sorted(strategy_root.glob(f"{STRATEGY_SKILL_PREFIX}*/SKILL.md")):
            name = skill_path.parent.name
            if name in SKILL_SPECS:
                continue
            if name in seen_names:
                continue
            seen_names.add(name)
            record = _strategy_record_payload(root, skill_path)
            if active_only and not record["active"]:
                continue
            records.append(record)
    return records


def read_optional_skill_records(root: Path | str, role: str | None = None, include_archived: bool = True) -> list[dict[str, Any]]:
    root = Path(root).resolve()
    records: list[dict[str, Any]] = []
    base = root / SUBAGENT_SKILL_DIR
    if base.exists():
        for path in sorted(base.glob("*/*/SKILL.md")):
            scope_name = path.parent.parent.name
            name = path.parent.name
            if name in SKILL_SPECS:
                continue
            metadata_path = path.parent / OPTIONAL_SKILL_STATUS_FILE
            record = read_json(metadata_path, {}) or {}
            roles = _optional_record_roles(record, scope_name)
            if scope_name == "shared" and not roles:
                roles = [""]
            for target_role in roles:
                if role and target_role != role:
                    continue
                candidate = {
                    **record,
                    "role": target_role,
                    "name": name,
                    "scope": "shared" if scope_name == "shared" else "role",
                    "source_file": _relative_path(root, path),
                    "metadata_file": _relative_path(root, path.parent / "agents" / "openai.yaml"),
                    "status_file": _relative_path(root, metadata_path),
                }
                payload = _optional_record_payload(root, candidate)
                if not include_archived and payload.get("status") == "archived":
                    continue
                records.append(payload)
    return records


def read_agent_additional_instructions(root: Path | str, role: str) -> dict[str, Any]:
    root = Path(root).resolve()
    if role not in AGENT_SPECS:
        raise ValueError(f"Unknown subagent or role: {role}")
    path = _additional_instruction_path(root, role)
    body = _safe_read(path)
    return {
        "role": role,
        "body": body,
        "installed": path.exists(),
        "source_file": _relative_path(root, path),
        "source_file_hash": _file_hash(path),
        "line_count": len(body.splitlines()) if body else 0,
        "char_count": len(body),
    }


def write_agent_additional_instructions(
    root: Path | str,
    role: str,
    body: str,
    *,
    actor: str = "local",
) -> dict[str, Any]:
    root = Path(root).resolve()
    if role not in AGENT_SPECS:
        raise ValueError(f"Unknown subagent or role: {role}")
    path = _additional_instruction_path(root, role)
    cleaned = str(body or "").strip()
    if cleaned:
        _atomic_write_text(path, cleaned + "\n")
    else:
        path.unlink(missing_ok=True)
    project_agent_configuration(root, role=role, applied_by=actor)
    return read_agent_additional_instructions(root, role)


def validate_optional_skill_payload(role: str, name: str, description: str = "", body: str = "") -> dict[str, Any]:
    errors: list[str] = []
    if role not in EXPECTED_SUBAGENTS:
        errors.append(f"optional skills can target fixed subagents only: {role}")
    if name in SKILL_SPECS:
        errors.append(f"core skill cannot be overwritten: {name}")
    if name.startswith(CORE_SKILL_PREFIX):
        errors.append("optional skill name cannot use reserved tcx- prefix")
    if name.startswith(STRATEGY_SKILL_PREFIX):
        errors.append("optional skill name cannot use reserved strategy- prefix")
    if name.startswith(INVESTMENT_BRAIN_SKILL_PREFIX):
        errors.append("optional skill name cannot use reserved investment-brain- prefix")
    if not SKILL_NAME_PATTERN.match(name):
        errors.append("optional skill name must be lowercase hyphen-case, 3-64 characters")
    combined = "\n".join([name, description, body])
    if OPTIONAL_SKILL_LOCKED_SURFACE_PATTERN.search(combined):
        errors.append("optional skills cannot change locked harness surfaces")
    risk_tags = infer_optional_skill_risk_tags(combined)
    agent = AGENT_SPECS.get(role)
    if agent:
        blocked_tags = sorted(set(agent.forbidden_skill_tags).intersection(risk_tags))
        if blocked_tags:
            errors.append(f"{role} cannot receive {name}; blocked risk tags: {', '.join(blocked_tags)}")
    return {"status": "blocked" if errors else "valid", "errors": errors, "risk_tags": risk_tags}


def infer_optional_skill_risk_tags(text: str) -> list[str]:
    return sorted(tag for tag, pattern in OPTIONAL_SKILL_RISK_PATTERNS.items() if pattern.search(text))


def normalize_optional_skill_name(raw: str) -> str:
    name = sanitize_id(raw).strip("-").lower()
    if name.startswith(CORE_SKILL_PREFIX):
        raise ValueError("optional skill name cannot use reserved tcx- prefix")
    return name


def create_or_update_strategy_skill(
    root: Path | str,
    name: str,
    *,
    description: str = "",
    body: str = "",
    language: str = "unknown",
    status: str = "draft",
    actor: str = "local",
) -> dict[str, Any]:
    root = Path(root).resolve()
    name = normalize_strategy_skill_name(name)
    if status not in {"draft", "active", "archived"}:
        raise ValueError(f"unknown strategy status: {status}")
    if name in SKILL_SPECS:
        raise ValueError(f"core skill cannot be overwritten: {name}")
    skill_dir = root / STRATEGY_SKILL_DIR / name
    skill_path = skill_dir / "SKILL.md"
    current_fields = _read_frontmatter_fields(skill_path)
    current_body = _read_markdown_body(skill_path)
    current_name = current_fields.get("name") or name
    if current_name != name:
        raise ValueError(f"strategy skill name must match its directory: {name}")
    description = description or current_fields.get("description") or f"Apply the {name} strategy."
    language = language or current_fields.get("language") or "unknown"
    body = body if body.strip() else current_body or _default_strategy_body(name)
    text = _render_strategy_skill_markdown(name, description, body, language, status)
    validation_errors = _validate_strategy_skill_text(name, text)
    if status == "active" and validation_errors:
        raise ValueError("; ".join(validation_errors))
    _atomic_write_text(skill_path, text)
    _atomic_write_text(skill_dir / "agents" / "openai.yaml", _render_openai_yaml(_strategy_display_name(name, body), description, f"Use ${name} to apply this user-approved strategy."))
    project_agent_configuration(root, applied_by=actor)
    return _strategy_record_payload(root, skill_path)


def set_strategy_skill_status(root: Path | str, name: str, status: str, *, actor: str = "local") -> dict[str, Any]:
    root = Path(root).resolve()
    name = normalize_strategy_skill_name(name)
    record = get_strategy_skill_record(root, name)
    fields = dict(record.get("frontmatter") or {})
    body = _read_markdown_body(root / str(record["source_file"]))
    updated = create_or_update_strategy_skill(
        root,
        name,
        description=fields.get("description", ""),
        body=body,
        language=fields.get("language", "unknown"),
        status=status,
        actor=actor,
    )
    return updated


def delete_strategy_skill(root: Path | str, name: str, *, force: bool = False, actor: str = "local") -> dict[str, Any]:
    root = Path(root).resolve()
    name = normalize_strategy_skill_name(name)
    record = get_strategy_skill_record(root, name)
    if record.get("status") == "active" and not force:
        return set_strategy_skill_status(root, name, "archived", actor=actor)
    source = root / str(record["source_file"])
    shutil.rmtree(source.parent)
    if source.parent.exists():
        raise RuntimeError(f"strategy skill directory could not be removed: {source.parent}")
    project_agent_configuration(root, applied_by=actor)
    return {"name": name, "status": "deleted", "active": False}


def get_strategy_skill_record(root: Path | str, name: str) -> dict[str, Any]:
    name = normalize_strategy_skill_name(name)
    for record in read_strategy_skill_records(root, active_only=False):
        if record["name"] == name:
            return record
    raise ValueError(f"unknown strategy: {name}")


def normalize_strategy_skill_name(raw: str) -> str:
    name = sanitize_id(raw).strip("-").lower()
    if not name.startswith(STRATEGY_SKILL_PREFIX):
        raise ValueError("strategy skill name must start with strategy-")
    if not SKILL_NAME_PATTERN.match(name):
        raise ValueError("strategy skill name must be lowercase hyphen-case, 3-64 characters")
    return name


def create_or_update_optional_skill(
    root: Path | str,
    role: str,
    name: str,
    *,
    description: str = "",
    body: str = "",
    status: str = "draft",
    actor: str = "local",
) -> dict[str, Any]:
    root = Path(root).resolve()
    if role not in EXPECTED_SUBAGENTS:
        raise ValueError(f"optional skills can target fixed subagents only: {role}")
    name = normalize_optional_skill_name(name)
    if status not in OPTIONAL_SKILL_STATUSES:
        raise ValueError(f"unknown optional skill status: {status}")
    if name in SKILL_SPECS or (root / MAINAGENT_SKILL_DIR / name).exists():
        raise ValueError(f"core or project-scope skill cannot be overwritten: {name}")
    for record in read_optional_skill_records(root, include_archived=True):
        if record.get("name") == name and record.get("role") != role and record.get("status") != "archived":
            raise ValueError(f"optional skill name already belongs to another role: {name}")
    skill_dir = root / SUBAGENT_SKILL_DIR / role / name
    skill_path = skill_dir / "SKILL.md"
    current_fields = _read_frontmatter_fields(skill_path)
    current_body = _read_markdown_body(skill_path)
    current_name = current_fields.get("name") or name
    if current_name != name:
        raise ValueError(f"optional skill name must match its directory: {name}")
    description = description or current_fields.get("description") or f"Use the {name} optional procedure."
    body = body if body.strip() else current_body or f"# {_skill_display_name(name, '')}\n\nDescribe the optional role-local procedure.\n"
    validation = validate_optional_skill_payload(role, name, description, body)
    if status == "active" and validation["errors"]:
        raise ValueError("; ".join(validation["errors"]))
    _atomic_write_text(skill_path, _render_basic_skill_markdown(name, description, body))
    _atomic_write_text(skill_dir / "agents" / "openai.yaml", _render_openai_yaml(_skill_display_name(name, body), description, f"Use ${name} for the {role} optional procedure."))
    metadata = {
        "role": role,
        "name": name,
        "scope": "role",
        "status": status,
        "updated_by": actor,
        "updated_at": now_iso(),
    }
    status_path = skill_dir / OPTIONAL_SKILL_STATUS_FILE
    existing = read_json(status_path, {}) or {}
    if existing.get("created_at"):
        metadata["created_at"] = existing["created_at"]
        metadata["created_by"] = existing.get("created_by", actor)
    else:
        metadata["created_at"] = metadata["updated_at"]
        metadata["created_by"] = actor
    _atomic_write_json(status_path, metadata)
    project_agent_configuration(root, role=role, applied_by=actor)
    return next(record for record in read_optional_skill_records(root, role=role, include_archived=True) if record["name"] == name)


def set_optional_skill_status(root: Path | str, role: str, name: str, status: str, *, actor: str = "local") -> dict[str, Any]:
    root = Path(root).resolve()
    name = normalize_optional_skill_name(name)
    record = get_optional_skill_record(root, role, name)
    body = _read_markdown_body(root / str(record["source_file"]))
    return create_or_update_optional_skill(
        root,
        role,
        name,
        description=str(record.get("description") or ""),
        body=body,
        status=status,
        actor=actor,
    )


def delete_optional_skill(root: Path | str, role: str, name: str, *, force: bool = False, actor: str = "local") -> dict[str, Any]:
    root = Path(root).resolve()
    name = normalize_optional_skill_name(name)
    record = get_optional_skill_record(root, role, name)
    if record.get("status") == "active" and not force:
        return set_optional_skill_status(root, role, name, "archived", actor=actor)
    source = root / str(record["source_file"])
    shutil.rmtree(source.parent)
    if source.parent.exists():
        raise RuntimeError(f"optional skill directory could not be removed: {source.parent}")
    project_agent_configuration(root, role=role, applied_by=actor)
    return {"role": role, "name": name, "status": "deleted"}


def get_optional_skill_record(root: Path | str, role: str, name: str) -> dict[str, Any]:
    name = normalize_optional_skill_name(name)
    for record in read_optional_skill_records(root, role=role, include_archived=True):
        if record["name"] == name:
            return record
    raise ValueError(f"unknown optional skill for {role}: {name}")


def _optional_records_by_role(records: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    by_role: dict[str, list[dict[str, Any]]] = {role: [] for role in AGENT_SPECS}
    for record in records:
        by_role.setdefault(str(record.get("role") or ""), []).append(record)
    return by_role


def diff_agent_configuration(root: Path | str, role: str) -> dict[str, Any]:
    root = Path(root).resolve()
    if role not in AGENT_SPECS:
        raise ValueError(f"Unknown subagent or role: {role}")
    state = build_projection_state(root)
    agent = state["agents"][role]
    builtin = set(agent["builtin_skills"])
    current = set(agent["projected_skills"])
    effective = set(agent["effective_skills"])
    return {
        "role": role,
        "builtin_skills": agent["builtin_skills"],
        "projected_skills": agent["projected_skills"],
        "effective_skills": agent["effective_skills"],
        "pending_proposals": agent["pending_proposals"],
        "applied_proposals": agent["applied_proposals"],
        "missing_from_projected": sorted(effective - current),
        "extra_projected": sorted(current - effective),
        "pending_additions": sorted(effective - builtin),
        "validation_errors": agent["validation_errors"],
        "codex_file": agent["codex_file"],
        "projection_manifest": state["projection_manifest"],
    }


def project_agent_configuration(
    root: Path | str,
    *,
    role: str | None = None,
    proposal_path: Path | str | None = None,
    applied_by: str = "local",
    generated_at: str | None = None,
) -> dict[str, Any]:
    root = Path(root).resolve()
    _assert_projection_write_targets(root)
    from tradingcodex_service.application.investment_brains import project_investment_brain_skills

    project_investment_brain_skills(root)
    selected_role = role
    proposal_record: dict[str, Any] | None = None
    if proposal_path:
        proposal = Path(proposal_path)
        proposal = proposal if proposal.is_absolute() else root / proposal
        proposal_record = read_skill_proposal(proposal, root)
        selected_role = selected_role or proposal_record.get("target")
        validation_errors = validate_skill_assignment(str(proposal_record.get("target", "")), str(proposal_record.get("skill", "")))
        if validation_errors:
            _rewrite_skill_proposal(root, proposal_record, proposal, "blocked", applied_by, validation_errors)
            raise ValueError("; ".join(validation_errors))
        _rewrite_skill_proposal(root, proposal_record, proposal, "applied", applied_by, [])
        proposal_record = read_skill_proposal(proposal, root)

    if selected_role and selected_role not in AGENT_SPECS:
        raise ValueError(f"Unknown subagent or role: {selected_role}")

    state = build_projection_state(root)
    for role_id in AGENT_SPECS:
        _project_agent_model_policy(root, role_id)
    if selected_role:
        roles_to_project = [selected_role] if selected_role != "head-manager" else []
    else:
        roles_to_project = [role_id for role_id in EXPECTED_SUBAGENTS]
    for role_id in roles_to_project:
        _project_agent_toml(
            root,
            role_id,
            state["agents"][role_id]["effective_skills"],
            state["agents"][role_id]["additional_instructions"]["body"],
        )
    if selected_role in {None, "head-manager"}:
        _project_head_manager_mcp_tools(root)
        _project_head_manager_prompt(root, state["agents"]["head-manager"]["additional_instructions"]["body"])
    _project_root_strategy_skills(root)
    _project_root_investment_brain_skills(root)

    refreshed = build_projection_state(root)
    generated_at = generated_at or now_iso()
    _write_projection_indexes(root, refreshed, applied_by, generated_at, proposal_record)
    return build_projection_state(root)


def write_skill_proposal_file(root: Path | str, type_: str, target: str, skill: str) -> dict[str, Any]:
    root = Path(root).resolve()
    now = datetime.now(timezone.utc)
    proposal_id = f"skill-{type_}-{target}-{skill}-{now.strftime('%Y%m%dT%H%M%S%fZ')}"
    path = root / PROPOSAL_DIR / f"{sanitize_id(proposal_id)}.yaml"
    validation_errors = validate_skill_assignment(target, skill)
    status = "blocked" if validation_errors else "proposed"
    fields = {
        "id": proposal_id,
        "type": type_,
        "target": target,
        "skill": skill,
        "created_at": now.isoformat().replace("+00:00", "Z"),
        "requires_validation": "true",
        "status": status,
        "validation_status": "blocked" if validation_errors else "valid",
    }
    if validation_errors:
        fields["validation_error"] = "; ".join(validation_errors)
    _write_simple_yaml(path, fields)
    result = {"status": status, "id": proposal_id, "path": path.relative_to(root).as_posix(), "validation_errors": validation_errors}
    return result


def read_skill_proposals(root: Path | str) -> list[dict[str, Any]]:
    root = Path(root).resolve()
    proposals: list[dict[str, Any]] = []
    for path in sorted((root / PROPOSAL_DIR).glob("*.yaml")):
        proposals.append(read_skill_proposal(path, root))
    return proposals


def read_skill_proposal(path: Path, root: Path | None = None) -> dict[str, Any]:
    root = root.resolve() if root else None
    data = _read_simple_yaml(path)
    data["path"] = path.relative_to(root).as_posix() if root and path.is_relative_to(root) else str(path)
    data["source_file_hash"] = _file_hash(path)
    return data


def validate_skill_assignment(role: str, skill: str) -> list[str]:
    errors: list[str] = []
    agent = AGENT_SPECS.get(role)
    skill_spec = SKILL_SPECS.get(skill)
    if not agent:
        errors.append(f"unknown role: {role}")
    if not skill_spec:
        errors.append(f"unknown skill: {skill}")
    if errors or not agent or not skill_spec:
        return errors
    if role != "head-manager" and skill_spec.scope == "mainagent":
        errors.append(f"{role} cannot receive project-scope mainagent skill: {skill}")
    blocked_tags = sorted(set(agent.forbidden_skill_tags).intersection(skill_spec.risk_tags))
    if role == "head-manager" and skill in NATIVE_EXECUTION_SKILLS:
        blocked_tags = []
    if blocked_tags:
        errors.append(f"{role} cannot receive {skill}; blocked risk tags: {', '.join(blocked_tags)}")
    if skill_spec.owner_roles and role not in skill_spec.owner_roles:
        errors.append(f"{role} is not an owner role for {skill}")
    return errors


def skills_for_role(root: Path | str, role: str) -> list[str]:
    return inspect_agent_configuration(root, role)["effective_skills"]


def build_projection_state(root: Path | str) -> dict[str, Any]:
    root = Path(root).resolve()
    manifest = read_json(root / MANIFEST_PATH, {}) or {}
    applied_by_role: dict[str, list[dict[str, Any]]] = {role: [] for role in AGENT_SPECS}
    pending_by_role: dict[str, list[dict[str, Any]]] = {role: [] for role in AGENT_SPECS}
    blocked_by_role: dict[str, list[dict[str, Any]]] = {role: [] for role in AGENT_SPECS}
    for proposal in read_skill_proposals(root):
        target = str(proposal.get("target", ""))
        if target not in AGENT_SPECS:
            continue
        status = str(proposal.get("status", "proposed"))
        if status == "applied":
            applied_by_role[target].append(proposal)
        elif status == "blocked":
            blocked_by_role[target].append(proposal)
        else:
            pending_by_role[target].append(proposal)

    agents: dict[str, dict[str, Any]] = {}
    skill_projection_exists = any((root / MAINAGENT_SKILL_DIR).glob("*/SKILL.md")) or any(
        (root / SUBAGENT_SKILL_DIR).glob("**/SKILL.md")
    )
    optional_records = read_optional_skill_records(root, include_archived=True)
    optional_by_role = _optional_records_by_role(optional_records)
    from tradingcodex_service.application.investment_brains import investment_brain_skill_index_records

    brain_records = investment_brain_skill_index_records(root)
    active_brains = [
        str(record["id"])
        for record in brain_records
        if record.get("active") and record.get("validation_status") == "valid"
    ]
    for role, spec in AGENT_SPECS.items():
        applied_skills = [str(item.get("skill")) for item in applied_by_role[role] if item.get("skill")]
        active_optional = [
            str(record.get("name"))
            for record in optional_by_role.get(role, [])
            if record.get("status") == "active" and not record.get("validation_errors")
        ]
        effective = _unique_existing(
            root,
            [
                *spec.builtin_skills,
                *applied_skills,
                *active_optional,
                *(active_brains if role == "head-manager" else []),
            ],
            role=role,
        )
        agent_file = _agent_config_path(root, role)
        projected_skills = _parse_toml_skill_paths(agent_file.read_text(encoding="utf-8")) if agent_file.exists() else []
        validation_errors: list[str] = []
        for skill in effective:
            if skill in SKILL_SPECS:
                validation_errors.extend(validate_skill_assignment(role, skill))
        for optional in optional_by_role.get(role, []):
            validation_errors.extend(optional.get("validation_errors") or [])
        additional = read_agent_additional_instructions(root, role)
        agents[role] = {
            **_agent_spec_payload(spec),
            "codex_file": _relative_path(root, agent_file) if agent_file else "",
            "codex_file_hash": _file_hash(agent_file) if agent_file else None,
            "builtin_skills": list(spec.builtin_skills)
            if not skill_projection_exists
            else [skill for skill in spec.builtin_skills if _skill_path(root, skill, role=role).exists()],
            "effective_skills": effective,
            "projected_skills": projected_skills,
            "pending_proposals": [_proposal_summary(proposal) for proposal in pending_by_role[role]],
            "applied_proposals": [_proposal_summary(proposal) for proposal in applied_by_role[role]],
            "blocked_proposals": [_proposal_summary(proposal) for proposal in blocked_by_role[role]],
            "optional_skills": optional_by_role.get(role, []),
            "optional_skill_count": len([record for record in optional_by_role.get(role, []) if record.get("status") == "active"]),
            "additional_instructions": additional,
            "additional_instruction_count": additional["line_count"],
            "validation_errors": sorted(set(validation_errors)),
            "mcp_allowlist": list(spec.mcp_allowlist),
        }

    skills = _installed_skill_index(root, optional_records)
    host_global_skill_collisions = detect_host_global_skill_collisions(root, skills)
    projection_input = {
        "agents": {
            role: {
                "effective_skills": agent["effective_skills"],
                "codex_file_hash": agent["codex_file_hash"],
                "additional_instructions_hash": agent["additional_instructions"]["source_file_hash"],
                "purpose": agent["purpose"],
                "handoff_contract": agent["handoff_contract"],
                "forbidden_actions": agent["forbidden_actions"],
                "mcp_allowlist": agent["mcp_allowlist"],
                "model_policy": agent["model_policy"],
            }
            for role, agent in agents.items()
        },
        "skills": {skill_id: item["source_file_hash"] for skill_id, item in skills.items()},
        "applied_proposals": {
            role: [proposal["source_file_hash"] for proposal in agent["applied_proposals"]]
            for role, agent in agents.items()
        },
    }
    return {
        "root": str(root),
        "registry": "tradingcodex_service.application.agents",
        "agents": agents,
        "skills": skills,
        "inventory_scope": SKILL_INVENTORY_SCOPE,
        "runtime_discovery_complete": False,
        "host_global_policy": HOST_GLOBAL_SKILL_POLICY,
        "host_global_skill_collisions": host_global_skill_collisions,
        "projection_hash": stable_hash(projection_input),
        "projection_manifest": manifest,
    }


def detect_host_global_skill_collisions(
    root: Path | str,
    managed_skills: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    root = Path(root).resolve()
    managed_skills = managed_skills or _installed_skill_index(root)
    home = Path(os.environ.get("HOME") or Path.home()).expanduser()
    codex_home = Path(os.environ.get("CODEX_HOME") or home / ".codex").expanduser()
    global_roots = list(dict.fromkeys([home / ".agents" / "skills", codex_home / "skills"]))
    collisions: list[dict[str, Any]] = []
    seen_paths: set[Path] = set()
    for global_root in global_roots:
        for skill_path in sorted(global_root.glob("*/SKILL.md")):
            resolved = skill_path.resolve()
            if resolved in seen_paths or resolved.is_relative_to(root):
                continue
            seen_paths.add(resolved)
            skill_id = skill_path.parent.name
            managed = managed_skills.get(skill_id)
            if not managed:
                continue
            collisions.append(
                {
                    "id": skill_id,
                    "layer": "host_global_unmanaged",
                    "trust_scope": "unmanaged",
                    "implicit_invocation": None,
                    "resolved_source_file": _portable_host_path(resolved, home, codex_home),
                    "managed_layer": managed.get("layer"),
                    "managed_resolved_source_file": managed.get("resolved_source_file"),
                }
            )
    return collisions


def inspect_skill_projection(
    root: Path | str,
    role: str,
    state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    root = Path(root).resolve()
    if role not in AGENT_SPECS:
        raise ValueError(f"Unknown subagent or role: {role}")
    state = state or build_projection_state(root)
    expected_ids = list(state["agents"][role]["effective_skills"])
    if role == "head-manager":
        expected_ids.extend(
            skill_id
            for skill_id, skill in state["skills"].items()
            if skill.get("layer") == "workspace_strategy" and skill.get("active")
        )
    expected_paths = sorted(
        {
            _resolve_workspace_path(root, str(state["skills"][skill_id]["resolved_source_file"]))
            for skill_id in expected_ids
            if skill_id in state["skills"] and state["skills"][skill_id].get("resolved_source_file")
        }
    )
    config_path = _agent_config_path(root, role)
    enabled_paths = _enabled_skill_config_paths(config_path)
    counts = Counter(enabled_paths)
    duplicates = sorted(path for path, count in counts.items() if count > 1)
    expected_set = set(expected_paths)
    enabled_set = set(enabled_paths)
    registered_paths = {
        _resolve_workspace_path(root, str(skill["resolved_source_file"]))
        for skill in state["skills"].values()
        if skill.get("resolved_source_file")
    }
    missing_paths = sorted(expected_set - enabled_set)
    extra_paths = sorted(enabled_set - expected_set)
    unregistered_paths = sorted(enabled_set - registered_paths)
    return {
        "role": role,
        "config_file": _relative_path(root, config_path),
        "expected_paths": expected_paths,
        "enabled_paths": enabled_paths,
        "missing_paths": missing_paths,
        "extra_paths": extra_paths,
        "unregistered_paths": unregistered_paths,
        "duplicate_paths": duplicates,
        "ok": not missing_paths and not extra_paths and not duplicates,
    }


def _write_projection_indexes(
    root: Path,
    state: dict[str, Any],
    applied_by: str,
    generated_at: str,
    proposal_record: dict[str, Any] | None,
) -> None:
    agent_index = {
        "generated_at": generated_at,
        "source": "tradingcodex_service.application.agents",
        "projection_hash": state["projection_hash"],
        "agents": state["agents"],
    }
    skill_index = {
        "generated_at": generated_at,
        "source": "workspace-files",
        "projection_hash": state["projection_hash"],
        "inventory_scope": SKILL_INVENTORY_SCOPE,
        "runtime_discovery_complete": False,
        "host_global_policy": HOST_GLOBAL_SKILL_POLICY,
        "host_global_skill_collisions": state["host_global_skill_collisions"],
        "skills": state["skills"],
    }
    manifest_roles = []
    for role, agent in state["agents"].items():
        manifest_roles.append(
            {
                "role": role,
                "codex_file": agent["codex_file"],
                "source_file_hash": agent["codex_file_hash"],
                "effective_skills": [
                    {
                        "id": skill,
                        "skill": skill,
                        "layer": state["skills"].get(skill, {}).get("layer"),
                        "trust_scope": state["skills"].get(skill, {}).get("trust_scope"),
                        "implicit_invocation": state["skills"].get(skill, {}).get("implicit_invocation", False),
                        "source_file": state["skills"].get(skill, {}).get("source_file", ""),
                        "resolved_source_file": state["skills"].get(skill, {}).get("resolved_source_file", ""),
                        "source_file_hash": state["skills"].get(skill, {}).get("source_file_hash"),
                    }
                    for skill in agent["effective_skills"]
                ],
                "additional_instructions": {
                    "source_file": agent["additional_instructions"]["source_file"],
                    "source_file_hash": agent["additional_instructions"]["source_file_hash"],
                    "line_count": agent["additional_instructions"]["line_count"],
                },
            }
        )
    manifest = {
        "generated_at": generated_at,
        "applied_by": applied_by,
        "projection_hash": state["projection_hash"],
        "source": "file-native-agent-skill-projection",
        "inventory_scope": SKILL_INVENTORY_SCOPE,
        "runtime_discovery_complete": False,
        "host_global_policy": HOST_GLOBAL_SKILL_POLICY,
        "host_global_skill_collisions": state["host_global_skill_collisions"],
        "proposal": _proposal_summary(proposal_record) if proposal_record else None,
        "roles": manifest_roles,
    }
    model_policy_manifest = {
        "generated_at": generated_at,
        "source": "tradingcodex_service.application.agents",
        "policy_revision": MODEL_POLICY_REVISION,
        "policy_hash": stable_hash({role: agent["model_policy"] for role, agent in state["agents"].items()}),
        "roles": {
            role: {
                **agent["model_policy"],
                "codex_file": agent["codex_file"],
                "codex_file_hash": agent["codex_file_hash"],
            }
            for role, agent in state["agents"].items()
        },
    }
    write_json(root / AGENT_INDEX_PATH, agent_index)
    write_json(root / SKILL_INDEX_PATH, skill_index)
    write_json(root / MANIFEST_PATH, manifest)
    write_json(root / MODEL_POLICY_MANIFEST_PATH, model_policy_manifest)


def _optional_record_payload(root: Path, record: dict[str, Any]) -> dict[str, Any]:
    role = str(record.get("role") or "")
    name = normalize_optional_skill_name(str(record.get("name") or ""))
    source_file = str(record.get("source_file") or "")
    metadata_file = str(record.get("metadata_file") or "")
    status_file = str(record.get("status_file") or "")
    skill_path = root / source_file if source_file else _skill_path(root, name, role=role)
    metadata_path = root / metadata_file if metadata_file else skill_path.parent / "agents" / "openai.yaml"
    status_path = root / status_file if status_file else skill_path.parent / OPTIONAL_SKILL_STATUS_FILE
    fields = _read_frontmatter_fields(skill_path)
    body = _safe_read(skill_path)
    validation = validate_optional_skill_payload(
        role,
        name,
        str(fields.get("description") or ""),
        body,
    )
    if "name" not in fields:
        validation["errors"].append("missing optional skill frontmatter: name")
    elif fields.get("name") != name:
        validation["errors"].append("optional skill frontmatter name must match directory name")
    if "description" not in fields:
        validation["errors"].append("missing optional skill frontmatter: description")
    status = str(record.get("status") or "active")
    if status not in OPTIONAL_SKILL_STATUSES:
        validation["errors"].append(f"unknown optional skill status: {status}")
    if str(record.get("scope") or "role") == "shared" and not role:
        validation["errors"].append("shared optional skill requires at least one explicit valid role")
    payload = {
        "id": name,
        "role": role,
        "name": name,
        "description": fields.get("description") or "",
        "status": status,
        "source": "optional",
        "layer": "workspace_optional",
        "trust_scope": "user_approved",
        "scope": str(record.get("scope") or "role"),
        "core": False,
        "implicit_invocation": _metadata_implicit_invocation(metadata_path),
        "installed": skill_path.exists(),
        "source_file": _relative_path(root, skill_path),
        "resolved_source_file": _relative_path(root, skill_path),
        "source_file_hash": _file_hash(skill_path),
        "metadata_file": _relative_path(root, metadata_path),
        "metadata_file_hash": _file_hash(metadata_path),
        "status_file": _relative_path(root, status_path),
        "status_file_hash": _file_hash(status_path),
        "validation_status": "blocked" if validation["errors"] else "valid",
        "validation_errors": validation["errors"],
        "risk_tags": validation["risk_tags"],
        "frontmatter": fields,
    }
    for key in ("created_by", "created_at", "updated_by", "updated_at", "roles"):
        if key in record:
            payload[key] = record[key]
    return payload


def _strategy_record_payload(root: Path, skill_path: Path) -> dict[str, Any]:
    name = skill_path.parent.name
    metadata_path = skill_path.parent / "agents" / "openai.yaml"
    body = _safe_read(skill_path)
    fields = _frontmatter_fields_from_text(body)
    validation_errors = _validate_strategy_skill_text(name, body)
    status = fields.get("status") or "unknown"
    active = status == "active" and not validation_errors
    return {
        "id": name,
        "name": name,
        "description": fields.get("description") or "",
        "label": name,
        "owner_roles": ["head-manager"],
        "risk_tags": ["strategy"],
        "user_visible": active,
        "source": "strategy",
        "layer": "workspace_strategy",
        "trust_scope": "user_approved",
        "scope": "strategy",
        "core": False,
        "implicit_invocation": _metadata_implicit_invocation(metadata_path),
        "status": status,
        "active": active,
        "installed": skill_path.exists(),
        "source_file": _relative_path(root, skill_path),
        "resolved_source_file": _relative_path(root, skill_path),
        "source_file_hash": _file_hash(skill_path),
        "metadata_file": _relative_path(root, metadata_path),
        "metadata_file_hash": _file_hash(metadata_path),
        "validation_status": "blocked" if validation_errors else "valid",
        "validation_errors": validation_errors,
        "frontmatter": fields,
    }


def _project_agent_toml(root: Path, role: str, skills: list[str], additional_instructions: str = "") -> None:
    path = _agent_config_path(root, role)
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8")
    marker = "[[skills.config]]"
    body = text[: text.find(marker)].rstrip() if marker in text else text.rstrip()
    body = _replace_developer_instructions(body, additional_instructions, _render_role_skill_source_block(root, role, skills))
    body = _replace_tradingcodex_enabled_tools(body, AGENT_SPECS[role].mcp_allowlist)
    rendered = body + "\n\n" + _render_role_skill_config_blocks(root, role, skills)
    _atomic_write_text(path, rendered.rstrip() + "\n")


def _project_agent_model_policy(root: Path, role: str) -> None:
    path = _agent_config_path(root, role)
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8")
    updated = _replace_agent_model_policy(text, resolve_agent_model_policy(role))
    if updated != text:
        _atomic_write_text(path, updated.rstrip() + "\n")


def _replace_agent_model_policy(text: str, policy: dict[str, Any]) -> str:
    model_line = f'model = {json.dumps(policy["resolved_model"])}'
    effort = str(policy["reasoning_effort"])
    if effort not in CODEX_REASONING_EFFORTS:
        raise ValueError(f"unsupported Codex reasoning effort: {effort}")
    effort_line = f'model_reasoning_effort = {json.dumps(effort)}'
    if re.search(r"(?m)^model\s*=.*$", text):
        text = re.sub(r"(?m)^model\s*=.*$", model_line, text, count=1)
    else:
        text = model_line + "\n" + text
    if re.search(r"(?m)^model_reasoning_effort\s*=.*$", text):
        return re.sub(r"(?m)^model_reasoning_effort\s*=.*$", effort_line, text, count=1)
    model_end = text.find("\n", text.find(model_line))
    return text[: model_end + 1] + effort_line + "\n" + text[model_end + 1 :]


def _project_head_manager_mcp_tools(root: Path) -> None:
    path = _agent_config_path(root, "head-manager")
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8")
    updated = _replace_tradingcodex_enabled_tools(text, AGENT_SPECS["head-manager"].mcp_allowlist)
    if updated != text:
        _atomic_write_text(path, updated.rstrip() + "\n")


def _replace_tradingcodex_enabled_tools(text: str, tools: tuple[str, ...]) -> str:
    rendered = "[\n" + "\n".join(f"  {json.dumps(tool)}," for tool in tools) + "\n]"
    pattern = re.compile(
        r"(?P<prefix>\[mcp_servers\.tradingcodex\].*?enabled_tools\s*=\s*)\[(?P<body>.*?)\]",
        re.S,
    )
    match = pattern.search(text)
    if not match:
        return text
    return text[: match.start("body") - 1] + rendered + text[match.end("body") + 1 :]


def _project_head_manager_prompt(root: Path, additional_instructions: str = "") -> None:
    path = root / ".codex" / "prompts" / "base_instructions" / "head-manager.md"
    if not path.exists():
        return
    text = _strip_core_extension_boundary(
        _strip_additional_instruction_block(path.read_text(encoding="utf-8"))
    ).rstrip()
    if additional_instructions.strip():
        text += "\n\n" + _render_additional_instruction_block(additional_instructions).rstrip()
    text += "\n\n" + _render_core_extension_boundary()
    _atomic_write_text(path, text.rstrip() + "\n")


def _replace_developer_instructions(
    text: str,
    additional_instructions: str,
    generated_skill_sources: str = "",
) -> str:
    pattern = re.compile(r'developer_instructions\s*=\s*"""(?P<body>.*?)"""', re.S)
    match = pattern.search(text)
    if not match:
        return text
    base = _strip_core_extension_boundary(
        _strip_role_skill_source_block(_strip_additional_instruction_block(match.group("body")))
    ).strip("\n")
    if generated_skill_sources.strip():
        base += "\n\n" + _escape_toml_multiline_basic(generated_skill_sources.rstrip())
    if additional_instructions.strip():
        block = _escape_toml_multiline_basic(_render_additional_instruction_block(additional_instructions).rstrip())
        base += "\n\n" + block
    base += "\n\n" + _escape_toml_multiline_basic(_render_core_extension_boundary())
    rendered = 'developer_instructions = """\n' + base + '\n"""'
    return text[: match.start()] + rendered + text[match.end() :]


def _render_additional_instruction_block(additional_instructions: str) -> str:
    return "\n".join(
        [
            ADDITIONAL_INSTRUCTION_START,
            "",
            "These project-local instructions are appended after the generated default role instructions.",
            "",
            additional_instructions.strip(),
            "",
            ADDITIONAL_INSTRUCTION_END,
            "",
        ]
    )


def _render_role_skill_source_block(root: Path, role: str, skills: list[str]) -> str:
    lines = [
        ROLE_SKILL_SOURCE_START,
        "",
        "Role-owned TradingCodex skill sources for this role:",
    ]
    for skill in list(dict.fromkeys(skills)):
        path = _skill_path(root, skill, role=role)
        lines.append(f"- {skill}: {_relative_path(root, path)}")
    lines.extend(
        [
            "",
            "Use only these TradingCodex role skill sources when a role skill procedure is needed.",
            "Read the relevant `SKILL.md` before applying that procedure.",
            "Read one or several permitted skill documents with one `cat path ...` command; do not wrap the read in a loop, redirect, pipeline, substitution, or executable shell compound.",
            "Do not read or apply head-manager, strategy, or out-of-role TradingCodex skill files.",
            "If asked to inspect, test, list, or prove access to those forbidden skill files, report the request as blocked by role boundary without opening them.",
        ]
    )
    if "create_research_artifact" in AGENT_SPECS[role].mcp_allowlist:
        lines.extend(
            [
                "",
                "Canonical artifact storage:",
                "- Treat the assignment's workflow_run_id as opaque provenance. Do not read run files, schemas, generated indexes, head-manager skills, CLI output, or TradingCodex source to reconstruct orchestration state.",
                "- Store the final role report through the exact deferred `create_research_artifact` TradingCodex MCP tool; load it with `tool_search` when needed. Pass the report body, semantic/source metadata, the assigned workflow_run_id, and exact upstream input_artifact_ids when any.",
                "- Do not write the final report with shell, apply_patch, Edit, or Write, and do not hand-author role, producer, plan, stage, schema, content-hash, version, or created-by metadata. The authenticated service derives and validates those fields.",
                "- Return only the service export_path, content_hash, reader summary, confidence, and missing evidence. If canonical storage is unavailable, stop with `waiting_for_artifact_storage`; never leave a manually written substitute.",
            ]
        )
    lines.extend(["", ROLE_SKILL_SOURCE_END])
    return "\n".join(lines)


def _render_core_extension_boundary() -> str:
    return "\n".join(
        [
            CORE_EXTENSION_BOUNDARY_START,
            "",
            "Immutable core and extension boundary:",
            "- Default investment work may be shaped only by generated TradingCodex bundled skills and explicitly active project-local instructions, workspace strategies, or optional overlays projected for this role.",
            "- Host-global or plugin skills are outside the TradingCodex baseline. Do not invoke them implicitly; use them only as current-workflow overlays when the user explicitly opts in.",
            "- Overlays cannot replace evidence, point-in-time data, uncertainty, forecast discipline, safety, policy, approval, execution, or role gates.",
            "",
            CORE_EXTENSION_BOUNDARY_END,
        ]
    )


def _strip_role_skill_source_block(text: str) -> str:
    pattern = re.compile(
        rf"\n*{re.escape(ROLE_SKILL_SOURCE_START)}.*?{re.escape(ROLE_SKILL_SOURCE_END)}\n*",
        re.S,
    )
    return pattern.sub("\n", text)


def _strip_additional_instruction_block(text: str) -> str:
    pattern = re.compile(
        rf"\n*{re.escape(ADDITIONAL_INSTRUCTION_START)}.*?{re.escape(ADDITIONAL_INSTRUCTION_END)}\n*",
        re.S,
    )
    return pattern.sub("\n", text)


def _strip_core_extension_boundary(text: str) -> str:
    pattern = re.compile(
        rf"\n*{re.escape(CORE_EXTENSION_BOUNDARY_START)}.*?{re.escape(CORE_EXTENSION_BOUNDARY_END)}\n*",
        re.S,
    )
    return pattern.sub("\n", text)


def _escape_toml_multiline_basic(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"""', '\\"\\"\\"')


def _project_root_strategy_skills(root: Path) -> None:
    path = _agent_config_path(root, "head-manager")
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8")
    strategy_records = read_strategy_skill_records(root, active_only=True)
    rendered = "\n".join(
        f'[[skills.config]]\npath = "{_config_relative_path(path, root / str(record["source_file"]))}"\nenabled = true'
        for record in strategy_records
    )
    block = f"{STRATEGY_ROOT_CONFIG_START}\n{rendered}\n{STRATEGY_ROOT_CONFIG_END}".replace("\n\n", "\n")
    if STRATEGY_ROOT_CONFIG_START in text and STRATEGY_ROOT_CONFIG_END in text:
        pattern = re.compile(
            rf"{re.escape(STRATEGY_ROOT_CONFIG_START)}.*?{re.escape(STRATEGY_ROOT_CONFIG_END)}",
            re.S,
        )
        updated = pattern.sub(block, text)
    else:
        permissions_index = text.find("\n[permissions.")
        if permissions_index >= 0:
            updated = text[:permissions_index].rstrip() + "\n\n" + block + "\n" + text[permissions_index:]
        else:
            updated = text.rstrip() + "\n\n" + block + "\n"
    if updated != text:
        _atomic_write_text(path, updated.rstrip() + "\n")


def _project_root_investment_brain_skills(root: Path) -> None:
    path = _agent_config_path(root, "head-manager")
    if not path.exists():
        return
    from tradingcodex_service.application.investment_brains import investment_brain_skill_index_records

    text = path.read_text(encoding="utf-8")
    records = [
        record
        for record in investment_brain_skill_index_records(root)
        if record.get("active") and record.get("validation_status") == "valid"
    ]
    rendered = "\n".join(
        f'[[skills.config]]\npath = "{_config_relative_path(path, root / str(record["source_file"]))}"\nenabled = true'
        for record in records
    )
    block = _render_marked_block(
        INVESTMENT_BRAIN_ROOT_CONFIG_START,
        rendered,
        INVESTMENT_BRAIN_ROOT_CONFIG_END,
    )
    updated = _replace_or_insert_marked_block(
        text,
        INVESTMENT_BRAIN_ROOT_CONFIG_START,
        INVESTMENT_BRAIN_ROOT_CONFIG_END,
        block,
        before="[mcp_servers.tradingcodex]",
    )
    if updated != text:
        _atomic_write_text(path, updated.rstrip() + "\n")


def _render_role_skill_config_blocks(root: Path, role: str, skills: list[str]) -> str:
    return _render_skill_config_blocks(root, list(dict.fromkeys(skills)), role=role, enabled=True)


def _render_skill_config_blocks(root: Path, skills: list[str], *, role: str | None = None, enabled: bool = True) -> str:
    blocks = []
    config_path = _agent_config_path(root, role or "head-manager")
    for skill in skills:
        skill_path = _skill_path(root, skill, role=role)
        blocks.append(
            f'[[skills.config]]\npath = "{_config_relative_path(config_path, skill_path)}"\nenabled = {str(enabled).lower()}'
        )
    return "\n\n".join(blocks) + ("\n" if blocks else "")


def _render_marked_block(start: str, body: str, end: str) -> str:
    body = body.strip()
    return f"{start}\n{body}\n{end}" if body else f"{start}\n{end}"


def _replace_or_insert_marked_block(text: str, start: str, end: str, block: str, *, before: str | None = None) -> str:
    if start in text and end in text:
        pattern = re.compile(rf"{re.escape(start)}.*?{re.escape(end)}", re.S)
        return pattern.sub(block, text)
    if before and before in text:
        index = text.find(before)
        return text[:index].rstrip() + "\n\n" + block + "\n\n" + text[index:]
    permissions_index = text.find("\n[permissions.")
    if permissions_index >= 0:
        return text[:permissions_index].rstrip() + "\n\n" + block + "\n" + text[permissions_index:]
    return text.rstrip() + "\n\n" + block + "\n"


def _rewrite_skill_proposal(root: Path, proposal: dict[str, Any], path: Path, status: str, applied_by: str, errors: list[str]) -> None:
    fields = {
        "id": proposal.get("id", path.stem),
        "type": proposal.get("type", "add"),
        "target": proposal.get("target", ""),
        "skill": proposal.get("skill", ""),
        "created_at": proposal.get("created_at", ""),
        "requires_validation": proposal.get("requires_validation", "true"),
        "status": status,
        "validation_status": "blocked" if errors else "valid",
        "applied_by": applied_by,
        "applied_at": now_iso(),
    }
    if errors:
        fields["validation_error"] = "; ".join(errors)
    _write_simple_yaml(path, fields)


def _installed_skill_index(root: Path, optional_records: list[dict[str, Any]] | None = None) -> dict[str, dict[str, Any]]:
    skills: dict[str, dict[str, Any]] = {}
    for skill_id, spec in SKILL_SPECS.items():
        skill_path = _skill_path(root, skill_id)
        meta_path = skill_path.parent / "agents" / "openai.yaml"
        skills[skill_id] = {
            "id": skill_id,
            "label": spec.label,
            "owner_roles": list(spec.owner_roles),
            "risk_tags": list(spec.risk_tags),
            "user_visible": spec.user_visible,
            "source": "core",
            "layer": "bundled_core",
            "trust_scope": "managed",
            "scope": spec.scope,
            "core": True,
            "implicit_invocation": _metadata_implicit_invocation(meta_path),
            "installed": skill_path.exists(),
            "source_file": _relative_path(root, skill_path),
            "resolved_source_file": _relative_path(root, skill_path),
            "source_file_hash": _file_hash(skill_path),
            "metadata_file": _relative_path(root, meta_path),
            "metadata_file_hash": _file_hash(meta_path),
        }
    for record in read_strategy_skill_records(root):
        skill_id = str(record.get("name") or "")
        if not skill_id or skill_id in skills:
            continue
        skills[skill_id] = record
    from tradingcodex_service.application.investment_brains import investment_brain_skill_index_records

    for record in investment_brain_skill_index_records(root):
        skill_id = str(record.get("id") or "")
        if not skill_id:
            continue
        if skill_id in skills:
            raise ValueError(f"reserved investment brain skill collides with another managed skill: {skill_id}")
        skills[skill_id] = record
    for record in optional_records or read_optional_skill_records(root, include_archived=True):
        skill_name = str(record.get("name") or "")
        if not skill_name or skill_name in skills:
            continue
        skills[skill_name] = {
            "id": skill_name,
            "name": skill_name,
            "label": skill_name,
            "description": str(record.get("description") or ""),
            "owner_roles": [record["role"]] if record.get("role") else [],
            "risk_tags": list(record.get("risk_tags") or []),
            "user_visible": False,
            "source": "optional",
            "layer": "workspace_optional",
            "trust_scope": "user_approved",
            "scope": record.get("scope", "role"),
            "core": False,
            "implicit_invocation": bool(record.get("implicit_invocation")),
            "status": record.get("status"),
            "installed": bool(record.get("installed")),
            "source_file": record.get("source_file", ""),
            "resolved_source_file": record.get("resolved_source_file", ""),
            "source_file_hash": record.get("source_file_hash"),
            "metadata_file": record.get("metadata_file", ""),
            "metadata_file_hash": record.get("metadata_file_hash"),
            "status_file": record.get("status_file", ""),
            "status_file_hash": record.get("status_file_hash"),
            "validation_status": record.get("validation_status"),
            "validation_errors": record.get("validation_errors", []),
        }
    return skills


def _agent_spec_payload(spec: AgentSpec) -> dict[str, Any]:
    return {
        "role": spec.role,
        "label": spec.label,
        "group": spec.group,
        "display_group": ROLE_DISPLAY_GROUPS.get(spec.role, spec.group),
        "purpose": ROLE_PURPOSES.get(spec.role, ""),
        "handoff_contract": ROLE_HANDOFF_CONTRACTS.get(spec.role, {}),
        "forbidden_actions": list(ROLE_FORBIDDEN_ACTIONS.get(spec.role, ())),
        "builtin_skills": list(spec.builtin_skills),
        "forbidden_skill_tags": list(spec.forbidden_skill_tags),
        "mcp_allowlist": list(spec.mcp_allowlist),
        "model_policy": resolve_agent_model_policy(spec.role),
    }


def _agent_config_path(root: Path, role: str) -> Path:
    if role == "head-manager":
        return root / ".codex" / "config.toml"
    return root / ".codex" / "agents" / f"{role}.toml"


def _assert_projection_write_targets(root: Path) -> None:
    targets = [
        *(_agent_config_path(root, role) for role in AGENT_SPECS),
        root / ".codex" / "prompts" / "base_instructions" / "head-manager.md",
        root / AGENT_INDEX_PATH,
        root / SKILL_INDEX_PATH,
        root / MANIFEST_PATH,
        root / MODEL_POLICY_MANIFEST_PATH,
    ]
    resolved_root = root.resolve()
    for target in targets:
        try:
            target.resolve(strict=False).relative_to(resolved_root)
        except ValueError as exc:
            raise ValueError(f"agent projection target escapes the workspace: {target}") from exc
        current = target
        while current != resolved_root:
            if current.is_symlink():
                raise ValueError(f"agent projection target cannot traverse a symlink: {current}")
            if current.parent == current:
                break
            current = current.parent


def _additional_instruction_path(root: Path, role: str) -> Path:
    return root / ADDITIONAL_INSTRUCTION_DIR / f"{role}.md"


def _skill_path(root: Path, skill: str, *, role: str | None = None) -> Path:
    spec = SKILL_SPECS.get(skill)
    if spec:
        if spec.scope == "subagent_shared":
            return root / SUBAGENT_SHARED_SKILL_DIR / skill / "SKILL.md"
        if spec.scope == "subagent_role":
            target_role = role or (spec.owner_roles[0] if spec.owner_roles else "")
            return root / SUBAGENT_SKILL_DIR / target_role / skill / "SKILL.md"
        return root / MAINAGENT_SKILL_DIR / skill / "SKILL.md"
    if skill.startswith(STRATEGY_SKILL_PREFIX):
        return root / STRATEGY_SKILL_DIR / skill / "SKILL.md"
    if skill.startswith(INVESTMENT_BRAIN_SKILL_PREFIX):
        return root / MAINAGENT_SKILL_DIR / skill / "SKILL.md"
    if role:
        role_path = root / SUBAGENT_SKILL_DIR / role / skill / "SKILL.md"
        if role_path.exists():
            return role_path
        shared_path = root / SUBAGENT_SHARED_SKILL_DIR / skill / "SKILL.md"
        if shared_path.exists():
            return shared_path
        return role_path
    for candidate in sorted((root / SUBAGENT_SKILL_DIR).glob(f"*/{skill}/SKILL.md")):
        return candidate
    return root / MAINAGENT_SKILL_DIR / skill / "SKILL.md"


def _parse_toml_skill_paths(text: str) -> list[str]:
    try:
        blocks = tomllib.loads(text).get("skills", {}).get("config", [])
    except tomllib.TOMLDecodeError:
        return []
    skills = []
    for block in blocks if isinstance(blocks, list) else []:
        if isinstance(block, dict) and block.get("enabled") is False:
            continue
        path = str(block.get("path") or "") if isinstance(block, dict) else ""
        if path.endswith("/SKILL.md"):
            skills.append(Path(path).parent.name)
    return list(dict.fromkeys(skills))


def _enabled_skill_config_paths(config_path: Path) -> list[str]:
    parsed = tomllib.loads(config_path.read_text(encoding="utf-8"))
    blocks = parsed.get("skills", {}).get("config", [])
    if isinstance(blocks, dict):
        blocks = [blocks]
    if not isinstance(blocks, list):
        return ["<invalid-skills-config>"]
    paths: list[str] = []
    for block in blocks:
        if not isinstance(block, dict):
            paths.append("<invalid-skill-config>")
            continue
        if block.get("enabled") is False:
            continue
        raw_path = str(block.get("path") or "").strip()
        if not raw_path:
            paths.append("<missing-skill-path>")
            continue
        path = Path(raw_path).expanduser()
        paths.append(str((path if path.is_absolute() else config_path.parent / path).resolve()))
    return paths


def _metadata_implicit_invocation(path: Path) -> bool:
    try:
        metadata = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"invalid skill metadata: {path}") from exc
    if not isinstance(metadata, dict):
        raise ValueError(f"skill metadata must be an object: {path}")
    if set(metadata) != {"interface", "policy"}:
        raise ValueError(f"skill metadata fields do not match the v1 schema: {path}")
    interface = metadata["interface"]
    if not isinstance(interface, dict) or set(interface) != {"display_name", "short_description", "default_prompt"}:
        raise ValueError(f"skill metadata interface does not match the v1 schema: {path}")
    for field in ("display_name", "short_description", "default_prompt"):
        value = interface[field]
        if not isinstance(value, str) or not value.strip() or value != value.strip():
            raise ValueError(f"skill metadata interface {field} must be a non-empty string: {path}")
    policy = metadata.get("policy")
    if not isinstance(policy, dict) or set(policy) != {"allow_implicit_invocation"}:
        raise ValueError(f"skill metadata policy does not match the v1 schema: {path}")
    allow_implicit_invocation = policy["allow_implicit_invocation"]
    if type(allow_implicit_invocation) is not bool:
        raise ValueError(f"skill metadata allow_implicit_invocation must be a boolean: {path}")
    return allow_implicit_invocation


def _proposal_summary(proposal: dict[str, Any] | None) -> dict[str, Any] | None:
    if proposal is None:
        return None
    return {
        "id": proposal.get("id"),
        "type": proposal.get("type"),
        "target": proposal.get("target"),
        "skill": proposal.get("skill"),
        "status": proposal.get("status"),
        "path": proposal.get("path"),
        "source_file_hash": proposal.get("source_file_hash"),
    }


def _relative_path(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


def _resolve_workspace_path(root: Path, value: str) -> str:
    path = Path(value).expanduser()
    return str((path if path.is_absolute() else root / path).resolve())


def _config_relative_path(config_path: Path, target: Path) -> str:
    return Path(os.path.relpath(target, config_path.parent)).as_posix()


def _portable_host_path(path: Path, home: Path, codex_home: Path) -> str:
    for base, label in ((codex_home.resolve(), "$CODEX_HOME"), (home.resolve(), "~")):
        try:
            return f"{label}/{path.relative_to(base).as_posix()}"
        except ValueError:
            continue
    return str(path)


def _optional_record_roles(record: dict[str, Any], scope_name: str) -> list[str]:
    if scope_name != "shared":
        return [scope_name]
    roles = record.get("roles")
    if isinstance(roles, list):
        return [str(role) for role in roles if str(role) in EXPECTED_SUBAGENTS]
    if isinstance(roles, str):
        parsed = [item.strip() for item in roles.split(",") if item.strip()]
        return [item for item in parsed if item in EXPECTED_SUBAGENTS]
    role = str(record.get("role") or "")
    if role in EXPECTED_SUBAGENTS:
        return [role]
    return []


def _unique_existing(root: Path, skills: list[str], *, role: str | None = None) -> list[str]:
    unique = list(dict.fromkeys(skills))
    if not (root / MAINAGENT_SKILL_DIR).exists() and not (root / SUBAGENT_SKILL_DIR).exists():
        return unique
    return [skill for skill in unique if _skill_path(root, skill, role=role).exists()]


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    _atomic_write_text(path, json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n")


def _read_markdown_body(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""
    except OSError as exc:
        raise ValueError(f"skill markdown is unavailable: {path}") from exc
    return _markdown_body_from_text(text)


def _markdown_body_from_text(text: str) -> str:
    return split_markdown_frontmatter(text).body


def _validate_strategy_skill_text(name: str, text: str) -> list[str]:
    fields = _frontmatter_fields_from_text(text)
    missing = sorted(STRATEGY_REQUIRED_FRONTMATTER - set(fields))
    validation_errors: list[str] = []
    if missing:
        validation_errors.append(f"missing strategy frontmatter: {', '.join(missing)}")
    if not name.startswith(STRATEGY_SKILL_PREFIX):
        validation_errors.append("strategy skill name must start with strategy-")
    if fields.get("name") and fields.get("name") != name:
        validation_errors.append("strategy skill frontmatter name must match directory name")
    if fields.get("type") and fields.get("type") != "strategy":
        validation_errors.append("strategy skill frontmatter type must be strategy")
    missing_sections = [section for section in STRATEGY_REQUIRED_SECTIONS if section not in text]
    if missing_sections:
        validation_errors.append(f"missing strategy sections: {', '.join(missing_sections)}")
    forbidden_terms = sorted({
        next(group for group in match.groups() if group)
        for match in STRATEGY_FORBIDDEN_COUPLING_PATTERN.finditer(text)
    })
    if forbidden_terms:
        validation_errors.append(f"strategy body must be standalone; remove platform coupling terms: {', '.join(forbidden_terms)}")
    return validation_errors


def _render_basic_skill_markdown(name: str, description: str, body: str) -> str:
    frontmatter = {
        "name": name,
        "description": description or name.replace("-", " ").title(),
    }
    return _render_frontmatter(frontmatter) + body.strip() + "\n"


def _render_strategy_skill_markdown(name: str, description: str, body: str, language: str, status: str) -> str:
    frontmatter = {
        "name": name,
        "description": description,
        "type": "strategy",
        "status": status,
        "language": language or "unknown",
        "owner": "user",
        "last_reviewed": now_iso()[:10],
    }
    return _render_frontmatter(frontmatter) + _ensure_strategy_sections(name, body).strip() + "\n"


def _render_frontmatter(fields: dict[str, Any]) -> str:
    return "---\n" + yaml.safe_dump(fields, sort_keys=False, allow_unicode=True).strip() + "\n---\n\n"


def _ensure_strategy_sections(name: str, body: str) -> str:
    text = body.strip() or _default_strategy_body(name)
    if not text.startswith("# "):
        text = f"# {_strategy_display_name(name, text)}\n\n{text}"
    for section in STRATEGY_REQUIRED_SECTIONS:
        if section not in text:
            text = text.rstrip() + f"\n\n{section}\nnot specified\n"
    return text


def _default_strategy_body(name: str) -> str:
    sections = "\n\n".join(f"{section}\nnot specified" for section in STRATEGY_REQUIRED_SECTIONS)
    return f"# {_strategy_display_name(name, '')}\n\n{sections}\n"


def _strategy_display_name(name: str, body: str) -> str:
    return _skill_display_name(name.removeprefix(STRATEGY_SKILL_PREFIX), body) if name.startswith(STRATEGY_SKILL_PREFIX) else _skill_display_name(name, body)


def _skill_display_name(name: str, body: str) -> str:
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            heading = stripped[2:].strip()
            if heading:
                return heading
    return name.replace("-", " ").title() or name


def _render_openai_yaml(display_name: str, short_description: str, default_prompt: str) -> str:
    short = re.sub(r"\s+", " ", short_description or display_name).strip()
    if len(short) < 25:
        short = (short + " for Codex workflows").strip()
    if len(short) > 64:
        short = short[:64].rstrip()
    return yaml.safe_dump(
        {
            "interface": {
                "display_name": display_name,
                "short_description": short,
                "default_prompt": default_prompt,
            },
            "policy": {"allow_implicit_invocation": False},
        },
        sort_keys=False,
        allow_unicode=True,
    )


def _write_simple_yaml(path: Path, fields: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_text(path, yaml.safe_dump({key: value for key, value in fields.items() if value is not None}, sort_keys=False, allow_unicode=True))


def _read_simple_yaml(path: Path) -> dict[str, Any]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"invalid YAML file: {path}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"YAML file must contain an object: {path}")
    return _plain_yaml_value(data)


def _read_frontmatter_fields(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except OSError as exc:
        raise ValueError(f"skill markdown is unavailable: {path}") from exc
    return _frontmatter_fields_from_text(text)


def _frontmatter_fields_from_text(text: str) -> dict[str, Any]:
    return split_markdown_frontmatter(text).frontmatter


def _plain_yaml_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _plain_yaml_value(child) for key, child in value.items()}
    if isinstance(value, list):
        return [_plain_yaml_value(child) for child in value]
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value

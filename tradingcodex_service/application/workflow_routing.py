from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any

from tradingcodex_service.application.agents import EXPECTED_SUBAGENTS, JUDGMENT_REVIEW_ROLE
from tradingcodex_service.application.common import _unique

INVESTMENT_WORKFLOW_TERMS = re.compile(
    r"\b("
    r"stock|stocks|equity|equities|share|shares|security|securities|ticker|tickers|"
    r"etf|index|indices|option|options|derivative|futures|crypto|bitcoin|btc|ethereum|eth|"
    r"bond|bonds|credit|cds|rates|fx|currency|commodity|commodities|oil|gold|"
    r"portfolio|position|holding|exposure|sizing|hedge|risk|drawdown|"
    r"valuation|fair value|target price|earnings|filing|catalyst|thesis|"
    r"technical|price action|trend|momentum|volume|volatility|"
    r"buy|sell|short|long|trade|trading|order ticket|paper order|broker|"
    r"approve|approval|execute|execution"
    r")\b"
)
INVESTMENT_ACTION_WITH_SYMBOL = re.compile(r"\b(analyze|analyse|review|research|evaluate|assess)\b")
INTUITION_WITH_SYMBOL = re.compile(
    r"\b("
    r"feel|feels|felt|look|looks|seem|seems|interesting|cheap|expensive|undervalued|overvalued|"
    r"bullish|bearish|like|love|hate|worried|concerned|watching|curious|vibe"
    r")\b",
    re.I,
)
SYMBOL_LIKE_TOKEN = re.compile(r"\b[A-Z][A-Z0-9.\-]{0,6}\b")
NON_INVESTMENT_CONTEXT_TERMS = re.compile(
    r"\b("
    r"repo|repository|code|docs|documentation|readme|file|files|python|django|"
    r"pytest|test|tests|template|templates|prompt|prompts|hook|hooks|config|"
    r"html|css|javascript|typescript|node|npm|uv|cli|ui|ux|frontend|backend|"
    r"migration|model|admin|api|mcp|cli|function|class|bug|fix|implement|"
    r"refactor|skill|skills"
    r")\b|agents\.md"
)
STRATEGY_AUTHORING_TERMS = re.compile(
    r"\b(create|write|draft|define|design|build|update|edit|revise|archive|delete|activate)\b"
    r"[^.?!]{0,100}\b(strategy|strategies|strategy skill|strategy library|entry criteria|exit criteria|sizing rule)\b|"
    r"\b(strategy|strategies|strategy skill|strategy library)\b"
    r"[^.?!]{0,100}\b(create|write|draft|define|design|build|update|edit|revise|archive|delete|activate)\b",
    re.I,
)
SECRET_WARNING_TERMS = re.compile(
    r"\b(api\s+key|apikey|broker\s+key|secret|token|credential|credentials|password)\b|\.env"
)
SECRET_ONLY_IGNORE_TERMS = re.compile(
    r"\b("
    r"api|key|broker|secret|token|credential|credentials|password|env|file|"
    r"save|store|write|read|rotate|keep|put|here|is|my|to|in|the|a|an|this"
    r")\b|\.env"
)
CONNECTOR_OPERATION_TERMS = re.compile(
    r"\b("
    r"connector|connectors|broker connector|broker profile|capability profile|credential_ref|"
    r"register_broker_connector|list_broker_adapter_providers|get_broker_capability_profile|"
    r"get_broker_instrument_constraints|sync_broker_account|broker sync|sandbox broker|test broker|"
    r"test/sandbox broker|broker setup|attach broker|configure broker|"
    r"binance|kis|korea investment|upbit|alpaca|ibkr|broker|brokers|provider|providers|broker api|exchange api"
    r")\b",
    re.I,
)
CONNECTOR_SUBJECT_TERMS = re.compile(r"\b(binance|kis|korea investment|upbit|alpaca|ibkr|broker|brokers|exchange|api|connector|provider|providers)\b", re.I)
CONNECTOR_BUILD_TERMS = re.compile(
    r"\b("
    r"attach|connect|integrate|configure|setup|scaffold|add|wire|implement|build"
    r")\b[^.?!]{0,120}\b("
    r"binance|kis|korea investment|upbit|alpaca|ibkr|broker|brokers|exchange|api|connector|provider|providers"
    r")\b|"
    r"\b(binance|kis|korea investment|upbit|alpaca|ibkr|broker|brokers|exchange|api|connector|provider|providers)\b[^.?!]{0,120}\b("
    r"attach|connect|integrate|configure|setup|scaffold|add|wire|implement|build"
    r")\b",
    re.I,
)
REMAINING_ORDER_APPROVAL_EXECUTION_TERMS = re.compile(
    r"\b(submit|already approved|approved paper|execute|execution|approve|approval|order ticket|paper order|buy order|sell order|trade|trading|place order|place-order)\b",
    re.I,
)
REMAINING_APPROVAL_EXECUTION_TERMS = re.compile(
    r"\b(submit|already approved|approved paper|execute|execution|approve|approval|place order|place-order|trade|trading)\b",
    re.I,
)


@dataclass(frozen=True)
class NormalizedInvestmentIntent:
    vague_analysis: bool = False
    broad_thesis_default: bool = False
    factual_profile_only: bool = False
    technical_only: bool = False
    technical_negated: bool = False
    valuation_requested: bool = False
    valuation_negated: bool = False
    news_negated: bool = False
    forecast_requested: bool = False
    forecast_negated: bool = False
    forecast_horizon: str = ""
    decision_support_requested: bool = False
    portfolio_risk_requested: bool = False
    portfolio_negated: bool = False
    risk_negated: bool = False
    order_negated: bool = False
    trading_negated: bool = False
    recommendation_negated: bool = False
    approval_execution_requested: bool = False
    backtest_or_signal_validation_requested: bool = False
    strategy_authoring_requested: bool = False
    connector_or_build_requested: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


INTENT_TERM_PATTERNS = {
    "factual_profile_only": re.compile(
        r"\b(company|issuer|business|facts?|profile|overview|description|what does .* do)\b[^.?!]{0,60}\b(only|facts?|profile|overview|description)\b|"
        r"\b(facts?|profile|overview|description)\s+only\b|"
        r"\bwhat does\s+[a-z0-9.\-]{1,10}\s+do\??\b|"
        r"\b[a-z0-9.\-]{1,10}\s+(facts?|profile|overview|description)\b",
        re.I,
    ),
    "technical_only": re.compile(
        r"\b(chart|charts|technical(?: analysis)?|price action|trend|momentum|setup)\s+only\b|"
        r"\b(just|only)\s+(?:the\s+)?(chart|charts|technical(?: analysis)?|price action|trend|momentum|setup)\b",
        re.I,
    ),
    "valuation_requested": re.compile(
        r"\b(valuation|fair value|target price|price target|cheap|expensive|undervalued|overvalued|intrinsic value|multiples|dcf)\b",
        re.I,
    ),
    "forecast_requested": re.compile(
        r"\b(forecast|predict|prediction|probability|odds|chance|scenario probability|expected return|by\s+\d{4}(?:-\d{2}-\d{2})?)\b",
        re.I,
    ),
    "decision_support_requested": re.compile(
        r"\b(should i|recommend|recommendation|buy|sell|add|trim|size|sizing|fit|risk/reward|decision support)\b",
        re.I,
    ),
    "portfolio_risk_requested": re.compile(
        r"\b(portfolio|position|holding|own|exposure|concentration|correlation|drawdown|hedge|sizing|size|risk review|risk budget|risk tolerance|loss capacity|downside)\b",
        re.I,
    ),
    "thesis_requested": re.compile(
        r"\b(earnings|filing|catalyst|preview|thesis|disclosure|narrative|expectation bar|variant perception)\b",
        re.I,
    ),
    "backtest_or_signal_validation_requested": re.compile(
        r"\b(backtest|back test|signal|model performance|sharpe|alpha|walk-forward|out-of-sample|overfit|overfitting|data snooping|look-ahead|survivorship)\b",
        re.I,
    ),
    "strategy_validation_hint": re.compile(r"\b(strategy|model|system)\b", re.I),
}
FORECAST_HORIZON_PATTERN = re.compile(
    r"\b(?:by|through|until|horizon|review date)\s+([0-9]{4}(?:-[0-9]{2}(?:-[0-9]{2})?)?|Q[1-4]\s+[0-9]{4}|[0-9]+\s+(?:days?|weeks?|months?|years?))\b",
    re.I,
)
HANDOFF_STATES = ("accepted", "revise", "blocked", "waiting")
ARTIFACT_EVALUATION_STATES = ("accept_artifact", "revise_artifact", "block_artifact", "wait_for_artifact")
TERMINAL_WORKFLOW_ACTIONS = ("synthesize", "blocked", "waiting", "lane_escalation_proposal")
PLANNER_ACTIONS = (
    "revise_same_role",
    "follow_up_existing_team",
    "challenge_conflict",
    "downstream_handoff",
    "lane_escalation_proposal",
    "blocked",
    "waiting",
    "synthesize",
)
FIXED_INVESTMENT_ROLES = tuple(EXPECTED_SUBAGENTS)
PRODUCER_INVESTMENT_ROLES = tuple(role for role in FIXED_INVESTMENT_ROLES if role != JUDGMENT_REVIEW_ROLE)
RESEARCH_AND_DECISION_ROLES = tuple(role for role in PRODUCER_INVESTMENT_ROLES if role != "execution-operator")
LOOP_STATE_PATH = ".tradingcodex/mainagent/workflow-loop-state.json"
LOOP_RUNS_DIR = ".tradingcodex/mainagent/workflows"
LOOP_RUN_STATE_FILENAME = "loop-state.json"


def selected_workflow_roles(plan: dict[str, Any]) -> list[str]:
    roles = [str(role) for role in (plan.get("selectedTeam") or plan.get("subagents") or [])]
    for stage in plan.get("stages") or []:
        if not isinstance(stage, dict):
            continue
        for item in stage.get("roles") or []:
            role = item.get("role") if isinstance(item, dict) else item
            roles.append(str(role))
    return _unique([role for role in roles if role in FIXED_INVESTMENT_ROLES])


def workflow_plan_has_role(plan: dict[str, Any], role: str) -> bool:
    return role in selected_workflow_roles(plan)


DEFAULT_LOOP_POLICY = {
    "max_iterations": 3,
    "max_followups_per_iteration": 2,
    "max_same_role_revisions": 1,
    "max_total_subagent_tasks": 8,
    "max_loop_subagent_tasks": 4,
    "require_user_consent_for_lane_escalation": True,
    "synthesize_on_budget_exhaustion": False,
    "terminal_workflow_actions": list(TERMINAL_WORKFLOW_ACTIONS),
    "artifact_handoff_states": list(HANDOFF_STATES),
    "artifact_evaluation_states": list(ARTIFACT_EVALUATION_STATES),
    "planner_actions": list(PLANNER_ACTIONS),
}
LANE_LOOP_POLICY_OVERRIDES: dict[str, dict[str, Any]] = {
    "research_only": {"max_iterations": 2, "max_loop_subagent_tasks": 3},
    "order_ticket_draft_gate": {"max_iterations": 2, "max_loop_subagent_tasks": 2},
    "order_ticket_approval_execution_gate": {"max_iterations": 1, "max_followups_per_iteration": 1, "max_loop_subagent_tasks": 1},
    "connector_build": {"max_iterations": 1, "max_followups_per_iteration": 0, "max_total_subagent_tasks": 0, "max_loop_subagent_tasks": 0},
    "head_manager_connector_operations": {"max_iterations": 1, "max_followups_per_iteration": 0, "max_total_subagent_tasks": 0, "max_loop_subagent_tasks": 0},
    "head_manager_strategy_authoring": {"max_iterations": 1, "max_followups_per_iteration": 0, "max_total_subagent_tasks": 0, "max_loop_subagent_tasks": 0},
}
WORKFLOW_LANE_COPY: dict[str, dict[str, str]] = {
    "research_only": {
        "label": "Research check",
        "summary": "Gather role-separated evidence before any valuation, portfolio, order, approval, or execution work.",
        "primary_question": "What is known, what is uncertain, and which evidence is missing?",
    },
    "thesis_review": {
        "label": "Thesis review",
        "summary": "Review business, technical, news, and valuation context without moving into portfolio action.",
        "primary_question": "Does the thesis have enough current evidence to support a view?",
    },
    "thesis_review_then_portfolio_risk_review": {
        "label": "Decision support",
        "summary": "Move from research and valuation into portfolio fit and risk review before any order work.",
        "primary_question": "Is the idea suitable for the portfolio after evidence, valuation, sizing, and risk checks?",
    },
    "portfolio_risk_review": {
        "label": "Portfolio fit",
        "summary": "Focus on exposure, concentration, liquidity, correlation, drawdown, and constraints.",
        "primary_question": "How would this affect the existing portfolio and risk budget?",
    },
    "order_ticket_draft_gate": {
        "label": "Draft order gate",
        "summary": "Create or review an order-ticket path while approval and execution remain blocked.",
        "primary_question": "Is the proposed order complete enough for policy and risk review?",
    },
    "order_ticket_approval_execution_gate": {
        "label": "Approved action gate",
        "summary": "Require the ticket, matching approval, policy allow state, duplicate-request check, connection, and audit trail.",
        "primary_question": "Can an already-approved action pass the service-gated approved action path?",
    },
    "head_manager_connector_operations": {
        "label": "Connector setup",
        "summary": "Use head-manager server operations for connector setup and read-only validation.",
        "primary_question": "Can the connector be inspected without secrets, order tickets, approvals, or execution?",
    },
    "connector_build": {
        "label": "Connector build",
        "summary": "Use TradingCodex build mode to scaffold or implement a broker/API provider without submitting live orders.",
        "primary_question": "Can the provider be added through scaffold, credential_ref, read sync, health checks, and service-gated validation?",
    },
    "head_manager_strategy_authoring": {
        "label": "Strategy authoring",
        "summary": "Use the strategy-creator workflow to turn user rules into a fixed, user-approved strategy guide.",
        "primary_question": "Can the strategy be written as rules without analyzing a ticker, granting approval, or changing policy?",
    },
}
SUITABILITY_PROFILE_FIELDS = (
    "investment objective",
    "time horizon",
    "risk tolerance and loss capacity",
    "liquidity or cash needs",
    "current holdings and concentration",
    "tax, account, or jurisdiction constraints",
)
PROFILE_FIELD_KEYS = {
    "investment objective": "investment_objective",
    "time horizon": "time_horizon",
    "risk tolerance and loss capacity": "risk_tolerance_and_loss_capacity",
    "liquidity or cash needs": "liquidity_needs",
    "current holdings and concentration": "current_holdings_and_concentrations",
    "tax, account, or jurisdiction constraints": "constraints",
}
PROFILE_COMPACT_KEYS = {
    "investment_objective": "obj",
    "time_horizon": "hor",
    "risk_tolerance_and_loss_capacity": "risk",
    "liquidity_needs": "liq",
    "current_holdings_and_concentrations": "hold",
    "constraints": "cons",
}
PROFILE_QUESTION_COPY: dict[str, dict[str, str]] = {
    "investment objective": {
        "question": "What outcome are you trying to achieve with this idea?",
        "why_required": "A recommendation or sizing view needs an objective before risk can be judged.",
    },
    "time horizon": {
        "question": "What time horizon should the workflow assume?",
        "why_required": "The same asset can fit differently for a short trade, medium-term thesis, or long-term allocation.",
    },
    "risk tolerance and loss capacity": {
        "question": "How much downside or temporary loss would be unacceptable?",
        "why_required": "Loss capacity frames suitability, position size, and whether the idea should stop at research.",
    },
    "liquidity or cash needs": {
        "question": "Do you need cash from this account soon, or are there liquidity constraints?",
        "why_required": "Near-term cash needs can block or reduce exposure even when the thesis is strong.",
    },
    "current holdings and concentration": {
        "question": "What are your current holdings, position sizes, and major concentrations?",
        "why_required": "Portfolio fit depends on existing exposure, diversification, and opportunity cost.",
    },
    "tax, account, or jurisdiction constraints": {
        "question": "Are there tax, account type, jurisdiction, mandate, or restricted-list constraints?",
        "why_required": "Constraints can change what actions are allowed before approval or execution.",
    },
}
PROFILE_REQUIRED_LANES = {
    "thesis_review_then_portfolio_risk_review",
    "portfolio_risk_review",
    "order_ticket_draft_gate",
}
INVESTMENT_UNIVERSE_LABELS = {
    "public_equity": "Public equities",
    "public_crypto": "Crypto assets",
    "etf_index": "ETFs and indexes",
    "macro_rates_fx_commodities": "Macro, rates, FX, and commodities",
    "options_derivatives": "Options and derivatives",
    "credit_signal": "Credit and fixed income",
    "broker_connector_operations": "Data source setup",
    "broker_connector_build": "Broker/API connector build",
    "strategy_authoring": "Strategy authoring",
}
ROLE_SELECTION_COPY: dict[str, str] = {
    "fundamental-analyst": "Reviews the business, financial drivers, filings, and issuer-specific thesis evidence.",
    "technical-analyst": "Checks price action, liquidity, trend, volatility, and market-structure context without creating an order.",
    "news-analyst": "Reviews current headlines, events, catalysts, and source dates that may change the thesis.",
    "macro-analyst": "Maps rates, inflation, FX, commodities, and cross-asset drivers into the investment question.",
    "instrument-analyst": "Checks instrument mechanics, methodology, liquidity, contract terms, and support boundaries.",
    "valuation-analyst": "Turns accepted evidence into scenarios, assumptions, valuation range, and sensitivity.",
    "portfolio-manager": "Tests portfolio fit, exposure, concentration, liquidity needs, and sizing support or blockage.",
    "risk-manager": "Reviews downside, policy constraints, restricted-list posture, and approval readiness.",
    "judgment-reviewer": "Independently challenges accepted artifacts for contrary evidence, source trust, update triggers, invalidation conditions, and downstream readiness.",
    "execution-operator": "Uses only approved service-boundary order artifacts after ticket, approval, policy, duplicate-request, connection, live gates, sync, and audit checks pass.",
}
NEXT_ALLOWED_ACTION_COPY: dict[str, list[dict[str, str]]] = {
    "research_only": [
        {
            "label": "Dispatch selected research roles",
            "detail": "Start with the selected evidence roles and wait for accepted artifacts before synthesis.",
        },
        {
            "label": "Return research-only synthesis",
            "detail": "Summarize facts, inferences, assumptions, missing evidence, and explicitly avoid valuation or action advice.",
        },
    ],
    "thesis_review": [
        {
            "label": "Dispatch thesis roles",
            "detail": "Collect business, event, technical, and valuation context only for the requested thesis scope.",
        },
        {
            "label": "Synthesize thesis state",
            "detail": "State what changed, what remains uncertain, and why order or portfolio action is still blocked.",
        },
    ],
    "thesis_review_then_portfolio_risk_review": [
        {
            "label": "Answer missing profile questions",
            "detail": "Gather objective, horizon, loss capacity, liquidity, holdings, and constraints before recommendation or sizing.",
        },
        {
            "label": "Dispatch research, valuation, portfolio, and risk roles",
            "detail": "Use accepted artifacts to decide whether the idea is ready for portfolio-risk discussion, not an order.",
        },
    ],
    "portfolio_risk_review": [
        {
            "label": "Provide portfolio context",
            "detail": "Share holdings, exposure, risk budget, time horizon, and constraints so fit can be reviewed.",
        },
        {
            "label": "Run portfolio and risk review",
            "detail": "Review concentration, drawdown, liquidity, and policy posture while order actions stay blocked.",
        },
    ],
    "order_ticket_draft_gate": [
        {
            "label": "Complete draft prerequisites",
            "detail": "Confirm portfolio fit, risk review, order scope, instrument support, and canonical order fields.",
        },
        {
            "label": "Draft only, then stop",
            "detail": "Prepare a structured candidate ticket without approval or execution.",
        },
    ],
    "order_ticket_approval_execution_gate": [
        {
            "label": "Verify approved artifacts",
            "detail": "Match the order ticket, approval receipt, policy allow state, duplicate-request key, connection, and audit path.",
        },
        {
            "label": "Submit only through the approved action path",
            "detail": "Submission is allowed only for approved artifacts through TradingCodex service checks.",
        },
    ],
    "head_manager_connector_operations": [
        {
            "label": "Inspect connector status",
            "detail": "Use TradingCodex server commands to review metadata, capability profile, health, and secret-free setup.",
        },
        {
            "label": "Keep connector work read-only",
            "detail": "Do not create order tickets, approvals, execution artifacts, raw broker calls, or secret output.",
        },
    ],
    "connector_build": [
        {
            "label": "Check build gate",
            "detail": "Require Codex full access plus explicit TradingCodex build mode before editing connector code or generated harness files.",
        },
        {
            "label": "Scaffold safely",
            "detail": "Create connector profiles, credential_ref schema, read/test validation, and docs without storing secrets or enabling live orders.",
        },
    ],
    "head_manager_strategy_authoring": [
        {
            "label": "Use strategy creator",
            "detail": "Draft or update a strategy through the validated strategy workflow so frontmatter, status, and projection stay aligned.",
        },
        {
            "label": "Keep it as rules",
            "detail": "Write fixed judgment criteria only; do not analyze a live ticker, approve orders, execute trades, or change policy.",
        },
    ],
}
METHOD_LENS_COPY: dict[str, list[dict[str, str]]] = {
    "research_only": [
        {
            "label": "Evidence discipline",
            "detail": "Separate facts, inferences, and assumptions with source/as-of posture before any downstream judgment.",
            "plain": "First separate what is verified from what is only a working guess.",
            "reference": "NIST AI RMF; SEC Plain English",
        },
        {
            "label": "No action shortcut",
            "detail": "Treat issuer or instrument evidence as research, not portfolio advice or an order path.",
            "plain": "A good-sounding idea is still only research until later gates pass.",
            "reference": "TradingCodex role-boundary contract",
        },
    ],
    "thesis_review": [
        {
            "label": "Scenario discipline",
            "detail": "Use thesis, catalyst, technical, and valuation artifacts as scenarios with assumptions and uncertainty.",
            "plain": "Compare a few possible stories instead of pretending there is one certain outcome.",
            "reference": "Valuation/scenario analysis practice",
        },
        {
            "label": "No portfolio leap",
            "detail": "Do not convert a thesis into sizing or recommendation without portfolio context.",
            "plain": "Even a strong thesis may be wrong for this user's portfolio.",
            "reference": "SEC Reg BI care-obligation posture",
        },
    ],
    "thesis_review_then_portfolio_risk_review": [
        {
            "label": "Suitability/profile gate",
            "detail": "Use objective, horizon, loss capacity, liquidity, holdings, and constraints before recommendation or sizing.",
            "plain": "The system needs to know what the user is trying to achieve before advice can be useful.",
            "reference": "FINRA Rule 2111; SEC Reg BI",
        },
        {
            "label": "Portfolio risk lens",
            "detail": "Check allocation, diversification, concentration, and rebalancing before single-security action.",
            "plain": "A single idea should be judged by how it changes the whole portfolio.",
            "reference": "SEC Investor.gov; Markowitz (1952)",
        },
        {
            "label": "Factor/exposure lens",
            "detail": "Separate issuer-specific thesis from market, size, value, maturity, default, and other factor exposures.",
            "plain": "Some risk comes from the company, and some comes from the market forces around it.",
            "reference": "Fama-French (1993)",
        },
    ],
    "portfolio_risk_review": [
        {
            "label": "Policy fit",
            "detail": "Frame the idea against objectives, constraints, liquidity, horizon, tax/account context, and unique limits.",
            "plain": "Check whether the idea fits the user's own rules and constraints.",
            "reference": "CFA Institute IPS framing; FINRA Rule 2111",
        },
        {
            "label": "Risk budget",
            "detail": "Review concentration, drawdown, covariance/diversification, and opportunity cost before order readiness.",
            "plain": "Decide how much risk this idea would consume before any order work starts.",
            "reference": "Markowitz (1952); SEC Investor.gov",
        },
    ],
    "order_ticket_draft_gate": [
        {
            "label": "Order readiness",
            "detail": "Draft only after portfolio fit, risk review, instrument support, canonical fields, and policy checks are visible.",
            "plain": "A draft order is allowed only after the missing decision checks are visible.",
            "reference": "TradingCodex order lifecycle",
        },
    ],
    "order_ticket_approval_execution_gate": [
        {
            "label": "Approved action boundary",
            "detail": "Require exact order, approval receipt, policy allow, duplicate-request status, connection posture, and audit evidence.",
            "plain": "Execution is a locked path, not something a natural-language prompt can skip into.",
            "reference": "TradingCodex approval/execution guardrail",
        },
    ],
    "head_manager_connector_operations": [
        {
            "label": "AI governance and secret boundary",
            "detail": "Inspect connector metadata, capability profile, and health without raw secrets or trade artifacts.",
            "plain": "Connector work can explain readiness without exposing secrets or creating trades.",
            "reference": "NIST AI RMF; IOSCO AI/ML governance",
        },
    ],
    "connector_build": [
        {
            "label": "Build lane boundary",
            "detail": "Treat provider implementation as product development, not trade authorization; live submission remains outside build mode.",
            "plain": "Adding a broker adapter is allowed in build mode, but placing live trades still requires the execution service gates.",
            "reference": "TradingCodex build/operate/execution plane split",
        },
    ],
    "head_manager_strategy_authoring": [
        {
            "label": "Fixed strategy contract",
            "detail": "Separate durable user-approved strategy rules from one-off market analysis, approvals, execution, and role authority.",
            "plain": "A strategy is a reusable guide, not permission to trade.",
            "reference": "TradingCodex strategy skill contract; SEC Plain English",
        },
        {
            "label": "Quality gate",
            "detail": "Require objective, universe, entry criteria, exit criteria, risk controls, evidence requirements, and blocked conditions before activation.",
            "plain": "The rulebook should say when it applies, when it stops, and what evidence it needs.",
            "reference": "TradingCodex strategy-creator validation",
        },
    ],
}
LOOP_CONTROL_COPY: dict[str, list[dict[str, str]]] = {
    "research_only": [
        {
            "label": "Discovery loop",
            "detail": "Repeat only evidence gathering, source checks, and missing-evidence cleanup until selected research artifacts are accepted or blocked.",
        },
        {
            "label": "Stop condition",
            "detail": "Stop at research synthesis; do not loop into valuation, portfolio advice, order drafting, approval, or execution.",
        },
    ],
    "thesis_review": [
        {
            "label": "Thesis loop",
            "detail": "Iterate across evidence, valuation assumptions, and source freshness until the thesis is accepted, revised, blocked, or waiting.",
        },
        {
            "label": "Stop condition",
            "detail": "Stop before recommendation or sizing unless portfolio context and risk review are explicitly requested.",
        },
    ],
    "thesis_review_then_portfolio_risk_review": [
        {
            "label": "Decision loop",
            "detail": "Iterate through evidence, valuation, portfolio fit, and risk review while carrying profile gaps and artifact handoffs forward.",
        },
        {
            "label": "Stop condition",
            "detail": "Stop at decision support; order tickets, approvals, and execution require separate explicit gates.",
        },
    ],
    "portfolio_risk_review": [
        {
            "label": "Fit loop",
            "detail": "Iterate on holdings, exposure, constraints, and risk-budget evidence until portfolio fit is accepted, revised, blocked, or waiting.",
        },
        {
            "label": "Stop condition",
            "detail": "Stop before draft order work unless the user asks for an order-readiness lane and prerequisites are visible.",
        },
    ],
    "order_ticket_draft_gate": [
        {
            "label": "Draft-readiness loop",
            "detail": "Iterate on missing canonical order fields, instrument support, portfolio fit, and risk checks until a draft is ready or blocked.",
        },
        {
            "label": "Stop condition",
            "detail": "Stop after draft output; approval and execution stay outside this loop.",
        },
    ],
    "order_ticket_approval_execution_gate": [
        {
            "label": "Execution-check loop",
            "detail": "Revalidate exact ticket, approval receipt, policy allow state, duplicate-request status, connection posture, and audit evidence.",
        },
        {
            "label": "Stop condition",
            "detail": "Stop if any approval, scope, connection, duplicate-request, or audit check fails; never self-approve.",
        },
    ],
    "head_manager_connector_operations": [
        {
            "label": "Connector-health loop",
            "detail": "Iterate only service status, MCP readiness, connector metadata, and capability profile checks.",
        },
        {
            "label": "Stop condition",
            "detail": "Stop before secrets, order tickets, approvals, execution, or raw broker API calls.",
        },
    ],
    "connector_build": [
        {
            "label": "Connector-build loop",
            "detail": "Iterate scaffold, adapter registration, read/test validation, docs, and doctor checks only after the build gate passes.",
        },
        {
            "label": "Stop condition",
            "detail": "Stop before raw secret handling or live order enablement; execution remains a separate plane.",
        },
    ],
    "head_manager_strategy_authoring": [
        {
            "label": "Strategy drafting loop",
            "detail": "Iterate only on rule clarity, required sections, validation errors, and user approval status.",
        },
        {
            "label": "Stop condition",
            "detail": "Stop after a valid strategy draft or update; do not turn the strategy into ticker analysis or an action path.",
        },
    ],
}
LOOP_VERIFICATION_CONTROL = {
    "label": "Verification budget",
        "detail": "After each pass, verify artifact quality, source freshness, source trust, profile gaps, and blocked actions; stop with revise, blocked, or waiting instead of widening the lane.",
}
JUDGMENT_CONTROL_COPY: dict[str, list[dict[str, str]]] = {
    "default": [
        {
            "label": "Fixed rule baseline",
            "detail": "Use generated role boundaries, service policy, active user-approved strategy context, and explicit user constraints as the operating baseline; agents do not rewrite these rules during a workflow.",
        },
        {
            "label": "Independent judgment review",
            "detail": "Before synthesis or downstream action gates, have judgment-reviewer test accepted artifacts against contrary evidence, stale or weak source posture, missing profile context, alternative scenarios, and policy or strategy conflicts.",
        },
    ],
    "research_only": [
        {
            "label": "Fixed rule baseline",
            "detail": "Stay inside the research-only lane, selected evidence roles, and blocked-action list; do not self-expand into valuation, advice, or order work.",
        },
        {
            "label": "Independent judgment review",
            "detail": "Before synthesis, have judgment-reviewer name missing evidence, stale sources, and facts that would weaken the research read instead of smoothing them into agreement.",
        },
    ],
    "head_manager_connector_operations": [
        {
            "label": "Fixed rule baseline",
            "detail": "Use the connector safety runbook, service status, MCP policy, and secret boundary as fixed constraints; do not self-grant broker or execution authority.",
        },
        {
            "label": "Challenge review",
            "detail": "Before status output, check version, DB, connector capability, blocked surfaces, and restart requirements for contradictory readiness signals.",
        },
    ],
    "connector_build": [
        {
            "label": "Fixed rule baseline",
            "detail": "Use Codex full access plus TradingCodex build mode only for implementation work; never convert it into live order authority.",
        },
        {
            "label": "Challenge review",
            "detail": "Before declaring success, check scaffold files, credential_ref-only posture, provider registration, service-gated validation, and doctor output.",
        },
    ],
    "head_manager_strategy_authoring": [
        {
            "label": "Fixed rule baseline",
            "detail": "Use strategy-creator requirements and existing service validation as fixed constraints; do not self-update policies, roles, approval, execution, or broker surfaces.",
        },
        {
            "label": "Challenge review",
            "detail": "Before saving or presenting a strategy, check vague criteria, missing risk controls, hidden recommendation language, and conflicts with existing active strategies.",
        },
    ],
    "order_ticket_approval_execution_gate": [
        {
            "label": "Fixed service baseline",
            "detail": "Use the existing ticket, approval receipt, policy state, duplicate-request status, broker connection posture, and audit trail as fixed execution preconditions.",
        },
        {
            "label": "Service gate verification",
            "detail": "Do not reopen investment judgment or dispatch judgment-reviewer; stop if any approved-action precondition is missing or mismatched.",
        },
    ],
}
IDEA_TRANSLATION_COPY: dict[str, dict[str, str]] = {
    "research_only": {
        "working_hypothesis": "Treat the idea as a research prompt: gather evidence first, then name what is known, uncertain, or missing.",
        "safety_boundary": "No valuation, recommendation, sizing, order ticket, approval, or execution is implied.",
    },
    "thesis_review": {
        "working_hypothesis": "Treat the idea as a thesis check: review business, technical, news, and valuation context without moving into portfolio action.",
        "safety_boundary": "Order, approval, and execution paths remain blocked unless the user later asks for a broader lane.",
    },
    "thesis_review_then_portfolio_risk_review": {
        "working_hypothesis": "Treat the idea as a candidate thesis that must pass evidence, valuation, portfolio fit, and risk review before any action discussion.",
        "safety_boundary": "Recommendation, sizing, approval, and execution require investor-profile answers and accepted role artifacts.",
    },
    "portfolio_risk_review": {
        "working_hypothesis": "Treat the idea as a portfolio-fit question: review exposure, concentration, liquidity, drawdown, and constraints.",
        "safety_boundary": "Order tickets, approvals, and execution stay blocked until a separate order lane is requested and supported.",
    },
    "order_ticket_draft_gate": {
        "working_hypothesis": "Treat the request as a draft-readiness check: verify prerequisites before preparing any structured ticket.",
        "safety_boundary": "Drafting does not approve or execute; approval and execution remain separate gates.",
    },
    "order_ticket_approval_execution_gate": {
        "working_hypothesis": "Treat the request as an execution-boundary check for already-approved service-gated artifacts.",
        "safety_boundary": "Natural-language instructions, direct broker APIs, and missing approval artifacts block submission.",
    },
    "head_manager_connector_operations": {
        "working_hypothesis": "Treat the request as connector setup or inspection, not investment analysis.",
        "safety_boundary": "Keep connector work secret-free and read-only unless a later workflow explicitly reaches a supported gate.",
    },
    "connector_build": {
        "working_hypothesis": "Treat the request as TradingCodex provider implementation work, not an investment or execution workflow.",
        "safety_boundary": "Build mode may create live-capable providers, but raw secrets, direct broker APIs, and live submission outside the service gates stay blocked.",
    },
    "head_manager_strategy_authoring": {
        "working_hypothesis": "Treat the request as strategy authoring: convert the user's fixed preferences into a validated reusable rule guide.",
        "safety_boundary": "Do not analyze current tickers, recommend trades, approve orders, execute, or change role and policy authority.",
    },
}
BLOCKED_ACTION_COPY: dict[str, dict[str, str]] = {
    "valuation unless requested": {
        "label": "Valuation",
        "reason": "The request is research-only, so valuation waits until the user asks for fair value, target price, or decision support.",
    },
    "valuation": {
        "label": "Valuation",
        "reason": "The user explicitly excluded valuation from this workflow.",
    },
    "technical analysis": {
        "label": "Technical analysis",
        "reason": "The user explicitly excluded technical analysis from this workflow.",
    },
    "news analysis": {
        "label": "News analysis",
        "reason": "The user explicitly excluded news or event review from this workflow.",
    },
    "portfolio review": {
        "label": "Portfolio review",
        "reason": "The user explicitly excluded portfolio review from this workflow.",
    },
    "risk review": {
        "label": "Risk review",
        "reason": "The user explicitly excluded risk review from this workflow.",
    },
    "recommendation": {
        "label": "Recommendation",
        "reason": "The user explicitly excluded recommendation or advice from this workflow.",
    },
    "order ticket": {
        "label": "Order ticket",
        "reason": "No structured draft order should be created until the workflow reaches portfolio/order readiness.",
    },
    "natural-language order": {
        "label": "Natural-language order",
        "reason": "Execution cannot rely on an unstructured instruction; it needs a validated order ticket.",
    },
    "approval": {
        "label": "Approval",
        "reason": "Approval remains blocked until risk review, policy checks, and order scope are complete.",
    },
    "execution": {
        "label": "Execution",
        "reason": "Execution remains blocked until approval, duplicate-request, connection, and audit gates pass.",
    },
    "execution without approved artifacts": {
        "label": "Execution without approved artifacts",
        "reason": "An approved order ticket and matching approval receipt are required before any service-boundary submission.",
    },
    "direct broker API": {
        "label": "Direct broker API",
        "reason": "Broker calls must go through TradingCodex service policy instead of ad hoc shell or agent code.",
    },
    "secret read": {
        "label": "Secret read",
        "reason": "Raw credentials stay outside prompts, workspace files, logs, and role context.",
    },
    "ticker analysis": {
        "label": "Ticker analysis",
        "reason": "Strategy authoring should define reusable rules without performing a current-market analysis unless separately requested.",
    },
}
RESEARCH_STAGE_ROLES = {
    "fundamental-analyst",
    "technical-analyst",
    "news-analyst",
    "macro-analyst",
    "instrument-analyst",
}
EDGE_GROUP_CONTRACTS: dict[str, str] = {
    "dispatch": "Assignment envelope only: original request, constraints, artifact language, lane, artifact path, out-of-scope actions, and return contract.",
    "research-handoff": "Accepted research artifacts move to valuation; weak or missing evidence returns to the owning research role.",
    "judgment-review-gate": "Accepted upstream artifacts move through independent judgment-reviewer challenge before synthesis, portfolio, risk, order, approval, or execution gates.",
    "portfolio-risk-gate": "Accepted judgment-reviewed research, valuation, or instrument context moves to portfolio and risk review; gaps block draft readiness.",
    "approval-gate": "Portfolio draft readiness moves to risk approval only after schema, policy, and role-separation checks.",
    "execution-gate": "Risk approval moves to execution only with approved order ticket, matching approval receipt, policy allow, duplicate-request check, connection, and audit.",
}


def normalize_investment_intent(request: str) -> NormalizedInvestmentIntent:
    raw = request.strip()
    text = strip_skill_invocation_tokens(raw.lower())
    action_text = strip_guardrail_verification_phrases(strip_negated_action_phrases(text))
    universe = classify_investment_universe(text)
    strategy_authoring = bool(STRATEGY_AUTHORING_TERMS.search(text))
    connector_or_build = is_connector_build_request(raw) or is_connector_operations_only_request(raw)
    factual_profile_only = _intent_match("factual_profile_only", text)
    technical_negated = negates_scope(text, r"technical(?: analysis)?|chart|price action")
    explicit_research_team = explicit_public_equity_research_team(action_text)
    explicit_narrow_research = bool(explicit_research_team) and not (
        _intent_match("valuation_requested", action_text)
        or _intent_match("decision_support_requested", action_text)
        or _intent_match("portfolio_risk_requested", action_text)
        or _intent_match("thesis_requested", action_text)
        or _intent_match("forecast_requested", action_text)
        or _intent_match("backtest_or_signal_validation_requested", action_text)
    )
    technical_only = not technical_negated and (_intent_match("technical_only", text) or (
        bool(re.search(r"\b(trend|technical|price action|chart|setup)\b", action_text))
        and not _intent_match("valuation_requested", action_text)
        and not _intent_match("decision_support_requested", action_text)
        and not _intent_match("portfolio_risk_requested", action_text)
        and not _intent_match("thesis_requested", action_text)
        and not any(role != "technical-analyst" for role in explicit_research_team)
    ))
    valuation_negated = negates_scope(text, r"valuation|fair value|target price|price target|multiples|dcf")
    forecast_negated = negates_scope(text, r"forecast|prediction|probability|odds|chance|expected return")
    portfolio_negated = negates_scope(text, r"portfolio review|portfolio")
    risk_negated = negates_scope(text, r"risk review|risk")
    order_negated = negates_scope(text, r"order|orders|order draft|order ticket|draft")
    trading_negated = negates_scope(text, r"trade|trades|trading|buy|sell|recommendation|recommend")
    recommendation_negated = negates_scope(text, r"recommendation|recommend")
    valuation_requested = _intent_match("valuation_requested", action_text) and not valuation_negated
    forecast_requested = _intent_match("forecast_requested", action_text) and not forecast_negated
    portfolio_risk = _intent_match("portfolio_risk_requested", action_text)
    decision_support = not recommendation_negated and (
        _intent_match("decision_support_requested", action_text)
        or (_intent_match("valuation_requested", action_text) and portfolio_risk)
    )
    backtest_or_signal = _intent_match("backtest_or_signal_validation_requested", action_text) and not strategy_authoring
    vague_analysis = _is_vague_analysis_request(raw, text, action_text)
    broad_thesis_default = (
        vague_analysis
        and universe == "public_equity"
        and not factual_profile_only
        and not technical_only
        and not explicit_narrow_research
        and not strategy_authoring
        and not connector_or_build
    )
    return NormalizedInvestmentIntent(
        vague_analysis=vague_analysis,
        broad_thesis_default=broad_thesis_default,
        factual_profile_only=factual_profile_only,
        technical_only=technical_only,
        technical_negated=technical_negated,
        valuation_requested=valuation_requested,
        valuation_negated=valuation_negated,
        news_negated=negates_scope(text, r"news(?: analysis)?|headline|event review"),
        forecast_requested=forecast_requested,
        forecast_negated=forecast_negated,
        forecast_horizon=_forecast_horizon(action_text),
        decision_support_requested=decision_support,
        portfolio_risk_requested=portfolio_risk,
        portfolio_negated=portfolio_negated,
        risk_negated=risk_negated,
        order_negated=order_negated,
        trading_negated=trading_negated,
        recommendation_negated=recommendation_negated,
        approval_execution_requested=bool(REMAINING_APPROVAL_EXECUTION_TERMS.search(action_text)) and not connector_or_build,
        backtest_or_signal_validation_requested=backtest_or_signal,
        strategy_authoring_requested=strategy_authoring,
        connector_or_build_requested=connector_or_build,
    )


def _intent_match(name: str, text: str) -> bool:
    return bool(INTENT_TERM_PATTERNS[name].search(text))


def _forecast_horizon(text: str) -> str:
    match = FORECAST_HORIZON_PATTERN.search(text)
    return match.group(1).strip() if match else ""


def _is_vague_analysis_request(raw: str, text: str, action_text: str) -> bool:
    if "$tcx-workflow" in text and SYMBOL_LIKE_TOKEN.search(raw):
        return True
    if INVESTMENT_ACTION_WITH_SYMBOL.search(action_text) and SYMBOL_LIKE_TOKEN.search(raw):
        return True
    if INVESTMENT_ACTION_WITH_SYMBOL.search(action_text) and re.search(r"\b(stock|stocks|equity|equities|share|shares|security|securities)\b", action_text):
        return True
    if INTUITION_WITH_SYMBOL.search(action_text) and SYMBOL_LIKE_TOKEN.search(raw):
        return True
    return False


def is_investment_workflow_request(request: str) -> bool:
    text = request.strip()
    if not text:
        return False
    if is_secret_only_request(text):
        return False
    lower = text.lower()
    if "$tcx-workflow" in lower:
        return True
    if STRATEGY_AUTHORING_TERMS.search(lower):
        return False
    if is_connector_build_request(text):
        return False
    if is_connector_operations_only_request(text):
        return False
    if _intent_match("factual_profile_only", lower) and SYMBOL_LIKE_TOKEN.search(text):
        if NON_INVESTMENT_CONTEXT_TERMS.search(lower):
            return False
        return True
    if INVESTMENT_WORKFLOW_TERMS.search(lower):
        return True
    if INVESTMENT_ACTION_WITH_SYMBOL.search(lower) and SYMBOL_LIKE_TOKEN.search(text):
        if NON_INVESTMENT_CONTEXT_TERMS.search(lower):
            return False
        return True
    if INTUITION_WITH_SYMBOL.search(lower) and SYMBOL_LIKE_TOKEN.search(text):
        if NON_INVESTMENT_CONTEXT_TERMS.search(lower):
            return False
        return True
    return False


def is_secret_warning_request(request: str) -> bool:
    return bool(SECRET_WARNING_TERMS.search(request.lower()))


def is_secret_only_request(request: str) -> bool:
    if not is_secret_warning_request(request):
        return False
    reduced = SECRET_ONLY_IGNORE_TERMS.sub(" ", request.lower())
    if INVESTMENT_WORKFLOW_TERMS.search(reduced):
        return False
    return not (INVESTMENT_ACTION_WITH_SYMBOL.search(reduced) and SYMBOL_LIKE_TOKEN.search(request))


def is_connector_operations_only_request(request: str) -> bool:
    lower = request.lower()
    if not CONNECTOR_OPERATION_TERMS.search(lower):
        return False
    action_text = strip_guardrail_verification_phrases(strip_negated_action_phrases(lower))
    return not REMAINING_ORDER_APPROVAL_EXECUTION_TERMS.search(action_text)


def is_connector_build_request(request: str) -> bool:
    if is_secret_only_request(request):
        return False
    lower = request.lower()
    action_text = strip_guardrail_verification_phrases(strip_negated_action_phrases(lower))
    if not CONNECTOR_BUILD_TERMS.search(lower) and not CONNECTOR_SUBJECT_TERMS.search(lower):
        return False
    return not REMAINING_APPROVAL_EXECUTION_TERMS.search(action_text)


def _with_judgment_reviewer(roles: list[str]) -> list[str]:
    selected = _unique(roles)
    if selected and JUDGMENT_REVIEW_ROLE not in selected:
        selected.append(JUDGMENT_REVIEW_ROLE)
    return selected


def classify_starter_request(request: str) -> dict[str, Any]:
    text = strip_skill_invocation_tokens(request.lower())
    intent = normalize_investment_intent(request)

    def finish(payload: dict[str, Any]) -> dict[str, Any]:
        flags = intent.as_dict()
        lane = str(payload.get("lane") or "")
        flags["deep_thesis_default"] = bool(flags.get("broad_thesis_default") and lane == "thesis_review")
        flags["decision_quality_required"] = JUDGMENT_REVIEW_ROLE in payload.get("subagents", ()) and lane not in {
            "head_manager_connector_operations",
            "connector_build",
            "head_manager_strategy_authoring",
        }
        flags["forecast_contract_required"] = bool(
            flags.get("forecast_requested")
            or flags.get("valuation_requested")
            or flags.get("decision_support_requested")
            or lane == "thesis_review_then_portfolio_risk_review"
        ) and not flags.get("forecast_negated")
        flags["profile_gate_required"] = lane in PROFILE_REQUIRED_LANES
        flags["anti_overfit_required"] = bool(flags.get("backtest_or_signal_validation_requested"))
        payload = {**payload, "blockedActions": _with_explicit_negation_blocks(payload.get("blockedActions") or [], flags)}
        loop_contract = build_artifact_supervisor_loop_contract(payload, flags)
        return {**payload, "intent": flags, "routingFlags": flags, **loop_contract}

    if intent.strategy_authoring_requested:
        return finish({
            "universe": "strategy_authoring",
            "lane": "head_manager_strategy_authoring",
            "subagents": [],
            "blockedActions": ["ticker analysis", "order ticket", "approval", "execution", "direct broker API", "secret read"],
        })
    universe = classify_investment_universe(text)
    action_text = strip_guardrail_verification_phrases(strip_negated_action_phrases(text))
    valuation_blocked = intent.valuation_negated
    technical_blocked = intent.technical_negated
    news_blocked = intent.news_negated
    connector_only = is_connector_operations_only_request(request)
    wants_approval_execution = intent.approval_execution_requested and not connector_only
    wants_order_draft = bool(re.search(r"draft|order ticket|buy order|sell order|paper buy order|paper sell order", action_text))
    wants_decision = intent.decision_support_requested
    wants_thesis_review = _intent_match("thesis_requested", action_text) or intent.valuation_requested or intent.forecast_requested
    wants_portfolio_risk = intent.portfolio_risk_requested
    wants_macro = bool(re.search(r"\b(macro|rates?|fx|currency|commodit(?:y|ies)|inflation|fed|boj|ecb|central bank|yield|oil|gold)\b", action_text))
    wants_instrument = bool(
        re.search(
            r"\b(etf|index|indices|option|options|derivative|futures|borrow|crypto|bitcoin|btc|ethereum|eth|cds|bond|credit|convertible|preferred|instrument)\b|"
            r"\b(short interest|market structure)\b",
            action_text,
        )
    )
    wants_technical = bool(re.search(r"trend|technical|price|volatility|liquidity|drawdown|down|setup|chart", action_text))
    wants_news = bool(re.search(r"news|event|earnings|filing|headline|catalyst|disclosure", action_text))
    research = base_research_team(universe, wants_technical, wants_news)
    if technical_blocked:
        research = [role for role in research if role != "technical-analyst"]
    if news_blocked:
        research = [role for role in research if role != "news-analyst"]
    if wants_macro:
        research.append("macro-analyst")
    if wants_instrument:
        research.append("instrument-analyst")
    thesis_roles = research + ([] if valuation_blocked else ["valuation-analyst"])
    if is_connector_build_request(request):
        return finish({"universe": "broker_connector_build", "lane": "connector_build", "subagents": [], "blockedActions": ["live submit", "order ticket", "approval", "execution", "direct broker API", "secret read"]})
    if connector_only:
        return finish({"universe": "broker_connector_operations", "lane": "head_manager_connector_operations", "subagents": [], "blockedActions": ["order ticket", "approval", "execution", "direct broker API", "secret read"]})
    if intent.factual_profile_only:
        profile_team = ["fundamental-analyst"] if universe == "public_equity" else base_research_team(universe, False, False)
        return finish({"universe": universe, "lane": "research_only", "subagents": _unique(profile_team), "blockedActions": ["valuation unless requested", "order ticket", "approval", "execution", "direct broker API", "secret read"]})
    if intent.technical_only:
        technical_team = ["technical-analyst"]
        if universe != "public_equity":
            technical_team.append("instrument-analyst")
        return finish({"universe": universe, "lane": "research_only", "subagents": _unique(technical_team), "blockedActions": ["valuation unless requested", "order ticket", "approval", "execution", "direct broker API", "secret read"]})
    if intent.backtest_or_signal_validation_requested:
        validation_team = ["technical-analyst"]
        if _intent_match("valuation_requested", action_text) or re.search(r"\b(expected return|model)\b", action_text):
            validation_team.append("valuation-analyst")
        if wants_portfolio_risk or wants_decision:
            validation_team.extend(["portfolio-manager", "risk-manager"])
        return finish({"universe": universe, "lane": "research_only", "subagents": _with_judgment_reviewer(validation_team), "blockedActions": ["order ticket", "approval", "execution", "direct broker API", "secret read"]})
    explicit_research_team = explicit_public_equity_research_team(action_text) if universe == "public_equity" else []
    if explicit_research_team and not (wants_approval_execution or wants_order_draft or wants_decision or wants_portfolio_risk or wants_thesis_review):
        return finish({"universe": universe, "lane": "research_only", "subagents": _with_judgment_reviewer(explicit_research_team), "blockedActions": ["valuation unless requested", "order ticket", "approval", "execution", "direct broker API", "secret read"]})
    if wants_approval_execution:
        return finish({"universe": universe, "lane": "order_ticket_approval_execution_gate", "subagents": _without_negated_roles((["macro-analyst"] if wants_macro else []) + (["instrument-analyst"] if wants_instrument else []) + ["portfolio-manager", "risk-manager", "execution-operator"], intent), "blockedActions": ["natural-language order", "direct broker API", "secret read", "execution without approved artifacts"]})
    if wants_order_draft:
        if intent.portfolio_negated:
            blocked_draft_roles = thesis_roles if intent.valuation_requested or intent.forecast_requested else research
            return finish({"universe": universe, "lane": "thesis_review", "subagents": _with_judgment_reviewer(blocked_draft_roles), "blockedActions": ["order ticket", "approval", "execution", "direct broker API", "secret read"]})
        return finish({"universe": universe, "lane": "order_ticket_draft_gate", "subagents": _with_judgment_reviewer(_without_negated_roles(research + ["portfolio-manager", "risk-manager"], intent)), "blockedActions": ["approval", "execution", "direct broker API", "secret read"]})
    if wants_decision:
        if intent.portfolio_negated or intent.risk_negated:
            return finish({"universe": universe, "lane": "thesis_review", "subagents": _with_judgment_reviewer(thesis_roles), "blockedActions": ["recommendation", "order ticket", "approval", "execution", "direct broker API", "secret read"]})
        return finish({"universe": universe, "lane": "thesis_review_then_portfolio_risk_review", "subagents": _with_judgment_reviewer(thesis_roles + ["portfolio-manager", "risk-manager"]), "blockedActions": ["order ticket", "approval", "execution", "direct broker API", "secret read"]})
    if wants_portfolio_risk:
        selected = _without_negated_roles((["macro-analyst"] if wants_macro else []) + (["instrument-analyst"] if wants_instrument else []) + (["technical-analyst"] if wants_technical else []) + (["news-analyst"] if wants_news else []) + ["portfolio-manager", "risk-manager"], intent)
        if "portfolio-manager" not in selected and "risk-manager" not in selected:
            return finish({"universe": universe, "lane": "thesis_review", "subagents": _with_judgment_reviewer(thesis_roles), "blockedActions": ["portfolio review", "risk review", "order ticket", "approval", "execution", "direct broker API", "secret read"]})
        return finish({"universe": universe, "lane": "portfolio_risk_review", "subagents": _with_judgment_reviewer(selected), "blockedActions": ["order ticket", "approval", "execution", "direct broker API", "secret read"]})
    if wants_thesis_review and universe == "public_equity":
        return finish({"universe": universe, "lane": "thesis_review", "subagents": _with_judgment_reviewer(thesis_roles), "blockedActions": ["order ticket", "approval", "execution", "direct broker API", "secret read"]})
    if intent.broad_thesis_default:
        return finish({"universe": universe, "lane": "thesis_review", "subagents": _with_judgment_reviewer(thesis_roles), "blockedActions": ["order ticket", "approval", "execution", "direct broker API", "secret read"]})
    return finish({"universe": universe, "lane": "research_only", "subagents": _with_judgment_reviewer(research), "blockedActions": ["valuation unless requested", "order ticket", "approval", "execution", "direct broker API", "secret read"]})


def _with_explicit_negation_blocks(actions: list[str], flags: dict[str, Any]) -> list[str]:
    blocked = list(actions)
    if flags.get("valuation_negated"):
        blocked.append("valuation")
    if flags.get("technical_negated"):
        blocked.append("technical analysis")
    if flags.get("news_negated"):
        blocked.append("news analysis")
    if flags.get("portfolio_negated"):
        blocked.append("portfolio review")
    if flags.get("risk_negated"):
        blocked.append("risk review")
    if flags.get("recommendation_negated"):
        blocked.append("recommendation")
    return _unique(blocked)


def _without_negated_roles(roles: list[str], intent: NormalizedInvestmentIntent) -> list[str]:
    blocked = set()
    if intent.portfolio_negated:
        blocked.add("portfolio-manager")
    if intent.risk_negated:
        blocked.add("risk-manager")
    if intent.technical_negated:
        blocked.add("technical-analyst")
    if intent.news_negated:
        blocked.add("news-analyst")
    if intent.valuation_negated:
        blocked.add("valuation-analyst")
    return [role for role in roles if role not in blocked]


def build_artifact_supervisor_loop_contract(plan: dict[str, Any], flags: dict[str, Any] | None = None) -> dict[str, Any]:
    lane = str(plan.get("lane") or "research_only")
    selected = selected_workflow_roles(plan)
    flags = flags or {}
    allowed = selected if selected else []
    escalation = _loop_escalation_team(lane, selected)
    if flags.get("valuation_negated"):
        escalation = [role for role in escalation if role != "valuation-analyst"]
    if flags.get("news_negated"):
        escalation = [role for role in escalation if role != "news-analyst"]
    if flags.get("technical_only"):
        escalation = [role for role in escalation if role in {"technical-analyst", "instrument-analyst"}]
    if lane == "research_only":
        allowed = [role for role in allowed if role not in {"valuation-analyst", "portfolio-manager", "risk-manager", "execution-operator"}]
        if flags.get("valuation_negated"):
            escalation = [role for role in escalation if role != "valuation-analyst"]
    if lane in {"connector_build", "head_manager_connector_operations", "head_manager_strategy_authoring"}:
        allowed = []
        escalation = []
    policy = build_loop_policy(lane)
    return {
        "selectedTeam": selected,
        "allowedFollowupTeam": _unique(allowed),
        "escalationTeam": _unique([role for role in escalation if role not in selected and role not in allowed]),
        "loopPolicy": policy,
        "loopStatePath": LOOP_STATE_PATH,
        "loopRunStatePattern": f"{LOOP_RUNS_DIR}/<workflow_run_id>/{LOOP_RUN_STATE_FILENAME}",
        "exitCriteria": build_loop_exit_criteria(lane, selected),
        "terminalWorkflowActions": list(TERMINAL_WORKFLOW_ACTIONS),
        "artifactHandoffStates": list(HANDOFF_STATES),
        "plannerActions": list(PLANNER_ACTIONS),
    }


def build_loop_policy(lane: str) -> dict[str, Any]:
    policy = {**DEFAULT_LOOP_POLICY, **LANE_LOOP_POLICY_OVERRIDES.get(lane, {})}
    return {
        **policy,
        "terminal_workflow_actions": list(policy["terminal_workflow_actions"]),
        "artifact_handoff_states": list(policy["artifact_handoff_states"]),
        "artifact_evaluation_states": list(policy["artifact_evaluation_states"]),
        "planner_actions": list(policy["planner_actions"]),
    }


def plan_follow_up_request(plan: dict[str, Any], follow_up_request: dict[str, Any]) -> dict[str, Any]:
    role = str(follow_up_request.get("suggested_role") or "")
    trigger = str(follow_up_request.get("trigger") or "")
    allowed = set(plan.get("allowedFollowupTeam") or [])
    escalation = set(plan.get("escalationTeam") or [])
    policy_reason = ""
    if role in allowed:
        planner_action = "challenge_conflict" if trigger == "contradiction" else "follow_up_existing_team"
        policy_within_current_lane = True
        policy_requires_user_consent = False
        policy_reason = f"{role} is in allowed_followup_team for {plan.get('lane')}"
    elif role in escalation or trigger == "scope_boundary":
        planner_action = "lane_escalation_proposal"
        policy_within_current_lane = False
        policy_requires_user_consent = True
        policy_reason = f"{role or 'requested role'} is outside allowed_followup_team for {plan.get('lane')}"
    else:
        planner_action = "blocked"
        policy_within_current_lane = False
        policy_requires_user_consent = True
        policy_reason = f"{role or 'requested role'} is not allowed for this routed lane"
    if role == "execution-operator":
        planner_action = "blocked"
        policy_within_current_lane = False
        policy_requires_user_consent = True
        policy_reason = "follow_up_requests cannot request execution work"
    return {
        "planner_decision": planner_action,
        "suggested_role": role,
        "trigger": trigger,
        "policy_within_current_lane": policy_within_current_lane,
        "policy_requires_user_consent": policy_requires_user_consent,
        "policy_reason": policy_reason,
        "delta_brief": build_delta_follow_up_brief(plan, follow_up_request, planner_action),
    }


def build_delta_follow_up_brief(plan: dict[str, Any], follow_up_request: dict[str, Any], planner_action: str) -> str:
    parts = [
        f"Planner action: {planner_action}",
        f"Workflow lane: {plan.get('lane')}",
        f"Source artifact: {follow_up_request.get('source_artifact_path') or follow_up_request.get('source_artifact_id') or 'unknown'}",
        f"Trigger: {follow_up_request.get('trigger') or 'unknown'}",
        f"Question: {follow_up_request.get('question') or ''}",
        f"Reason: {follow_up_request.get('reason') or ''}",
        "Use only artifact path, context_summary, source/as-of metadata, and this delta question; do not paste full prior artifacts.",
    ]
    return "\n".join(parts)

def build_loop_exit_criteria(lane: str, selected_roles: list[str] | None = None) -> list[str]:
    includes_valuation = selected_roles is None or "valuation-analyst" in selected_roles
    includes_judgment_review = selected_roles is None or JUDGMENT_REVIEW_ROLE in selected_roles
    if lane == "research_only":
        upstream = "selected research artifacts and independent judgment review" if includes_judgment_review else "selected research artifacts"
        return [f"{upstream} accepted or blocked", "no automatic valuation, portfolio, risk, approval, or execution expansion"]
    if lane == "thesis_review":
        upstream = "accepted research, valuation, and independent judgment review artifacts" if includes_valuation else "accepted research and independent judgment review artifacts"
        return [f"{upstream} or explicit revise/blocked/waiting state", "judgment-reviewer preserves material conflicts"]
    if lane == "thesis_review_then_portfolio_risk_review":
        upstream = "accepted research, valuation, judgment review, portfolio, and risk artifacts" if includes_valuation else "accepted research, judgment review, portfolio, and risk artifacts"
        return [f"{upstream} or explicit revise/blocked/waiting state", "profile gaps and blocked actions remain visible"]
    if lane == "portfolio_risk_review":
        return ["accepted judgment review and portfolio/risk artifacts or explicit revise/blocked/waiting state", "no order drafting, approval, or execution"]
    if lane == "order_ticket_draft_gate":
        return ["judgment review and draft-readiness artifact accepted or blocked", "approval and execution remain blocked"]
    if lane == "order_ticket_approval_execution_gate":
        return ["service-layer approval/execution preconditions verified or blocked", "no natural-language approval or self-approval"]
    return ["lane-specific work is complete or blocked", "no execution-sensitive authority is widened"]


def _loop_escalation_team(lane: str, selected: list[str]) -> list[str]:
    if lane == "research_only":
        return list(RESEARCH_AND_DECISION_ROLES)
    if lane == "thesis_review":
        return ["macro-analyst", "instrument-analyst", "portfolio-manager", "risk-manager"]
    if lane == "thesis_review_then_portfolio_risk_review":
        return ["macro-analyst", "instrument-analyst"]
    if lane == "portfolio_risk_review":
        return ["fundamental-analyst", "technical-analyst", "news-analyst", "macro-analyst", "instrument-analyst", "valuation-analyst"]
    if lane == "order_ticket_draft_gate":
        return ["execution-operator"]
    return []


def classify_investment_universe(text: str) -> str:
    text = strip_skill_invocation_tokens(text)
    if STRATEGY_AUTHORING_TERMS.search(text):
        return "strategy_authoring"
    if re.search(r"\b(btc|bitcoin|eth|ethereum|crypto|token|stablecoin|on-chain|defi)\b", text):
        return "public_crypto"
    if re.search(r"\b(option|options|derivative|futures|swap|volatility surface)\b", text):
        return "options_derivatives"
    if re.search(r"\b(etf|index|indices|benchmark|constituent)\b", text):
        return "etf_index"
    if re.search(r"\b(cds|bond|credit|spread|covenant|restructuring|distressed|loan)\b", text):
        return "credit_signal"
    if re.search(r"\b(macro|rates|fx|currency|commodity|commodities|inflation|fed|boj|ecb|central bank|yield|oil|gold)\b", text):
        return "macro_rates_fx_commodities"
    return "public_equity"


def strip_skill_invocation_tokens(text: str) -> str:
    return re.sub(r"\$[a-z0-9][a-z0-9_-]*", " ", text, flags=re.I)


def base_research_team(universe: str, wants_technical: bool, wants_news: bool) -> list[str]:
    if universe == "public_crypto":
        return ["technical-analyst", "news-analyst", "instrument-analyst"]
    if universe == "macro_rates_fx_commodities":
        team = ["macro-analyst"]
        if wants_technical:
            team.append("technical-analyst")
        if wants_news:
            team.append("news-analyst")
        return team
    if universe in {"options_derivatives", "credit_signal"}:
        team = ["instrument-analyst"]
        if wants_technical:
            team.append("technical-analyst")
        if wants_news:
            team.append("news-analyst")
        return team
    if universe == "etf_index":
        return ["instrument-analyst", "technical-analyst", "news-analyst"]
    return ["fundamental-analyst", "technical-analyst", "news-analyst"]


def explicit_public_equity_research_team(text: str) -> list[str]:
    team: list[str] = []
    if re.search(r"\b(fundamental|fundamentals|business|financials?|company|issuer)\b", text):
        team.append("fundamental-analyst")
    if re.search(r"\b(chart|charts|technical(?: analysis)?|price action|price|trend|momentum|setup|volume|volatility|liquidity)\b", text):
        team.append("technical-analyst")
    if re.search(r"\b(news|headline|headlines|event|events|filing|filings|disclosure|catalyst|catalysts)\b", text):
        team.append("news-analyst")
    return _unique(team)

def strip_negated_action_phrases(text: str) -> str:
    text = re.sub(r"\b(?:do not|don't|dont|does not|doesn't)\s+want\s+(?:to\s+)?(?:a\s+|any\s+)?(trade|trading|trades|order|orders|execution|execute|approval|approve|buy|sell)\b", " ", text)
    text = re.sub(r"\b(?:not|no longer)\s+(?:wanting|seeking|requesting)\s+(?:to\s+)?(?:a\s+|any\s+)?(trade|trading|trades|order|orders|execution|execute|approval|approve|buy|sell)\b", " ", text)
    text = re.sub(r"\b(no|do not|don't|dont|without)\s+(?:a\s+|any\s+)?(account access|order draft|trade execution|trading|trade|trades|orders|order|draft|execution|execute|approval|approve|buy|sell|recommendation|recommend|decision support|valuation|fair value|target price|price target|multiples|dcf|technical analysis|technical|chart|price action|news analysis|news|headline|event review|portfolio review|portfolio|risk review|risk)\b", " ", text)
    text = re.sub(r"\b(no|do not|don't|dont|without)\s+(live trading|live execution|broker access|account|broker|trade execution)\b", " ", text)
    text = re.sub(r"\bnot\s+(?:asking\s+for\s+|requesting\s+|seeking\s+)?(?:a\s+|any\s+)?(order|trade|execution|approval|valuation|fair value|target price|price target|recommendation|portfolio review|risk review|technical analysis|news analysis)\b", " ", text)
    return text


def strip_guardrail_verification_phrases(text: str) -> str:
    text = re.sub(r"\b(?:user-prompt-submit|pre-tool-use|post-tool-use|session-start|subagent-start|subagent-stop|permission-request)\b", " ", text)
    text = re.sub(r"\beven with\b[^.]{0,180}\b(?:blocked|denied|rejected|unavailable)[-\s]+action\s+wording\b[^.]*", " ", text)
    text = re.sub(r"\b(?:blocked|denied|rejected|unavailable)[-\s]+action\s+wording\s+like\b[^.]*", " ", text)
    text = re.sub(r"\bwhether\s+(?:order|approval|execution|direct|broker|secret|access|/|\s|was|were|is|are|blocked|denied|rejected)+", " ", text)
    text = re.sub(r"\b(?:blocked|denied|rejected|unavailable)\s+(?:order|approval|execution|direct|broker|secret|access|/|\s|actions|paths)+", " ", text)
    text = re.sub(r"\bverify\s+(?:routing\s+and\s+)?(?:blocked|denied|rejected|unavailable)\s+(?:actions|paths|access)?", " ", text)
    text = re.sub(r"\bverify\b.{0,120}\b(?:blocked|denied|rejected|unavailable|no trading|no order|no execution)\b", " ", text)
    text = re.sub(r"\bconfirm\b.{0,120}\b(?:blocked|denied|rejected|unavailable|no trading|no order|no execution)\b", " ", text)
    return text


def negates_scope(text: str, scope_pattern: str) -> bool:
    return bool(re.search(rf"\b(?:no|do not|don't|dont|without)\s+(?:a\s+|any\s+)?(?:{scope_pattern})\b", text)) or bool(
        re.search(rf"\bnot\s+(?:asking\s+for\s+|requesting\s+|seeking\s+)?(?:a\s+|any\s+)?(?:{scope_pattern})\b", text)
    )

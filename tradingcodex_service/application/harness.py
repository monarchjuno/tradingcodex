from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from tradingcodex_service.application.common import _status_class, _unique
from tradingcodex_service.application.agents import (
    AGENT_SPECS,
    EXPECTED_SKILLS,
    EXPECTED_SUBAGENTS,
    ROLE_DISPLAY_GROUPS,
    ROLE_FORBIDDEN_ACTIONS,
    ROLE_HANDOFF_CONTRACTS,
    ROLE_PURPOSES,
    ROLE_PERMISSION_PROFILES,
    ROLE_SKILL_MAP,
    USER_VISIBLE_SKILLS,
    read_strategy_skill_records,
)
from tradingcodex_service.application.components import count_harness_component_tags, list_harness_components
from tradingcodex_service.application.policy import EXPLICIT_DENY_ACTIONS
from tradingcodex_service.application.research import list_workspace_research_artifacts
from tradingcodex_service.application.runtime import active_profile_for_workspace, ensure_runtime_database, tradingcodex_db_path, workspace_context_payload
from tradingcodex_service.mcp_runtime import static_mcp_tools as _static_mcp_tools

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
    r"register_broker_connector|list_broker_connector_templates|get_broker_capability_profile|"
    r"get_broker_instrument_constraints|sync_broker_account|broker sync|sandbox broker|test broker|"
    r"test/sandbox broker|broker setup|attach broker|configure broker|"
    r"binance|kis|korea investment|upbit|alpaca|ibkr|broker api|exchange api"
    r")\b|한투|한국투자|바이낸스|업비트|브로커|증권사|거래소",
    re.I,
)
CONNECTOR_BUILD_TERMS = re.compile(
    r"\b("
    r"attach|connect|integrate|configure|setup|scaffold|add|wire|implement|build"
    r")\b[^.?!]{0,120}\b("
    r"binance|kis|korea investment|upbit|alpaca|ibkr|broker|exchange|api|connector"
    r")\b|"
    r"\b(binance|kis|korea investment|upbit|alpaca|ibkr|broker|exchange|api|connector)\b[^.?!]{0,120}\b("
    r"attach|connect|integrate|configure|setup|scaffold|add|wire|implement|build"
    r")\b|"
    r"(바이낸스|binance|kis|한투|한국투자|업비트|upbit|브로커|증권사|거래소|api)[^.?!]{0,80}(붙여|연결|연동|추가|구현|설정)",
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
HANDOFF_STATES = ("accepted", "revise", "blocked", "waiting")
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
        "primary_question": "Can an already-approved non-live action pass the approved action path?",
    },
    "head_manager_connector_operations": {
        "label": "Connector setup",
        "summary": "Use head-manager server operations for connector setup and read-only validation.",
        "primary_question": "Can the connector be inspected without secrets, order tickets, approvals, or execution?",
    },
    "connector_build": {
        "label": "Connector build",
        "summary": "Use TradingCodex build mode to scaffold or implement a broker/API connector without enabling live execution.",
        "primary_question": "Can the connector be added through scaffold, credential_ref, read sync, and sandbox/test validation only?",
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
    "order_ticket_approval_execution_gate",
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
    "execution-operator": "Uses only approved non-live order artifacts after ticket, approval, policy, duplicate-request, connection, and audit checks pass.",
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
            "detail": "Submission is allowed only for approved non-live artifacts through TradingCodex service checks.",
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
            "detail": "Treat connector implementation as product development, not trade authorization; live execution remains outside build mode.",
            "plain": "Adding a broker adapter is allowed in build mode, but placing live trades is still locked.",
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
    "detail": "After each pass, verify artifact quality, source freshness, profile gaps, and blocked actions; stop with revise, blocked, or waiting instead of widening the lane.",
}
JUDGMENT_CONTROL_COPY: dict[str, list[dict[str, str]]] = {
    "default": [
        {
            "label": "Fixed rule baseline",
            "detail": "Use generated role boundaries, service policy, active user-approved strategy context, and explicit user constraints as the operating baseline; agents do not rewrite these rules during a workflow.",
        },
        {
            "label": "Challenge review",
            "detail": "Before synthesis, test the favorable case against contrary evidence, stale data, missing profile context, alternative scenarios, and policy or strategy conflicts.",
        },
    ],
    "research_only": [
        {
            "label": "Fixed rule baseline",
            "detail": "Stay inside the research-only lane, selected evidence roles, and blocked-action list; do not self-expand into valuation, advice, or order work.",
        },
        {
            "label": "Challenge review",
            "detail": "Before synthesis, name missing evidence, stale sources, and facts that would weaken the research read instead of smoothing them into agreement.",
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
            "detail": "Use Codex full access plus TradingCodex build mode only for implementation work; never convert it into live execution authority.",
        },
        {
            "label": "Challenge review",
            "detail": "Before declaring success, check scaffold files, credential_ref-only posture, read/test validation, live-order disabled state, and doctor output.",
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
        "working_hypothesis": "Treat the request as an execution-boundary check for already-approved non-live artifacts.",
        "safety_boundary": "Natural-language instructions, direct broker APIs, and missing approval artifacts block submission.",
    },
    "head_manager_connector_operations": {
        "working_hypothesis": "Treat the request as connector setup or inspection, not investment analysis.",
        "safety_boundary": "Keep connector work secret-free and read-only unless a later workflow explicitly reaches a supported gate.",
    },
    "connector_build": {
        "working_hypothesis": "Treat the request as TradingCodex connector implementation work, not an investment or execution workflow.",
        "safety_boundary": "Build mode may scaffold and validate read/test paths, but live_order, raw secrets, direct broker APIs, and execution stay blocked.",
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
        "reason": "An approved order ticket and matching approval receipt are required before any non-live submission.",
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
    "dispatch": "Assignment envelope only: original request, constraints, research artifact language, lane, artifact path, out-of-scope actions, and return contract.",
    "research-handoff": "Accepted research artifacts move to valuation; weak or missing evidence returns to the owning research role.",
    "portfolio-risk-gate": "Accepted research/valuation/instrument context moves to portfolio and risk review; gaps block draft readiness.",
    "approval-gate": "Portfolio draft readiness moves to risk approval only after schema, policy, and role-separation checks.",
    "execution-gate": "Risk approval moves to execution only with approved order ticket, matching approval receipt, policy allow, duplicate-request check, connection, and audit.",
}


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
    if not CONNECTOR_BUILD_TERMS.search(lower):
        return False
    action_text = strip_guardrail_verification_phrases(strip_negated_action_phrases(lower))
    return not REMAINING_APPROVAL_EXECUTION_TERMS.search(action_text)


def classify_starter_request(request: str) -> dict[str, Any]:
    text = strip_skill_invocation_tokens(request.lower())
    if STRATEGY_AUTHORING_TERMS.search(text):
        return {
            "universe": "strategy_authoring",
            "lane": "head_manager_strategy_authoring",
            "subagents": [],
            "blockedActions": ["ticker analysis", "order ticket", "approval", "execution", "direct broker API", "secret read"],
        }
    universe = classify_investment_universe(text)
    action_text = strip_guardrail_verification_phrases(strip_negated_action_phrases(text))
    valuation_blocked = negates_scope(text, r"valuation|fair value|target price|price target|multiples|dcf")
    technical_blocked = negates_scope(text, r"technical(?: analysis)?|chart|price action")
    news_blocked = negates_scope(text, r"news(?: analysis)?|headline|event review")
    connector_only = is_connector_operations_only_request(request)
    wants_approval_execution = False if connector_only else bool(REMAINING_APPROVAL_EXECUTION_TERMS.search(action_text))
    wants_order_draft = bool(re.search(r"draft|order ticket|buy order|sell order|paper buy order|paper sell order", action_text))
    wants_decision = bool(re.search(r"should i buy|should i sell|recommend|fair value|target price|buy|sell", action_text))
    wants_thesis_review = bool(re.search(r"earnings|filing|catalyst|preview|thesis|valuation|disclosure|narrative", action_text))
    wants_portfolio_risk = bool(
        re.search(
            r"portfolio|position|holding|own|exposure|concentration|correlation|drawdown|hedge|sizing|size|"
            r"risk review|risk budget|risk tolerance|loss capacity|downside",
            action_text,
        )
    )
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
        return {"universe": "broker_connector_build", "lane": "connector_build", "subagents": [], "blockedActions": ["live_order", "order ticket", "approval", "execution", "direct broker API", "secret read"]}
    if connector_only:
        return {"universe": "broker_connector_operations", "lane": "head_manager_connector_operations", "subagents": [], "blockedActions": ["order ticket", "approval", "execution", "direct broker API", "secret read"]}
    if wants_approval_execution:
        return {"universe": universe, "lane": "order_ticket_approval_execution_gate", "subagents": _unique((["macro-analyst"] if wants_macro else []) + (["instrument-analyst"] if wants_instrument else []) + ["portfolio-manager", "risk-manager", "execution-operator"]), "blockedActions": ["natural-language order", "direct broker API", "secret read", "execution without approved artifacts"]}
    if wants_order_draft:
        return {"universe": universe, "lane": "order_ticket_draft_gate", "subagents": _unique(research + ["portfolio-manager", "risk-manager"]), "blockedActions": ["approval", "execution", "direct broker API", "secret read"]}
    if wants_decision:
        return {"universe": universe, "lane": "thesis_review_then_portfolio_risk_review", "subagents": _unique(thesis_roles + ["portfolio-manager", "risk-manager"]), "blockedActions": ["order ticket", "approval", "execution", "direct broker API", "secret read"]}
    if wants_portfolio_risk:
        return {"universe": universe, "lane": "portfolio_risk_review", "subagents": _unique((["macro-analyst"] if wants_macro else []) + (["instrument-analyst"] if wants_instrument else []) + (["technical-analyst"] if wants_technical else []) + (["news-analyst"] if wants_news else []) + ["portfolio-manager", "risk-manager"]), "blockedActions": ["order ticket", "approval", "execution", "direct broker API", "secret read"]}
    if wants_thesis_review and universe == "public_equity":
        return {"universe": universe, "lane": "thesis_review", "subagents": _unique(thesis_roles), "blockedActions": ["order ticket", "approval", "execution", "direct broker API", "secret read"]}
    return {"universe": universe, "lane": "research_only", "subagents": _unique(research), "blockedActions": ["valuation unless requested", "order ticket", "approval", "execution", "direct broker API", "secret read"]}


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


def build_subagent_starter_prompt(request: str, workspace_root: Path | str | None = None) -> str:
    plan = classify_starter_request(request)
    artifact_language = infer_research_artifact_language(request)
    profile_status = investor_profile_status(plan, workspace_root)
    profile_inputs = profile_status["missing_fields"]
    known_profile = profile_status["known_fields"]
    stage_order = " -> ".join(stage["label"] for stage in build_workflow_stages(plan))
    spawn_line = ", ".join(plan["subagents"]) if plan["subagents"] else "none"
    if not plan["subagents"]:
        ops = no_subagent_lane_copy(plan)
        return "\n".join([
            ops["workflow_intro"],
            "No fixed-role subagent dispatch is required for this lane.",
            f'Original user request (verbatim): "{request}"',
            f"Workflow lane: {plan['lane']}",
            f"Operational universe: {investment_universe_label(plan['universe'])}",
            f"Workflow stage order: {stage_order}",
            ops["skill_instruction"],
            ops["secret_instruction"],
            ops["broker_instruction"],
            ops["artifact_instruction"],
            ops["output_instruction"],
            "Method lenses for this lane: " + format_method_lenses(plan),
            "Iteration controls for this lane: " + format_loop_controls(plan),
            "Judgment controls for this lane: " + format_judgment_controls(plan),
            f"Blocked actions: {', '.join(plan['blockedActions'])}",
        ])
    lines = [
        "Use this workspace's fixed-role subagent workflow.",
        "Explicitly use Codex subagents.",
        f'Original user request (verbatim): "{request}"',
        f"Research artifact language: {artifact_language}",
        f"Investment universe: {investment_universe_label(plan['universe'])}",
        f"Workflow lane: {plan['lane']}",
        f"Workflow stage order: {stage_order}",
        f"Spawn these fixed role subagents in parallel: {spawn_line}",
        "This selected team is binding for the current lane; do not spawn roles outside this exact list unless the user later asks for a broader lane.",
        "For `research_only`, do not add valuation, portfolio, risk, approval, or execution roles.",
        "When calling `spawn_agent` for a fixed role, use `agent_type` and a compact `message`; do not set `fork_context` to true.",
        "Use each role's exact `.codex/agents/*.toml` name as the runtime label.",
        "Preserve the original user request and explicit constraints in every subagent brief.",
        "Context budget: use artifact paths, context_summary, source/as-of metadata, and short deltas; do not paste full prior artifacts, source dumps, or unrelated chat history.",
        "Reader mode: open with a plain-English answer, then provide professional evidence, assumptions, and caveats.",
        "Artifact memory: write artifacts in the research artifact language with context_summary, reader_summary, next_action, source snapshots, missing-evidence notes, and improvement proposals for reuse.",
        "Iteration controls: stay within the selected lane; verify handoff quality after each artifact; lane controls: " + format_loop_controls(plan),
        "Judgment controls: fixed rules and selected strategy context are read-only; do not change strategy, policy, role authority, approval, execution, or MCP gates; lane controls: " + format_judgment_controls_compact(plan),
        "Challenge review: before final synthesis, name contrary evidence, alternatives, stale or missing data, profile gaps, and policy/strategy conflicts; use revise, blocked, or waiting if material.",
        "Method lenses for this lane: " + format_method_lenses(plan) + "; guardrails: separate facts, inferences, and assumptions; check portfolio fit before action advice.",
        "Strategy baseline: " + build_strategy_baseline(workspace_root)["prompt_summary"],
        "Do not let head-manager perform substantive investment analysis before subagent outputs exist.",
        "Require each role handoff to include artifact path, reader summary, next action, handoff state, source/as-of posture, confidence, missing evidence, readiness/support gaps, next eligible recipient, and blocked actions.",
        "Use handoff states: accepted, revise, blocked, waiting.",
        "Do not let downstream roles redo missing upstream work; request revision from the owning role or stop with waiting/blocked status.",
        "Wait for all selected subagents, then synthesize their outputs with artifact paths, handoff states, disagreements, missing evidence, and next allowed action.",
        f"Blocked actions before artifacts: {', '.join(plan['blockedActions'])}",
    ]
    if known_profile:
        known = "; ".join(f"{item['field']}: {item['answer']}" for item in known_profile)
        lines.insert(
            -1,
            "Known investor profile context from the active profile: " + known + ".",
        )
    if profile_inputs:
        question_items = build_profile_questions(plan, workspace_root)
        question_examples = "; ".join(item["question"] for item in question_items[:3])
        lines.insert(
            -1,
            "Investor profile gaps to request before recommendation, sizing, approval, or execution: "
            + ", ".join(profile_inputs)
            + ".",
        )
        lines.insert(
            -1,
            "Investor profile questions to ask if unanswered include: "
            + question_examples
            + ". Use the listed missing fields for any remaining profile questions.",
        )
    return "\n".join(lines)


def build_compact_dispatch_context(request: str, workspace_root: Path | str | None = None) -> dict[str, Any]:
    plan = classify_starter_request(request)
    profile_status = investor_profile_status(plan, workspace_root)
    has_subagents = bool(plan["subagents"])
    context = {
        "context_mode": "compact_workflow_gate",
        "workflow_lane": plan["lane"],
        "required_subagents": plan["subagents"],
        "routing_status": {
            "lane": plan["lane"],
            "selected_team": plan["subagents"],
            "blocked_actions": plan["blockedActions"],
        },
        "research_artifact_language": infer_research_artifact_language(request),
        "profile_missing": [
            PROFILE_COMPACT_KEYS.get(PROFILE_FIELD_KEYS.get(field, field), field)
            for field in profile_status["missing_fields"]
        ],
        "selected_team_binding": has_subagents,
        "starter_prompt_path": ".tradingcodex/mainagent/latest-user-prompt-gate.json",
        "dispatch_rules": (
            [
                "dispatch_or_reuse_selected_subagents_before_substantive_analysis",
                "treat_selected_team_as_closed_for_current_lane",
                "use_agent_type_with_compact_message_and_no_full_history_fork",
                "return_waiting_for_subagent_dispatch_if_exact_role_routing_is_unavailable",
                "do_not_repair_missing_upstream_work_inside_downstream_roles",
            ]
            if has_subagents
            else [
                "handle_in_head_manager_lane",
                "do_not_dispatch_fixed_role_subagents",
                "do_not_create_blocked_artifacts",
            ]
        ),
    }
    if profile_status["known_fields"]:
        context["profile_known"] = [PROFILE_COMPACT_KEYS.get(item["key"], item["key"]) for item in profile_status["known_fields"]]
    return context


def build_workflow_intake_summary(request: str, workspace_root: Path | str | None = None) -> dict[str, Any]:
    if not request.strip():
        return {}
    plan = classify_starter_request(request)
    lane_copy = WORKFLOW_LANE_COPY.get(plan["lane"], WORKFLOW_LANE_COPY["research_only"])
    profile_status = investor_profile_status(plan, workspace_root)
    return {
        "label": lane_copy["label"],
        "summary": lane_copy["summary"],
        "primary_question": lane_copy["primary_question"],
        "idea_translation": build_idea_translation(plan),
        "investment_universe": plan["universe"],
        "investment_universe_label": investment_universe_label(plan["universe"]),
        "workflow_lane": plan["lane"],
        "subagents": build_selected_role_details(plan),
        "workflow_stages": build_workflow_stages(plan),
        "method_lenses": build_method_lenses(plan),
        "loop_controls": build_loop_controls(plan),
        "judgment_controls": build_judgment_controls(plan),
        "review_highlights": build_review_highlights(plan, profile_status),
        "strategy_baseline": build_strategy_baseline(workspace_root),
        "next_allowed_actions": build_next_allowed_actions(plan, profile_status),
        "blocked_actions": plan["blockedActions"],
        "blocked_action_details": build_blocked_action_details(plan["blockedActions"]),
        "investor_profile_inputs": profile_status["missing_fields"],
        "questions_to_answer": build_profile_questions(plan, workspace_root),
        "investor_profile": profile_status,
        "artifact_language": infer_research_artifact_language(request),
        "plain_language_output": True,
    }


def investment_universe_label(universe: str | None) -> str:
    value = str(universe or "").strip()
    return INVESTMENT_UNIVERSE_LABELS.get(value, value.replace("_", " ").title() if value else "Unknown")


def no_subagent_lane_copy(plan: dict[str, Any]) -> dict[str, str]:
    lane = str(plan.get("lane") or "")
    if lane == "head_manager_strategy_authoring":
        return {
            "workflow_intro": "Use this workspace's head-manager strategy workflow.",
            "skill_instruction": "Use `$strategy-creator` and the `tcx strategies` path for validated strategy creation, update, inspection, or activation.",
            "secret_instruction": "Do not read, print, store, or transform raw secrets.",
            "broker_instruction": "Do not call broker APIs directly from shell, hooks, skills, or ad hoc code.",
            "artifact_instruction": "Do not create ticker research, order tickets, approvals, or execution artifacts unless the user later asks for a separate workflow lane.",
            "output_instruction": "Use plain-English strategy status first, then put validation details behind concise evidence labels.",
        }
    if lane == "connector_build":
        return {
            "workflow_intro": "Use this workspace's TradingCodex build workflow.",
            "skill_instruction": "Use `$tcx-build` plus `tcx connectors scaffold|register|validate` for connector implementation work.",
            "secret_instruction": "Do not read, print, store, or transform raw secrets; create only credential_ref schemas.",
            "broker_instruction": "Do not call broker APIs directly from shell, hooks, skills, or ad hoc code outside TradingCodex service validation paths.",
            "artifact_instruction": "Create connector scaffold files, docs, and tests only; do not create order tickets, approvals, execution artifacts, or live_order enablement.",
            "output_instruction": "Report build gate, scaffold files, validation status, and restart/doctor steps; stop before live execution.",
        }
    return {
        "workflow_intro": "Use this workspace's head-manager operational workflow.",
        "skill_instruction": "Use `$tcx-server` for connector setup, profile inspection, health checks, and translation preview.",
        "secret_instruction": "Do not read, print, store, or transform raw secrets.",
        "broker_instruction": "Do not call broker APIs directly from shell, hooks, skills, or ad hoc code.",
        "artifact_instruction": "Do not create order tickets, approvals, or execution artifacts unless the user later asks for them explicitly.",
        "output_instruction": "Use plain-English status output first, then put technical connector details behind concise evidence labels.",
    }


def required_profile_inputs(plan: dict[str, Any]) -> list[str]:
    return list(SUITABILITY_PROFILE_FIELDS) if plan.get("lane") in PROFILE_REQUIRED_LANES else []


def investor_profile_status(plan: dict[str, Any], workspace_root: Path | str | None = None) -> dict[str, Any]:
    required = required_profile_inputs(plan)
    profile = active_profile_for_workspace(workspace_root) if workspace_root is not None else {}
    investor_profile = profile.get("investor_profile") if isinstance(profile.get("investor_profile"), dict) else {}
    known_fields = []
    missing_fields = []
    for field in required:
        key = PROFILE_FIELD_KEYS[field]
        answer = investor_profile.get(key) or investor_profile.get(field)
        if answer not in (None, ""):
            known_fields.append({"field": field, "key": key, "answer": str(answer)})
        else:
            missing_fields.append(field)
    completion = 1.0 if not required else round((len(required) - len(missing_fields)) / len(required), 2)
    return {
        "profile_id": str(profile.get("profile_id") or ""),
        "required_fields": required,
        "known_fields": known_fields,
        "missing_fields": missing_fields,
        "completion": completion,
    }


def build_selected_role_details(plan: dict[str, Any]) -> list[dict[str, str]]:
    details = []
    for role in plan.get("subagents") or []:
        label = AGENT_SPECS[role].label if role in AGENT_SPECS else role
        details.append({
            "role": role,
            "label": label,
            "why_selected": ROLE_SELECTION_COPY.get(role, "Handles the role-specific workflow step selected for this lane."),
        })
    return details


def build_method_lenses(plan: dict[str, Any]) -> list[dict[str, str]]:
    lane = str(plan.get("lane") or "research_only")
    return [dict(item) for item in METHOD_LENS_COPY.get(lane, METHOD_LENS_COPY["research_only"])]


def build_loop_controls(plan: dict[str, Any]) -> list[dict[str, str]]:
    lane = str(plan.get("lane") or "research_only")
    controls = [dict(item) for item in LOOP_CONTROL_COPY.get(lane, LOOP_CONTROL_COPY["research_only"])]
    return [*controls, dict(LOOP_VERIFICATION_CONTROL)]


def build_judgment_controls(plan: dict[str, Any]) -> list[dict[str, str]]:
    lane = str(plan.get("lane") or "research_only")
    return [dict(item) for item in JUDGMENT_CONTROL_COPY.get(lane, JUDGMENT_CONTROL_COPY["default"])]


def build_review_highlights(plan: dict[str, Any], profile_status: dict[str, Any] | None = None) -> list[dict[str, str]]:
    lane = str(plan.get("lane") or "research_only")
    challenge = next(
        (item for item in build_judgment_controls(plan) if item.get("label") == "Challenge review"),
        {},
    )
    highlights = [
        {
            "label": "Pressure test",
            "detail": challenge.get("detail")
            or "Before synthesis, name the strongest reason the idea may be wrong.",
        }
    ]
    if lane in PROFILE_REQUIRED_LANES:
        status = profile_status or investor_profile_status(plan)
        missing = list(status.get("missing_fields") or [])
        if missing:
            highlights.append({
                "label": "Profile gap",
                "detail": "Recommendation and sizing stay weak until these are answered: " + ", ".join(missing) + ".",
            })
        else:
            highlights.append({
                "label": "Profile fit",
                "detail": "Saved objective, horizon, risk, liquidity, holdings, and constraints must stay visible in the decision.",
            })
    highlights.append({
        "label": "Stop before action",
        "detail": "A useful answer can end at decision support; order, approval, and execution remain separate gates.",
    })
    return highlights


def build_strategy_baseline(workspace_root: Path | str | None = None) -> dict[str, Any]:
    if workspace_root is None:
        return {
            "mode": "not_inspected",
            "active_strategies": [],
            "summary": "Strategy library not inspected in this preview; use explicit user constraints and fixed TradingCodex rules as the baseline.",
            "prompt_summary": "Strategy library not inspected; use explicit user constraints and fixed TradingCodex rules.",
        }
    try:
        records = read_strategy_skill_records(workspace_root, active_only=True)
    except Exception:
        records = []
    strategies = [
        {
            "name": str(record.get("name") or ""),
            "heading": str(record.get("heading") or record.get("name") or ""),
            "status": str(record.get("status") or ""),
        }
        for record in records
        if record.get("name")
    ]
    if strategies:
        names = ", ".join(item["name"] for item in strategies)
        return {
            "mode": "active_user_strategy",
            "active_strategies": strategies,
            "summary": "Active user-approved strategy skills available: "
            + names
            + ". Select at most one relevant strategy and treat it as fixed context, not authority to approve or execute.",
            "prompt_summary": "Active user-approved strategy skills available: "
            + names
            + ". Select at most one relevant strategy as fixed context only.",
        }
    return {
        "mode": "no_saved_strategy",
        "active_strategies": [],
        "summary": "No active user-approved strategy is saved for this workspace; use explicit user constraints and fixed TradingCodex rules as temporary workflow context, not a persistent strategy.",
        "prompt_summary": "No active user-approved strategy; treat request preferences as temporary context, not a persistent strategy.",
    }


def format_method_lenses(plan: dict[str, Any]) -> str:
    return "; ".join(
        f"{item['label']} ({item['reference']})"
        for item in build_method_lenses(plan)
    )


def format_loop_controls(plan: dict[str, Any]) -> str:
    return "; ".join(
        f"{item['label']}: {item['detail']}"
        for item in build_loop_controls(plan)
    )


def format_judgment_controls(plan: dict[str, Any]) -> str:
    return "; ".join(
        f"{item['label']}: {item['detail']}"
        for item in build_judgment_controls(plan)
    )


def format_judgment_controls_compact(plan: dict[str, Any]) -> str:
    return "; ".join(item["label"] for item in build_judgment_controls(plan))


def build_next_allowed_actions(plan: dict[str, Any], profile_status: dict[str, Any] | None = None) -> list[dict[str, str]]:
    lane = str(plan.get("lane") or "research_only")
    actions = [dict(item) for item in NEXT_ALLOWED_ACTION_COPY.get(lane, NEXT_ALLOWED_ACTION_COPY["research_only"])]
    if lane in PROFILE_REQUIRED_LANES and actions and actions[0].get("label") == "Answer missing profile questions":
        status = profile_status or investor_profile_status(plan)
        missing = list(status.get("missing_fields") or [])
        known = list(status.get("known_fields") or [])
        required = list(status.get("required_fields") or [])
        if required and not missing:
            actions[0] = {
                "label": "Use saved profile context",
                "detail": "Objective, horizon, loss capacity, liquidity, holdings, and constraints are present; keep them visible while dispatching roles.",
            }
        elif known:
            actions[0] = {
                "label": "Answer remaining profile questions",
                "detail": "Still missing: " + ", ".join(missing) + ".",
            }
    return actions


def build_idea_translation(plan: dict[str, Any]) -> dict[str, str]:
    lane = str(plan.get("lane") or "research_only")
    lane_copy = WORKFLOW_LANE_COPY.get(lane, WORKFLOW_LANE_COPY["research_only"])
    copy = IDEA_TRANSLATION_COPY.get(lane, IDEA_TRANSLATION_COPY["research_only"])
    return {
        "label": "Idea translated",
        "plain_english": f"{lane_copy['label']}: {lane_copy['primary_question']}",
        "working_hypothesis": copy["working_hypothesis"],
        "safety_boundary": copy["safety_boundary"],
    }


def build_profile_questions(plan: dict[str, Any], workspace_root: Path | str | None = None) -> list[dict[str, str]]:
    questions = []
    for field in investor_profile_status(plan, workspace_root)["missing_fields"]:
        copy = PROFILE_QUESTION_COPY[field]
        questions.append({
            "category": "investor_profile",
            "field": field,
            "key": PROFILE_FIELD_KEYS[field],
            "question": copy["question"],
            "why_required": copy["why_required"],
        })
    return questions


def build_blocked_action_details(actions: list[str]) -> list[dict[str, str]]:
    details = []
    for action in actions:
        copy = BLOCKED_ACTION_COPY.get(action, {})
        details.append({
            "action": action,
            "label": copy.get("label") or action.replace("_", " ").title(),
            "reason": copy.get("reason") or "This action waits for the required TradingCodex workflow gate.",
        })
    return details


def build_workflow_stages(plan: dict[str, Any]) -> list[dict[str, Any]]:
    lane = str(plan.get("lane") or "")
    subagents = list(plan.get("subagents") or [])
    role_labels = {role: AGENT_SPECS[role].label for role in subagents if role in AGENT_SPECS}
    stages: list[dict[str, Any]] = [
        {
            "key": "intake",
            "label": "Intake",
            "owner": "head-manager",
            "summary": "Classify the request, preserve user constraints, and keep blocked actions visible.",
            "exit_criteria": ["workflow lane selected", "required roles identified", "blocked actions recorded"],
            "roles": ["head-manager"],
        }
    ]
    if lane == "head_manager_connector_operations":
        stages.append({
            "key": "connector_setup",
            "label": "Connector setup",
            "owner": "head-manager",
            "summary": "Inspect connector metadata and health without reading secrets or creating trade artifacts.",
            "exit_criteria": ["connector metadata reviewed", "secret-free status reported", "no order artifacts created"],
            "roles": ["head-manager"],
        })
        stages.append(_synthesis_stage())
        return stages
    if lane == "connector_build":
        stages.append({
            "key": "build_gate",
            "label": "Build gate",
            "owner": "head-manager",
            "summary": "Verify Codex full access and explicit TradingCodex build mode before modifying connector or harness files.",
            "exit_criteria": ["full access detected", "TradingCodex build mode active", "live execution remains blocked"],
            "roles": ["head-manager"],
        })
        stages.append({
            "key": "connector_scaffold",
            "label": "Connector scaffold",
            "owner": "head-manager",
            "summary": "Scaffold or register a broker/API connector using credential_ref, read scopes, and sandbox/test validation only.",
            "exit_criteria": ["connector files generated", "credential_ref schema present", "read/test validation checked", "live_order disabled"],
            "roles": ["head-manager"],
        })
        stages.append(_synthesis_stage())
        return stages
    if lane == "head_manager_strategy_authoring":
        stages.append({
            "key": "strategy_authoring",
            "label": "Strategy authoring",
            "owner": "head-manager",
            "summary": "Draft or update a fixed strategy guide through strategy-creator validation.",
            "exit_criteria": ["required strategy sections present", "risk controls stated", "no action authority added"],
            "roles": ["head-manager"],
        })
        stages.append(_challenge_review_stage())
        stages.append(_synthesis_stage())
        return stages

    research_roles = [role for role in subagents if role in RESEARCH_STAGE_ROLES]
    if research_roles:
        stages.append({
            "key": "evidence",
            "label": "Evidence",
            "owner": "research roles",
            "summary": "Collect source-aware role artifacts before any downstream judgment.",
            "exit_criteria": ["artifact paths written", "source/as-of posture recorded", "missing evidence named"],
            "roles": [{"role": role, "label": role_labels.get(role, role)} for role in research_roles],
        })
    if "valuation-analyst" in subagents:
        stages.append({
            "key": "valuation",
            "label": "Valuation",
            "owner": "valuation-analyst",
            "summary": "Translate accepted evidence into scenarios, assumptions, valuation range, and uncertainty.",
            "exit_criteria": ["assumptions stated", "scenario range produced", "confidence and sensitivity recorded"],
            "roles": [{"role": "valuation-analyst", "label": role_labels.get("valuation-analyst", "valuation-analyst")}],
        })
    if lane == "order_ticket_draft_gate":
        stages.append({
            "key": "order_ticket_draft",
            "label": "Order draft",
            "owner": "portfolio-manager",
            "summary": "Prepare a structured order-ticket candidate while approval and execution stay blocked.",
            "exit_criteria": ["canonical order fields complete", "policy checks ready", "approval remains blocked"],
            "roles": [{"role": "portfolio-manager", "label": role_labels.get("portfolio-manager", "portfolio-manager")}],
        })
    elif "portfolio-manager" in subagents:
        stages.append({
            "key": "portfolio_fit",
            "label": "Portfolio fit",
            "owner": "portfolio-manager",
            "summary": "Check exposure, concentration, liquidity, opportunity cost, and investor-profile gaps.",
            "exit_criteria": ["portfolio impact stated", "profile gaps named", "sizing support or blockage recorded"],
            "roles": [{"role": "portfolio-manager", "label": role_labels.get("portfolio-manager", "portfolio-manager")}],
        })
    if "risk-manager" in subagents:
        stages.append({
            "key": "risk_review",
            "label": "Risk review",
            "owner": "risk-manager",
            "summary": "Review policy, restricted-list, downside, approval readiness, and blocked actions.",
            "exit_criteria": ["policy decision recorded", "downside risks stated", "approval readiness or blocked reason returned"],
            "roles": [{"role": "risk-manager", "label": role_labels.get("risk-manager", "risk-manager")}],
        })
    if "execution-operator" in subagents:
        stages.append({
            "key": "execution_boundary",
            "label": "Approved action path",
            "owner": "execution-operator",
            "summary": "Submit only if ticket, approval receipt, policy, duplicate-request, connection, and audit checks pass.",
            "exit_criteria": ["approved ticket matched", "duplicate-request status checked", "connection and audit result recorded"],
            "roles": [{"role": "execution-operator", "label": role_labels.get("execution-operator", "execution-operator")}],
        })
    stages.append(_challenge_review_stage("risk-manager" in subagents))
    stages.append(_synthesis_stage())
    return stages


def _challenge_review_stage(has_risk_role: bool = False) -> dict[str, Any]:
    roles: list[Any] = ["head-manager"]
    if has_risk_role:
        roles.append({"role": "risk-manager", "label": "Risk Manager"})
    return {
        "key": "challenge_review",
        "label": "Challenge review",
        "owner": "head-manager" if not has_risk_role else "head-manager with risk-manager artifact",
        "summary": "Test accepted artifacts against contrary evidence, alternative scenarios, rule or strategy conflicts, and blocked actions before synthesis.",
        "exit_criteria": ["counterarguments named", "rule and strategy conflicts checked", "revise, blocked, or waiting used when support is weak"],
        "roles": roles,
    }


def _synthesis_stage() -> dict[str, Any]:
    return {
        "key": "synthesis",
        "label": "Synthesis",
        "owner": "head-manager",
        "summary": "Summarize accepted artifacts, disagreements, missing evidence, and the next allowed action.",
        "exit_criteria": ["accepted artifacts cited", "uncertainties preserved", "next allowed action stated"],
        "roles": ["head-manager"],
    }


def infer_research_artifact_language(request: str) -> str:
    text = request.strip()
    lower = text.lower()
    if not text:
        return "same language as the original user request unless explicitly overridden"
    explicit_language_patterns = [
        (r"\bkorean\b|한국어|한글", "Korean"),
        (r"\benglish\b|영어", "English"),
        (r"\bjapanese\b|일본어|日本語", "Japanese"),
        (r"\bchinese\b|중국어|中文|汉语|漢語", "Chinese"),
    ]
    for pattern, language in explicit_language_patterns:
        if re.search(pattern, lower):
            return f"{language} (explicitly requested or named by the user)"
    if re.search(r"[\uac00-\ud7a3]", text):
        return "Korean (inferred from the original user request)"
    if re.search(r"[\u3040-\u30ff]", text):
        return "Japanese (inferred from the original user request)"
    if re.search(r"[\u4e00-\u9fff]", text):
        return "Chinese (inferred from the original user request)"
    return "same language as the original user request unless explicitly overridden"


def strip_negated_action_phrases(text: str) -> str:
    text = re.sub(r"\b(?:do not|don't|dont|does not|doesn't)\s+want\s+(?:to\s+)?(?:a\s+|any\s+)?(trade|trading|trades|order|orders|execution|execute|approval|approve|buy|sell)\b", " ", text)
    text = re.sub(r"\b(?:not|no longer)\s+(?:wanting|seeking|requesting)\s+(?:to\s+)?(?:a\s+|any\s+)?(trade|trading|trades|order|orders|execution|execute|approval|approve|buy|sell)\b", " ", text)
    text = re.sub(r"\b(no|do not|don't|dont|without)\s+(?:a\s+|any\s+)?(account access|order draft|trade execution|trading|trade|trades|orders|order|draft|execution|execute|approval|approve|buy|sell|recommendation|recommend|decision support|valuation|fair value|target price|price target|multiples|dcf|technical analysis|technical|chart|price action|news analysis|news|headline|event review|portfolio review|portfolio|risk review|risk)\b", " ", text)
    text = re.sub(r"\b(no|do not|don't|dont|without)\s+(live trading|live execution|broker access|account|broker|trade execution)\b", " ", text)
    text = re.sub(r"\bnot\s+(?:asking\s+for\s+|requesting\s+|seeking\s+)?(?:a\s+|any\s+)?(order|trade|execution|approval|valuation|fair value|target price|price target|recommendation|portfolio review|risk review|technical analysis|news analysis)\b", " ", text)
    return text


def strip_guardrail_verification_phrases(text: str) -> str:
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


ROLE_NODE_POSITIONS: dict[str, tuple[int, int]] = {
    "head-manager": (50, 10),
    "fundamental-analyst": (12, 29),
    "technical-analyst": (31, 29),
    "news-analyst": (50, 29),
    "macro-analyst": (69, 29),
    "instrument-analyst": (88, 29),
    "valuation-analyst": (31, 53),
    "portfolio-manager": (50, 66),
    "risk-manager": (69, 78),
    "execution-operator": (88, 91),
}


TOPOLOGY_EDGES: tuple[dict[str, str], ...] = (
    {"source": "head-manager", "target": "fundamental-analyst", "group": "dispatch"},
    {"source": "head-manager", "target": "technical-analyst", "group": "dispatch"},
    {"source": "head-manager", "target": "news-analyst", "group": "dispatch"},
    {"source": "head-manager", "target": "macro-analyst", "group": "dispatch"},
    {"source": "head-manager", "target": "instrument-analyst", "group": "dispatch"},
    {"source": "fundamental-analyst", "target": "valuation-analyst", "group": "research-handoff"},
    {"source": "technical-analyst", "target": "valuation-analyst", "group": "research-handoff"},
    {"source": "news-analyst", "target": "valuation-analyst", "group": "research-handoff"},
    {"source": "macro-analyst", "target": "portfolio-manager", "group": "portfolio-risk-gate"},
    {"source": "instrument-analyst", "target": "portfolio-manager", "group": "portfolio-risk-gate"},
    {"source": "valuation-analyst", "target": "portfolio-manager", "group": "portfolio-risk-gate"},
    {"source": "portfolio-manager", "target": "risk-manager", "group": "approval-gate"},
    {"source": "risk-manager", "target": "execution-operator", "group": "execution-gate"},
)


EDGE_GROUP_LABELS: dict[str, str] = {
    "dispatch": "Dispatch",
    "research-handoff": "Research handoff",
    "portfolio-risk-gate": "Portfolio/risk gate",
    "approval-gate": "Approval gate",
    "execution-gate": "Approved action gate",
}


def get_harness_topology(workspace_root: Path | str | None = None) -> dict[str, Any]:
    tools = _static_mcp_tools()
    nodes = []
    for role, skills in ROLE_SKILL_MAP.items():
        x, y = ROLE_NODE_POSITIONS[role]
        spec = AGENT_SPECS[role]
        allowed_tools = _allowed_tools_for_role(role, tools)
        nodes.append({
            "role": role,
            "label": spec.label,
            "group": ROLE_DISPLAY_GROUPS.get(role, spec.group),
            "purpose": ROLE_PURPOSES.get(role, ""),
            "skills_count": len(skills),
            "tools_count": len(allowed_tools),
            "x": x,
            "y": y,
        })
    edges = []
    for edge in TOPOLOGY_EDGES:
        source_x, source_y = ROLE_NODE_POSITIONS[edge["source"]]
        target_x, target_y = ROLE_NODE_POSITIONS[edge["target"]]
        mid_y = round((source_y + target_y) / 2, 2)
        edges.append({
            **edge,
            "label": EDGE_GROUP_LABELS[edge["group"]],
            "contract": EDGE_GROUP_CONTRACTS[edge["group"]],
            "source_x": source_x,
            "source_y": source_y,
            "target_x": target_x,
            "target_y": target_y,
            "mid_y": mid_y,
        })
    return {
        "nodes": nodes,
        "edges": edges,
        "edge_groups": [{"key": key, "label": label, "contract": EDGE_GROUP_CONTRACTS[key]} for key, label in EDGE_GROUP_LABELS.items()],
        "handoff_states": list(HANDOFF_STATES),
        "systems": get_harness_systems(),
        "components": list_harness_components(),
        "component_tag_counts": count_harness_component_tags(),
        "layers": [
            {"label": "Coordinator", "y": 10},
            {"label": "Research roles", "y": 29},
            {"label": "Valuation", "y": 53},
            {"label": "Portfolio fit", "y": 66},
            {"label": "Risk approval", "y": 78},
            {"label": "Approved submission", "y": 91},
        ],
        "boundary": {
            "label": "Approved action boundary",
            "summary": "Execution-sensitive actions must prove the requester, permission, policy fit, exact approval, duplicate-request status, connection, and audit trail.",
            "x": 78,
            "y1": 72,
            "y2": 96,
        },
        "db_canonical": True,
        "workspace_context": workspace_context_payload(workspace_root),
    }


def get_harness_systems() -> list[dict[str, Any]]:
    return [
        {
            "key": "guardrails",
            "label": "Guardrails",
            "summary": "Reduce, isolate, and block risky behavior before executable action.",
            "items": [
                {"label": "Guidance", "summary": "Prompts, skills, hooks, and checklists shape agent behavior."},
                {"label": "Enforcement", "summary": "Policy, schemas, approvals, allowlists, duplicate-request checks, and connection gates block unsafe final paths."},
                {"label": "Information barriers", "summary": "Role-local context, file walls, secret walls, and tool boundaries limit knowledge flow."},
            ],
        },
        {
            "key": "improvement",
            "label": "Improvement",
            "summary": "Raise workflow quality and feed lessons back into the harness.",
            "items": [
                {"label": "Workflow quality", "summary": "Workflow maps, no-overlap handoffs, role briefs, quality gates, and readiness labels."},
                {"label": "Research memory", "summary": "Workspace markdown artifacts, versions, source snapshots, and freshness warnings."},
                {"label": "Skill evolution", "summary": "File proposal, validation, projection, and manifest state."},
                {"label": "Postmortems", "summary": "Rejected orders, thesis changes, and process failures become concrete improvements."},
                {"label": "Validation feedback", "summary": "Recurring issues become tests, smoke checks, and routing scenarios."},
            ],
        },
    ]


def get_role_detail(role: str, workspace_root: Path | str | None = None) -> dict[str, Any]:
    if role not in ROLE_SKILL_MAP:
        role = "head-manager"
    tools = _static_mcp_tools()
    spec = AGENT_SPECS[role]
    return {
        "role": role,
        "label": spec.label,
        "group": ROLE_DISPLAY_GROUPS.get(role, spec.group),
        "purpose": ROLE_PURPOSES.get(role, ""),
        "skills": ROLE_SKILL_MAP[role],
        "handoff_contract": ROLE_HANDOFF_CONTRACTS.get(role, {}),
        "handoff_states": list(HANDOFF_STATES),
        "allowed_tools": _allowed_tools_for_role(role, tools),
        "forbidden_actions": list(ROLE_FORBIDDEN_ACTIONS.get(role, ())),
        "latest_artifacts": _latest_role_artifacts(role, workspace_root),
        "latest_activity": _latest_role_activity(role, workspace_root),
        "db_canonical": True,
        "workspace_context": workspace_context_payload(workspace_root),
    }


def get_harness_health(workspace_root: Path | str | None = None) -> dict[str, Any]:
    try:
        ensure_runtime_database(workspace_root)
    except Exception:
        pass

    from tradingcodex_service.mcp_runtime import static_mcp_tools

    tools = static_mcp_tools()
    counts = {
        "roster": len(EXPECTED_SUBAGENTS),
        "roles_total": len(ROLE_SKILL_MAP),
        "skills": len(EXPECTED_SKILLS),
        "components": len(list_harness_components()),
        "mcp_tools": len(tools),
        "mcp_execution_tools": sum(1 for tool in tools if tool.get("annotations", {}).get("risk_level") == "execution"),
        "policy_blocks": _model_count("apps.policy.models", "PolicyDecision", decision="deny"),
        "restricted_symbols": _model_count("apps.policy.models", "RestrictedSymbol", active=True),
        "workspace_contexts": _model_count("apps.harness.models", "WorkspaceContext"),
        "research_artifacts": _workspace_research_count(workspace_root),
        "order_tickets": _model_count("apps.orders.models", "OrderTicket"),
        "approval_receipts": _model_count("apps.orders.models", "ApprovalReceipt"),
        "execution_results": _model_count("apps.orders.models", "ExecutionResult"),
        "mcp_calls": _model_count("apps.mcp.models", "McpToolCall"),
    }
    checks = [
        {"label": "Fixed subagent roster", "value": f"{counts['roster']} of 9", "status": "good"},
        {"label": "Repo skills installed", "value": str(counts["skills"]), "status": "good"},
        {"label": "Handoff contract", "value": "/".join(HANDOFF_STATES), "status": "good"},
        {"label": "Harness components", "value": str(counts["components"]), "status": "good"},
        {"label": "Available actions", "value": str(counts["mcp_tools"]), "status": "good"},
        {"label": "Execution tools", "value": str(counts["mcp_execution_tools"]), "status": "warn"},
        {"label": "Policy blocks", "value": str(counts["policy_blocks"]), "status": "neutral"},
        {"label": "Workspace contexts", "value": str(counts["workspace_contexts"]), "status": "neutral"},
    ]
    return {
        "counts": counts,
        "checks": checks,
        "systems": get_harness_systems(),
        "components": list_harness_components(),
        "component_tag_counts": count_harness_component_tags(),
        "db_path": str(tradingcodex_db_path()),
        "central_local_service": True,
        "db_canonical": True,
        "workspace_context": workspace_context_payload(workspace_root),
    }


def list_recent_activity(workspace_root: Path | str | None = None, limit: int = 12) -> list[dict[str, Any]]:
    try:
        ensure_runtime_database(workspace_root)
    except Exception:
        pass
    context = workspace_context_payload(workspace_root)
    items: list[dict[str, Any]] = []
    try:
        from apps.mcp.models import McpToolCall

        for call in _filter_workspace_queryset(McpToolCall.objects, context).order_by("-created_at", "-id")[:limit]:
            items.append({
                "kind": "MCP",
                "title": call.tool_name,
                "subtitle": call.principal_id,
                "status": call.status,
                "status_class": _status_class(call.status),
                "created_at": call.created_at,
            })
    except Exception:
        pass
    try:
        from apps.audit.models import AuditEvent

        for event in _filter_workspace_queryset(AuditEvent.objects, context).order_by("-created_at", "-id")[:limit]:
            items.append({
                "kind": "Audit",
                "title": event.action,
                "subtitle": event.actor_principal,
                "status": event.decision,
                "status_class": _status_class(event.decision),
                "created_at": event.created_at,
            })
    except Exception:
        pass
    try:
        from apps.workflows.models import WorkflowRun

        for run in _filter_workspace_queryset(WorkflowRun.objects, context).order_by("-created_at", "-id")[:limit]:
            items.append({
                "kind": "Workflow",
                "title": run.lane,
                "subtitle": run.universe,
                "status": run.status,
                "status_class": _status_class(run.status),
                "created_at": run.created_at,
            })
    except Exception:
        pass
    items.sort(key=lambda item: item["created_at"], reverse=True)
    return items[:limit]


def list_policy_overview(workspace_root: Path | str | None = None) -> dict[str, Any]:
    try:
        ensure_runtime_database(workspace_root)
    except Exception:
        pass
    restricted_symbols: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []
    principals: list[dict[str, Any]] = []
    try:
        from apps.policy.models import PolicyDecision, Principal, RestrictedSymbol

        restricted_symbols = [
            {"symbol": item.symbol, "reason": item.reason, "active": item.active, "status_class": "bad" if item.active else "neutral"}
            for item in RestrictedSymbol.objects.order_by("symbol")[:50]
        ]
        decisions = [
            {
                "principal_id": item.principal_id,
                "action": item.action,
                "resource": item.resource,
                "decision": item.decision,
                "reasons": item.reasons,
                "created_at": item.created_at,
                "status_class": _status_class(item.decision),
            }
            for item in PolicyDecision.objects.order_by("-created_at", "-id")[:20]
        ]
        principals = [
            {"principal_id": item.principal_id, "role": item.role, "active": item.active}
            for item in Principal.objects.order_by("role", "principal_id")[:50]
        ]
    except Exception:
        pass
    return {
        "restricted_symbols": restricted_symbols,
        "recent_decisions": decisions,
        "principals": principals,
        "explicit_denies": sorted(EXPLICIT_DENY_ACTIONS),
        "db_canonical": True,
        "workspace_context": workspace_context_payload(workspace_root),
    }


def _allowed_tools_for_role(role: str, tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    allowed = []
    for tool in tools:
        annotations = tool.get("annotations") or {}
        risk_level = str(annotations.get("risk_level", "read") or "read")
        if role in annotations.get("allowed_roles", []):
            allowed.append({
                "name": tool["name"],
                "category": annotations.get("category", ""),
                "risk_level": risk_level,
                "risk_label": risk_level.replace("_", " ").title(),
                "requires_approval": bool(annotations.get("requires_approval")),
                "status_class": _status_class(risk_level),
            })
    return allowed


def _latest_role_artifacts(role: str, workspace_root: Path | str | None) -> list[dict[str, Any]]:
    try:
        role_alias = role.replace("-analyst", "").replace("-manager", "").replace("-operator", "")
        artifacts = [
            artifact
            for artifact in list_workspace_research_artifacts(Path(workspace_root or Path.cwd()))
            if artifact.get("created_by") == role or artifact.get("role") in {role, role_alias}
        ]
        return [
            {
                "artifact_id": artifact["artifact_id"],
                "title": artifact["title"],
                "artifact_type": artifact["artifact_type"],
                "universe": artifact["universe"],
                "readiness_label": artifact.get("readiness_label") or "unlabeled",
                "updated_at": artifact["updated_at"],
            }
            for artifact in artifacts[:5]
        ]
    except Exception:
        return []


def _workspace_research_count(workspace_root: Path | str | None) -> int:
    try:
        return len(list_workspace_research_artifacts(Path(workspace_root or Path.cwd())))
    except Exception:
        return 0


def _latest_role_activity(role: str, workspace_root: Path | str | None = None) -> list[dict[str, Any]]:
    try:
        from apps.mcp.models import McpToolCall

        context = workspace_context_payload(workspace_root)
        return [
            {
                "title": call.tool_name,
                "status": call.status,
                "status_class": _status_class(call.status),
                "created_at": call.created_at,
            }
            for call in _filter_workspace_queryset(McpToolCall.objects.filter(principal_id=role), context).order_by("-created_at", "-id")[:5]
        ]
    except Exception:
        return []


def _filter_workspace_queryset(queryset: Any, context: dict[str, Any]) -> Any:
    workspace_id = str(context.get("workspace_id") or "")
    if workspace_id:
        return queryset.filter(workspace_context__workspace_id=workspace_id)
    return queryset.none()


def _model_count(module_name: str, class_name: str, **filters: Any) -> int:
    try:
        module = __import__(module_name, fromlist=[class_name])
        model = getattr(module, class_name)
        queryset = model.objects.filter(**filters) if filters else model.objects
        return int(queryset.count())
    except Exception:
        return 0

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from tradingcodex_service.application.common import _status_class, _unique
from tradingcodex_service.application.agents import (
    EXPECTED_SKILLS,
    EXPECTED_SUBAGENTS,
    ROLE_PERMISSION_PROFILES,
    ROLE_SKILL_MAP,
    USER_VISIBLE_SKILLS,
)
from tradingcodex_service.application.components import count_harness_component_tags, list_harness_components
from tradingcodex_service.application.policy import EXPLICIT_DENY_ACTIONS
from tradingcodex_service.application.research import list_workspace_research_artifacts
from tradingcodex_service.application.runtime import ensure_runtime_database, tradingcodex_db_path, workspace_context_payload
from tradingcodex_service.mcp_runtime import static_mcp_tools as _static_mcp_tools

INVESTMENT_WORKFLOW_TERMS = re.compile(
    r"\b("
    r"stock|stocks|equity|equities|share|shares|security|securities|ticker|tickers|"
    r"etf|index|indices|option|options|derivative|futures|crypto|bitcoin|btc|ethereum|eth|"
    r"bond|bonds|credit|cds|rates|fx|currency|commodity|commodities|oil|gold|"
    r"portfolio|position|holding|exposure|sizing|hedge|risk|drawdown|"
    r"valuation|fair value|target price|earnings|filing|catalyst|thesis|"
    r"technical|price action|trend|momentum|volume|volatility|"
    r"buy|sell|short|long|trade|trading|order intent|paper order|broker|"
    r"approve|approval|execute|execution"
    r")\b"
)
INVESTMENT_ACTION_WITH_SYMBOL = re.compile(r"\b(analyze|analyse|review|research|evaluate|assess)\b")
SYMBOL_LIKE_TOKEN = re.compile(r"\b[A-Z][A-Z0-9.\-]{0,6}\b")
NON_INVESTMENT_CONTEXT_TERMS = re.compile(
    r"\b("
    r"repo|repository|code|docs|documentation|readme|file|files|python|django|"
    r"pytest|test|tests|template|templates|prompt|prompts|hook|hooks|config|"
    r"migration|model|admin|api|mcp|cli|function|class|bug|fix|implement|"
    r"refactor|skill|skills"
    r")\b|agents\.md"
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
HANDOFF_STATES = ("accepted", "revise", "blocked", "waiting")
ROLE_HANDOFF_CONTRACTS: dict[str, dict[str, str]] = {
    "head-manager": {
        "receives": "User request, accepted role artifacts, workflow/service state.",
        "returns": "Lane, selected team, compact briefs, accepted artifacts, conflicts, and next allowed action.",
        "quality_gate": "Marks handoffs accepted, revise, blocked, or waiting before moving the workflow forward.",
        "overlap_rule": "Coordinates and synthesizes; does not replace specialist role analysis.",
    },
    "fundamental-analyst": {
        "receives": "Assigned evidence and source references.",
        "returns": "Fundamental report with source/as-of posture, confidence, and missing evidence.",
        "quality_gate": "Business, financial, filing, and economics claims stay evidence-backed and role-owned.",
        "overlap_rule": "Does not create valuation, order intent, approval, or execution posture.",
    },
    "technical-analyst": {
        "receives": "Assigned market-data references and user-stated technical constraints.",
        "returns": "Technical report with setup observations, data posture, confidence, and invalidation gaps.",
        "quality_gate": "Price, volume, volatility, and liquidity claims show data timing and uncertainty.",
        "overlap_rule": "Does not turn a setup into a standalone investment conclusion or order intent.",
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
        "overlap_rule": "Does not infer execution eligibility from data availability or connector coverage.",
    },
    "valuation-analyst": {
        "receives": "Accepted research artifacts and user-stated method constraints.",
        "returns": "Valuation report with assumptions, sensitivity, confidence, and readiness for portfolio/risk review.",
        "quality_gate": "Valuation assumptions and current-price implications are explicit and source-aware.",
        "overlap_rule": "Does not approve, execute, or replace missing research evidence.",
    },
    "portfolio-manager": {
        "receives": "Accepted research/valuation artifacts and portfolio state.",
        "returns": "Portfolio fit report and, only when allowed, draft order readiness or draft order intent.",
        "quality_gate": "Sizing, cash, concentration, liquidity, and opportunity-cost assumptions are visible.",
        "overlap_rule": "Does not self-approve, execute, or repair missing research/valuation work.",
    },
    "risk-manager": {
        "receives": "Accepted portfolio/order artifacts, policy state, restricted-list state, and audit evidence.",
        "returns": "Risk/policy report, approval readiness state, approval receipt when allowed, or blocked reasons.",
        "quality_gate": "Downside, limits, restricted-list, and approval-readiness checks are explicit.",
        "overlap_rule": "Does not draft orders, submit execution, or loosen policy in the same workflow.",
    },
    "execution-operator": {
        "receives": "Approved order intent, approval receipt, and policy allow state.",
        "returns": "Execution result, MCP response, audit reference, or rejected/blocked reasons.",
        "quality_gate": "Execution uses only approved paper/stub MCP paths and records audit evidence.",
        "overlap_rule": "Does not approve, change policy, read secrets, or call raw broker APIs.",
    },
}
EDGE_GROUP_CONTRACTS: dict[str, str] = {
    "dispatch": "Assignment envelope only: original request, constraints, lane, artifact path, out-of-scope actions, and return contract.",
    "research-handoff": "Accepted research artifacts move to valuation; weak or missing evidence returns to the owning research role.",
    "portfolio-risk-gate": "Accepted research/valuation/instrument context moves to portfolio and risk review; gaps block draft readiness.",
    "approval-gate": "Portfolio draft readiness moves to risk approval only after schema, policy, and role-separation checks.",
    "execution-gate": "Risk approval moves to execution only with approved order intent, approval receipt, policy allow, idempotency, adapter, and audit.",
}


def is_investment_workflow_request(request: str) -> bool:
    text = request.strip()
    if not text:
        return False
    if is_secret_only_request(text):
        return False
    lower = text.lower()
    if "$orchestrate-workflow" in lower:
        return True
    if INVESTMENT_WORKFLOW_TERMS.search(lower):
        return True
    if INVESTMENT_ACTION_WITH_SYMBOL.search(lower) and SYMBOL_LIKE_TOKEN.search(text):
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


def classify_starter_request(request: str) -> dict[str, Any]:
    text = request.lower()
    universe = classify_investment_universe(text)
    action_text = strip_guardrail_verification_phrases(strip_negated_action_phrases(text))
    valuation_blocked = negates_scope(text, r"valuation|fair value|target price|price target|multiples|dcf")
    technical_blocked = negates_scope(text, r"technical(?: analysis)?|chart|price action")
    news_blocked = negates_scope(text, r"news(?: analysis)?|headline|event review")
    wants_approval_execution = bool(re.search(r"submit|already approved|approved paper|execute|execution|approve|approval|broker|live", action_text))
    wants_order_draft = bool(re.search(r"draft|order intent|buy order|sell order|paper buy order|paper sell order", action_text))
    wants_decision = bool(re.search(r"should i buy|should i sell|recommend|fair value|target price|buy|sell", action_text))
    wants_thesis_review = bool(re.search(r"earnings|filing|catalyst|preview|thesis|valuation|disclosure|narrative", action_text))
    wants_portfolio_risk = bool(re.search(r"portfolio|position|holding|own|exposure|concentration|correlation|drawdown|hedge|sizing|size|risk", action_text))
    wants_macro = bool(re.search(r"macro|rates|rate|fx|currency|commodity|commodities|inflation|fed|boj|ecb|central bank|yield|oil|gold", action_text))
    wants_instrument = bool(re.search(r"etf|index|indices|option|options|derivative|futures|borrow|short interest|crypto|bitcoin|btc|ethereum|eth|cds|bond|credit|convertible|preferred|instrument|market structure", action_text))
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
    if wants_approval_execution:
        return {"universe": universe, "lane": "order_intent_or_approval_execution_gate", "subagents": _unique((["macro-analyst"] if wants_macro else []) + (["instrument-analyst"] if wants_instrument else []) + ["portfolio-manager", "risk-manager", "execution-operator"]), "blockedActions": ["natural-language order", "direct broker API", "secret read", "execution without approved artifacts"]}
    if wants_order_draft:
        return {"universe": universe, "lane": "order_intent_draft_gate", "subagents": _unique(research + ["portfolio-manager", "risk-manager"]), "blockedActions": ["approval", "execution", "direct broker API", "secret read"]}
    if wants_portfolio_risk:
        return {"universe": universe, "lane": "portfolio_risk_review", "subagents": _unique((["macro-analyst"] if wants_macro else []) + (["instrument-analyst"] if wants_instrument else []) + (["technical-analyst"] if wants_technical else []) + (["news-analyst"] if wants_news else []) + ["portfolio-manager", "risk-manager"]), "blockedActions": ["order intent", "approval", "execution", "direct broker API", "secret read"]}
    if wants_decision:
        return {"universe": universe, "lane": "thesis_review_then_portfolio_risk_review", "subagents": _unique(thesis_roles + ["portfolio-manager", "risk-manager"]), "blockedActions": ["order intent", "approval", "execution", "direct broker API", "secret read"]}
    if wants_thesis_review and universe == "public_equity":
        return {"universe": universe, "lane": "thesis_review", "subagents": _unique(thesis_roles), "blockedActions": ["order intent", "approval", "execution", "direct broker API", "secret read"]}
    return {"universe": universe, "lane": "research_only", "subagents": _unique(research), "blockedActions": ["valuation unless requested", "order intent", "approval", "execution", "direct broker API", "secret read"]}


def classify_investment_universe(text: str) -> str:
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


def build_subagent_starter_prompt(request: str) -> str:
    plan = classify_starter_request(request)
    return "\n".join([
        "Use this workspace's fixed-role subagent workflow.",
        "Explicitly use Codex subagents.",
        f'Original user request (verbatim): "{request}"',
        f"Investment universe: {plan['universe']}",
        f"Workflow lane: {plan['lane']}",
        f"Spawn these fixed role subagents in parallel: {', '.join(plan['subagents'])}",
        "This selected team is binding for the current lane; do not spawn roles outside this exact list unless the user later asks for a broader lane.",
        "For `research_only`, do not add valuation, portfolio, risk, approval, or execution roles.",
        "When calling `spawn_agent` for a fixed role, use `agent_type` and a compact `message`; do not set `fork_context` to true.",
        "Use each role's exact `.codex/agents/*.toml` name as the runtime label.",
        "Preserve the original user request and explicit constraints in every subagent brief.",
        "Do not let head-manager perform substantive investment analysis before subagent outputs exist.",
        "Require each role handoff to include artifact path, handoff state, source/as-of posture, confidence, missing evidence, readiness/support gaps, next eligible recipient, and blocked actions.",
        "Use handoff states: accepted, revise, blocked, waiting.",
        "Do not let downstream roles redo missing upstream work; request revision from the owning role or stop with waiting/blocked status.",
        "Wait for all selected subagents, then synthesize their outputs with artifact paths, handoff states, disagreements, missing evidence, and next allowed action.",
        f"Blocked actions before artifacts: {', '.join(plan['blockedActions'])}",
    ])


def strip_negated_action_phrases(text: str) -> str:
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


ROLE_UI_PROFILES: dict[str, dict[str, Any]] = {
    "head-manager": {
        "label": "Head Manager",
        "group": "main",
        "purpose": "Routes the request, coordinates fixed subagents, waits for artifacts, and synthesizes the workflow state.",
        "forbidden_actions": [
            "Do not replace specialist analysis with direct analysis.",
            "Do not call broker APIs directly.",
            "Do not bypass policy, approval, adapter, or audit checks.",
        ],
    },
    "fundamental-analyst": {
        "label": "Fundamental Analyst",
        "group": "research",
        "purpose": "Reviews business quality, financial statements, official disclosures, and competitive position.",
        "forbidden_actions": ["No order intent.", "No approval.", "No execution.", "No secret access."],
    },
    "technical-analyst": {
        "label": "Technical Analyst",
        "group": "research",
        "purpose": "Reviews price action, trend, volatility, liquidity, volume, and market setup.",
        "forbidden_actions": ["No order intent.", "No execution.", "No standalone investment conclusion."],
    },
    "news-analyst": {
        "label": "News Analyst",
        "group": "research",
        "purpose": "Reviews news, official disclosures, event risk, catalysts, and narrative change.",
        "forbidden_actions": ["No unverified rumor claims.", "No execution.", "No secret access."],
    },
    "macro-analyst": {
        "label": "Macro Analyst",
        "group": "research",
        "purpose": "Reviews rates, FX, commodities, liquidity, policy, and cross-asset transmission.",
        "forbidden_actions": ["No order intent.", "No execution.", "No unsupported implementation claims."],
    },
    "instrument-analyst": {
        "label": "Instrument Analyst",
        "group": "research",
        "purpose": "Reviews ETF/index, options, derivatives, crypto public markets, credit signals, and instrument mechanics.",
        "forbidden_actions": ["No order intent.", "No execution.", "No unsupported instrument execution claims."],
    },
    "valuation-analyst": {
        "label": "Valuation Analyst",
        "group": "analysis",
        "purpose": "Builds valuation, scenario, multiple, DCF, reverse DCF, and expected-return views.",
        "forbidden_actions": ["No approval.", "No execution.", "No broker API calls."],
    },
    "portfolio-manager": {
        "label": "Portfolio Manager",
        "group": "portfolio",
        "purpose": "Reviews portfolio fit, sizing, cash, concentration, and draft order intent readiness.",
        "forbidden_actions": ["No self-approval.", "No execution.", "No arbitrary policy changes."],
    },
    "risk-manager": {
        "label": "Risk Manager",
        "group": "risk",
        "purpose": "Reviews risk, restricted list, downside, policy readiness, and approval receipt eligibility.",
        "forbidden_actions": ["No order drafting.", "No execution.", "No arbitrary policy changes."],
    },
    "execution-operator": {
        "label": "Execution Operator",
        "group": "execution",
        "purpose": "Submits approved order intents through TradingCodex MCP using paper or stub adapters only.",
        "forbidden_actions": ["No raw broker API.", "No secret read.", "No policy change.", "No live broker path in core."],
    },
}


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
    "execution-gate": "Execution gate",
}


def get_harness_topology(workspace_root: Path | str | None = None) -> dict[str, Any]:
    tools = _static_mcp_tools()
    nodes = []
    for role, skills in ROLE_SKILL_MAP.items():
        x, y = ROLE_NODE_POSITIONS[role]
        profile = ROLE_UI_PROFILES[role]
        allowed_tools = _allowed_tools_for_role(role, tools)
        nodes.append({
            "role": role,
            "label": profile["label"],
            "group": profile["group"],
            "purpose": profile["purpose"],
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
            {"label": "MCP execution", "y": 91},
        ],
        "boundary": {
            "label": "MCP execution boundary",
            "summary": "Execution-sensitive actions must pass principal, capability, policy, schema, approval, idempotency, adapter, and audit checks.",
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
                {"label": "Enforcement", "summary": "Policy, schemas, approvals, allowlists, idempotency, and adapters block unsafe final paths."},
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
    profile = ROLE_UI_PROFILES[role]
    return {
        "role": role,
        "label": profile["label"],
        "group": profile["group"],
        "purpose": profile["purpose"],
        "skills": ROLE_SKILL_MAP[role],
        "handoff_contract": ROLE_HANDOFF_CONTRACTS[role],
        "handoff_states": list(HANDOFF_STATES),
        "allowed_tools": _allowed_tools_for_role(role, tools),
        "forbidden_actions": profile["forbidden_actions"],
        "latest_artifacts": _latest_role_artifacts(role, workspace_root),
        "latest_activity": _latest_role_activity(role),
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
        "order_intents": _model_count("apps.orders.models", "OrderIntent"),
        "approval_receipts": _model_count("apps.orders.models", "ApprovalReceipt"),
        "execution_results": _model_count("apps.orders.models", "ExecutionResult"),
        "mcp_calls": _model_count("apps.mcp.models", "McpToolCall"),
    }
    checks = [
        {"label": "Fixed subagent roster", "value": f"{counts['roster']} of 9", "status": "good"},
        {"label": "Repo skills installed", "value": str(counts["skills"]), "status": "good"},
        {"label": "Handoff contract", "value": "/".join(HANDOFF_STATES), "status": "good"},
        {"label": "Harness components", "value": str(counts["components"]), "status": "good"},
        {"label": "MCP tools visible", "value": str(counts["mcp_tools"]), "status": "good"},
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
    items: list[dict[str, Any]] = []
    try:
        from apps.mcp.models import McpToolCall

        for call in McpToolCall.objects.order_by("-created_at", "-id")[:limit]:
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

        for event in AuditEvent.objects.order_by("-created_at", "-id")[:limit]:
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

        for run in WorkflowRun.objects.order_by("-created_at", "-id")[:limit]:
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
        if role in annotations.get("allowed_roles", []):
            allowed.append({
                "name": tool["name"],
                "category": annotations.get("category", ""),
                "risk_level": annotations.get("risk_level", "read"),
                "requires_approval": bool(annotations.get("requires_approval")),
                "status_class": _status_class(annotations.get("risk_level", "read")),
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


def _latest_role_activity(role: str) -> list[dict[str, Any]]:
    try:
        from apps.mcp.models import McpToolCall

        return [
            {
                "title": call.tool_name,
                "status": call.status,
                "status_class": _status_class(call.status),
                "created_at": call.created_at,
            }
            for call in McpToolCall.objects.filter(principal_id=role).order_by("-created_at", "-id")[:5]
        ]
    except Exception:
        return []


def _model_count(module_name: str, class_name: str, **filters: Any) -> int:
    try:
        module = __import__(module_name, fromlist=[class_name])
        model = getattr(module, class_name)
        queryset = model.objects.filter(**filters) if filters else model.objects
        return int(queryset.count())
    except Exception:
        return 0

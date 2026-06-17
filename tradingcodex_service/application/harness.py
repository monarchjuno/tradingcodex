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
    r"buy|sell|short|long|trade|trading|order ticket|paper order|broker|"
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
    r"test/sandbox broker|broker setup|attach broker|configure broker"
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
HANDOFF_STATES = ("accepted", "revise", "blocked", "waiting")
EDGE_GROUP_CONTRACTS: dict[str, str] = {
    "dispatch": "Assignment envelope only: original request, constraints, research artifact language, lane, artifact path, out-of-scope actions, and return contract.",
    "research-handoff": "Accepted research artifacts move to valuation; weak or missing evidence returns to the owning research role.",
    "portfolio-risk-gate": "Accepted research/valuation/instrument context moves to portfolio and risk review; gaps block draft readiness.",
    "approval-gate": "Portfolio draft readiness moves to risk approval only after schema, policy, and role-separation checks.",
    "execution-gate": "Risk approval moves to execution only with approved order ticket, matching approval receipt, policy allow, idempotency, adapter, and audit.",
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
    if STRATEGY_AUTHORING_TERMS.search(lower):
        return False
    if is_connector_operations_only_request(text):
        return False
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


def is_connector_operations_only_request(request: str) -> bool:
    lower = request.lower()
    if not CONNECTOR_OPERATION_TERMS.search(lower):
        return False
    action_text = strip_guardrail_verification_phrases(strip_negated_action_phrases(lower))
    return not REMAINING_ORDER_APPROVAL_EXECUTION_TERMS.search(action_text)


def classify_starter_request(request: str) -> dict[str, Any]:
    text = request.lower()
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
    wants_portfolio_risk = bool(re.search(r"portfolio|position|holding|own|exposure|concentration|correlation|drawdown|hedge|sizing|size|risk", action_text))
    wants_macro = bool(re.search(r"macro|rates|rate|fx|currency|commodity|commodities|inflation|fed|boj|ecb|central bank|yield|oil|gold", action_text))
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
    artifact_language = infer_research_artifact_language(request)
    spawn_line = ", ".join(plan["subagents"]) if plan["subagents"] else "none"
    if not plan["subagents"]:
        return "\n".join([
            "Use this workspace's head-manager operational workflow.",
            "No fixed-role subagent dispatch is required for this lane.",
            f'Original user request (verbatim): "{request}"',
            f"Workflow lane: {plan['lane']}",
            f"Operational universe: {plan['universe']}",
            "Use `$use-tradingcodex-server` for connector setup, profile inspection, health checks, and translation preview.",
            "Do not read, print, store, or transform raw secrets.",
            "Do not call broker APIs directly from shell, hooks, skills, or ad hoc code.",
            "Do not create order tickets, approvals, or execution artifacts unless the user later asks for them explicitly.",
            f"Blocked actions: {', '.join(plan['blockedActions'])}",
        ])
    return "\n".join([
        "Use this workspace's fixed-role subagent workflow.",
        "Explicitly use Codex subagents.",
        f'Original user request (verbatim): "{request}"',
        f"Research artifact language: {artifact_language}",
        f"Investment universe: {plan['universe']}",
        f"Workflow lane: {plan['lane']}",
        f"Spawn these fixed role subagents in parallel: {spawn_line}",
        "This selected team is binding for the current lane; do not spawn roles outside this exact list unless the user later asks for a broader lane.",
        "For `research_only`, do not add valuation, portfolio, risk, approval, or execution roles.",
        "When calling `spawn_agent` for a fixed role, use `agent_type` and a compact `message`; do not set `fork_context` to true.",
        "Use each role's exact `.codex/agents/*.toml` name as the runtime label.",
        "Preserve the original user request and explicit constraints in every subagent brief.",
        "Context budget: use artifact paths, context_summary, source/as-of metadata, and short deltas; do not paste full prior artifacts, source dumps, or unrelated chat history.",
        "Tell each subagent to write reader-facing research artifacts in the research artifact language unless the user explicitly requested a different artifact language.",
        "Tell each subagent to include concise context_summary frontmatter in stored research markdown for downstream reuse.",
        "Do not let head-manager perform substantive investment analysis before subagent outputs exist.",
        "Require each role handoff to include artifact path, handoff state, source/as-of posture, confidence, missing evidence, readiness/support gaps, next eligible recipient, and blocked actions.",
        "Use handoff states: accepted, revise, blocked, waiting.",
        "Do not let downstream roles redo missing upstream work; request revision from the owning role or stop with waiting/blocked status.",
        "Wait for all selected subagents, then synthesize their outputs with artifact paths, handoff states, disagreements, missing evidence, and next allowed action.",
        f"Blocked actions before artifacts: {', '.join(plan['blockedActions'])}",
    ])


def build_compact_dispatch_context(request: str) -> dict[str, Any]:
    plan = classify_starter_request(request)
    return {
        "context_mode": "compact_workflow_gate_v1",
        "investment_universe": plan["universe"],
        "workflow_lane": plan["lane"],
        "required_subagents": plan["subagents"],
        "research_artifact_language": infer_research_artifact_language(request),
        "selected_team_binding": True,
        "blocked_actions": plan["blockedActions"],
        "starter_prompt_path": ".tradingcodex/mainagent/latest-user-prompt-gate.json",
        "dispatch_rules": [
            "dispatch_or_reuse_selected_subagents_before_substantive_analysis",
            "treat_selected_team_as_closed_for_current_lane",
            "use_agent_type_with_compact_message_and_no_full_history_fork",
            "return_waiting_for_subagent_dispatch_if_exact_role_routing_is_unavailable",
            "do_not_repair_missing_upstream_work_inside_downstream_roles",
        ],
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
    "execution-gate": "Execution gate",
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

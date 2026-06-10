from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from tradingcodex_service.application.common import _status_class, _unique
from tradingcodex_service.application.policy import EXPLICIT_DENY_ACTIONS
from tradingcodex_service.application.runtime import ensure_runtime_database, tradingcodex_db_path, workspace_context_payload
from tradingcodex_service.mcp_runtime import static_mcp_tools as _static_mcp_tools

ROLE_SKILL_MAP: dict[str, list[str]] = {
    "head-manager": [
        "orchestrate-workflow",
        "investment-workflow-map",
        "scenario-quality-gates",
        "external-data-source-gate",
        "manage-subagents",
        "head-manager-interview",
        "synthesize-decision",
        "postmortem",
    ],
    "fundamental-analyst": ["external-data-source-gate", "collect-evidence", "fundamental-analysis"],
    "technical-analyst": ["external-data-source-gate", "collect-evidence", "technical-analysis"],
    "news-analyst": ["external-data-source-gate", "collect-evidence", "news-analysis"],
    "macro-analyst": ["external-data-source-gate", "collect-evidence", "macro-analysis"],
    "instrument-analyst": ["external-data-source-gate", "collect-evidence", "instrument-analysis"],
    "valuation-analyst": ["external-data-source-gate", "valuation-review"],
    "portfolio-manager": ["portfolio-review", "create-order-intent"],
    "risk-manager": ["review-risk", "policy-review", "approve-order"],
    "execution-operator": ["execute-paper-order"],
}

USER_VISIBLE_SKILLS = [
    "orchestrate-workflow",
    "head-manager-interview",
    "postmortem",
]

EXPECTED_SUBAGENTS = [role for role in ROLE_SKILL_MAP if role != "head-manager"]
EXPECTED_SKILLS = sorted({skill for skills in ROLE_SKILL_MAP.values() for skill in skills})
ROLE_PERMISSION_PROFILES = {
    "fundamental-analyst": "tradingcodex-fundamental",
    "technical-analyst": "tradingcodex-technical",
    "news-analyst": "tradingcodex-news",
    "macro-analyst": "tradingcodex-macro",
    "instrument-analyst": "tradingcodex-instrument",
    "valuation-analyst": "tradingcodex-valuation",
    "portfolio-manager": "tradingcodex-portfolio",
    "risk-manager": "tradingcodex-risk",
    "execution-operator": "tradingcodex-execution",
}

def classify_starter_request(request: str) -> dict[str, Any]:
    text = request.lower()
    universe = classify_investment_universe(text)
    action_text = strip_guardrail_verification_phrases(strip_negated_action_phrases(text))
    wants_approval_execution = bool(re.search(r"submit|already approved|approved paper|execute|execution|approve|approval|broker|live", action_text))
    wants_order_draft = bool(re.search(r"draft|order intent|buy order|sell order|paper buy order|paper sell order", action_text))
    wants_decision = bool(re.search(r"should i buy|should i sell|recommend|fair value|target price|buy|sell", action_text))
    wants_thesis_review = bool(re.search(r"earnings|filing|catalyst|preview|thesis|valuation|disclosure|narrative", text))
    wants_portfolio_risk = bool(re.search(r"portfolio|position|holding|own|exposure|concentration|correlation|drawdown|hedge|sizing|size|risk", text))
    wants_macro = bool(re.search(r"macro|rates|rate|fx|currency|commodity|commodities|inflation|fed|boj|ecb|central bank|yield|oil|gold", text))
    wants_instrument = bool(re.search(r"etf|index|indices|option|options|derivative|futures|borrow|short interest|crypto|bitcoin|btc|ethereum|eth|cds|bond|credit|convertible|preferred|instrument|market structure", text))
    wants_technical = bool(re.search(r"trend|technical|price|volatility|liquidity|drawdown|down|setup|chart", text))
    wants_news = bool(re.search(r"news|event|earnings|filing|headline|catalyst|disclosure", text))
    research = base_research_team(universe, wants_technical, wants_news)
    if wants_macro:
        research.append("macro-analyst")
    if wants_instrument:
        research.append("instrument-analyst")
    if wants_approval_execution:
        return {"universe": universe, "lane": "order_intent_or_approval_execution_gate", "subagents": _unique((["macro-analyst"] if wants_macro else []) + (["instrument-analyst"] if wants_instrument else []) + ["portfolio-manager", "risk-manager", "execution-operator"]), "blockedActions": ["natural-language order", "direct broker API", "secret read", "execution without approved artifacts"]}
    if wants_order_draft:
        return {"universe": universe, "lane": "order_intent_draft_gate", "subagents": _unique(research + ["portfolio-manager", "risk-manager"]), "blockedActions": ["approval", "execution", "direct broker API", "secret read"]}
    if wants_portfolio_risk:
        return {"universe": universe, "lane": "portfolio_risk_review", "subagents": _unique((["macro-analyst"] if wants_macro else []) + (["instrument-analyst"] if wants_instrument else []) + (["technical-analyst"] if wants_technical else []) + (["news-analyst"] if wants_news else []) + ["portfolio-manager", "risk-manager"]), "blockedActions": ["order intent", "approval", "execution", "direct broker API", "secret read"]}
    if wants_decision:
        return {"universe": universe, "lane": "thesis_review_then_portfolio_risk_review", "subagents": _unique(research + ["valuation-analyst", "portfolio-manager", "risk-manager"]), "blockedActions": ["order intent", "approval", "execution", "direct broker API", "secret read"]}
    if wants_thesis_review and universe == "public_equity":
        return {"universe": universe, "lane": "thesis_review", "subagents": _unique(research + ["valuation-analyst"]), "blockedActions": ["order intent", "approval", "execution", "direct broker API", "secret read"]}
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
        "Use each role's exact `.codex/agents/*.toml` name as the runtime label.",
        "Preserve the original user request and explicit constraints in every subagent brief.",
        "Do not let head-manager perform substantive investment analysis before subagent outputs exist.",
        "Wait for all selected subagents, then synthesize their outputs with artifact paths, disagreements, missing evidence, and next allowed action.",
        f"Blocked actions before artifacts: {', '.join(plan['blockedActions'])}",
    ])


def strip_negated_action_phrases(text: str) -> str:
    text = re.sub(r"\b(no|do not|don't|dont|without)\s+(account access|order draft|trade execution|trading|trade|trades|orders|order|draft|execution|execute|approval|approve)\b", " ", text)
    text = re.sub(r"\b(no|do not|don't|dont|without)\s+(live trading|live execution|broker access|account|broker|trade execution)\b", " ", text)
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
            "source_x": source_x,
            "source_y": source_y,
            "target_x": target_x,
            "target_y": target_y,
            "mid_y": mid_y,
        })
    return {
        "nodes": nodes,
        "edges": edges,
        "edge_groups": [{"key": key, "label": label} for key, label in EDGE_GROUP_LABELS.items()],
        "systems": get_harness_systems(),
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
            "summary": "Execution-sensitive actions must pass principal, policy, schema, approval, adapter, and audit checks.",
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
                {"label": "Workflow quality", "summary": "Workflow maps, role briefs, quality gates, and readiness labels."},
                {"label": "Research memory", "summary": "DB-backed artifacts, versions, source snapshots, and freshness warnings."},
                {"label": "Skill evolution", "summary": "Skill proposals, review, application, and audit trail."},
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
        "mcp_tools": len(tools),
        "mcp_execution_tools": sum(1 for tool in tools if tool.get("annotations", {}).get("risk_level") == "execution"),
        "policy_blocks": _model_count("apps.policy.models", "PolicyDecision", decision="deny"),
        "restricted_symbols": _model_count("apps.policy.models", "RestrictedSymbol", active=True),
        "workspace_contexts": _model_count("apps.harness.models", "WorkspaceContext"),
        "research_artifacts": _model_count("apps.research.models", "ResearchArtifact"),
        "order_intents": _model_count("apps.orders.models", "OrderIntent"),
        "approval_receipts": _model_count("apps.orders.models", "ApprovalReceipt"),
        "execution_results": _model_count("apps.orders.models", "ExecutionResult"),
        "mcp_calls": _model_count("apps.mcp.models", "McpToolCall"),
    }
    checks = [
        {"label": "Fixed subagent roster", "value": f"{counts['roster']} of 9", "status": "good"},
        {"label": "Repo skills installed", "value": str(counts["skills"]), "status": "good"},
        {"label": "MCP tools visible", "value": str(counts["mcp_tools"]), "status": "good"},
        {"label": "Execution tools", "value": str(counts["mcp_execution_tools"]), "status": "warn"},
        {"label": "Policy blocks", "value": str(counts["policy_blocks"]), "status": "neutral"},
        {"label": "Workspace contexts", "value": str(counts["workspace_contexts"]), "status": "neutral"},
    ]
    return {
        "counts": counts,
        "checks": checks,
        "systems": get_harness_systems(),
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
        ensure_runtime_database(workspace_root)
        from apps.research.models import ResearchArtifact

        role_alias = role.replace("-analyst", "").replace("-manager", "").replace("-operator", "")
        queryset = ResearchArtifact.objects.filter(created_by=role).order_by("-updated_at", "-id")
        if not queryset.exists():
            queryset = ResearchArtifact.objects.filter(metadata__role=role_alias).order_by("-updated_at", "-id")
        return [
            {
                "artifact_id": artifact.artifact_id,
                "title": artifact.title,
                "artifact_type": artifact.artifact_type,
                "universe": artifact.universe,
                "readiness_label": artifact.readiness_label or "unlabeled",
                "updated_at": artifact.updated_at,
            }
            for artifact in queryset[:5]
        ]
    except Exception:
        return []


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


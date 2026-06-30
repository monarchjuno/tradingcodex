from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from django.contrib.admin.views.decorators import staff_member_required
from django.http import Http404
from ninja import NinjaAPI, Router, Schema
from pydantic import ConfigDict, Field

from tradingcodex_service import __version__
from tradingcodex_service.application.components import (
    count_harness_component_tags,
    get_harness_component,
    list_components_by_tag,
    list_harness_components,
)
from tradingcodex_service.application.common import local_or_staff_source
from tradingcodex_service.application.harness import (
    EXPECTED_SUBAGENTS,
    EXPECTED_SKILLS,
    ROLE_SKILL_MAP,
    build_subagent_starter_prompt,
    build_workflow_intake_summary,
    evaluate_artifact_supervisor_loop,
)
from tradingcodex_service.application.agents import (
    build_projection_state,
    create_or_update_optional_skill,
    create_or_update_strategy_skill,
    delete_optional_skill,
    delete_strategy_skill,
    get_optional_skill_record,
    get_strategy_skill_record,
    inspect_agent_configuration,
    list_optional_role_skills,
    list_user_visible_skills,
    read_strategy_skill_records,
    set_optional_skill_status,
    set_strategy_skill_status,
    skills_for_role,
)
from tradingcodex_service.application.brokers import (
    get_broker_connection_status,
    list_broker_connections,
    list_reconciliation_runs,
    sync_broker_account,
)
from tradingcodex_service.application.orders import (
    create_order_ticket,
    get_order_ticket,
    list_order_tickets,
    request_order_approval,
    run_order_checks,
)
from tradingcodex_service.application.policy import simulate_policy as simulate_policy_service
from tradingcodex_service.application.portfolio import list_positions
from tradingcodex_service.application.research import (
    create_research_artifact,
    export_research_artifact_md,
    get_research_artifact,
    list_research_artifacts,
    list_workflow_artifacts,
    record_source_snapshot,
    search_research_artifacts,
)
from tradingcodex_service.application.runtime import (
    ensure_runtime_database,
    persist_workspace_context_if_available,
    tradingcodex_db_path,
    workspace_context_payload,
)
from tradingcodex_service.mcp_runtime import call_mcp_tool, list_mcp_tools, prepare_mcp_runtime


def local_or_staff(request):
    return local_or_staff_source(request, api_key=os.environ.get("TRADINGCODEX_API_KEY"))


api = NinjaAPI(
    title="TradingCodex Service API",
    version=__version__,
    description="Typed control API for TradingCodex workspace, policy, portfolio, audit, and workflow state.",
    docs_decorator=staff_member_required,
    auth=local_or_staff,
)

harness_router = Router()
subagents_router = Router()
policy_router = Router()
orders_router = Router()
approvals_router = Router()
executions_router = Router()
portfolio_router = Router()
brokers_router = Router()
audit_router = Router()
workflows_router = Router()
integrations_router = Router()
research_router = Router()


class PolicyRequest(Schema):
    principal_id: str = "unknown"
    action: str = "unknown"
    resource: str | None = None
    order: dict[str, Any] | None = None
    approval_receipt: dict[str, Any] | None = None
    require_approval_check: bool = False


class ApprovalReceiptPayload(Schema):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=180)
    order_ticket_id: str = Field(min_length=1, max_length=160)
    approved_by: str = Field(min_length=1, max_length=120)
    valid: bool
    expires_at: str = Field(min_length=1, max_length=80)
    created_at: str | None = Field(default=None, min_length=1, max_length=80)
    exact_order_hash: str | None = None
    broker_connection_id: str | None = None
    broker_account_id: str | None = None
    max_notional: float | None = None
    max_price: float | None = None
    max_slippage_bps: int | None = None
    approved_order_type: str | None = None
    approved_time_in_force: str | None = None
    valid_until: str | None = None
    quote_as_of_requirement: str | None = None
    policy_decision: dict[str, Any] | None = None


class ApprovalRequest(Schema):
    principal_id: str = "risk-manager"
    approved_by: str | None = None
    ticket_id: str = Field(min_length=1, max_length=160)
    expires_hours: int = Field(default=24, ge=1, le=168)


class SubmitApprovedRequest(Schema):
    principal_id: str = "execution-operator"
    approval_receipt: ApprovalReceiptPayload | None = None
    ticket_id: str | None = None
    order_ticket_id: str | None = None


class BrokerSyncRequest(Schema):
    principal_id: str = "portfolio-manager"
    broker_id: str = "paper-trading"
    broker_account_id: str | None = None


class OrderTicketRequest(Schema):
    principal_id: str = "portfolio-manager"
    ticket_id: str | None = None
    natural_language: str | None = None
    source: str = "api"
    symbol: str | None = None
    side: str | None = None
    quantity: float | None = None
    order_type: str = "limit"
    limit_price: float | None = None
    stop_price: float | None = None
    time_in_force: str = "day"
    currency: str = "KRW"
    broker_id: str = "paper-trading"
    broker_account_id: str | None = None
    portfolio_id: str | None = None
    account_id: str | None = None
    strategy_id: str | None = None


class OrderTicketActionRequest(Schema):
    principal_id: str = "portfolio-manager"
    ticket_id: str | None = None
    expires_hours: int = Field(default=24, ge=1, le=168)


class OrderTicketApprovalRequest(Schema):
    principal_id: str = "risk-manager"
    approved_by: str | None = None
    ticket_id: str | None = None
    expires_hours: int = Field(default=24, ge=1, le=168)


class WorkflowValidationRequest(Schema):
    original_request: str


class WorkflowLoopRequest(Schema):
    original_request: str = ""
    artifact_paths: list[str]
    record: bool = False


class ResearchArtifactRequest(Schema):
    artifact_id: str | None = None
    artifact_type: str = "research_memo"
    universe: str = "public_equity"
    workflow_type: str = ""
    role: str | None = None
    symbol: str = ""
    title: str
    markdown: str
    metadata: dict[str, Any] | None = None
    source_as_of: str = ""
    readiness_label: str = ""
    context_summary: str = ""
    reader_summary: str = ""
    handoff_state: str = ""
    confidence: str = ""
    missing_evidence: list[Any] | None = None
    next_recipient: str = ""
    next_action: str = ""
    blocked_actions: list[Any] | None = None
    source_snapshot_ids: list[str] | None = None
    follow_up_requests: list[Any] | None = None
    created_by: str = "head-manager"
    export_path: str | None = None


class ResearchSearchRequest(Schema):
    query: str
    universe: str | None = None
    artifact_type: str | None = None
    limit: int = 20


class SourceSnapshotRequest(Schema):
    provider: str
    source_category: str
    as_of: str = ""
    artifact_id: str = ""
    warnings: list[Any] | None = None
    payload: dict[str, Any] | None = None
    principal_id: str = "system"


class StrategySkillRequest(Schema):
    name: str | None = None
    description: str = ""
    body: str = ""
    language: str = "unknown"
    status: str = "draft"
    actor: str = "api"


class OptionalSkillRequest(Schema):
    name: str | None = None
    description: str = ""
    body: str = ""
    status: str = "draft"
    actor: str = "api"


def workspace_root() -> Path:
    return Path(os.environ.get("TRADINGCODEX_WORKSPACE_ROOT", os.getcwd())).resolve()


@api.get("/health", auth=None)
def health(request):
    return {
        "status": "ok",
        "service": "tradingcodex",
        "version": __version__,
        "db_path": str(tradingcodex_db_path()),
        "central_local_service": True,
        "process_scope": os.environ.get("TRADINGCODEX_MCP_SCOPE", "local-service"),
    }


@harness_router.get("/status")
def harness_status(request):
    root = workspace_root()
    prepare_mcp_runtime(root)
    optional_status = list_optional_role_skills(root, include_archived=False)
    return {
        "expected_count": len(EXPECTED_SUBAGENTS),
        "installed_count": len(EXPECTED_SUBAGENTS),
        "fixed_roster_ok": True,
        "skills_installed": len(EXPECTED_SKILLS),
        "core_skills_installed": len(EXPECTED_SKILLS),
        "optional_skills_active": len(optional_status["optional_skills"]),
        "user_visible_skills": list_user_visible_skills(root),
        "subagents": EXPECTED_SUBAGENTS,
        "components_total": len(list_harness_components()),
        "component_tag_counts": count_harness_component_tags(),
        "mcp_tools": [tool["name"] for tool in list_mcp_tools()],
        "db_path": str(tradingcodex_db_path()),
        "workspace_context": persist_workspace_context_if_available(root),
    }


@harness_router.get("/components")
def harness_components(request, tag: str | None = None):
    return {
        "components": list_components_by_tag(tag) if tag else list_harness_components(),
        "component_tag_counts": count_harness_component_tags(),
    }


@harness_router.get("/components/{component_id}")
def harness_component(request, component_id: str):
    component = get_harness_component(component_id)
    if component is None:
        raise Http404(f"Unknown harness component: {component_id}")
    return component


@harness_router.get("/skills")
def harness_skills(request, include_internal: bool = False):
    root = workspace_root()
    return {
        "scope": "all" if include_internal else "user-visible",
        "skills": sorted(build_projection_state(root)["skills"]) if include_internal else list_user_visible_skills(root),
    }


@harness_router.get("/optional-skills")
def harness_optional_skills(request, role: str | None = None, include_archived: bool = True):
    return list_optional_role_skills(workspace_root(), role=role, include_archived=include_archived)


@harness_router.get("/strategies")
def harness_strategies(request, active_only: bool = False):
    return {"strategies": read_strategy_skill_records(workspace_root(), active_only=active_only)}


@harness_router.post("/strategies")
def harness_strategy_create(request, payload: StrategySkillRequest):
    if not payload.name:
        raise ValueError("name is required")
    return create_or_update_strategy_skill(
        workspace_root(),
        payload.name,
        description=payload.description,
        body=payload.body,
        language=payload.language,
        status=payload.status,
        actor=payload.actor,
    )


@harness_router.get("/strategies/{name}")
def harness_strategy_detail(request, name: str):
    return get_strategy_skill_record(workspace_root(), name)


@harness_router.patch("/strategies/{name}")
def harness_strategy_update(request, name: str, payload: StrategySkillRequest):
    return create_or_update_strategy_skill(
        workspace_root(),
        name,
        description=payload.description,
        body=payload.body,
        language=payload.language,
        status=payload.status,
        actor=payload.actor,
    )


@harness_router.delete("/strategies/{name}")
def harness_strategy_delete(request, name: str, force: bool = False):
    return delete_strategy_skill(workspace_root(), name, force=force, actor="api")


@harness_router.post("/strategies/{name}/activate")
def harness_strategy_activate(request, name: str):
    return set_strategy_skill_status(workspace_root(), name, "active", actor="api")


@harness_router.post("/strategies/{name}/archive")
def harness_strategy_archive(request, name: str):
    return set_strategy_skill_status(workspace_root(), name, "archived", actor="api")


def _subagent_records(root: Path) -> list[dict[str, Any]]:
    return [
        {
            "name": role,
            "skills": skills_for_role(root, role),
            "builtin_skills": ROLE_SKILL_MAP[role],
        }
        for role in EXPECTED_SUBAGENTS
    ]


@subagents_router.get("")
def subagents_index(request):
    root = workspace_root()
    return _subagent_records(root)


@subagents_router.get("/{role}/skills")
def subagent_skills(request, role: str):
    root = workspace_root()
    config = inspect_agent_configuration(root, role)
    return {
        "agent": role,
        "skills": skills_for_role(root, role),
        "core_skills": config.get("builtin_skills", []),
        "optional_skills": config.get("optional_skills", []),
        "projected_skills": config.get("projected_skills", []),
        "config": config,
    }


@subagents_router.get("/{role}/optional-skills")
def subagent_optional_skills(request, role: str, include_archived: bool = True):
    return list_optional_role_skills(workspace_root(), role=role, include_archived=include_archived)


@subagents_router.post("/{role}/optional-skills")
def subagent_optional_skill_create(request, role: str, payload: OptionalSkillRequest):
    if not payload.name:
        raise ValueError("name is required")
    return create_or_update_optional_skill(
        workspace_root(),
        role,
        payload.name,
        description=payload.description,
        body=payload.body,
        status=payload.status,
        actor=payload.actor,
    )


@subagents_router.get("/{role}/optional-skills/{name}")
def subagent_optional_skill_detail(request, role: str, name: str):
    return get_optional_skill_record(workspace_root(), role, name)


@subagents_router.patch("/{role}/optional-skills/{name}")
def subagent_optional_skill_update(request, role: str, name: str, payload: OptionalSkillRequest):
    return create_or_update_optional_skill(
        workspace_root(),
        role,
        name,
        description=payload.description,
        body=payload.body,
        status=payload.status,
        actor=payload.actor,
    )


@subagents_router.delete("/{role}/optional-skills/{name}")
def subagent_optional_skill_delete(request, role: str, name: str, force: bool = False):
    return delete_optional_skill(workspace_root(), role, name, force=force, actor="api")


@subagents_router.post("/{role}/optional-skills/{name}/activate")
def subagent_optional_skill_activate(request, role: str, name: str):
    return set_optional_skill_status(workspace_root(), role, name, "active", actor="api")


@subagents_router.post("/{role}/optional-skills/{name}/archive")
def subagent_optional_skill_archive(request, role: str, name: str):
    return set_optional_skill_status(workspace_root(), role, name, "archived", actor="api")


@harness_router.get("/subagents/prompt")
def subagent_prompt(request, q: str):
    root = workspace_root()
    return {"prompt": build_subagent_starter_prompt(q, root), "intake_summary": build_workflow_intake_summary(q, root)}


@harness_router.post("/subagents/loop")
def subagent_loop(request, payload: WorkflowLoopRequest):
    return evaluate_artifact_supervisor_loop(workspace_root(), payload.original_request, payload.artifact_paths, record=payload.record)


@policy_router.post("/simulate")
def simulate_policy(request, payload: PolicyRequest):
    return simulate_policy_service(workspace_root(), payload.dict())


@orders_router.get("/tickets")
def order_tickets(request, limit: int = 30):
    return list_order_tickets(workspace_root(), {"limit": limit})


@orders_router.post("/tickets")
def order_ticket_create(request, payload: OrderTicketRequest):
    return create_order_ticket(workspace_root(), payload.dict())


@orders_router.get("/tickets/{ticket_id}")
def order_ticket_detail(request, ticket_id: str):
    return get_order_ticket(workspace_root(), {"ticket_id": ticket_id})


@orders_router.post("/tickets/{ticket_id}/checks")
def order_ticket_checks(request, ticket_id: str, payload: OrderTicketActionRequest):
    return run_order_checks(workspace_root(), {**payload.dict(), "ticket_id": ticket_id})


@orders_router.post("/tickets/{ticket_id}/approval-request")
def order_ticket_approval_request(request, ticket_id: str, payload: OrderTicketApprovalRequest):
    data = payload.dict()
    return request_order_approval(workspace_root(), {**data, "ticket_id": ticket_id, "approved_by": data.get("approved_by") or data["principal_id"]})


@approvals_router.post("")
def create_approval(request, payload: ApprovalRequest):
    data = payload.dict()
    return request_order_approval(workspace_root(), {**data, "approved_by": data.get("approved_by") or data["principal_id"]})


@executions_router.post("/submit-approved")
def submit_approved(request, payload: SubmitApprovedRequest):
    return call_mcp_tool(workspace_root(), "submit_approved_order", payload.dict())


@portfolio_router.get("/snapshot")
def portfolio_snapshot(request):
    return list_positions(workspace_root())


@portfolio_router.get("/reconciliations")
def portfolio_reconciliations(request, limit: int = 20):
    return list_reconciliation_runs(workspace_root(), {"limit": limit})


@brokers_router.get("")
def brokers_index(request):
    return list_broker_connections(workspace_root())


@brokers_router.get("/{broker_id}")
def broker_detail(request, broker_id: str):
    return get_broker_connection_status(workspace_root(), {"broker_id": broker_id})


@brokers_router.post("/{broker_id}/sync")
def broker_sync(request, broker_id: str, payload: BrokerSyncRequest):
    return sync_broker_account(workspace_root(), {**payload.dict(), "broker_id": broker_id})


@audit_router.get("/events")
def audit_events(request):
    try:
        root = workspace_root()
        ensure_runtime_database(root)
        context = workspace_context_payload(root)
        from apps.audit.models import AuditEvent

        return [
            {
                "created_at": event.created_at.isoformat(),
                "actor_principal": event.actor_principal,
                "source": event.source,
                "action": event.action,
                "decision": event.decision,
                "resource": event.resource,
                "workspace_context": event.workspace_context,
            }
            for event in AuditEvent.objects.filter(workspace_context__workspace_id=context["workspace_id"]).order_by("-created_at", "-id")[:100]
        ]
    except Exception:
        return []


@workflows_router.get("/{workflow_id}")
def workflow_detail(request, workflow_id: str):
    return {"workflow_id": workflow_id, "artifacts": list_workflow_artifacts(workspace_root())["artifacts"]}


@workflows_router.post("/{workflow_id}/validate")
def workflow_validate(request, workflow_id: str, payload: WorkflowValidationRequest):
    return {
        "workflow_id": workflow_id,
        "starter_prompt": build_subagent_starter_prompt(payload.original_request, workspace_root()),
        "intake_summary": build_workflow_intake_summary(payload.original_request, workspace_root()),
    }


@integrations_router.get("/mcp-tools")
def mcp_tools(request):
    prepare_mcp_runtime(workspace_root())
    return {"tools": list_mcp_tools()}


@research_router.post("/artifacts")
def create_research(request, payload: ResearchArtifactRequest):
    return create_research_artifact(workspace_root(), payload.dict())


@research_router.get("/artifacts")
def list_research(request, artifact_type: str | None = None, universe: str | None = None, symbol: str | None = None, limit: int = 50):
    return list_research_artifacts(workspace_root(), {"artifact_type": artifact_type, "universe": universe, "symbol": symbol, "limit": limit})


@research_router.get("/artifacts/{artifact_id}")
def get_research(request, artifact_id: str):
    return get_research_artifact(workspace_root(), {"artifact_id": artifact_id})


@research_router.post("/artifacts/{artifact_id}/export")
def export_research(request, artifact_id: str, export_path: str | None = None):
    return export_research_artifact_md(workspace_root(), {"artifact_id": artifact_id, "export_path": export_path})


@research_router.post("/search")
def search_research(request, payload: ResearchSearchRequest):
    return search_research_artifacts(workspace_root(), payload.dict())


@research_router.post("/source-snapshots")
def create_source_snapshot(request, payload: SourceSnapshotRequest):
    return record_source_snapshot(workspace_root(), payload.dict())


api.add_router("/harness", harness_router)
api.add_router("/subagents", subagents_router)
api.add_router("/policy", policy_router)
api.add_router("/orders", orders_router)
api.add_router("/approvals", approvals_router)
api.add_router("/executions", executions_router)
api.add_router("/portfolio", portfolio_router)
api.add_router("/brokers", brokers_router)
api.add_router("/audit", audit_router)
api.add_router("/workflows", workflows_router)
api.add_router("/integrations", integrations_router)
api.add_router("/research", research_router)

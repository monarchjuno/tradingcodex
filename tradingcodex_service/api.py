from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from django.contrib.admin.views.decorators import staff_member_required
from ninja import NinjaAPI, Router, Schema

from tradingcodex_service import __version__
from tradingcodex_service.application.harness import (
    EXPECTED_SUBAGENTS,
    EXPECTED_SKILLS,
    ROLE_SKILL_MAP,
    USER_VISIBLE_SKILLS,
    build_subagent_starter_prompt,
)
from tradingcodex_service.application.orders import create_approval_receipt, validate_order_intent
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
)
from tradingcodex_service.mcp_runtime import call_mcp_tool as call_tool, list_mcp_tools, prepare_mcp_runtime


def local_or_staff(request):
    if getattr(request, "user", None) and request.user.is_staff:
        return "staff"
    remote_addr = request.META.get("REMOTE_ADDR", "")
    if remote_addr in {"127.0.0.1", "::1", ""}:
        return "local"
    api_key = os.environ.get("TRADINGCODEX_API_KEY")
    if api_key and request.headers.get("X-TradingCodex-Key") == api_key:
        return "api-key"
    return None


api = NinjaAPI(
    title="TradingCodex Service API",
    version=__version__,
    description="Typed control API for TradingCodex harness, policy, portfolio, audit, and workflow state.",
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
audit_router = Router()
workflows_router = Router()
integrations_router = Router()
research_router = Router()


class PolicyRequest(Schema):
    principal_id: str = "unknown"
    action: str = "unknown"
    resource: str | None = None
    order_intent: dict[str, Any] | None = None
    approval_receipt: dict[str, Any] | None = None
    require_approval_check: bool = False


class OrderIntentRequest(Schema):
    principal_id: str = "portfolio-manager"
    order_intent: dict[str, Any]


class ApprovalRequest(Schema):
    approved_by: str = "risk-manager"
    expires_hours: int = 24
    order_intent: dict[str, Any]


class SubmitApprovedRequest(Schema):
    principal_id: str = "execution-operator"
    order_intent: dict[str, Any] | None = None
    approval_receipt: dict[str, Any] | None = None
    order_intent_id: str | None = None


class WorkflowValidationRequest(Schema):
    original_request: str


class ResearchArtifactRequest(Schema):
    artifact_id: str | None = None
    artifact_type: str = "research_memo"
    universe: str = "public_equity"
    workflow_type: str = ""
    symbol: str = ""
    title: str
    markdown: str
    metadata: dict[str, Any] | None = None
    source_as_of: str = ""
    readiness_label: str = ""
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
    prepare_mcp_runtime(workspace_root())
    return {
        "expected_count": len(EXPECTED_SUBAGENTS),
        "installed_count": len(EXPECTED_SUBAGENTS),
        "fixed_roster_ok": True,
        "skills_installed": len(EXPECTED_SKILLS),
        "user_visible_skills": USER_VISIBLE_SKILLS,
        "subagents": EXPECTED_SUBAGENTS,
        "mcp_tools": [tool["name"] for tool in list_mcp_tools()],
        "db_path": str(tradingcodex_db_path()),
        "workspace_context": persist_workspace_context_if_available(workspace_root()),
    }


@harness_router.get("/skills")
def harness_skills(request, include_internal: bool = False):
    return {
        "scope": "all" if include_internal else "user-visible",
        "skills": EXPECTED_SKILLS if include_internal else USER_VISIBLE_SKILLS,
    }


@harness_router.get("/subagents")
def subagents(request):
    return [{"name": role, "skills": ROLE_SKILL_MAP[role]} for role in EXPECTED_SUBAGENTS]


@subagents_router.get("")
def subagents_index(request):
    return subagents(request)


@harness_router.get("/subagents/{role}/skills")
def subagent_skills(request, role: str):
    return {"agent": role, "skills": ROLE_SKILL_MAP.get(role, [])}


@subagents_router.get("/{role}/skills")
def subagent_skills_index(request, role: str):
    return subagent_skills(request, role)


@harness_router.get("/subagents/prompt")
def subagent_prompt(request, q: str):
    return {"prompt": build_subagent_starter_prompt(q)}


@policy_router.post("/simulate")
def simulate_policy(request, payload: PolicyRequest):
    return simulate_policy_service(workspace_root(), payload.dict())


@orders_router.post("/validate-intent")
def validate_intent(request, payload: OrderIntentRequest):
    return validate_order_intent(workspace_root(), payload.dict())


@orders_router.post("/approvals")
def create_approval(request, payload: ApprovalRequest):
    return create_approval_receipt(workspace_root(), payload.order_intent, payload.approved_by, payload.expires_hours)


@approvals_router.post("")
def create_approval_index(request, payload: ApprovalRequest):
    return create_approval(request, payload)


@orders_router.post("/executions/submit-approved")
def submit_approved(request, payload: SubmitApprovedRequest):
    return call_tool(workspace_root(), "submit_approved_order", payload.dict())


@executions_router.post("/submit-approved")
def submit_approved_index(request, payload: SubmitApprovedRequest):
    return submit_approved(request, payload)


@portfolio_router.get("/snapshot")
def portfolio_snapshot(request):
    return list_positions(workspace_root())


@audit_router.get("/events")
def audit_events(request):
    try:
        ensure_runtime_database(workspace_root())
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
            for event in AuditEvent.objects.all()[:100]
        ]
    except Exception:
        return []


@workflows_router.get("/{workflow_id}")
def workflow_detail(request, workflow_id: str):
    return {"workflow_id": workflow_id, "artifacts": list_workflow_artifacts(workspace_root())["artifacts"]}


@workflows_router.post("/{workflow_id}/validate")
def workflow_validate(request, workflow_id: str, payload: WorkflowValidationRequest):
    return {"workflow_id": workflow_id, "starter_prompt": build_subagent_starter_prompt(payload.original_request)}


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
api.add_router("/audit", audit_router)
api.add_router("/workflows", workflows_router)
api.add_router("/integrations", integrations_router)
api.add_router("/research", research_router)

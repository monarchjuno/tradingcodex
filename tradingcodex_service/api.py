from __future__ import annotations

import os
import secrets
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from django.contrib.admin.views.decorators import staff_member_required
from django.conf import settings
from django.http import Http404
from ninja import NinjaAPI, Router, Schema
from ninja.errors import HttpError
from ninja.utils import check_csrf
from pydantic import Field

from tradingcodex_service import __version__
from tradingcodex_service.application.artifact_catalog import list_artifact_catalog
from tradingcodex_service.application.components import (
    count_harness_component_tags,
    get_harness_component,
    list_components_by_tag,
    list_harness_components,
)
from tradingcodex_service.application.common import local_or_staff_source
from tradingcodex_service.application.analysis_runs import begin_analysis_run, read_analysis_run
from tradingcodex_service.application.health import liveness_payload, readiness_payload
from tradingcodex_service.application.agents import (
    EXPECTED_SKILLS,
    EXPECTED_SUBAGENTS,
    ROLE_SKILL_MAP,
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
)
from tradingcodex_service.application.orders import (
    get_order_ticket,
    list_order_tickets,
)
from tradingcodex_service.application.portfolio import list_positions
from tradingcodex_service.application.forecasting import (
    calibration_report,
    get_forecast,
    list_forecasts,
)
from tradingcodex_service.application.research import (
    get_research_artifact,
    list_research_artifacts,
)
from tradingcodex_service.application.research_specs import (
    get_research_spec,
    list_research_specs,
)
from tradingcodex_service.application.runtime import (
    ensure_runtime_database,
    persist_workspace_context_if_available,
    tradingcodex_db_path,
    workspace_context_payload,
)
from tradingcodex_service.application.workspaces import WorkspaceSelectionError, bind_request_workspace, current_workspace_root
from tradingcodex_service.mcp_runtime import call_mcp_tool, list_mcp_tools, prepare_mcp_runtime
from tradingcodex_service.runtime_profile import LOCAL_PROFILE


def local_or_staff(request):
    api_key = os.environ.get("TRADINGCODEX_API_KEY")
    api_key_principal = os.environ.get("TRADINGCODEX_API_PRINCIPAL")
    supplied_key = request.headers.get("X-TradingCodex-Key", "")
    if api_key and api_key_principal and supplied_key and secrets.compare_digest(supplied_key, api_key):
        source = f"principal:{api_key_principal}"
        request._tradingcodex_auth_source = "api-key"
    elif getattr(getattr(request, "user", None), "is_staff", False):
        if check_csrf(request):
            raise HttpError(403, "CSRF check failed")
        source = f"principal:{request.user.username}"
        request._tradingcodex_auth_source = "staff-session"
    else:
        source = local_or_staff_source(
            request,
            allow_local_readonly=settings.SERVICE_PROFILE == LOCAL_PROFILE,
        )
        request._tradingcodex_auth_source = source or "anonymous"
    if source:
        try:
            bind_request_workspace(request)
        except WorkspaceSelectionError as exc:
            raise HttpError(404, str(exc)) from exc
    return source


def mutation_principal(request) -> str:
    authenticated = str(getattr(request, "auth", "") or "")
    if not authenticated.startswith("principal:") or not authenticated.removeprefix("principal:"):
        raise HttpError(403, "an authenticated mutation principal is required")
    return authenticated.removeprefix("principal:")


def _admin_mutation_principal(request) -> str:
    principal_id = mutation_principal(request)
    if getattr(request, "_tradingcodex_auth_source", "") == "staff-session":
        return principal_id
    prepare_mcp_runtime(workspace_root())
    from apps.policy.models import Principal

    principal = Principal.objects.filter(principal_id=principal_id, active=True).first()
    if principal is None or principal.role != "head-manager":
        raise HttpError(403, "this administrative mutation requires staff or an active head-manager principal")
    return principal_id


def _call_mutation_tool(request, name: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
    if getattr(request, "_tradingcodex_auth_source", "") == "staff-session":
        raise HttpError(403, "role-authored mutations require an API-key-bound principal")
    try:
        return call_mcp_tool(
            workspace_root(),
            name,
            args or {},
            transport_principal=mutation_principal(request),
        )
    except PermissionError as exc:
        raise HttpError(403, str(exc)) from exc
    except ValueError as exc:
        raise HttpError(400, str(exc)) from exc


def _payload(payload: Schema, *, exclude: set[str] | None = None) -> dict[str, Any]:
    return payload.model_dump(exclude_none=True, exclude=exclude or set())


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
portfolio_router = Router()
brokers_router = Router()
audit_router = Router()
workflows_router = Router()
integrations_router = Router()
research_router = Router()
evaluations_router = Router()


class PolicyRequest(Schema):
    principal_id: str = "unknown"
    action: str = "unknown"
    resource: str | None = None
    order: dict[str, Any] | None = None


class ApprovalRequest(Schema):
    ticket_id: str = Field(min_length=1, max_length=160)
    expires_hours: int = Field(default=24, ge=1, le=168)


class BrokerSyncRequest(Schema):
    broker_account_id: str | None = None


class OrderTicketRequest(Schema):
    ticket_id: str = Field(min_length=1, max_length=160)
    natural_language: str | None = None
    source: str = "api"
    symbol: str | None = None
    side: str | None = None
    quantity: Decimal | None = None
    order_type: str = "limit"
    limit_price: Decimal | None = None
    stop_price: Decimal | None = None
    time_in_force: str = "day"
    currency: str | None = None
    base_currency: str | None = None
    fx_rate: Decimal | None = None
    fx_source_snapshot_id: str | None = None
    fx_as_of: str | None = None
    broker_id: str = "paper-trading"
    broker_account_id: str | None = None
    portfolio_id: str | None = None
    account_id: str | None = None
    strategy_id: str | None = None


class OrderTicketApprovalRequest(Schema):
    expires_hours: int = Field(default=24, ge=1, le=168)


class AnalysisRunRequest(Schema):
    request: str = Field(min_length=1, max_length=20000)
    workflow_run_id: str | None = Field(default=None, max_length=180)


class ResearchArtifactRequest(Schema):
    artifact_id: str | None = None
    artifact_type: str = "research_memo"
    universe: str = ""
    workflow_type: str = ""
    workflow_run_id: str = ""
    input_artifact_ids: list[str] = Field(default_factory=list)
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
    evidence_lane: Literal["historical_replay", "historical_holdout", "live_forward"] | None = None
    research_spec_id: str = ""
    replay_manifest_id: str = ""
    decision_snapshot_id: str = ""
    strategy_name: str = ""
    strategy_hash: str = ""
    investor_context_applied: bool | None = None
    investor_context_hash: str = ""
    decision_memory_consulted: bool | None = None
    decision_memory_cutoff: str = ""
    forecast_required: bool | None = None
    decision_quality_required: bool | None = None
    investor_context_gate_required: bool | None = None
    anti_overfit_required: bool | None = None
    anti_overfit_checks: dict[str, Any] | None = None
    forecast_allowed: bool | None = None
    forecast_block_reason: str = ""
    forecast_target: str = ""
    forecast_horizon: str = ""
    probability: Any = None
    probability_range: Any = None
    base_rate: Any = None
    missing_base_rate_note: str = ""
    evidence_ids: list[Any] | None = None
    contrary_evidence: list[Any] | None = None
    resolution_source: str = ""
    review_date: str = ""
    update_triggers: list[Any] | None = None
    invalidation_conditions: list[Any] | None = None
    source_trust_notes: list[Any] | None = None
    scenario_cases: list[Any] | None = None
    scenario_summary: str = ""
    thesis_lifecycle: dict[str, Any] | None = None
    current_price_as_of: str = ""
    market_anchor_as_of: str = ""
    investor_context_gaps: list[Any] | None = None
    producer_role: str = ""
    knowledge_cutoff: str = ""
    follow_up_requests: list[Any] | None = None
    improvements: list[Any] | None = None
    export_path: str | None = None


class ResearchSearchRequest(Schema):
    query: str
    universe: str | None = None
    artifact_type: str | None = None
    limit: int = 20


class ArtifactCatalogSearchRequest(Schema):
    query: str
    universe: str | None = None
    artifact_type: str | None = None
    symbol: str | None = None
    workflow_run_id: str | None = None
    readiness_label: str | None = None
    handoff_state: str | None = None
    compatibility: str | None = None
    knowledge_cutoff: str | None = None
    limit: int = 20


class SourceSnapshotRequest(Schema):
    """Record source evidence while the service owns receipt times and snapshot id."""

    provider: str
    source_category: str
    source_locator: str | None = None
    provider_query: dict[str, Any] | None = None
    as_of: str = ""
    observed_at: str = ""
    effective_at: str = ""
    published_at: str = ""
    retrieved_at: str | None = Field(
        default=None,
        description=(
            "Service-owned by default; omit so TradingCodex records receipt "
            "time. An explicit value must be a truthful timezone-aware "
            "ISO-8601 datetime."
        ),
    )
    known_at: str | None = Field(
        default=None,
        description=(
            "Provide only when the evidence's actual knowable time is genuinely "
            "known and timezone-qualified; otherwise omit it."
        ),
    )
    recorded_at: str | None = Field(
        default=None,
        description=(
            "Service-owned by default; omit so TradingCodex records storage "
            "time. Explicit values remain strictly validated."
        ),
    )
    revision: str = "not_applicable"
    vintage: str = "not_applicable"
    timezone: str = "UTC"
    schema_hash: str | None = None
    corporate_action_policy: str = "not_specified"
    price_adjustment_policy: str = "not_specified"
    universe_membership: dict[str, Any] | None = None
    delisting_policy: str = "not_specified"
    coverage_note: str = "coverage and licensing not specified"
    artifact_id: str = ""
    warnings: list[Any] | None = None
    payload: dict[str, Any] | None = None


class ResearchSpecRequest(Schema):
    spec_id: str | None = None
    created_at: str | None = None
    knowledge_cutoff: str
    evidence_lane: Literal["historical_replay", "historical_holdout", "live_forward"] | None = None
    parent_spec_id: str | None = None
    method_profile: Literal[
        "general_evidence_v1",
        "event_research_v1",
        "quant_signal_v1",
        "listed_equity_fcff_dcf_v1",
    ]
    hypothesis: str
    economic_mechanism: str
    research_type: str | None = None
    instrument: str | None = None
    universe: str
    universe_membership_rule: str
    target: str
    horizon: str
    benchmark: str | None = None
    holding_period: str | None = None
    rebalance_rule: str | None = None
    signal_definition: dict[str, Any] | None = None
    falsification_criteria: list[Any]
    validation_plan: dict[str, Any]
    parameter_trial_budget: int | None = Field(default=None, ge=1)
    cost_assumptions: dict[str, Any] | None = None
    capacity_assumptions: dict[str, Any] | None = None
    resolution_rule: str
    causal_analysis_required: bool | None = None
    driver_tree: dict[str, Any] | None = None
    base_rate_cohort: dict[str, Any] | None = None
    implied_expectations_plan: dict[str, Any] | None = None
    scenario_plan: dict[str, Any] | None = None
    method_reconciliation_plan: dict[str, Any] | None = None
    independent_review_plan: dict[str, Any] | None = None


class ReplayManifestRequest(Schema):
    manifest_id: str | None = None
    spec_id: str
    source_snapshot_ids: list[str]
    created_at: str | None = None


class ExperimentRunRequest(Schema):
    run_id: str | None = None
    spec_id: str
    replay_manifest_id: str
    created_at: str | None = None
    code_hash: str
    data_hash: str
    config_hash: str
    model: str = ""
    reasoning_effort: str = ""
    prompt_hash: str = ""
    tool_profile_hash: str = ""
    splits: dict[str, Any]
    trial_count: int = Field(default=1, ge=1)
    metrics: dict[str, Any]
    checks: dict[str, Any]
    conclusion: str
    source_limitations: list[Any] | None = None


class ForecastIssueRequest(Schema):
    forecast_id: str | None = None
    workflow_run_id: str = ""
    artifact_id: str
    artifact_path: str = ""
    research_spec_id: str = ""
    replay_manifest_id: str = ""
    evidence_lane: Literal["historical_replay", "historical_holdout", "live_forward"] | None = None
    role: str = ""
    instrument: str = ""
    universe: str = ""
    regime: str = "unclassified"
    forecast_target: str
    target_type: str = "binary"
    unit: str = ""
    benchmark: str = ""
    horizon: str
    issued_at: str | None = None
    knowledge_cutoff: str
    probability: float | None = None
    probability_range: list[float] | str | None = None
    probabilities: dict[str, float] | None = None
    prediction: float | None = None
    interval: dict[str, float] | None = None
    quantiles: dict[str, float] | None = None
    base_rate: dict[str, Any]
    evidence_ids: list[Any]
    contrary_evidence: list[Any]
    invalidation_conditions: list[Any]
    update_triggers: list[Any]
    resolution_rule: str
    resolution_source: str = ""
    review_date: str = ""
    model: str = ""
    reasoning_effort: str = ""
    prompt_hash: str = ""
    tool_profile_hash: str = ""
    config_hash: str = ""
    idempotency_key: str | None = None


class ForecastRevisionRequest(Schema):
    revision_reason: str
    revised_at: str | None = None
    knowledge_cutoff: str | None = None
    probability: float | None = None
    probability_range: list[float] | str | None = None
    probabilities: dict[str, float] | None = None
    prediction: float | None = None
    interval: dict[str, float] | None = None
    quantiles: dict[str, float] | None = None
    base_rate: dict[str, Any] | None = None
    evidence_ids: list[Any] | None = None
    contrary_evidence: list[Any] | None = None
    invalidation_conditions: list[Any] | None = None
    update_triggers: list[Any] | None = None
    regime: str | None = None
    model: str | None = None
    reasoning_effort: str | None = None
    prompt_hash: str | None = None
    tool_profile_hash: str | None = None
    config_hash: str | None = None
    idempotency_key: str | None = None


class ForecastResolutionRequest(Schema):
    outcome: Any
    resolution_source_snapshot_id: str
    resolved_at: str | None = None
    observed_at: str | None = None
    resolution_note: str = ""
    dispute_state: str = "undisputed"
    resolve_dispute: bool = False
    idempotency_key: str | None = None


class CausalEquityAnalysisRequest(Schema):
    analysis_id: str | None = None
    spec_id: str
    replay_manifest_id: str
    analysis_input_snapshot_id: str
    prior_id: str


class BlindJudgmentPriorRequest(Schema):
    prior_id: str | None = None
    spec_id: str
    specification_view: str
    evidence_quality_view: str
    key_driver_view: list[Any]
    falsifiers: list[Any]


class JudgmentReviewRequest(Schema):
    review_id: str | None = None
    prior_id: str
    analysis_id: str
    conclusion: str
    changed_views: list[Any] | None = None
    remaining_disagreements: list[Any]
    acceptance: str = "revise"


class EvaluationCorpusRequest(Schema):
    corpus_id: str | None = None
    evaluation_profile: str = "core_investment_v1"
    required_case_tags: list[str] | None = None
    metric_dimensions: list[str] | None = None
    cases: list[dict[str, Any]]
    promotion_criteria: list[dict[str, Any]]
    minimum_blind_reviews: int = Field(default=2, ge=2)


class EvaluationRunRequest(Schema):
    run_id: str | None = None
    corpus_id: str
    arm: str
    model: str
    reasoning_effort: str
    prompt_hash: str
    config_hash: str
    tool_profile_hash: str
    deterministic_calculation_hash: str
    extension_profile_hash: str
    case_results: list[dict[str, Any]]
    metrics: dict[str, Any] | None = None
    operations: dict[str, Any]


class BlindReviewAssignmentRequest(Schema):
    assignment_id: str | None = None
    control_run_id: str
    candidate_run_id: str
    reviewer_principal: str


class BlindHumanReviewRequest(Schema):
    review_id: str | None = None
    assignment_id: str
    preference: str
    ratings: dict[str, Any]
    rationale: str


class EvaluationComparisonRequest(Schema):
    comparison_id: str | None = None
    control_run_id: str
    candidate_run_id: str


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
    return current_workspace_root()


@api.get("/health", auth=None)
def health(request):
    payload = readiness_payload()
    return {**payload, "status": "ok" if payload["ready"] else "not_ready"}


@api.get("/health/live", auth=None)
def health_live(request):
    return liveness_payload()


@api.get("/health/ready", auth=None)
def health_ready(request):
    payload = readiness_payload()
    return api.create_response(request, payload, status=200 if payload["ready"] else 503)


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
        actor=_admin_mutation_principal(request),
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
        actor=_admin_mutation_principal(request),
    )


@harness_router.delete("/strategies/{name}")
def harness_strategy_delete(request, name: str, force: bool = False):
    return delete_strategy_skill(workspace_root(), name, force=force, actor=_admin_mutation_principal(request))


@harness_router.post("/strategies/{name}/activate")
def harness_strategy_activate(request, name: str):
    return set_strategy_skill_status(workspace_root(), name, "active", actor=_admin_mutation_principal(request))


@harness_router.post("/strategies/{name}/archive")
def harness_strategy_archive(request, name: str):
    return set_strategy_skill_status(workspace_root(), name, "archived", actor=_admin_mutation_principal(request))


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
        actor=_admin_mutation_principal(request),
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
        actor=_admin_mutation_principal(request),
    )


@subagents_router.delete("/{role}/optional-skills/{name}")
def subagent_optional_skill_delete(request, role: str, name: str, force: bool = False):
    return delete_optional_skill(workspace_root(), role, name, force=force, actor=_admin_mutation_principal(request))


@subagents_router.post("/{role}/optional-skills/{name}/activate")
def subagent_optional_skill_activate(request, role: str, name: str):
    return set_optional_skill_status(workspace_root(), role, name, "active", actor=_admin_mutation_principal(request))


@subagents_router.post("/{role}/optional-skills/{name}/archive")
def subagent_optional_skill_archive(request, role: str, name: str):
    return set_optional_skill_status(workspace_root(), role, name, "archived", actor=_admin_mutation_principal(request))


@harness_router.get("/subagents/prompt")
def subagent_prompt(request, q: str):
    return {
        "request": q,
        "orchestration": "codex_native",
        "instruction": "Head Manager interprets the request directly and dynamically chooses fixed roles through $tcx-workflow.",
    }


@policy_router.post("/simulate")
def simulate_policy(request, payload: PolicyRequest):
    data = _payload(payload)
    if payload.principal_id == "unknown":
        data.pop("principal_id", None)
    return _call_mutation_tool(request, "simulate_policy", data)


@orders_router.get("/tickets")
def order_tickets(request, limit: int = 30):
    return list_order_tickets(workspace_root(), {"limit": limit})


@orders_router.post("/tickets")
def order_ticket_create(request, payload: OrderTicketRequest):
    return _call_mutation_tool(request, "create_order_ticket", _payload(payload))


@orders_router.get("/tickets/{ticket_id}")
def order_ticket_detail(request, ticket_id: str):
    return get_order_ticket(workspace_root(), {"ticket_id": ticket_id})


@orders_router.post("/tickets/{ticket_id}/checks")
def order_ticket_checks(request, ticket_id: str):
    return _call_mutation_tool(request, "run_order_checks", {"ticket_id": ticket_id})


@orders_router.post("/tickets/{ticket_id}/approval-request")
def order_ticket_approval_request(request, ticket_id: str, payload: OrderTicketApprovalRequest):
    return _call_mutation_tool(
        request,
        "request_order_approval",
        {**_payload(payload), "ticket_id": ticket_id},
    )


@orders_router.post("/tickets/{ticket_id}/discard")
def order_ticket_discard(request, ticket_id: str):
    return _call_mutation_tool(request, "discard_draft_order", {"ticket_id": ticket_id})


@approvals_router.post("")
def create_approval(request, payload: ApprovalRequest):
    return _call_mutation_tool(request, "request_order_approval", _payload(payload))


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
    return _call_mutation_tool(
        request,
        "sync_broker_account",
        {**_payload(payload), "broker_id": broker_id},
    )


@audit_router.get("/events")
def audit_events(request):
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


@workflows_router.post("")
def analysis_run_begin(request, payload: AnalysisRunRequest):
    _admin_mutation_principal(request)
    return begin_analysis_run(
        workspace_root(),
        payload.request,
        run_id=payload.workflow_run_id or "",
    )


@workflows_router.get("/{workflow_id}")
def workflow_detail(request, workflow_id: str):
    root = workspace_root()
    run = read_analysis_run(root, workflow_id)
    if not run:
        raise Http404("analysis run not found")
    return {
        "workflow_id": workflow_id,
        "run": run,
        "artifacts": list_research_artifacts(root, {"workflow_run_id": workflow_id, "limit": 200})["artifacts"],
    }


@integrations_router.get("/mcp-tools")
def mcp_tools(request):
    prepare_mcp_runtime(workspace_root())
    return {"tools": list_mcp_tools()}


@research_router.post("/artifacts")
def create_research(request, payload: ResearchArtifactRequest):
    data = _payload(payload)
    for field in ("handoff_state", "workflow_run_id"):
        if not data.get(field):
            data.pop(field, None)
    return _call_mutation_tool(
        request,
        "create_research_artifact",
        data,
    )


@research_router.get("/artifacts")
def list_research(request, artifact_type: str | None = None, universe: str | None = None, symbol: str | None = None, limit: int = 50):
    return list_research_artifacts(workspace_root(), {"artifact_type": artifact_type, "universe": universe, "symbol": symbol, "limit": limit})


@research_router.get("/artifacts/{artifact_id}")
def get_research(request, artifact_id: str):
    return get_research_artifact(workspace_root(), {"artifact_id": artifact_id})


@research_router.post("/artifacts/{artifact_id}/export")
def export_research(request, artifact_id: str, export_path: str | None = None):
    return _call_mutation_tool(
        request,
        "export_research_artifact_md",
        {"artifact_id": artifact_id, **({"export_path": export_path} if export_path else {})},
    )


@research_router.post("/search")
def search_research(request, payload: ResearchSearchRequest):
    return _call_mutation_tool(request, "search_research_artifacts", _payload(payload))


@research_router.get("/catalog")
def list_catalog(
    request,
    artifact_type: str | None = None,
    universe: str | None = None,
    symbol: str | None = None,
    workflow_run_id: str | None = None,
    compatibility: str | None = None,
    knowledge_cutoff: str | None = None,
    limit: int = 100,
):
    return list_artifact_catalog(
        workspace_root(),
        {
            "artifact_type": artifact_type,
            "universe": universe,
            "symbol": symbol,
            "workflow_run_id": workflow_run_id,
            "compatibility": compatibility,
            "knowledge_cutoff": knowledge_cutoff,
            "limit": limit,
        },
    )


@research_router.post("/catalog/search")
def search_catalog(request, payload: ArtifactCatalogSearchRequest):
    return _call_mutation_tool(request, "search_artifact_catalog", _payload(payload))


@research_router.post("/source-snapshots")
def create_source_snapshot(request, payload: SourceSnapshotRequest):
    return _call_mutation_tool(request, "record_source_snapshot", _payload(payload))


@research_router.post("/specs")
def create_spec(request, payload: ResearchSpecRequest):
    return _call_mutation_tool(request, "create_research_spec", _payload(payload))


@research_router.get("/specs")
def list_specs(request):
    return list_research_specs(workspace_root())


@research_router.get("/specs/{spec_id}")
def get_spec(request, spec_id: str):
    return get_research_spec(workspace_root(), {"spec_id": spec_id})


@research_router.post("/replay-manifests")
def create_replay(request, payload: ReplayManifestRequest):
    return _call_mutation_tool(request, "create_replay_manifest", _payload(payload))


@research_router.post("/experiments")
def create_experiment(request, payload: ExperimentRunRequest):
    return _call_mutation_tool(request, "record_experiment_run", _payload(payload))


@research_router.post("/causal-equity-analyses")
def create_causal_analysis(request, payload: CausalEquityAnalysisRequest):
    return _call_mutation_tool(request, "create_causal_equity_analysis", _payload(payload))


@research_router.post("/judgment-priors")
def create_judgment_prior(request, payload: BlindJudgmentPriorRequest):
    return _call_mutation_tool(request, "record_blind_judgment_prior", _payload(payload))


@research_router.post("/judgment-reviews")
def create_judgment_review(request, payload: JudgmentReviewRequest):
    return _call_mutation_tool(request, "complete_judgment_review", _payload(payload))


@research_router.post("/index/rebuild")
def rebuild_index(request):
    return _call_mutation_tool(request, "rebuild_research_index")


@research_router.post("/catalog/rebuild")
def rebuild_catalog(request):
    return _call_mutation_tool(request, "rebuild_artifact_catalog")


@research_router.post("/forecasts")
def create_forecast(request, payload: ForecastIssueRequest):
    return _call_mutation_tool(request, "issue_forecast", _payload(payload, exclude={"role"}))


@research_router.get("/forecasts")
def forecast_list(request, status: str | None = None, role: str | None = None, evidence_lane: str | None = None, limit: int = 100):
    return list_forecasts(workspace_root(), {"status": status, "role": role, "evidence_lane": evidence_lane, "limit": limit})


@research_router.get("/forecasts/calibration")
def forecast_calibration(request, minimum_sample: int = 20, evidence_lane: str = "live_forward"):
    return calibration_report(workspace_root(), {"minimum_sample": minimum_sample, "evidence_lane": evidence_lane})


@research_router.get("/forecasts/{forecast_id}")
def forecast_detail(request, forecast_id: str, include_history: bool = True):
    return get_forecast(workspace_root(), {"forecast_id": forecast_id, "include_history": include_history})


@research_router.post("/forecasts/{forecast_id}/revisions")
def forecast_revision(request, forecast_id: str, payload: ForecastRevisionRequest):
    return _call_mutation_tool(
        request,
        "revise_forecast",
        {**_payload(payload), "forecast_id": forecast_id},
    )


@research_router.post("/forecasts/{forecast_id}/resolution")
def forecast_resolution(request, forecast_id: str, payload: ForecastResolutionRequest):
    return _call_mutation_tool(
        request,
        "resolve_forecast",
        {**_payload(payload), "forecast_id": forecast_id},
    )


@research_router.post("/forecasts/{forecast_id}/score")
def forecast_score(request, forecast_id: str, idempotency_key: str | None = None):
    return _call_mutation_tool(
        request,
        "score_forecast",
        {"forecast_id": forecast_id, **({"idempotency_key": idempotency_key} if idempotency_key else {})},
    )


@evaluations_router.post("/corpora")
def create_evaluation_corpus_api(request, payload: EvaluationCorpusRequest):
    return _call_mutation_tool(request, "create_evaluation_corpus", _payload(payload))


@evaluations_router.post("/runs")
def create_evaluation_run_api(request, payload: EvaluationRunRequest):
    return _call_mutation_tool(request, "record_evaluation_run", _payload(payload))


@evaluations_router.post("/blind-review-assignments")
def create_blind_review_assignment_api(request, payload: BlindReviewAssignmentRequest):
    return _call_mutation_tool(request, "create_blind_review_assignment", _payload(payload))


@evaluations_router.get("/blind-review-assignments/{assignment_id}")
def get_blind_review_packet_api(request, assignment_id: str):
    return _call_mutation_tool(request, "get_blind_review_packet", {"assignment_id": assignment_id})


@evaluations_router.post("/blind-reviews")
def create_blind_human_review_api(request, payload: BlindHumanReviewRequest):
    return _call_mutation_tool(request, "record_blind_human_review", _payload(payload))


@evaluations_router.post("/comparisons")
def create_evaluation_comparison_api(request, payload: EvaluationComparisonRequest):
    return _call_mutation_tool(request, "compare_evaluation_runs", _payload(payload))


api.add_router("/harness", harness_router)
api.add_router("/subagents", subagents_router)
api.add_router("/policy", policy_router)
api.add_router("/orders", orders_router)
api.add_router("/approvals", approvals_router)
api.add_router("/portfolio", portfolio_router)
api.add_router("/brokers", brokers_router)
api.add_router("/audit", audit_router)
api.add_router("/workflows", workflows_router)
api.add_router("/integrations", integrations_router)
api.add_router("/research", research_router)
api.add_router("/evaluations", evaluations_router)

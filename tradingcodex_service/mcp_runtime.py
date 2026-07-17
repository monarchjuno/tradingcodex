from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import urlsplit

from tradingcodex_service.application.agents import AGENT_SPECS
from tradingcodex_service.application.artifact_quality import (
    ANTI_OVERFIT_CHECK_KEYS,
    FOLLOW_UP_CONSENT_POSTURE,
    FOLLOW_UP_MATERIALITY,
    FOLLOW_UP_ROLES,
    FOLLOW_UP_TRIGGERS,
    IMPROVEMENT_TYPES,
)
from tradingcodex_service.application.build_gateway import (
    BUILD_PROTECTED_MCP_TOOLS,
    WORKSPACE_PROTECTED_MCP_TOOLS,
    WORKSPACE_PROTECTED_MCP_TOOL_SCOPES,
    begin_reserved_build_turn_use,
    fail_closed_finalize_started_build_turn_use,
    finish_reserved_build_turn_use,
)


SAFE_HOME_TOOL_NAMES = frozenset({
    "get_tradingcodex_status",
    "get_update_status",
    "get_connector_build_status",
    "list_broker_connections",
    "get_broker_connection_status",
    "get_order_status",
    "get_order_ticket",
    "list_order_tickets",
    "get_positions",
    "get_portfolio_snapshot",
    "list_reconciliation_runs",
    "list_workflow_artifacts",
    "get_research_artifact",
    "list_research_artifacts",
    "search_research_artifacts",
    "list_artifact_catalog",
    "search_artifact_catalog",
    "search_datasets",
    "get_dataset_manifest",
    "profile_dataset",
    "search_calculations",
    "get_calculation_run",
    "compare_calculation_runs",
    "get_research_spec",
    "list_research_specs",
    "get_forecast",
    "list_forecasts",
    "get_forecast_calibration_report",
})
REGISTRY_FAILURE_SAFE_READ_TOOLS = frozenset({
    "get_tradingcodex_status",
    "get_update_status",
    "list_workflow_artifacts",
    "get_research_artifact",
    "list_research_artifacts",
    "search_research_artifacts",
    "list_artifact_catalog",
    "search_artifact_catalog",
    "search_datasets",
    "get_dataset_manifest",
    "profile_dataset",
    "search_calculations",
    "get_calculation_run",
    "compare_calculation_runs",
})
RETIRED_PUBLIC_MCP_TOOLS = frozenset(
    {
        "connect_broker_connector",
        "scaffold_broker_connector",
        "submit_approved_order",
        "cancel_submitted_order",
        "refresh_broker_order_status",
    }
)
ORDER_TURN_GRANT_TOOL = "use_order_turn_grant"
_ORDER_TURN_GRANT_PROOF_FIELD = "_execution_turn_proof"
_BUILD_TURN_PROOF_FIELD = "_build_turn_proof"


def roles_with_mcp_tool(tool_name: str) -> frozenset[str]:
    return frozenset(role for role, spec in AGENT_SPECS.items() if tool_name in spec.mcp_allowlist)


@dataclass(frozen=True)
class McpToolSpec:
    name: str
    description: str
    category: str
    risk_level: str
    allowed_roles: frozenset[str]
    handler_name: str
    input_schema: dict[str, Any]
    capability_required: str = ""
    requires_approval: bool = False
    audit_required: bool = True
    experimental: bool = False

    def public_definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
            "annotations": {
                "title": self.name.replace("_", " ").title(),
                "readOnlyHint": self.risk_level == "read",
                "destructiveHint": self._destructive_hint(),
                "idempotentHint": self._idempotent_hint(),
                "openWorldHint": self._open_world_hint(),
                "category": self.category,
                "risk_level": self.risk_level,
                "requires_approval": self.requires_approval,
                "audit_required": self.audit_required,
                "allowed_roles": sorted(self.allowed_roles),
                "experimental": self.experimental,
                "requires_build_turn": self.name in BUILD_PROTECTED_MCP_TOOLS,
                "requires_workspace_turn": self.name in WORKSPACE_PROTECTED_MCP_TOOLS,
                "workspace_turn_scope": WORKSPACE_PROTECTED_MCP_TOOL_SCOPES.get(self.name, ""),
            },
        }

    def _destructive_hint(self) -> bool:
        return self.risk_level == "execution" or self.name.startswith("cancel_")

    def _idempotent_hint(self) -> bool:
        return self.risk_level == "read" or self.name in {
            "simulate_policy",
            "validate_approval_receipt",
            "run_order_checks",
            "get_broker_instrument_constraints",
            "preview_order_translation",
        }

    def _open_world_hint(self) -> bool:
        return self.category in {"brokers", "execution"}


def json_object_schema(
    properties: dict[str, Any] | None = None,
    required: list[str] | None = None,
    *,
    additional_properties: bool = True,
) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties or {},
        "required": required or [],
        "additionalProperties": additional_properties,
    }


def object_schema(
    properties: dict[str, Any] | None = None,
    required: list[str] | None = None,
    *,
    additional_properties: bool = True,
) -> dict[str, Any]:
    merged = {"principal_id": {"type": "string", "minLength": 1, "maxLength": 120}}
    merged.update(properties or {})
    return json_object_schema(merged, required, additional_properties=additional_properties)


ANTI_OVERFIT_CHECK_SCHEMA = json_object_schema(
    {
        "status": {"type": "string", "enum": ["pass", "fail", "not_applicable"]},
        "reason": {"type": "string", "minLength": 1},
        "evidence_refs": {
            "type": "array",
            "items": {"type": "string", "minLength": 1},
        },
    },
    ["status", "reason", "evidence_refs"],
    additional_properties=False,
)
ANTI_OVERFIT_CHECKS_SCHEMA = json_object_schema(
    {key: ANTI_OVERFIT_CHECK_SCHEMA for key in ANTI_OVERFIT_CHECK_KEYS},
    list(ANTI_OVERFIT_CHECK_KEYS),
    additional_properties=False,
)
STRING_LIST_SCHEMA = {
    "type": "array",
    "items": {"type": "string", "minLength": 1},
}
PROBABILITY_RANGE_SCHEMA = {
    "type": ["array", "string"],
    "description": (
        "One lower/upper probability range only, either a two-number array such as [0.3, 0.4] "
        "or a string such as '30-40%'. Put multiple scenario ranges in scenario_cases instead."
    ),
    "items": {"type": "number", "minimum": 0, "maximum": 1},
    "minItems": 2,
    "maxItems": 2,
}
THESIS_LIFECYCLE_SCHEMA = json_object_schema(
    {
        "state": {
            "type": "string",
            "enum": ["exploring", "testing", "validated", "rejected", "monitoring"],
            "description": "Current thesis state; state-specific evidence is required by the artifact quality gate.",
        },
        "evidence_refs": STRING_LIST_SCHEMA,
        "evidence_run_card": {"type": ["object", "string"]},
        "evidence_run_cards": {"type": "array"},
        "validation_card": {"type": ["object", "string"]},
        "validation_cards": {"type": "array"},
        "reviewer_acceptance": {"type": ["object", "string"]},
        "invalidation_note": {"type": "string", "minLength": 1},
        "monitoring_artifact": {"type": ["object", "string"]},
        "review_cadence": {"type": "string", "minLength": 1},
    },
    ["state"],
    additional_properties=True,
)
FORECAST_BASE_RATE_SCHEMA = json_object_schema(
    {
        "cohort": {"type": "string", "minLength": 1},
        "source_snapshot_id": {"type": "string", "minLength": 1},
        "sample_size": {"type": "integer", "minimum": 1},
        "selection_rule": {"type": "string", "minLength": 1},
        "as_of": {
            "type": "string",
            "format": "date-time",
            "description": "Optional RFC 3339 base-rate timestamp at or before knowledge_cutoff.",
        },
        "value": {"type": "number"},
        "probabilities": {
            "type": "object",
            "description": "Required for categorical targets; keys must match forecast probabilities and values must sum to 1.",
        },
        "prediction": {"type": "number"},
    },
    ["cohort", "source_snapshot_id", "sample_size", "selection_rule"],
    additional_properties=True,
)
FOLLOW_UP_REQUEST_SCHEMA = json_object_schema(
    {
        "trigger": {"type": "string", "enum": sorted(FOLLOW_UP_TRIGGERS)},
        "suggested_role": {"type": "string", "enum": sorted(FOLLOW_UP_ROLES)},
        "question": {"type": "string", "minLength": 1},
        "reason": {"type": "string", "minLength": 1},
        "materiality": {"type": "string", "enum": sorted(FOLLOW_UP_MATERIALITY)},
        "required_inputs": STRING_LIST_SCHEMA,
        "trigger_evidence_refs": STRING_LIST_SCHEMA,
        "suggested_consent_posture": {
            "type": "string",
            "enum": sorted(FOLLOW_UP_CONSENT_POSTURE),
        },
        "blocked_actions": STRING_LIST_SCHEMA,
    },
    ["trigger", "suggested_role", "question", "reason", "materiality"],
    additional_properties=False,
)
IMPROVEMENT_SCHEMA = json_object_schema(
    {
        "improvement_type": {"type": "string", "enum": sorted(IMPROVEMENT_TYPES)},
        "improvement": {"type": "string", "minLength": 1},
        "reason": {"type": "string", "minLength": 1},
        "materiality": {"type": "string", "enum": sorted(FOLLOW_UP_MATERIALITY)},
        "suggested_role": {
            "type": "string",
            "enum": sorted({*FOLLOW_UP_ROLES, "head-manager"}),
        },
        "evidence_refs": STRING_LIST_SCHEMA,
        "applies_to": STRING_LIST_SCHEMA,
        "blocked_actions": STRING_LIST_SCHEMA,
    },
    ["improvement_type", "improvement", "reason"],
    additional_properties=False,
)


RESEARCH_ARTIFACT_METADATA_FIELDS = {
    "knowledge_cutoff": {
        "type": "string",
        "format": "date-time",
        "description": (
            "Optional RFC 3339 timestamp with an explicit timezone, for example "
            "2026-07-13T00:00:00Z; omit it rather than sending a date-only value. "
            "It must not be later than the service receipt time; omit it instead "
            "of guessing an end-of-day or other future timestamp. "
            "When source_snapshot_ids are supplied, it must be at or after the "
            "maximum service-returned snapshot known_at timestamp; prefer that exact maximum."
        ),
    },
    "context_summary": {"type": "string"},
    "reader_summary": {"type": "string"},
    "handoff_state": {"type": "string", "enum": ["accepted", "revise", "blocked", "waiting"]},
    "confidence": {
        "type": ["string", "number"],
        "description": "Prefer one of low, medium, or high; numeric confidence is also accepted for compatibility.",
    },
    "missing_evidence": {"type": "array"},
    "next_recipient": {"type": "string"},
    "next_action": {"type": "string"},
    "blocked_actions": {"type": "array"},
    "source_snapshot_ids": {"type": "array", "items": {"type": "string"}},
    "calculation_run_ids": {
        "type": "array",
        "maxItems": 50,
        "items": {"type": "string", "minLength": 1, "maxLength": 180},
        "description": (
            "Current-workflow successful or reused CalculationRun ids used in the conclusion. "
            "Run hashes and reuse origins are service-derived."
        ),
    },
    "evidence_lane": {"type": "string", "enum": ["historical_replay", "historical_holdout", "live_forward"]},
    "research_spec_id": {"type": "string"},
    "replay_manifest_id": {"type": "string"},
    "decision_snapshot_id": {"type": "string"},
    "strategy_name": {"type": "string"},
    "strategy_hash": {"type": "string"},
    "investment_brain_id": {"type": "string"},
    "investment_brain_version": {"type": "string"},
    "investment_brain_content_digest": {"type": "string"},
    "investor_context_applied": {"type": "boolean"},
    "investor_context_hash": {"type": "string"},
    "decision_memory_consulted": {"type": "boolean"},
    "decision_memory_cutoff": {"type": "string"},
    "forecast_required": {"type": "boolean"},
    "decision_quality_required": {"type": "boolean"},
    "investor_context_gate_required": {"type": "boolean"},
    "anti_overfit_required": {"type": "boolean"},
    "anti_overfit_checks": ANTI_OVERFIT_CHECKS_SCHEMA,
    "forecast_allowed": {"type": "boolean"},
    "forecast_block_reason": {"type": "string"},
    "forecast_target": {"type": "string"},
    "forecast_horizon": {"type": "string"},
    "probability": {},
    "probability_range": PROBABILITY_RANGE_SCHEMA,
    "base_rate": {
        "type": "object",
        "description": (
            "Artifact metadata only. To create a ledger forecast, call issue_forecast after this artifact is accepted "
            "and use its complete base_rate contract."
        ),
    },
    "missing_base_rate_note": {"type": "string"},
    "evidence_ids": {"type": "array"},
    "contrary_evidence": {"type": "array"},
    "resolution_source": {"type": "string"},
    "review_date": {"type": "string"},
    "update_triggers": {"type": "array"},
    "invalidation_conditions": {"type": "array"},
    "source_trust_notes": {"type": "array"},
    "scenario_cases": {"type": "array"},
    "scenario_summary": {"type": "string"},
    "thesis_lifecycle": THESIS_LIFECYCLE_SCHEMA,
    "current_price_as_of": {"type": "string"},
    "market_anchor_as_of": {"type": "string"},
    "investor_context_gaps": {"type": "array"},
    "follow_up_requests": {"type": "array", "items": FOLLOW_UP_REQUEST_SCHEMA},
    "improvements": {"type": "array", "items": IMPROVEMENT_SCHEMA},
}
RESEARCH_ARTIFACT_WORKFLOW_FIELDS = {
    "workflow_run_id": {"type": "string", "minLength": 1, "maxLength": 180},
    "input_artifact_ids": {
        "type": "array",
        "maxItems": 50,
        "items": {"type": "string", "minLength": 1, "maxLength": 180},
    },
}
RESEARCH_SPEC_FIELDS = {
    "spec_id": {"type": "string"},
    "created_at": {"type": "string"},
    "knowledge_cutoff": {
        "type": "string",
        "format": "date-time",
        "description": "RFC 3339 timestamp with an explicit timezone.",
    },
    "evidence_lane": {"type": "string", "enum": ["historical_replay", "historical_holdout", "live_forward"]},
    "parent_spec_id": {"type": "string"},
    "method_profile": {
        "type": "string",
        "enum": [
            "general_evidence_v1",
            "event_research_v1",
            "quant_signal_v1",
            "listed_equity_fcff_dcf_v1",
        ],
    },
    "hypothesis": {"type": "string"},
    "economic_mechanism": {"type": "string"},
    "research_type": {"type": "string"},
    "instrument": {"type": "string"},
    "universe": {"type": "string"},
    "universe_membership_rule": {"type": "string"},
    "target": {"type": "string"},
    "horizon": {"type": "string"},
    "benchmark": {"type": "string"},
    "holding_period": {"type": "string"},
    "rebalance_rule": {"type": "string"},
    "signal_definition": {"type": "object"},
    "falsification_criteria": {"type": "array"},
    "validation_plan": {"type": "object"},
    "parameter_trial_budget": {"type": "integer", "minimum": 1},
    "cost_assumptions": {"type": "object"},
    "capacity_assumptions": {"type": "object"},
    "resolution_rule": {"type": "string"},
    "causal_analysis_required": {"type": "boolean"},
    "driver_tree": {"type": "object"},
    "base_rate_cohort": {"type": "object"},
    "implied_expectations_plan": {"type": "object"},
    "scenario_plan": {"type": "object"},
    "method_reconciliation_plan": {"type": "object"},
    "independent_review_plan": {"type": "object"},
}
FORECAST_ISSUE_FIELDS = {
    "forecast_id": {"type": "string"},
    "workflow_run_id": {"type": "string"},
    "artifact_id": {"type": "string"},
    "artifact_path": {"type": "string"},
    "research_spec_id": {"type": "string"},
    "replay_manifest_id": {"type": "string"},
    "evidence_lane": {"type": "string", "enum": ["historical_replay", "historical_holdout", "live_forward"]},
    "role": {"type": "string"},
    "instrument": {"type": "string"},
    "universe": {"type": "string"},
    "regime": {"type": "string"},
    "forecast_target": {"type": "string"},
    "target_type": {"type": "string", "enum": ["binary", "categorical", "continuous"]},
    "unit": {"type": "string"},
    "benchmark": {"type": "string"},
    "horizon": {
        "type": "string",
        "format": "date-time",
        "description": "RFC 3339 resolution timestamp with an explicit timezone.",
    },
    "issued_at": {
        "type": "string",
        "format": "date-time",
        "description": "Optional RFC 3339 timestamp; normally omit it so the service records receipt time.",
    },
    "knowledge_cutoff": {
        "type": "string",
        "format": "date-time",
        "description": "RFC 3339 timestamp with an explicit timezone.",
    },
    "probability": {"type": "number", "minimum": 0, "maximum": 1},
    "probability_range": PROBABILITY_RANGE_SCHEMA,
    "probabilities": {
        "type": "object",
        "description": "Categorical probabilities keyed by outcome; values must sum to 1.",
    },
    "prediction": {"type": "number"},
    "interval": {"type": "object"},
    "quantiles": {"type": "object"},
    "base_rate": FORECAST_BASE_RATE_SCHEMA,
    "evidence_ids": {"type": "array"},
    "contrary_evidence": {"type": "array"},
    "invalidation_conditions": {"type": "array"},
    "update_triggers": {"type": "array"},
    "resolution_rule": {"type": "string"},
    "resolution_source": {"type": "string"},
    "review_date": {"type": "string"},
    "model": {"type": "string"},
    "reasoning_effort": {"type": "string"},
    "prompt_hash": {"type": "string"},
    "tool_profile_hash": {"type": "string"},
    "config_hash": {"type": "string"},
    "idempotency_key": {"type": "string"},
}
FORECAST_REVISION_FIELDS = {
    key: value
    for key, value in FORECAST_ISSUE_FIELDS.items()
    if key in {
        "probability", "probability_range", "probabilities", "prediction", "interval", "quantiles",
        "base_rate", "evidence_ids", "contrary_evidence", "invalidation_conditions", "update_triggers",
        "knowledge_cutoff", "regime", "model", "reasoning_effort", "prompt_hash", "tool_profile_hash",
        "config_hash", "idempotency_key",
    }
}
FORECAST_REVISION_FIELDS.update({
    "forecast_id": {"type": "string"},
    "revision_reason": {"type": "string"},
    "revised_at": {"type": "string"},
})
ORDER_TICKET_SCHEMA = json_object_schema(
    {
        "ticket_id": {"type": "string", "minLength": 1, "maxLength": 160},
        "natural_language": {"type": "string", "minLength": 1, "maxLength": 1000},
        "source": {"type": "string", "enum": ["codex", "web", "api", "cli"]},
        "symbol": {"type": "string", "minLength": 1, "maxLength": 64},
        "side": {"type": "string", "enum": ["buy", "sell"]},
        "quantity": {"type": "number", "exclusiveMinimum": 0},
        "order_type": {"type": "string", "enum": ["market", "limit", "stop", "stop_limit"]},
        "limit_price": {"type": "number", "exclusiveMinimum": 0},
        "stop_price": {"type": "number", "exclusiveMinimum": 0},
        "time_in_force": {"type": "string", "minLength": 1, "maxLength": 32},
        "currency": {"type": "string", "pattern": "^[A-Za-z]{3}$"},
        "base_currency": {"type": "string", "pattern": "^[A-Za-z]{3}$"},
        "fx_rate": {"type": "number", "exclusiveMinimum": 0},
        "fx_source_snapshot_id": {"type": "string", "minLength": 1, "maxLength": 160},
        "fx_as_of": {"type": "string", "minLength": 1, "maxLength": 80},
        "broker_id": {"type": "string", "minLength": 1, "maxLength": 120},
        "broker_account_id": {"type": "string", "minLength": 1, "maxLength": 160},
        "portfolio_id": {"type": "string", "minLength": 1, "maxLength": 120},
        "account_id": {"type": "string", "minLength": 1, "maxLength": 120},
        "strategy_id": {"type": "string", "minLength": 1, "maxLength": 120},
    },
    [],
    additional_properties=True,
)


TOOL_SPECS: tuple[McpToolSpec, ...] = (
    McpToolSpec(
        name="get_tradingcodex_status",
        description="Return TradingCodex service, DB, workspace, and paper account scope status.",
        category="harness",
        risk_level="read",
        allowed_roles=roles_with_mcp_tool("get_tradingcodex_status"),
        handler_name="get_tradingcodex_status",
        input_schema=object_schema(),
    ),
    McpToolSpec(
        name="list_codex_capabilities",
        description="List user-installed Codex MCP servers, skills, plugins, and plugin components without exposing launch configuration or credentials.",
        category="harness",
        risk_level="read",
        allowed_roles=roles_with_mcp_tool("list_codex_capabilities"),
        handler_name="list_codex_capabilities",
        input_schema=object_schema(additional_properties=False),
    ),
    McpToolSpec(
        name="get_runtime_mode",
        description="Compatibility-only retired runtime-mode status. It always grants no Build authority.",
        category="harness",
        risk_level="read",
        allowed_roles=frozenset({"head-manager"}),
        handler_name="get_runtime_mode",
        input_schema=object_schema(),
    ),
    McpToolSpec(
        name="get_update_status",
        description="Return TradingCodex package/workspace update status and self-update gate metadata without running an update.",
        category="harness",
        risk_level="read",
        allowed_roles=roles_with_mcp_tool("get_update_status"),
        handler_name="get_update_status",
        input_schema=object_schema(),
    ),
    McpToolSpec(
        name="manage_strategy",
        description=(
            "Perform one exact Strategy lifecycle action through the canonical workspace service. "
            "Requires a current root $tcx-strategy turn; create/update read a reviewable root-level body_path."
        ),
        category="customization",
        risk_level="write",
        allowed_roles=frozenset({"head-manager"}),
        handler_name="manage_strategy",
        input_schema=object_schema(
            {
                "action": {
                    "type": "string",
                    "enum": ["list", "inspect", "create", "update", "activate", "archive", "delete"],
                },
                "name": {"type": "string", "maxLength": 64},
                "description": {"type": "string", "maxLength": 500},
                "body_path": {"type": "string", "maxLength": 180},
                "language": {"type": "string", "maxLength": 32},
                "status": {"type": "string", "enum": ["draft", "active", "archived"]},
                "active_only": {"type": "boolean"},
                "force": {"type": "boolean"},
            },
            ["action"],
            additional_properties=False,
        ),
    ),
    McpToolSpec(
        name="manage_investment_brain",
        description=(
            "Perform one exact Investment Brain validation or lifecycle action through the canonical service. "
            "Requires a current root $tcx-brain turn; local_source stays below investment-brains/."
        ),
        category="customization",
        risk_level="write",
        allowed_roles=frozenset({"head-manager"}),
        handler_name="manage_investment_brain",
        input_schema=object_schema(
            {
                "action": {
                    "type": "string",
                    "enum": [
                        "list", "inspect", "validate", "install", "update",
                        "activate", "deactivate", "rollback", "remove",
                    ],
                },
                "brain_id": {"type": "string", "maxLength": 80},
                "local_source": {"type": "string", "maxLength": 240},
                "git_source": {"type": "string", "maxLength": 2048},
                "ref": {"type": "string", "maxLength": 240},
                "version": {"type": "string", "maxLength": 40},
                "active_only": {"type": "boolean"},
            },
            ["action"],
            additional_properties=False,
        ),
    ),
    McpToolSpec(
        name="begin_analysis_run",
        description=(
            "Create the lightweight run/provenance binding for a Codex-native analysis. The service stores only request hash/size and "
            "sealed explicit Investment Brain, strategy, and investor-context provenance; it does not classify intent, choose roles, "
            "build a DAG, or schedule work."
        ),
        category="harness",
        risk_level="write",
        allowed_roles=roles_with_mcp_tool("begin_analysis_run"),
        handler_name="begin_analysis_run",
        input_schema=object_schema(
            {
                "request": {"type": "string", "minLength": 1, "maxLength": 20000},
                "workflow_run_id": {"type": "string", "minLength": 1, "maxLength": 180},
            },
            ["request"],
            additional_properties=False,
        ),
    ),
    McpToolSpec(
        name="simulate_policy",
        description="Evaluate TradingCodex policy for a proposed action without bypassing service-layer checks.",
        category="policy",
        risk_level="read",
        allowed_roles=roles_with_mcp_tool("simulate_policy"),
        handler_name="simulate_policy",
        input_schema=object_schema({
            "principal_id": {"type": "string"},
            "action": {"type": "string"},
            "resource": {"type": "string"},
            "order": {"type": "object"},
        }),
    ),
    McpToolSpec(
        name="validate_approval_receipt",
        description="Validate that an approval receipt is valid, unexpired, and matches its order ticket payload.",
        category="orders",
        risk_level="write",
        allowed_roles=roles_with_mcp_tool("validate_approval_receipt"),
        handler_name="validate_approval_receipt",
        input_schema=object_schema(
            {
                "ticket_id": {"type": "string", "minLength": 1, "maxLength": 160},
                "approval_receipt_id": {"type": "string", "minLength": 1, "maxLength": 160},
            },
            ["ticket_id", "approval_receipt_id"],
            additional_properties=False,
        ),
        capability_required="approval_receipt.validate",
    ),
    McpToolSpec(
        name="discard_draft_order",
        description="Discard a local DRAFT or PRECHECKED order ticket without invoking a broker.",
        category="orders",
        risk_level="write",
        allowed_roles=frozenset({"portfolio-manager"}),
        handler_name="discard_draft_order",
        input_schema=object_schema(
            {"ticket_id": {"type": "string", "minLength": 1, "maxLength": 160}},
            ["ticket_id"],
            additional_properties=False,
        ),
        capability_required="mcp.tradingcodex.discard_draft_order",
    ),
    McpToolSpec(
        name="get_order_status",
        description="Experimental: return local order and broker-order status information without submitting or canceling orders.",
        category="execution",
        risk_level="read",
        allowed_roles=roles_with_mcp_tool("get_order_status"),
        handler_name="get_order_status",
        input_schema=object_schema({"ticket_id": {"type": "string"}, "broker_order_id": {"type": "string"}}, additional_properties=False),
        experimental=True,
    ),
    McpToolSpec(
        name="get_positions",
        description="Return paper portfolio positions and cash state.",
        category="portfolio",
        risk_level="read",
        allowed_roles=roles_with_mcp_tool("get_positions"),
        handler_name="list_positions",
        input_schema=object_schema(),
    ),
    McpToolSpec(
        name="get_portfolio_snapshot",
        description="Return portfolio snapshot for portfolio, risk, and execution checks.",
        category="portfolio",
        risk_level="read",
        allowed_roles=roles_with_mcp_tool("get_portfolio_snapshot"),
        handler_name="list_positions",
        input_schema=object_schema(),
    ),
    McpToolSpec(
        name="list_broker_connections",
        description="List local broker connections, read scopes, trading lock state, accounts, and sync status.",
        category="brokers",
        risk_level="read",
        allowed_roles=roles_with_mcp_tool("list_broker_connections"),
        handler_name="list_broker_connections",
        input_schema=object_schema(),
    ),
    McpToolSpec(
        name="get_broker_connection_status",
        description="Return one broker connection health, credential reference, capabilities, and trading lock state.",
        category="brokers",
        risk_level="read",
        allowed_roles=roles_with_mcp_tool("get_broker_connection_status"),
        handler_name="get_broker_connection_status",
        input_schema=object_schema({"broker_id": {"type": "string", "minLength": 1, "maxLength": 120}}, ["broker_id"], additional_properties=False),
    ),
    McpToolSpec(
        name="list_broker_adapter_providers",
        description="List installed TradingCodex broker adapter providers. Core ships paper only; broker-specific live providers are added by build work.",
        category="brokers",
        risk_level="read",
        allowed_roles=roles_with_mcp_tool("list_broker_adapter_providers"),
        handler_name="list_broker_adapter_providers",
        input_schema=object_schema(
            {
                "family": {"type": "string", "maxLength": 120},
                "asset_class": {"type": "string", "maxLength": 80},
            },
            additional_properties=False,
        ),
    ),
    McpToolSpec(
        name="register_broker_connector",
        description="Register or update a native broker connector profile using a credential_ref, without exposing raw broker tools or secrets.",
        category="brokers",
        risk_level="write",
        allowed_roles=roles_with_mcp_tool("register_broker_connector"),
        handler_name="register_broker_connector",
        input_schema=object_schema(
            {
                "provider_id": {"type": "string", "minLength": 1, "maxLength": 120},
                "broker_id": {"type": "string", "minLength": 1, "maxLength": 120},
                "display_name": {"type": "string", "maxLength": 160},
                "credential_ref": {"type": "string", "maxLength": 255},
                "environment": {"type": "string", "maxLength": 80},
                "region": {"type": "string", "maxLength": 80},
            },
            ["provider_id", "broker_id"],
            additional_properties=False,
        ),
        capability_required="broker_connector.register",
    ),
    McpToolSpec(
        name="render_broker_connector_scaffold",
        description="Render content-addressed provider-driven connector scaffold files without writing them. If the provider is missing, use this only for an explicit user scaffold-only request; implementation or connection requests must build and approve the provider first.",
        category="brokers",
        risk_level="read",
        allowed_roles=roles_with_mcp_tool("render_broker_connector_scaffold"),
        handler_name="render_broker_connector_scaffold",
        input_schema=object_schema(
            {
                "provider_id": {"type": "string", "minLength": 1, "maxLength": 120},
                "broker_id": {"type": "string", "minLength": 1, "maxLength": 120},
                "display_name": {"type": "string", "maxLength": 160},
                "credential_ref": {"type": "string", "maxLength": 255},
                "environment": {"type": "string", "maxLength": 80},
                "region": {"type": "string", "maxLength": 80},
            },
            ["provider_id", "broker_id"],
            additional_properties=False,
        ),
        capability_required="broker_connector.register",
    ),
    McpToolSpec(
        name="validate_broker_connector_build",
        description="Explicitly validate provider-driven connector health and registration metadata, persisting validation state and eligible trade scopes without submitting an order.",
        category="brokers",
        risk_level="write",
        allowed_roles=roles_with_mcp_tool("validate_broker_connector_build"),
        handler_name="validate_broker_connector_build",
        input_schema=object_schema({"broker_id": {"type": "string", "minLength": 1, "maxLength": 120}}, ["broker_id"], additional_properties=False),
        capability_required="broker_connector.register",
    ),
    McpToolSpec(
        name="get_broker_capability_profile",
        description="Return the normalized BrokerCapabilityProfile stored on a native broker connector.",
        category="brokers",
        risk_level="read",
        allowed_roles=roles_with_mcp_tool("get_broker_capability_profile"),
        handler_name="get_broker_capability_profile",
        input_schema=object_schema({"broker_id": {"type": "string", "minLength": 1, "maxLength": 120}}, ["broker_id"], additional_properties=False),
    ),
    McpToolSpec(
        name="get_broker_instrument_constraints",
        description="Return normalized instrument/order constraints for a broker, asset class, product, and symbol.",
        category="brokers",
        risk_level="read",
        allowed_roles=roles_with_mcp_tool("get_broker_instrument_constraints"),
        handler_name="get_broker_instrument_constraints",
        input_schema=object_schema(
            {
                "broker_id": {"type": "string", "minLength": 1, "maxLength": 120},
                "symbol": {"type": "string", "minLength": 1, "maxLength": 120},
                "venue_symbol": {"type": "string", "maxLength": 160},
                "asset_class": {"type": "string", "maxLength": 80},
                "product_type": {"type": "string", "maxLength": 80},
                "market": {"type": "string", "maxLength": 80},
            },
            ["broker_id", "symbol"],
            additional_properties=False,
        ),
    ),
    McpToolSpec(
        name="preview_order_translation",
        description="Preview canonical_order translation for a broker connector without submission, cancellation, or approval authority.",
        category="orders",
        risk_level="write",
        allowed_roles=roles_with_mcp_tool("preview_order_translation"),
        handler_name="preview_order_translation",
        input_schema=object_schema(ORDER_TICKET_SCHEMA["properties"], ["broker_id"], additional_properties=True),
        capability_required="broker_order.preview",
    ),
    McpToolSpec(
        name="get_connector_build_status",
        description="Return connector scaffold metadata created by an authorized TradingCodex Build turn without enabling live execution.",
        category="brokers",
        risk_level="read",
        allowed_roles=roles_with_mcp_tool("get_connector_build_status"),
        handler_name="get_connector_build_status",
        input_schema=object_schema(),
    ),
    McpToolSpec(
        name="sync_broker_account",
        description="Run a read-only broker account sync through the TradingCodex connection registry and materialize the local portfolio snapshot.",
        category="brokers",
        risk_level="write",
        allowed_roles=roles_with_mcp_tool("sync_broker_account"),
        handler_name="sync_broker_account",
        input_schema=object_schema(
            {
                "broker_id": {"type": "string"},
                "broker_account_id": {"type": "string"},
            },
            ["broker_id"],
            additional_properties=False,
        ),
        capability_required="broker_account.sync",
    ),
    McpToolSpec(
        name="list_reconciliation_runs",
        description="List broker/local reconciliation summaries created by portfolio sync.",
        category="portfolio",
        risk_level="read",
        allowed_roles=roles_with_mcp_tool("list_reconciliation_runs"),
        handler_name="list_reconciliation_runs",
        input_schema=object_schema({"broker_id": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 200}}),
    ),
    McpToolSpec(
        name="create_order_ticket",
        description="Create a draft-only canonical order ticket from explicit fields or natural language without broker submission.",
        category="orders",
        risk_level="write",
        allowed_roles=roles_with_mcp_tool("create_order_ticket"),
        handler_name="create_order_ticket",
        input_schema=object_schema(ORDER_TICKET_SCHEMA["properties"], ["ticket_id"], additional_properties=False),
        capability_required="order_ticket.create",
    ),
    McpToolSpec(
        name="run_order_checks",
        description="Run schema, policy, restricted symbol, cash/position, broker validation, market, and risk readiness checks for an order ticket.",
        category="orders",
        risk_level="write",
        allowed_roles=roles_with_mcp_tool("run_order_checks"),
        handler_name="run_order_checks",
        input_schema=object_schema(
            {"ticket_id": {"type": "string", "minLength": 1, "maxLength": 160}},
            ["ticket_id"],
            additional_properties=False,
        ),
        capability_required="order_ticket.check",
    ),
    McpToolSpec(
        name="request_order_approval",
        description="Create an approval receipt for a checked order ticket, binding the approval to the exact order payload hash and broker/account scope.",
        category="approvals",
        risk_level="approval",
        allowed_roles=roles_with_mcp_tool("request_order_approval"),
        handler_name="request_order_approval",
        input_schema=object_schema(
            {
                "ticket_id": {"type": "string"},
                "expires_hours": {"type": "integer", "minimum": 1, "maximum": 168},
            },
            ["ticket_id"],
            additional_properties=False,
        ),
        capability_required="approval_receipt.create",
        requires_approval=True,
    ),
    McpToolSpec(
        name="get_order_ticket",
        description="Fetch a canonical order ticket, checks, fills, broker order records, and event timeline.",
        category="orders",
        risk_level="read",
        allowed_roles=roles_with_mcp_tool("get_order_ticket"),
        handler_name="get_order_ticket",
        input_schema=object_schema(
            {"ticket_id": {"type": "string", "minLength": 1, "maxLength": 160}},
            ["ticket_id"],
            additional_properties=False,
        ),
    ),
    McpToolSpec(
        name="list_order_tickets",
        description="List canonical order tickets and approval readiness state.",
        category="orders",
        risk_level="read",
        allowed_roles=roles_with_mcp_tool("list_order_tickets"),
        handler_name="list_order_tickets",
        input_schema=object_schema(
            {"state": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 200}},
            additional_properties=False,
        ),
    ),
    McpToolSpec(
        name=ORDER_TURN_GRANT_TOOL,
        description=(
            "Use the single order effect authorized for this root Codex turn. "
            "The call is rejected unless UserPromptSubmit accepted an exact $tcx-order-allow "
            "invocation on the first meaningful line and PreToolUse injects its one-time proof."
        ),
        category="execution",
        risk_level="execution",
        allowed_roles=roles_with_mcp_tool(ORDER_TURN_GRANT_TOOL),
        handler_name=ORDER_TURN_GRANT_TOOL,
        input_schema=object_schema(
            {
                "action": {"type": "string", "enum": ["submit", "cancel"]},
                "ticket_id": {"type": "string", "minLength": 1, "maxLength": 160},
                "approval_receipt_id": {"type": "string", "minLength": 1, "maxLength": 160},
                "broker_order_id": {"type": "string", "minLength": 1, "maxLength": 160},
                "live_confirmation": {"type": "string", "minLength": 1, "maxLength": 500},
            },
            ["action", "ticket_id", "approval_receipt_id"],
            additional_properties=False,
        ),
        capability_required="execution.use_order_turn_grant",
    ),
    McpToolSpec(
        name="list_workflow_artifacts",
        description="List workflow artifacts from workspace paths and file-native research memory.",
        category="workflows",
        risk_level="read",
        allowed_roles=roles_with_mcp_tool("list_workflow_artifacts"),
        handler_name="list_workflow_artifacts",
        input_schema=object_schema(),
    ),
    McpToolSpec(
        name="create_research_artifact",
        description=(
            "Store markdown research as a workspace-native file. For an analysis run, pass workflow_run_id and any exact "
            "input_artifact_ids consumed. The service derives producer identity, verifies run-local lineage, and computes content hashes; "
            "it does not require a server plan or task binding. Include a non-empty readiness_label. When "
            "decision_quality_required=true, thesis_lifecycle.state and its state-specific evidence are required."
        ),
        category="research",
        risk_level="write",
        allowed_roles=roles_with_mcp_tool("create_research_artifact"),
        handler_name="create_research_artifact",
        input_schema=object_schema({
            "artifact_id": {"type": "string"},
            "artifact_type": {"type": "string"},
            "universe": {"type": "string"},
            "workflow_type": {"type": "string"},
            "symbol": {"type": "string"},
            "title": {"type": "string"},
            "markdown": {"type": "string"},
            "markdown_path": {"type": "string"},
            "export_path": {"type": "string"},
            "source_as_of": {"type": "string"},
            "readiness_label": {"type": "string"},
            **RESEARCH_ARTIFACT_METADATA_FIELDS,
            **RESEARCH_ARTIFACT_WORKFLOW_FIELDS,
        }),
        capability_required="research_artifact.write",
    ),
    McpToolSpec(
        name="append_research_artifact_version",
        description=(
            "Append a workspace-file version for an existing research artifact; recorded workflow bindings remain service-derived."
        ),
        category="research",
        risk_level="write",
        allowed_roles=roles_with_mcp_tool("append_research_artifact_version"),
        handler_name="append_research_artifact_version",
        input_schema=object_schema({
            "artifact_id": {"type": "string"},
            "workflow_type": {"type": "string"},
            "markdown": {"type": "string"},
            "markdown_path": {"type": "string"},
            "source_as_of": {"type": "string"},
            "readiness_label": {"type": "string"},
            **RESEARCH_ARTIFACT_METADATA_FIELDS,
            **RESEARCH_ARTIFACT_WORKFLOW_FIELDS,
        }, ["artifact_id"]),
        capability_required="research_artifact.write",
    ),
    McpToolSpec(
        name="get_research_artifact",
        description="Fetch a workspace-native research artifact by artifact_id.",
        category="research",
        risk_level="read",
        allowed_roles=roles_with_mcp_tool("get_research_artifact"),
        handler_name="get_research_artifact",
        input_schema=object_schema({"artifact_id": {"type": "string"}, "include_markdown": {"type": "boolean"}}, ["artifact_id"]),
    ),
    McpToolSpec(
        name="list_research_artifacts",
        description="List workspace-native research artifacts and metadata.",
        category="research",
        risk_level="read",
        allowed_roles=roles_with_mcp_tool("list_research_artifacts"),
        handler_name="list_research_artifacts",
        input_schema=object_schema({"artifact_type": {"type": "string"}, "universe": {"type": "string"}, "symbol": {"type": "string"}, "limit": {"type": "integer"}}),
    ),
    McpToolSpec(
        name="search_research_artifacts",
        description="Search workspace-native markdown research artifacts with lexical file search.",
        category="research",
        risk_level="read",
        allowed_roles=roles_with_mcp_tool("search_research_artifacts"),
        handler_name="search_research_artifacts",
        input_schema=object_schema({"query": {"type": "string"}, "limit": {"type": "integer"}}, ["query"]),
    ),
    McpToolSpec(
        name="list_artifact_catalog",
        description="List the rebuildable v2 catalog across research, reports, decisions, forecasts, and evaluation artifacts without changing source files.",
        category="research",
        risk_level="read",
        allowed_roles=roles_with_mcp_tool("list_artifact_catalog"),
        handler_name="list_artifact_catalog",
        input_schema=object_schema({
            "artifact_type": {"type": "string"},
            "universe": {"type": "string"},
            "symbol": {"type": "string"},
            "workflow_run_id": {"type": "string"},
            "readiness_label": {"type": "string"},
            "handoff_state": {"type": "string"},
            "compatibility": {"type": "string", "enum": ["full", "legacy_partial", "invalid"]},
            "knowledge_cutoff": {"type": "string"},
            "include_invalid": {"type": "boolean"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 1000},
        }, additional_properties=False),
    ),
    McpToolSpec(
        name="search_artifact_catalog",
        description="Search the v2 cross-artifact catalog with metadata, lexical relevance, compatibility, and point-in-time filters.",
        category="research",
        risk_level="read",
        allowed_roles=roles_with_mcp_tool("search_artifact_catalog"),
        handler_name="search_artifact_catalog",
        input_schema=object_schema({
            "query": {"type": "string"},
            "artifact_type": {"type": "string"},
            "universe": {"type": "string"},
            "symbol": {"type": "string"},
            "workflow_run_id": {"type": "string"},
            "readiness_label": {"type": "string"},
            "handoff_state": {"type": "string"},
            "compatibility": {"type": "string", "enum": ["full", "legacy_partial"]},
            "knowledge_cutoff": {"type": "string"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 200},
        }, ["query"], additional_properties=False),
    ),
    McpToolSpec(
        name="export_research_artifact_md",
        description="Export or copy a workspace-native research artifact markdown file.",
        category="research",
        risk_level="write",
        allowed_roles=roles_with_mcp_tool("export_research_artifact_md"),
        handler_name="export_research_artifact_md",
        input_schema=object_schema({"artifact_id": {"type": "string"}, "export_path": {"type": "string"}}, ["artifact_id"]),
        capability_required="research_artifact.export",
    ),
    McpToolSpec(
        name="record_source_snapshot",
        description=(
            "Record content-addressed point-in-time provider, query, timestamp, "
            "revision, coverage, and payload metadata. Omit snapshot_id, "
            "retrieved_at, and recorded_at for normal agent calls: the service "
            "records receipt times and returns a safe snapshot_id. Provide "
            "known_at only when the evidence's actual knowable time is known, "
            "using an ISO-8601 datetime with an explicit timezone; otherwise "
            "omit it and let the service use the receipt time. The response "
            "returns the exact service-owned known_at, retrieved_at, recorded_at, "
            "and system_recorded_at values for downstream artifact binding. "
            "Explicit caller timestamps remain strictly validated."
        ),
        category="research",
        risk_level="write",
        allowed_roles=roles_with_mcp_tool("record_source_snapshot"),
        handler_name="record_source_snapshot",
        input_schema=object_schema({
            "provider": {"type": "string"},
            "source_category": {"type": "string"},
            "source_locator": {"type": "string"},
            "provider_query": {"type": "object"},
            "as_of": {"type": "string"},
            "observed_at": {"type": "string"},
            "effective_at": {"type": "string"},
            "published_at": {"type": "string"},
            "retrieved_at": {
                "type": "string",
                "description": (
                    "Service-owned by default. Omit for normal agent calls so "
                    "TradingCodex records the receipt time. Any explicit value "
                    "must be a truthful timezone-aware ISO-8601 datetime."
                ),
            },
            "known_at": {
                "type": "string",
                "description": (
                    "Provide only when the evidence's actual knowable time is "
                    "genuinely known, as a timezone-aware ISO-8601 datetime. "
                    "Do not substitute an as-of, publication date, or guessed "
                    "time; omit it to use the service receipt time."
                ),
            },
            "recorded_at": {
                "type": "string",
                "description": (
                    "Service-owned by default. Omit for normal agent calls so "
                    "TradingCodex records storage time. Explicit values are "
                    "accepted only when timezone-aware and not in the future."
                ),
            },
            "revision": {"type": "string"},
            "vintage": {"type": "string"},
            "timezone": {"type": "string"},
            "schema_hash": {"type": "string"},
            "corporate_action_policy": {"type": "string"},
            "price_adjustment_policy": {"type": "string"},
            "universe_membership": {"type": "object"},
            "delisting_policy": {"type": "string"},
            "coverage_note": {"type": "string"},
            "artifact_id": {"type": "string"},
            "warnings": {"type": "array"},
            "payload": {"type": "object"},
        }, ["provider", "source_category"], additional_properties=False),
        capability_required="source_snapshot.record",
    ),
    McpToolSpec(
        name="search_datasets",
        description=(
            "Search immutable Dataset cards through the rebuildable research-object catalog. "
            "Returns metadata cards only, never payload rows or the full schema."
        ),
        category="research",
        risk_level="read",
        allowed_roles=roles_with_mcp_tool("search_datasets"),
        handler_name="search_datasets",
        input_schema=object_schema({
            "query": {"type": "string"},
            "symbol": {"type": "string"},
            "instrument_id": {"type": "string"},
            "provider": {"type": "string"},
            "frequency": {"type": "string"},
            "column": {"type": "string"},
            "period_start": {"type": "string"},
            "period_end": {"type": "string"},
            "knowledge_cutoff": {"type": "string"},
            "include_withdrawn": {"type": "boolean"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 200},
        }, additional_properties=False),
    ),
    McpToolSpec(
        name="get_dataset_manifest",
        description="Read one immutable Dataset manifest and lineage without returning payload rows.",
        category="research",
        risk_level="read",
        allowed_roles=roles_with_mcp_tool("get_dataset_manifest"),
        handler_name="get_dataset_manifest",
        input_schema=object_schema({"dataset_id": {"type": "string"}}, ["dataset_id"], additional_properties=False),
    ),
    McpToolSpec(
        name="profile_dataset",
        description="Profile selected Dataset columns and return bounded statistics plus at most 20 sample rows.",
        category="research",
        risk_level="read",
        allowed_roles=roles_with_mcp_tool("profile_dataset"),
        handler_name="profile_dataset",
        input_schema=object_schema({
            "dataset_id": {"type": "string"},
            "columns": {"type": "array", "items": {"type": "string"}},
            "sample_rows": {"type": "integer", "minimum": 0, "maximum": 20},
        }, ["dataset_id"], additional_properties=False),
    ),
    McpToolSpec(
        name="record_dataset_snapshot",
        description=(
            "Promote an explicit-schema scratch-local CSV, JSONL, or Parquet file into an immutable "
            "content-addressed Dataset. source_filename must be a basename in TRADINGCODEX_SCRATCH."
        ),
        category="research",
        risk_level="write",
        allowed_roles=roles_with_mcp_tool("record_dataset_snapshot"),
        handler_name="record_dataset_snapshot",
        input_schema=object_schema({
            "source_filename": {"type": "string", "minLength": 1, "maxLength": 181},
            "title": {"type": "string", "minLength": 1, "maxLength": 240},
            "description": {"type": "string", "maxLength": 4000},
            "tags": {
                "type": "array",
                "maxItems": 50,
                "items": {"type": "string", "minLength": 1, "maxLength": 100},
            },
            "provider": {"type": "string", "minLength": 1, "maxLength": 200},
            "provider_query": {"type": "object"},
            "source_snapshot_ids": {"type": "array", "items": {"type": "string"}},
            "parent_dataset_ids": {"type": "array", "items": {"type": "string"}},
            "transformation_code_hash": {"type": "string"},
            "known_at": {"type": "string"},
            "knowledge_cutoff": {"type": "string"},
            "as_of": {"type": "string"},
            "vintage": {"type": "string"},
            "period_start": {"type": "string"},
            "period_end": {"type": "string"},
            "observed_at": {"type": "string"},
            "published_at": {"type": "string"},
            "timezone": {"type": "string"},
            "frequency": {"type": "string"},
            "universe_membership_policy": {"type": "string"},
            "universe_membership": {"type": "object"},
            "instrument_ids": {"type": "array", "items": {"type": "string"}},
            "symbols": {"type": "array", "items": {"type": "string"}},
            "columns": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "type": {"type": "string"},
                        "nullable": {"type": "boolean"},
                        "unit": {"type": "string"},
                        "currency": {"type": "string"},
                        "description": {"type": "string"},
                    },
                    "required": ["name", "type"],
                    "additionalProperties": False,
                },
            },
            "adjustment_policy": {"type": "string"},
            "corporate_action_policy": {"type": "string"},
            "delisting_policy": {"type": "string"},
            "retention_policy": {
                "type": "string",
                "enum": ["permanent_local", "locator_only", "time_limited"],
            },
            "redistribution": {"type": "string"},
            "license_notes": {"type": "string"},
            "data_classification": {
                "type": "string",
                "enum": ["public", "licensed_research", "user_provided"],
            },
        }, [
            "source_filename",
            "title",
            "provider",
            "knowledge_cutoff",
            "as_of",
            "vintage",
            "period_start",
            "period_end",
            "timezone",
            "frequency",
            "universe_membership_policy",
            "universe_membership",
            "columns",
        ], additional_properties=False),
        capability_required="dataset.record",
    ),
    McpToolSpec(
        name="materialize_dataset_slice",
        description=(
            "Create a bounded scratch-local Parquet slice using typed column, time, instrument, and symbol selectors. "
            "Arbitrary SQL is not accepted."
        ),
        category="research",
        risk_level="write",
        allowed_roles=roles_with_mcp_tool("materialize_dataset_slice"),
        handler_name="materialize_dataset_slice",
        input_schema=object_schema({
            "dataset_id": {"type": "string"},
            "columns": {"type": "array", "items": {"type": "string"}},
            "time_column": {"type": "string"},
            "start": {"type": "string"},
            "end": {"type": "string"},
            "instrument_ids": {"type": "array", "items": {"type": "string"}},
            "symbols": {"type": "array", "items": {"type": "string"}},
            "max_rows": {"type": "integer", "minimum": 1, "maximum": 1000000},
            "max_bytes": {"type": "integer", "minimum": 1, "maximum": 268435456},
        }, ["dataset_id", "columns"], additional_properties=False),
        capability_required="dataset.materialize",
    ),
    McpToolSpec(
        name="search_calculations",
        description=(
            "Search bounded CalculationRun cards by type, status, metric text, and point-in-time cutoff. "
            "Returns summaries only and never script, input, output, stdout, or stderr payloads."
        ),
        category="research",
        risk_level="read",
        allowed_roles=roles_with_mcp_tool("search_calculations"),
        handler_name="search_calculations",
        input_schema=object_schema({
            "query": {"type": "string"},
            "calculation_type": {"type": "string"},
            "status": {"type": "string", "enum": ["succeeded", "failed", "reused"]},
            "knowledge_cutoff": {"type": "string"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 200},
        }, additional_properties=False),
    ),
    McpToolSpec(
        name="get_calculation_run",
        description="Read one hash-verified immutable CalculationRun and its CalculationSpec.",
        category="research",
        risk_level="read",
        allowed_roles=roles_with_mcp_tool("get_calculation_run"),
        handler_name="get_calculation_run",
        input_schema=object_schema(
            {"calculation_run_id": {"type": "string"}},
            ["calculation_run_id"],
            additional_properties=False,
        ),
    ),
    McpToolSpec(
        name="compare_calculation_runs",
        description="Compare only the requested typed metrics across two to twenty immutable CalculationRuns.",
        category="research",
        risk_level="read",
        allowed_roles=roles_with_mcp_tool("compare_calculation_runs"),
        handler_name="compare_calculation_runs",
        input_schema=object_schema({
            "calculation_run_ids": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 2,
                "maxItems": 20,
            },
            "metrics": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
                "maxItems": 50,
            },
        }, ["calculation_run_ids", "metrics"], additional_properties=False),
    ),
    McpToolSpec(
        name="prepare_calculation",
        description=(
            "Validate a scratch-local calculation script, declared inputs and outputs, parameters, cutoff, and typed result schema; "
            "seal an immutable CalculationSpec and runner sidecar, or create a current-workflow reuse Run on an exact fingerprint hit."
        ),
        category="research",
        risk_level="write",
        allowed_roles=roles_with_mcp_tool("prepare_calculation"),
        handler_name="prepare_calculation",
        input_schema=object_schema({
            "script_name": {"type": "string"},
            "workflow_run_id": {"type": "string"},
            "calculation_type": {"type": "string"},
            "calculation_version": {"type": "string"},
            "knowledge_cutoff": {"type": "string"},
            "parameters": {"type": "object"},
            "output_schema": {"type": "object"},
            "inputs": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "filename": {"type": "string"},
                        "kind": {"type": "string"},
                        "sha256": {"type": "string"},
                        "dataset_id": {"type": "string"},
                        "materialization_id": {"type": "string"},
                        "ledger_snapshot_id": {"type": "string"},
                        "ledger_snapshot_hash": {"type": "string"},
                    },
                    "required": ["name", "filename"],
                    "additionalProperties": False,
                },
            },
            "outputs": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "filename": {"type": "string"},
                        "media_type": {"type": "string"},
                        "dataset": {
                            "type": "object",
                            "description": (
                                "Explicit Dataset manifest metadata for a declared tabular or "
                                "time-series output. The application service validates the full "
                                "schema, cutoff, source lineage, and private-input boundary."
                            ),
                        },
                    },
                    "required": ["name", "filename"],
                    "additionalProperties": False,
                },
            },
        }, [
            "script_name",
            "workflow_run_id",
            "calculation_type",
            "calculation_version",
            "knowledge_cutoff",
            "output_schema",
        ], additional_properties=False),
        capability_required="calculation.prepare",
    ),
    McpToolSpec(
        name="record_calculation_run",
        description=(
            "Verify a prepared tcx-calc result envelope and record an immutable successful or failed CalculationRun. "
            "Only successful Runs are eligible for exact reuse."
        ),
        category="research",
        risk_level="write",
        allowed_roles=roles_with_mcp_tool("record_calculation_run"),
        handler_name="record_calculation_run",
        input_schema=object_schema({
            "result_file": {"type": "string"},
            "calculation_spec_id": {"type": "string"},
            "workflow_run_id": {"type": "string"},
        }, ["result_file", "workflow_run_id"], additional_properties=False),
        capability_required="calculation.record",
    ),
    McpToolSpec(
        name="create_research_spec",
        description="Freeze a point-in-time, falsifiable research plan as an immutable evidence-only artifact.",
        category="research",
        risk_level="write",
        allowed_roles=roles_with_mcp_tool("create_research_spec"),
        handler_name="create_research_spec",
        input_schema=object_schema(
            RESEARCH_SPEC_FIELDS,
            [
                "knowledge_cutoff", "hypothesis", "economic_mechanism", "universe",
                "universe_membership_rule", "target", "horizon",
                "falsification_criteria", "validation_plan", "resolution_rule",
            ],
            additional_properties=False,
        ),
        capability_required="research_spec.write",
    ),
    McpToolSpec(
        name="get_research_spec",
        description="Fetch an immutable ResearchSpec by id.",
        category="research",
        risk_level="read",
        allowed_roles=roles_with_mcp_tool("get_research_spec"),
        handler_name="get_research_spec",
        input_schema=object_schema({"spec_id": {"type": "string"}}, ["spec_id"], additional_properties=False),
    ),
    McpToolSpec(
        name="list_research_specs",
        description="List immutable point-in-time ResearchSpec artifacts.",
        category="research",
        risk_level="read",
        allowed_roles=roles_with_mcp_tool("list_research_specs"),
        handler_name="list_research_specs",
        input_schema=object_schema(additional_properties=False),
    ),
    McpToolSpec(
        name="create_replay_manifest",
        description="Bind a frozen ResearchSpec to content-addressed point-in-time source snapshots.",
        category="research",
        risk_level="write",
        allowed_roles=roles_with_mcp_tool("create_replay_manifest"),
        handler_name="create_replay_manifest",
        input_schema=object_schema({
            "manifest_id": {"type": "string"},
            "spec_id": {"type": "string"},
            "source_snapshot_ids": {"type": "array", "items": {"type": "string"}},
            "created_at": {"type": "string"},
        }, ["spec_id", "source_snapshot_ids"], additional_properties=False),
        capability_required="research_replay.write",
    ),
    McpToolSpec(
        name="record_experiment_run",
        description="Record an immutable profile-aware experiment run with evidence-backed checks; quant profiles add typed anti-overfit rules.",
        category="research",
        risk_level="write",
        allowed_roles=roles_with_mcp_tool("record_experiment_run"),
        handler_name="record_experiment_run",
        input_schema=object_schema({
            "run_id": {"type": "string"},
            "spec_id": {"type": "string"},
            "replay_manifest_id": {"type": "string"},
            "created_at": {"type": "string"},
            "code_hash": {"type": "string"},
            "data_hash": {"type": "string"},
            "config_hash": {"type": "string"},
            "model": {"type": "string"},
            "reasoning_effort": {"type": "string"},
            "prompt_hash": {"type": "string"},
            "tool_profile_hash": {"type": "string"},
            "splits": {"type": "object"},
            "trial_count": {"type": "integer", "minimum": 1},
            "metrics": {"type": "object"},
            "checks": {"type": "object"},
            "conclusion": {"type": "string"},
            "source_limitations": {"type": "array"},
        }, ["spec_id", "replay_manifest_id", "code_hash", "data_hash", "config_hash", "splits", "metrics", "checks", "conclusion"], additional_properties=False),
        capability_required="research_experiment.write",
    ),
    McpToolSpec(
        name="rebuild_research_index",
        description="Safely rebuild the workspace-file-native research metadata index.",
        category="research",
        risk_level="write",
        allowed_roles=roles_with_mcp_tool("rebuild_research_index"),
        handler_name="rebuild_research_index",
        input_schema=object_schema(additional_properties=False),
        capability_required="research_index.rebuild",
    ),
    McpToolSpec(
        name="rebuild_artifact_catalog",
        description="Discard and safely rebuild the workspace-file-native v2 artifact catalog without modifying source artifacts.",
        category="research",
        risk_level="write",
        allowed_roles=roles_with_mcp_tool("rebuild_artifact_catalog"),
        handler_name="rebuild_artifact_catalog",
        input_schema=object_schema(additional_properties=False),
        capability_required="research_index.rebuild",
    ),
    McpToolSpec(
        name="create_causal_equity_analysis",
        description="Run deterministic reverse/forward DCF arithmetic against a frozen listed-equity ResearchSpec.",
        category="research",
        risk_level="write",
        allowed_roles=roles_with_mcp_tool("create_causal_equity_analysis"),
        handler_name="create_causal_equity_analysis",
        input_schema=object_schema({
            "analysis_id": {"type": "string"},
            "spec_id": {"type": "string"},
            "replay_manifest_id": {"type": "string"},
            "analysis_input_snapshot_id": {"type": "string"},
            "prior_id": {"type": "string"},
        }, ["spec_id", "replay_manifest_id", "analysis_input_snapshot_id", "prior_id"], additional_properties=False),
        capability_required="causal_analysis.write",
    ),
    McpToolSpec(
        name="record_blind_judgment_prior",
        description="Freeze an independent reviewer view before revealing the producer conclusion.",
        category="research",
        risk_level="write",
        allowed_roles=roles_with_mcp_tool("record_blind_judgment_prior"),
        handler_name="record_blind_judgment_prior",
        input_schema=object_schema({
            "prior_id": {"type": "string"},
            "spec_id": {"type": "string"},
            "specification_view": {"type": "string"},
            "evidence_quality_view": {"type": "string"},
            "key_driver_view": {"type": "array"},
            "falsifiers": {"type": "array"},
        }, ["spec_id", "specification_view", "evidence_quality_view", "key_driver_view", "falsifiers"], additional_properties=False),
        capability_required="judgment_prior.write",
    ),
    McpToolSpec(
        name="complete_judgment_review",
        description="Complete the second-pass independent review bound to a blind prior and causal analysis.",
        category="research",
        risk_level="write",
        allowed_roles=roles_with_mcp_tool("complete_judgment_review"),
        handler_name="complete_judgment_review",
        input_schema=object_schema({
            "review_id": {"type": "string"},
            "prior_id": {"type": "string"},
            "analysis_id": {"type": "string"},
            "conclusion": {"type": "string"},
            "changed_views": {"type": "array"},
            "remaining_disagreements": {"type": "array"},
            "acceptance": {"type": "string", "enum": ["accepted", "revise", "blocked"]},
        }, ["prior_id", "analysis_id", "conclusion", "remaining_disagreements"], additional_properties=False),
        capability_required="judgment_review.write",
    ),
    McpToolSpec(
        name="issue_forecast",
        description=(
            "Issue an immutable evidence-only forecast after its research artifact is accepted. Use RFC 3339 horizon and "
            "knowledge_cutoff values, normally omit issued_at, and provide the complete base_rate cohort/source/sample/selection contract."
        ),
        category="research",
        risk_level="write",
        allowed_roles=roles_with_mcp_tool("issue_forecast"),
        handler_name="issue_forecast",
        input_schema=object_schema(
            FORECAST_ISSUE_FIELDS,
            ["artifact_id", "forecast_target", "horizon", "knowledge_cutoff", "base_rate", "evidence_ids", "contrary_evidence", "invalidation_conditions", "update_triggers", "resolution_rule"],
            additional_properties=False,
        ),
        capability_required="forecast.write",
    ),
    McpToolSpec(
        name="revise_forecast",
        description="Append an author-only forecast revision without overwriting the original forecast.",
        category="research",
        risk_level="write",
        allowed_roles=roles_with_mcp_tool("revise_forecast"),
        handler_name="revise_forecast",
        input_schema=object_schema(FORECAST_REVISION_FIELDS, ["forecast_id", "revision_reason"], additional_properties=False),
        capability_required="forecast.write",
    ),
    McpToolSpec(
        name="resolve_forecast",
        description="Independently resolve an open forecast from a reviewed source snapshot.",
        category="research",
        risk_level="write",
        allowed_roles=roles_with_mcp_tool("resolve_forecast"),
        handler_name="resolve_forecast",
        input_schema=object_schema({
            "forecast_id": {"type": "string"},
            "outcome": {},
            "resolution_source_snapshot_id": {"type": "string"},
            "resolved_at": {"type": "string"},
            "observed_at": {"type": "string"},
            "resolution_note": {"type": "string"},
            "dispute_state": {"type": "string", "enum": ["undisputed", "disputed", "under_review"]},
            "resolve_dispute": {"type": "boolean"},
            "idempotency_key": {"type": "string"},
        }, ["forecast_id", "outcome", "resolution_source_snapshot_id"], additional_properties=False),
        capability_required="forecast.resolve",
    ),
    McpToolSpec(
        name="score_forecast",
        description="Compute proper scores for an independently resolved forecast and every immutable revision.",
        category="research",
        risk_level="write",
        allowed_roles=roles_with_mcp_tool("score_forecast"),
        handler_name="score_forecast",
        input_schema=object_schema({"forecast_id": {"type": "string"}, "idempotency_key": {"type": "string"}}, ["forecast_id"], additional_properties=False),
        capability_required="forecast.score",
    ),
    McpToolSpec(
        name="promote_lesson",
        description="Append an authenticated judgment-reviewer transition to a sealed Decision Memory lesson chain.",
        category="research",
        risk_level="write",
        allowed_roles=roles_with_mcp_tool("promote_lesson"),
        handler_name="promote_lesson",
        input_schema=object_schema({
            "lesson_id": {"type": "string"},
            "to_state": {"type": "string", "enum": ["corroborated", "validated", "retired"]},
            "reason": {"type": "string"},
            "evidence_refs": {"type": "array"},
            "regimes": {"type": "array", "items": {"type": "string"}},
        }, ["lesson_id", "to_state", "reason"], additional_properties=False),
        capability_required="judgment_review.write",
    ),
    McpToolSpec(
        name="get_forecast",
        description="Fetch the latest forecast state and immutable event history.",
        category="research",
        risk_level="read",
        allowed_roles=roles_with_mcp_tool("get_forecast"),
        handler_name="get_forecast",
        input_schema=object_schema({"forecast_id": {"type": "string"}, "include_history": {"type": "boolean"}}, ["forecast_id"], additional_properties=False),
    ),
    McpToolSpec(
        name="list_forecasts",
        description="List latest evidence-only forecast records by status or role.",
        category="research",
        risk_level="read",
        allowed_roles=roles_with_mcp_tool("list_forecasts"),
        handler_name="list_forecasts",
        input_schema=object_schema({
            "status": {"type": "string"},
            "role": {"type": "string"},
            "evidence_lane": {"type": "string", "enum": ["historical_replay", "historical_holdout", "live_forward"]},
            "limit": {"type": "integer", "minimum": 1},
        }, additional_properties=False),
    ),
    McpToolSpec(
        name="get_forecast_calibration_report",
        description="Report binary forecast calibration only after the documented minimum sample.",
        category="research",
        risk_level="read",
        allowed_roles=roles_with_mcp_tool("get_forecast_calibration_report"),
        handler_name="get_forecast_calibration_report",
        input_schema=object_schema({
            "minimum_sample": {"type": "integer", "minimum": 2},
            "evidence_lane": {"type": "string", "enum": ["historical_replay", "historical_holdout", "live_forward"]},
        }, additional_properties=False),
    ),
    McpToolSpec(
        name="create_evaluation_corpus",
        description="Freeze the research-only investment and model-upgrade evaluation corpus.",
        category="evaluation",
        risk_level="write",
        allowed_roles=roles_with_mcp_tool("create_evaluation_corpus"),
        handler_name="create_evaluation_corpus",
        input_schema=object_schema({
            "corpus_id": {"type": "string"},
            "evaluation_profile": {"type": "string"},
            "required_case_tags": {"type": "array", "items": {"type": "string"}},
            "metric_dimensions": {"type": "array", "items": {"type": "string"}},
            "cases": {"type": "array", "items": {"type": "object"}},
            "promotion_criteria": {"type": "array", "items": {"type": "object"}},
            "minimum_blind_reviews": {"type": "integer", "minimum": 2},
        }, ["cases", "promotion_criteria"], additional_properties=False),
        capability_required="evaluation.write",
    ),
    McpToolSpec(
        name="record_evaluation_run",
        description="Record a caller-attested control or candidate evaluation run against a frozen corpus; promotion remains blocked until trusted runner provenance is implemented.",
        category="evaluation",
        risk_level="write",
        allowed_roles=roles_with_mcp_tool("record_evaluation_run"),
        handler_name="record_evaluation_run",
        input_schema=object_schema({
            "run_id": {"type": "string"},
            "corpus_id": {"type": "string"},
            "arm": {"type": "string", "enum": ["control", "candidate"]},
            "model": {"type": "string"},
            "reasoning_effort": {"type": "string"},
            "prompt_hash": {"type": "string"},
            "config_hash": {"type": "string"},
            "tool_profile_hash": {"type": "string"},
            "deterministic_calculation_hash": {"type": "string"},
            "extension_profile_hash": {"type": "string"},
            "case_results": {"type": "array", "items": {"type": "object"}},
            "metrics": {"type": "object"},
            "operations": {"type": "object"},
        }, ["corpus_id", "arm", "model", "reasoning_effort", "prompt_hash", "config_hash", "tool_profile_hash", "deterministic_calculation_hash", "extension_profile_hash", "case_results", "operations"], additional_properties=False),
        capability_required="evaluation.write",
    ),
    McpToolSpec(
        name="create_blind_review_assignment",
        description="Assign an authenticated independent reviewer an opaque A/B packet for a frozen run pair.",
        category="evaluation",
        risk_level="write",
        allowed_roles=roles_with_mcp_tool("create_blind_review_assignment"),
        handler_name="create_blind_review_assignment",
        input_schema=object_schema({
            "assignment_id": {"type": "string"},
            "control_run_id": {"type": "string"},
            "candidate_run_id": {"type": "string"},
            "reviewer_principal": {"type": "string"},
        }, ["control_run_id", "candidate_run_id", "reviewer_principal"], additional_properties=False),
        capability_required="evaluation.assign",
    ),
    McpToolSpec(
        name="get_blind_review_packet",
        description="Fetch an opaque A/B evaluation packet only for its authenticated assigned reviewer.",
        category="evaluation",
        risk_level="read",
        allowed_roles=roles_with_mcp_tool("get_blind_review_packet"),
        handler_name="get_blind_review_packet",
        input_schema=object_schema({
            "assignment_id": {"type": "string"},
        }, ["assignment_id"], additional_properties=False),
        capability_required="evaluation.review.read",
    ),
    McpToolSpec(
        name="record_blind_human_review",
        description="Record a model-identity-hidden human comparison for control and candidate runs.",
        category="evaluation",
        risk_level="write",
        allowed_roles=roles_with_mcp_tool("record_blind_human_review"),
        handler_name="record_blind_human_review",
        input_schema=object_schema({
            "review_id": {"type": "string"},
            "assignment_id": {"type": "string"},
            "preference": {"type": "string", "enum": ["a", "b", "tie"]},
            "ratings": {"type": "object"},
            "rationale": {"type": "string"},
        }, ["assignment_id", "preference", "ratings", "rationale"], additional_properties=False),
        capability_required="evaluation.review",
    ),
    McpToolSpec(
        name="compare_evaluation_runs",
        description="Apply frozen promotion criteria and blinded reviews to a control/candidate pair.",
        category="evaluation",
        risk_level="write",
        allowed_roles=roles_with_mcp_tool("compare_evaluation_runs"),
        handler_name="compare_evaluation_runs",
        input_schema=object_schema({
            "comparison_id": {"type": "string"},
            "control_run_id": {"type": "string"},
            "candidate_run_id": {"type": "string"},
        }, ["control_run_id", "candidate_run_id"], additional_properties=False),
        capability_required="evaluation.compare",
    ),
    McpToolSpec(
        name="record_audit_event",
        description="Append an explicit audit event through the TradingCodex audit ledger.",
        category="audit",
        risk_level="write",
        allowed_roles=roles_with_mcp_tool("record_audit_event"),
        handler_name="record_audit_event",
        input_schema=object_schema(
            {
                "event": {
                    "type": "object",
                    "properties": {
                        "type": {"type": "string", "minLength": 1},
                        "resource": {"type": "string"},
                        "decision": {"type": "string", "minLength": 1},
                        "payload": {"type": "object"},
                    },
                    "required": ["type", "payload"],
                    "additionalProperties": False,
                },
            },
            ["event"],
            additional_properties=False,
        ),
        capability_required="audit_event.record",
    ),
)

TOOL_REGISTRY = {tool.name: tool for tool in TOOL_SPECS}
_REGISTRY_SYNCED = False
_REGISTRY_SYNCED_DB = ""
_REGISTRY_ERROR = ""


def prepare_mcp_runtime(workspace_root: Path | str | None = None) -> None:
    global _REGISTRY_ERROR, _REGISTRY_SYNCED, _REGISTRY_SYNCED_DB
    from tradingcodex_service.application.runtime import tradingcodex_db_path

    current_db = str(tradingcodex_db_path())
    if _REGISTRY_SYNCED and (not current_db or _REGISTRY_SYNCED_DB == current_db):
        return
    try:
        if workspace_root is not None:
            from tradingcodex_service.application.runtime import ensure_runtime_database, workspace_file_lock

            ensure_runtime_database(workspace_root)
            with workspace_file_lock(workspace_root, "mcp-registry"):
                sync_mcp_tool_definitions()
        else:
            sync_mcp_tool_definitions()
        _REGISTRY_SYNCED = True
        _REGISTRY_SYNCED_DB = current_db
        _REGISTRY_ERROR = ""
    except Exception as exc:
        _REGISTRY_SYNCED = False
        _REGISTRY_ERROR = f"mcp_registry_unavailable:{type(exc).__name__}"


def list_mcp_tools() -> list[dict[str, Any]]:
    return [tool.public_definition() for tool in visible_tool_specs() if tool_enabled(tool.name)]


def static_mcp_tools() -> list[dict[str, Any]]:
    return [tool.public_definition() for tool in TOOL_SPECS]


def visible_tool_specs() -> tuple[McpToolSpec, ...]:
    if safe_home_mcp_scope():
        return tuple(tool for tool in TOOL_SPECS if tool.name in SAFE_HOME_TOOL_NAMES)
    return TOOL_SPECS


def safe_home_mcp_scope() -> bool:
    import os

    return os.environ.get("TRADINGCODEX_MCP_SAFE_TOOLS", "").lower() in {"1", "true", "yes", "on"}


def sync_mcp_tool_definitions() -> None:
    from apps.mcp.models import McpToolDefinition
    from apps.policy.services import sync_builtin_principals_and_capabilities

    McpToolDefinition.objects.filter(name__in=RETIRED_PUBLIC_MCP_TOOLS).delete()
    for tool in TOOL_SPECS:
        existing = McpToolDefinition.objects.filter(name=tool.name).first()
        McpToolDefinition.objects.update_or_create(
            name=tool.name,
            defaults={
                "description": tool.description,
                "category": tool.category,
                "capability_required": tool.capability_required,
                "input_schema": tool.input_schema,
                "risk_level": tool.risk_level,
                "allowed_roles": sorted(tool.allowed_roles),
                "requires_approval": tool.requires_approval,
                "audit_required": tool.audit_required,
                "experimental": tool.experimental,
                "enabled": existing.enabled if existing is not None else True,
            },
        )
    sync_builtin_principals_and_capabilities(TOOL_SPECS)


def tool_enabled(name: str) -> bool:
    if _REGISTRY_ERROR:
        return name in REGISTRY_FAILURE_SAFE_READ_TOOLS
    try:
        from apps.mcp.models import McpToolDefinition

        configured = McpToolDefinition.objects.filter(name=name).first()
        if configured is not None:
            return configured.enabled
    except Exception:
        return name in REGISTRY_FAILURE_SAFE_READ_TOOLS
    return True


def role_for_principal(principal_id: str) -> str:
    try:
        from apps.policy.services import role_for_principal_id

        return role_for_principal_id(principal_id)
    except Exception:
        known = {"head-manager", "user", *AGENT_SPECS}
        return principal_id if principal_id in known else "unknown"


def call_mcp_tool(
    workspace_root: Path | str,
    name: str,
    args: dict[str, Any] | None = None,
    *,
    transport_principal: str | None = None,
) -> dict[str, Any]:
    args = dict(args or {})
    internal_context: dict[str, Any] = {}
    if name == ORDER_TURN_GRANT_TOOL:
        internal_context["execution_turn_proof"] = str(args.pop(_ORDER_TURN_GRANT_PROOF_FIELD, "") or "")
    if name in WORKSPACE_PROTECTED_MCP_TOOLS:
        internal_context["build_turn_proof"] = str(args.pop(_BUILD_TURN_PROOF_FIELD, "") or "")
    build_grant_id = ""
    if name in WORKSPACE_PROTECTED_MCP_TOOLS:
        build_grant_id = str(
            begin_reserved_build_turn_use(
                workspace_root,
                name,
                args,
                internal_context.get("build_turn_proof", ""),
            )
        )
    started = time.monotonic()
    principal_id = str(transport_principal or "unknown")
    request_payload: dict[str, Any] = {"tool_name": name, "arguments": args}
    raw_succeeded = False
    try:
        prepare_mcp_runtime(workspace_root)
        tool = TOOL_REGISTRY.get(name)
        if tool is None:
            raise ValueError(f"Unknown TradingCodex tool: {name}")
        if _REGISTRY_ERROR and name not in REGISTRY_FAILURE_SAFE_READ_TOOLS:
            raise PermissionError(f"MCP registry unavailable; fail-closed for {name} ({_REGISTRY_ERROR})")
        if safe_home_mcp_scope() and name not in SAFE_HOME_TOOL_NAMES:
            raise PermissionError(f"MCP tool is not available in tradingcodex-home safe scope: {name}")
        if not tool_enabled(name):
            raise PermissionError(f"MCP tool is disabled: {name}")
        claimed_principal = str(args.pop("principal_id", "") or "")
        if transport_principal is not None:
            principal_id = str(transport_principal or "")
            if not principal_id:
                raise PermissionError("authenticated MCP transport principal is required")
            if claimed_principal and claimed_principal != principal_id:
                raise PermissionError("payload principal does not match the authenticated MCP transport principal")
        else:
            # Direct in-process callers are trusted service code. Network and stdio
            # transports always pass transport_principal and cannot use this path.
            principal_id = claimed_principal or default_principal_for_tool(tool)
        role = role_for_principal(principal_id)
        if role not in tool.allowed_roles:
            raise PermissionError(f"{principal_id} is not allowed to call {name}")
        try:
            from apps.policy.services import capability_check, tool_capability_action

            action = tool_capability_action(tool.name, tool.capability_required)
            capability_allowed, capability_reasons = capability_check(principal_id, action, args.get("resource"))
        except Exception as exc:
            if not (_REGISTRY_ERROR and name in REGISTRY_FAILURE_SAFE_READ_TOOLS):
                raise PermissionError("canonical capability state unavailable; MCP call denied") from exc
        else:
            if not capability_allowed:
                raise PermissionError("; ".join(capability_reasons))
        validate_input_schema(tool, args)
        request_payload.update(
            {
                "arguments": args,
                "principal_id": principal_id,
                "claimed_principal": claimed_principal or None,
            }
        )
        result = raw_call_tool(
            workspace_root,
            tool,
            args,
            principal_id,
            internal_context=internal_context,
        )
        raw_succeeded = True
        if isinstance(result, dict):
            from tradingcodex_service.application.runtime import persist_workspace_context_if_available

            result = dict(result)
            result.setdefault("db_canonical", True)
            result.setdefault("workspace_context", persist_workspace_context_if_available(workspace_root))
        if build_grant_id:
            finish_reserved_build_turn_use(workspace_root, build_grant_id, "ok")
    except Exception as exc:
        surfaced_exc: Exception = exc
        surface_cause: Exception | None = None
        if build_grant_id and not raw_succeeded:
            try:
                finish_reserved_build_turn_use(workspace_root, build_grant_id, "error")
            except Exception as finish_exc:
                try:
                    fail_closed_finalize_started_build_turn_use(
                        workspace_root,
                        build_grant_id,
                        "error",
                    )
                except Exception as recovery_exc:
                    surfaced_exc = PermissionError(
                        "turn-protected workspace call failed, and its grant finalization and fail-closed recovery failed"
                    )
                    surface_cause = recovery_exc
                else:
                    surfaced_exc = PermissionError(
                        "turn-protected workspace call failed and normal grant finalization failed; "
                        "the grant was revoked fail-closed"
                    )
                    surface_cause = finish_exc
        elif build_grant_id and raw_succeeded:
            try:
                fail_closed_finalize_started_build_turn_use(
                    workspace_root,
                    build_grant_id,
                    "ok",
                )
            except Exception as recovery_exc:
                surfaced_exc = PermissionError(
                    "turn-protected workspace operation completed, but its grant finalization and fail-closed recovery failed; "
                    "do not retry the operation blindly"
                )
                surface_cause = recovery_exc
            else:
                surfaced_exc = PermissionError(
                    "turn-protected workspace operation completed, but normal grant finalization failed; "
                    "the grant was revoked fail-closed and the operation must not be retried blindly"
                )
                surface_cause = exc
        error = {"status": "error", "error": str(surfaced_exc), "tool_name": name}
        record_tool_call(
            workspace_root,
            name,
            principal_id,
            "error",
            request_payload,
            error,
            started,
            str(surfaced_exc),
        )
        if surfaced_exc is exc:
            raise
        raise surfaced_exc from surface_cause
    record_tool_call(workspace_root, name, principal_id, "ok", request_payload, result, started)
    return result


def _require_only_action_args(args: Mapping[str, Any], allowed: set[str]) -> None:
    unexpected = sorted(set(args) - {"action", *allowed})
    if unexpected:
        raise ValueError(f"management action received unsupported fields: {', '.join(unexpected)}")


def _read_root_strategy_body(workspace_root: Path | str, raw_path: str) -> str:
    root = Path(workspace_root).resolve()
    supplied = Path(raw_path)
    if supplied.is_absolute() or supplied.name != raw_path or raw_path in {"", ".", ".."}:
        raise ValueError("Strategy body_path must be one root-level workspace filename")
    candidate = root / supplied
    if candidate.is_symlink():
        raise ValueError("Strategy body_path must identify one regular root-level workspace file")
    path = candidate.resolve(strict=False)
    if path.parent != root or not path.is_file():
        raise ValueError("Strategy body_path must identify one regular root-level workspace file")
    if path.stat().st_size > 256 * 1024:
        raise ValueError("Strategy body_path exceeds 256 KiB")
    return path.read_text(encoding="utf-8")


def _managed_brain_sources(
    workspace_root: Path | str,
    args: Mapping[str, Any],
) -> tuple[Path | None, str | None]:
    raw_local = str(args.get("local_source") or "")
    raw_git = str(args.get("git_source") or "")
    if bool(raw_local) == bool(raw_git):
        raise ValueError("select exactly one of local_source or git_source")
    if raw_git:
        try:
            parsed = urlsplit(raw_git)
            _ = parsed.port
        except ValueError as exc:
            raise ValueError("git_source must be a valid public credential-free HTTPS URL") from exc
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("git_source must be a public credential-free HTTPS URL without query or fragment")
        from tradingcodex_service.application.investment_brains import (
            validate_public_investment_brain_repository_url,
        )

        validate_public_investment_brain_repository_url(raw_git)
        return None, raw_git
    root = Path(workspace_root).resolve()
    source_root = root / "investment-brains"
    supplied = Path(raw_local)
    try:
        lexical = supplied.relative_to(root) if supplied.is_absolute() else supplied
        resolved = supplied.resolve(strict=False) if supplied.is_absolute() else (root / supplied).resolve(strict=False)
        resolved.relative_to(source_root.resolve(strict=False))
    except (OSError, RuntimeError, ValueError) as exc:
        raise ValueError("local_source must stay below investment-brains/") from exc
    if lexical.parts[:1] != ("investment-brains",) or any(part in {"", ".", ".."} for part in lexical.parts):
        raise ValueError("local_source must be a canonical path below investment-brains/")
    current = root
    for part in lexical.parts:
        current /= part
        if current.is_symlink():
            raise ValueError("local_source cannot traverse a symlink")
    if not resolved.is_dir():
        raise ValueError("local_source must identify an existing Investment Brain directory")
    return resolved, None


def raw_call_tool(
    workspace_root: Path | str,
    tool: McpToolSpec,
    args: dict[str, Any],
    principal_id: str,
    *,
    internal_context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    from tradingcodex_service.application import (
        agents,
        analysis_runs,
        artifact_catalog,
        artifact_bindings,
        audit,
        brokers,
        calculations,
        codex_capabilities,
        datasets,
        evaluation_lab,
        execution_gateway,
        forecasting,
        investment_analysis,
        investment_brains,
        orders,
        policy,
        portfolio,
        postmortems,
        research,
        research_specs,
    )
    def get_tradingcodex_status() -> dict[str, Any]:
        from tradingcodex_service.application.runtime import persist_workspace_context_if_available, tradingcodex_db_path
        from tradingcodex_service.version import TRADINGCODEX_VERSION

        return {
            "status": "ok",
            "service": "tradingcodex",
            "version": TRADINGCODEX_VERSION,
            "db_path": str(tradingcodex_db_path()),
            "workspace_context": persist_workspace_context_if_available(workspace_root),
            "mcp_scope": "global-home" if safe_home_mcp_scope() else "project-scoped",
            "mcp_registry": {
                "status": "degraded" if _REGISTRY_ERROR else "ready",
                "reason_code": _REGISTRY_ERROR,
                "safe_read_subset": sorted(REGISTRY_FAILURE_SAFE_READ_TOOLS) if _REGISTRY_ERROR else [],
            },
        }

    def get_runtime_mode() -> dict[str, Any]:
        from tradingcodex_service.application.runtime_mode import get_runtime_mode_status

        return get_runtime_mode_status(workspace_root)

    def get_update_status() -> dict[str, Any]:
        from tradingcodex_cli.startup_status import build_update_status

        return build_update_status(workspace_root, check_latest_release=True)

    def get_authorized_research_artifact() -> dict[str, Any]:
        artifact = research.get_research_artifact(workspace_root, args)
        workflow_run_id = str(artifact.get("workflow_run_id") or "")
        if workflow_run_id and not analysis_runs.read_analysis_run(workspace_root, workflow_run_id):
            raise PermissionError("research artifact does not belong to a recorded analysis run")
        if workflow_run_id:
            artifact_bindings.verify_authenticated_artifact_binding(workspace_root, artifact)
        return artifact

    def store_authenticated_research_artifact(*, append: bool = False) -> dict[str, Any]:
        bound_args = _principal_bound_research_args(
            workspace_root,
            args,
            principal_id,
            append=append,
        )
        authorized_args = research.authenticated_service_research_args(bound_args)
        return research.store_authenticated_research_artifact(
            workspace_root,
            authorized_args,
            append=append,
        )

    def begin_agent_analysis_run() -> dict[str, Any]:
        bound_run_id = str(os.environ.get("TRADINGCODEX_WORKFLOW_RUN_ID") or "").strip()
        requested_run_id = str(args.get("workflow_run_id") or "").strip()
        if bound_run_id and requested_run_id and requested_run_id != bound_run_id:
            raise ValueError("workflow_run_id does not match the bound Codex run")
        run_id = bound_run_id or requested_run_id
        apply_context_raw = str(os.environ.get("TRADINGCODEX_WORKFLOW_APPLY_INVESTOR_CONTEXT") or "").lower()
        apply_context = None if not apply_context_raw else apply_context_raw in {"1", "true", "yes", "on"}
        return analysis_runs.begin_analysis_run(
            workspace_root,
            str(args["request"]),
            run_id=run_id,
            strategy_id=str(os.environ.get("TRADINGCODEX_WORKFLOW_STRATEGY_ID") or ""),
            apply_investor_context=apply_context,
        )

    def manage_strategy() -> dict[str, Any]:
        action = str(args["action"])
        if action == "list":
            _require_only_action_args(args, {"active_only"})
            return {
                "action": action,
                "records": agents.read_strategy_skill_records(
                    workspace_root,
                    active_only=bool(args.get("active_only")),
                ),
            }
        name = str(args.get("name") or "")
        if not name:
            raise ValueError(f"manage_strategy {action} requires name")
        if action == "inspect":
            _require_only_action_args(args, {"name"})
            return {"action": action, "record": agents.get_strategy_skill_record(workspace_root, name)}
        if action in {"create", "update"}:
            _require_only_action_args(
                args,
                {"name", "description", "body_path", "language", "status"},
            )
            body_path = str(args.get("body_path") or "")
            if not body_path:
                raise ValueError(f"manage_strategy {action} requires body_path")
            if action == "create":
                try:
                    agents.get_strategy_skill_record(workspace_root, name)
                except ValueError:
                    pass
                else:
                    raise ValueError(f"strategy already exists; use update: {name}")
            else:
                agents.get_strategy_skill_record(workspace_root, name)
            status = str(args.get("status") or "")
            if not status:
                raise ValueError(f"manage_strategy {action} requires explicit status")
            record = agents.create_or_update_strategy_skill(
                workspace_root,
                name,
                description=str(args.get("description") or ""),
                body=_read_root_strategy_body(workspace_root, body_path),
                language=str(args.get("language") or "unknown"),
                status=status,
                actor=principal_id,
            )
            return {"action": action, "record": record}
        if action in {"activate", "archive"}:
            _require_only_action_args(args, {"name"})
            record = agents.set_strategy_skill_status(
                workspace_root,
                name,
                "active" if action == "activate" else "archived",
                actor=principal_id,
            )
            return {"action": action, "record": record}
        if action == "delete":
            _require_only_action_args(args, {"name", "force"})
            return {
                "action": action,
                "record": agents.delete_strategy_skill(
                    workspace_root,
                    name,
                    force=bool(args.get("force")),
                    actor=principal_id,
                ),
            }
        raise ValueError(f"unsupported Strategy management action: {action}")

    def manage_investment_brain() -> dict[str, Any]:
        action = str(args["action"])
        if action == "list":
            _require_only_action_args(args, {"active_only"})
            records = investment_brains.read_investment_brain_records(
                workspace_root,
                include_removed=not bool(args.get("active_only")),
            )
            if args.get("active_only"):
                records = [record for record in records if record.get("status") == "active"]
            return {"action": action, "records": records}
        brain_id = str(args.get("brain_id") or "")
        if action == "inspect":
            _require_only_action_args(args, {"brain_id"})
            if not brain_id:
                raise ValueError("manage_investment_brain inspect requires brain_id")
            return {
                "action": action,
                "record": investment_brains.get_investment_brain_record(workspace_root, brain_id),
            }
        if action in {"validate", "install"}:
            _require_only_action_args(args, {"local_source", "git_source", "ref"})
            local_source, git_source = _managed_brain_sources(workspace_root, args)
            operation = (
                investment_brains.validate_investment_brain_source(
                    workspace_root,
                    local_source=local_source,
                    git_source=git_source,
                    ref=str(args.get("ref") or ""),
                )
                if action == "validate"
                else investment_brains.install_investment_brain(
                    workspace_root,
                    local_source=local_source,
                    git_source=git_source,
                    ref=str(args.get("ref") or ""),
                    active=False,
                    actor=principal_id,
                )
            )
            return {"action": action, "record": operation}
        if not brain_id:
            raise ValueError(f"manage_investment_brain {action} requires brain_id")
        if action == "update":
            _require_only_action_args(args, {"brain_id", "local_source", "git_source", "ref"})
            local_source = None
            git_source = None
            if args.get("local_source") or args.get("git_source"):
                local_source, git_source = _managed_brain_sources(workspace_root, args)
            record = investment_brains.update_investment_brain(
                workspace_root,
                brain_id,
                local_source=local_source,
                git_source=git_source,
                ref=str(args["ref"]) if "ref" in args else None,
                actor=principal_id,
            )
            return {"action": action, "record": record}
        if action in {"activate", "deactivate"}:
            _require_only_action_args(args, {"brain_id"})
            record = investment_brains.set_investment_brain_status(
                workspace_root,
                brain_id,
                "active" if action == "activate" else "inactive",
                actor=principal_id,
            )
            return {"action": action, "record": record}
        if action == "rollback":
            _require_only_action_args(args, {"brain_id", "version"})
            return {
                "action": action,
                "record": investment_brains.rollback_investment_brain(
                    workspace_root,
                    brain_id,
                    version=str(args.get("version") or ""),
                    actor=principal_id,
                ),
            }
        if action == "remove":
            _require_only_action_args(args, {"brain_id"})
            return {
                "action": action,
                "record": investment_brains.remove_investment_brain(
                    workspace_root,
                    brain_id,
                    actor=principal_id,
                ),
            }
        raise ValueError(f"unsupported Investment Brain management action: {action}")

    with_principal = {**args, "principal_id": principal_id}
    internal_context = dict(internal_context or {})
    handlers: dict[str, Callable[[], dict[str, Any]]] = {
        "get_tradingcodex_status": get_tradingcodex_status,
        "list_codex_capabilities": lambda: codex_capabilities.list_codex_capabilities(workspace_root),
        "get_runtime_mode": get_runtime_mode,
        "get_update_status": get_update_status,
        "manage_strategy": manage_strategy,
        "manage_investment_brain": manage_investment_brain,
        "begin_analysis_run": begin_agent_analysis_run,
        "simulate_policy": lambda: policy.simulate_policy(workspace_root, with_principal),
        "validate_approval_receipt": lambda: orders.validate_approval_receipt(workspace_root, with_principal),
        "discard_draft_order": lambda: orders.discard_draft_order(workspace_root, with_principal),
        "get_order_status": lambda: orders.get_order_status(workspace_root, args),
        "list_positions": lambda: portfolio.list_positions(workspace_root),
        "list_broker_connections": lambda: brokers.list_broker_connections(workspace_root, args),
        "get_broker_connection_status": lambda: brokers.get_broker_connection_status(workspace_root, args),
        "list_broker_adapter_providers": lambda: brokers.list_broker_adapter_providers(workspace_root, args),
        "register_broker_connector": lambda: brokers.register_broker_connector(workspace_root, with_principal),
        "render_broker_connector_scaffold": lambda: brokers.render_broker_connector_scaffold(workspace_root, with_principal),
        "validate_broker_connector_build": lambda: brokers.validate_broker_connector_build(workspace_root, with_principal),
        "get_broker_capability_profile": lambda: brokers.get_broker_capability_profile(workspace_root, args),
        "get_broker_instrument_constraints": lambda: brokers.get_broker_instrument_constraints(workspace_root, args),
        "preview_order_translation": lambda: brokers.preview_order_translation(workspace_root, with_principal),
        "get_connector_build_status": lambda: brokers.get_connector_build_status(workspace_root, args),
        "sync_broker_account": lambda: brokers.sync_broker_account(workspace_root, with_principal),
        "list_reconciliation_runs": lambda: brokers.list_reconciliation_runs(workspace_root, args),
        "create_order_ticket": lambda: orders.create_order_ticket(workspace_root, with_principal),
        "run_order_checks": lambda: orders.run_order_checks(workspace_root, with_principal),
        "request_order_approval": lambda: orders.request_order_approval(workspace_root, with_principal),
        "get_order_ticket": lambda: orders.get_order_ticket(workspace_root, args),
        "list_order_tickets": lambda: orders.list_order_tickets(workspace_root, args),
        ORDER_TURN_GRANT_TOOL: lambda: execution_gateway.execute_reserved_order_turn_grant(
            workspace_root,
            args,
            internal_context.get("execution_turn_proof", ""),
        ),
        "list_workflow_artifacts": lambda: research.list_workflow_artifacts(workspace_root),
        "create_research_artifact": store_authenticated_research_artifact,
        "append_research_artifact_version": lambda: store_authenticated_research_artifact(append=True),
        "get_research_artifact": get_authorized_research_artifact,
        "list_research_artifacts": lambda: research.list_research_artifacts(workspace_root, args),
        "search_research_artifacts": lambda: research.search_research_artifacts(workspace_root, args),
        "list_artifact_catalog": lambda: artifact_catalog.list_artifact_catalog(workspace_root, args),
        "search_artifact_catalog": lambda: artifact_catalog.search_artifact_catalog(workspace_root, args),
        "export_research_artifact_md": lambda: research.export_research_artifact_md(workspace_root, args),
        "record_source_snapshot": lambda: research.record_source_snapshot(
            workspace_root,
            {**args, "principal_id": principal_id},
        ),
        "search_datasets": lambda: datasets.search_datasets(workspace_root, args),
        "get_dataset_manifest": lambda: datasets.get_dataset_manifest(workspace_root, args),
        "profile_dataset": lambda: datasets.profile_dataset(workspace_root, args),
        "record_dataset_snapshot": lambda: datasets.record_dataset_snapshot(
            workspace_root,
            {**args, "principal_id": principal_id},
        ),
        "materialize_dataset_slice": lambda: datasets.materialize_dataset_slice(workspace_root, args),
        "search_calculations": lambda: calculations.search_calculations(workspace_root, args),
        "get_calculation_run": lambda: calculations.get_calculation_run(workspace_root, args),
        "compare_calculation_runs": lambda: calculations.compare_calculation_runs(workspace_root, args),
        "prepare_calculation": lambda: calculations.prepare_calculation(
            workspace_root,
            {**args, "principal_id": principal_id},
        ),
        "record_calculation_run": lambda: calculations.record_calculation_run(
            workspace_root,
            {**args, "principal_id": principal_id},
        ),
        "create_research_spec": lambda: research_specs.create_research_spec(workspace_root, {**args, "created_by": principal_id}),
        "get_research_spec": lambda: research_specs.get_research_spec(workspace_root, args),
        "list_research_specs": lambda: research_specs.list_research_specs(workspace_root),
        "create_replay_manifest": lambda: research_specs.create_replay_manifest(workspace_root, {**args, "created_by": principal_id}),
        "record_experiment_run": lambda: research_specs.record_experiment_run(workspace_root, {**args, "created_by": principal_id}),
        "rebuild_research_index": lambda: research.rebuild_research_index(workspace_root),
        "rebuild_artifact_catalog": lambda: artifact_catalog.rebuild_artifact_catalog(workspace_root),
        "create_causal_equity_analysis": lambda: investment_analysis.create_causal_equity_analysis(workspace_root, {**args, "created_by": principal_id}),
        "record_blind_judgment_prior": lambda: investment_analysis.record_blind_judgment_prior(workspace_root, {**args, "reviewer": principal_id}),
        "complete_judgment_review": lambda: investment_analysis.complete_judgment_review(workspace_root, {**args, "reviewer": principal_id}),
        "issue_forecast": lambda: forecasting.issue_forecast(workspace_root, {**args, "role": principal_id, "author": principal_id}),
        "revise_forecast": lambda: forecasting.revise_forecast(workspace_root, {**args, "author": principal_id}),
        "resolve_forecast": lambda: forecasting.resolve_forecast(workspace_root, {**args, "resolver": principal_id}),
        "score_forecast": lambda: forecasting.score_forecast(workspace_root, args),
        "promote_lesson": lambda: postmortems.promote_lesson(
            workspace_root,
            args,
            authenticated_principal=principal_id,
        ),
        "get_forecast": lambda: forecasting.get_forecast(workspace_root, args),
        "list_forecasts": lambda: forecasting.list_forecasts(workspace_root, args),
        "get_forecast_calibration_report": lambda: forecasting.calibration_report(workspace_root, args),
        "create_evaluation_corpus": lambda: evaluation_lab.create_evaluation_corpus(workspace_root, {**args, "created_by": principal_id}),
        "record_evaluation_run": lambda: evaluation_lab.record_evaluation_run(workspace_root, {**args, "created_by": principal_id}),
        "create_blind_review_assignment": lambda: evaluation_lab.create_blind_review_assignment(workspace_root, {**args, "assigned_by": principal_id}),
        "get_blind_review_packet": lambda: evaluation_lab.get_blind_review_packet(workspace_root, {**args, "reviewer": principal_id}),
        "record_blind_human_review": lambda: evaluation_lab.record_blind_human_review(workspace_root, {**args, "reviewer": principal_id}),
        "compare_evaluation_runs": lambda: evaluation_lab.compare_evaluation_runs(workspace_root, args),
        "record_audit_event": lambda: audit.write_audit_event(workspace_root, args["event"], principal_id, "mcp"),
    }
    handler = handlers.get(tool.handler_name)
    if handler is None:
        raise ValueError(f"Unknown TradingCodex tool handler: {tool.handler_name}")
    return handler()


def default_principal_for_tool(tool: McpToolSpec) -> str:
    if "risk-manager" in tool.allowed_roles and tool.category == "approvals":
        return "risk-manager"
    if "head-manager" in tool.allowed_roles:
        return "head-manager"
    return sorted(tool.allowed_roles)[0]


def _canonical_analysis_artifact_binding(
    workspace_root: Path | str,
    workflow_run_id: str,
    args: dict[str, Any],
    metadata: dict[str, Any],
    source: dict[str, Any],
    existing: dict[str, Any],
) -> dict[str, Any]:
    from tradingcodex_service.application.analysis_runs import read_analysis_run
    from tradingcodex_service.application.artifact_bindings import verify_authenticated_artifact_binding
    from tradingcodex_service.application.research import (
        find_workspace_research_artifact_read_only,
    )

    run = read_analysis_run(workspace_root, workflow_run_id)
    if not run or str(run.get("workflow_run_id") or "") != workflow_run_id:
        raise ValueError("research artifact workflow_run_id does not identify a recorded analysis run")
    sources = (args, metadata, source, existing)
    lineage = _analysis_run_artifact_lineage(run)
    for field, expected in lineage.items():
        for supplied in _research_values(field, *sources):
            if supplied != expected:
                raise ValueError(f"research artifact {field} is service-derived from the analysis run")
    input_ids: list[str] = []
    for supplied in _research_values("input_artifact_ids", *sources):
        if not isinstance(supplied, list):
            raise ValueError("input_artifact_ids must be an array")
        values = [str(value) for value in supplied if value]
        if input_ids and values != input_ids:
            raise ValueError("research artifact input_artifact_ids inputs disagree")
        input_ids = values
    if len(input_ids) != len(set(input_ids)):
        raise ValueError("input_artifact_ids must not contain duplicates")
    hashes: dict[str, str] = {}
    for artifact_id in input_ids:
        artifact = find_workspace_research_artifact_read_only(
            Path(workspace_root),
            artifact_id,
        )
        if not artifact:
            raise ValueError(f"input research artifact does not exist: {artifact_id}")
        verify_authenticated_artifact_binding(workspace_root, artifact)
        if str(artifact.get("workflow_run_id") or "") != workflow_run_id:
            raise ValueError(f"input research artifact belongs to another analysis run: {artifact_id}")
        if str(artifact.get("handoff_state") or "") != "accepted":
            raise ValueError(f"input research artifact is not an accepted handoff: {artifact_id}")
        content_hash = str(artifact.get("content_hash") or "")
        if not content_hash:
            raise ValueError(f"input research artifact has no content hash: {artifact_id}")
        for field, expected in lineage.items():
            if artifact.get(field) != expected:
                raise ValueError(f"input research artifact has different run provenance: {artifact_id}")
        hashes[artifact_id] = content_hash
    for supplied in _research_values("input_artifact_hashes", *sources):
        if supplied != hashes:
            raise ValueError("input_artifact_hashes are service-derived from input_artifact_ids")
    return {
        "workflow_run_id": workflow_run_id,
        "artifact_schema_version": 1,
        "input_artifact_ids": input_ids,
        "input_artifact_hashes": hashes,
        **lineage,
    }


def _analysis_run_artifact_lineage(run: dict[str, Any]) -> dict[str, Any]:
    strategy = run.get("strategy_binding") if isinstance(run.get("strategy_binding"), dict) else {}
    context = run.get("investor_context_binding") if isinstance(run.get("investor_context_binding"), dict) else {}
    brain = run.get("investment_brain_binding") if isinstance(run.get("investment_brain_binding"), dict) else {}
    context_applied = bool(context.get("applied"))
    return {
        "strategy_name": str(strategy.get("strategy_id") or ""),
        "strategy_hash": str(strategy.get("content_hash") or ""),
        "investment_brain_id": str(brain.get("brain_id") or ""),
        "investment_brain_version": str(brain.get("version") or ""),
        "investment_brain_content_digest": str(brain.get("content_digest") or ""),
        "investor_context_applied": context_applied,
        "investor_context_hash": str(context.get("content_hash") or "") if context_applied else "",
    }


def _research_values(field: str, *sources: dict[str, Any]) -> list[Any]:
    return [source[field] for source in sources if isinstance(source, dict) and source.get(field) not in (None, "")]


def _principal_bound_research_args(
    workspace_root: Path | str,
    args: dict[str, Any],
    principal_id: str,
    *,
    append: bool = False,
) -> dict[str, Any]:
    from tradingcodex_service.application.common import safe_workspace_path
    from tradingcodex_service.application.markdown_preview import split_markdown_frontmatter
    from tradingcodex_service.application.artifact_bindings import (
        verify_current_artifact_binding_before_append,
    )
    from tradingcodex_service.application.research import (
        RESEARCH_FILE_ROOTS,
        find_workspace_research_artifact_read_only,
        validate_research_artifact_path_declarations,
    )

    role = role_for_principal(principal_id)
    if role not in AGENT_SPECS:
        raise PermissionError("research artifacts require an authenticated TradingCodex role")
    markdown = args.get("markdown")
    if not markdown and args.get("markdown_path"):
        markdown = safe_workspace_path(
            workspace_root,
            str(args["markdown_path"]),
            allowed_roots=RESEARCH_FILE_ROOTS,
        ).read_text(encoding="utf-8")
    source = split_markdown_frontmatter(str(markdown or "")).frontmatter
    metadata = args.get("metadata") if isinstance(args.get("metadata"), dict) else {}
    existing: dict[str, Any] = {}
    if append:
        existing = find_workspace_research_artifact_read_only(
            workspace_root,
            str(args.get("artifact_id") or ""),
        ) or {}
        if existing:
            verify_current_artifact_binding_before_append(
                workspace_root,
                existing,
            )
            canonical_path = str(
                existing.get("path") or existing.get("export_path") or ""
            )
            if not canonical_path:
                raise ValueError("existing research artifact has no canonical path")
            validate_research_artifact_path_declarations(
                canonical_path,
                args,
                metadata,
                source,
                operation="append",
            )
    artifact_type = str(
        args.get("artifact_type")
        or source.get("artifact_type")
        or existing.get("artifact_type")
        or "research_memo"
    )
    if "created_by" in args:
        raise ValueError("research artifact created_by is derived from the authenticated MCP principal")
    expected = {"role": role, "producer_role": role, "created_by": principal_id}
    for field, expected_value in expected.items():
        supplied = [args.get(field), metadata.get(field), source.get(field)]
        if append:
            supplied.append(existing.get(field))
        if any(str(value) != expected_value for value in supplied if value not in (None, "")):
            raise PermissionError(f"research artifact {field} must match the authenticated MCP principal")
    if role == "head-manager" and artifact_type != "synthesis_report":
        raise PermissionError("head-manager may create only synthesis_report research artifacts")
    if role != "head-manager" and artifact_type == "synthesis_report":
        raise PermissionError("only head-manager may create synthesis_report research artifacts")
    retired_bindings = ("plan_hash", "stage_id", "task_id")
    if any(_research_values(field, args, metadata, source, existing) for field in retired_bindings):
        raise ValueError("analysis artifacts do not accept plan_hash, stage_id, or task_id bindings")
    canonical_args = {
        key: value
        for key, value in args.items()
        if key not in {"content_hash", "created_by", "producer_role", "recorded_at", "role", "version", "workspace_native"}
    }
    if append and existing:
        canonical_args.pop("path", None)
        canonical_args["export_path"] = str(existing["path"])
    workflow_values = _research_values("workflow_run_id", args, metadata, source, existing)
    if len({str(value) for value in workflow_values}) > 1:
        raise ValueError("research artifact workflow_run_id inputs disagree")
    if role == "head-manager" and not workflow_values:
        raise ValueError("head-manager synthesis requires a canonical workflow_run_id")
    workflow_binding = {}
    if workflow_values:
        workflow_binding = _canonical_analysis_artifact_binding(
            workspace_root,
            str(workflow_values[0]),
            args,
            metadata,
            source,
            existing,
        )
    if role == "head-manager" and not workflow_binding.get("input_artifact_hashes"):
        raise ValueError("head-manager synthesis requires at least one run-local input_artifact_id")
    return {
        **canonical_args,
        **workflow_binding,
        "artifact_type": artifact_type,
        "principal_id": principal_id,
        "role": role,
        "producer_role": role,
        "metadata": {**metadata, **expected},
    }


def validate_input_schema(tool: McpToolSpec, args: dict[str, Any]) -> None:
    _validate_schema_value(tool.input_schema, args, tool.name)


def _validate_schema_value(schema: dict[str, Any], value: Any, path: str) -> None:
    expected_type = schema.get("type")
    if expected_type:
        _validate_schema_type(expected_type, value, path)
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError(f"{path} must be one of: {', '.join(map(str, schema['enum']))}")
    if isinstance(value, dict):
        properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
        for field in schema.get("required", []):
            if value.get(field) in (None, ""):
                raise ValueError(f"{path} requires {field}")
        if schema.get("additionalProperties") is False:
            extra = sorted(set(value) - set(properties))
            if extra:
                raise ValueError(f"{path} does not allow additional properties: {', '.join(extra)}")
        for field, child_schema in properties.items():
            if field in value and value[field] is not None:
                _validate_schema_value(child_schema, value[field], f"{path}.{field}")
    if isinstance(value, list) and isinstance(schema.get("items"), dict):
        for index, item in enumerate(value):
            _validate_schema_value(schema["items"], item, f"{path}[{index}]")
    if isinstance(value, str):
        if "minLength" in schema and len(value) < int(schema["minLength"]):
            raise ValueError(f"{path} is too short")
        if "maxLength" in schema and len(value) > int(schema["maxLength"]):
            raise ValueError(f"{path} is too long")
    if _is_number(value):
        number = float(value)
        if "minimum" in schema and number < float(schema["minimum"]):
            raise ValueError(f"{path} must be >= {schema['minimum']}")
        if "exclusiveMinimum" in schema and number <= float(schema["exclusiveMinimum"]):
            raise ValueError(f"{path} must be > {schema['exclusiveMinimum']}")
        if "maximum" in schema and number > float(schema["maximum"]):
            raise ValueError(f"{path} must be <= {schema['maximum']}")
        if "exclusiveMaximum" in schema and number >= float(schema["exclusiveMaximum"]):
            raise ValueError(f"{path} must be < {schema['exclusiveMaximum']}")


def _validate_schema_type(expected_type: str | list[str], value: Any, path: str) -> None:
    expected = expected_type if isinstance(expected_type, list) else [expected_type]
    if "object" in expected and isinstance(value, dict):
        return
    if "array" in expected and isinstance(value, list):
        return
    if "string" in expected and isinstance(value, str):
        return
    if "integer" in expected and isinstance(value, int) and not isinstance(value, bool):
        return
    if "number" in expected and _is_number(value):
        return
    if "boolean" in expected and isinstance(value, bool):
        return
    if "null" in expected and value is None:
        return
    raise ValueError(f"{path} must be {', '.join(expected)}")


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float, Decimal)) and not isinstance(value, bool)


def record_tool_call(
    workspace_root: Path | str | None,
    name: str,
    principal_id: str,
    status: str,
    request_payload: dict[str, Any],
    response_payload: dict[str, Any],
    started: float,
    error: str = "",
) -> None:
    if _skip_db_tool_call_ledger(name):
        return
    try:
        from apps.mcp.models import McpToolCall
        from apps.mcp.services import redact_sensitive_data
        from tradingcodex_service.application.common import stable_hash
        from tradingcodex_service.application.runtime import workspace_context_payload

        canonical_request = _json_field_safe(redact_sensitive_data(request_payload))
        persisted_request = _redact_tool_ledger_request(name, canonical_request)
        persisted_response = _json_field_safe(redact_sensitive_data(response_payload))
        persisted_error = redact_sensitive_data(error)
        McpToolCall.objects.create(
            tool_name=name,
            principal_id=principal_id,
            status=status,
            request=persisted_request,
            response=persisted_response,
            workspace_context=workspace_context_payload(workspace_root),
            request_hash=stable_hash(canonical_request),
            result_hash=stable_hash(persisted_response),
            error=persisted_error,
            duration_ms=max(0, int((time.monotonic() - started) * 1000)),
        )
    except Exception:
        return


def _redact_tool_ledger_request(name: str, request_payload: Any) -> Any:
    if name != "begin_analysis_run" or not isinstance(request_payload, dict):
        return request_payload
    persisted = dict(request_payload)
    arguments = dict(persisted.get("arguments") or {})
    request = arguments.pop("request", "")
    encoded = str(request).encode("utf-8")
    arguments.update({
        "request_sha256": hashlib.sha256(encoded).hexdigest(),
        "request_bytes": len(encoded),
    })
    persisted["arguments"] = arguments
    return persisted


def _json_field_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_field_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_field_safe(item) for item in value]
    if isinstance(value, Decimal):
        return str(value)
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return str(value)


def _skip_db_tool_call_ledger(name: str) -> bool:
    tool = TOOL_REGISTRY.get(name)
    if tool and tool.category in {"research", "evaluation"}:
        return True
    return name == "list_workflow_artifacts"


def handle_mcp_rpc(
    workspace_root: Path | str,
    message: dict[str, Any],
    *,
    transport_principal: str | None = None,
) -> dict[str, Any] | None:
    from tradingcodex_service.version import TRADINGCODEX_VERSION
    import os

    prepare_mcp_runtime(workspace_root)
    method = message.get("method")
    if method == "notifications/initialized":
        return None
    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": message.get("id"),
            "result": {
                "protocolVersion": "2025-06-18",
                "capabilities": {"tools": {"listChanged": False}, "resources": {}, "prompts": {}},
                "serverInfo": {"name": "tradingcodex-home" if safe_home_mcp_scope() else "tradingcodex", "version": TRADINGCODEX_VERSION},
                "instructions": "TradingCodex MCP is a Django service-layer gateway backed by workspace files for agent/skill/research state and the central local TradingCodex DB for runtime records. Codex projects are callers/provenance; research tools use workspace markdown; execution tools revalidate policy, approval, connection, duplicate-request status, and audit.",
            },
        }
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": message.get("id"), "result": {"tools": list_mcp_tools()}}
    if method == "resources/list":
        return {"jsonrpc": "2.0", "id": message.get("id"), "result": {"resources": []}}
    if method == "resources/templates/list":
        return {"jsonrpc": "2.0", "id": message.get("id"), "result": {"resourceTemplates": []}}
    if method == "prompts/list":
        return {"jsonrpc": "2.0", "id": message.get("id"), "result": {"prompts": []}}
    if method == "tools/call":
        try:
            params = message.get("params") or {}
            principal_id = transport_principal or os.environ.get("TRADINGCODEX_MCP_PRINCIPAL")
            if not principal_id:
                raise PermissionError("authenticated MCP transport principal is required")
            result = call_mcp_tool(
                workspace_root,
                params.get("name"),
                params.get("arguments") or {},
                transport_principal=principal_id,
            )
            return {"jsonrpc": "2.0", "id": message.get("id"), "result": {"content": [{"type": "text", "text": json.dumps(result, indent=2, ensure_ascii=False)}]}}
        except Exception as exc:
            return {"jsonrpc": "2.0", "id": message.get("id"), "error": {"code": -32000, "message": str(exc)}}
    return {"jsonrpc": "2.0", "id": message.get("id"), "error": {"code": -32601, "message": f"Method not found: {method}"}}


def handle_mcp_batch(workspace_root: Path | str, payload: Any) -> Any:
    if isinstance(payload, list):
        responses = [handle_mcp_rpc(workspace_root, item) for item in payload]
        return [response for response in responses if response is not None]
    return handle_mcp_rpc(workspace_root, payload)

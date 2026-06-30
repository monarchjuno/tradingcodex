from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


RESEARCH_ROLES = {
    "head-manager",
    "fundamental-analyst",
    "technical-analyst",
    "news-analyst",
    "macro-analyst",
    "instrument-analyst",
    "valuation-analyst",
    "portfolio-manager",
    "risk-manager",
}
POLICY_ROLES = {"head-manager", "portfolio-manager", "risk-manager", "execution-operator"}
PORTFOLIO_ROLES = {"head-manager", "portfolio-manager", "risk-manager", "execution-operator"}
APPROVAL_ROLES = {"risk-manager"}
EXECUTION_ROLES = {"execution-operator"}
SAFE_HOME_TOOL_NAMES = frozenset({
    "get_tradingcodex_status",
    "get_runtime_mode",
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
})


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
        return self.category in {"brokers", "execution", "external_mcp"}


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


APPROVAL_RECEIPT_SCHEMA = json_object_schema(
    {
        "id": {"type": "string", "minLength": 1, "maxLength": 180},
        "order_ticket_id": {"type": "string", "minLength": 1, "maxLength": 160},
        "approved_by": {"type": "string", "minLength": 1, "maxLength": 120},
        "valid": {"type": "boolean"},
        "created_at": {"type": "string", "minLength": 1, "maxLength": 80},
        "expires_at": {"type": "string", "minLength": 1, "maxLength": 80},
        "exact_order_hash": {"type": "string", "maxLength": 64},
        "broker_connection_id": {"type": "string", "maxLength": 120},
        "broker_account_id": {"type": "string", "maxLength": 160},
        "max_notional": {"type": "number", "minimum": 0},
        "max_price": {"type": "number", "minimum": 0},
        "max_slippage_bps": {"type": "integer", "minimum": 0},
        "approved_order_type": {"type": "string", "maxLength": 32},
        "approved_time_in_force": {"type": "string", "maxLength": 32},
        "valid_until": {"type": "string", "maxLength": 80},
        "quote_as_of_requirement": {"type": "string", "maxLength": 80},
        "policy_decision": {"type": "object"},
    },
    ["id", "order_ticket_id", "approved_by", "valid", "expires_at"],
    additional_properties=False,
)
RESEARCH_ARTIFACT_METADATA_FIELDS = {
    "role": {"type": "string"},
    "context_summary": {"type": "string"},
    "reader_summary": {"type": "string"},
    "handoff_state": {"type": "string", "enum": ["accepted", "revise", "blocked", "waiting"]},
    "confidence": {"type": "string"},
    "missing_evidence": {"type": "array"},
    "next_recipient": {"type": "string"},
    "next_action": {"type": "string"},
    "blocked_actions": {"type": "array"},
    "source_snapshot_ids": {"type": "array", "items": {"type": "string"}},
    "follow_up_requests": {"type": "array"},
}
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
        "currency": {"type": "string", "minLength": 1, "maxLength": 16},
        "broker_id": {"type": "string", "minLength": 1, "maxLength": 120},
        "broker_connection_id": {"type": "string", "minLength": 1, "maxLength": 120},
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
        description="Return TradingCodex service, DB, workspace, and active profile status.",
        category="harness",
        risk_level="read",
        allowed_roles=frozenset({"head-manager"}),
        handler_name="get_tradingcodex_status",
        input_schema=object_schema(),
    ),
    McpToolSpec(
        name="get_runtime_mode",
        description="Return TradingCodex operate/build mode status without changing permissions or mode.",
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
        allowed_roles=frozenset({"head-manager"}),
        handler_name="get_update_status",
        input_schema=object_schema(),
    ),
    McpToolSpec(
        name="simulate_policy",
        description="Evaluate TradingCodex policy for a proposed action without bypassing service-layer checks.",
        category="policy",
        risk_level="read",
        allowed_roles=frozenset(POLICY_ROLES),
        handler_name="simulate_policy",
        input_schema=object_schema({
            "principal_id": {"type": "string"},
            "action": {"type": "string"},
            "resource": {"type": "string"},
            "order": {"type": "object"},
            "approval_receipt": {"type": "object"},
        }),
    ),
    McpToolSpec(
        name="validate_approval_receipt",
        description="Validate that an approval receipt is valid, unexpired, and matches its order ticket payload.",
        category="orders",
        risk_level="write",
        allowed_roles=frozenset({"risk-manager", "execution-operator"}),
        handler_name="validate_approval_receipt",
        input_schema=object_schema(
            {
                "ticket_id": {"type": "string", "minLength": 1, "maxLength": 160},
                "order_ticket_id": {"type": "string", "minLength": 1, "maxLength": 160},
                "approval_receipt": APPROVAL_RECEIPT_SCHEMA,
                "approval_receipt_path": {"type": "string", "minLength": 1, "maxLength": 500},
                "approval_receipt_id": {"type": "string", "minLength": 1, "maxLength": 180},
            },
            additional_properties=False,
        ),
        capability_required="approval_receipt.validate",
    ),
    McpToolSpec(
        name="submit_approved_order",
        description="Experimental: submit an approved order ticket through the service boundary after approval, duplicate-request, policy, connection, and live-confirmation gates.",
        category="execution",
        risk_level="execution",
        allowed_roles=frozenset(EXECUTION_ROLES),
        handler_name="submit_approved_order",
        input_schema=object_schema(
            {
                "ticket_id": {"type": "string", "minLength": 1, "maxLength": 160},
                "order_ticket_id": {"type": "string", "minLength": 1, "maxLength": 160},
                "approval_receipt": APPROVAL_RECEIPT_SCHEMA,
                "approval_receipt_path": {"type": "string", "minLength": 1, "maxLength": 500},
                "approval_receipt_id": {"type": "string", "minLength": 1, "maxLength": 180},
                "live_confirmation": {"type": "string", "maxLength": 500},
            },
            additional_properties=False,
        ),
        capability_required="mcp.tradingcodex.submit_approved_order",
        requires_approval=True,
        experimental=True,
    ),
    McpToolSpec(
        name="cancel_approved_order",
        description="Experimental: cancel through provider cancel when supported, otherwise mark cancelable local validation/paper broker orders as canceled.",
        category="execution",
        risk_level="execution",
        allowed_roles=frozenset(EXECUTION_ROLES),
        handler_name="cancel_approved_order",
        input_schema=object_schema(
            {
                "order_id": {"type": "string", "minLength": 1, "maxLength": 160},
                "ticket_id": {"type": "string", "minLength": 1, "maxLength": 160},
                "order_ticket_id": {"type": "string", "minLength": 1, "maxLength": 160},
                "broker_order_id": {"type": "string", "minLength": 1, "maxLength": 160},
            },
            additional_properties=False,
        ),
        capability_required="mcp.tradingcodex.cancel_approved_order",
        requires_approval=True,
        experimental=True,
    ),
    McpToolSpec(
        name="get_order_status",
        description="Experimental: return local order and broker-order status information without submitting or canceling orders.",
        category="execution",
        risk_level="read",
        allowed_roles=frozenset(EXECUTION_ROLES | {"head-manager"}),
        handler_name="get_order_status",
        input_schema=object_schema({"order_id": {"type": "string"}, "ticket_id": {"type": "string"}, "broker_order_id": {"type": "string"}}, additional_properties=False),
        experimental=True,
    ),
    McpToolSpec(
        name="get_positions",
        description="Return paper portfolio positions and cash state.",
        category="portfolio",
        risk_level="read",
        allowed_roles=frozenset(PORTFOLIO_ROLES),
        handler_name="list_positions",
        input_schema=object_schema(),
    ),
    McpToolSpec(
        name="get_portfolio_snapshot",
        description="Return portfolio snapshot for portfolio, risk, and execution checks.",
        category="portfolio",
        risk_level="read",
        allowed_roles=frozenset(PORTFOLIO_ROLES),
        handler_name="list_positions",
        input_schema=object_schema(),
    ),
    McpToolSpec(
        name="list_broker_connections",
        description="List local broker connections, read scopes, trading lock state, accounts, and sync status.",
        category="brokers",
        risk_level="read",
        allowed_roles=frozenset(PORTFOLIO_ROLES | {"head-manager"}),
        handler_name="list_broker_connections",
        input_schema=object_schema(),
    ),
    McpToolSpec(
        name="get_broker_connection_status",
        description="Return one broker connection health, credential reference, capabilities, and trading lock state.",
        category="brokers",
        risk_level="read",
        allowed_roles=frozenset(PORTFOLIO_ROLES | {"head-manager"}),
        handler_name="get_broker_connection_status",
        input_schema=object_schema({"broker_id": {"type": "string"}, "broker_connection_id": {"type": "string"}}),
    ),
    McpToolSpec(
        name="list_broker_adapter_providers",
        description="List installed TradingCodex broker adapter providers. Core ships paper only; broker-specific live providers are added by build work.",
        category="brokers",
        risk_level="read",
        allowed_roles=frozenset({"head-manager"}),
        handler_name="list_broker_adapter_providers",
        input_schema=object_schema(
            {
                "family": {"type": "string", "maxLength": 120},
                "asset_class": {"type": "string", "maxLength": 80},
                "asset": {"type": "string", "maxLength": 80},
            },
            additional_properties=False,
        ),
    ),
    McpToolSpec(
        name="connect_broker_connector",
        description="Agentic broker onboarding: scaffold/register/validate a provider-backed connector with credential_ref only and no live submission.",
        category="brokers",
        risk_level="write",
        allowed_roles=frozenset({"head-manager"}),
        handler_name="connect_broker_connector",
        input_schema=object_schema(
            {
                "broker": {"type": "string", "maxLength": 120},
                "provider": {"type": "string", "maxLength": 120},
                "provider_id": {"type": "string", "maxLength": 120},
                "broker_id": {"type": "string", "maxLength": 120},
                "broker_connection_id": {"type": "string", "maxLength": 120},
                "credential_ref": {"type": "string", "maxLength": 255},
                "environment": {"type": "string", "maxLength": 80},
                "mode": {"type": "string", "enum": ["read-only", "validation", "live-request"]},
            },
            additional_properties=False,
        ),
        capability_required="broker_connector.register",
    ),
    McpToolSpec(
        name="register_broker_connector",
        description="Register or update a native broker connector profile using a credential_ref, without exposing raw broker tools or secrets.",
        category="brokers",
        risk_level="write",
        allowed_roles=frozenset({"head-manager"}),
        handler_name="register_broker_connector",
        input_schema=object_schema(
            {
                "provider": {"type": "string", "maxLength": 120},
                "provider_id": {"type": "string", "maxLength": 120},
                "template": {"type": "string", "maxLength": 120},
                "template_id": {"type": "string", "maxLength": 120},
                "broker_id": {"type": "string", "maxLength": 120},
                "broker_connection_id": {"type": "string", "maxLength": 120},
                "label": {"type": "string", "maxLength": 160},
                "display_name": {"type": "string", "maxLength": 160},
                "credential_ref": {"type": "string", "maxLength": 255},
                "environment": {"type": "string", "maxLength": 80},
                "region": {"type": "string", "maxLength": 80},
            },
            additional_properties=False,
        ),
        capability_required="broker_connector.register",
    ),
    McpToolSpec(
        name="scaffold_broker_connector",
        description="Create provider-driven connector scaffold files. Unknown providers produce a provider-development-required scaffold instead of enabling execution.",
        category="brokers",
        risk_level="write",
        allowed_roles=frozenset({"head-manager"}),
        handler_name="scaffold_broker_connector",
        input_schema=object_schema(
            {
                "provider": {"type": "string", "maxLength": 120},
                "provider_id": {"type": "string", "maxLength": 120},
                "template": {"type": "string", "maxLength": 120},
                "template_id": {"type": "string", "maxLength": 120},
                "broker_id": {"type": "string", "maxLength": 120},
                "broker_connection_id": {"type": "string", "maxLength": 120},
                "label": {"type": "string", "maxLength": 160},
                "display_name": {"type": "string", "maxLength": 160},
                "credential_ref": {"type": "string", "maxLength": 255},
                "environment": {"type": "string", "maxLength": 80},
                "region": {"type": "string", "maxLength": 80},
            },
            additional_properties=False,
        ),
        capability_required="broker_connector.register",
    ),
    McpToolSpec(
        name="validate_broker_connector_build",
        description="Validate provider-driven connector scaffold/registration metadata without enabling live order submission.",
        category="brokers",
        risk_level="read",
        allowed_roles=frozenset({"head-manager"}),
        handler_name="validate_broker_connector_build",
        input_schema=object_schema({"broker_id": {"type": "string", "maxLength": 120}, "broker_connection_id": {"type": "string", "maxLength": 120}}, additional_properties=False),
    ),
    McpToolSpec(
        name="get_broker_capability_profile",
        description="Return the normalized BrokerCapabilityProfile stored on a native broker connector.",
        category="brokers",
        risk_level="read",
        allowed_roles=frozenset({"head-manager", "portfolio-manager", "risk-manager"}),
        handler_name="get_broker_capability_profile",
        input_schema=object_schema({"broker_id": {"type": "string"}, "broker_connection_id": {"type": "string"}}, additional_properties=False),
    ),
    McpToolSpec(
        name="get_broker_instrument_constraints",
        description="Return normalized instrument/order constraints for a broker, asset class, product, and symbol.",
        category="brokers",
        risk_level="read",
        allowed_roles=frozenset({"head-manager", "instrument-analyst", "portfolio-manager", "risk-manager"}),
        handler_name="get_broker_instrument_constraints",
        input_schema=object_schema(
            {
                "broker_id": {"type": "string", "maxLength": 120},
                "broker_connection_id": {"type": "string", "maxLength": 120},
                "symbol": {"type": "string", "maxLength": 120},
                "instrument": {"type": "string", "maxLength": 160},
                "venue_symbol": {"type": "string", "maxLength": 160},
                "asset_class": {"type": "string", "maxLength": 80},
                "product_type": {"type": "string", "maxLength": 80},
                "market": {"type": "string", "maxLength": 80},
            },
            additional_properties=False,
        ),
    ),
    McpToolSpec(
        name="preview_order_translation",
        description="Preview canonical_order translation for a broker connector without submission, cancellation, or approval authority.",
        category="orders",
        risk_level="write",
        allowed_roles=frozenset({"head-manager", "portfolio-manager", "risk-manager"}),
        handler_name="preview_order_translation",
        input_schema=object_schema(ORDER_TICKET_SCHEMA["properties"], additional_properties=True),
        capability_required="broker_order.preview",
    ),
    McpToolSpec(
        name="get_connector_build_status",
        description="Return connector scaffold metadata created by TradingCodex build mode without enabling live execution.",
        category="brokers",
        risk_level="read",
        allowed_roles=frozenset({"head-manager"}),
        handler_name="get_connector_build_status",
        input_schema=object_schema(),
    ),
    McpToolSpec(
        name="sync_broker_account",
        description="Run a read-only broker account sync through the TradingCodex connection registry and materialize the local portfolio snapshot.",
        category="brokers",
        risk_level="write",
        allowed_roles=frozenset({"portfolio-manager", "risk-manager"}),
        handler_name="sync_broker_account",
        input_schema=object_schema(
            {
                "broker_id": {"type": "string"},
                "broker_connection_id": {"type": "string"},
                "broker_account_id": {"type": "string"},
                "account_id": {"type": "string"},
            }
        ),
        capability_required="broker_account.sync",
    ),
    McpToolSpec(
        name="list_reconciliation_runs",
        description="List broker/local reconciliation summaries created by portfolio sync.",
        category="portfolio",
        risk_level="read",
        allowed_roles=frozenset(PORTFOLIO_ROLES | {"head-manager"}),
        handler_name="list_reconciliation_runs",
        input_schema=object_schema({"broker_id": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 200}}),
    ),
    McpToolSpec(
        name="create_order_ticket",
        description="Create a draft-only canonical order ticket from explicit fields or natural language without broker submission.",
        category="orders",
        risk_level="write",
        allowed_roles=frozenset({"portfolio-manager"}),
        handler_name="create_order_ticket",
        input_schema=object_schema(ORDER_TICKET_SCHEMA["properties"], additional_properties=True),
        capability_required="order_ticket.create",
    ),
    McpToolSpec(
        name="run_order_checks",
        description="Run schema, policy, restricted symbol, cash/position, broker validation, market, and risk readiness checks for an order ticket.",
        category="orders",
        risk_level="write",
        allowed_roles=frozenset({"portfolio-manager", "risk-manager"}),
        handler_name="run_order_checks",
        input_schema=object_schema({"ticket_id": {"type": "string"}, "order_ticket_id": {"type": "string"}}, additional_properties=False),
        capability_required="order_ticket.check",
    ),
    McpToolSpec(
        name="request_order_approval",
        description="Create an approval receipt for a checked order ticket, binding the approval to the exact order payload hash and broker/account scope.",
        category="approvals",
        risk_level="approval",
        allowed_roles=frozenset(APPROVAL_ROLES),
        handler_name="request_order_approval",
        input_schema=object_schema(
            {
                "ticket_id": {"type": "string"},
                "order_ticket_id": {"type": "string"},
                "approved_by": {"type": "string"},
                "expires_hours": {"type": "integer", "minimum": 1, "maximum": 168},
            },
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
        allowed_roles=frozenset({"portfolio-manager", "risk-manager", "execution-operator", "head-manager"}),
        handler_name="get_order_ticket",
        input_schema=object_schema({"ticket_id": {"type": "string"}, "order_ticket_id": {"type": "string"}, "order_id": {"type": "string"}}),
    ),
    McpToolSpec(
        name="list_order_tickets",
        description="List canonical order tickets and approval readiness state.",
        category="orders",
        risk_level="read",
        allowed_roles=frozenset({"portfolio-manager", "risk-manager", "execution-operator", "head-manager"}),
        handler_name="list_order_tickets",
        input_schema=object_schema({"state": {"type": "string"}, "status": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 200}}),
    ),
    McpToolSpec(
        name="record_broker_mapping_review",
        description="Record reviewed external MCP broker tool mappings and keep execution mappings disabled unless gated by a TradingCodex service connection.",
        category="brokers",
        risk_level="write",
        allowed_roles=frozenset({"head-manager", "risk-manager"}),
        handler_name="record_broker_mapping_review",
        input_schema=object_schema({"broker_id": {"type": "string"}, "broker_connection_id": {"type": "string"}}, additional_properties=True),
        capability_required="broker_mapping.review",
    ),
    McpToolSpec(
        name="list_external_mcp_connections",
        description="List managed External MCP Gate connections, review state, and discovered tools without exposing broker MCP directly to Codex.",
        category="external_mcp",
        risk_level="read",
        allowed_roles=frozenset({"head-manager"}),
        handler_name="list_external_mcp_connections",
        input_schema=object_schema({"name": {"type": "string"}, "enabled": {"type": "boolean"}, "limit": {"type": "integer", "minimum": 1, "maximum": 200}}),
    ),
    McpToolSpec(
        name="register_external_mcp_connection",
        description="Register or update a broker/data MCP connection inside TradingCodex External MCP Gate; this does not add raw broker tools to Codex TOML.",
        category="external_mcp",
        risk_level="write",
        allowed_roles=frozenset({"head-manager"}),
        handler_name="register_external_mcp_connection",
        input_schema=object_schema(
            {
                "name": {"type": "string", "minLength": 1, "maxLength": 160},
                "router_name": {"type": "string", "minLength": 1, "maxLength": 160},
                "label": {"type": "string", "maxLength": 160},
                "transport": {"type": "string", "enum": ["stdio", "http", "streamable-http", "streamable_http"]},
                "command": {"type": "string", "maxLength": 1000},
                "args": {"type": "array"},
                "env": {"type": "object"},
                "url": {"type": "string", "maxLength": 1000},
                "credential_ref": {"type": "string", "maxLength": 255},
                "enabled": {"type": "boolean"},
            },
            additional_properties=False,
        ),
        capability_required="external_mcp.register",
    ),
    McpToolSpec(
        name="check_external_mcp_connection",
        description="Check a managed external MCP connection lifecycle without importing tool metadata when the connection is disabled or unhealthy.",
        category="external_mcp",
        risk_level="write",
        allowed_roles=frozenset({"head-manager"}),
        handler_name="check_external_mcp_connection",
        input_schema=object_schema({"router_id": {"type": "integer"}, "name": {"type": "string"}, "router_name": {"type": "string"}, "timeout": {"type": "number"}}, additional_properties=False),
        capability_required="external_mcp.check",
    ),
    McpToolSpec(
        name="discover_external_mcp_connection",
        description="Run initialize/tools-list/resources-list/prompts-list discovery for a managed external MCP connection and store schema hashes for review.",
        category="external_mcp",
        risk_level="write",
        allowed_roles=frozenset({"head-manager"}),
        handler_name="discover_external_mcp_connection",
        input_schema=object_schema({"router_id": {"type": "integer"}, "name": {"type": "string"}, "router_name": {"type": "string"}, "timeout": {"type": "number"}}, additional_properties=False),
        capability_required="external_mcp.discover",
    ),
    McpToolSpec(
        name="review_external_mcp_tool",
        description="Review a discovered external MCP tool. Read-only/account-read tools may be enabled; execution-like tools are kept adapter-mapping-required.",
        category="external_mcp",
        risk_level="write",
        allowed_roles=frozenset({"head-manager"}),
        handler_name="review_external_mcp_tool",
        input_schema=object_schema(
            {
                "tool_id": {"type": "integer"},
                "external_tool_id": {"type": "integer"},
                "router_name": {"type": "string"},
                "external_name": {"type": "string"},
                "primitive": {"type": "string"},
                "category": {"type": "string"},
                "risk_level": {"type": "string"},
                "sensitivity": {"type": "string"},
                "canonical_capability": {"type": "string"},
                "proxy_mode": {"type": "string"},
                "allowed_roles": {"type": "array"},
                "enabled": {"type": "boolean"},
                "review_status": {"type": "string"},
            },
            additional_properties=False,
        ),
        capability_required="external_mcp.review",
    ),
    McpToolSpec(
        name="refresh_broker_order_status",
        description="Refresh local broker order status through the TradingCodex connection registry without bypassing the service path.",
        category="execution",
        risk_level="execution",
        allowed_roles=frozenset(EXECUTION_ROLES),
        handler_name="refresh_broker_order_status",
        input_schema=object_schema({"ticket_id": {"type": "string"}, "broker_order_id": {"type": "string"}}, additional_properties=False),
        capability_required="mcp.tradingcodex.refresh_broker_order_status",
        experimental=True,
    ),
    McpToolSpec(
        name="list_workflow_artifacts",
        description="List workflow artifacts from workspace paths and file-native research memory.",
        category="workflows",
        risk_level="read",
        allowed_roles=frozenset(RESEARCH_ROLES | {"execution-operator"}),
        handler_name="list_workflow_artifacts",
        input_schema=object_schema(),
    ),
    McpToolSpec(
        name="create_research_artifact",
        description="Store markdown research as a workspace-native file through the service layer.",
        category="research",
        risk_level="write",
        allowed_roles=frozenset(RESEARCH_ROLES),
        handler_name="create_research_artifact",
        input_schema=object_schema({
            "artifact_id": {"type": "string"},
            "artifact_type": {"type": "string"},
            "universe": {"type": "string"},
            "symbol": {"type": "string"},
            "title": {"type": "string"},
            "markdown": {"type": "string"},
            "markdown_path": {"type": "string"},
            "source_as_of": {"type": "string"},
            "readiness_label": {"type": "string"},
            **RESEARCH_ARTIFACT_METADATA_FIELDS,
        }),
        capability_required="research_artifact.write",
    ),
    McpToolSpec(
        name="append_research_artifact_version",
        description="Append a workspace-file version for an existing research artifact.",
        category="research",
        risk_level="write",
        allowed_roles=frozenset(RESEARCH_ROLES),
        handler_name="append_research_artifact_version",
        input_schema=object_schema({
            "artifact_id": {"type": "string"},
            "markdown": {"type": "string"},
            "markdown_path": {"type": "string"},
            "source_as_of": {"type": "string"},
            "readiness_label": {"type": "string"},
            **RESEARCH_ARTIFACT_METADATA_FIELDS,
        }, ["artifact_id"]),
        capability_required="research_artifact.write",
    ),
    McpToolSpec(
        name="get_research_artifact",
        description="Fetch a workspace-native research artifact by artifact_id.",
        category="research",
        risk_level="read",
        allowed_roles=frozenset(RESEARCH_ROLES),
        handler_name="get_research_artifact",
        input_schema=object_schema({"artifact_id": {"type": "string"}, "include_markdown": {"type": "boolean"}}, ["artifact_id"]),
    ),
    McpToolSpec(
        name="list_research_artifacts",
        description="List workspace-native research artifacts and metadata.",
        category="research",
        risk_level="read",
        allowed_roles=frozenset(RESEARCH_ROLES),
        handler_name="list_research_artifacts",
        input_schema=object_schema({"artifact_type": {"type": "string"}, "universe": {"type": "string"}, "symbol": {"type": "string"}, "limit": {"type": "integer"}}),
    ),
    McpToolSpec(
        name="search_research_artifacts",
        description="Search workspace-native markdown research artifacts with lexical file search.",
        category="research",
        risk_level="read",
        allowed_roles=frozenset(RESEARCH_ROLES),
        handler_name="search_research_artifacts",
        input_schema=object_schema({"query": {"type": "string"}, "limit": {"type": "integer"}}, ["query"]),
    ),
    McpToolSpec(
        name="export_research_artifact_md",
        description="Export or copy a workspace-native research artifact markdown file.",
        category="research",
        risk_level="write",
        allowed_roles=frozenset(RESEARCH_ROLES),
        handler_name="export_research_artifact_md",
        input_schema=object_schema({"artifact_id": {"type": "string"}, "export_path": {"type": "string"}}, ["artifact_id"]),
        capability_required="research_artifact.export",
    ),
    McpToolSpec(
        name="record_source_snapshot",
        description="Record provider/as-of/retrieved metadata and warnings as a workspace source-snapshot JSON file.",
        category="research",
        risk_level="write",
        allowed_roles=frozenset(RESEARCH_ROLES),
        handler_name="record_source_snapshot",
        input_schema=object_schema({"provider": {"type": "string"}, "source_category": {"type": "string"}, "as_of": {"type": "string"}, "artifact_id": {"type": "string"}, "warnings": {"type": "array"}, "payload": {"type": "object"}}),
        capability_required="source_snapshot.record",
    ),
    McpToolSpec(
        name="record_audit_event",
        description="Append an explicit audit event through the TradingCodex audit ledger.",
        category="audit",
        risk_level="write",
        allowed_roles=frozenset(RESEARCH_ROLES | {"execution-operator"}),
        handler_name="record_audit_event",
        input_schema=object_schema({"event": {"type": "object"}, "principal_id": {"type": "string"}}),
        capability_required="audit_event.record",
    ),
)

TOOL_REGISTRY = {tool.name: tool for tool in TOOL_SPECS}
_REGISTRY_SYNCED = False
_REGISTRY_SYNCED_DB = ""


def prepare_mcp_runtime(workspace_root: Path | str | None = None) -> None:
    global _REGISTRY_SYNCED, _REGISTRY_SYNCED_DB
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
    except Exception:
        return


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
    try:
        from apps.mcp.models import McpToolDefinition

        configured = McpToolDefinition.objects.filter(name=name).first()
        if configured is not None:
            return configured.enabled
    except Exception:
        return True
    return True


def role_for_principal(principal_id: str) -> str:
    try:
        from apps.policy.services import role_for_principal_id

        return role_for_principal_id(principal_id)
    except Exception:
        pass
    return principal_id or "unknown"


def call_mcp_tool(workspace_root: Path | str, name: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
    prepare_mcp_runtime(workspace_root)
    args = dict(args or {})
    tool = TOOL_REGISTRY.get(name)
    if tool is None:
        raise ValueError(f"Unknown TradingCodex tool: {name}")
    if safe_home_mcp_scope() and name not in SAFE_HOME_TOOL_NAMES:
        raise PermissionError(f"MCP tool is not available in tradingcodex-home safe scope: {name}")
    if not tool_enabled(name):
        raise PermissionError(f"MCP tool is disabled: {name}")
    principal_id = str(args.get("principal_id") or default_principal_for_tool(tool))
    role = role_for_principal(principal_id)
    if role not in tool.allowed_roles:
        raise PermissionError(f"{principal_id} is not allowed to call {name}")
    from apps.policy.services import capability_check, tool_capability_action

    action = tool_capability_action(tool.name, tool.capability_required)
    capability_allowed, capability_reasons = capability_check(principal_id, action, args.get("resource"))
    if not capability_allowed:
        raise PermissionError("; ".join(capability_reasons))
    validate_input_schema(tool, args)

    started = time.monotonic()
    request_payload = {"tool_name": name, "arguments": args, "principal_id": principal_id}
    try:
        result = raw_call_tool(workspace_root, tool, args, principal_id)
        if isinstance(result, dict):
            from tradingcodex_service.application.runtime import persist_workspace_context_if_available

            result = dict(result)
            result.setdefault("db_canonical", True)
            result.setdefault("workspace_context", persist_workspace_context_if_available(workspace_root))
        record_tool_call(workspace_root, name, principal_id, "ok", request_payload, result, started)
        return result
    except Exception as exc:
        error = {"status": "error", "error": str(exc), "tool_name": name}
        record_tool_call(workspace_root, name, principal_id, "error", request_payload, error, started, str(exc))
        raise


def raw_call_tool(workspace_root: Path | str, tool: McpToolSpec, args: dict[str, Any], principal_id: str) -> dict[str, Any]:
    from tradingcodex_service.application import audit, brokers, orders, policy, portfolio, research
    from apps.mcp import services as mcp_services

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
        }

    def get_runtime_mode() -> dict[str, Any]:
        from tradingcodex_service.application.runtime_mode import get_runtime_mode_status

        return get_runtime_mode_status(workspace_root)

    def get_update_status() -> dict[str, Any]:
        from tradingcodex_cli.startup_status import build_update_status

        return build_update_status(workspace_root)

    with_principal = {**args, "principal_id": principal_id}
    handlers: dict[str, Callable[[], dict[str, Any]]] = {
        "get_tradingcodex_status": get_tradingcodex_status,
        "get_runtime_mode": get_runtime_mode,
        "get_update_status": get_update_status,
        "simulate_policy": lambda: policy.simulate_policy(workspace_root, args),
        "validate_approval_receipt": lambda: orders.validate_approval_receipt(workspace_root, args),
        "submit_approved_order": lambda: orders.submit_approved_order(workspace_root, with_principal),
        "cancel_approved_order": lambda: orders.cancel_approved_order(workspace_root, with_principal),
        "get_order_status": lambda: orders.get_order_status(workspace_root, args),
        "list_positions": lambda: portfolio.list_positions(workspace_root),
        "list_broker_connections": lambda: brokers.list_broker_connections(workspace_root, args),
        "get_broker_connection_status": lambda: brokers.get_broker_connection_status(workspace_root, args),
        "list_broker_adapter_providers": lambda: brokers.list_broker_adapter_providers(workspace_root, args),
        "connect_broker_connector": lambda: brokers.connect_broker_connector(workspace_root, with_principal),
        "register_broker_connector": lambda: brokers.register_broker_connector(workspace_root, with_principal),
        "scaffold_broker_connector": lambda: brokers.scaffold_broker_connector(workspace_root, with_principal),
        "validate_broker_connector_build": lambda: brokers.validate_broker_connector_build(workspace_root, with_principal),
        "get_broker_capability_profile": lambda: brokers.get_broker_capability_profile(workspace_root, args),
        "get_broker_instrument_constraints": lambda: brokers.get_broker_instrument_constraints(workspace_root, args),
        "preview_order_translation": lambda: brokers.preview_order_translation(workspace_root, with_principal),
        "get_connector_build_status": lambda: brokers.get_connector_build_status(workspace_root, args),
        "sync_broker_account": lambda: brokers.sync_broker_account(workspace_root, with_principal),
        "list_reconciliation_runs": lambda: brokers.list_reconciliation_runs(workspace_root, args),
        "create_order_ticket": lambda: orders.create_order_ticket(workspace_root, with_principal),
        "run_order_checks": lambda: orders.run_order_checks(workspace_root, with_principal),
        "request_order_approval": lambda: orders.request_order_approval(workspace_root, {**with_principal, "approved_by": args.get("approved_by") or principal_id}),
        "get_order_ticket": lambda: orders.get_order_ticket(workspace_root, args),
        "list_order_tickets": lambda: orders.list_order_tickets(workspace_root, args),
        "record_broker_mapping_review": lambda: brokers.record_broker_mapping_review(workspace_root, with_principal),
        "list_external_mcp_connections": lambda: mcp_services.list_external_mcp_connections(workspace_root, with_principal),
        "register_external_mcp_connection": lambda: mcp_services.register_external_mcp_connection(workspace_root, with_principal),
        "check_external_mcp_connection": lambda: mcp_services.check_external_mcp_connection(workspace_root, with_principal),
        "discover_external_mcp_connection": lambda: mcp_services.discover_external_mcp_connection(workspace_root, with_principal),
        "review_external_mcp_tool": lambda: mcp_services.review_external_mcp_tool(workspace_root, with_principal),
        "refresh_broker_order_status": lambda: orders.refresh_broker_order_status(workspace_root, with_principal),
        "list_workflow_artifacts": lambda: research.list_workflow_artifacts(workspace_root),
        "create_research_artifact": lambda: research.create_research_artifact(workspace_root, with_principal),
        "append_research_artifact_version": lambda: research.append_research_artifact_version(workspace_root, with_principal),
        "get_research_artifact": lambda: research.get_research_artifact(workspace_root, args),
        "list_research_artifacts": lambda: research.list_research_artifacts(workspace_root, args),
        "search_research_artifacts": lambda: research.search_research_artifacts(workspace_root, args),
        "export_research_artifact_md": lambda: research.export_research_artifact_md(workspace_root, args),
        "record_source_snapshot": lambda: research.record_source_snapshot(workspace_root, with_principal),
        "record_audit_event": lambda: audit.write_audit_event(workspace_root, args.get("event") or args, principal_id, "mcp"),
    }
    handler = handlers.get(tool.handler_name)
    if handler is None:
        raise ValueError(f"Unknown TradingCodex tool handler: {tool.handler_name}")
    return handler()


def default_principal_for_tool(tool: McpToolSpec) -> str:
    if "execution-operator" in tool.allowed_roles and len(tool.allowed_roles) == 1:
        return "execution-operator"
    if "risk-manager" in tool.allowed_roles and tool.category == "approvals":
        return "risk-manager"
    if "head-manager" in tool.allowed_roles:
        return "head-manager"
    return sorted(tool.allowed_roles)[0]


def validate_input_schema(tool: McpToolSpec, args: dict[str, Any]) -> None:
    _validate_schema_value(tool.input_schema, args, tool.name)
    if tool.name == "validate_approval_receipt" and not any(args.get(field) for field in ("approval_receipt", "approval_receipt_path", "approval_receipt_id")):
        raise ValueError(f"{tool.name} requires approval_receipt, approval_receipt_path, or approval_receipt_id")
    if tool.name == "validate_approval_receipt" and not any(args.get(field) for field in ("ticket_id", "order_ticket_id")):
        raise ValueError(f"{tool.name} requires ticket_id")
    if tool.name == "submit_approved_order" and not any(args.get(field) for field in ("ticket_id", "order_ticket_id")):
        raise ValueError("submit_approved_order requires ticket_id")
    if tool.name == "cancel_approved_order" and not any(args.get(field) for field in ("order_id", "ticket_id", "order_ticket_id", "broker_order_id")):
        raise ValueError("cancel_approved_order requires order_id, ticket_id, or broker_order_id")
    if tool.name in {"run_order_checks", "request_order_approval", "get_order_ticket"} and not any(args.get(field) for field in ("ticket_id", "order_ticket_id", "order_id")):
        raise ValueError(f"{tool.name} requires ticket_id")


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
    return isinstance(value, (int, float)) and not isinstance(value, bool)


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
        from tradingcodex_service.application.common import stable_hash
        from tradingcodex_service.application.runtime import workspace_context_payload

        McpToolCall.objects.create(
            tool_name=name,
            principal_id=principal_id,
            status=status,
            request=request_payload,
            response=response_payload,
            workspace_context=workspace_context_payload(workspace_root),
            request_hash=stable_hash(request_payload),
            result_hash=stable_hash(response_payload),
            error=error,
            duration_ms=max(0, int((time.monotonic() - started) * 1000)),
        )
    except Exception:
        return


def _skip_db_tool_call_ledger(name: str) -> bool:
    tool = TOOL_REGISTRY.get(name)
    if tool and tool.category == "research":
        return True
    return name == "list_workflow_artifacts"


def handle_mcp_rpc(workspace_root: Path | str, message: dict[str, Any]) -> dict[str, Any] | None:
    from tradingcodex_service.version import TRADINGCODEX_VERSION

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
    if method == "prompts/list":
        return {"jsonrpc": "2.0", "id": message.get("id"), "result": {"prompts": []}}
    if method == "tools/call":
        try:
            params = message.get("params") or {}
            result = call_mcp_tool(workspace_root, params.get("name"), params.get("arguments") or {})
            return {"jsonrpc": "2.0", "id": message.get("id"), "result": {"content": [{"type": "text", "text": json.dumps(result, indent=2, ensure_ascii=False)}]}}
        except Exception as exc:
            return {"jsonrpc": "2.0", "id": message.get("id"), "error": {"code": -32000, "message": str(exc)}}
    return {"jsonrpc": "2.0", "id": message.get("id"), "error": {"code": -32601, "message": f"Method not found: {method}"}}


def handle_mcp_batch(workspace_root: Path | str, payload: Any) -> Any:
    if isinstance(payload, list):
        responses = [handle_mcp_rpc(workspace_root, item) for item in payload]
        return [response for response in responses if response is not None]
    return handle_mcp_rpc(workspace_root, payload)

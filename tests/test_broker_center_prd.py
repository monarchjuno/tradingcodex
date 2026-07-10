from __future__ import annotations

import os
import uuid
from pathlib import Path

import yaml
import pytest
from django.test import Client

from tradingcodex_cli.generator import bootstrap_workspace
from tradingcodex_service.application.brokers import (
    BrokerAccountDTO,
    BrokerAdapter,
    BrokerAdapterProvider,
    BrokerHealth,
    BrokerSubmissionUncertainError,
    CashDTO,
    OrderValidationResult,
    PositionDTO,
    _BROKER_ADAPTER_PROVIDERS,
    _WORKSPACE_PROVIDER_SOURCES,
    create_external_mcp_broker_connection,
    record_broker_mapping_review,
    register_broker_adapter_provider,
    sync_broker_account,
)
from tradingcodex_service.application.orders import create_order_ticket, order_payload_from_ticket, validate_approval_receipt
from tradingcodex_service.application.runtime import ensure_runtime_database
from tradingcodex_service.mcp_runtime import call_mcp_tool, handle_mcp_rpc


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def restore_broker_provider_registry():
    previous = dict(_BROKER_ADAPTER_PROVIDERS)
    previous_sources = dict(_WORKSPACE_PROVIDER_SOURCES)
    yield
    _BROKER_ADAPTER_PROVIDERS.clear()
    _BROKER_ADAPTER_PROVIDERS.update(previous)
    _WORKSPACE_PROVIDER_SOURCES.clear()
    _WORKSPACE_PROVIDER_SOURCES.update(previous_sources)


@pytest.fixture(autouse=True)
def _cleanup_order_tables():
    from apps.orders.models import (
        ApprovalReceipt,
        BrokerOrder,
        ExecutionResult,
        Fill,
        OrderCheckRun,
        OrderEvent,
        OrderTicket,
    )
    yield
    Fill.objects.all().delete()
    BrokerOrder.objects.all().delete()
    ExecutionResult.objects.all().delete()
    OrderEvent.objects.all().delete()
    OrderCheckRun.objects.all().delete()
    OrderTicket.objects.all().delete()
    ApprovalReceipt.objects.all().delete()


def make_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    bootstrap_workspace(workspace, force=True)
    return workspace


class FakeLiveBrokerAdapter(BrokerAdapter):
    provider_id = "fake-live-provider"
    submit_mode = "filled"
    health_status = "ok"
    submit_calls: list[dict] = []
    cancel_calls: list[str] = []
    status_calls: list[str] = []
    batch_status_calls: list[list[str]] = []
    missing_batch_ids: set[str] = set()

    def __init__(self, connection, workspace_root: Path | str | None = None) -> None:
        self.connection = connection
        self.workspace_root = workspace_root

    @classmethod
    def reset(cls) -> None:
        cls.submit_mode = "filled"
        cls.health_status = "ok"
        cls.submit_calls = []
        cls.cancel_calls = []
        cls.status_calls = []
        cls.batch_status_calls = []
        cls.missing_batch_ids = set()

    def describe_capabilities(self) -> dict:
        metadata = self.connection.metadata if isinstance(self.connection.metadata, dict) else {}
        return metadata.get("capability_profile") if isinstance(metadata.get("capability_profile"), dict) else {}

    def health_check(self) -> BrokerHealth:
        return BrokerHealth(self.health_status, "fake signed health", {"signed": self.health_status == "ok"})

    def discover_accounts(self) -> list[BrokerAccountDTO]:
        return [
            BrokerAccountDTO(
                broker_account_id="fake-account",
                account_label="Fake Live Account",
                account_type="live",
                base_currency="USD",
                masked_identifier="fake-***-acct",
                trading_enabled=True,
                metadata={"portfolio_id": "default-paper", "strategy_id": "default-strategy"},
            )
        ]

    def get_cash(self, account_id: str) -> list[CashDTO]:
        return [CashDTO(currency="USD", amount=100_000)]

    def get_positions(self, account_id: str) -> list[PositionDTO]:
        return [PositionDTO(symbol="AAPL", quantity=2, average_price=100, currency="USD", instrument_id="AAPL")]

    def validate_order(self, order: dict) -> OrderValidationResult:
        return OrderValidationResult(True, [], {"provider": self.provider_id, "symbol": order.get("symbol")})

    def submit_order(self, order: dict) -> dict:
        self.submit_calls.append(dict(order))
        client_order_id = str(order.get("client_order_id") or "")
        broker_order_id = f"fake-{client_order_id}"
        if self.submit_mode == "uncertain":
            raise BrokerSubmissionUncertainError("timeout after broker submit boundary", broker_order_id=broker_order_id, status_payload={"status": "unknown"})
        result = {
            "adapter": self.provider_id,
            "broker_order_id": broker_order_id,
            "client_order_id": client_order_id,
            "status": "submitted",
            "submitted_at": "2026-01-01T00:00:00Z",
        }
        if self.submit_mode == "filled":
            result.update(
                {
                    "status": "filled",
                    "filled_quantity": order.get("quantity"),
                    "average_price": order.get("limit_price"),
                    "fill_id": f"fill-{client_order_id}",
                    "fee": 0,
                }
            )
        return result

    def cancel_order(self, broker_order_id: str) -> dict:
        self.cancel_calls.append(broker_order_id)
        return {"status": "canceled", "broker_order_id": broker_order_id, "canceled_at": "2026-01-01T00:01:00Z"}

    def get_order_status(self, broker_order_id: str) -> dict:
        self.status_calls.append(broker_order_id)
        return {
            "status": "filled",
            "broker_order_id": broker_order_id,
            "filled_quantity": 1,
            "average_price": 100,
            "fill_id": f"refresh-{broker_order_id}",
            "submitted_at": "2026-01-01T00:00:00Z",
        }

    def get_order_statuses(self, broker_order_ids: list[str]) -> dict:
        """Batch status: one call covers every requested id; ids missing from
        this batch's mapping fall through as ``unknown`` so the caller can
        distinguish a real broker miss from a crash."""
        self.batch_status_calls.append(list(broker_order_ids))
        results: dict = {}
        for broker_order_id in broker_order_ids:
            if self.missing_batch_ids and broker_order_id in self.missing_batch_ids:
                continue
            results[broker_order_id] = {
                "status": "filled",
                "broker_order_id": broker_order_id,
                "filled_quantity": 1,
                "average_price": 100,
                "fill_id": f"refresh-{broker_order_id}",
                "submitted_at": "2026-01-01T00:00:00Z",
            }
        return results


def install_fake_live_provider() -> None:
    register_broker_adapter_provider(
        BrokerAdapterProvider(
            provider_id=FakeLiveBrokerAdapter.provider_id,
            display_name="Fake Live Provider",
            family="fake",
            venue="broker",
            region="test",
            asset_classes=("equity",),
            products=("spot",),
            default_environment="live",
            auth_model={"type": "credential_ref", "credential_ref_required": True},
            account_model={"multi_account": False, "balances": "cash", "positions": True},
            instrument_model={"identity": "symbol", "examples": []},
            order_model={"sides": ["buy", "sell"], "order_types": ["limit"], "time_in_force": ["day"], "quantity_modes": ["quantity"]},
            validation_model={"preview": True, "dry_run": False, "broker_validate": True},
            event_model={"polling": True, "streaming": False, "fills": True},
            execution_posture="live_broker",
            adapter_type=FakeLiveBrokerAdapter.provider_id,
            live=True,
            factory=lambda connection, workspace_root: FakeLiveBrokerAdapter(connection, workspace_root),
        )
    )


def enable_live_policy(workspace: Path, broker_id: str) -> None:
    config_path = workspace / ".tradingcodex" / "config.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    execution = config.setdefault("execution", {})
    execution["live_enabled"] = True
    execution["enabled_adapters"] = sorted(set(execution.get("enabled_adapters") or []) | {broker_id})
    execution["enabled_execution_postures"] = sorted(set(execution.get("enabled_execution_postures") or []) | {"live_broker"})
    config_path.write_text(yaml.safe_dump(config, sort_keys=False, allow_unicode=True), encoding="utf-8")

    policy_path = workspace / ".tradingcodex" / "policies" / "access-policies.yaml"
    policy = yaml.safe_load(policy_path.read_text(encoding="utf-8"))
    conditions = policy["allow"][0]["conditions"]
    for index, condition in enumerate(conditions):
        if condition.startswith("order.broker in "):
            brokers = yaml.safe_load(condition.split(" in ", 1)[1])
            conditions[index] = f'order.broker in {sorted(set(brokers) | {broker_id})}'
        if condition.startswith("order.execution_posture in "):
            postures = yaml.safe_load(condition.split(" in ", 1)[1])
            conditions[index] = f'order.execution_posture in {sorted(set(postures) | {"live_broker"})}'
    policy_path.write_text(yaml.safe_dump(policy, sort_keys=False, allow_unicode=True), encoding="utf-8")


def register_fake_live_connection(workspace: Path, broker_id: str = "fake-live") -> None:
    install_fake_live_provider()
    call_mcp_tool(
        workspace,
        "register_broker_connector",
        {
            "principal_id": "head-manager",
            "provider": FakeLiveBrokerAdapter.provider_id,
            "broker_id": broker_id,
            "credential_ref": "env:FAKE_LIVE",
            "environment": "live",
        },
    )
    from apps.integrations.models import AdapterDefinition

    AdapterDefinition.objects.filter(adapter_id=FakeLiveBrokerAdapter.provider_id).update(enabled=True, live=True)
    status = call_mcp_tool(workspace, "get_broker_connection_status", {"principal_id": "head-manager", "broker_id": broker_id})
    assert status["health"]["status"] == "ok"
    assert status["connection"]["status"] == "trading_enabled"
    assert "order.submit.live" in status["connection"]["enabled_trade_scopes"]
    sync_broker_account(workspace, {"broker_id": broker_id, "principal_id": "portfolio-manager"})


def create_approved_fake_live_ticket(workspace: Path, ticket_id: str, broker_id: str = "fake-live") -> str:
    call_mcp_tool(
        workspace,
        "create_order_ticket",
        {
            "principal_id": "portfolio-manager",
            "ticket_id": ticket_id,
            "broker_id": broker_id,
            "broker_account_id": "fake-account",
            "symbol": "AAPL",
            "side": "buy",
            "quantity": 1,
            "order_type": "limit",
            "limit_price": 100,
            "time_in_force": "day",
            "currency": "USD",
        },
    )
    checks = call_mcp_tool(workspace, "run_order_checks", {"principal_id": "portfolio-manager", "ticket_id": ticket_id})
    assert checks["approval_ready"] is True, checks
    approval = call_mcp_tool(workspace, "request_order_approval", {"principal_id": "risk-manager", "ticket_id": ticket_id})
    assert approval["status"] == "approved", approval
    return f"LIVE:{ticket_id}:{broker_id}:AAPL:buy:1.0"


def test_status_and_broker_tools_support_compact_redacted_responses(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    broker_id = "redact-live"
    try:
        register_fake_live_connection(workspace, broker_id)

        status = call_mcp_tool(workspace, "get_tradingcodex_status", {"principal_id": "head-manager", "compact": True, "redact": True})
        assert status["status"] == "ok"
        assert status["compact"] is True
        assert status["redacted"] is True
        assert "db_path" not in status
        assert "workspace_context" not in status

        listed = call_mcp_tool(workspace, "list_broker_connections", {"principal_id": "head-manager", "compact": True, "redact": True})
        listed_live = next(connection for connection in listed["connections"] if connection["broker_id"] == broker_id)
        assert listed["compact"] is True
        assert listed["redacted"] is True
        assert listed_live["credential_ref"] == "redacted:env"
        assert "metadata" not in listed_live
        assert "accounts" not in listed_live
        assert "capability_profile" not in listed_live

        detail = call_mcp_tool(workspace, "get_broker_connection_status", {"principal_id": "head-manager", "broker_id": broker_id, "compact": True, "redact": True})
        assert detail["compact"] is True
        assert detail["redacted"] is True
        assert detail["connection"]["broker_id"] == broker_id
        assert detail["connection"]["credential_ref"] == "redacted:env"
        assert detail["health"]["status"] == "ok"
        assert "details" not in detail["health"]
    finally:
        from tradingcodex_service import mcp_runtime

        mcp_runtime._REGISTRY_SYNCED = False
        mcp_runtime._REGISTRY_SYNCED_DB = ""


def test_paper_broker_sync_creates_ledger_snapshot_and_reconciliation(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)

    result = sync_broker_account(workspace, {"broker_id": "paper-trading", "principal_id": "portfolio-manager"})

    assert result["status"] == "ok"
    assert result["accounts"][0]["broker_account_id"] == "local-paper"

    from apps.integrations.models import BrokerAccount, BrokerConnection
    from apps.portfolio.models import BrokerSyncRun, PortfolioLedgerEvent, PortfolioSnapshot, ReconciliationRun

    connection = BrokerConnection.objects.get(broker_id="paper-trading")
    assert connection.transport == "paper"
    assert connection.credential_ref == ""
    assert BrokerAccount.objects.filter(broker_connection=connection, broker_account_id="local-paper").exists()
    assert BrokerSyncRun.objects.filter(broker_connection=connection, status="ok").exists()
    assert PortfolioLedgerEvent.objects.filter(broker_connection=connection, event_type="cash").exists()
    assert PortfolioSnapshot.objects.filter(source="paper-trading", account_id="local-paper").exists()
    assert ReconciliationRun.objects.filter(broker_connection=connection, status="clean").exists()


def test_order_ticket_checks_approval_scope_submit_fill_and_duplicate_block(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    ensure_runtime_database(workspace)

    from apps.policy.models import Capability, Principal

    risk_principal, _ = Principal.objects.get_or_create(principal_id="risk-manager", defaults={"role": "risk-manager", "active": True})
    Capability.objects.get_or_create(principal=risk_principal, action="order_ticket.create", resource_pattern="*", defaults={"effect": "allow"})

    try:
        call_mcp_tool(
            workspace,
            "create_order_ticket",
            {
                "principal_id": "risk-manager",
                "ticket_id": "risk-created-ticket",
                "symbol": "MSFT",
                "side": "buy",
                "quantity": 1,
                "order_type": "limit",
                "limit_price": 1000,
            },
        )
    except PermissionError as exc:
        assert "not allowed" in str(exc) or "only portfolio-manager" in str(exc)
    else:
        raise AssertionError("risk-manager must not create order tickets")
    assert not Capability.objects.filter(principal=risk_principal, action="order_ticket.create", effect="allow").exists()
    try:
        create_order_ticket(
            workspace,
            {
                "principal_id": "risk-manager",
                "ticket_id": "blocked-risk-role-service-ticket",
                "symbol": "MSFT",
                "side": "buy",
                "quantity": 1,
                "order_type": "limit",
                "limit_price": 1000,
            },
        )
    except PermissionError as exc:
        assert "only portfolio-manager" in str(exc)
    else:
        raise AssertionError("risk-manager must not create order tickets through service calls")

    created = call_mcp_tool(
        workspace,
        "create_order_ticket",
        {
            "principal_id": "portfolio-manager",
            "ticket_id": "prd-ticket-1",
            "natural_language": "buy 1 MSFT limit 1000",
        },
    )
    assert created["ticket"]["current_state"] == "DRAFT"

    checks = call_mcp_tool(workspace, "run_order_checks", {"principal_id": "portfolio-manager", "ticket_id": "prd-ticket-1"})
    assert checks["approval_ready"] is True
    assert {check["check_type"] for check in checks["checks"]} >= {"schema", "policy", "cash", "broker_validate", "risk"}

    approval = call_mcp_tool(workspace, "request_order_approval", {"principal_id": "risk-manager", "ticket_id": "prd-ticket-1"})
    assert approval["status"] == "approved"
    receipt = approval["approval_receipt"]
    assert receipt["exact_order_hash"]
    assert receipt["order_ticket_id"] == "prd-ticket-1"
    assert receipt["broker_connection_id"] == "paper-trading"

    from apps.orders.models import Fill, OrderEvent, OrderTicket

    ticket = OrderTicket.objects.get(ticket_id="prd-ticket-1")
    mutated_order = {**order_payload_from_ticket(ticket), "quantity": 2}
    invalid = validate_approval_receipt(workspace, {"order": mutated_order, "approval_receipt": receipt})
    assert invalid["valid"] is False
    assert "exact_order_hash" in "\n".join(invalid["reasons"])

    submitted = call_mcp_tool(workspace, "submit_approved_order", {"principal_id": "execution-operator", "ticket_id": "prd-ticket-1"})
    assert submitted["status"] == "accepted", submitted

    ticket.refresh_from_db()
    assert ticket.current_state == "FILLED"
    assert Fill.objects.filter(ticket=ticket).exists()
    assert OrderEvent.objects.filter(ticket=ticket, event_type__in={"acked", "fill"}).count() >= 2

    duplicate = call_mcp_tool(workspace, "submit_approved_order", {"principal_id": "execution-operator", "ticket_id": "prd-ticket-1"})
    assert duplicate["status"] == "rejected"
    assert "already has an execution result" in "\n".join(duplicate["reasons"])


def test_list_order_tickets_filters_by_symbol_and_side(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)

    for ticket_id, symbol, side in [
        ("dot-buy", "KRW-DOT", "buy"),
        ("dot-sell", "KRW-DOT", "sell"),
        ("classys-buy", "352820", "buy"),
    ]:
        call_mcp_tool(
            workspace,
            "create_order_ticket",
            {
                "principal_id": "portfolio-manager",
                "ticket_id": ticket_id,
                "symbol": symbol,
                "side": side,
                "quantity": 1,
                "order_type": "limit",
                "limit_price": 1000,
            },
        )

    result = call_mcp_tool(workspace, "list_order_tickets", {"symbol": "KRW-DOT", "side": "buy"})

    assert [ticket["ticket_id"] for ticket in result["tickets"]] == ["dot-buy"]


def test_order_ticket_preserves_free_text_without_changing_execution_hash(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    ensure_runtime_database(workspace)

    created = call_mcp_tool(
        workspace,
        "create_order_ticket",
        {
            "principal_id": "portfolio-manager",
            "ticket_id": "free-text-ticket",
            "symbol": "MSFT",
            "side": "buy",
            "quantity": 1,
            "order_type": "limit",
            "limit_price": 1000,
            "thesis": "Durable demand with explicit invalidation trigger.",
            "strategy": "Starter position only if risk approves.",
            "notes": "Preserve this note for later review.",
        },
    )

    assert created["ticket"]["free_text"] == {
        "thesis": "Durable demand with explicit invalidation trigger.",
        "strategy": "Starter position only if risk approves.",
        "notes": "Preserve this note for later review.",
    }
    assert "thesis" not in created["ticket"]["canonical_order"]

    detail = call_mcp_tool(workspace, "get_order_ticket", {"principal_id": "portfolio-manager", "ticket_id": "free-text-ticket"})
    assert detail["ticket"]["free_text"]["strategy"] == "Starter position only if risk approves."

    from apps.orders.models import OrderTicket

    ticket = OrderTicket.objects.get(ticket_id="free-text-ticket")
    original_hash = ticket.payload_hash

    updated = call_mcp_tool(
        workspace,
        "create_order_ticket",
        {
            "principal_id": "portfolio-manager",
            "ticket_id": "free-text-ticket",
            "symbol": "MSFT",
            "side": "buy",
            "quantity": 1,
            "order_type": "limit",
            "limit_price": 1000,
            "thesis": "Updated non-executable thesis text.",
            "strategy": "Updated non-executable strategy text.",
        },
    )

    ticket.refresh_from_db()
    assert updated["status"] == "updated"
    assert ticket.payload_hash == original_hash
    assert updated["ticket"]["free_text"]["thesis"] == "Updated non-executable thesis text."

    from tradingcodex_service import mcp_runtime

    mcp_runtime._REGISTRY_SYNCED = False
    mcp_runtime._REGISTRY_SYNCED_DB = ""


def test_instrument_analyst_can_read_constraints_but_not_order_approve_or_submit(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)

    constraints = call_mcp_tool(workspace, "get_broker_instrument_constraints", {"principal_id": "instrument-analyst", "broker_id": "paper-trading", "symbol": "MSFT"})
    assert constraints["broker_id"] == "paper-trading"
    assert constraints["constraints"]["quantity_modes"]

    blocked_tools = [
        ("create_order_ticket", {"ticket_id": "instrument-ticket", "symbol": "MSFT", "side": "buy", "quantity": 1, "order_type": "limit", "limit_price": 1000}),
        ("request_order_approval", {"ticket_id": "missing"}),
        ("submit_approved_order", {"ticket_id": "missing"}),
    ]
    for tool_name, payload in blocked_tools:
        try:
            call_mcp_tool(workspace, tool_name, {"principal_id": "instrument-analyst", **payload})
        except PermissionError as exc:
            assert "not allowed" in str(exc) or "lacks capability" in str(exc)
        else:
            raise AssertionError(f"instrument-analyst must not call {tool_name}")


def test_safe_home_mcp_exposes_only_broker_order_read_status_tools(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    previous = os.environ.get("TRADINGCODEX_MCP_SAFE_TOOLS")
    os.environ["TRADINGCODEX_MCP_SAFE_TOOLS"] = "1"
    try:
        tools = handle_mcp_rpc(workspace, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        tool_names = {tool["name"] for tool in tools["result"]["tools"]}
        list_order_schema = next(tool for tool in tools["result"]["tools"] if tool["name"] == "list_order_tickets")["inputSchema"]
    finally:
        if previous is None:
            os.environ.pop("TRADINGCODEX_MCP_SAFE_TOOLS", None)
        else:
            os.environ["TRADINGCODEX_MCP_SAFE_TOOLS"] = previous

    assert {"list_broker_connections", "get_broker_connection_status", "list_order_tickets", "get_order_ticket", "list_reconciliation_runs"}.issubset(tool_names)
    assert {"state", "status", "symbol", "side", "limit"}.issubset(list_order_schema["properties"])
    assert "sync_broker_account" not in tool_names
    assert "create_order_ticket" not in tool_names
    assert "request_order_approval" not in tool_names
    assert "submit_approved_order" not in tool_names
    assert {
        "validate_order_approval_crosswalk",
        "get_pre_approval_occupancy",
        "get_resting_lifecycle_panel",
    }.issubset(tool_names)


def test_external_mcp_broker_discovery_stays_read_only_until_review() -> None:
    ensure_runtime_database(ROOT)
    from apps.integrations.models import BrokerConnection
    from apps.mcp.models import McpExternalTool, McpRouter
    from apps.mcp.services import set_external_tool_policy

    BrokerConnection.objects.filter(broker_id="prd-mcp-broker").delete()
    McpRouter.objects.filter(name="prd-mcp-router").delete()

    imported = create_external_mcp_broker_connection(
        ROOT,
        broker_id="prd-mcp-broker",
        display_name="PRD MCP Broker",
        router_name="prd-mcp-router",
        discovery_payload={
            "tools": [
                {"name": "get_positions", "description": "Read account positions", "inputSchema": {"type": "object"}},
                {"name": "get_market_quote", "description": "Read market quote", "inputSchema": {"type": "object"}},
                {"name": "place_order", "description": "Submit broker order", "inputSchema": {"type": "object"}},
            ]
        },
        actor="test",
    )
    assert imported["imported"]["imported"] == 3
    connection = BrokerConnection.objects.get(broker_id="prd-mcp-broker")
    assert connection.status == "read_only"
    assert connection.enabled_trade_scopes == []
    assert connection.metadata["execution_enabled"] is False

    router = McpRouter.objects.get(name="prd-mcp-router")
    positions = McpExternalTool.objects.get(router=router, external_name="get_positions")
    order = McpExternalTool.objects.get(router=router, external_name="place_order")
    set_external_tool_policy(positions, enabled=True, review_status="reviewed", actor="test")
    reviewed = record_broker_mapping_review(ROOT, {"broker_id": "prd-mcp-broker", "principal_id": "risk-manager"})

    assert "account.positions.read" in connection.__class__.objects.get(pk=connection.pk).enabled_read_scopes
    assert reviewed["blocked_tools"]
    assert order.category == "execution"
    assert order.proxy_mode == "service_adapter"
    assert order.enabled is False


def test_provider_registry_is_request_driven_and_unknown_broker_scaffolds_development(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    providers = call_mcp_tool(workspace, "list_broker_adapter_providers", {"principal_id": "head-manager"})
    provider_ids = {provider["provider_id"] for provider in providers["providers"]}

    assert "paper" in provider_ids
    assert providers["templates"] == []
    assert providers["named_broker_examples_builtin"] is False
    assert not (provider_ids & {"binance_spot", "upbit_spot_kr", "kis_openapi", "alpaca_rest", "ibkr_gateway"})

    connected = call_mcp_tool(
        workspace,
        "connect_broker_connector",
        {"principal_id": "head-manager", "broker": "binance", "credential_ref": "env:BINANCE_TESTNET"},
    )
    assert connected["status"] == "provider_missing"
    assert connected["lifecycle_state"] == "provider_missing"
    assert connected["live_order_enabled"] is False

    scaffold = call_mcp_tool(
        workspace,
        "scaffold_broker_connector",
        {"principal_id": "head-manager", "provider": "binance", "broker_id": "binance"},
    )
    assert scaffold["status"] == "scaffolded"
    assert scaffold["provider_development_required"] is True
    assert scaffold["live_order_enabled"] is False
    assert not any("connectors register" in step for step in scaffold["next"])


def test_connect_read_only_does_not_promote_validation_trade_scopes(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    register_broker_adapter_provider(
        BrokerAdapterProvider(
            provider_id="validation-provider",
            display_name="Validation Provider",
            auth_model={"type": "credential_ref", "credential_ref_required": True},
            execution_posture="broker_validation_only",
            adapter_type="validation-provider",
            live=False,
            factory=lambda connection, workspace_root: FakeLiveBrokerAdapter(connection, workspace_root),
        )
    )

    connected = call_mcp_tool(
        workspace,
        "connect_broker_connector",
        {
            "principal_id": "head-manager",
            "broker": "validation-provider",
            "credential_ref": "env:VALIDATION_PROVIDER",
            "mode": "read-only",
        },
    )

    from apps.integrations.models import BrokerConnection

    connection = BrokerConnection.objects.get(broker_id="validation-provider")
    assert connected["lifecycle_state"] == "read_only"
    assert connection.status == "read_only"
    assert connection.enabled_trade_scopes == []
    assert connection.metadata["credential_validation_status"] == "ok"
    assert connection.metadata["validation_execution_enabled"] is False


def test_workspace_provider_file_registers_live_connection(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    provider_dir = workspace / "trading" / "connectors" / "kis"
    provider_dir.mkdir(parents=True, exist_ok=True)
    provider_path = provider_dir / "provider.py"
    provider_path.write_text(
        """
from tradingcodex_service.application.brokers import BrokerAccountDTO, BrokerAdapter, BrokerAdapterProvider, BrokerHealth, CashDTO, OrderValidationResult


class Adapter(BrokerAdapter):
    def __init__(self, connection, workspace_root=None):
        self.connection = connection

    def describe_capabilities(self):
        metadata = self.connection.metadata if isinstance(self.connection.metadata, dict) else {}
        return metadata.get("capability_profile") if isinstance(metadata.get("capability_profile"), dict) else {}

    def health_check(self):
        return BrokerHealth("ok", "signed health ok", {"signed": True})

    def discover_accounts(self):
        return [BrokerAccountDTO("account", "Account", "live", "KRW", "env:KIS", True)]

    def get_cash(self, account_id):
        return [CashDTO("KRW", 1000)]

    def get_positions(self, account_id):
        return []

    def validate_order(self, order):
        return OrderValidationResult(True, [], {"symbol": order.get("symbol")})


PROVIDER = BrokerAdapterProvider(
    provider_id="kis",
    display_name="KIS Smoke Provider",
    region="KR",
    asset_classes=("equity", "cash"),
    products=("stock",),
    auth_model={"type": "credential_ref", "credential_ref_required": True},
    execution_posture="live_broker",
    adapter_type="kis",
    live=True,
    factory=lambda connection, workspace_root: Adapter(connection, workspace_root),
)
""".lstrip(),
        encoding="utf-8",
    )
    providers = call_mcp_tool(workspace, "list_broker_adapter_providers", {"principal_id": "head-manager"})
    assert "kis" in {provider["provider_id"] for provider in providers["providers"]}

    registered = call_mcp_tool(
        workspace,
        "register_broker_connector",
        {"principal_id": "head-manager", "provider": "kis", "broker_id": "kis", "credential_ref": "env:KIS", "environment": "live"},
    )
    assert registered["connection"]["status"] == "read_only"

    from apps.integrations.models import AdapterDefinition

    AdapterDefinition.objects.filter(adapter_id="kis").update(enabled=True, live=True)
    status = call_mcp_tool(workspace, "get_broker_connection_status", {"principal_id": "head-manager", "broker_id": "kis"})
    assert status["health"]["status"] == "ok"
    assert status["connection"]["status"] == "trading_enabled"
    assert "order.submit.live" in status["connection"]["enabled_trade_scopes"]

    provider_path.write_text(provider_path.read_text(encoding="utf-8").replace("signed health ok", "changed health ok"), encoding="utf-8")
    listed = call_mcp_tool(workspace, "list_broker_connections", {"principal_id": "head-manager"})
    listed_kis = next(connection for connection in listed["connections"] if connection["broker_id"] == "kis")
    assert listed_kis["service_restart_required"] is True
    assert listed_kis["lifecycle_state"] == "review_required"
    assert listed_kis["trading_status"] == "locked"

    drifted = call_mcp_tool(workspace, "validate_broker_connector_build", {"principal_id": "head-manager", "broker_id": "kis"})
    assert drifted["service_restart_required"] is True
    assert "provider_source_changed_restart_required" in drifted["blockers"]

    drifted_status = call_mcp_tool(workspace, "get_broker_connection_status", {"principal_id": "head-manager", "broker_id": "kis"})
    assert drifted_status["health"]["status"] == "blocked"
    assert drifted_status["connection"]["status"] == "read_only"
    assert drifted_status["connection"]["enabled_trade_scopes"] == []

    _BROKER_ADAPTER_PROVIDERS.pop("kis", None)
    _WORKSPACE_PROVIDER_SOURCES.pop("kis", None)
    call_mcp_tool(workspace, "list_broker_adapter_providers", {"principal_id": "head-manager"})
    revalidated = call_mcp_tool(workspace, "validate_broker_connector_build", {"principal_id": "head-manager", "broker_id": "kis"})
    assert revalidated["status"] == "ok"
    assert revalidated["service_restart_required"] is False
    revalidated_connection = revalidated["connection"]["connection"]
    assert revalidated_connection["status"] == "trading_enabled"
    assert "order.submit.live" in revalidated_connection["enabled_trade_scopes"]


def test_default_policy_rejects_live_broker_posture(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    install_fake_live_provider()

    from tradingcodex_service.application.policy import read_runtime_policy

    policy = read_runtime_policy(workspace)
    assert policy.live_enabled is False
    assert "live_broker" not in policy.allowed_execution_postures


def test_fake_live_provider_registration_gates_submit_and_records_fill_sync(tmp_path: Path, monkeypatch) -> None:
    workspace = make_workspace(tmp_path)
    ticket_id = f"fake-live-filled-{uuid.uuid4().hex[:12]}"
    broker_id = "fake-live"
    FakeLiveBrokerAdapter.reset()
    register_fake_live_connection(workspace, broker_id)
    enable_live_policy(workspace, broker_id)

    confirmation = create_approved_fake_live_ticket(workspace, ticket_id, broker_id)

    from apps.orders.models import BrokerOrder, ExecutionResult, Fill
    from apps.portfolio.models import PortfolioLedgerEvent, PortfolioSnapshot, ReconciliationRun

    rejected_env = call_mcp_tool(
        workspace,
        "submit_approved_order",
        {"principal_id": "execution-operator", "ticket_id": ticket_id, "live_confirmation": confirmation},
    )
    assert rejected_env["status"] == "rejected"
    assert "TRADINGCODEX_ENABLE_LIVE_EXECUTION=1" in "\n".join(rejected_env["reasons"])
    assert ExecutionResult.objects.filter(order_ticket_id=ticket_id).count() == 0
    assert FakeLiveBrokerAdapter.submit_calls == []

    monkeypatch.setenv("TRADINGCODEX_ENABLE_LIVE_EXECUTION", "1")
    rejected_confirmation = call_mcp_tool(workspace, "submit_approved_order", {"principal_id": "execution-operator", "ticket_id": ticket_id})
    assert rejected_confirmation["status"] == "rejected"
    assert "live confirmation required" in "\n".join(rejected_confirmation["reasons"])
    assert FakeLiveBrokerAdapter.submit_calls == []

    from apps.integrations.models import BrokerConnection

    connection = BrokerConnection.objects.get(broker_id=broker_id)
    connection.enabled_trade_scopes = []
    connection.save(update_fields=["enabled_trade_scopes"])
    rejected_scope = call_mcp_tool(
        workspace,
        "submit_approved_order",
        {"principal_id": "execution-operator", "ticket_id": ticket_id, "live_confirmation": confirmation},
    )
    assert rejected_scope["status"] == "rejected"
    assert "order.submit.live" in "\n".join(rejected_scope["reasons"])
    assert ExecutionResult.objects.filter(order_ticket_id=ticket_id).count() == 0
    assert FakeLiveBrokerAdapter.submit_calls == []

    call_mcp_tool(workspace, "get_broker_connection_status", {"principal_id": "head-manager", "broker_id": broker_id})
    FakeLiveBrokerAdapter.health_status = "error"
    rejected_health = call_mcp_tool(
        workspace,
        "submit_approved_order",
        {"principal_id": "execution-operator", "ticket_id": ticket_id, "live_confirmation": confirmation},
    )
    assert rejected_health["status"] == "rejected"
    assert "broker health is error" in "\n".join(rejected_health["reasons"])
    assert ExecutionResult.objects.filter(order_ticket_id=ticket_id).count() == 0
    assert FakeLiveBrokerAdapter.submit_calls == []

    FakeLiveBrokerAdapter.health_status = "ok"
    call_mcp_tool(workspace, "get_broker_connection_status", {"principal_id": "head-manager", "broker_id": broker_id})
    submitted = call_mcp_tool(
        workspace,
        "submit_approved_order",
        {"principal_id": "execution-operator", "ticket_id": ticket_id, "live_confirmation": confirmation},
    )
    assert submitted["status"] == "accepted", submitted
    assert len(FakeLiveBrokerAdapter.submit_calls) == 1
    assert FakeLiveBrokerAdapter.submit_calls[0]["client_order_id"].startswith("tcx-")

    assert ExecutionResult.objects.filter(order_ticket_id=ticket_id, status="accepted").count() == 1
    assert BrokerOrder.objects.filter(ticket__ticket_id=ticket_id, broker_status="filled").exists()
    assert Fill.objects.filter(ticket__ticket_id=ticket_id).exists()
    assert PortfolioLedgerEvent.objects.filter(event_type="fill", broker_connection__broker_id=broker_id).exists()
    assert PortfolioSnapshot.objects.filter(source=broker_id, account_id="fake-account").exists()
    assert ReconciliationRun.objects.filter(broker_connection__broker_id=broker_id, status="clean").exists()

    duplicate = call_mcp_tool(
        workspace,
        "submit_approved_order",
        {"principal_id": "execution-operator", "ticket_id": ticket_id, "live_confirmation": confirmation},
    )
    assert duplicate["status"] == "rejected"
    assert "already has an execution result" in "\n".join(duplicate["reasons"])
    assert len(FakeLiveBrokerAdapter.submit_calls) == 1

    broker_order = BrokerOrder.objects.get(ticket__ticket_id=ticket_id)
    refreshed = call_mcp_tool(workspace, "refresh_broker_order_status", {"principal_id": "execution-operator", "broker_order_id": broker_order.broker_order_id})
    assert refreshed["status"] == "refreshed"
    assert refreshed["broker_status"] == "filled"
    assert FakeLiveBrokerAdapter.status_calls == [broker_order.broker_order_id]


def test_refresh_broker_order_status_transitions_ticket_and_records_fill(tmp_path: Path, monkeypatch) -> None:
    workspace = make_workspace(tmp_path)
    ticket_id = f"fake-live-refresh-{uuid.uuid4().hex[:12]}"
    broker_id = "fake-live-refresh"
    FakeLiveBrokerAdapter.reset()
    register_fake_live_connection(workspace, broker_id)
    enable_live_policy(workspace, broker_id)
    confirmation = create_approved_fake_live_ticket(workspace, ticket_id, broker_id)
    monkeypatch.setenv("TRADINGCODEX_ENABLE_LIVE_EXECUTION", "1")

    submitted = call_mcp_tool(
        workspace,
        "submit_approved_order",
        {"principal_id": "execution-operator", "ticket_id": ticket_id, "live_confirmation": confirmation},
    )
    assert submitted["status"] == "accepted", submitted

    broker_order_id = submitted["result"]["broker_order_id"]

    refreshed = call_mcp_tool(
        workspace,
        "refresh_broker_order_status",
        {"principal_id": "execution-operator", "broker_order_id": broker_order_id},
    )
    assert refreshed["status"] == "refreshed"
    assert refreshed["broker_status"] == "filled"
    assert refreshed["broker_order_id"] == broker_order_id
    assert refreshed["ticket"]["current_state"] == "FILLED"
    assert refreshed["ticket"]["broker_orders"][0]["broker_status"] == "filled"

    from apps.orders.models import BrokerOrder, Fill, OrderEvent, OrderTicket

    ticket = OrderTicket.objects.get(ticket_id=ticket_id)
    assert ticket.current_state == "FILLED"
    broker_order = BrokerOrder.objects.get(ticket=ticket, broker_order_id=broker_order_id)
    assert broker_order.broker_status == "filled"
    assert broker_order.last_seen_at is not None
    assert broker_order.raw_status_payload_hash

    fills = Fill.objects.filter(ticket=ticket, broker_order_id=broker_order_id)
    assert fills.exists()

    refresh_event = OrderEvent.objects.get(ticket=ticket, event_type="status_refreshed")
    assert refresh_event.actor == "execution-operator"
    assert refresh_event.payload["broker_order_id"] == broker_order_id
    assert refresh_event.payload["broker_status"] == "filled"

    assert FakeLiveBrokerAdapter.status_calls == [broker_order_id]


def _submit_acked_live_ticket(workspace: Path, ticket_id: str, broker_id: str, monkeypatch) -> str:
    confirmation = create_approved_fake_live_ticket(workspace, ticket_id, broker_id)
    monkeypatch.setenv("TRADINGCODEX_ENABLE_LIVE_EXECUTION", "1")
    submitted = call_mcp_tool(
        workspace,
        "submit_approved_order",
        {"principal_id": "execution-operator", "ticket_id": ticket_id, "live_confirmation": confirmation},
    )
    assert submitted["status"] == "accepted", submitted
    return submitted["result"]["broker_order_id"]


def test_refresh_broker_order_statuses_batch_calls_adapter_once_per_connection(
    tmp_path: Path, monkeypatch
) -> None:
    from tradingcodex_service.application import orders as orders_module

    workspace = make_workspace(tmp_path)
    broker_id = "fake-live-batch"
    FakeLiveBrokerAdapter.reset()
    register_fake_live_connection(workspace, broker_id)
    enable_live_policy(workspace, broker_id)
    monkeypatch.setenv("TRADINGCODEX_ENABLE_LIVE_EXECUTION", "1")

    ticket_a = f"batch-a-{uuid.uuid4().hex[:10]}"
    ticket_b = f"batch-b-{uuid.uuid4().hex[:10]}"
    ticket_c = f"batch-c-{uuid.uuid4().hex[:10]}"
    bo_a = _submit_acked_live_ticket(workspace, ticket_a, broker_id, monkeypatch)
    bo_b = _submit_acked_live_ticket(workspace, ticket_b, broker_id, monkeypatch)
    bo_c = _submit_acked_live_ticket(workspace, ticket_c, broker_id, monkeypatch)

    # Force one ticket's ref to be missing from the batch result.
    FakeLiveBrokerAdapter.missing_batch_ids.add(bo_c)

    payload = orders_module.refresh_broker_order_statuses(
        workspace,
        {"principal_id": "execution-operator", "ticket_ids": [ticket_a, ticket_b, ticket_c]},
    )

    assert payload["status"] == "batch_refreshed"
    results_by_ticket = {r["ticket_id"]: r for r in payload["results"]}
    assert set(results_by_ticket) == {ticket_a, ticket_b, ticket_c}

    for ticket_id, broker_order_id in [(ticket_a, bo_a), (ticket_b, bo_b)]:
        entry = results_by_ticket[ticket_id]
        assert entry["status"] == "refreshed"
        assert entry["broker_order_id"] == broker_order_id
        assert entry["broker_status"] == "filled"

    missing_entry = results_by_ticket[ticket_c]
    assert missing_entry["status"] == "unknown"
    assert missing_entry["broker_order_id"] == bo_c
    assert missing_entry["broker_status"] == "unknown"

    assert len(FakeLiveBrokerAdapter.batch_status_calls) == 1
    assert sorted(FakeLiveBrokerAdapter.batch_status_calls[0]) == sorted([bo_a, bo_b, bo_c])

    assert FakeLiveBrokerAdapter.status_calls == []


def test_refresh_broker_order_statuses_batch_isolates_per_ticket_failure(
    tmp_path: Path, monkeypatch
) -> None:
    from tradingcodex_service.application import orders as orders_module

    workspace = make_workspace(tmp_path)
    broker_id = "fake-live-batch-fail"
    FakeLiveBrokerAdapter.reset()
    register_fake_live_connection(workspace, broker_id)
    enable_live_policy(workspace, broker_id)
    monkeypatch.setenv("TRADINGCODEX_ENABLE_LIVE_EXECUTION", "1")

    ticket_ok = f"batch-ok-{uuid.uuid4().hex[:10]}"
    ticket_bad = f"batch-bad-{uuid.uuid4().hex[:10]}"
    bo_ok = _submit_acked_live_ticket(workspace, ticket_ok, broker_id, monkeypatch)
    bo_bad = _submit_acked_live_ticket(workspace, ticket_bad, broker_id, monkeypatch)

    real_apply = orders_module._apply_broker_status

    def flaky_apply(root, ticket, broker_order, adapter_status, principal_id):
        if broker_order.broker_order_id == bo_bad:
            raise RuntimeError("simulated per-ticket apply failure")
        return real_apply(root, ticket, broker_order, adapter_status, principal_id)

    monkeypatch.setattr(orders_module, "_apply_broker_status", flaky_apply)

    payload = orders_module.refresh_broker_order_statuses(
        workspace,
        {"principal_id": "execution-operator", "ticket_ids": [ticket_ok, ticket_bad]},
    )

    results_by_ticket = {r["ticket_id"]: r for r in payload["results"]}
    assert results_by_ticket[ticket_ok]["status"] == "refreshed"
    assert results_by_ticket[ticket_ok]["broker_order_id"] == bo_ok
    assert results_by_ticket[ticket_bad]["status"] == "error"
    assert results_by_ticket[ticket_bad]["broker_order_id"] == bo_bad
    assert any("RuntimeError" in r for r in results_by_ticket[ticket_bad]["reasons"])


def test_fake_live_provider_cancel_calls_provider_cancel_path(tmp_path: Path, monkeypatch) -> None:
    workspace = make_workspace(tmp_path)
    ticket_id = f"fake-live-cancel-{uuid.uuid4().hex[:12]}"
    broker_id = "fake-live-cancel"
    FakeLiveBrokerAdapter.reset()
    FakeLiveBrokerAdapter.submit_mode = "submitted"
    register_fake_live_connection(workspace, broker_id)
    enable_live_policy(workspace, broker_id)
    confirmation = create_approved_fake_live_ticket(workspace, ticket_id, broker_id)
    monkeypatch.setenv("TRADINGCODEX_ENABLE_LIVE_EXECUTION", "1")

    submitted = call_mcp_tool(
        workspace,
        "submit_approved_order",
        {"principal_id": "execution-operator", "ticket_id": ticket_id, "live_confirmation": confirmation},
    )
    assert submitted["status"] == "accepted", submitted

    cancel = call_mcp_tool(
        workspace,
        "cancel_approved_order",
        {"principal_id": "execution-operator", "ticket_id": ticket_id, "broker_order_id": submitted["result"]["broker_order_id"]},
    )
    assert cancel["status"] == "canceled"
    assert cancel["ticket"]["current_state"] == "CANCELED"
    assert cancel["ticket"]["broker_orders"][0]["broker_status"] == "canceled"
    assert FakeLiveBrokerAdapter.cancel_calls == [submitted["result"]["broker_order_id"]]


def test_approved_only_live_ticket_can_be_voided_without_broker_order(tmp_path: Path, monkeypatch) -> None:
    workspace = make_workspace(tmp_path)
    ticket_id = f"fake-live-void-{uuid.uuid4().hex[:12]}"
    broker_id = "fake-live-void"
    FakeLiveBrokerAdapter.reset()
    register_fake_live_connection(workspace, broker_id)
    enable_live_policy(workspace, broker_id)
    create_approved_fake_live_ticket(workspace, ticket_id, broker_id)
    monkeypatch.setenv("TRADINGCODEX_ENABLE_LIVE_EXECUTION", "1")

    from apps.orders.models import ApprovalReceipt, BrokerOrder, Fill, OrderTicket

    ticket = OrderTicket.objects.get(ticket_id=ticket_id)
    assert ticket.current_state == "APPROVED"
    assert not BrokerOrder.objects.filter(ticket=ticket).exists()
    assert not Fill.objects.filter(ticket=ticket).exists()
    assert ApprovalReceipt.objects.filter(order_ticket_id=ticket_id, valid=True).count() == 1

    result = call_mcp_tool(
        workspace,
        "cancel_approved_order",
        {
            "principal_id": "execution-operator",
            "ticket_id": ticket_id,
            "void_reason": "superseded approved-only ticket",
            "superseded_by_ticket_id": "replacement-ticket",
        },
    )

    assert result["status"] == "voided", result
    assert result["ticket"]["current_state"] == "VOIDED"
    assert result["invalidated_approval_receipts"] == 1
    assert FakeLiveBrokerAdapter.cancel_calls == []
    assert ApprovalReceipt.objects.filter(order_ticket_id=ticket_id, valid=True).count() == 0

    rejected = call_mcp_tool(
        workspace,
        "submit_approved_order",
        {"principal_id": "execution-operator", "ticket_id": ticket_id},
    )
    assert rejected["status"] == "rejected"
    assert "voided" in "\n".join(rejected["reasons"]).lower()


def test_submit_reconciles_existing_broker_order_before_preflight_reject(tmp_path: Path, monkeypatch) -> None:
    workspace = make_workspace(tmp_path)
    ticket_id = f"fake-live-race-{uuid.uuid4().hex[:12]}"
    broker_id = "fake-live-race"
    FakeLiveBrokerAdapter.reset()
    register_fake_live_connection(workspace, broker_id)
    enable_live_policy(workspace, broker_id)
    confirmation = create_approved_fake_live_ticket(workspace, ticket_id, broker_id)
    monkeypatch.setenv("TRADINGCODEX_ENABLE_LIVE_EXECUTION", "1")

    from django.utils import timezone
    from apps.integrations.models import BrokerConnection
    from apps.orders.models import BrokerOrder, OrderTicket

    ticket = OrderTicket.objects.get(ticket_id=ticket_id)
    ticket.current_state = "SUBMITTED"
    ticket.status = "SUBMITTED"
    ticket.save(update_fields=["current_state", "status", "updated_at"])
    BrokerOrder.objects.create(
        ticket=ticket,
        broker_order_id="race-existing-provider-order",
        broker_status="submitted",
        submitted_at=timezone.now(),
        last_seen_at=timezone.now(),
        metadata={"source": "race fixture"},
    )

    connection = BrokerConnection.objects.get(broker_id=broker_id)
    connection.status = "read_only"
    connection.save(update_fields=["status", "updated_at"])

    result = call_mcp_tool(
        workspace,
        "submit_approved_order",
        {"principal_id": "execution-operator", "ticket_id": ticket_id, "live_confirmation": confirmation},
    )

    assert result["status"] == "reconciled", result
    assert result["original_rejection"]["reasons"] == [f"broker connection is not trading_enabled: {broker_id}"]
    assert result["ticket"]["current_state"] == "FILLED"
    assert result["reconciliation"]["status"] == "refreshed"
    assert FakeLiveBrokerAdapter.submit_calls == []
    assert FakeLiveBrokerAdapter.status_calls == ["race-existing-provider-order"]


def test_fake_live_uncertain_submit_records_needs_review_and_blocks_retry(tmp_path: Path, monkeypatch) -> None:
    workspace = make_workspace(tmp_path)
    ticket_id = f"fake-live-uncertain-{uuid.uuid4().hex[:12]}"
    broker_id = "fake-live-uncertain"
    FakeLiveBrokerAdapter.reset()
    FakeLiveBrokerAdapter.submit_mode = "uncertain"
    register_fake_live_connection(workspace, broker_id)
    enable_live_policy(workspace, broker_id)
    confirmation = create_approved_fake_live_ticket(workspace, ticket_id, broker_id)
    monkeypatch.setenv("TRADINGCODEX_ENABLE_LIVE_EXECUTION", "1")

    result = call_mcp_tool(
        workspace,
        "submit_approved_order",
        {"principal_id": "execution-operator", "ticket_id": ticket_id, "live_confirmation": confirmation},
    )
    assert result["status"] == "needs_review", result
    assert len(FakeLiveBrokerAdapter.submit_calls) == 1

    from apps.orders.models import BrokerOrder, ExecutionResult, OrderTicket

    assert ExecutionResult.objects.filter(order_ticket_id=ticket_id, status="needs_review").count() == 1
    assert OrderTicket.objects.get(ticket_id=ticket_id).current_state == "NEEDS_REVIEW"
    assert BrokerOrder.objects.filter(ticket__ticket_id=ticket_id, broker_status="unknown").exists()

    duplicate = call_mcp_tool(
        workspace,
        "submit_approved_order",
        {"principal_id": "execution-operator", "ticket_id": ticket_id, "live_confirmation": confirmation},
    )
    assert duplicate["status"] == "rejected"
    assert "already has an execution result" in "\n".join(duplicate["reasons"])
    assert len(FakeLiveBrokerAdapter.submit_calls) == 1


def test_broker_center_and_order_ticket_web_surfaces_render() -> None:
    ensure_runtime_database(ROOT)
    client = Client(REMOTE_ADDR="127.0.0.1")

    brokers = client.get("/brokers/")
    assert brokers.status_code == 200
    broker_body = brokers.content.decode()
    assert "Broker Center" in broker_body
    assert "Connect broker" in broker_body
    assert "Read-only sync" in broker_body
    assert "Add paper broker" in broker_body
    assert "Import data source discovery" in broker_body
    assert "Discovery JSON" in broker_body
    assert "Live execution" in broker_body
    assert "Locked by default" in broker_body
    assert "example-source" in broker_body
    assert "example-mcp" not in broker_body
    assert "paper/stub" not in broker_body
    broker_template = (ROOT / "tradingcodex_service" / "templates" / "web" / "brokers.html").read_text(encoding="utf-8")
    assert "No brokers connected" in broker_template
    assert "without enabling live trading" in broker_template

    orders = client.get("/orders/")
    assert orders.status_code == 200
    order_body = orders.content.decode()
    assert "Prepare draft" in order_body
    assert "Draft only after decision support" in order_body
    assert "Plan decision support" in order_body
    assert "Draft-only example" in order_body
    assert "Save review ticket" in order_body
    assert "Save draft" not in order_body
    assert "Review ticket" in order_body or "No order tickets" in order_body
    assert "Submission is locked" in order_body
    assert "Risk review first" in order_body
    assert "Approved submission only" in order_body
    assert "local confirmation" in order_body
def create_pending_fake_live_ticket(workspace, ticket_id, symbol="AAPL", side="buy", broker_id="fake-live"):
    call_mcp_tool(workspace, "create_order_ticket", {"principal_id": "portfolio-manager", "ticket_id": ticket_id, "broker_id": broker_id, "broker_account_id": "fake-account", "symbol": symbol, "side": side, "quantity": 1, "order_type": "limit", "limit_price": 100, "time_in_force": "day", "currency": "USD"})
    checks = call_mcp_tool(workspace, "run_order_checks", {"principal_id": "portfolio-manager", "ticket_id": ticket_id})
    assert checks["approval_ready"] is True, checks
    approval = call_mcp_tool(workspace, "request_order_approval", {"principal_id": "risk-manager", "ticket_id": ticket_id})
    assert approval["status"] == "approved", approval
    return approval

def test_approval_crosswalk_flags_unknown_and_blocks_terminal_inference(tmp_path, monkeypatch):
    workspace = make_workspace(tmp_path)
    ticket_id = f"crosswalk-unknown-{uuid.uuid4().hex[:12]}"
    broker_id = "fake-live-crosswalk"
    FakeLiveBrokerAdapter.reset()
    FakeLiveBrokerAdapter.submit_mode = "uncertain"
    register_fake_live_connection(workspace, broker_id)
    enable_live_policy(workspace, broker_id)
    confirmation = create_approved_fake_live_ticket(workspace, ticket_id, broker_id)
    monkeypatch.setenv("TRADINGCODEX_ENABLE_LIVE_EXECUTION", "1")
    submitted = call_mcp_tool(workspace, "submit_approved_order", {"principal_id": "execution-operator", "ticket_id": ticket_id, "live_confirmation": confirmation})
    assert submitted["status"] == "needs_review", submitted
    crosswalk = call_mcp_tool(workspace, "validate_order_approval_crosswalk", {"principal_id": "execution-operator", "ticket_id": ticket_id})
    assert crosswalk["status"] == "anomaly"
    assert crosswalk["terminal_inference_allowed"] is False
    assert "unresolved_acked_or_unknown" in crosswalk["terminal_inference_blockers"]
    assert crosswalk["rows"][0]["canonical_ticket_id"] == ticket_id
    assert crosswalk["rows"][0]["broker_order_ids"]
    assert "unresolved_acked_or_unknown" in crosswalk["rows"][0]["anomalies"]
    assert crosswalk["rows"][0]["terminal_inference_allowed"] is False

def test_approval_crosswalk_flags_approved_missing_broker_order_without_mutation(tmp_path):
    workspace = make_workspace(tmp_path)
    ticket_id = f"crosswalk-approved-{uuid.uuid4().hex[:12]}"
    broker_id = "fake-live-approved"
    FakeLiveBrokerAdapter.reset()
    register_fake_live_connection(workspace, broker_id)
    enable_live_policy(workspace, broker_id)
    approval = create_pending_fake_live_ticket(workspace, ticket_id, broker_id=broker_id)
    crosswalk = call_mcp_tool(workspace, "validate_order_approval_crosswalk", {"principal_id": "risk-manager", "approval_receipt_id": approval["approval_receipt"]["id"]})
    assert crosswalk["status"] == "anomaly"
    assert crosswalk["terminal_inference_allowed"] is False
    assert crosswalk["anomaly_counts"]["missing_broker_order_id"] == 1
    assert crosswalk["rows"][0]["latest_status"]["ticket_state"] == "APPROVED"
    assert crosswalk["rows"][0]["broker_order_ids"] == []

def test_approval_crosswalk_reports_replacement_lineage_and_duplicate_replacements(tmp_path):
    workspace = make_workspace(tmp_path)
    broker_id = "fake-live-lineage"
    FakeLiveBrokerAdapter.reset()
    register_fake_live_connection(workspace, broker_id)
    enable_live_policy(workspace, broker_id)
    original_id = f"lineage-original-{uuid.uuid4().hex[:8]}"
    replacement_a = f"lineage-replacement-a-{uuid.uuid4().hex[:8]}"
    replacement_b = f"lineage-replacement-b-{uuid.uuid4().hex[:8]}"
    create_pending_fake_live_ticket(workspace, original_id, broker_id=broker_id)
    create_pending_fake_live_ticket(workspace, replacement_a, broker_id=broker_id)
    create_pending_fake_live_ticket(workspace, replacement_b, broker_id=broker_id)

    from apps.orders.models import OrderEvent, OrderTicket
    for replacement_id in (replacement_a, replacement_b):
        replacement = OrderTicket.objects.get(ticket_id=replacement_id)
        payload = dict(replacement.payload or {})
        raw = dict(payload.get("raw") or {})
        raw["replacement_of"] = original_id
        payload["raw"] = raw
        replacement.payload = payload
        replacement.save(update_fields=["payload", "updated_at"])
        OrderEvent.objects.create(ticket=replacement, event_type="replacement_created", actor="test", payload={"original_ticket_id": original_id})
    crosswalk = call_mcp_tool(workspace, "validate_order_approval_crosswalk", {"principal_id": "head-manager", "ticket_id": original_id})

    assert crosswalk["status"] == "anomaly"
    assert crosswalk["terminal_inference_allowed"] is False
    assert "duplicate_replacement" in crosswalk["terminal_inference_blockers"]
    row = crosswalk["rows"][0]
    assert set(row["replacement_lineage"]["replacement_ticket_ids"]) == {replacement_a, replacement_b}
    assert "duplicate_replacement" in row["anomalies"]
    assert "stale_original" in row["anomalies"]

def test_pre_approval_occupancy_blocks_overlapping_unresolved_rows(tmp_path):
    workspace = make_workspace(tmp_path)
    ticket_id = f"occupancy-acked-{uuid.uuid4().hex[:12]}"
    broker_id = "fake-live-occupancy"
    FakeLiveBrokerAdapter.reset()
    FakeLiveBrokerAdapter.submit_mode = "submitted"
    register_fake_live_connection(workspace, broker_id)
    enable_live_policy(workspace, broker_id)
    create_pending_fake_live_ticket(workspace, ticket_id, broker_id=broker_id)
    from apps.orders.models import BrokerOrder, OrderTicket
    ticket = OrderTicket.objects.get(ticket_id=ticket_id)
    ticket.current_state = "ACKED"
    ticket.status = "ACKED"
    ticket.save(update_fields=["current_state", "status", "updated_at"])
    BrokerOrder.objects.create(ticket=ticket, broker_order_id=f"broker-{ticket_id}", broker_status="unknown", metadata={"status": "unknown"})
    occupancy = call_mcp_tool(workspace, "get_pre_approval_occupancy", {"principal_id": "risk-manager", "account_id": "local-paper", "broker_account_id": "fake-account", "symbol": "AAPL", "side": "buy"})
    assert occupancy["status"] == "blocked"
    assert occupancy["overlap_policy"]["disposition"] == "blocked"
    assert ticket_id in occupancy["overlap_policy"]["overlapping_ticket_ids"]
    assert occupancy["reserved_notional_conservative"] == 100.0
    assert occupancy["groups"][0]["unresolved_unknown_count"] == 1

def test_order_lineage_read_tools_are_role_scoped_and_read_only(tmp_path):
    workspace = make_workspace(tmp_path)
    tools = handle_mcp_rpc(workspace, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    tool_by_name = {tool["name"]: tool for tool in tools["result"]["tools"]}
    for name in ("validate_order_approval_crosswalk", "get_pre_approval_occupancy"):
        assert tool_by_name[name]["annotations"]["readOnlyHint"] is True
        assert tool_by_name[name]["annotations"]["destructiveHint"] is False
        assert tool_by_name[name]["annotations"]["requires_approval"] is False
        assert "risk-manager" in tool_by_name[name]["annotations"]["allowed_roles"]
        assert "execution-operator" in tool_by_name[name]["annotations"]["allowed_roles"]
    assert tool_by_name["request_order_approval"]["annotations"]["requires_approval"] is True
    assert tool_by_name["submit_approved_order"]["annotations"]["readOnlyHint"] is False


def test_refresh_broker_order_statuses_tool_is_registered_and_allowlisted(tmp_path: Path) -> None:
    from tradingcodex_service.mcp_runtime import TOOL_SPECS
    from tradingcodex_service.application.agents import AGENT_SPECS

    make_workspace(tmp_path)

    spec = next(s for s in TOOL_SPECS if s.name == "refresh_broker_order_statuses")
    assert "execution-operator" in spec.allowed_roles
    assert spec.handler_name == "refresh_broker_order_statuses"
    assert spec.category == "execution"
    assert spec.experimental is True
    assert "ticket_ids" in spec.input_schema["properties"]
    assert spec.input_schema.get("additionalProperties") is False

    allowlist = AGENT_SPECS["execution-operator"].mcp_allowlist
    assert "refresh_broker_order_statuses" in allowlist
    assert "refresh_broker_order_status" in allowlist


def test_refresh_broker_order_statuses_is_listed_in_static_execution_operator_template(tmp_path: Path) -> None:
    from tradingcodex_service.application.agents import AGENT_SPECS
    from tradingcodex_service.mcp_runtime import TOOL_SPECS

    make_workspace(tmp_path)

    template_path = (
        Path(__file__).resolve().parents[1]
        / "workspace_templates"
        / "modules"
        / "fixed-subagents"
        / "files"
        / ".codex"
        / "agents"
        / "execution-operator.toml"
    )
    text = template_path.read_text(encoding="utf-8")
    assert '"refresh_broker_order_statuses"' in text
    assert '"refresh_broker_order_status"' in text

    spec = next(s for s in TOOL_SPECS if s.name == "refresh_broker_order_statuses")
    assert "execution-operator" in spec.allowed_roles
    assert "refresh_broker_order_statuses" in AGENT_SPECS["execution-operator"].mcp_allowlist


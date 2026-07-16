from __future__ import annotations

import json
import os
import uuid
from datetime import timedelta
from pathlib import Path

import yaml
import pytest

from tradingcodex_cli.generator import bootstrap_workspace
from tradingcodex_service.application.execution_gateway import parse_native_execution_invocation
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
    _WORKSPACE_PROVIDER_CACHE,
    _WORKSPACE_PROVIDER_SOURCES,
    approve_workspace_broker_provider_source,
    connect_broker_connector,
    get_broker_connection_status,
    register_broker_adapter_provider,
    register_broker_connector,
    sync_broker_account,
    validate_broker_connector_build,
)
from tradingcodex_service.application.orders import (
    cancel_submitted_order,
    create_order_ticket,
    get_order_status,
    _refresh_broker_order_status_for_reconciliation,
    submit_approved_order,
    validate_approval_receipt,
)
from tradingcodex_service.application.operator_authority import (
    PROVIDER_SOURCE_APPROVE,
    _issue_operator_authority,
    provider_source_approval_resource,
)
from tradingcodex_service.application.runtime import active_profile_for_workspace, ensure_runtime_database
from tradingcodex_service.mcp_runtime import TOOL_REGISTRY, call_mcp_tool, handle_mcp_rpc


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def restore_broker_provider_registry():
    previous = dict(_BROKER_ADAPTER_PROVIDERS)
    previous_cache = dict(_WORKSPACE_PROVIDER_CACHE)
    previous_sources = dict(_WORKSPACE_PROVIDER_SOURCES)
    yield
    _BROKER_ADAPTER_PROVIDERS.clear()
    _BROKER_ADAPTER_PROVIDERS.update(previous)
    _WORKSPACE_PROVIDER_CACHE.clear()
    _WORKSPACE_PROVIDER_CACHE.update(previous_cache)
    _WORKSPACE_PROVIDER_SOURCES.clear()
    _WORKSPACE_PROVIDER_SOURCES.update(previous_sources)


def make_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    bootstrap_workspace(workspace)
    return workspace


def issue_test_provider_approval_authority(workspace: Path, provider_id: str, bundle_sha256: str):
    """Test-only stand-in for the CLI's completed interactive confirmation."""

    return _issue_operator_authority(
        workspace,
        action=PROVIDER_SOURCE_APPROVE,
        resource=provider_source_approval_resource(provider_id, bundle_sha256),
    )


def native_submit(workspace: Path, ticket_id: str, receipt_id: str, live_confirmation: str = "") -> dict:
    prompt = f"$tcx-order-submit --ticket-id {ticket_id} --approval-receipt-id {receipt_id}"
    if live_confirmation:
        prompt += f" --live-confirmation {live_confirmation}"
    mandate = parse_native_execution_invocation(prompt, workspace)
    assert mandate is not None
    return submit_approved_order(workspace, mandate.service_arguments(), native_mandate=mandate)


def native_cancel(
    workspace: Path,
    ticket_id: str,
    broker_order_id: str,
    receipt_id: str,
    live_confirmation: str = "",
) -> dict:
    prompt = (
        f"$tcx-order-cancel --ticket-id {ticket_id} --broker-order-id {broker_order_id} "
        f"--approval-receipt-id {receipt_id}"
    )
    if live_confirmation:
        prompt += f" --live-confirmation {live_confirmation}"
    mandate = parse_native_execution_invocation(prompt, workspace)
    assert mandate is not None
    return cancel_submitted_order(workspace, mandate.service_arguments(), native_mandate=mandate)


class FakeLiveBrokerAdapter(BrokerAdapter):
    provider_id = "fake-live-provider"
    submit_mode = "filled"
    health_status = "ok"
    health_message = "fake signed health"
    health_details: dict = {"signed": True}
    sync_error = ""
    account_label = "Fake Live Account"
    account_type = "live"
    masked_identifier = "fake-***-acct"
    account_metadata: dict = {}
    submit_calls: list[dict] = []
    cancel_calls: list[str] = []
    status_calls: list[str] = []

    def __init__(self, connection, workspace_root: Path | str | None = None) -> None:
        self.connection = connection
        self.workspace_root = workspace_root

    @classmethod
    def reset(cls) -> None:
        cls.submit_mode = "filled"
        cls.health_status = "ok"
        cls.health_message = "fake signed health"
        cls.health_details = {"signed": True}
        cls.sync_error = ""
        cls.account_label = "Fake Live Account"
        cls.account_type = "live"
        cls.masked_identifier = "fake-***-acct"
        cls.account_metadata = {}
        cls.submit_calls = []
        cls.cancel_calls = []
        cls.status_calls = []

    def describe_capabilities(self) -> dict:
        metadata = self.connection.metadata if isinstance(self.connection.metadata, dict) else {}
        return metadata.get("capability_profile") if isinstance(metadata.get("capability_profile"), dict) else {}

    def health_check(self) -> BrokerHealth:
        return BrokerHealth(self.health_status, self.health_message, dict(self.health_details))

    def discover_accounts(self) -> list[BrokerAccountDTO]:
        if self.sync_error:
            raise RuntimeError(self.sync_error)
        return [
            BrokerAccountDTO(
                broker_account_id="fake-account",
                account_label=self.account_label,
                account_type=self.account_type,
                base_currency="USD",
                masked_identifier=self.masked_identifier,
                trading_enabled=True,
                metadata=dict(self.account_metadata),
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


def register_fake_live_connection(workspace: Path, broker_id: str = "fake-live") -> None:
    install_fake_live_provider()
    register_broker_connector(
        workspace,
        {
            "principal_id": "head-manager",
            "provider_id": FakeLiveBrokerAdapter.provider_id,
            "broker_id": broker_id,
            "credential_ref": "env:FAKE_LIVE",
            "environment": "live",
        },
    )
    from apps.integrations.models import AdapterDefinition

    AdapterDefinition.objects.filter(adapter_id=FakeLiveBrokerAdapter.provider_id).update(enabled=True, live=True)
    status = validate_broker_connector_build(
        workspace,
        {"principal_id": "head-manager", "broker_id": broker_id},
    )["connection"]
    assert status["health"]["status"] == "ok"
    assert status["connection"]["status"] == "trading_enabled"
    assert "order.submit.live" in status["connection"]["enabled_trade_scopes"]
    sync_broker_account(workspace, {"broker_id": broker_id, "principal_id": "portfolio-manager"})


def create_approved_fake_live_ticket(workspace: Path, ticket_id: str, broker_id: str = "fake-live") -> dict[str, str]:
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
    return {
        "live_confirmation": f"LIVE:{ticket_id}:{broker_id}:AAPL:buy:1.000000",
        "approval_receipt_id": approval["approval_receipt"]["approval_receipt_id"],
    }


def test_paper_broker_sync_creates_ledger_snapshot_and_reconciliation(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)

    result = sync_broker_account(workspace, {"broker_id": "paper-trading", "principal_id": "portfolio-manager"})
    account_id = active_profile_for_workspace(workspace)["account_id"]

    assert result["status"] == "ok"
    assert result["accounts"][0]["broker_account_id"] == account_id

    from apps.integrations.models import BrokerAccount, BrokerConnection
    from apps.portfolio.models import BrokerSyncRun, PortfolioLedgerEvent, PortfolioSnapshot, ReconciliationRun

    connection = BrokerConnection.objects.get(broker_id="paper-trading")
    assert connection.transport == "paper"
    assert connection.credential_ref == ""
    assert BrokerAccount.objects.filter(broker_connection=connection, broker_account_id=account_id).exists()
    assert BrokerSyncRun.objects.filter(broker_connection=connection, status="ok").exists()
    assert PortfolioLedgerEvent.objects.filter(broker_connection=connection, event_type="cash").exists()
    assert PortfolioSnapshot.objects.filter(source="paper-trading", account_id=account_id).exists()
    assert ReconciliationRun.objects.filter(broker_connection=connection, status="clean").exists()


def test_order_ticket_id_is_immutable_and_same_payload_is_idempotent(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    payload = {
        "principal_id": "portfolio-manager",
        "ticket_id": f"immutable-{uuid.uuid4().hex[:12]}",
        "symbol": "MSFT",
        "side": "buy",
        "quantity": 1,
        "order_type": "limit",
        "limit_price": 100,
    }

    created = create_order_ticket(workspace, payload)
    repeated = create_order_ticket(workspace, payload)

    assert created["status"] == "created"
    assert repeated["status"] == "existing"
    assert repeated["ticket"]["current_state"] == "DRAFT"

    with pytest.raises(ValueError, match="already exists with a different payload"):
        create_order_ticket(workspace, {**payload, "quantity": 2})

    from apps.orders.models import OrderTicket

    ticket = OrderTicket.objects.get(ticket_id=payload["ticket_id"])
    assert str(ticket.quantity) == "1.000000"
    assert ticket.payload_hash == created["ticket"]["payload_hash"]


def test_order_ticket_checks_approval_scope_submit_fill_and_duplicate_block(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    ensure_runtime_database(workspace)

    from apps.policy.models import Capability, Principal
    from tradingcodex_service import mcp_runtime

    risk_principal, _ = Principal.objects.get_or_create(principal_id="risk-manager", defaults={"role": "risk-manager", "active": True})
    Capability.objects.get_or_create(principal=risk_principal, action="order_ticket.create", resource_pattern="*", defaults={"effect": "allow"})
    mcp_runtime._REGISTRY_SYNCED = False
    mcp_runtime._REGISTRY_SYNCED_DB = ""

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
    assert receipt["ticket_id"] == "prd-ticket-1"
    assert receipt["broker_id"] == "paper-trading"

    from apps.orders.models import ApprovalReceipt, Fill, OrderEvent, OrderTicket

    ticket = OrderTicket.objects.get(ticket_id="prd-ticket-1")
    ApprovalReceipt.objects.filter(approval_receipt_id=receipt["approval_receipt_id"]).update(exact_order_hash="forged")
    invalid = validate_approval_receipt(
        workspace,
        {"ticket_id": "prd-ticket-1", "approval_receipt_id": receipt["approval_receipt_id"]},
    )
    assert invalid["valid"] is False
    assert "exact_order_hash" in "\n".join(invalid["reasons"])
    ApprovalReceipt.objects.filter(approval_receipt_id=receipt["approval_receipt_id"]).update(exact_order_hash=receipt["exact_order_hash"])

    submitted = native_submit(workspace, "prd-ticket-1", receipt["approval_receipt_id"])
    assert submitted["status"] == "accepted", submitted

    ticket.refresh_from_db()
    assert ticket.current_state == "FILLED"
    assert Fill.objects.filter(ticket=ticket).exists()
    assert OrderEvent.objects.filter(ticket=ticket, event_type__in={"acked", "fill"}).count() >= 2

    duplicate = native_submit(workspace, "prd-ticket-1", receipt["approval_receipt_id"])
    assert duplicate["status"] == "rejected"
    assert "already has an execution result" in "\n".join(duplicate["reasons"])


def test_instrument_analyst_can_read_constraints_but_not_order_approve_or_submit(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)

    constraints = call_mcp_tool(workspace, "get_broker_instrument_constraints", {"principal_id": "instrument-analyst", "broker_id": "paper-trading", "symbol": "MSFT"})
    assert constraints["broker_id"] == "paper-trading"
    assert constraints["constraints"]["quantity_modes"]

    blocked_tools = [
        ("create_order_ticket", {"ticket_id": "instrument-ticket", "symbol": "MSFT", "side": "buy", "quantity": 1, "order_type": "limit", "limit_price": 1000}),
        ("request_order_approval", {"ticket_id": "missing"}),
    ]
    for tool_name, payload in blocked_tools:
        try:
            call_mcp_tool(workspace, tool_name, {"principal_id": "instrument-analyst", **payload})
        except PermissionError as exc:
            assert "not allowed" in str(exc) or "lacks capability" in str(exc)
        else:
            raise AssertionError(f"instrument-analyst must not call {tool_name}")
    with pytest.raises(ValueError, match="Unknown TradingCodex tool"):
        call_mcp_tool(
            workspace,
            "submit_approved_order",
            {"principal_id": "instrument-analyst", "ticket_id": "missing"},
        )


def test_safe_home_mcp_exposes_only_broker_order_read_status_tools(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    previous = os.environ.get("TRADINGCODEX_MCP_SAFE_TOOLS")
    os.environ["TRADINGCODEX_MCP_SAFE_TOOLS"] = "1"
    try:
        tools = handle_mcp_rpc(workspace, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        tool_names = {tool["name"] for tool in tools["result"]["tools"]}
    finally:
        if previous is None:
            os.environ.pop("TRADINGCODEX_MCP_SAFE_TOOLS", None)
        else:
            os.environ["TRADINGCODEX_MCP_SAFE_TOOLS"] = previous

    assert {"list_broker_connections", "get_broker_connection_status", "list_order_tickets", "get_order_ticket", "list_reconciliation_runs"}.issubset(tool_names)
    assert "sync_broker_account" not in tool_names
    assert "create_order_ticket" not in tool_names
    assert "request_order_approval" not in tool_names
    assert "submit_approved_order" not in tool_names


def test_provider_registry_is_request_driven_and_unknown_broker_scaffolds_development(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    providers = call_mcp_tool(workspace, "list_broker_adapter_providers", {"principal_id": "head-manager"})
    provider_ids = {provider["provider_id"] for provider in providers["providers"]}

    assert "paper" in provider_ids
    assert providers["templates"] == []
    assert providers["named_broker_examples_builtin"] is False
    assert not (provider_ids & {"binance_spot", "regional_equity", "alpaca_rest", "ibkr_gateway"})

    assert "connect_broker_connector" not in TOOL_REGISTRY
    assert "scaffold_broker_connector" not in TOOL_REGISTRY

    rendered = call_mcp_tool(
        workspace,
        "render_broker_connector_scaffold",
        {
            "principal_id": "head-manager",
            "provider_id": "binance",
            "broker_id": "binance",
            "credential_ref": "env:BINANCE_TESTNET",
        },
    )
    assert rendered["status"] == "rendered"
    assert rendered["writes_performed"] is False
    assert rendered["provider_development_required"] is True
    assert not (workspace / "trading/connectors/binance").exists()

    connected = connect_broker_connector(
        workspace,
        {
            "principal_id": "local-operator",
            "provider_id": "binance",
            "broker_id": "binance",
            "credential_ref": "env:BINANCE_TESTNET",
        },
    )
    assert connected["status"] == "provider_missing"
    assert connected["lifecycle_state"] == "provider_missing"
    assert connected["live_order_enabled"] is False
    assert connected["connector_files_created"] is False
    assert not (workspace / "trading/connectors/binance").exists()

    assert not any("connectors register" in step for step in rendered["next"])


def test_connect_read_only_does_not_promote_validation_trade_scopes(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    register_broker_adapter_provider(
        BrokerAdapterProvider(
            provider_id="validation-provider",
            display_name="Validation Provider",
            auth_model={"type": "credential_ref", "credential_ref_required": True},
            execution_posture="broker_validation_only",
            live=False,
            factory=lambda connection, workspace_root: FakeLiveBrokerAdapter(connection, workspace_root),
        )
    )

    connected = connect_broker_connector(
        workspace,
        {
            "principal_id": "local-operator",
            "provider_id": "validation-provider",
            "broker_id": "validation-provider",
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


def test_workspace_provider_file_requires_exact_operator_approval_and_restart(tmp_path: Path, monkeypatch) -> None:
    workspace = make_workspace(tmp_path)
    provider_dir = workspace / "trading" / "connectors" / "demo-live"
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
        return [BrokerAccountDTO("account", "Account", "live", "EUR", "env:DEMO_LIVE_TOKEN", True)]

    def get_cash(self, account_id):
        return [CashDTO("EUR", 1000)]

    def get_positions(self, account_id):
        return []

    def validate_order(self, order):
        return OrderValidationResult(True, [], {"symbol": order.get("symbol")})


PROVIDER = BrokerAdapterProvider(
    provider_id="demo-live",
    display_name="Demo Live Provider",
    region="global",
    asset_classes=("equity", "cash"),
    products=("stock",),
    auth_model={"type": "credential_ref", "credential_ref_required": True},
    execution_posture="live_broker",
    live=True,
    factory=lambda connection, workspace_root: Adapter(connection, workspace_root),
)
""".lstrip(),
        encoding="utf-8",
    )
    (provider_dir / "source-provenance.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "sources": [
                    {
                        "kind": "git",
                        "url": "https://github.com/example/demo-live-provider",
                        "requested_ref": "main",
                        "resolved_commit": "a" * 40,
                        "fetched_content_sha256": "b" * 64,
                        "retrieved_at": "2026-07-16T01:02:03Z",
                    }
                ],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    providers = call_mcp_tool(workspace, "list_broker_adapter_providers", {"principal_id": "head-manager"})
    assert "demo-live" not in {provider["provider_id"] for provider in providers["providers"]}
    source_status = next(item for item in providers["workspace_provider_sources"] if item["provider_id"] == "demo-live")
    assert source_status["approval_status"] == "approval_required"
    assert source_status["source_provenance"]["status"] == "validated"
    assert source_status["source_provenance"]["source_count"] == 1
    assert source_status["source_provenance"]["sources"][0]["url"] == (
        "https://github.com/example/demo-live-provider"
    )

    approved = approve_workspace_broker_provider_source(
        workspace,
        "demo-live",
        expected_bundle_sha256=source_status["bundle_sha256"],
        operator_authority=issue_test_provider_approval_authority(
            workspace,
            "demo-live",
            source_status["bundle_sha256"],
        ),
    )
    assert approved["service_restart_required"] is True
    providers = call_mcp_tool(workspace, "list_broker_adapter_providers", {"principal_id": "head-manager"})
    assert "demo-live" not in {provider["provider_id"] for provider in providers["providers"]}

    from django.utils import timezone as django_timezone

    monkeypatch.setattr(
        "tradingcodex_service.application.brokers._PROVIDER_RUNTIME_STARTED_AT",
        django_timezone.now() + timedelta(days=1),
    )
    providers = call_mcp_tool(workspace, "list_broker_adapter_providers", {"principal_id": "head-manager"})
    assert "demo-live" in {provider["provider_id"] for provider in providers["providers"]}

    registered = register_broker_connector(
        workspace,
        {"principal_id": "head-manager", "provider_id": "demo-live", "broker_id": "demo-live", "credential_ref": "env:DEMO_LIVE_TOKEN", "environment": "live"},
    )
    assert registered["connection"]["status"] == "read_only"

    from apps.integrations.models import AdapterDefinition

    AdapterDefinition.objects.filter(adapter_id="demo-live").update(enabled=True, live=True)
    status = validate_broker_connector_build(
        workspace,
        {"principal_id": "head-manager", "broker_id": "demo-live"},
    )["connection"]
    assert status["health"]["status"] == "ok"
    assert status["connection"]["status"] == "trading_enabled"
    assert "order.submit.live" in status["connection"]["enabled_trade_scopes"]

    provider_path.write_text(provider_path.read_text(encoding="utf-8").replace("signed health ok", "changed health ok"), encoding="utf-8")
    listed = call_mcp_tool(workspace, "list_broker_connections", {"principal_id": "head-manager"})
    listed_demo = next(connection for connection in listed["connections"] if connection["broker_id"] == "demo-live")
    assert listed_demo["service_restart_required"] is True
    assert listed_demo["lifecycle_state"] == "review_required"
    assert listed_demo["trading_status"] == "locked"

    drifted = validate_broker_connector_build(
        workspace,
        {"principal_id": "head-manager", "broker_id": "demo-live"},
    )
    assert drifted["service_restart_required"] is True
    assert "provider_source_changed_restart_required" in drifted["blockers"]

    drifted_status = call_mcp_tool(workspace, "get_broker_connection_status", {"principal_id": "head-manager", "broker_id": "demo-live"})
    assert drifted_status["health"]["status"] == "blocked"
    assert drifted_status["connection"]["status"] == "read_only"
    assert drifted_status["connection"]["enabled_trade_scopes"] == []

    changed_status = next(
        item
        for item in call_mcp_tool(
            workspace,
            "list_broker_adapter_providers",
            {"principal_id": "head-manager"},
        )["workspace_provider_sources"]
        if item["provider_id"] == "demo-live"
    )
    assert changed_status["approval_status"] == "stale"
    reapproved = approve_workspace_broker_provider_source(
        workspace,
        "demo-live",
        expected_bundle_sha256=changed_status["bundle_sha256"],
        operator_authority=issue_test_provider_approval_authority(
            workspace,
            "demo-live",
            changed_status["bundle_sha256"],
        ),
    )
    assert reapproved["service_restart_required"] is True
    _WORKSPACE_PROVIDER_CACHE.clear()
    _WORKSPACE_PROVIDER_SOURCES.clear()
    call_mcp_tool(workspace, "list_broker_adapter_providers", {"principal_id": "head-manager"})
    register_broker_connector(
        workspace,
        {
            "principal_id": "head-manager",
            "provider_id": "demo-live",
            "broker_id": "demo-live",
            "credential_ref": "env:DEMO_LIVE_TOKEN",
            "environment": "live",
        },
    )
    revalidated = validate_broker_connector_build(
        workspace,
        {"principal_id": "head-manager", "broker_id": "demo-live"},
    )
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

    approval = create_approved_fake_live_ticket(workspace, ticket_id, broker_id)
    confirmation = approval["live_confirmation"]
    receipt_id = approval["approval_receipt_id"]

    from apps.orders.models import BrokerOrder, ExecutionResult, Fill
    from apps.portfolio.models import PortfolioLedgerEvent, PortfolioSnapshot, ReconciliationRun

    rejected_env = native_submit(workspace, ticket_id, receipt_id, confirmation)
    assert rejected_env["status"] == "rejected"
    assert "TRADINGCODEX_ENABLE_LIVE_EXECUTION=1" in "\n".join(rejected_env["reasons"])
    assert ExecutionResult.objects.filter(order_ticket_id=ticket_id).count() == 0
    assert FakeLiveBrokerAdapter.submit_calls == []

    monkeypatch.setenv("TRADINGCODEX_ENABLE_LIVE_EXECUTION", "1")
    rejected_confirmation = native_submit(workspace, ticket_id, receipt_id)
    assert rejected_confirmation["status"] == "rejected"
    assert "live confirmation required" in "\n".join(rejected_confirmation["reasons"])
    assert FakeLiveBrokerAdapter.submit_calls == []

    from apps.integrations.models import BrokerConnection

    connection = BrokerConnection.objects.get(broker_id=broker_id)
    connection.enabled_trade_scopes = []
    connection.save(update_fields=["enabled_trade_scopes"])
    rejected_scope = native_submit(workspace, ticket_id, receipt_id, confirmation)
    assert rejected_scope["status"] == "rejected"
    assert "no enabled trade scopes" in "\n".join(rejected_scope["reasons"])
    assert ExecutionResult.objects.filter(order_ticket_id=ticket_id).count() == 0
    assert FakeLiveBrokerAdapter.submit_calls == []

    validate_broker_connector_build(
        workspace,
        {"principal_id": "head-manager", "broker_id": broker_id},
    )
    FakeLiveBrokerAdapter.health_status = "error"
    rejected_health = native_submit(workspace, ticket_id, receipt_id, confirmation)
    assert rejected_health["status"] == "rejected"
    assert "broker health is error" in "\n".join(rejected_health["reasons"])
    assert ExecutionResult.objects.filter(order_ticket_id=ticket_id).count() == 0
    assert FakeLiveBrokerAdapter.submit_calls == []

    FakeLiveBrokerAdapter.health_status = "ok"
    validate_broker_connector_build(
        workspace,
        {"principal_id": "head-manager", "broker_id": broker_id},
    )
    submitted = native_submit(workspace, ticket_id, receipt_id, confirmation)
    assert submitted["status"] == "accepted", submitted
    assert len(FakeLiveBrokerAdapter.submit_calls) == 1
    assert FakeLiveBrokerAdapter.submit_calls[0]["client_order_id"].startswith("tcx-")

    assert ExecutionResult.objects.filter(order_ticket_id=ticket_id, status="accepted").count() == 1
    assert BrokerOrder.objects.filter(ticket__ticket_id=ticket_id, broker_status="filled").exists()
    assert Fill.objects.filter(ticket__ticket_id=ticket_id).exists()
    assert PortfolioLedgerEvent.objects.filter(event_type="fill", broker_connection__broker_id=broker_id).exists()
    assert PortfolioSnapshot.objects.filter(source=broker_id, account_id=active_profile_for_workspace(workspace)["account_id"]).exists()
    assert ReconciliationRun.objects.filter(broker_connection__broker_id=broker_id, status="clean").exists()

    duplicate = native_submit(workspace, ticket_id, receipt_id, confirmation)
    assert duplicate["status"] == "rejected"
    assert "already has an execution result" in "\n".join(duplicate["reasons"])
    assert len(FakeLiveBrokerAdapter.submit_calls) == 1

    with pytest.raises(ValueError, match="already exists with a different payload"):
        create_order_ticket(
            workspace,
            {
                "principal_id": "portfolio-manager",
                "ticket_id": ticket_id,
                "broker_id": broker_id,
                "broker_account_id": "fake-account",
                "symbol": "AAPL",
                "side": "buy",
                "quantity": 2,
                "order_type": "limit",
                "limit_price": 100,
                "time_in_force": "day",
                "currency": "USD",
            },
        )
    broker_order = BrokerOrder.objects.get(ticket__ticket_id=ticket_id)
    with pytest.raises(ValueError, match="Unknown TradingCodex tool"):
        call_mcp_tool(
            workspace,
            "refresh_broker_order_status",
            {"principal_id": "portfolio-manager", "ticket_id": ticket_id, "broker_order_id": broker_order.broker_order_id},
        )
    refreshed = _refresh_broker_order_status_for_reconciliation(
        workspace,
        {"principal_id": "system", "ticket_id": ticket_id, "broker_order_id": broker_order.broker_order_id},
    )
    assert refreshed["status"] == "refreshed"
    assert refreshed["broker_status"] == "filled"
    assert FakeLiveBrokerAdapter.status_calls == [broker_order.broker_order_id]


def test_fake_live_provider_cancel_calls_provider_cancel_path(tmp_path: Path, monkeypatch) -> None:
    workspace = make_workspace(tmp_path)
    ticket_id = f"fake-live-cancel-{uuid.uuid4().hex[:12]}"
    broker_id = "fake-live-cancel"
    FakeLiveBrokerAdapter.reset()
    FakeLiveBrokerAdapter.submit_mode = "submitted"
    register_fake_live_connection(workspace, broker_id)
    enable_live_policy(workspace, broker_id)
    approval = create_approved_fake_live_ticket(workspace, ticket_id, broker_id)
    confirmation = approval["live_confirmation"]
    receipt_id = approval["approval_receipt_id"]
    monkeypatch.setenv("TRADINGCODEX_ENABLE_LIVE_EXECUTION", "1")

    submitted = native_submit(workspace, ticket_id, receipt_id, confirmation)
    assert submitted["status"] == "accepted", submitted

    cancel = native_cancel(
        workspace,
        ticket_id,
        submitted["result"]["broker_order_id"],
        receipt_id,
        f"CANCEL:{ticket_id}:{broker_id}:{submitted['result']['broker_order_id']}",
    )
    assert cancel["status"] == "canceled"
    assert cancel["ticket"]["current_state"] == "CANCELED"
    assert cancel["ticket"]["broker_orders"][0]["broker_status"] == "canceled"
    assert FakeLiveBrokerAdapter.cancel_calls == [submitted["result"]["broker_order_id"]]
    from apps.audit.models import AuditEvent
    from apps.orders.models import BrokerOrder

    broker_order = BrokerOrder.objects.get(ticket__ticket_id=ticket_id)
    assert broker_order.metadata["cancel_intent"]["status"] == "canceled"
    assert AuditEvent.objects.filter(action="order_ticket.cancel.intent", resource=ticket_id).exists()
    assert AuditEvent.objects.filter(action="order_ticket.cancel.finalized", resource=ticket_id).exists()


def test_fake_live_cancel_finalization_failure_is_needs_review_and_not_retried(tmp_path: Path, monkeypatch) -> None:
    workspace = make_workspace(tmp_path)
    ticket_id = f"fake-live-cancel-uncertain-{uuid.uuid4().hex[:12]}"
    broker_id = "fake-live-cancel-uncertain"
    FakeLiveBrokerAdapter.reset()
    FakeLiveBrokerAdapter.submit_mode = "submitted"
    register_fake_live_connection(workspace, broker_id)
    enable_live_policy(workspace, broker_id)
    approval = create_approved_fake_live_ticket(workspace, ticket_id, broker_id)
    confirmation = approval["live_confirmation"]
    receipt_id = approval["approval_receipt_id"]
    monkeypatch.setenv("TRADINGCODEX_ENABLE_LIVE_EXECUTION", "1")
    submitted = native_submit(workspace, ticket_id, receipt_id, confirmation)
    assert submitted["status"] == "accepted", submitted

    from tradingcodex_service.application import orders as order_service

    original_transition = order_service.transition_order_ticket

    def fail_cancel_transition(ticket, target_state, actor, payload=None):
        if target_state == "CANCELED":
            raise RuntimeError("simulated local finalization failure")
        return original_transition(ticket, target_state, actor, payload)

    monkeypatch.setattr(order_service, "transition_order_ticket", fail_cancel_transition)
    broker_order_id = submitted["result"]["broker_order_id"]
    cancel_confirmation = f"CANCEL:{ticket_id}:{broker_id}:{broker_order_id}"
    result = native_cancel(workspace, ticket_id, broker_order_id, receipt_id, cancel_confirmation)
    assert result["status"] == "needs_review"
    assert "finalization failed" in "\n".join(result["reasons"])
    assert FakeLiveBrokerAdapter.cancel_calls == [submitted["result"]["broker_order_id"]]

    from apps.orders.models import BrokerOrder, OrderTicket

    broker_order = BrokerOrder.objects.get(ticket__ticket_id=ticket_id)
    assert broker_order.broker_status == "unknown"
    assert broker_order.metadata["cancel_intent"]["status"] == "needs_review"
    assert OrderTicket.objects.get(ticket_id=ticket_id).current_state == "NEEDS_REVIEW"

    retry = native_cancel(workspace, ticket_id, broker_order_id, receipt_id, cancel_confirmation)
    assert retry["status"] == "not_cancelable"
    assert FakeLiveBrokerAdapter.cancel_calls == [submitted["result"]["broker_order_id"]]


def test_fake_live_uncertain_submit_records_needs_review_and_blocks_retry(tmp_path: Path, monkeypatch) -> None:
    workspace = make_workspace(tmp_path)
    ticket_id = f"fake-live-uncertain-{uuid.uuid4().hex[:12]}"
    broker_id = "fake-live-uncertain"
    FakeLiveBrokerAdapter.reset()
    FakeLiveBrokerAdapter.submit_mode = "uncertain"
    register_fake_live_connection(workspace, broker_id)
    enable_live_policy(workspace, broker_id)
    approval = create_approved_fake_live_ticket(workspace, ticket_id, broker_id)
    confirmation = approval["live_confirmation"]
    receipt_id = approval["approval_receipt_id"]
    monkeypatch.setenv("TRADINGCODEX_ENABLE_LIVE_EXECUTION", "1")

    result = native_submit(workspace, ticket_id, receipt_id, confirmation)
    assert result["status"] == "needs_review", result
    assert len(FakeLiveBrokerAdapter.submit_calls) == 1

    from apps.orders.models import BrokerOrder, ExecutionResult, OrderTicket

    assert ExecutionResult.objects.filter(order_ticket_id=ticket_id, status="needs_review").count() == 1
    assert OrderTicket.objects.get(ticket_id=ticket_id).current_state == "NEEDS_REVIEW"
    assert BrokerOrder.objects.filter(ticket__ticket_id=ticket_id, broker_status="unknown").exists()

    duplicate = native_submit(workspace, ticket_id, receipt_id, confirmation)
    assert duplicate["status"] == "rejected"
    assert "already has an execution result" in "\n".join(duplicate["reasons"])
    assert len(FakeLiveBrokerAdapter.submit_calls) == 1


@pytest.mark.parametrize("raise_uncertain", [False, True])
def test_provider_secrets_never_persist_or_reappear_in_order_status(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    raise_uncertain: bool,
) -> None:
    workspace = make_workspace(tmp_path)
    canary = f"provider-secret-{uuid.uuid4().hex}"
    ticket_id = f"fake-live-redaction-{uuid.uuid4().hex[:12]}"
    broker_id = f"fake-live-redaction-{uuid.uuid4().hex[:8]}"
    FakeLiveBrokerAdapter.reset()
    register_fake_live_connection(workspace, broker_id)
    enable_live_policy(workspace, broker_id)
    approval = create_approved_fake_live_ticket(workspace, ticket_id, broker_id)
    confirmation = approval["live_confirmation"]
    receipt_id = approval["approval_receipt_id"]
    monkeypatch.setenv("TRADINGCODEX_ENABLE_LIVE_EXECUTION", "1")

    def malicious_submit(self, order):
        self.submit_calls.append(dict(order))
        broker_order_id = f"fake-{order['client_order_id']}"
        if raise_uncertain:
            raise BrokerSubmissionUncertainError(
                canary,
                broker_order_id=broker_order_id,
                status_payload={"authorization": canary, "raw_http_body": canary},
            )
        return {
            "broker_order_id": broker_order_id,
            "status": "submitted",
            "submitted_at": "2026-01-01T00:00:00Z",
            "authorization": canary,
            "raw_http_body": canary,
            "nested": {"token": canary},
        }

    monkeypatch.setattr(FakeLiveBrokerAdapter, "submit_order", malicious_submit)

    result = native_submit(workspace, ticket_id, receipt_id, confirmation)

    from apps.audit.models import AuditEvent
    from apps.orders.models import BrokerOrder, ExecutionResult, OrderEvent

    public_status = get_order_status(workspace, {"ticket_id": ticket_id})
    durable = {
        "executions": list(ExecutionResult.objects.filter(order_ticket_id=ticket_id).values("payload")),
        "broker_orders": list(BrokerOrder.objects.filter(ticket__ticket_id=ticket_id).values("metadata")),
        "events": list(OrderEvent.objects.filter(ticket__ticket_id=ticket_id).values("payload")),
        "audits": list(AuditEvent.objects.filter(resource=ticket_id).values("payload")),
    }
    serialized = json.dumps(
        {"result": result, "status": public_status, "durable": durable},
        default=str,
        sort_keys=True,
    )

    assert result["status"] == ("needs_review" if raise_uncertain else "accepted")
    assert canary not in serialized
    assert "authorization" not in serialized
    assert "raw_http_body" not in serialized


def test_provider_health_text_is_canonicalized_before_execution_or_persistence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = make_workspace(tmp_path)
    canary = f"provider-health-secret-{uuid.uuid4().hex}"
    ticket_id = f"fake-live-health-{uuid.uuid4().hex[:12]}"
    broker_id = f"fake-live-health-{uuid.uuid4().hex[:8]}"
    FakeLiveBrokerAdapter.reset()
    register_fake_live_connection(workspace, broker_id)
    enable_live_policy(workspace, broker_id)
    approval = create_approved_fake_live_ticket(workspace, ticket_id, broker_id)
    monkeypatch.setenv("TRADINGCODEX_ENABLE_LIVE_EXECUTION", "1")
    FakeLiveBrokerAdapter.health_status = "error"
    FakeLiveBrokerAdapter.health_message = canary
    FakeLiveBrokerAdapter.health_details = {"authorization": canary, "raw_http_body": canary}

    result = native_submit(
        workspace,
        ticket_id,
        approval["approval_receipt_id"],
        approval["live_confirmation"],
    )

    from apps.audit.models import AuditEvent
    from apps.integrations.models import BrokerConnection

    connection = BrokerConnection.objects.get(broker_id=broker_id)
    public_status = get_broker_connection_status(workspace, {"broker_id": broker_id})
    serialized = json.dumps(
        {
            "result": result,
            "connection_metadata": connection.metadata,
            "public_status": public_status,
            "audits": list(AuditEvent.objects.all().values("payload")),
        },
        default=str,
        sort_keys=True,
    )

    assert result["status"] == "rejected"
    assert FakeLiveBrokerAdapter.submit_calls == []
    assert canary not in serialized
    assert "authorization" not in serialized
    assert "raw_http_body" not in serialized


def test_post_fill_account_sync_error_is_canonicalized_and_not_retried(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = make_workspace(tmp_path)
    canary = f"provider-sync-secret-{uuid.uuid4().hex}"
    ticket_id = f"fake-live-sync-{uuid.uuid4().hex[:12]}"
    broker_id = f"fake-live-sync-{uuid.uuid4().hex[:8]}"
    FakeLiveBrokerAdapter.reset()
    register_fake_live_connection(workspace, broker_id)
    enable_live_policy(workspace, broker_id)
    approval = create_approved_fake_live_ticket(workspace, ticket_id, broker_id)
    monkeypatch.setenv("TRADINGCODEX_ENABLE_LIVE_EXECUTION", "1")
    FakeLiveBrokerAdapter.sync_error = canary

    result = native_submit(
        workspace,
        ticket_id,
        approval["approval_receipt_id"],
        approval["live_confirmation"],
    )
    retry = native_submit(
        workspace,
        ticket_id,
        approval["approval_receipt_id"],
        approval["live_confirmation"],
    )

    from apps.audit.models import AuditEvent
    from apps.orders.models import BrokerOrder, ExecutionResult, OrderEvent
    from apps.portfolio.models import BrokerSyncRun

    serialized = json.dumps(
        {
            "result": result,
            "retry": retry,
            "public_status": get_order_status(workspace, {"ticket_id": ticket_id}),
            "sync_runs": list(BrokerSyncRun.objects.filter(broker_connection__broker_id=broker_id).values("error")),
            "executions": list(ExecutionResult.objects.filter(order_ticket_id=ticket_id).values("payload")),
            "broker_orders": list(BrokerOrder.objects.filter(ticket__ticket_id=ticket_id).values("metadata")),
            "events": list(OrderEvent.objects.filter(ticket__ticket_id=ticket_id).values("payload")),
            "audits": list(AuditEvent.objects.all().values("payload")),
        },
        default=str,
        sort_keys=True,
    )

    assert result["status"] == "needs_review"
    assert retry["status"] == "rejected"
    assert len(FakeLiveBrokerAdapter.submit_calls) == 1
    assert canary not in serialized


def test_successful_account_sync_discards_provider_display_metadata(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    canary = f"provider-account-secret-{uuid.uuid4().hex}"
    broker_id = f"fake-live-account-{uuid.uuid4().hex[:8]}"
    FakeLiveBrokerAdapter.reset()
    register_fake_live_connection(workspace, broker_id)
    FakeLiveBrokerAdapter.account_label = canary
    FakeLiveBrokerAdapter.account_type = canary
    FakeLiveBrokerAdapter.masked_identifier = canary
    FakeLiveBrokerAdapter.account_metadata = {
        "authorization": canary,
        "raw_http_body": canary,
    }

    result = sync_broker_account(
        workspace,
        {"broker_id": broker_id, "principal_id": "portfolio-manager"},
    )

    from apps.audit.models import AuditEvent
    from apps.integrations.models import BrokerAccount

    account = BrokerAccount.objects.get(
        broker_connection__broker_id=broker_id,
        broker_account_id="fake-account",
    )
    serialized = json.dumps(
        {
            "result": result,
            "account": {
                "account_label": account.account_label,
                "account_type": account.account_type,
                "masked_identifier": account.masked_identifier,
                "metadata": account.metadata,
            },
            "public_status": get_broker_connection_status(workspace, {"broker_id": broker_id}),
            "audits": list(AuditEvent.objects.all().values("payload")),
        },
        default=str,
        sort_keys=True,
    )

    assert result["status"] == "ok"
    assert account.account_type == "unknown"
    assert account.metadata == {}
    assert canary not in serialized
    assert "authorization" not in serialized
    assert "raw_http_body" not in serialized

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

from django.test import Client

from tradingcodex_cli.generator import bootstrap_workspace
from tradingcodex_service.application.brokers import (
    create_external_mcp_broker_connection,
    record_broker_mapping_review,
    sync_broker_account,
)
from tradingcodex_service.application.orders import create_order_ticket, order_payload_from_ticket, validate_approval_receipt
from tradingcodex_service.application.runtime import ensure_runtime_database
from tradingcodex_service.mcp_runtime import call_mcp_tool, handle_mcp_rpc


ROOT = Path(__file__).resolve().parents[1]


def make_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    bootstrap_workspace(workspace, force=True)
    return workspace


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
                "ticket_id": "risk-created-service-ticket",
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


def test_binance_spot_testnet_connector_lifecycle_through_execution_boundary(tmp_path: Path, monkeypatch) -> None:
    workspace = make_workspace(tmp_path)
    ticket_id = f"binance-testnet-ticket-{uuid.uuid4().hex[:12]}"
    monkeypatch.setenv("BINANCE_TESTNET_API_KEY", "unit-api-key")
    monkeypatch.setenv("BINANCE_TESTNET_SECRET_KEY", "unit-secret-key")
    monkeypatch.setenv("TRADINGCODEX_BINANCE_TESTNET_SUBMIT_MODE", "order_test")

    calls: list[dict[str, str]] = []

    def fake_binance_request(method: str, base_url: str, path: str, query: str, headers: dict[str, str]) -> dict:
        calls.append({"method": method, "base_url": base_url, "path": path, "query": query, "api_key": headers.get("X-MBX-APIKEY", "")})
        assert base_url == "https://testnet.binance.vision"
        if path == "/api/v3/time":
            return {"serverTime": 1780000000000}
        if path == "/api/v3/exchangeInfo":
            assert "symbol=BTCUSDT" in query
            return {
                "symbols": [
                    {
                        "symbol": "BTCUSDT",
                        "baseAsset": "BTC",
                        "quoteAsset": "USDT",
                        "orderTypes": ["LIMIT", "MARKET"],
                        "timeInForce": ["GTC", "IOC", "FOK"],
                        "filters": [
                            {"filterType": "PRICE_FILTER", "tickSize": "0.01000000"},
                            {"filterType": "LOT_SIZE", "minQty": "0.00001000", "stepSize": "0.00001000"},
                            {"filterType": "MIN_NOTIONAL", "minNotional": "5.00000000"},
                        ],
                    }
                ]
            }
        assert headers.get("X-MBX-APIKEY") == "unit-api-key"
        assert "signature=" in query
        assert "unit-secret-key" not in query
        if path == "/api/v3/account":
            return {
                "accountType": "SPOT",
                "canTrade": True,
                "permissions": ["SPOT"],
                "balances": [
                    {"asset": "USDT", "free": "1000.00000000", "locked": "0.00000000"},
                    {"asset": "BTC", "free": "0.01000000", "locked": "0.00000000"},
                ],
            }
        if path == "/api/v3/order/test":
            assert method == "POST"
            assert "symbol=BTCUSDT" in query
            assert "side=BUY" in query
            assert "type=LIMIT" in query
            assert "timeInForce=GTC" in query
            assert "quantity=0.0001" in query
            assert "price=50000" in query
            return {}
        raise AssertionError(f"unexpected Binance call: {method} {path} {query}")

    monkeypatch.setattr("tradingcodex_service.application.brokers._binance_http_request", fake_binance_request)

    registered = call_mcp_tool(
        workspace,
        "register_broker_connector",
        {
            "principal_id": "head-manager",
            "template": "binance_spot",
            "broker_id": "binance-spot-testnet",
            "credential_ref": "env:BINANCE_TESTNET",
            "environment": "testnet",
        },
    )
    assert registered["connection"]["status"] == "read_only"
    assert registered["connection"]["enabled_trade_scopes"] == []
    assert registered["connection"]["metadata"]["credential_validation_status"] == "not_checked"
    assert registered["capability_profile"]["execution_posture"] == "broker_validation_only"
    assert "unit-secret-key" not in str(registered)

    status = call_mcp_tool(workspace, "get_broker_connection_status", {"principal_id": "head-manager", "broker_id": "binance-spot-testnet"})
    assert status["health"]["status"] == "ok"
    assert status["connection"]["status"] == "trading_enabled"
    assert status["connection"]["enabled_trade_scopes"] == ["order.submit.validation"]
    assert status["connection"]["metadata"]["credential_validation_status"] == "ok"

    synced = call_mcp_tool(workspace, "sync_broker_account", {"principal_id": "portfolio-manager", "broker_id": "binance-spot-testnet"})
    assert synced["status"] == "ok"
    assert synced["accounts"][0]["broker_account_id"] == "spot-testnet"

    constraints = call_mcp_tool(workspace, "get_broker_instrument_constraints", {"principal_id": "head-manager", "broker_id": "binance-spot-testnet", "symbol": "BTCUSDT"})
    assert constraints["constraints"]["min_notional"] == "5.00000000"
    assert constraints["constraints"]["quantity_increment"] == "0.00001000"

    instrument_constraints = call_mcp_tool(workspace, "get_broker_instrument_constraints", {"principal_id": "instrument-analyst", "broker_id": "binance-spot-testnet", "symbol": "BTCUSDT"})
    assert instrument_constraints["constraints"]["min_notional"] == "5.00000000"

    preview = call_mcp_tool(
        workspace,
        "preview_order_translation",
        {
            "principal_id": "head-manager",
            "broker_id": "binance-spot-testnet",
            "symbol": "BTCUSDT",
            "side": "buy",
            "quantity": 0.0001,
            "order_type": "limit",
            "limit_price": 50000,
            "time_in_force": "GTC",
            "currency": "USDT",
        },
    )
    assert preview["status"] == "validated_on_testnet"
    assert preview["valid"] is True

    denied_submit = call_mcp_tool
    try:
        denied_submit(workspace, "submit_approved_order", {"principal_id": "head-manager", "ticket_id": "missing"})
    except PermissionError as exc:
        assert "not allowed" in str(exc) or "lacks capability" in str(exc)
    else:
        raise AssertionError("head-manager must not submit approved orders")

    created = call_mcp_tool(
        workspace,
        "create_order_ticket",
        {
            "principal_id": "portfolio-manager",
            "ticket_id": ticket_id,
            "broker_id": "binance-spot-testnet",
            "broker_account_id": "spot-testnet",
            "symbol": "BTCUSDT",
            "side": "buy",
            "quantity": 0.0001,
            "order_type": "limit",
            "limit_price": 50000,
            "time_in_force": "GTC",
            "currency": "USDT",
        },
    )
    assert created["ticket"]["broker_connection_id"] == "binance-spot-testnet"

    checks = call_mcp_tool(workspace, "run_order_checks", {"principal_id": "portfolio-manager", "ticket_id": ticket_id})
    assert checks["approval_ready"] is True
    broker_check = next(check for check in checks["checks"] if check["check_type"] == "broker_validate")
    assert broker_check["decision"] == "pass"

    approval = call_mcp_tool(workspace, "request_order_approval", {"principal_id": "risk-manager", "ticket_id": ticket_id})
    assert approval["status"] == "approved"
    assert approval["approval_receipt"]["broker_connection_id"] == "binance-spot-testnet"

    submitted = call_mcp_tool(workspace, "submit_approved_order", {"principal_id": "execution-operator", "ticket_id": ticket_id})
    assert submitted["status"] == "accepted", submitted
    assert submitted["result"]["adapter"] == "binance_spot_testnet"
    assert submitted["result"]["mode"] == "order_test"
    assert submitted["result"]["testnet"] is True
    assert "unit-api-key" not in str(submitted)
    assert "unit-secret-key" not in str(submitted)

    status_read = call_mcp_tool(workspace, "get_order_status", {"principal_id": "head-manager", "ticket_id": ticket_id})
    assert status_read["status"] == "ACKED"
    assert status_read["ticket"]["broker_orders"][0]["broker_status"] == "validated"

    refreshed = call_mcp_tool(workspace, "refresh_broker_order_status", {"principal_id": "execution-operator", "broker_order_id": submitted["result"]["broker_order_id"]})
    assert refreshed["status"] == "refreshed"
    assert refreshed["broker_status"] == "validated"
    assert refreshed["adapter_status"]["mode"] == "order_test"
    assert "does not create an exchange order" in refreshed["adapter_status"]["reason"]

    cancel = call_mcp_tool(workspace, "cancel_approved_order", {"principal_id": "execution-operator", "order_id": submitted["result"]["broker_order_id"]})
    assert cancel["status"] == "not_supported"

    duplicate = call_mcp_tool(workspace, "submit_approved_order", {"principal_id": "execution-operator", "ticket_id": ticket_id})
    assert duplicate["status"] == "rejected"
    assert "already has an execution result" in "\n".join(duplicate["reasons"])

    order_test_calls = [call for call in calls if call["path"] == "/api/v3/order/test"]
    assert len(order_test_calls) >= 3


def test_binance_sync_failure_records_secret_free_credential_diagnostic(tmp_path: Path, monkeypatch) -> None:
    workspace = make_workspace(tmp_path)
    monkeypatch.setenv("BINANCE_TESTNET_API_KEY", "unit-api-key")
    monkeypatch.setenv("BINANCE_TESTNET_SECRET_KEY", "unit-secret-key")

    def fake_binance_request(method: str, base_url: str, path: str, query: str, headers: dict[str, str]) -> dict:
        assert headers.get("X-MBX-APIKEY") == "unit-api-key"
        assert "unit-secret-key" not in query
        if path == "/api/v3/account":
            raise ValueError("Binance error -2015: Invalid API-key, IP, or permissions for action.")
        raise AssertionError(f"unexpected Binance call: {method} {path} {query}")

    monkeypatch.setattr("tradingcodex_service.application.brokers._binance_http_request", fake_binance_request)
    call_mcp_tool(
        workspace,
        "register_broker_connector",
        {
            "principal_id": "head-manager",
            "template": "binance_spot",
            "broker_id": "binance-sync-failure",
            "credential_ref": "env:BINANCE_TESTNET",
            "environment": "testnet",
        },
    )
    try:
        call_mcp_tool(workspace, "sync_broker_account", {"principal_id": "portfolio-manager", "broker_id": "binance-sync-failure"})
    except ValueError as exc:
        assert "Invalid API-key" in str(exc)
    else:
        raise AssertionError("sync must fail when signed account credentials are rejected")

    from apps.integrations.models import BrokerConnection

    connection = BrokerConnection.objects.get(broker_id="binance-sync-failure")
    assert connection.status == "read_only"
    assert connection.enabled_trade_scopes == []
    assert connection.metadata["validation_execution_enabled"] is False
    assert connection.metadata["credential_validation_details"]["code"] == "binance_auth_rejected"
    assert "unit-api-key" not in str(connection.metadata)
    assert "unit-secret-key" not in str(connection.metadata)


def test_binance_signed_health_failure_does_not_consume_execution_idempotency(tmp_path: Path, monkeypatch) -> None:
    workspace = make_workspace(tmp_path)
    ticket_id = f"binance-health-retry-{uuid.uuid4().hex[:12]}"
    monkeypatch.setenv("BINANCE_TESTNET_API_KEY", "unit-api-key")
    monkeypatch.setenv("BINANCE_TESTNET_SECRET_KEY", "unit-secret-key")
    monkeypatch.setenv("TRADINGCODEX_BINANCE_TESTNET_SUBMIT_MODE", "order_test")
    fail_signed_health = False

    def fake_binance_request(method: str, base_url: str, path: str, query: str, headers: dict[str, str]) -> dict:
        assert base_url == "https://testnet.binance.vision"
        if path == "/api/v3/time":
            return {"serverTime": 1780000000000}
        if path == "/api/v3/exchangeInfo":
            return {
                "symbols": [
                    {
                        "symbol": "BTCUSDT",
                        "quoteAsset": "USDT",
                        "orderTypes": ["LIMIT", "MARKET"],
                        "timeInForce": ["GTC", "IOC", "FOK"],
                        "filters": [
                            {"filterType": "PRICE_FILTER", "tickSize": "0.01000000"},
                            {"filterType": "LOT_SIZE", "minQty": "0.00001000", "stepSize": "0.00001000"},
                            {"filterType": "MIN_NOTIONAL", "minNotional": "5.00000000"},
                        ],
                    }
                ]
            }
        assert headers.get("X-MBX-APIKEY") == "unit-api-key"
        assert "signature=" in query
        if path == "/api/v3/account":
            if fail_signed_health:
                raise ValueError("Binance error -2015: Invalid API-key, IP, or permissions for action.")
            return {
                "accountType": "SPOT",
                "canTrade": True,
                "permissions": ["SPOT"],
                "balances": [{"asset": "USDT", "free": "1000.00000000", "locked": "0.00000000"}],
            }
        if path == "/api/v3/order/test":
            assert method == "POST"
            return {}
        raise AssertionError(f"unexpected Binance call: {method} {path} {query}")

    monkeypatch.setattr("tradingcodex_service.application.brokers._binance_http_request", fake_binance_request)

    registered = call_mcp_tool(
        workspace,
        "register_broker_connector",
        {
            "principal_id": "head-manager",
            "template": "binance_spot",
            "broker_id": "binance-spot-testnet",
            "credential_ref": "env:BINANCE_TESTNET",
            "environment": "testnet",
        },
    )
    assert registered["connection"]["status"] == "read_only"
    assert registered["connection"]["enabled_trade_scopes"] == []

    call_mcp_tool(workspace, "sync_broker_account", {"principal_id": "portfolio-manager", "broker_id": "binance-spot-testnet"})
    synced_status = call_mcp_tool(workspace, "get_broker_connection_status", {"principal_id": "head-manager", "broker_id": "binance-spot-testnet"})
    assert synced_status["connection"]["status"] == "trading_enabled"
    assert synced_status["connection"]["enabled_trade_scopes"] == ["order.submit.validation"]
    call_mcp_tool(
        workspace,
        "create_order_ticket",
        {
            "principal_id": "portfolio-manager",
            "ticket_id": ticket_id,
            "broker_id": "binance-spot-testnet",
            "broker_account_id": "spot-testnet",
            "symbol": "BTCUSDT",
            "side": "buy",
            "quantity": 0.0001,
            "order_type": "limit",
            "limit_price": 50000,
            "time_in_force": "GTC",
            "currency": "USDT",
        },
    )
    checks = call_mcp_tool(workspace, "run_order_checks", {"principal_id": "portfolio-manager", "ticket_id": ticket_id})
    assert checks["approval_ready"] is True
    approval = call_mcp_tool(workspace, "request_order_approval", {"principal_id": "risk-manager", "ticket_id": ticket_id})
    assert approval["status"] == "approved"

    fail_signed_health = True
    rejected = call_mcp_tool(workspace, "submit_approved_order", {"principal_id": "execution-operator", "ticket_id": ticket_id})
    assert rejected["status"] == "rejected"
    assert "broker health is error" in "\n".join(rejected["reasons"])
    locked_status = call_mcp_tool(workspace, "get_broker_connection_status", {"principal_id": "head-manager", "broker_id": "binance-spot-testnet"})
    assert locked_status["connection"]["status"] == "read_only"
    assert locked_status["connection"]["enabled_trade_scopes"] == []
    assert locked_status["health"]["details"]["code"] == "binance_auth_rejected"
    assert locked_status["connection"]["metadata"]["credential_validation_details"]["code"] == "binance_auth_rejected"
    assert "Spot Testnet API key" in "\n".join(locked_status["health"]["details"]["remediation"])
    assert "unit-api-key" not in str(locked_status)
    assert "unit-secret-key" not in str(locked_status)

    from apps.orders.models import ExecutionResult

    assert ExecutionResult.objects.filter(order_ticket_id=ticket_id).count() == 0

    fail_signed_health = False
    ok_status = call_mcp_tool(workspace, "get_broker_connection_status", {"principal_id": "head-manager", "broker_id": "binance-spot-testnet"})
    assert ok_status["connection"]["status"] == "trading_enabled"
    retried = call_mcp_tool(workspace, "submit_approved_order", {"principal_id": "execution-operator", "ticket_id": ticket_id})
    assert retried["status"] == "accepted", retried
    assert retried["result"]["mode"] == "order_test"
    assert ExecutionResult.objects.filter(order_ticket_id=ticket_id, status="accepted").count() == 1


def test_broker_center_and_order_ticket_web_surfaces_render() -> None:
    ensure_runtime_database(ROOT)
    client = Client(REMOTE_ADDR="127.0.0.1")

    brokers = client.get("/brokers/")
    assert brokers.status_code == 200
    broker_body = brokers.content.decode()
    assert "Broker Center" in broker_body
    assert "Add paper broker" in broker_body
    assert "Import External MCP discovery" in broker_body
    assert "Live execution" in broker_body

    orders = client.get("/orders/")
    assert orders.status_code == 200
    order_body = orders.content.decode()
    assert "Create draft" in order_body
    assert "Run checks" in order_body or "No order tickets" in order_body
    assert "Submit approved order" in order_body
    assert "disabled" in order_body

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from tradingcodex_service.application import order_lineage
from tradingcodex_service.application.brokers import FillDTO, sync_broker_account
from tradingcodex_service.application.runtime import ensure_runtime_database
from tradingcodex_service.mcp_runtime import call_mcp_tool

from test_broker_center_prd import (  # noqa: F401  (autouse fixtures registered via import)
    FakeLiveBrokerAdapter,
    _cleanup_order_tables,
    create_pending_fake_live_ticket,
    enable_live_policy,
    make_workspace,
    register_fake_live_connection,
    restore_broker_provider_registry,
)


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True, scope="module")
def _ensure_runtime_tables():
    ensure_runtime_database(ROOT)


@pytest.fixture(autouse=True)
def _reset_builtin_capabilities():
    from apps.policy.models import Capability, Principal
    from apps.policy.services import BUILTIN_ROLE_IDS, sync_builtin_principals_and_capabilities

    sync_builtin_principals_and_capabilities()
    Principal.objects.filter(principal_id__in=BUILTIN_ROLE_IDS).update(active=True)
    Capability.objects.filter(principal__principal_id__in=BUILTIN_ROLE_IDS, effect="deny").update(effect="allow")


@pytest.fixture(autouse=True)
def _cleanup_ledger_events():
    yield
    from apps.portfolio.models import PortfolioLedgerEvent

    PortfolioLedgerEvent.objects.filter(event_type="unattributed_fill").delete()


def _voided_ticket(workspace, broker_id, symbol="DOT", side="sell"):
    ticket_id = f"void-{symbol.lower()}-{uuid.uuid4().hex[:12]}"
    create_pending_fake_live_ticket(workspace, ticket_id, symbol=symbol, side=side, broker_id=broker_id)
    voided = call_mcp_tool(workspace, "cancel_approved_order", {"principal_id": "execution-operator", "ticket_id": ticket_id})
    assert voided["status"] == "voided", voided
    return ticket_id


def _install_fills(monkeypatch, fills):
    monkeypatch.setattr(FakeLiveBrokerAdapter, "get_fills", lambda self, account_id: list(fills), raising=False)


def _manual_fill(fill_id="upbit-fill-1", broker_order_id="upbit-manual-1", symbol="DOT", side="sell"):
    return FillDTO(
        fill_id=fill_id,
        broker_order_id=broker_order_id,
        quantity=10,
        price=5000,
        currency="USD",
        fee=25,
        filled_at="2026-07-10T01:00:00+00:00",
        symbol=symbol,
        side=side,
    )


# --- ROB-802 regression case 1: broker-order lookup failure must fail closed ---

def test_crosswalk_fails_closed_when_broker_order_lookup_fails(tmp_path, monkeypatch):
    workspace = make_workspace(tmp_path)

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated broker-order lookup outage")

    monkeypatch.setattr(order_lineage, "_matched_tickets", _boom)
    result = call_mcp_tool(
        workspace,
        "validate_order_approval_crosswalk",
        {"principal_id": "risk-manager", "broker_order_id": "any-broker-order"},
    )
    assert result["status"] == "error"
    assert result["success"] is False
    assert result["error"] == "broker_order_lookup_failed"
    assert "simulated broker-order lookup outage" in result["failure_reason"]
    assert result["rows"] == []
    assert result["terminal_inference_allowed"] is False
    assert "broker_order_lookup_failed" in result["terminal_inference_blockers"]


# --- ROB-802 regression case 2: no-match must not return unrelated approvals ---

def test_crosswalk_no_match_returns_empty_rows_not_unrelated_approvals(tmp_path):
    workspace = make_workspace(tmp_path)
    broker_id = "fake-live-nomatch"
    FakeLiveBrokerAdapter.reset()
    register_fake_live_connection(workspace, broker_id)
    enable_live_policy(workspace, broker_id)
    for _ in range(3):
        create_pending_fake_live_ticket(workspace, f"unrelated-{uuid.uuid4().hex[:12]}", broker_id=broker_id)

    result = call_mcp_tool(
        workspace,
        "validate_order_approval_crosswalk",
        {"principal_id": "risk-manager", "broker_order_id": "does-not-exist"},
    )
    assert result["status"] == "no_match"
    assert result["no_match"] is True
    assert result["rows"] == []
    assert any("broker_order_id" in reason for reason in result["no_match_reasons"])
    assert result["terminal_inference_allowed"] is False
    assert "no_match" in result["terminal_inference_blockers"]

    by_ticket = call_mcp_tool(
        workspace,
        "validate_order_approval_crosswalk",
        {"principal_id": "risk-manager", "ticket_id": "missing-ticket-id"},
    )
    assert by_ticket["status"] == "no_match"
    assert by_ticket["rows"] == []


# --- ROB-802 regression case 3: VOID without successor must surface an anomaly ---

def test_crosswalk_flags_voided_ticket_without_successor(tmp_path):
    workspace = make_workspace(tmp_path)
    broker_id = "fake-live-voided"
    FakeLiveBrokerAdapter.reset()
    register_fake_live_connection(workspace, broker_id)
    enable_live_policy(workspace, broker_id)
    ticket_id = _voided_ticket(workspace, broker_id)

    crosswalk = call_mcp_tool(
        workspace,
        "validate_order_approval_crosswalk",
        {"principal_id": "risk-manager", "ticket_id": ticket_id},
    )
    assert crosswalk["status"] == "anomaly"
    assert "voided_without_successor" in crosswalk["terminal_inference_blockers"]
    row = crosswalk["rows"][0]
    assert row["canonical_ticket_id"] == ticket_id
    assert "voided_without_successor" in row["anomalies"]
    assert row["replacement_lineage"]["manual_executions"] == []
    assert row["terminal_inference_allowed"] is False


# --- ROB-801: broker sync exposes unattributed fills explicitly ---

def test_broker_sync_exposes_unattributed_fills_explicitly(tmp_path, monkeypatch):
    workspace = make_workspace(tmp_path)
    broker_id = "fake-live-manualfill"
    FakeLiveBrokerAdapter.reset()
    register_fake_live_connection(workspace, broker_id)
    enable_live_policy(workspace, broker_id)
    attributed_ticket = f"attributed-{uuid.uuid4().hex[:12]}"
    create_pending_fake_live_ticket(workspace, attributed_ticket, broker_id=broker_id)
    from apps.orders.models import BrokerOrder, OrderTicket

    BrokerOrder.objects.create(
        ticket=OrderTicket.objects.get(ticket_id=attributed_ticket),
        broker_order_id="known-broker-order",
        broker_status="filled",
    )
    _install_fills(
        monkeypatch,
        [
            _manual_fill(),
            FillDTO(
                fill_id="known-fill",
                broker_order_id="known-broker-order",
                quantity=1,
                price=100,
                currency="USD",
                filled_at="2026-07-10T01:00:00+00:00",
                symbol="AAPL",
                side="buy",
            ),
        ],
    )

    result = sync_broker_account(workspace, {"broker_id": broker_id, "principal_id": "portfolio-manager"})
    assert "unattributed_fills" in result
    assert [fill["fill_id"] for fill in result["unattributed_fills"]] == ["upbit-fill-1"]
    fill = result["unattributed_fills"][0]
    assert fill["attribution_status"] == "unattributed"
    assert fill["symbol"] == "DOT"
    assert fill["broker_order_id"] == "upbit-manual-1"

    from apps.portfolio.models import PortfolioLedgerEvent

    assert PortfolioLedgerEvent.objects.filter(event_type="unattributed_fill").count() == 1

    # re-running sync must not duplicate the ledger record but must keep it visible
    result2 = sync_broker_account(workspace, {"broker_id": broker_id, "principal_id": "portfolio-manager"})
    assert PortfolioLedgerEvent.objects.filter(event_type="unattributed_fill").count() == 1
    assert [fill["fill_id"] for fill in result2["unattributed_fills"]] == ["upbit-fill-1"]

    listed = call_mcp_tool(workspace, "list_unattributed_fills", {"principal_id": "execution-operator", "broker_id": broker_id})
    assert [fill["fill_id"] for fill in listed["unattributed_fills"]] == ["upbit-fill-1"]
    assert listed["unattributed_fills"][0]["attribution_status"] == "unattributed"


def test_broker_sync_reports_fill_lookup_failure_as_warning(tmp_path, monkeypatch):
    workspace = make_workspace(tmp_path)
    broker_id = "fake-live-fillfail"
    FakeLiveBrokerAdapter.reset()
    register_fake_live_connection(workspace, broker_id)

    def _boom(self, account_id):
        raise RuntimeError("fill endpoint outage")

    monkeypatch.setattr(FakeLiveBrokerAdapter, "get_fills", _boom, raising=False)
    result = sync_broker_account(workspace, {"broker_id": broker_id, "principal_id": "portfolio-manager"})
    assert any("fill" in warning and "fill endpoint outage" in warning for warning in result["warnings"])
    assert result["unattributed_fills"] == []


# --- ROB-801: evidence-based annotation links a VOID ticket and resolves the anomaly ---

def test_annotate_manual_execution_links_void_ticket_and_resolves_anomaly(tmp_path, monkeypatch):
    workspace = make_workspace(tmp_path)
    broker_id = "fake-live-annotate"
    FakeLiveBrokerAdapter.reset()
    register_fake_live_connection(workspace, broker_id)
    enable_live_policy(workspace, broker_id)
    ticket_id = _voided_ticket(workspace, broker_id, symbol="DOT", side="sell")
    _install_fills(monkeypatch, [_manual_fill()])
    sync_broker_account(workspace, {"broker_id": broker_id, "principal_id": "portfolio-manager"})

    annotated = call_mcp_tool(
        workspace,
        "annotate_manual_execution",
        {
            "principal_id": "execution-operator",
            "fill_id": "upbit-fill-1",
            "ticket_id": ticket_id,
            "evidence": "2026-07-10 crypto retro: DOT manual stop executed in the Upbit app",
            "cost_basis_price": 5200,
        },
    )
    assert annotated["status"] == "linked", annotated
    assert annotated["provenance"] == "manual"
    assert annotated["ticket_id"] == ticket_id
    realized = annotated["realized"]
    assert realized["source"] == "broker_fill"
    assert realized["gross_notional"] == 50000.0
    assert realized["fee"] == 25.0
    assert realized["net_amount"] == 49975.0
    assert realized["realized_pnl"] == -2025.0

    # the manual execution must never be disguised as a canonical fill/broker order
    from apps.orders.models import OrderTicket

    ticket = OrderTicket.objects.get(ticket_id=ticket_id)
    assert not ticket.fills.exists()
    assert not ticket.broker_orders.exists()
    event = ticket.events.filter(event_type="manual_execution_linked").get()
    assert event.payload["provenance"] == "manual"
    assert event.payload["fill_id"] == "upbit-fill-1"

    crosswalk = call_mcp_tool(
        workspace,
        "validate_order_approval_crosswalk",
        {"principal_id": "risk-manager", "ticket_id": ticket_id},
    )
    row = crosswalk["rows"][0]
    assert "voided_without_successor" not in row["anomalies"]
    manual_nodes = row["replacement_lineage"]["manual_executions"]
    assert manual_nodes and manual_nodes[0]["node_type"] == "manual_execution"
    assert manual_nodes[0]["provenance"] == "manual"
    assert manual_nodes[0]["fill_id"] == "upbit-fill-1"
    assert "manual_execution:upbit-fill-1" in row["replacement_lineage"]["path"]
    assert crosswalk["status"] == "ok"

    listed = call_mcp_tool(workspace, "list_unattributed_fills", {"principal_id": "execution-operator", "broker_id": broker_id})
    linked = [fill for fill in listed["unattributed_fills"] if fill["fill_id"] == "upbit-fill-1"]
    assert linked and linked[0]["attribution_status"] == "linked"
    assert linked[0]["linked_ticket_id"] == ticket_id


def test_annotate_manual_execution_rejects_without_evidence(tmp_path, monkeypatch):
    workspace = make_workspace(tmp_path)
    broker_id = "fake-live-reject"
    FakeLiveBrokerAdapter.reset()
    register_fake_live_connection(workspace, broker_id)
    enable_live_policy(workspace, broker_id)
    void_ticket = _voided_ticket(workspace, broker_id, symbol="DOT", side="sell")
    live_ticket = f"live-{uuid.uuid4().hex[:12]}"
    create_pending_fake_live_ticket(workspace, live_ticket, symbol="AAPL", side="buy", broker_id=broker_id)
    _install_fills(monkeypatch, [_manual_fill()])
    sync_broker_account(workspace, {"broker_id": broker_id, "principal_id": "portfolio-manager"})

    unknown = call_mcp_tool(
        workspace,
        "annotate_manual_execution",
        {"principal_id": "execution-operator", "fill_id": "never-seen", "ticket_id": void_ticket, "evidence": "retro"},
    )
    assert unknown["status"] == "rejected"
    assert any("not found" in reason for reason in unknown["reasons"])

    no_evidence = call_mcp_tool(
        workspace,
        "annotate_manual_execution",
        {"principal_id": "execution-operator", "fill_id": "upbit-fill-1", "ticket_id": void_ticket, "evidence": ""},
    )
    assert no_evidence["status"] == "rejected"
    assert any("evidence" in reason for reason in no_evidence["reasons"])

    not_voided = call_mcp_tool(
        workspace,
        "annotate_manual_execution",
        {"principal_id": "execution-operator", "fill_id": "upbit-fill-1", "ticket_id": live_ticket, "evidence": "retro"},
    )
    assert not_voided["status"] == "rejected"
    assert any("VOIDED" in reason for reason in not_voided["reasons"])

    mismatched_ticket = _voided_ticket(workspace, broker_id, symbol="ETH", side="sell")
    mismatch = call_mcp_tool(
        workspace,
        "annotate_manual_execution",
        {"principal_id": "execution-operator", "fill_id": "upbit-fill-1", "ticket_id": mismatched_ticket, "evidence": "retro"},
    )
    assert mismatch["status"] == "rejected"
    assert any("symbol" in reason for reason in mismatch["reasons"])

    linked = call_mcp_tool(
        workspace,
        "annotate_manual_execution",
        {"principal_id": "execution-operator", "fill_id": "upbit-fill-1", "ticket_id": void_ticket, "evidence": "retro evidence"},
    )
    assert linked["status"] == "linked", linked
    duplicate = call_mcp_tool(
        workspace,
        "annotate_manual_execution",
        {"principal_id": "execution-operator", "fill_id": "upbit-fill-1", "ticket_id": void_ticket, "evidence": "retro evidence"},
    )
    assert duplicate["status"] == "rejected"
    assert any("already linked" in reason for reason in duplicate["reasons"])


# --- r6 consistency: resting lifecycle panel stays coherent with VOID + manual nodes ---

def test_resting_lifecycle_panel_stays_consistent_with_manual_annotation(tmp_path, monkeypatch):
    workspace = make_workspace(tmp_path)
    broker_id = "fake-live-panel"
    FakeLiveBrokerAdapter.reset()
    register_fake_live_connection(workspace, broker_id)
    enable_live_policy(workspace, broker_id)
    void_ticket = _voided_ticket(workspace, broker_id, symbol="DOT", side="sell")
    open_ticket = f"panel-open-{uuid.uuid4().hex[:12]}"
    create_pending_fake_live_ticket(workspace, open_ticket, symbol="AAPL", side="buy", broker_id=broker_id)
    _install_fills(monkeypatch, [_manual_fill()])
    sync_broker_account(workspace, {"broker_id": broker_id, "principal_id": "portfolio-manager"})
    annotated = call_mcp_tool(
        workspace,
        "annotate_manual_execution",
        {
            "principal_id": "execution-operator",
            "fill_id": "upbit-fill-1",
            "ticket_id": void_ticket,
            "evidence": "2026-07-10 crypto retro",
        },
    )
    assert annotated["status"] == "linked", annotated

    panel = call_mcp_tool(workspace, "get_resting_lifecycle_panel", {"principal_id": "risk-manager"})
    panel_ticket_ids = {row["ticket_id"] for row in panel["rows"]}
    assert void_ticket not in panel_ticket_ids
    assert open_ticket in panel_ticket_ids
    open_row = next(row for row in panel["rows"] if row["ticket_id"] == open_ticket)
    assert open_row["replacement_lineage"]["manual_executions"] == []
    assert "voided_without_successor" not in panel["terminal_inference_blockers"]

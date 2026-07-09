from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tradingcodex_service.application.resting_lifecycle import _planned_checkpoints
from tradingcodex_service.application.runtime import ensure_runtime_database
from tradingcodex_service.mcp_runtime import call_mcp_tool, handle_mcp_rpc

from test_broker_center_prd import (  # noqa: F401  (autouse fixtures registered via import)
    FakeLiveBrokerAdapter,
    _cleanup_order_tables,
    create_approved_fake_live_ticket,
    create_pending_fake_live_ticket,
    enable_live_policy,
    make_workspace,
    register_fake_live_connection,
    restore_broker_provider_registry,
)


ROOT = Path(__file__).resolve().parents[1]

# 2026-07-06 is a Monday; 10:00 KST == 01:00 UTC.
SUBMIT_AT = datetime(2026, 7, 6, 1, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True, scope="module")
def _ensure_runtime_tables():
    ensure_runtime_database(ROOT)


@pytest.fixture(autouse=True)
def _reset_builtin_capabilities():
    # Earlier test modules may leave deny/inactive rows on builtin principals.
    from apps.policy.models import Capability, Principal
    from apps.policy.services import BUILTIN_ROLE_IDS, sync_builtin_principals_and_capabilities

    sync_builtin_principals_and_capabilities()
    Principal.objects.filter(principal_id__in=BUILTIN_ROLE_IDS).update(active=True)
    Capability.objects.filter(principal__principal_id__in=BUILTIN_ROLE_IDS, effect="deny").update(effect="allow")


def _kst(hour, minute):
    return datetime(2026, 7, 6, hour - 9, minute, tzinfo=timezone.utc)


def _submit_live_ticket(workspace, ticket_id, broker_id, monkeypatch, submit_mode="submitted"):
    FakeLiveBrokerAdapter.reset()
    FakeLiveBrokerAdapter.submit_mode = submit_mode
    register_fake_live_connection(workspace, broker_id)
    enable_live_policy(workspace, broker_id)
    confirmation = create_approved_fake_live_ticket(workspace, ticket_id, broker_id)
    monkeypatch.setenv("TRADINGCODEX_ENABLE_LIVE_EXECUTION", "1")
    return call_mcp_tool(
        workspace,
        "submit_approved_order",
        {"principal_id": "execution-operator", "ticket_id": ticket_id, "live_confirmation": confirmation},
    )


def _pin_submission(ticket_id, submitted_at, broker_status="submitted"):
    from apps.orders.models import BrokerOrder, OrderTicket

    ticket = OrderTicket.objects.get(ticket_id=ticket_id)
    BrokerOrder.objects.filter(ticket=ticket).update(submitted_at=submitted_at, last_seen_at=submitted_at, broker_status=broker_status)
    return ticket


def _record_refresh(ticket, broker_status, at):
    from apps.orders.models import BrokerOrder, OrderEvent

    broker_order = ticket.broker_orders.first()
    event = OrderEvent.objects.create(
        ticket=ticket,
        event_type="status_refreshed",
        actor="execution-operator",
        payload={"broker_order_id": broker_order.broker_order_id if broker_order else "", "broker_status": broker_status},
    )
    OrderEvent.objects.filter(pk=event.pk).update(created_at=at)
    if broker_order:
        BrokerOrder.objects.filter(pk=broker_order.pk).update(broker_status=broker_status, last_seen_at=at)


def _panel(workspace, **args):
    return call_mcp_tool(workspace, "get_resting_lifecycle_panel", dict({"principal_id": "execution-operator"}, **args))


def test_planned_checkpoints_tick_counts_and_boundaries():
    plan = _planned_checkpoints(SUBMIT_AT, _kst(21, 0))
    assert plan["trading_day"] == "2026-07-06"
    assert plan["is_weekend"] is False
    assert len(plan["regular_ticks"]) == 66
    assert plan["regular_ticks"][0].isoformat() == "2026-07-06T10:05:00+09:00"
    assert plan["regular_ticks"][-1].isoformat() == "2026-07-06T15:30:00+09:00"
    assert len(plan["nxt_ticks"]) == 27
    assert plan["nxt_ticks"][0].isoformat() == "2026-07-06T15:40:00+09:00"
    assert plan["nxt_ticks"][-1].isoformat() == "2026-07-06T20:00:00+09:00"
    assert plan["terminal_due"].isoformat() == "2026-07-06T20:00:00+09:00"


def test_planned_checkpoints_weekend_anchor_has_no_cadence():
    saturday = datetime(2026, 7, 4, 1, 0, tzinfo=timezone.utc)
    plan = _planned_checkpoints(saturday, saturday)
    assert plan["is_weekend"] is True
    assert plan["regular_ticks"] == []
    assert plan["nxt_ticks"] == []


def test_approved_ticket_shows_approved_state_without_checkpoints(tmp_path):
    workspace = make_workspace(tmp_path)
    ticket_id = f"panel-approved-{uuid.uuid4().hex[:12]}"
    broker_id = f"fake-live-{uuid.uuid4().hex[:8]}"
    FakeLiveBrokerAdapter.reset()
    register_fake_live_connection(workspace, broker_id)
    enable_live_policy(workspace, broker_id)
    create_pending_fake_live_ticket(workspace, ticket_id, broker_id=broker_id)

    panel = _panel(workspace, ticket_id=ticket_id)
    row = panel["rows"][0]
    assert row["lifecycle_state"] == "approved", row
    assert row["ticket_state"] == "APPROVED"
    assert row["owner"] == "portfolio-manager"
    assert row["approval"]["approved_by"] == "risk-manager"
    assert row["checkpoints"] == {}
    assert row["flags"]["no_auto_reprice"] is True
    assert row["flags"]["no_overnight_carry"] is True
    assert row["next_allowed_action"]["action"] == "submit_approved_order"
    assert row["next_allowed_action"]["role"] == "execution-operator"
    assert "auto_reprice" in row["blocked_actions"]
    assert "overnight_carry" in row["blocked_actions"]


def test_acked_ticket_without_refresh_evidence_stays_acked(tmp_path, monkeypatch):
    workspace = make_workspace(tmp_path)
    ticket_id = f"panel-acked-{uuid.uuid4().hex[:12]}"
    broker_id = f"fake-live-{uuid.uuid4().hex[:8]}"
    submitted = _submit_live_ticket(workspace, ticket_id, broker_id, monkeypatch)
    assert submitted["status"] == "accepted", submitted
    _pin_submission(ticket_id, SUBMIT_AT)

    panel = _panel(workspace, ticket_id=ticket_id, as_of=_kst(10, 1).isoformat())
    row = panel["rows"][0]
    assert row["ticket_state"] == "ACKED"
    assert row["lifecycle_state"] == "acked", row
    assert row["checkpoints"]["post_submit"]["result"] == "pending"
    assert row["next_allowed_action"]["action"] == "refresh_broker_order_status"


def test_submitted_state_before_acknowledgement(tmp_path, monkeypatch):
    workspace = make_workspace(tmp_path)
    ticket_id = f"panel-submitted-{uuid.uuid4().hex[:12]}"
    broker_id = f"fake-live-{uuid.uuid4().hex[:8]}"
    _submit_live_ticket(workspace, ticket_id, broker_id, monkeypatch)
    ticket = _pin_submission(ticket_id, SUBMIT_AT)
    from apps.orders.models import OrderTicket

    OrderTicket.objects.filter(pk=ticket.pk).update(current_state="SUBMITTED", status="SUBMITTED")

    panel = _panel(workspace, ticket_id=ticket_id, as_of=_kst(10, 1).isoformat())
    row = panel["rows"][0]
    assert row["lifecycle_state"] == "submitted", row


def test_resting_requires_observed_refresh_evidence(tmp_path, monkeypatch):
    workspace = make_workspace(tmp_path)
    ticket_id = f"panel-resting-{uuid.uuid4().hex[:12]}"
    broker_id = f"fake-live-{uuid.uuid4().hex[:8]}"
    _submit_live_ticket(workspace, ticket_id, broker_id, monkeypatch)
    ticket = _pin_submission(ticket_id, SUBMIT_AT)
    _record_refresh(ticket, "open", _kst(10, 1))

    panel = _panel(workspace, ticket_id=ticket_id, as_of=_kst(10, 3).isoformat())
    row = panel["rows"][0]
    assert row["lifecycle_state"] == "resting", row
    assert row["checkpoints"]["post_submit"]["result"] == "observed"
    assert row["checkpoints"]["regular_5min"]["next_due_at"] == "2026-07-06T10:05:00+09:00"
    assert row["session"]["phase"] == "regular"
    assert panel["session_phase"] == "regular"
    # A resting ACKED row keeps the crosswalk's unresolved anomaly and blocks terminal inference.
    assert "unresolved_acked_or_unknown" in row["anomalies"]
    assert row["terminal_inference_allowed"] is False


def test_nxt_active_in_after_market_window(tmp_path, monkeypatch):
    workspace = make_workspace(tmp_path)
    ticket_id = f"panel-nxt-{uuid.uuid4().hex[:12]}"
    broker_id = f"fake-live-{uuid.uuid4().hex[:8]}"
    _submit_live_ticket(workspace, ticket_id, broker_id, monkeypatch)
    ticket = _pin_submission(ticket_id, SUBMIT_AT)
    _record_refresh(ticket, "open", _kst(10, 1))
    _record_refresh(ticket, "open", _kst(16, 0))

    panel = _panel(workspace, ticket_id=ticket_id, as_of=_kst(16, 5).isoformat())
    row = panel["rows"][0]
    assert row["lifecycle_state"] == "nxt_active", row
    assert row["checkpoints"]["nxt_10min"]["interval_minutes"] == 10
    assert row["checkpoints"]["nxt_10min"]["result"] == "observed"
    assert row["session"]["phase"] == "nxt"


def test_ttl_stale_after_two_missed_regular_checkpoints(tmp_path, monkeypatch):
    workspace = make_workspace(tmp_path)
    ticket_id = f"panel-stale-{uuid.uuid4().hex[:12]}"
    broker_id = f"fake-live-{uuid.uuid4().hex[:8]}"
    _submit_live_ticket(workspace, ticket_id, broker_id, monkeypatch)
    ticket = _pin_submission(ticket_id, SUBMIT_AT)
    _record_refresh(ticket, "open", _kst(10, 0))

    panel = _panel(workspace, ticket_id=ticket_id, as_of=_kst(10, 12).isoformat())
    row = panel["rows"][0]
    assert row["lifecycle_state"] == "ttl_stale", row
    assert row["checkpoints"]["regular_5min"]["missed_count"] == 2
    assert len(row["checkpoints"]["regular_5min"]["last_results"]) <= 5
    gaps = {gap["gap"] for gap in row["evidence_gaps"]}
    assert "missed_checkpoints" in gaps
    assert row["terminal_inference_allowed"] is False
    assert "terminal_inference" in row["blocked_actions"]
    assert panel["status"] == "attention"


def test_post_2000_without_terminal_evidence_is_terminal_blocked_not_expired(tmp_path, monkeypatch):
    workspace = make_workspace(tmp_path)
    ticket_id = f"panel-terminal-{uuid.uuid4().hex[:12]}"
    broker_id = f"fake-live-{uuid.uuid4().hex[:8]}"
    _submit_live_ticket(workspace, ticket_id, broker_id, monkeypatch)
    ticket = _pin_submission(ticket_id, SUBMIT_AT)
    _record_refresh(ticket, "open", _kst(15, 0))

    from apps.orders.models import OrderEvent, OrderTicket

    events_before = OrderEvent.objects.filter(ticket=ticket).count()
    panel = _panel(workspace, ticket_id=ticket_id, as_of=_kst(20, 30).isoformat())
    row = panel["rows"][0]
    assert row["lifecycle_state"] == "terminal_blocked", row
    assert row["lifecycle_state"] != "expired"
    assert row["checkpoints"]["terminal_refresh_post_2000"]["result"] == "skipped"
    gap_names = {gap["gap"] for gap in row["evidence_gaps"]}
    assert "no_terminal_evidence" in gap_names
    assert row["terminal_inference_allowed"] is False
    assert "no_terminal_evidence" in panel["terminal_inference_blockers"]
    assert "expire_without_evidence" in row["blocked_actions"]
    # Read-only proof: no state transition and no new events were recorded.
    refreshed = OrderTicket.objects.get(ticket_id=ticket_id)
    assert refreshed.current_state == "ACKED"
    assert OrderEvent.objects.filter(ticket=ticket).count() == events_before


def test_terminal_states_are_evidence_backed(tmp_path, monkeypatch):
    workspace = make_workspace(tmp_path)
    ticket_id = f"panel-filled-{uuid.uuid4().hex[:12]}"
    broker_id = f"fake-live-{uuid.uuid4().hex[:8]}"
    submitted = _submit_live_ticket(workspace, ticket_id, broker_id, monkeypatch, submit_mode="filled")
    assert submitted["status"] == "accepted", submitted

    panel = _panel(workspace, ticket_id=ticket_id)
    row = panel["rows"][0]
    assert row["ticket_state"] == "FILLED"
    assert row["lifecycle_state"] == "filled", row
    assert row["next_allowed_action"]["action"] == ""

    from apps.orders.models import BrokerOrder, OrderTicket

    ticket = OrderTicket.objects.get(ticket_id=ticket_id)
    OrderTicket.objects.filter(pk=ticket.pk).update(current_state="REJECTED", status="REJECTED")
    BrokerOrder.objects.filter(ticket=ticket).update(broker_status="rejected")
    row = _panel(workspace, ticket_id=ticket_id)["rows"][0]
    assert row["lifecycle_state"] == "cancelled", row
    assert row["ticket_state"] == "REJECTED"

    OrderTicket.objects.filter(pk=ticket.pk).update(current_state="EXPIRED", status="EXPIRED")
    BrokerOrder.objects.filter(ticket=ticket).update(broker_status="expired")
    row = _panel(workspace, ticket_id=ticket_id)["rows"][0]
    assert row["lifecycle_state"] == "expired", row


def test_unknown_broker_status_is_anomaly_unverified(tmp_path, monkeypatch):
    workspace = make_workspace(tmp_path)
    ticket_id = f"panel-unknown-{uuid.uuid4().hex[:12]}"
    broker_id = f"fake-live-{uuid.uuid4().hex[:8]}"
    submitted = _submit_live_ticket(workspace, ticket_id, broker_id, monkeypatch, submit_mode="uncertain")
    assert submitted["status"] == "needs_review", submitted

    panel = _panel(workspace, ticket_id=ticket_id)
    row = panel["rows"][0]
    assert row["lifecycle_state"] == "anomaly_unverified", row
    assert "unknown_broker_status" in row["anomalies"]
    assert row["terminal_inference_allowed"] is False
    assert row["next_allowed_action"]["action"] == "validate_order_approval_crosswalk"
    assert panel["status"] == "attention"


def test_terminal_ticket_with_unresolved_broker_evidence_is_anomaly(tmp_path, monkeypatch):
    workspace = make_workspace(tmp_path)
    ticket_id = f"panel-mismatch-{uuid.uuid4().hex[:12]}"
    broker_id = f"fake-live-{uuid.uuid4().hex[:8]}"
    _submit_live_ticket(workspace, ticket_id, broker_id, monkeypatch)
    ticket = _pin_submission(ticket_id, SUBMIT_AT, broker_status="open")
    from apps.orders.models import OrderTicket

    OrderTicket.objects.filter(pk=ticket.pk).update(current_state="FILLED", status="FILLED")

    row = _panel(workspace, ticket_id=ticket_id, as_of=_kst(11, 0).isoformat())["rows"][0]
    assert row["lifecycle_state"] == "anomaly_unverified", row
    assert "terminal_without_broker_evidence" in row["anomalies"]


def test_as_of_is_deterministic_and_changes_state(tmp_path, monkeypatch):
    workspace = make_workspace(tmp_path)
    ticket_id = f"panel-asof-{uuid.uuid4().hex[:12]}"
    broker_id = f"fake-live-{uuid.uuid4().hex[:8]}"
    _submit_live_ticket(workspace, ticket_id, broker_id, monkeypatch)
    ticket = _pin_submission(ticket_id, SUBMIT_AT)
    _record_refresh(ticket, "open", _kst(10, 1))

    first = _panel(workspace, ticket_id=ticket_id, as_of=_kst(10, 3).isoformat())
    second = _panel(workspace, ticket_id=ticket_id, as_of=_kst(10, 3).isoformat())
    assert first["rows"] == second["rows"]
    later = _panel(workspace, ticket_id=ticket_id, as_of=_kst(10, 16).isoformat())
    assert first["rows"][0]["lifecycle_state"] == "resting"
    assert later["rows"][0]["lifecycle_state"] == "ttl_stale"


def test_lifecycle_state_filter(tmp_path, monkeypatch):
    workspace = make_workspace(tmp_path)
    ticket_id = f"panel-filter-{uuid.uuid4().hex[:12]}"
    broker_id = f"fake-live-{uuid.uuid4().hex[:8]}"
    _submit_live_ticket(workspace, ticket_id, broker_id, monkeypatch)
    ticket = _pin_submission(ticket_id, SUBMIT_AT)
    _record_refresh(ticket, "open", _kst(10, 1))

    filtered = _panel(workspace, ticket_id=ticket_id, as_of=_kst(10, 3).isoformat(), lifecycle_state="ttl_stale")
    assert filtered["rows"] == []
    matched = _panel(workspace, ticket_id=ticket_id, as_of=_kst(10, 3).isoformat(), lifecycle_state="resting")
    assert [row["ticket_id"] for row in matched["rows"]] == [ticket_id]


def test_panel_tool_is_role_scoped_and_read_only(tmp_path):
    workspace = make_workspace(tmp_path)
    tools = handle_mcp_rpc(workspace, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    tool = next(item for item in tools["result"]["tools"] if item["name"] == "get_resting_lifecycle_panel")
    assert tool["annotations"]["readOnlyHint"] is True
    assert tool["annotations"]["destructiveHint"] is False
    assert tool["annotations"]["requires_approval"] is False
    for role in ("head-manager", "portfolio-manager", "risk-manager", "execution-operator"):
        assert role in tool["annotations"]["allowed_roles"]
    assert {"ticket_id", "lifecycle_state", "as_of", "limit"}.issubset(tool["inputSchema"]["properties"])

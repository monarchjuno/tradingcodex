from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tradingcodex_cli.generator import bootstrap_workspace
from tradingcodex_service.application import orders
from tradingcodex_service.application.portfolio import (
    load_paper_portfolio_state,
    persist_paper_portfolio_state,
)
from tradingcodex_service.mcp_runtime import call_mcp_tool


def make_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    bootstrap_workspace(workspace, force=True)
    return workspace


def _ensure_sufficient_cash(workspace: Path) -> None:
    state = load_paper_portfolio_state(workspace)
    if state.get("cash_krw", 0) < 1_000_000:
        state["cash_krw"] = 1_000_000
        persist_paper_portfolio_state(
            workspace, state, state["portfolio_id"], state["account_id"], state["strategy_id"], source="paper-trading"
        )


def freeze_deadline_clock(monkeypatch, moment: datetime) -> None:
    monkeypatch.setattr(orders, "_deadline_now", lambda: moment)


def create_approved_day_ticket(workspace: Path, ticket_id: str, session_close_at: str | None) -> None:
    _ensure_sufficient_cash(workspace)
    args = {
        "principal_id": "portfolio-manager",
        "ticket_id": ticket_id,
        "symbol": "MSFT",
        "side": "buy",
        "quantity": 1,
        "order_type": "limit",
        "limit_price": 1000,
        "time_in_force": "day",
        "currency": "KRW",
    }
    if session_close_at is not None:
        args["session_close_at"] = session_close_at
    created = call_mcp_tool(workspace, "create_order_ticket", args)
    assert created["ticket"]["current_state"] == "DRAFT"
    checks = call_mcp_tool(workspace, "run_order_checks", {"principal_id": "portfolio-manager", "ticket_id": ticket_id})
    assert checks["approval_ready"] is True, checks
    approval = call_mcp_tool(workspace, "request_order_approval", {"principal_id": "risk-manager", "ticket_id": ticket_id})
    assert approval["status"] == "approved", approval


def audit_text(workspace: Path) -> str:
    audit_path = workspace / "trading" / "audit" / "tradingcodex-mcp.jsonl"
    return audit_path.read_text() if audit_path.exists() else ""


@pytest.fixture(autouse=True)
def _cleanup_order_tables():
    from tradingcodex_service.application.runtime import ensure_runtime_database

    ensure_runtime_database(None)
    from apps.orders.models import (
        ApprovalReceipt,
        BrokerOrder,
        ExecutionResult,
        Fill,
        OrderCheckRun,
        OrderEvent,
        OrderTicket,
    )

    Fill.objects.all().delete()
    BrokerOrder.objects.all().delete()
    ExecutionResult.objects.all().delete()
    OrderEvent.objects.all().delete()
    OrderCheckRun.objects.all().delete()
    ApprovalReceipt.objects.all().delete()
    OrderTicket.objects.all().delete()
    yield


def test_expire_service_transitions_approved_day_ticket_after_close(tmp_path: Path, monkeypatch) -> None:
    workspace = make_workspace(tmp_path)
    close = datetime.now(timezone.utc) + timedelta(hours=3)
    create_approved_day_ticket(workspace, "expire-svc-ticket", session_close_at=close.isoformat())

    from apps.orders.models import ApprovalReceipt

    assert ApprovalReceipt.objects.filter(order_ticket_id="expire-svc-ticket", valid=True).count() == 1

    freeze_deadline_clock(monkeypatch, close + timedelta(minutes=1))
    ticket = orders.get_order_ticket_model(workspace, {"ticket_id": "expire-svc-ticket"})
    assert orders._approved_only_expiry_reasons(ticket) == []

    result = orders.expire_approved_only_ticket(workspace, ticket, "execution-operator", {"reason": "session closed"})

    assert result["status"] == "expired"
    assert result["expire_reason"] == "ticket_expired_no_resubmit"
    assert result["invalidated_approval_receipts"] == 1
    assert result["ticket"]["current_state"] == "EXPIRED"
    assert "successor" in result["successor_guidance"].lower()
    assert ApprovalReceipt.objects.filter(order_ticket_id="expire-svc-ticket", valid=True).count() == 0
    assert '"order_ticket.expire.accepted"' in audit_text(workspace)


def test_expire_service_rejects_before_close_and_without_session_meta(tmp_path: Path, monkeypatch) -> None:
    workspace = make_workspace(tmp_path)
    close = datetime.now(timezone.utc) + timedelta(hours=3)
    create_approved_day_ticket(workspace, "expire-early", session_close_at=close.isoformat())
    create_approved_day_ticket(workspace, "expire-no-meta", session_close_at=None)

    freeze_deadline_clock(monkeypatch, close - timedelta(minutes=30))
    early = orders.get_order_ticket_model(workspace, {"ticket_id": "expire-early"})
    reasons_early = orders._approved_only_expiry_reasons(early)
    assert any("has not passed" in r for r in reasons_early)
    with pytest.raises(ValueError):
        orders.expire_approved_only_ticket(workspace, early, "execution-operator", {})

    no_meta = orders.get_order_ticket_model(workspace, {"ticket_id": "expire-no-meta"})
    reasons_no_meta = orders._approved_only_expiry_reasons(no_meta)
    assert any("session_close_at" in r for r in reasons_no_meta)


def test_dispatcher_single_expire_and_idempotent_not_expirable(tmp_path: Path, monkeypatch) -> None:
    workspace = make_workspace(tmp_path)
    close = datetime.now(timezone.utc) + timedelta(hours=3)
    create_approved_day_ticket(workspace, "expire-one", session_close_at=close.isoformat())

    freeze_deadline_clock(monkeypatch, close + timedelta(minutes=1))
    first = orders.expire_stale_approved_orders(workspace, {"principal_id": "execution-operator", "ticket_id": "expire-one"})
    assert first["status"] == "expired"
    assert first["ticket"]["current_state"] == "EXPIRED"

    second = orders.expire_stale_approved_orders(workspace, {"principal_id": "execution-operator", "ticket_id": "expire-one"})
    assert second["status"] == "not_expirable"
    assert any("APPROVED" in r for r in second["reasons"])
    assert '"order_ticket.expire.rejected"' in audit_text(workspace)


def test_sweep_expires_only_eligible_tickets_and_is_idempotent(tmp_path: Path, monkeypatch) -> None:
    workspace = make_workspace(tmp_path)
    close = datetime.now(timezone.utc) + timedelta(hours=3)
    create_approved_day_ticket(workspace, "sweep-eligible", session_close_at=close.isoformat())
    create_approved_day_ticket(workspace, "sweep-no-meta", session_close_at=None)

    freeze_deadline_clock(monkeypatch, close + timedelta(minutes=1))
    swept = orders.expire_stale_approved_orders(workspace, {"principal_id": "execution-operator"})
    assert swept["status"] == "swept"
    expired_ids = {row["ticket_id"] for row in swept["expired"]}
    skipped_ids = {row["ticket_id"] for row in swept["skipped"]}
    assert expired_ids == {"sweep-eligible"}
    assert "sweep-no-meta" in skipped_ids

    again = orders.expire_stale_approved_orders(workspace, {"principal_id": "execution-operator"})
    assert again["expired"] == []
    assert '"order_ticket.expire.swept"' in audit_text(workspace)


def test_mcp_tool_expire_single_and_sweep_as_execution_operator(tmp_path: Path, monkeypatch) -> None:
    workspace = make_workspace(tmp_path)
    close = datetime.now(timezone.utc) + timedelta(hours=3)
    create_approved_day_ticket(workspace, "mcp-expire-one", session_close_at=close.isoformat())
    create_approved_day_ticket(workspace, "mcp-expire-sweep", session_close_at=close.isoformat())

    freeze_deadline_clock(monkeypatch, close + timedelta(minutes=1))

    single = call_mcp_tool(
        workspace,
        "expire_stale_approved_orders",
        {"principal_id": "execution-operator", "ticket_id": "mcp-expire-one", "reason": "canary cleanup"},
    )
    assert single["status"] == "expired"
    assert single["ticket"]["current_state"] == "EXPIRED"
    assert single["expire_reason"] == "ticket_expired_no_resubmit"

    swept = call_mcp_tool(workspace, "expire_stale_approved_orders", {"principal_id": "execution-operator"})
    assert swept["status"] == "swept"
    assert {row["ticket_id"] for row in swept["expired"]} == {"mcp-expire-sweep"}


def test_expire_tool_visible_to_execution_operator():
    from tradingcodex_service.mcp_runtime import TOOL_SPECS

    spec = next(tool for tool in TOOL_SPECS if tool.name == "expire_stale_approved_orders")
    assert "execution-operator" in spec.allowed_roles
    assert spec.requires_approval is True
    assert spec.capability_required == "mcp.tradingcodex.expire_stale_approved_orders"


def test_submit_against_expired_ticket_fails_closed_with_coded_reason(tmp_path: Path, monkeypatch) -> None:
    workspace = make_workspace(tmp_path)
    close = datetime.now(timezone.utc) + timedelta(hours=3)
    create_approved_day_ticket(workspace, "expire-submit", session_close_at=close.isoformat())

    freeze_deadline_clock(monkeypatch, close + timedelta(minutes=1))
    expired = call_mcp_tool(workspace, "expire_stale_approved_orders", {"principal_id": "execution-operator", "ticket_id": "expire-submit"})
    assert expired["status"] == "expired"

    rejected = call_mcp_tool(workspace, "submit_approved_order", {"principal_id": "execution-operator", "ticket_id": "expire-submit"})
    assert rejected["status"] == "rejected"
    assert "ticket_expired_no_resubmit" in rejected["reasons"]


def test_crosswalk_marks_expired_terminal_and_links_successor(tmp_path: Path, monkeypatch) -> None:
    workspace = make_workspace(tmp_path)
    close = datetime.now(timezone.utc) + timedelta(hours=3)
    create_approved_day_ticket(workspace, "expire-xwalk", session_close_at=close.isoformat())

    freeze_deadline_clock(monkeypatch, close + timedelta(minutes=1))
    expired = call_mcp_tool(
        workspace,
        "expire_stale_approved_orders",
        {"principal_id": "execution-operator", "ticket_id": "expire-xwalk", "superseded_by_ticket_id": "expire-xwalk-successor"},
    )
    assert expired["status"] == "expired"

    crosswalk = call_mcp_tool(workspace, "validate_order_approval_crosswalk", {"principal_id": "execution-operator", "ticket_id": "expire-xwalk"})
    row = next(row for row in crosswalk["rows"] if row["canonical_ticket_id"] == "expire-xwalk")
    assert row["latest_status"]["ticket_state"] == "EXPIRED"
    assert row["terminal_inference_allowed"] is True
    assert "voided_without_successor" not in row["anomalies"]
    assert "expire-xwalk-successor" in row["replacement_lineage"]["replacement_ticket_ids"]


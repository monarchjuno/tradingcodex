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
            workspace,
            state,
            state["portfolio_id"],
            state["account_id"],
            state["strategy_id"],
            source="paper-trading",
        )


@pytest.fixture(autouse=True)
def _cleanup_stale_risk_manager_capability() -> None:
    from tradingcodex_service.application.runtime import ensure_runtime_database

    ensure_runtime_database(None)
    from apps.policy.models import Capability, Principal

    risk = Principal.objects.filter(principal_id="risk-manager").first()
    if risk is not None:
        Capability.objects.filter(principal=risk, action="order_ticket.create", effect="allow").delete()
    yield
    if risk is not None:
        Capability.objects.filter(principal=risk, action="order_ticket.create", effect="allow").delete()
    import tradingcodex_service.mcp_runtime as mcp_runtime

    mcp_runtime._REGISTRY_SYNCED = False
    mcp_runtime._REGISTRY_SYNCED_DB = None


def freeze_deadline_clock(monkeypatch, moment: datetime) -> None:
    monkeypatch.setattr(orders, "_deadline_now", lambda: moment)


def create_checked_ticket(
    workspace: Path,
    ticket_id: str,
    session_close_at: str | None = None,
    order_type: str = "limit",
) -> dict:
    _ensure_sufficient_cash(workspace)
    args = {
        "principal_id": "portfolio-manager",
        "ticket_id": ticket_id,
        "symbol": "MSFT",
        "side": "buy",
        "quantity": 1,
        "order_type": order_type,
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
    return checks


def request_approval(workspace: Path, ticket_id: str) -> dict:
    return call_mcp_tool(workspace, "request_order_approval", {"principal_id": "risk-manager", "ticket_id": ticket_id})


def audit_event_types(workspace: Path) -> str:
    audit_path = workspace / "trading" / "audit" / "tradingcodex-mcp.jsonl"
    return audit_path.read_text() if audit_path.exists() else ""


def test_stage_transitions_with_fake_clock(monkeypatch) -> None:
    close = datetime(2026, 7, 10, 20, 0, tzinfo=timezone.utc)
    order = {"session_close_at": close.isoformat(), "time_in_force": "day", "order_type": "limit"}
    stale_meta = {"created_at": (close - timedelta(hours=3)).isoformat()}
    fresh_meta = {"created_at": (close - timedelta(minutes=50)).isoformat()}

    freeze_deadline_clock(monkeypatch, close - timedelta(minutes=61))
    assert orders._session_deadline_evaluation(order, stale_meta)["cutoff_stage"] == ""

    freeze_deadline_clock(monkeypatch, close - timedelta(minutes=60))
    assert orders._session_deadline_evaluation(order, stale_meta)["cutoff_stage"] == "session_revalidation_window"
    assert orders._session_deadline_evaluation(order, fresh_meta)["cutoff_stage"] == ""

    freeze_deadline_clock(monkeypatch, close - timedelta(minutes=30))
    assert orders._session_deadline_evaluation(order, fresh_meta)["cutoff_stage"] == "resting_day_cutoff"
    immediate = {**order, "order_type": "market"}
    assert orders._session_deadline_evaluation(immediate, fresh_meta)["cutoff_stage"] == ""

    freeze_deadline_clock(monkeypatch, close - timedelta(minutes=15))
    assert orders._session_deadline_evaluation(order, fresh_meta)["cutoff_stage"] == "session_close_cutoff"
    assert orders._session_deadline_evaluation(immediate, fresh_meta)["cutoff_stage"] == "session_close_cutoff"

    freeze_deadline_clock(monkeypatch, close)
    assert orders._session_deadline_evaluation(order, fresh_meta)["cutoff_stage"] == "session_close_cutoff"

    non_day = {**order, "time_in_force": "gtc"}
    assert orders._session_deadline_evaluation(non_day, fresh_meta)["cutoff_stage"] == ""

    no_meta = orders._session_deadline_evaluation({"time_in_force": "day"}, fresh_meta)
    assert no_meta["cutoff_stage"] == ""
    assert orders.APPROVAL_WAIT_NO_SESSION_META_WARNING in no_meta["warnings"]

    invalid = orders._session_deadline_evaluation({"session_close_at": "not-a-date", "time_in_force": "day"}, fresh_meta)
    assert invalid["cutoff_stage"] == "invalid_session_close"
    assert invalid["reasons"]


def test_session_deadline_is_stamped_into_approval_table_meta(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    close = datetime.now(timezone.utc) + timedelta(hours=3)
    checks = create_checked_ticket(workspace, "deadline-meta-ticket", session_close_at=close.isoformat())

    meta = checks["approval_table_meta"]
    assert set(meta["invalidates_on"]) >= {
        "session_revalidation_window",
        "resting_day_cutoff",
        "session_close_cutoff",
    }
    deadline = meta["session_deadline"]
    parsed_close = orders._parse_datetime(deadline["session_close_at"])
    assert orders._parse_datetime(deadline["revalidation_due_at"]) == parsed_close - timedelta(minutes=60)
    assert orders._parse_datetime(deadline["resting_day_cutoff_at"]) == parsed_close - timedelta(minutes=30)
    assert orders._parse_datetime(deadline["latest_safe_submit_at"]) == parsed_close - timedelta(minutes=15)
    assert deadline["cutoff_policy_id"] == "approval-wait-session-cutoff-v1"


def test_t60_forces_revalidation_and_re_present_then_allows_one_more_approval(tmp_path: Path, monkeypatch) -> None:
    workspace = make_workspace(tmp_path)
    close = datetime.now(timezone.utc) + timedelta(hours=3)
    create_checked_ticket(workspace, "t60-ticket", session_close_at=close.isoformat())

    freeze_deadline_clock(monkeypatch, close - timedelta(minutes=45))
    stale = request_approval(workspace, "t60-ticket")

    assert stale["status"] == "recheck_required"
    assert "session-revalidation-window invalidated approval table" in "\n".join(stale["reasons"])
    re_present = stale["re_present"]
    assert re_present["ticket_id"] == "t60-ticket"
    assert re_present["order_payload_hash"]
    assert re_present["remaining_minutes_to_close"] == 45
    assert "fill chance degrades" in re_present["fill_chance_note"]
    assert "re-present" in re_present["next_action"]
    from apps.orders.models import ApprovalReceipt

    assert ApprovalReceipt.objects.filter(order_ticket_id="t60-ticket").count() == 0

    rechecked = call_mcp_tool(workspace, "run_order_checks", {"principal_id": "portfolio-manager", "ticket_id": "t60-ticket"})
    assert rechecked["approval_ready"] is True
    approval = request_approval(workspace, "t60-ticket")
    assert approval["status"] == "approved", approval
    receipt = approval["approval_receipt"]
    latest_safe = orders._parse_datetime(receipt["approval_table_meta"]["session_deadline"]["latest_safe_submit_at"])
    assert orders._parse_datetime(receipt["expires_at"]) <= latest_safe
    assert orders._parse_datetime(receipt["valid_until"]) <= latest_safe


def test_t30_blocks_resting_day_approval_with_cutoff_status(tmp_path: Path, monkeypatch) -> None:
    workspace = make_workspace(tmp_path)
    close = datetime.now(timezone.utc) + timedelta(hours=3)
    freeze_deadline_clock(monkeypatch, close - timedelta(minutes=25))
    create_checked_ticket(workspace, "t30-ticket", session_close_at=close.isoformat())

    blocked = request_approval(workspace, "t30-ticket")

    assert blocked["status"] == "approval_wait_cutoff"
    assert blocked["cutoff_stage"] == "resting_day_cutoff"
    assert "resting-day-cutoff invalidated approval table" in "\n".join(blocked["reasons"])
    assert "next-session successor" in blocked["re_present"]["next_action"]
    from apps.orders.models import ApprovalReceipt

    assert ApprovalReceipt.objects.filter(order_ticket_id="t30-ticket").count() == 0
    assert '"approval_wait.cutoff"' in audit_event_types(workspace)


def test_t15_blocks_all_day_submits_and_invalidates_receipts(tmp_path: Path, monkeypatch) -> None:
    workspace = make_workspace(tmp_path)
    close = datetime.now(timezone.utc) + timedelta(hours=3)
    create_checked_ticket(workspace, "t15-ticket", session_close_at=close.isoformat())
    approval = request_approval(workspace, "t15-ticket")
    assert approval["status"] == "approved", approval

    freeze_deadline_clock(monkeypatch, close - timedelta(minutes=10))
    rejected = call_mcp_tool(workspace, "submit_approved_order", {"principal_id": "execution-operator", "ticket_id": "t15-ticket"})

    assert rejected["status"] == "rejected"
    assert rejected["cutoff_stage"] == "session_close_cutoff"
    assert "session-close-cutoff invalidated approval table" in "\n".join(rejected["reasons"])
    from apps.orders.models import ApprovalReceipt

    assert ApprovalReceipt.objects.filter(order_ticket_id="t15-ticket", valid=True).count() == 0
    assert '"approval_wait.cutoff"' in audit_event_types(workspace)


def test_t15_blocks_immediate_execution_receipts_too(tmp_path: Path, monkeypatch) -> None:
    workspace = make_workspace(tmp_path)
    close = datetime.now(timezone.utc) + timedelta(hours=3)
    freeze_deadline_clock(monkeypatch, close - timedelta(minutes=10))
    create_checked_ticket(workspace, "t15-immediate-ticket", session_close_at=close.isoformat())

    blocked = request_approval(workspace, "t15-immediate-ticket")

    assert blocked["status"] == "approval_wait_cutoff"
    assert blocked["cutoff_stage"] == "session_close_cutoff"


def test_ticket_without_session_meta_keeps_existing_flow_with_warning(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    checks = create_checked_ticket(workspace, "no-deadline-ticket")

    meta = checks["approval_table_meta"]
    assert "session_deadline" not in meta
    assert not set(meta["invalidates_on"]) & {"session_revalidation_window", "resting_day_cutoff", "session_close_cutoff"}

    approval = request_approval(workspace, "no-deadline-ticket")
    assert approval["status"] == "approved", approval
    assert orders.APPROVAL_WAIT_NO_SESSION_META_WARNING in "\n".join(approval["warnings"])


def test_invalid_session_close_at_is_rejected_at_creation(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    _ensure_sufficient_cash(workspace)
    with pytest.raises(ValueError, match="session_close_at"):
        orders.create_order_ticket(
            workspace,
            {
                "principal_id": "portfolio-manager",
                "ticket_id": "invalid-close-ticket",
                "symbol": "MSFT",
                "side": "buy",
                "quantity": 1,
                "limit_price": 1000,
                "session_close_at": "not-a-date",
            },
        )

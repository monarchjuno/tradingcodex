from __future__ import annotations

from pathlib import Path

import pytest

from tradingcodex_cli.generator import bootstrap_workspace
from tradingcodex_service.application.orders import validate_approval_receipt
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
def create_checked_ticket(workspace: Path, ticket_id: str = "validity-ticket") -> dict:
    _ensure_sufficient_cash(workspace)
    created = call_mcp_tool(
        workspace,
        "create_order_ticket",
        {
            "principal_id": "portfolio-manager",
            "ticket_id": ticket_id,
            "symbol": "MSFT",
            "side": "buy",
            "quantity": 1,
            "order_type": "limit",
            "limit_price": 1000,
            "time_in_force": "day",
            "currency": "KRW",
        },
    )
    assert created["ticket"]["current_state"] == "DRAFT"
    checks = call_mcp_tool(workspace, "run_order_checks", {"principal_id": "portfolio-manager", "ticket_id": ticket_id})
    assert checks["approval_ready"] is True, checks
    return checks


def approval_table_check(ticket_id: str):
    from apps.orders.models import OrderCheckRun

    check = OrderCheckRun.objects.get(ticket__ticket_id=ticket_id, check_type="approval_table")
    assert isinstance(check.payload, dict)
    assert isinstance(check.payload.get("approval_table_meta"), dict)
    return check


def test_run_order_checks_writes_machine_readable_approval_table_meta(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    checks = create_checked_ticket(workspace, "meta-ticket")

    meta = checks["approval_table_meta"]
    assert meta["schema_version"] == 1
    assert meta["valid_until"]
    assert set(meta["invalidates_on"]) >= {
        "quote_drift",
        "cash_delta",
        "order_status_delta",
        "replacement_created",
        "terminal_refresh_failed",
        "age_threshold",
    }
    assert meta["rows"][0]["ticket_id"] == "meta-ticket"
    assert meta["rows"][0]["quote_as_of"]
    assert meta["rows"][0]["cash_as_of"]
    assert meta["rows"][0]["order_status_as_of"]
    stress = meta["cash_reserve_stress"]
    assert stress["pre_batch_orderable"] >= stress["total_notional"]
    assert "fee_tax_reserve" in stress
    assert "post_batch_residual" in stress
    assert "residual_warning_threshold" in stress

    stored = approval_table_check("meta-ticket")
    assert stored.payload["approval_table_meta"]["valid_until"] == meta["valid_until"]


def test_request_order_approval_rejects_expired_approval_table(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    create_checked_ticket(workspace, "expired-table-ticket")
    check = approval_table_check("expired-table-ticket")
    payload = dict(check.payload)
    meta = dict(payload["approval_table_meta"])
    meta["valid_until"] = "2000-01-01T00:00:00Z"
    payload["approval_table_meta"] = meta
    check.payload = payload
    check.save(update_fields=["payload"])

    approval = call_mcp_tool(workspace, "request_order_approval", {"principal_id": "risk-manager", "ticket_id": "expired-table-ticket"})

    assert approval["status"] == "recheck_required"
    assert "approval table valid_until is expired" in "\n".join(approval["reasons"])
    from apps.orders.models import ApprovalReceipt

    assert ApprovalReceipt.objects.filter(order_ticket_id="expired-table-ticket").count() == 0


def test_request_order_approval_rejects_cash_delta_after_checks(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    create_checked_ticket(workspace, "cash-delta-ticket")

    state = load_paper_portfolio_state(workspace)
    state["cash_krw"] = 1
    persist_paper_portfolio_state(
        workspace,
        state,
        state["portfolio_id"],
        state["account_id"],
        state["strategy_id"],
        source="paper-trading",
    )

    approval = call_mcp_tool(workspace, "request_order_approval", {"principal_id": "risk-manager", "ticket_id": "cash-delta-ticket"})

    assert approval["status"] == "recheck_required"
    assert "cash delta invalidated approval table" in "\n".join(approval["reasons"])


def test_request_order_approval_rejects_order_status_delta_after_checks(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    create_checked_ticket(workspace, "status-delta-ticket")

    from apps.orders.models import OrderTicket
    from tradingcodex_service.application.orders import transition_order_ticket

    ticket = OrderTicket.objects.get(ticket_id="status-delta-ticket")
    transition_order_ticket(ticket, "NEEDS_REVIEW", "test", {"reason": "status drift regression test"})

    approval = call_mcp_tool(workspace, "request_order_approval", {"principal_id": "risk-manager", "ticket_id": "status-delta-ticket"})

    assert approval["status"] == "recheck_required"
    assert "order-status delta invalidated approval table" in "\n".join(approval["reasons"])


def test_legacy_metadata_free_checks_keep_approval_flow_with_warning(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    create_checked_ticket(workspace, "legacy-ticket")
    approval_table_check("legacy-ticket").delete()

    approval = call_mcp_tool(workspace, "request_order_approval", {"principal_id": "risk-manager", "ticket_id": "legacy-ticket"})

    assert approval["status"] == "approved", approval
    assert "legacy approval table has no machine-readable validity metadata" in "\n".join(approval["warnings"])
    receipt = approval["approval_receipt"]
    assert "approval_table_meta" not in receipt


def test_validate_approval_receipt_rejects_stale_embedded_table_meta(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    create_checked_ticket(workspace, "receipt-stale-ticket")
    approval = call_mcp_tool(workspace, "request_order_approval", {"principal_id": "risk-manager", "ticket_id": "receipt-stale-ticket"})
    assert approval["status"] == "approved", approval

    receipt = dict(approval["approval_receipt"])
    meta = dict(receipt["approval_table_meta"])
    meta["valid_until"] = "2000-01-01T00:00:00Z"
    receipt["approval_table_meta"] = meta

    invalid = validate_approval_receipt(workspace, {"ticket_id": "receipt-stale-ticket", "approval_receipt": receipt})

    assert invalid["valid"] is False
    assert "approval table valid_until is expired" in "\n".join(invalid["reasons"])

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

from tradingcodex_service.application.audit import write_audit_event
from tradingcodex_service.application.common import _parse_datetime, now_iso, stable_hash
from tradingcodex_service.application.order_lineage import MANUAL_EXECUTION_EVENT_TYPE
from tradingcodex_service.application.portfolio import portfolio_keys
from tradingcodex_service.application.runtime import ensure_runtime_database, workspace_context_payload


UNATTRIBUTED_FILL_EVENT_TYPE = "unattributed_fill"


def record_unattributed_fills(workspace_root, *, connection, broker_account, adapter, sync_run_id=None) -> dict[str, Any]:
    try:
        fills = adapter.get_fills(broker_account.broker_account_id)
    except Exception as exc:
        return {"rows": [], "warnings": [f"fill sync failed for broker account {broker_account.broker_account_id}: {exc}"]}
    from apps.orders.models import BrokerOrder, Fill
    from apps.portfolio.models import PortfolioLedgerEvent

    portfolio_id, account_id, strategy_id = portfolio_keys(
        {
            "portfolio_id": broker_account.metadata.get("portfolio_id") if isinstance(broker_account.metadata, dict) else "",
            "account_id": broker_account.broker_account_id,
            "strategy_id": broker_account.metadata.get("strategy_id") if isinstance(broker_account.metadata, dict) else "",
        },
        workspace_root,
    )
    rows: list[dict[str, Any]] = []
    for fill in fills:
        if fill.broker_order_id and BrokerOrder.objects.filter(broker_order_id=fill.broker_order_id).exists():
            continue
        if Fill.objects.filter(broker_order_id=fill.broker_order_id, fill_id=fill.fill_id).exists():
            continue
        payload = {
            "fill_id": fill.fill_id,
            "broker_order_id": fill.broker_order_id,
            "symbol": str(fill.symbol or "").upper(),
            "side": str(fill.side or "").lower(),
            "quantity": float(fill.quantity),
            "price": float(fill.price),
            "fee": float(fill.fee or 0),
            "currency": fill.currency,
            "filled_at": fill.filled_at,
            "broker_connection_id": connection.broker_id,
            "broker_account_id": broker_account.broker_account_id,
        }
        payload_hash = stable_hash(payload)
        event = PortfolioLedgerEvent.objects.filter(event_type=UNATTRIBUTED_FILL_EVENT_TYPE, source_payload_hash=payload_hash).first()
        if event is None:
            event = PortfolioLedgerEvent.objects.create(
                event_type=UNATTRIBUTED_FILL_EVENT_TYPE,
                broker_connection=connection,
                broker_account=broker_account,
                portfolio_id=portfolio_id,
                account_id=account_id,
                strategy_id=strategy_id,
                instrument_id=payload["symbol"],
                symbol=payload["symbol"],
                quantity=fill.quantity,
                price=fill.price,
                amount=Decimal(str(fill.quantity)) * Decimal(str(fill.price)),
                currency=fill.currency,
                event_at=_parse_datetime(fill.filled_at),
                source_payload_hash=payload_hash,
                raw_payload_ref=f"broker_sync_run:{sync_run_id}" if sync_run_id else "",
                metadata={**payload, "attribution_status": "unattributed"},
            )
        rows.append(serialize_unattributed_fill(event))
    return {"rows": rows, "warnings": []}


def serialize_unattributed_fill(event) -> dict[str, Any]:
    metadata = event.metadata if isinstance(event.metadata, dict) else {}
    linked_ticket_id = str(metadata.get("linked_ticket_id") or "")
    return {
        "ledger_event_id": event.pk,
        "fill_id": str(metadata.get("fill_id") or ""),
        "broker_order_id": str(metadata.get("broker_order_id") or ""),
        "broker_connection_id": event.broker_connection.broker_id if event.broker_connection else str(metadata.get("broker_connection_id") or ""),
        "broker_account_id": event.broker_account.broker_account_id if event.broker_account else str(metadata.get("broker_account_id") or ""),
        "symbol": event.symbol or str(metadata.get("symbol") or ""),
        "side": str(metadata.get("side") or ""),
        "quantity": float(event.quantity or 0),
        "price": float(event.price or 0),
        "fee": float(metadata.get("fee") or 0),
        "currency": event.currency,
        "filled_at": event.event_at.isoformat() if event.event_at else str(metadata.get("filled_at") or ""),
        "attribution_status": "linked" if linked_ticket_id else "unattributed",
        "linked_ticket_id": linked_ticket_id,
        "linked_by": str(metadata.get("linked_by") or ""),
        "linked_at": str(metadata.get("linked_at") or ""),
        "provenance": "manual" if linked_ticket_id else "unattributed_broker_fill",
        "recorded_at": event.created_at.isoformat() if event.created_at else "",
    }


def list_unattributed_fills(workspace_root, args=None) -> dict[str, Any]:
    args = args or {}
    root = Path(workspace_root)
    ensure_runtime_database(root)
    from apps.portfolio.models import PortfolioLedgerEvent

    limit = max(1, min(int(args.get("limit") or 100), 500))
    queryset = PortfolioLedgerEvent.objects.select_related("broker_connection", "broker_account").filter(
        event_type=UNATTRIBUTED_FILL_EVENT_TYPE
    )
    broker_id = str(args.get("broker_id") or args.get("broker_connection_id") or "")
    if broker_id:
        queryset = queryset.filter(broker_connection__broker_id=broker_id)
    broker_account_id = str(args.get("broker_account_id") or "")
    if broker_account_id:
        queryset = queryset.filter(broker_account__broker_account_id=broker_account_id)
    rows = [serialize_unattributed_fill(event) for event in queryset.order_by("-created_at", "-id")[:limit]]
    attribution_status = str(args.get("attribution_status") or "")
    if attribution_status:
        rows = [row for row in rows if row["attribution_status"] == attribution_status]
    return {
        "status": "ok",
        "unattributed_fills": rows,
        "counts": {
            "unattributed": sum(1 for row in rows if row["attribution_status"] == "unattributed"),
            "linked": sum(1 for row in rows if row["attribution_status"] == "linked"),
        },
        "db_canonical": True,
        "workspace_context": workspace_context_payload(root),
    }


def annotate_manual_execution(workspace_root, args=None) -> dict[str, Any]:
    args = args or {}
    root = Path(workspace_root)
    ensure_runtime_database(root)
    from apps.orders.models import OrderTicket
    from apps.portfolio.models import PortfolioLedgerEvent

    principal_id = str(args.get("principal_id") or "execution-operator")
    fill_id = str(args.get("fill_id") or "")
    broker_order_id = str(args.get("broker_order_id") or "")
    ticket_id = str(args.get("ticket_id") or args.get("order_ticket_id") or "")
    evidence = args.get("evidence")
    evidence_text = evidence if isinstance(evidence, str) else stable_hash(evidence) if evidence else ""

    reasons: list[str] = []
    if not fill_id:
        reasons.append("fill_id is required")
    if not ticket_id:
        reasons.append("ticket_id is required")
    if not str(evidence_text or "").strip():
        reasons.append("evidence is required to link a manual execution")

    event = None
    if fill_id:
        queryset = PortfolioLedgerEvent.objects.filter(event_type=UNATTRIBUTED_FILL_EVENT_TYPE, metadata__fill_id=fill_id)
        if broker_order_id:
            queryset = queryset.filter(metadata__broker_order_id=broker_order_id)
        event = queryset.order_by("-created_at", "-id").first()
        if event is None:
            reasons.append(f"unattributed fill not found: {fill_id}; run sync_broker_account first")
    ticket = OrderTicket.objects.filter(ticket_id=ticket_id).first() if ticket_id else None
    if ticket_id and ticket is None:
        reasons.append(f"order ticket not found: {ticket_id}")

    fill_row = serialize_unattributed_fill(event) if event is not None else {}
    if event is not None and fill_row["attribution_status"] == "linked":
        reasons.append(f"unattributed fill is already linked to ticket {fill_row['linked_ticket_id']}")
    if ticket is not None:
        if ticket.current_state != "VOIDED":
            reasons.append(f"manual execution can only be linked to a VOIDED ticket; {ticket_id} is {ticket.current_state}")
        if event is not None:
            if fill_row["symbol"] and str(ticket.symbol).upper() != fill_row["symbol"]:
                reasons.append(f"fill symbol {fill_row['symbol']} does not match ticket symbol {str(ticket.symbol).upper()}")
            if fill_row["side"] and str(ticket.side).lower() != fill_row["side"]:
                reasons.append(f"fill side {fill_row['side']} does not match ticket side {str(ticket.side).lower()}")
            if fill_row["currency"] and ticket.currency and fill_row["currency"] != ticket.currency:
                reasons.append(f"fill currency {fill_row['currency']} does not match ticket currency {ticket.currency}")

    if reasons:
        rejected = {
            "status": "rejected",
            "ticket_id": ticket_id,
            "fill_id": fill_id,
            "reasons": reasons,
            "db_canonical": True,
            "workspace_context": workspace_context_payload(root),
        }
        write_audit_event(root, {"type": "manual_execution.link.rejected", "payload": rejected}, principal_id, "service")
        return rejected

    realized = _realized_from_fill(fill_row, args)
    linked_at = now_iso()
    node_payload = {
        "fill_id": fill_row["fill_id"],
        "broker_order_id": fill_row["broker_order_id"],
        "symbol": fill_row["symbol"],
        "side": fill_row["side"],
        "quantity": fill_row["quantity"],
        "price": fill_row["price"],
        "fee": fill_row["fee"],
        "currency": fill_row["currency"],
        "filled_at": fill_row["filled_at"],
        "provenance": "manual",
        "evidence": evidence if isinstance(evidence, (str, dict, list)) else str(evidence),
        "realized": realized,
        "ledger_event_id": event.pk,
    }
    from tradingcodex_service.application.orders import record_order_event

    record_order_event(ticket, MANUAL_EXECUTION_EVENT_TYPE, principal_id, node_payload)
    metadata = dict(event.metadata or {})
    metadata.update(
        {
            "attribution_status": "linked",
            "linked_ticket_id": ticket.ticket_id,
            "linked_by": principal_id,
            "linked_at": linked_at,
            "evidence": node_payload["evidence"],
            "provenance": "manual",
        }
    )
    event.metadata = metadata
    event.save(update_fields=["metadata"])
    result = {
        "status": "linked",
        "provenance": "manual",
        "ticket_id": ticket.ticket_id,
        "fill_id": fill_row["fill_id"],
        "broker_order_id": fill_row["broker_order_id"],
        "manual_execution": {**node_payload, "node_type": "manual_execution", "linked_by": principal_id, "linked_at": linked_at},
        "realized": realized,
        "unattributed_fill": serialize_unattributed_fill(event),
        "db_canonical": True,
        "workspace_context": workspace_context_payload(root),
    }
    write_audit_event(root, {"type": "manual_execution.link.accepted", "payload": result}, principal_id, "service")
    return result


def _realized_from_fill(fill_row: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
    quantity = Decimal(str(fill_row["quantity"]))
    price = Decimal(str(fill_row["price"]))
    fee = Decimal(str(fill_row["fee"]))
    gross = quantity * price
    net = gross - fee if fill_row["side"] != "buy" else gross + fee
    cost_basis_price = args.get("cost_basis_price")
    realized_pnl = None
    if cost_basis_price is not None and fill_row["side"] == "sell":
        realized_pnl = float((price - Decimal(str(cost_basis_price))) * quantity - fee)
    return {
        "source": "broker_fill",
        "currency": fill_row["currency"],
        "quantity": float(quantity),
        "price": float(price),
        "gross_notional": float(gross),
        "fee": float(fee),
        "net_amount": float(net),
        "cost_basis_price": float(cost_basis_price) if cost_basis_price is not None else None,
        "realized_pnl": realized_pnl,
    }

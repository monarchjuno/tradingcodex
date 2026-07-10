from __future__ import annotations

from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path
from typing import Any

from tradingcodex_service.application.orders import order_payload_from_ticket, serialize_order_ticket
from tradingcodex_service.application.portfolio import portfolio_keys
from tradingcodex_service.application.runtime import ensure_runtime_database, workspace_context_payload


TERMINAL_TICKET_STATES = {"FILLED", "REJECTED", "CANCELED", "EXPIRED", "FAILED", "VOIDED"}
UNRESOLVED_TICKET_STATES = {"APPROVED", "RESERVED", "SUBMITTED", "ACKED", "PARTIALLY_FILLED", "NEEDS_REVIEW"}
BROKER_TERMINAL_STATUSES = {"filled", "rejected", "canceled", "cancelled", "expired", "failed"}
BROKER_UNRESOLVED_STATUSES = {"", "unknown", "open", "new", "submitted", "accepted", "acked", "pending", "partial", "partially_filled"}
BROKER_ORDER_REQUIRED_STATES = {"APPROVED", "RESERVED", "SUBMITTED", "ACKED", "PARTIALLY_FILLED", "FILLED", "NEEDS_REVIEW"}
REPLACEMENT_ORIGINAL_KEYS = ("original_ticket_id", "replaces_ticket_id", "replacement_of", "original_order_ticket_id")
REPLACEMENT_CHILD_KEYS = ("replacement_ticket_id", "replacement_order_ticket_id", "replaced_by_ticket_id", "superseded_by_ticket_id")
MANUAL_EXECUTION_EVENT_TYPE = "manual_execution_linked"


def validate_order_approval_crosswalk(workspace_root, args=None):
    args = args or {}
    root = Path(workspace_root)
    ensure_runtime_database(root)
    from apps.orders.models import ApprovalReceipt, OrderTicket

    portfolio_id, account_id, strategy_id = portfolio_keys(args, root)
    ticket_queryset = (
        OrderTicket.objects.select_related("broker_connection", "broker_account")
        .prefetch_related("broker_orders", "fills", "events", "check_runs")
        .filter(portfolio_id=portfolio_id, account_id=account_id, strategy_id=strategy_id)
    )
    ticket_id = str(args.get("ticket_id") or args.get("order_ticket_id") or args.get("order_id") or "")
    approval_receipt_id = str(args.get("approval_receipt_id") or args.get("receipt_id") or "")
    broker_order_id = str(args.get("broker_order_id") or "")
    lookup_filters = {
        key: value
        for key, value in (("ticket_id", ticket_id), ("approval_receipt_id", approval_receipt_id), ("broker_order_id", broker_order_id))
        if value
    }
    if ticket_id:
        ticket_queryset = ticket_queryset.filter(ticket_id=ticket_id)
    if broker_order_id:
        ticket_queryset = ticket_queryset.filter(broker_orders__broker_order_id=broker_order_id)

    try:
        tickets = _matched_tickets(ticket_queryset, args)
        ticket_ids = {ticket.ticket_id for ticket in tickets}
        approval_queryset = ApprovalReceipt.objects.all().order_by("-created_at", "-id")
        if approval_receipt_id:
            approval_queryset = approval_queryset.filter(receipt_id=approval_receipt_id)
        elif lookup_filters or ticket_ids:
            approval_queryset = approval_queryset.filter(order_ticket_id__in=ticket_ids)
        approvals = list(approval_queryset[: _limit(args)])

        if approval_receipt_id and not tickets:
            approved_ticket_ids = [approval.order_ticket_id for approval in approvals if approval.order_ticket_id]
            tickets = list(
                OrderTicket.objects.select_related("broker_connection", "broker_account")
                .prefetch_related("broker_orders", "fills", "events", "check_runs")
                .filter(ticket_id__in=approved_ticket_ids)
            )
    except Exception as exc:
        return _lookup_failed_payload(root, exc)

    if lookup_filters and not tickets and not approvals:
        return _no_match_payload(root, lookup_filters)
    ticket_by_id = {ticket.ticket_id: ticket for ticket in tickets}
    lineage_index = _lineage_index(portfolio_id, account_id, strategy_id)

    rows = []
    for approval in approvals:
        ticket = ticket_by_id.get(approval.order_ticket_id)
        if ticket is None:
            rows.append(_missing_ticket_row(approval))
            continue
        rows.append(_crosswalk_row(approval, ticket, lineage_index))

    if not approvals and tickets:
        for ticket in tickets:
            rows.append(_crosswalk_row(None, ticket, lineage_index))

    anomaly_counts = Counter(anomaly for row in rows for anomaly in row["anomalies"])
    blockers = sorted(anomaly_counts)
    return {
        "status": "anomaly" if blockers else "ok",
        "terminal_inference_allowed": not blockers,
        "terminal_inference_blockers": blockers,
        "anomaly_counts": dict(sorted(anomaly_counts.items())),
        "rows": rows,
        "db_canonical": True,
        "workspace_context": workspace_context_payload(root),
    }

def get_pre_approval_occupancy(workspace_root, args=None):
    args = args or {}
    root = Path(workspace_root)
    ensure_runtime_database(root)
    from apps.orders.models import OrderTicket

    portfolio_id, default_account_id, strategy_id = portfolio_keys(args, root)
    queryset = (
        OrderTicket.objects.select_related("broker_connection", "broker_account")
        .prefetch_related("broker_orders", "fills", "events", "check_runs")
        .filter(portfolio_id=portfolio_id, strategy_id=strategy_id)
        .exclude(current_state__in=TERMINAL_TICKET_STATES)
    )
    symbol = str(args.get("symbol") or "").upper()
    side = str(args.get("side") or "").lower()
    broker_account_id = str(args.get("broker_account_id") or "")
    requested_account_id = str(args.get("account_id") or "")
    account_id = requested_account_id or default_account_id
    if requested_account_id:
        queryset = queryset.filter(account_id=requested_account_id)
    else:
        queryset = queryset.filter(account_id=default_account_id)
    if symbol:
        queryset = queryset.filter(symbol=symbol)
    if side:
        queryset = queryset.filter(side=side)
    if broker_account_id:
        queryset = queryset.filter(broker_account__broker_account_id=broker_account_id)

    lineage_index = _lineage_index(portfolio_id, account_id, strategy_id)
    rows = [_occupancy_row(ticket, lineage_index) for ticket in queryset.order_by("broker_account__broker_account_id", "symbol", "side", "-created_at", "-id")[: _limit(args)]]
    groups = _occupancy_groups(rows)
    overlap = _overlap_policy(rows, args)
    return {
        "status": overlap["disposition"],
        "portfolio_id": portfolio_id,
        "account_id": account_id,
        "strategy_id": strategy_id,
        "filters": {"symbol": symbol, "side": side, "broker_account_id": broker_account_id},
        "reserved_notional_conservative": float(sum(Decimal(str(row["reserved_notional_conservative"])) for row in rows)),
        "overlap_policy": overlap,
        "groups": groups,
        "db_canonical": True,
        "workspace_context": workspace_context_payload(root),
    }

def _limit(args):
    return max(1, min(int(args.get("limit") or 200), 500))


def _matched_tickets(ticket_queryset, args):
    return list(ticket_queryset.distinct().order_by("-created_at", "-id")[: _limit(args)])


def _lookup_failed_payload(root, exc):
    return {
        "status": "error",
        "success": False,
        "error": "broker_order_lookup_failed",
        "failure_reason": str(exc),
        "terminal_inference_allowed": False,
        "terminal_inference_blockers": ["broker_order_lookup_failed"],
        "anomaly_counts": {},
        "rows": [],
        "db_canonical": True,
        "workspace_context": workspace_context_payload(root),
    }


def _no_match_payload(root, lookup_filters):
    return {
        "status": "no_match",
        "no_match": True,
        "no_match_reasons": [f"no order ticket or approval matched {key}={value}" for key, value in lookup_filters.items()],
        "filters": dict(lookup_filters),
        "terminal_inference_allowed": False,
        "terminal_inference_blockers": ["no_match"],
        "anomaly_counts": {},
        "rows": [],
        "db_canonical": True,
        "workspace_context": workspace_context_payload(root),
    }


def _crosswalk_row(approval, ticket, lineage_index):
    broker_orders = list(ticket.broker_orders.all())
    broker_order_ids = [broker_order.broker_order_id for broker_order in broker_orders if broker_order.broker_order_id]
    latest_status = _latest_status(ticket, broker_orders)
    lineage = _lineage_for_ticket(ticket, lineage_index)
    anomalies = _row_anomalies(approval, ticket, broker_orders, lineage)
    return {
        "approval_receipt_id": approval.receipt_id if approval else "",
        "approval_row_pk": approval.pk if approval else None,
        "approval_table_path": "db:orders.ApprovalReceipt" if approval else "",
        "canonical_ticket_id": ticket.ticket_id,
        "submitted_ticket_id": lineage["path"][-1] if lineage["path"] else ticket.ticket_id,
        "broker_order_ids": broker_order_ids,
        "account_id": ticket.account_id,
        "broker_connection_id": ticket.broker_connection.broker_id if ticket.broker_connection else "",
        "broker_account_id": ticket.broker_account.broker_account_id if ticket.broker_account else "",
        "symbol": ticket.symbol,
        "side": ticket.side,
        "quantity": float(ticket.quantity),
        "limit_price": float(ticket.limit_price or 0),
        "time_in_force": ticket.time_in_force,
        "estimated_notional": float(ticket.estimated_notional or 0),
        "currency": ticket.currency,
        "replacement_lineage": lineage,
        "latest_status": latest_status,
        "ticket": serialize_order_ticket(ticket, include_related=True),
        "anomalies": anomalies,
        "terminal_inference_allowed": not anomalies,
    }


def _missing_ticket_row(approval):
    return {
        "approval_receipt_id": approval.receipt_id,
        "approval_row_pk": approval.pk,
        "approval_table_path": "db:orders.ApprovalReceipt",
        "canonical_ticket_id": approval.order_ticket_id,
        "submitted_ticket_id": "",
        "broker_order_ids": [],
        "account_id": "",
        "broker_connection_id": approval.broker_connection_id,
        "broker_account_id": approval.broker_account_id,
        "symbol": "",
        "side": "",
        "quantity": 0.0,
        "limit_price": 0.0,
        "time_in_force": "",
        "estimated_notional": float(approval.max_notional or 0),
        "currency": "",
        "replacement_lineage": {"original_ticket_id": "", "replacement_ticket_ids": [], "manual_executions": [], "path": [approval.order_ticket_id] if approval.order_ticket_id else []},
        "latest_status": {"ticket_state": "MISSING", "broker_status": "", "source": "approval", "source_timestamp": approval.created_at.isoformat()},
        "ticket": {},
        "anomalies": ["id_mismatch"],
        "terminal_inference_allowed": False,
    }

def _row_anomalies(approval, ticket, broker_orders, lineage):
    anomalies = []
    if approval is not None:
        if approval.order_ticket_id != ticket.ticket_id:
            anomalies.append("id_mismatch")
        if approval.exact_order_hash:
            try:
                from tradingcodex_service.application.common import stable_hash

                if stable_hash(order_payload_from_ticket(ticket)) != approval.exact_order_hash:
                    anomalies.append("id_mismatch")
            except Exception:
                anomalies.append("id_mismatch")
    if ticket.current_state in BROKER_ORDER_REQUIRED_STATES and not any(order.broker_order_id for order in broker_orders):
        anomalies.append("missing_broker_order_id")
    if len(lineage["replacement_ticket_ids"]) > 1:
        anomalies.append("duplicate_replacement")
    if lineage["replacement_ticket_ids"] and ticket.current_state not in TERMINAL_TICKET_STATES:
        anomalies.append("stale_original")
    if ticket.current_state == "VOIDED" and not lineage["replacement_ticket_ids"] and not lineage.get("manual_executions"):
        anomalies.append("voided_without_successor")
    if _is_unresolved_unknown(ticket, broker_orders):
        anomalies.append("unresolved_acked_or_unknown")
    return sorted(set(anomalies))


def _is_unresolved_unknown(ticket, broker_orders):
    if ticket.current_state in {"ACKED", "NEEDS_REVIEW"}:
        return True
    for broker_order in broker_orders:
        status = str(broker_order.broker_status or "").lower()
        if status in BROKER_UNRESOLVED_STATUSES:
            return True
    return False


def _latest_status(ticket, broker_orders):
    latest_broker = None
    for broker_order in broker_orders:
        if latest_broker is None:
            latest_broker = broker_order
            continue
        current_time = broker_order.last_seen_at or broker_order.submitted_at or ticket.updated_at
        latest_time = latest_broker.last_seen_at or latest_broker.submitted_at or ticket.updated_at
        if current_time > latest_time:
            latest_broker = broker_order
    if latest_broker is not None:
        source_time = latest_broker.last_seen_at or latest_broker.submitted_at or ticket.updated_at
        return {
            "ticket_state": ticket.current_state,
            "broker_status": latest_broker.broker_status,
            "source": "broker_order",
            "source_timestamp": source_time.isoformat() if source_time else "",
        }
    return {
        "ticket_state": ticket.current_state,
        "broker_status": "",
        "source": "order_ticket",
        "source_timestamp": ticket.updated_at.isoformat(),
    }

def _lineage_index(portfolio_id, account_id, strategy_id):
    from apps.orders.models import OrderTicket

    tickets = list(
        OrderTicket.objects.prefetch_related("events", "broker_orders")
        .filter(portfolio_id=portfolio_id, account_id=account_id, strategy_id=strategy_id)
    )
    replacements_by_original = defaultdict(set)
    original_by_replacement = {}
    for ticket in tickets:
        original = _extract_first_value(ticket, REPLACEMENT_ORIGINAL_KEYS)
        if original:
            original_by_replacement[ticket.ticket_id] = original
            replacements_by_original[original].add(ticket.ticket_id)
        child = _extract_first_value(ticket, REPLACEMENT_CHILD_KEYS)
        if child:
            replacements_by_original[ticket.ticket_id].add(child)
            original_by_replacement[child] = ticket.ticket_id
    return {
        "replacements_by_original": {key: sorted(value) for key, value in replacements_by_original.items()},
        "original_by_replacement": original_by_replacement,
    }


def _lineage_for_ticket(ticket, lineage_index):
    original_by_replacement = lineage_index.get("original_by_replacement", {})
    replacements_by_original = lineage_index.get("replacements_by_original", {})
    original = original_by_replacement.get(ticket.ticket_id, "")
    replacement_ids = list(replacements_by_original.get(ticket.ticket_id, []))
    manual_executions = _manual_execution_nodes(ticket)
    path = [original, ticket.ticket_id] if original else [ticket.ticket_id]
    if replacement_ids:
        path.extend(replacement_ids)
    path.extend(node["node_id"] for node in manual_executions)
    return {
        "original_ticket_id": original,
        "replacement_ticket_ids": replacement_ids,
        "manual_executions": manual_executions,
        "path": [item for item in path if item],
    }


def _manual_execution_nodes(ticket):
    nodes = []
    for event in ticket.events.all():
        if event.event_type != MANUAL_EXECUTION_EVENT_TYPE:
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        fill_id = str(payload.get("fill_id") or "")
        nodes.append({
            "node_type": "manual_execution",
            "node_id": f"manual_execution:{fill_id}" if fill_id else f"manual_execution:event-{event.pk}",
            "fill_id": fill_id,
            "broker_order_id": str(payload.get("broker_order_id") or ""),
            "provenance": "manual",
            "linked_by": event.actor,
            "linked_at": event.created_at.isoformat() if event.created_at else "",
            "evidence": payload.get("evidence") or "",
        })
    return nodes


def _extract_first_value(ticket, keys):
    containers = [ticket.payload, ticket.workspace_context]
    containers.extend(event.payload for event in ticket.events.all())
    containers.extend(order.metadata for order in ticket.broker_orders.all())
    for container in containers:
        value = _find_key(container, keys)
        if value:
            return value
    return ""

def _find_key(value, keys):
    if isinstance(value, dict):
        for key in keys:
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
        for child in value.values():
            found = _find_key(child, keys)
            if found:
                return found
    if isinstance(value, list):
        for child in value:
            found = _find_key(child, keys)
            if found:
                return found
    return ""

def _occupancy_row(ticket, lineage_index):
    broker_orders = list(ticket.broker_orders.all())
    latest = _latest_status(ticket, broker_orders)
    unresolved_unknown = _is_unresolved_unknown(ticket, broker_orders)
    approved_not_submitted = ticket.current_state == "APPROVED" and not broker_orders
    reserved_notional = Decimal(str(ticket.estimated_notional or 0)) if ticket.current_state in UNRESOLVED_TICKET_STATES or unresolved_unknown else Decimal("0")
    return {
        "ticket_id": ticket.ticket_id,
        "account_id": ticket.account_id,
        "broker_connection_id": ticket.broker_connection.broker_id if ticket.broker_connection else "",
        "broker_account_id": ticket.broker_account.broker_account_id if ticket.broker_account else "",
        "symbol": ticket.symbol,
        "side": ticket.side,
        "quantity": float(ticket.quantity),
        "limit_price": float(ticket.limit_price or 0),
        "time_in_force": ticket.time_in_force,
        "ticket_state": ticket.current_state,
        "latest_status": latest,
        "broker_order_ids": [order.broker_order_id for order in broker_orders if order.broker_order_id],
        "approved_not_submitted": approved_not_submitted,
        "unresolved_unknown": unresolved_unknown,
        "replacement_lineage": _lineage_for_ticket(ticket, lineage_index),
        "reserved_notional_conservative": float(reserved_notional),
        "currency": ticket.currency,
        "created_at": ticket.created_at.isoformat(),
        "updated_at": ticket.updated_at.isoformat(),
    }


def _occupancy_groups(rows):
    grouped = defaultdict(list)
    for row in rows:
        grouped[(row["account_id"], row["broker_account_id"], row["symbol"], row["side"])].append(row)
    groups = []
    for (account_id, broker_account_id, symbol, side), group_rows in sorted(grouped.items()):
        groups.append({
            "account_id": account_id,
            "broker_account_id": broker_account_id,
            "symbol": symbol,
            "side": side,
            "reserved_notional_conservative": float(sum(Decimal(str(row["reserved_notional_conservative"])) for row in group_rows)),
            "unresolved_unknown_count": sum(1 for row in group_rows if row["unresolved_unknown"]),
            "approved_not_submitted_count": sum(1 for row in group_rows if row["approved_not_submitted"]),
            "rows": group_rows,
        })
    return groups


def _overlap_policy(rows, args):
    requested_symbol = str(args.get("symbol") or "").upper()
    requested_side = str(args.get("side") or "").lower()
    allow_conservative_exclusion = bool(args.get("allow_conservative_exclusion"))
    overlapping = [
        row
        for row in rows
        if row["ticket_state"] in UNRESOLVED_TICKET_STATES
        and (not requested_symbol or row["symbol"] == requested_symbol)
        and (not requested_side or row["side"] == requested_side)
    ]
    if not overlapping:
        return {"disposition": "clear", "reasons": [], "overlapping_ticket_ids": []}
    disposition = "conservative_exclusion" if allow_conservative_exclusion else "blocked"
    reason = "unresolved overlap requires conservative exclusion" if allow_conservative_exclusion else "unresolved overlap blocks new approval row"
    return {
        "disposition": disposition,
        "reasons": [reason],
        "overlapping_ticket_ids": [row["ticket_id"] for row in overlapping],
    }
from __future__ import annotations

import re
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from tradingcodex_service.application.audit import write_audit_event, write_audit_event_if_available
from tradingcodex_service.application.common import (
    _parse_datetime,
    safe_workspace_path,
    _validate_positive,
    now_iso,
    read_json,
    sanitize_id,
    stable_hash,
    write_json,
)
from tradingcodex_service.application.policy import evaluate_policy
from tradingcodex_service.application.portfolio import (
    DEFAULT_ACCOUNT_ID,
    DEFAULT_PORTFOLIO_ID,
    DEFAULT_STRATEGY_ID,
    portfolio_keys,
    submit_paper_order,
)
from tradingcodex_service.application.runtime import ensure_runtime_database, workspace_context_payload

APPROVAL_FILE_ROOTS = (Path("trading/approvals"),)
ORDER_TICKET_CREATOR_ROLE = "portfolio-manager"
ORDER_TICKET_STATES = {
    "DRAFT",
    "PRECHECKED",
    "READY_FOR_APPROVAL",
    "APPROVED",
    "RESERVED",
    "SUBMITTED",
    "ACKED",
    "PARTIALLY_FILLED",
    "FILLED",
    "REJECTED",
    "CANCELED",
    "EXPIRED",
    "FAILED",
    "NEEDS_REVIEW",
    "VOIDED",
}
ORDER_TICKET_TERMINAL_STATES = {"FILLED", "REJECTED", "CANCELED", "EXPIRED", "FAILED", "VOIDED"}
ORDER_TICKET_TRANSITIONS = {
    "DRAFT": {"PRECHECKED", "READY_FOR_APPROVAL", "NEEDS_REVIEW", "REJECTED", "CANCELED", "VOIDED"},
    "PRECHECKED": {"READY_FOR_APPROVAL", "NEEDS_REVIEW", "APPROVED", "REJECTED", "CANCELED", "VOIDED"},
    "READY_FOR_APPROVAL": {"APPROVED", "NEEDS_REVIEW", "REJECTED", "CANCELED", "VOIDED"},
    "APPROVED": {"RESERVED", "EXPIRED", "CANCELED", "NEEDS_REVIEW", "VOIDED"},
    "RESERVED": {"SUBMITTED", "FAILED"},
    "SUBMITTED": {"ACKED", "FILLED", "PARTIALLY_FILLED", "REJECTED", "FAILED", "NEEDS_REVIEW"},
    "ACKED": {"PARTIALLY_FILLED", "FILLED", "CANCELED", "REJECTED", "FAILED", "NEEDS_REVIEW"},
    "PARTIALLY_FILLED": {"FILLED", "CANCELED", "FAILED", "NEEDS_REVIEW"},
    "NEEDS_REVIEW": {"DRAFT", "PRECHECKED", "REJECTED", "CANCELED", "VOIDED"},
}
ORDER_RESOLUTION_ERROR_FIELD = "_order_resolution_error"
ORDER_TICKET_FREE_TEXT_FIELDS = ("thesis", "strategy", "rationale", "notes", "decision_summary", "source_artifact")


def order_ticket_free_text_from_args(args: dict[str, Any]) -> dict[str, str]:
    return {
        field: str(args[field])
        for field in ORDER_TICKET_FREE_TEXT_FIELDS
        if args.get(field) not in (None, "")
    }


def validate_order_ticket_payload(workspace_root: Path | str, args: dict[str, Any]) -> dict[str, Any]:
    order = resolve_order_ticket_payload(Path(workspace_root), args)
    reasons = _schema_reasons(order)
    conflict = order_ticket_payload_conflict(Path(workspace_root), order)
    if conflict:
        reasons.append(conflict)
    policy = evaluate_policy(workspace_root, {**args, "action": args.get("action") or "order_ticket.check", "order": order})
    all_reasons = list(dict.fromkeys(reasons + policy["reasons"]))
    result = {"valid": not all_reasons and policy["decision"] == "allow", "reasons": all_reasons, "policy": policy, "db_canonical": True, "workspace_context": workspace_context_payload(workspace_root)}
    return result


def validate_approval_receipt(workspace_root: Path | str, args: dict[str, Any]) -> dict[str, Any]:
    root = Path(workspace_root)
    order = resolve_order_ticket_payload(root, args)
    receipt = resolve_approval_receipt(root, args, order)
    reasons: list[str] = []
    for field in ["id", "order_ticket_id", "approved_by", "valid", "expires_at"]:
        if receipt.get(field) in (None, ""):
            reasons.append(f"missing {field}")
    if receipt.get("valid") is not True:
        reasons.append("approval_receipt.valid must be true")
    if order.get("id") and receipt.get("order_ticket_id") != order["id"]:
        reasons.append("approval_receipt.order_ticket_id does not match order ticket id")
    if order.get("created_by") and receipt.get("approved_by") == order["created_by"]:
        reasons.append("order creator cannot approve the same order")
    expires_at = _parse_datetime(receipt.get("expires_at"))
    if receipt.get("expires_at") and expires_at is None:
        reasons.append("approval_receipt.expires_at is not a valid date")
    if expires_at and expires_at <= datetime.now(timezone.utc):
        reasons.append("approval receipt is expired")
    valid_until = _parse_datetime(receipt.get("valid_until"))
    if receipt.get("valid_until") and valid_until is None:
        reasons.append("approval_receipt.valid_until is not a valid date")
    if valid_until and valid_until <= datetime.now(timezone.utc):
        reasons.append("approval valid_until is expired")
    if receipt.get("exact_order_hash") and stable_hash(order) != receipt.get("exact_order_hash"):
        reasons.append("approval exact_order_hash does not match order ticket payload")
    if receipt.get("broker_connection_id") and order.get("broker_connection_id") and receipt.get("broker_connection_id") != order.get("broker_connection_id"):
        reasons.append("approval broker_connection_id does not match order")
    if receipt.get("broker_account_id") and order.get("broker_account_id") and receipt.get("broker_account_id") != order.get("broker_account_id"):
        reasons.append("approval broker_account_id does not match order")
    max_notional = _number_or_none(receipt.get("max_notional"))
    order_notional = _number_or_none(order.get("estimated_notional_krw"))
    if max_notional is not None and order_notional is not None and order_notional > max_notional:
        reasons.append("order notional exceeds approval scope")
    max_price = _number_or_none(receipt.get("max_price"))
    order_price = _number_or_none(order.get("limit_price"))
    if max_price is not None and order_price is not None and order_price > max_price:
        reasons.append("order price exceeds approval scope")
    if receipt.get("approved_order_type") and order.get("order_type") and receipt.get("approved_order_type") != order.get("order_type"):
        reasons.append("order type differs from approval scope")
    if receipt.get("approved_time_in_force") and order.get("time_in_force") and receipt.get("approved_time_in_force") != order.get("time_in_force"):
        reasons.append("time in force differs from approval scope")
    return {"valid": not reasons, "reasons": reasons, "db_canonical": True, "workspace_context": workspace_context_payload(workspace_root)}


def _create_order_approval_receipt(workspace_root: Path | str, order: dict[str, Any], approved_by: str = "risk-manager", expires_hours: int = 24) -> dict[str, Any]:
    root = Path(workspace_root)
    validation = validate_order_ticket_payload(root, {"principal_id": approved_by, "order": order})
    if not validation["valid"]:
        rejected = {"status": "rejected", "order_ticket_id": order.get("id"), "reasons": validation["reasons"], "db_canonical": True, "workspace_context": workspace_context_payload(root)}
        write_rejected_order(root, order, validation["reasons"])
        write_audit_event(root, {"type": "approval.rejected", "payload": rejected}, principal_id=approved_by, source="service")
        return rejected
    ticket = ensure_order_ticket_for_order(root, order, approved_by)
    if ticket is not None:
        order = order_payload_from_ticket(ticket)
    approval_policy = evaluate_policy(root, {"principal_id": approved_by, "action": "approval_receipt.create", "order": order})
    if approval_policy["decision"] != "allow":
        rejected = {"status": "rejected", "order_ticket_id": order.get("id"), "reasons": approval_policy["reasons"], "db_canonical": True, "workspace_context": workspace_context_payload(root)}
        write_rejected_order(root, order, approval_policy["reasons"])
        write_audit_event(root, {"type": "approval.rejected", "payload": rejected}, principal_id=approved_by, source="service")
        return rejected
    if approved_by == order.get("created_by"):
        raise ValueError("order creator cannot approve the same order")
    created = datetime.now(timezone.utc)
    receipt = {
        "id": f"approval-{sanitize_id(order['id'])}-{created.strftime('%Y%m%dT%H%M%S%fZ')}",
        "approved_by": approved_by,
        "valid": True,
        "created_at": created.isoformat().replace("+00:00", "Z"),
        "expires_at": (created + timedelta(hours=expires_hours)).isoformat().replace("+00:00", "Z"),
        "exact_order_hash": stable_hash(order),
        "order_ticket_id": order.get("order_ticket_id") or order.get("id", ""),
        "broker_connection_id": order.get("broker_connection_id") or order.get("broker", ""),
        "broker_account_id": order.get("broker_account_id") or order.get("account_id", ""),
        "max_notional": order.get("estimated_notional_krw"),
        "max_price": order.get("limit_price"),
        "max_slippage_bps": order.get("max_slippage_bps", 0),
        "approved_order_type": order.get("order_type") or "limit",
        "approved_time_in_force": order.get("time_in_force") or "day",
        "valid_until": (created + timedelta(hours=expires_hours)).isoformat().replace("+00:00", "Z"),
        "quote_as_of_requirement": order.get("quote_as_of_requirement", ""),
        "policy_decision": validation["policy"],
    }
    receipt_validation = validate_approval_receipt(root, {"order": order, "approval_receipt": receipt})
    if not receipt_validation["valid"]:
        return {"status": "rejected", "order_ticket_id": order.get("id"), "reasons": receipt_validation["reasons"], "db_canonical": True, "workspace_context": workspace_context_payload(root)}
    persist_approval_receipt_if_available(root, receipt)
    if ticket is not None:
        try:
            if ticket.current_state in {"DRAFT", "PRECHECKED", "NEEDS_REVIEW"}:
                transition_order_ticket(ticket, "READY_FOR_APPROVAL", approved_by, {"approval_receipt_id": receipt["id"]})
            if ticket.current_state == "READY_FOR_APPROVAL":
                transition_order_ticket(ticket, "APPROVED", approved_by, {"approval_receipt_id": receipt["id"]})
        except ValueError:
            pass
    result = {
        "status": "approved",
        "order_ticket_id": order["id"],
        "approval_receipt": receipt,
        "db_canonical": True,
        "workspace_context": workspace_context_payload(root),
    }
    write_audit_event(root, {"type": "approval.accepted", "payload": result}, principal_id=approved_by, source="service")
    return result


def submit_approved_order(workspace_root: Path | str, args: dict[str, Any]) -> dict[str, Any]:
    root = Path(workspace_root)
    order = resolve_order_ticket_payload(root, args)
    receipt = resolve_approval_receipt(root, args, order)
    principal_id = args.get("principal_id") or "execution-operator"
    terminal_reasons = order_ticket_submit_block_reasons(root, args)
    order_reasons = _schema_reasons(order)
    order_validation = {"valid": not order_reasons, "reasons": order_reasons}
    receipt_validation = validate_approval_receipt(root, {"order": order, "approval_receipt": receipt})
    policy = evaluate_policy(root, {
        "principal_id": principal_id,
        "action": "mcp.tradingcodex.submit_approved_order",
        "order": order,
        "approval_receipt": receipt,
        "require_approval_check": True,
    })
    if terminal_reasons or not order_validation["valid"] or not receipt_validation["valid"] or policy["decision"] != "allow":
        rejected = {
            "status": "rejected",
            "order_ticket_id": order.get("id"),
            "reasons": terminal_reasons + order_validation["reasons"] + receipt_validation["reasons"] + policy["reasons"],
            "db_canonical": True,
            "workspace_context": workspace_context_payload(root),
        }
        write_audit_event(root, {"type": "submit_approved_order.rejected", "payload": rejected}, principal_id=principal_id, source="mcp")
        return rejected
    adapter_reasons = _adapter_preflight_reasons(root, order, args)
    if adapter_reasons:
        rejected = {
            "status": "rejected",
            "order_ticket_id": order.get("id"),
            "adapter": order.get("broker"),
            "reasons": adapter_reasons,
            "db_canonical": True,
            "workspace_context": workspace_context_payload(root),
        }
        reconciled = reconcile_existing_ticket_activity_before_reject(root, args, principal_id, rejected)
        if reconciled is not None:
            return reconciled
        write_audit_event(root, {"type": "submit_approved_order.adapter_preflight_rejected", "payload": rejected}, principal_id=principal_id, source="mcp")
        return rejected
    ensure_runtime_database(root)
    from apps.orders.services import finalize_execution_reservation, reserve_execution

    portfolio_id, account_id, strategy_id = portfolio_keys(order, root)
    reservation = reserve_execution(
        order=order,
        receipt=receipt,
        adapter=order.get("broker", ""),
        portfolio_id=portfolio_id,
        account_id=account_id,
        strategy_id=strategy_id,
        workspace_context=workspace_context_payload(root),
        principal_id=principal_id,
    )
    if not reservation.created:
        rejected = {
            "status": "rejected",
            "order_ticket_id": order.get("id"),
            "idempotency_key": reservation.idempotency_key,
            "reasons": [f"order already has an execution result: {reservation.execution.status}"],
            "db_canonical": True,
            "workspace_context": workspace_context_payload(root),
        }
        write_audit_event(root, {"type": "submit_approved_order.duplicate", "payload": rejected}, principal_id=principal_id, source="mcp")
        return rejected
    from tradingcodex_service.application.brokers import BrokerSubmissionUncertainError

    try:
        adapter_result = submit_with_adapter(root, order)
    except BrokerSubmissionUncertainError as exc:
        uncertain_result = {
            "adapter": order.get("broker"),
            "broker_order_id": exc.broker_order_id or order.get("client_order_id") or _deterministic_client_order_id(order),
            "client_order_id": order.get("client_order_id") or _deterministic_client_order_id(order),
            "status": "unknown",
            "submitted_at": now_iso(),
            "needs_review": True,
            "raw_status": exc.status_payload,
            "reason": str(exc),
        }
        needs_review = {
            "status": "needs_review",
            "order_ticket_id": order.get("id"),
            "adapter": order.get("broker"),
            "idempotency_key": reservation.idempotency_key,
            "reasons": [f"adapter reached uncertain submit boundary: {exc}"],
            "result": uncertain_result,
            "db_canonical": True,
            "workspace_context": workspace_context_payload(root),
        }
        finalize_execution_reservation(reservation.execution, needs_review)
        _record_ticket_submit_result(root, order, receipt, uncertain_result, principal_id)
        try:
            ticket = ensure_order_ticket_for_order(root, order, principal_id)
            if ticket is not None and ticket.current_state != "NEEDS_REVIEW":
                transition_order_ticket(ticket, "NEEDS_REVIEW", principal_id, needs_review)
        except Exception:
            pass
        write_audit_event(root, {"type": "submit_approved_order.needs_review", "payload": needs_review}, principal_id=principal_id, source="mcp")
        return needs_review
    except Exception as exc:
        rejected = {
            "status": "rejected",
            "order_ticket_id": order.get("id"),
            "adapter": order.get("broker"),
            "reasons": [f"adapter error: {exc}"],
            "db_canonical": True,
            "workspace_context": workspace_context_payload(root),
        }
        finalize_execution_reservation(reservation.execution, rejected)
        write_audit_event(root, {"type": "submit_approved_order.adapter_error", "payload": rejected}, principal_id=principal_id, source="mcp")
        return rejected
    accepted = {"status": "accepted", "order_ticket_id": order["id"], "adapter": order["broker"], "idempotency_key": reservation.idempotency_key, "result": adapter_result, "db_canonical": True, "workspace_context": workspace_context_payload(root)}
    persist_approval_receipt_if_available(root, receipt)
    finalize_execution_reservation(reservation.execution, accepted)
    _record_ticket_submit_result(root, order, receipt, adapter_result, principal_id)
    write_audit_event(root, {"type": "submit_approved_order.accepted", "payload": accepted}, principal_id=principal_id, source="mcp")
    return accepted


def create_order_ticket(workspace_root: Path | str, args: dict[str, Any]) -> dict[str, Any]:
    root = Path(workspace_root)
    ensure_runtime_database(root)
    from apps.orders.models import OrderTicket
    from tradingcodex_service.application.brokers import ensure_paper_broker_connection

    principal_id = str(args.get("principal_id") or args.get("created_by") or ORDER_TICKET_CREATOR_ROLE)
    if principal_id != ORDER_TICKET_CREATOR_ROLE:
        raise PermissionError(f"only {ORDER_TICKET_CREATOR_ROLE} can create order tickets")

    fields = normalize_order_ticket_fields(root, args)
    fields["created_by"] = principal_id
    pre_existing = OrderTicket.objects.filter(ticket_id=fields.get("ticket_id") or fields.get("id") or "").first()
    if pre_existing is not None:
        stored_order = (pre_existing.payload or {}).get("order") if isinstance(pre_existing.payload, dict) else {}
        if isinstance(stored_order, dict) and stored_order.get("created_at"):
            fields["created_at"] = str(stored_order["created_at"])
    connection = _resolve_ticket_broker_connection(root, fields)
    broker_account = _resolve_ticket_broker_account(connection, fields)
    portfolio_id, account_id, strategy_id = portfolio_keys(
        {
            "portfolio_id": fields.get("portfolio_id"),
            "account_id": fields.get("account_id"),
            "strategy_id": fields.get("strategy_id"),
        },
        root,
    )
    if connection is None:
        connection = ensure_paper_broker_connection(root)
    ticket_id = fields.get("ticket_id") or fields.get("id") or f"ticket-{sanitize_id(fields['symbol'])}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')}"
    estimated_notional = Decimal(str(fields["quantity"])) * Decimal(str(fields["limit_price"]))
    order_payload = canonical_order_from_fields(
        {
            **fields,
            "id": ticket_id,
            "portfolio_id": portfolio_id,
            "account_id": account_id,
            "strategy_id": strategy_id,
            "broker": connection.broker_id,
            "broker_connection_id": connection.broker_id,
            "broker_account_id": broker_account.broker_account_id if broker_account else "",
            "estimated_notional": str(estimated_notional),
            "estimated_notional_krw": str(estimated_notional),
        }
    )
    payload_hash = stable_hash(order_payload)
    free_text = order_ticket_free_text_from_args(args)
    existing = OrderTicket.objects.filter(ticket_id=ticket_id).first()
    if existing is not None and (existing.portfolio_id, existing.account_id, existing.strategy_id) != (portfolio_id, account_id, strategy_id):
        raise ValueError("order ticket id already exists for another active profile")
    if existing is not None and existing.current_state not in {"DRAFT", "PRECHECKED", "NEEDS_REVIEW"} and existing.payload_hash != payload_hash:
        raise ValueError("order ticket cannot be mutated after approval or submission")
    ticket, created = OrderTicket.objects.update_or_create(
        ticket_id=ticket_id,
        defaults={
            "source": fields.get("source") or "web",
            "portfolio_id": portfolio_id,
            "account_id": account_id,
            "strategy_id": strategy_id,
            "broker_connection": connection,
            "broker_account": broker_account,
            "instrument_id": fields.get("instrument_id") or fields["symbol"],
            "symbol": fields["symbol"],
            "side": fields["side"],
            "quantity": fields["quantity"],
            "order_type": fields.get("order_type") or "limit",
            "limit_price": fields["limit_price"],
            "stop_price": fields.get("stop_price"),
            "time_in_force": fields.get("time_in_force") or "day",
            "estimated_notional": estimated_notional,
            "currency": fields.get("currency") or "KRW",
            "status": existing.status if existing else "DRAFT",
            "current_state": existing.current_state if existing else "DRAFT",
            "payload_hash": payload_hash,
            "user_visible_summary": fields.get("user_visible_summary") or _ticket_summary(order_payload),
            "created_by": principal_id,
            "natural_language_source": fields.get("natural_language") or "",
            "workspace_context": workspace_context_payload(root),
            "payload": {
                "order": order_payload,
                "canonical_order": order_payload.get("canonical_order", {}),
                "free_text": free_text,
                "raw": args,
            },
        },
    )
    record_order_event(ticket, "created" if created else "updated", args.get("principal_id") or ticket.created_by, {"payload_hash": payload_hash})
    result = serialize_order_ticket(ticket)
    write_audit_event(root, {"type": "order_ticket.created" if created else "order_ticket.updated", "payload": result}, args.get("principal_id") or ticket.created_by, "service")
    return {"status": "created" if created else "updated", "ticket": result, "db_canonical": True, "workspace_context": workspace_context_payload(root)}


def run_order_checks(workspace_root: Path | str, args: dict[str, Any]) -> dict[str, Any]:
    root = Path(workspace_root)
    ensure_runtime_database(root)
    from apps.orders.models import OrderCheckRun
    from tradingcodex_service.application.brokers import adapter_for_connection

    ticket = get_order_ticket_model(root, args)
    order = order_payload_from_ticket(ticket)
    principal_id = str(args.get("principal_id") or "portfolio-manager")
    checks: list[dict[str, Any]] = []

    schema_reasons = _schema_reasons(order)
    checks.append(_check_result("schema", not schema_reasons, schema_reasons))

    policy = evaluate_policy(root, {**args, "principal_id": principal_id, "action": "order_ticket.check", "order": order})
    policy_reasons = [reason for reason in policy["reasons"] if "symbol is restricted" not in reason]
    restricted_reasons = [reason for reason in policy["reasons"] if "symbol is restricted" in reason]
    checks.append(_check_result("policy", not policy_reasons, policy_reasons, {"policy": policy}))
    checks.append(_check_result("restricted", not restricted_reasons, restricted_reasons))

    adapter = adapter_for_connection(ticket.broker_connection, root) if ticket.broker_connection else None
    if adapter is not None:
        adapter_validation = adapter.validate_order_translation(order)
        adapter_reasons = list(adapter_validation.reasons)
    else:
        adapter_reasons = ["broker adapter is not configured"]
    cash_position_reasons = [reason for reason in adapter_reasons if "cash" in reason or "position" in reason]
    broker_reasons = [reason for reason in adapter_reasons if reason not in cash_position_reasons]
    checks.append(_check_result("cash" if order.get("side") == "buy" else "position", not cash_position_reasons, cash_position_reasons))
    checks.append(_check_result("broker_validate", not broker_reasons, broker_reasons))
    checks.append(_check_result("market", True, ["market quote freshness is not configured for paper broker"], decision="warn"))
    checks.append(_check_result("risk", not [item for item in checks if item["decision"] == "fail"], [], {"review": "risk-manager review required before approval"}))

    OrderCheckRun.objects.filter(ticket=ticket).delete()
    for check in checks:
        OrderCheckRun.objects.create(
            ticket=ticket,
            check_type=check["check_type"],
            decision=check["decision"],
            reasons=check["reasons"],
            quote_as_of=check.get("quote_as_of", ""),
            source_snapshot_ref=check.get("source_snapshot_ref", ""),
            payload=check.get("payload", {}),
        )
    failed = [check for check in checks if check["decision"] == "fail"]
    next_state = "NEEDS_REVIEW" if failed else "READY_FOR_APPROVAL"
    transition_order_ticket(ticket, next_state, principal_id, {"checks": checks})
    result = {
        "status": "checked",
        "ticket_id": ticket.ticket_id,
        "approval_ready": not failed,
        "checks": checks,
        "db_canonical": True,
        "workspace_context": workspace_context_payload(root),
    }
    write_audit_event(root, {"type": "order_ticket.checked", "payload": result}, principal_id, "service")
    return result


def request_order_approval(workspace_root: Path | str, args: dict[str, Any]) -> dict[str, Any]:
    root = Path(workspace_root)
    ticket = get_order_ticket_model(root, args)
    principal_id = str(args.get("principal_id") or args.get("approved_by") or "risk-manager")
    latest_checks = list(ticket.check_runs.all())
    if not latest_checks:
        checks = run_order_checks(root, {"ticket_id": ticket.ticket_id, "principal_id": principal_id})
        if not checks["approval_ready"]:
            return {"status": "rejected", "ticket_id": ticket.ticket_id, "reasons": ["order checks failed"], "checks": checks["checks"], "db_canonical": True, "workspace_context": workspace_context_payload(root)}
    elif any(check.decision == "fail" for check in latest_checks):
        return {"status": "rejected", "ticket_id": ticket.ticket_id, "reasons": ["fail check prevents approval"], "db_canonical": True, "workspace_context": workspace_context_payload(root)}
    order = order_payload_from_ticket(ticket)
    result = _create_order_approval_receipt(root, order, principal_id, int(args.get("expires_hours") or 24))
    if result.get("status") == "approved":
        ticket.refresh_from_db()
        if ticket.current_state != "APPROVED":
            transition_order_ticket(ticket, "APPROVED", principal_id, result)
    return {**result, "ticket_id": ticket.ticket_id}


def list_order_tickets(workspace_root: Path | str, args: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_runtime_database(workspace_root)
    from apps.orders.models import OrderTicket

    args = args or {}
    limit = max(1, min(int(args.get("limit") or 30), 200))
    portfolio_id, account_id, strategy_id = portfolio_keys(args, workspace_root)
    queryset = OrderTicket.objects.select_related("broker_connection", "broker_account").prefetch_related("check_runs", "events")
    queryset = queryset.filter(portfolio_id=portfolio_id, account_id=account_id, strategy_id=strategy_id)
    state = args.get("state") or args.get("status")
    if state:
        queryset = queryset.filter(current_state=state)
    return {
        "tickets": [serialize_order_ticket(ticket) for ticket in queryset[:limit]],
        "db_canonical": True,
        "portfolio_id": portfolio_id,
        "account_id": account_id,
        "strategy_id": strategy_id,
        "workspace_context": workspace_context_payload(workspace_root),
    }


def get_order_ticket(workspace_root: Path | str, args: dict[str, Any]) -> dict[str, Any]:
    return {
        "ticket": serialize_order_ticket(get_order_ticket_model(workspace_root, args), include_related=True),
        "db_canonical": True,
        "workspace_context": workspace_context_payload(workspace_root),
    }


def refresh_broker_order_status(workspace_root: Path | str, args: dict[str, Any]) -> dict[str, Any]:
    root = Path(workspace_root)
    ensure_runtime_database(root)
    from tradingcodex_service.application.brokers import adapter_for_connection, broker_connection_provider_review_reasons

    principal_id = str(args.get("principal_id") or "execution-operator")
    ticket = get_order_ticket_model(root, args) if args.get("ticket_id") or args.get("order_ticket_id") else None
    broker_order_id = str(args.get("broker_order_id") or "")
    broker_order = None
    if ticket is not None and broker_order_id:
        broker_order = ticket.broker_orders.filter(broker_order_id=broker_order_id).first()
    elif ticket is not None:
        broker_order = ticket.broker_orders.order_by("-submitted_at", "-id").first()
    elif broker_order_id:
        broker_order = _find_broker_order_for_active_profile(root, args, broker_order_id)
        ticket = broker_order.ticket if broker_order is not None else None
    if ticket is None:
        raise ValueError("ticket_id or known broker_order_id is required")
    if broker_order is None:
        result = {
            "status": "no_broker_order",
            "ticket_id": ticket.ticket_id,
            "reasons": ["ticket has no broker order recorded yet"],
            "ticket": serialize_order_ticket(ticket, include_related=True),
            "db_canonical": True,
            "workspace_context": workspace_context_payload(root),
        }
        write_audit_event(root, {"type": "broker_order_status.not_found", "payload": result}, principal_id, "service")
        return result
    if ticket.broker_connection is None:
        result = {
            "status": "local-only",
            "ticket_id": ticket.ticket_id,
            "broker_order_id": broker_order.broker_order_id,
            "broker_status": broker_order.broker_status,
            "reasons": ["ticket has no broker connection"],
            "ticket": serialize_order_ticket(ticket, include_related=True),
            "db_canonical": True,
            "workspace_context": workspace_context_payload(root),
        }
        write_audit_event(root, {"type": "broker_order_status.local_only", "payload": result}, principal_id, "service")
        return result

    source_reasons = broker_connection_provider_review_reasons(ticket.broker_connection, root)
    if source_reasons:
        result = {
            "status": "blocked",
            "ticket_id": ticket.ticket_id,
            "broker_order_id": broker_order.broker_order_id,
            "reasons": source_reasons,
            "ticket": serialize_order_ticket(ticket, include_related=True),
            "db_canonical": True,
            "workspace_context": workspace_context_payload(root),
        }
        write_audit_event(root, {"type": "broker_order_status.blocked", "payload": result}, principal_id, "service")
        return result

    adapter_status = adapter_for_connection(ticket.broker_connection, root).get_order_status(broker_order.broker_order_id)
    broker_status = str(adapter_status.get("status") or "unknown").lower()
    broker_order.broker_status = broker_status
    broker_order.last_seen_at = datetime.now(timezone.utc)
    broker_order.raw_status_payload_hash = stable_hash(adapter_status)
    metadata = dict(broker_order.metadata or {})
    metadata["last_status_refresh"] = adapter_status
    broker_order.metadata = metadata
    broker_order.save(update_fields=["broker_status", "last_seen_at", "raw_status_payload_hash", "metadata"])
    record_order_event(ticket, "status_refreshed", principal_id, {"broker_order_id": broker_order.broker_order_id, "broker_status": broker_status, "adapter_status": adapter_status})
    state_target = {
        "filled": "FILLED",
        "partially_filled": "PARTIALLY_FILLED",
        "partial": "PARTIALLY_FILLED",
        "canceled": "CANCELED",
        "cancelled": "CANCELED",
        "rejected": "REJECTED",
        "expired": "EXPIRED",
        "failed": "FAILED",
    }.get(broker_status)
    if state_target and ticket.current_state != state_target:
        try:
            transition_order_ticket(ticket, state_target, principal_id, {"broker_order_id": broker_order.broker_order_id, "broker_status": broker_status})
        except ValueError:
            pass
    if adapter_status.get("filled_quantity") and adapter_status.get("average_price"):
        fill_payload = {
            **adapter_status,
            "broker_order_id": broker_order.broker_order_id,
            "submitted_at": broker_order.submitted_at.isoformat() if broker_order.submitted_at else now_iso(),
        }
        _record_ticket_submit_result(root, order_payload_from_ticket(ticket), {"approved_by": "status-refresh"}, fill_payload, principal_id)
        try:
            from tradingcodex_service.application.brokers import sync_broker_account

            sync_broker_account(root, {"broker_id": ticket.broker_connection.broker_id, "principal_id": principal_id})
        except Exception:
            pass
    ticket.refresh_from_db()
    result = {
        "status": "refreshed",
        "ticket_id": ticket.ticket_id,
        "broker_order_id": broker_order.broker_order_id,
        "broker_status": broker_status,
        "adapter_status": adapter_status,
        "ticket": serialize_order_ticket(ticket, include_related=True),
        "db_canonical": True,
        "workspace_context": workspace_context_payload(root),
    }
    write_audit_event(root, {"type": "broker_order_status.refreshed", "payload": result}, principal_id, "service")
    return result


def get_order_status(workspace_root: Path | str, args: dict[str, Any]) -> dict[str, Any]:
    root = Path(workspace_root)
    ensure_runtime_database(root)
    order_id = str(args.get("ticket_id") or args.get("order_ticket_id") or args.get("order_id") or args.get("broker_order_id") or "")
    if not order_id:
        raise ValueError("order_id, ticket_id, or broker_order_id is required")
    try:
        ticket = get_order_ticket_model(root, {**args, "ticket_id": order_id})
        return {
            "status": ticket.current_state,
            "ticket_id": ticket.ticket_id,
            "ticket": serialize_order_ticket(ticket, include_related=True),
            "db_canonical": True,
            "workspace_context": workspace_context_payload(root),
        }
    except ValueError:
        broker_order = _find_broker_order_for_active_profile(root, args, order_id)
        if broker_order is None:
            return {
                "status": "unknown",
                "order_id": order_id,
                "reasons": ["no local order ticket or broker order matched"],
                "db_canonical": True,
                "workspace_context": workspace_context_payload(root),
            }
        return {
            "status": broker_order.broker_status,
            "ticket_id": broker_order.ticket.ticket_id,
            "broker_order_id": broker_order.broker_order_id,
            "ticket": serialize_order_ticket(broker_order.ticket, include_related=True),
            "db_canonical": True,
            "workspace_context": workspace_context_payload(root),
        }


def void_approved_only_ticket(workspace_root: Path | str, ticket: Any, principal_id: str, args: dict[str, Any]) -> dict[str, Any]:
    root = Path(workspace_root)
    if ticket.broker_orders.exists() or ticket.fills.exists():
        raise ValueError("local void is only allowed before broker orders or fills exist")
    if ticket.current_state not in {"READY_FOR_APPROVAL", "APPROVED", "NEEDS_REVIEW"}:
        raise ValueError(f"local void is not allowed from {ticket.current_state}")
    payload = {
        "mode": "local_approved_only_void",
        "void_reason": str(args.get("void_reason") or args.get("reason") or "approved-only ticket voided locally"),
        "superseded_by_ticket_id": str(args.get("superseded_by_ticket_id") or ""),
    }
    invalidated = invalidate_approval_receipts_for_ticket(root, ticket.ticket_id, principal_id, payload)
    transition_order_ticket(ticket, "VOIDED", principal_id, payload)
    ticket.refresh_from_db()
    result = {
        "status": "voided",
        "ticket_id": ticket.ticket_id,
        "invalidated_approval_receipts": invalidated,
        "ticket": serialize_order_ticket(ticket, include_related=True),
        "db_canonical": True,
        "workspace_context": workspace_context_payload(root),
    }
    write_audit_event(root, {"type": "order_ticket.void.accepted", "payload": result}, principal_id, "service")
    return result


def cancel_approved_order(workspace_root: Path | str, args: dict[str, Any]) -> dict[str, Any]:
    root = Path(workspace_root)
    ensure_runtime_database(root)
    principal_id = str(args.get("principal_id") or "execution-operator")
    order_id = str(args.get("broker_order_id") or args.get("order_id") or "")
    ticket = get_order_ticket_model(root, args) if args.get("ticket_id") or args.get("order_ticket_id") else None
    broker_order = None
    if ticket is not None and order_id:
        broker_order = ticket.broker_orders.filter(broker_order_id=order_id).first()
    elif ticket is not None:
        broker_order = ticket.broker_orders.order_by("-submitted_at", "-id").first()
    elif order_id:
        try:
            ticket = get_order_ticket_model(root, {**args, "ticket_id": order_id})
            broker_order = ticket.broker_orders.order_by("-submitted_at", "-id").first()
        except ValueError:
            broker_order = _find_broker_order_for_active_profile(root, args, order_id)
            ticket = broker_order.ticket if broker_order is not None else None
    if ticket is None:
        raise ValueError("ticket_id or known broker_order_id is required")
    if broker_order is None and not ticket.fills.exists() and ticket.current_state in {"READY_FOR_APPROVAL", "APPROVED", "NEEDS_REVIEW"}:
        try:
            return void_approved_only_ticket(root, ticket, principal_id, args)
        except ValueError as exc:
            result = {
                "status": "not_cancelable",
                "ticket_id": ticket.ticket_id,
                "reasons": [str(exc)],
                "ticket": serialize_order_ticket(ticket, include_related=True),
                "db_canonical": True,
                "workspace_context": workspace_context_payload(root),
            }
            write_audit_event(root, {"type": "order_ticket.void.rejected", "payload": result}, principal_id, "service")
            return result
    if ticket.current_state in {"FILLED", "REJECTED", "CANCELED", "EXPIRED", "FAILED"}:
        result = {
            "status": "not_cancelable",
            "ticket_id": ticket.ticket_id,
            "reasons": [f"order ticket is already {ticket.current_state}"],
            "ticket": serialize_order_ticket(ticket, include_related=True),
            "db_canonical": True,
            "workspace_context": workspace_context_payload(root),
        }
        write_audit_event(root, {"type": "order_ticket.cancel.rejected", "payload": result}, principal_id, "service")
        return result
    if "CANCELED" not in ORDER_TICKET_TRANSITIONS.get(ticket.current_state or "DRAFT", set()):
        result = {
            "status": "not_cancelable",
            "ticket_id": ticket.ticket_id,
            "reasons": [f"invalid order ticket transition: {ticket.current_state or 'DRAFT'} -> CANCELED"],
            "ticket": serialize_order_ticket(ticket, include_related=True),
            "db_canonical": True,
            "workspace_context": workspace_context_payload(root),
        }
        write_audit_event(root, {"type": "order_ticket.cancel.rejected", "payload": result}, principal_id, "service")
        return result
    cancel_payload: dict[str, Any] = {"broker_order_id": broker_order.broker_order_id if broker_order else "", "mode": "local_non_live"}
    if ticket.broker_connection is not None:
        connection = ticket.broker_connection
        metadata = connection.metadata if isinstance(connection.metadata, dict) else {}
        profile = metadata.get("capability_profile") if isinstance(metadata.get("capability_profile"), dict) else {}
        if profile.get("execution_posture") == "live_broker":
            reasons: list[str] = []
            from tradingcodex_service.application.brokers import broker_connection_provider_review_reasons

            reasons.extend(broker_connection_provider_review_reasons(connection, root))
            if broker_order is None:
                reasons.append("live cancel requires a known broker_order_id")
            if connection.status != "trading_enabled":
                reasons.append(f"broker connection is not trading_enabled: {connection.broker_id}")
            if "order.cancel.live" not in set(connection.enabled_trade_scopes or []):
                reasons.append(f"broker connection lacks order.cancel.live scope: {connection.broker_id}")
            if reasons:
                result = {
                    "status": "not_cancelable",
                    "ticket_id": ticket.ticket_id,
                    "reasons": reasons,
                    "ticket": serialize_order_ticket(ticket, include_related=True),
                    "db_canonical": True,
                    "workspace_context": workspace_context_payload(root),
                }
                write_audit_event(root, {"type": "order_ticket.cancel.rejected", "payload": result}, principal_id, "service")
                return result
            from tradingcodex_service.application.brokers import adapter_for_connection

            adapter_status = adapter_for_connection(connection, root).cancel_order(broker_order.broker_order_id)
            broker_status = str(adapter_status.get("status") or "canceled").lower()
            metadata = dict(broker_order.metadata or {})
            metadata["provider_cancel"] = {
                "canceled_by": principal_id,
                "canceled_at": now_iso(),
                "adapter_status": adapter_status,
            }
            broker_order.broker_status = broker_status
            broker_order.last_seen_at = datetime.now(timezone.utc)
            broker_order.raw_status_payload_hash = stable_hash(adapter_status)
            broker_order.metadata = metadata
            broker_order.save(update_fields=["broker_status", "last_seen_at", "raw_status_payload_hash", "metadata"])
            cancel_payload = {
                "broker_order_id": broker_order.broker_order_id,
                "mode": "provider_live",
                "broker_status": broker_status,
                "adapter_status": adapter_status,
            }
    if broker_order is not None:
        if cancel_payload.get("mode") != "provider_live":
            metadata = dict(broker_order.metadata or {})
            metadata["local_cancel"] = {"canceled_by": principal_id, "canceled_at": now_iso()}
            broker_order.broker_status = "canceled"
            broker_order.last_seen_at = datetime.now(timezone.utc)
            broker_order.raw_status_payload_hash = stable_hash(metadata)
            broker_order.metadata = metadata
            broker_order.save(update_fields=["broker_status", "last_seen_at", "raw_status_payload_hash", "metadata"])
    try:
        transition_order_ticket(ticket, "CANCELED", principal_id, cancel_payload)
    except ValueError as exc:
        result = {
            "status": "not_cancelable",
            "ticket_id": ticket.ticket_id,
            "reasons": [str(exc)],
            "ticket": serialize_order_ticket(ticket, include_related=True),
            "db_canonical": True,
            "workspace_context": workspace_context_payload(root),
        }
        write_audit_event(root, {"type": "order_ticket.cancel.rejected", "payload": result}, principal_id, "service")
        return result
    ticket.refresh_from_db()
    result = {
        "status": "canceled",
        "ticket_id": ticket.ticket_id,
        "broker_order_id": broker_order.broker_order_id if broker_order else "",
        "broker_status": broker_order.broker_status if broker_order else "local_only",
        "ticket": serialize_order_ticket(ticket, include_related=True),
        "db_canonical": True,
        "workspace_context": workspace_context_payload(root),
    }
    write_audit_event(root, {"type": "order_ticket.cancel.accepted", "payload": result}, principal_id, "service")
    return result


def get_order_ticket_model(workspace_root: Path | str, args: dict[str, Any]) -> Any:
    ensure_runtime_database(workspace_root)
    from apps.orders.models import OrderTicket

    ticket_id = args.get("ticket_id") or args.get("order_ticket_id") or args.get("order_id")
    if not ticket_id:
        raise ValueError("ticket_id is required")
    portfolio_id, account_id, strategy_id = portfolio_keys(args, workspace_root)
    ticket = (
        OrderTicket.objects.select_related("broker_connection", "broker_account")
        .prefetch_related("check_runs", "events", "fills", "broker_orders")
        .filter(
            ticket_id=ticket_id,
            portfolio_id=portfolio_id,
            account_id=account_id,
            strategy_id=strategy_id,
        )
        .first()
    )
    if ticket is None:
        raise ValueError(f"unknown order ticket for active profile: {ticket_id}")
    return ticket


def order_ticket_submit_block_reasons(workspace_root: Path | str, args: dict[str, Any]) -> list[str]:
    try:
        ticket = get_order_ticket_model(workspace_root, args)
    except ValueError:
        return []
    state = ticket.current_state or "DRAFT"
    if state in ORDER_TICKET_TERMINAL_STATES and state != "FILLED":
        return [f"order ticket is {state.lower()}"]
    return []


def _find_broker_order_for_active_profile(workspace_root: Path | str, args: dict[str, Any], broker_order_id: str) -> Any:
    ensure_runtime_database(workspace_root)
    from apps.orders.models import BrokerOrder

    portfolio_id, account_id, strategy_id = portfolio_keys(args, workspace_root)
    return (
        BrokerOrder.objects.select_related("ticket__broker_connection", "ticket__broker_account")
        .filter(
            broker_order_id=broker_order_id,
            ticket__portfolio_id=portfolio_id,
            ticket__account_id=account_id,
            ticket__strategy_id=strategy_id,
        )
        .first()
    )


def transition_order_ticket(ticket: Any, target_state: str, actor: str, payload: dict[str, Any] | None = None) -> None:
    if target_state not in ORDER_TICKET_STATES:
        raise ValueError(f"unknown order ticket state: {target_state}")
    current = ticket.current_state or "DRAFT"
    if target_state != current and target_state not in ORDER_TICKET_TRANSITIONS.get(current, set()):
        raise ValueError(f"invalid order ticket transition: {current} -> {target_state}")
    ticket.current_state = target_state
    ticket.status = target_state
    ticket.save(update_fields=["current_state", "status", "updated_at"])
    record_order_event(ticket, target_state.lower(), actor, payload or {})
    ticket_context = ticket.workspace_context if isinstance(ticket.workspace_context, dict) else {}
    workspace_root = ticket_context.get("path") or None
    write_audit_event_if_available(workspace_root, actor, "service", {"type": "order_ticket.transition", "payload": {"ticket_id": ticket.ticket_id, "order_ticket_id": ticket.ticket_id, "from": current, "to": target_state, **(payload or {})}})


def record_order_event(ticket: Any, event_type: str, actor: str, payload: dict[str, Any] | None = None) -> Any:
    from apps.orders.models import OrderEvent

    payload = payload or {}
    return OrderEvent.objects.create(
        ticket=ticket,
        event_type=event_type,
        actor=actor,
        payload=payload,
        payload_hash=stable_hash(payload),
    )


def invalidate_approval_receipts_for_ticket(root: Path, ticket_id: str, actor: str, payload: dict[str, Any]) -> int:
    ensure_runtime_database(root)
    from apps.orders.models import ApprovalReceipt

    count = 0
    for receipt in ApprovalReceipt.objects.filter(order_ticket_id=ticket_id, valid=True).order_by("created_at", "id"):
        stored_payload = dict(receipt.payload or {})
        stored_payload["valid"] = False
        stored_payload["voided_at"] = now_iso()
        stored_payload["voided_by"] = actor
        stored_payload["void_payload"] = payload
        receipt.valid = False
        receipt.payload = stored_payload
        receipt.save(update_fields=["valid", "payload"])
        count += 1
    return count


def order_payload_from_ticket(ticket: Any) -> dict[str, Any]:
    stored_payload = ticket.payload if isinstance(ticket.payload, dict) else {}
    payload = dict(stored_payload.get("order") or {})
    if not payload:
        payload = dict(stored_payload.get("canonical_order") or {})
    canonical_order = stored_payload.get("canonical_order")
    if payload:
        if canonical_order:
            payload["canonical_order"] = canonical_order
        payload.update(
            {
                "id": ticket.ticket_id,
                "order_ticket_id": ticket.ticket_id,
                "symbol": ticket.symbol,
                "side": ticket.side,
                "quantity": float(ticket.quantity),
                "limit_price": float(ticket.limit_price or 0),
                "currency": ticket.currency,
                "broker": ticket.broker_connection.broker_id if ticket.broker_connection else "paper-trading",
                "estimated_notional_krw": float(ticket.estimated_notional or 0),
                "created_by": ticket.created_by,
                "portfolio_id": ticket.portfolio_id,
                "account_id": ticket.account_id,
                "strategy_id": ticket.strategy_id,
                "broker_connection_id": ticket.broker_connection.broker_id if ticket.broker_connection else "",
                "broker_account_id": ticket.broker_account.broker_account_id if ticket.broker_account else "",
                "order_type": ticket.order_type,
                "time_in_force": ticket.time_in_force,
            }
        )
        payload.setdefault("created_at", ticket.created_at.isoformat())
        return payload
    return {
        "id": ticket.ticket_id,
        "order_ticket_id": ticket.ticket_id,
        "symbol": ticket.symbol,
        "side": ticket.side,
        "quantity": float(ticket.quantity),
        "limit_price": float(ticket.limit_price or 0),
        "currency": ticket.currency,
        "broker": ticket.broker_connection.broker_id if ticket.broker_connection else "paper-trading",
        "estimated_notional_krw": float(ticket.estimated_notional or 0),
        "created_by": ticket.created_by,
        "created_at": ticket.created_at.isoformat(),
        "portfolio_id": ticket.portfolio_id,
        "account_id": ticket.account_id,
        "strategy_id": ticket.strategy_id,
        "broker_connection_id": ticket.broker_connection.broker_id if ticket.broker_connection else "",
        "broker_account_id": ticket.broker_account.broker_account_id if ticket.broker_account else "",
        "order_type": ticket.order_type,
        "time_in_force": ticket.time_in_force,
    }


def ensure_order_ticket_for_order(root: Path, order: dict[str, Any], actor: str = "service") -> Any | None:
    try:
        ensure_runtime_database(root)
        from apps.orders.models import OrderTicket

        ticket_id = str(order.get("order_ticket_id") or order.get("id") or "")
        if not ticket_id:
            return None
        ticket = OrderTicket.objects.select_related("broker_connection", "broker_account").filter(ticket_id=ticket_id).first()
        if ticket is not None:
            return ticket
        result = create_order_ticket(root, {**order, "ticket_id": ticket_id, "source": order.get("source") or "api", "principal_id": actor})
        return OrderTicket.objects.select_related("broker_connection", "broker_account").get(ticket_id=result["ticket"]["ticket_id"])
    except Exception:
        return None


def serialize_order_ticket(ticket: Any, include_related: bool = False) -> dict[str, Any]:
    stored_payload = ticket.payload if isinstance(ticket.payload, dict) else {}
    free_text = stored_payload.get("free_text") if isinstance(stored_payload.get("free_text"), dict) else {}
    record = {
        "ticket_id": ticket.ticket_id,
        "source": ticket.source,
        "portfolio_id": ticket.portfolio_id,
        "account_id": ticket.account_id,
        "strategy_id": ticket.strategy_id,
        "broker_connection_id": ticket.broker_connection.broker_id if ticket.broker_connection else "",
        "broker_account_id": ticket.broker_account.broker_account_id if ticket.broker_account else "",
        "symbol": ticket.symbol,
        "side": ticket.side,
        "quantity": float(ticket.quantity),
        "order_type": ticket.order_type,
        "limit_price": float(ticket.limit_price or 0),
        "stop_price": float(ticket.stop_price or 0) if ticket.stop_price is not None else None,
        "time_in_force": ticket.time_in_force,
        "estimated_notional": float(ticket.estimated_notional or 0),
        "currency": ticket.currency,
        "status": ticket.status,
        "current_state": ticket.current_state,
        "payload_hash": ticket.payload_hash,
        "user_visible_summary": ticket.user_visible_summary,
        "created_by": ticket.created_by,
        "created_at": ticket.created_at.isoformat(),
        "updated_at": ticket.updated_at.isoformat(),
        "canonical_order": (ticket.payload or {}).get("canonical_order", {}) if isinstance(ticket.payload, dict) else {},
        "free_text": free_text,
        "checks": [
            {"check_type": check.check_type, "decision": check.decision, "reasons": check.reasons, "created_at": check.created_at.isoformat()}
            for check in ticket.check_runs.all()
        ],
    }
    if include_related:
        record.update(
            {
                "events": [
                    {"event_type": event.event_type, "actor": event.actor, "payload": event.payload, "created_at": event.created_at.isoformat()}
                    for event in ticket.events.all()
                ],
                "fills": [
                    {"fill_id": fill.fill_id, "broker_order_id": fill.broker_order_id, "quantity": float(fill.quantity), "price": float(fill.price), "currency": fill.currency, "filled_at": fill.filled_at.isoformat()}
                    for fill in ticket.fills.all()
                ],
                "broker_orders": [
                    {"broker_order_id": broker_order.broker_order_id, "broker_status": broker_order.broker_status, "last_seen_at": broker_order.last_seen_at.isoformat() if broker_order.last_seen_at else ""}
                    for broker_order in ticket.broker_orders.all()
                ],
            }
        )
    return record


def normalize_order_ticket_fields(root: Path, args: dict[str, Any]) -> dict[str, Any]:
    parsed = parse_natural_language_order(str(args.get("natural_language") or args.get("prompt") or ""))
    fields = {**parsed, **{key: value for key, value in args.items() if value not in (None, "")}}
    missing = [field for field in ["symbol", "side", "quantity", "limit_price"] if fields.get(field) in (None, "")]
    if missing:
        raise ValueError(f"order ticket requires: {', '.join(missing)}")
    fields["symbol"] = str(fields["symbol"]).upper()
    fields["side"] = str(fields["side"]).lower()
    fields["quantity"] = Decimal(str(fields["quantity"]))
    fields["limit_price"] = Decimal(str(fields["limit_price"]))
    if fields["side"] not in {"buy", "sell"}:
        raise ValueError("side must be buy or sell")
    if fields["quantity"] <= 0:
        raise ValueError("quantity must be positive")
    if fields["limit_price"] <= 0:
        raise ValueError("limit_price must be positive")
    fields.setdefault("currency", "KRW")
    fields.setdefault("order_type", "limit")
    fields.setdefault("time_in_force", "day")
    fields.setdefault("created_at", datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"))
    fields.setdefault("created_by", args.get("principal_id") or "portfolio-manager")
    return fields


def parse_natural_language_order(text: str) -> dict[str, Any]:
    if not text.strip():
        return {}
    normalized = text.replace(",", " ")
    side = ""
    if re.search(r"\bbuy\b", normalized, flags=re.I):
        side = "buy"
    elif re.search(r"\bsell\b", normalized, flags=re.I):
        side = "sell"
    symbol = ""
    symbol_match = re.search(r"\b([A-Z]{1,6}(?:\.[A-Z]{1,3})?|\d{5,6})\b", normalized)
    if symbol_match:
        symbol = symbol_match.group(1).upper()
    quantity = ""
    quantity_match = re.search(r"(\d+(?:\.\d+)?)\s*shares?", normalized, flags=re.I)
    if not quantity_match:
        quantity_match = re.search(r"(?:buy|sell)\s+(\d+(?:\.\d+)?)", normalized, flags=re.I)
    if quantity_match:
        quantity = quantity_match.group(1)
    limit_price = ""
    price_match = re.search(r"(?:limit|@|at|price)\s*\$?\s*(\d+(?:\.\d+)?)", normalized, flags=re.I)
    if not price_match:
        price_match = re.search(r"(\d+(?:\.\d+)?)\s*(?:usd|dollars?|krw)\b", normalized, flags=re.I)
    if price_match:
        limit_price = price_match.group(1)
    currency = "USD" if re.search(r"\b(usd|dollars?)\b|\$", normalized, flags=re.I) else "KRW"
    return {key: value for key, value in {"symbol": symbol, "side": side, "quantity": quantity, "limit_price": limit_price, "currency": currency, "natural_language": text}.items() if value}


def canonical_order_from_fields(fields: dict[str, Any]) -> dict[str, Any]:
    created_at = str(fields.get("created_at") or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"))
    order = {
        "id": str(fields["id"]),
        "order_ticket_id": str(fields.get("order_ticket_id") or fields["id"]),
        "symbol": str(fields["symbol"]).upper(),
        "side": str(fields["side"]).lower(),
        "quantity": float(fields["quantity"]),
        "limit_price": float(fields["limit_price"]),
        "currency": str(fields.get("currency") or "KRW"),
        "broker": str(fields.get("broker") or fields.get("broker_connection_id") or "paper-trading"),
        "estimated_notional_krw": float(fields.get("estimated_notional_krw") or fields.get("estimated_notional") or (Decimal(str(fields["quantity"])) * Decimal(str(fields["limit_price"])))),
        "created_by": str(fields.get("created_by") or "portfolio-manager"),
        "created_at": created_at,
        "portfolio_id": str(fields.get("portfolio_id") or DEFAULT_PORTFOLIO_ID),
        "account_id": str(fields.get("account_id") or DEFAULT_ACCOUNT_ID),
        "strategy_id": str(fields.get("strategy_id") or DEFAULT_STRATEGY_ID),
        "broker_connection_id": str(fields.get("broker_connection_id") or fields.get("broker") or "paper-trading"),
        "broker_account_id": str(fields.get("broker_account_id") or fields.get("account_id") or DEFAULT_ACCOUNT_ID),
        "order_type": str(fields.get("order_type") or "limit"),
        "time_in_force": str(fields.get("time_in_force") or "day"),
    }
    try:
        from tradingcodex_service.application.brokers import canonical_order_from_order

        order["canonical_order"] = canonical_order_from_order({**fields, **order})
    except Exception:
        order["canonical_order"] = {}
    return order


def _resolve_ticket_broker_connection(root: Path, fields: dict[str, Any]) -> Any:
    from apps.integrations.models import BrokerConnection
    from tradingcodex_service.application.brokers import ensure_paper_broker_connection

    broker_id = fields.get("broker_connection_id") or fields.get("broker_id") or fields.get("broker") or "paper-trading"
    if broker_id == "paper-trading":
        return ensure_paper_broker_connection(root)
    connection = BrokerConnection.objects.filter(broker_id=broker_id).first()
    if connection is None:
        raise ValueError(f"unknown broker connection: {broker_id}")
    return connection


def _resolve_ticket_broker_account(connection: Any, fields: dict[str, Any]) -> Any:
    if connection is None:
        return None
    account_id = fields.get("broker_account_id") or fields.get("account_id")
    if account_id:
        return connection.accounts.filter(broker_account_id=account_id).first()
    return connection.accounts.order_by("broker_account_id").first()


def _ticket_summary(order: dict[str, Any]) -> str:
    return f"{order['side'].upper()} {order['quantity']} {order['symbol']} @ {order['limit_price']} {order['currency']}"


def _schema_reasons(order: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    if order.get(ORDER_RESOLUTION_ERROR_FIELD):
        reasons.append(str(order[ORDER_RESOLUTION_ERROR_FIELD]))
    for field in ["id", "symbol", "side", "quantity", "limit_price", "currency", "broker", "estimated_notional_krw", "created_by", "created_at"]:
        if order.get(field) in (None, ""):
            reasons.append(f"missing {field}")
    if order.get("side") not in {"buy", "sell"}:
        reasons.append("side must be buy or sell")
    _validate_positive(order.get("quantity"), "quantity", reasons)
    _validate_positive(order.get("limit_price"), "limit_price", reasons)
    _validate_positive(order.get("estimated_notional_krw"), "estimated_notional_krw", reasons)
    return reasons


def _check_result(check_type: str, passed: bool, reasons: list[str], payload: dict[str, Any] | None = None, decision: str | None = None) -> dict[str, Any]:
    if decision is None:
        decision = "pass" if passed else "fail"
    return {"check_type": check_type, "decision": decision, "reasons": reasons, "payload": payload or {}}


def _record_ticket_submit_result(root: Path, order: dict[str, Any], receipt: dict[str, Any], adapter_result: dict[str, Any], principal_id: str) -> None:
    ensure_runtime_database(root)
    from apps.orders.models import BrokerOrder, Fill
    from apps.portfolio.models import PortfolioLedgerEvent
    from tradingcodex_service.application.brokers import ensure_paper_broker_connection, sync_broker_account

    ticket = ensure_order_ticket_for_order(root, order, principal_id)
    if ticket is None:
        return
    try:
        if ticket.current_state in {"READY_FOR_APPROVAL", "PRECHECKED", "DRAFT"}:
            transition_order_ticket(ticket, "APPROVED", receipt.get("approved_by") or "risk-manager", {"approval_receipt_id": receipt.get("id")})
        if ticket.current_state == "APPROVED":
            transition_order_ticket(ticket, "RESERVED", principal_id, {"approval_receipt_id": receipt.get("id")})
        if ticket.current_state == "RESERVED":
            transition_order_ticket(ticket, "SUBMITTED", principal_id, adapter_result)
    except ValueError:
        pass
    broker_order_id = str(adapter_result.get("broker_order_id") or f"local-{order.get('id')}")
    submitted_at = _parse_datetime(adapter_result.get("submitted_at")) or datetime.now(timezone.utc)
    broker_order, _ = BrokerOrder.objects.update_or_create(
        ticket=ticket,
        broker_order_id=broker_order_id,
        defaults={
            "broker_status": str(adapter_result.get("status") or "submitted"),
            "submitted_at": submitted_at,
            "last_seen_at": datetime.now(timezone.utc),
            "raw_status_payload_hash": stable_hash(adapter_result),
            "metadata": adapter_result,
        },
    )
    record_order_event(ticket, "submitted", principal_id, {"broker_order_id": broker_order_id, "status": broker_order.broker_status})
    try:
        if adapter_result.get("needs_review") and ticket.current_state in {"SUBMITTED", "ACKED", "PARTIALLY_FILLED"}:
            transition_order_ticket(ticket, "NEEDS_REVIEW", principal_id, adapter_result)
        elif ticket.current_state == "SUBMITTED":
            transition_order_ticket(ticket, "ACKED", principal_id, {"broker_order_id": broker_order_id, "broker_status": broker_order.broker_status})
    except ValueError:
        pass
    filled_quantity = _number_or_none(adapter_result.get("filled_quantity"))
    fill_price = _number_or_none(adapter_result.get("average_price"))
    if filled_quantity and fill_price:
        raw_fill = {
            "broker_order_id": broker_order_id,
            "quantity": float(filled_quantity),
            "price": float(fill_price),
            "adapter_result": adapter_result,
        }
        fill, created = Fill.objects.get_or_create(
            ticket=ticket,
            broker_order_id=broker_order_id,
            fill_id=str(adapter_result.get("fill_id") or stable_hash(raw_fill)[:24]),
            defaults={
                "quantity": filled_quantity,
                "price": fill_price,
                "fee": adapter_result.get("fee", 0),
                "currency": order.get("currency", "KRW"),
                "filled_at": submitted_at,
                "raw_payload_hash": stable_hash(raw_fill),
            },
        )
        if created:
            record_order_event(ticket, "fill", principal_id, {"fill_id": fill.fill_id, "quantity": float(filled_quantity), "price": float(fill_price)})
            connection = ticket.broker_connection or ensure_paper_broker_connection(root)
            PortfolioLedgerEvent.objects.create(
                event_type="fill",
                broker_connection=connection,
                broker_account=ticket.broker_account,
                portfolio_id=ticket.portfolio_id,
                account_id=ticket.account_id,
                strategy_id=ticket.strategy_id,
                instrument_id=ticket.instrument_id or ticket.symbol,
                symbol=ticket.symbol,
                quantity=filled_quantity,
                amount=Decimal(str(filled_quantity)) * Decimal(str(fill_price)),
                price=fill_price,
                currency=order.get("currency", "KRW"),
                event_at=submitted_at,
                source_payload_hash=stable_hash(raw_fill),
                raw_payload_ref=f"fill:{fill.fill_id}",
                metadata=raw_fill,
            )
        try:
            if ticket.current_state in {"SUBMITTED", "ACKED", "PARTIALLY_FILLED"}:
                transition_order_ticket(ticket, "FILLED", principal_id, {"fill_id": fill.fill_id})
        except ValueError:
            pass
        try:
            sync_broker_account(root, {"broker_id": (ticket.broker_connection.broker_id if ticket.broker_connection else order.get("broker") or "paper-trading"), "principal_id": principal_id})
        except Exception:
            pass
    elif str(adapter_result.get("status") or "").lower() in {"rejected", "failed"}:
        try:
            transition_order_ticket(ticket, "REJECTED", principal_id, adapter_result)
        except ValueError:
            pass


def _number_or_none(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value))
    except Exception:
        return None


def submit_with_adapter(root: Path, order: dict[str, Any]) -> dict[str, Any]:
    broker = order.get("broker")
    if broker == "stub-execution":
        return {
            "adapter": "stub-execution",
            "broker_order_id": f"stub-{order['id']}",
            "status": "stubbed",
            "submitted_at": now_iso(),
            "order": order,
        }
    if broker == "paper-trading":
        return submit_paper_order(root, order)
    ensure_runtime_database(root)
    from apps.integrations.models import BrokerConnection
    from tradingcodex_service.application.brokers import adapter_for_connection, broker_connection_provider_review_reasons

    connection = BrokerConnection.objects.filter(broker_id=broker).first()
    if connection is None:
        raise ValueError(f"Adapter is not enabled: {broker}")
    if connection.status != "trading_enabled":
        raise ValueError(f"broker connection is not trading_enabled: {broker}")
    source_reasons = broker_connection_provider_review_reasons(connection, root)
    if source_reasons:
        raise ValueError(source_reasons[0])
    order = dict(order)
    order.setdefault("client_order_id", _deterministic_client_order_id(order))
    return adapter_for_connection(connection, root).submit_order(order)


def reconcile_existing_ticket_activity_before_reject(
    root: Path,
    args: dict[str, Any],
    principal_id: str,
    original_rejection: dict[str, Any],
) -> dict[str, Any] | None:
    try:
        ticket = get_order_ticket_model(root, args)
    except ValueError:
        return None
    broker_order = ticket.broker_orders.order_by("-submitted_at", "-id").first()
    if broker_order is None and ticket.current_state not in {"SUBMITTED", "ACKED", "PARTIALLY_FILLED", "FILLED", "NEEDS_REVIEW"}:
        return None

    reconciliation: dict[str, Any] = {"status": "local_timeline", "ticket_id": ticket.ticket_id}
    if broker_order is not None:
        try:
            reconciliation = refresh_broker_order_status(
                root,
                {
                    **args,
                    "principal_id": principal_id,
                    "ticket_id": ticket.ticket_id,
                    "broker_order_id": broker_order.broker_order_id,
                },
            )
        except Exception as exc:
            reconciliation = {
                "status": "refresh_failed",
                "ticket_id": ticket.ticket_id,
                "broker_order_id": broker_order.broker_order_id,
                "reasons": [str(exc)],
            }
    ticket.refresh_from_db()
    if ticket.current_state in {"SUBMITTED", "ACKED", "PARTIALLY_FILLED", "FILLED", "NEEDS_REVIEW"}:
        result = {
            "status": "reconciled",
            "order_ticket_id": ticket.ticket_id,
            "original_rejection": original_rejection,
            "reconciliation": reconciliation,
            "ticket": serialize_order_ticket(ticket, include_related=True),
            "db_canonical": True,
            "workspace_context": workspace_context_payload(root),
        }
        write_audit_event(root, {"type": "submit_approved_order.reconciled_before_reject", "payload": result}, principal_id=principal_id, source="mcp")
        return result
    return None


def _adapter_preflight_reasons(root: Path, order: dict[str, Any], args: dict[str, Any] | None = None) -> list[str]:
    args = args or {}
    broker = order.get("broker")
    if broker in {"stub-execution", "paper-trading"}:
        return []
    ensure_runtime_database(root)
    from apps.integrations.models import BrokerConnection
    from tradingcodex_service.application.brokers import adapter_for_connection, broker_connection_provider_review_reasons, _reconcile_validation_execution_status

    connection = BrokerConnection.objects.filter(broker_id=broker).first()
    if connection is None:
        return [f"Adapter is not enabled: {broker}"]
    if connection.status != "trading_enabled":
        return [f"broker connection is not trading_enabled: {broker}"]
    source_reasons = broker_connection_provider_review_reasons(connection, root)
    if source_reasons:
        return source_reasons
    metadata = connection.metadata if isinstance(connection.metadata, dict) else {}
    profile = metadata.get("capability_profile") if isinstance(metadata.get("capability_profile"), dict) else {}
    if profile.get("execution_posture") == "live_broker":
        reasons: list[str] = []
        if os.environ.get("TRADINGCODEX_ENABLE_LIVE_EXECUTION", "").lower() not in {"1", "true", "yes", "on"}:
            reasons.append("TRADINGCODEX_ENABLE_LIVE_EXECUTION=1 is required for live broker submission")
        expected = _expected_live_confirmation(order)
        if str(args.get("live_confirmation") or "") != expected:
            reasons.append(f"live confirmation required: {expected}")
        if "order.submit.live" not in set(connection.enabled_trade_scopes or []):
            reasons.append(f"broker connection lacks order.submit.live scope: {broker}")
        if reasons:
            return reasons
    health = adapter_for_connection(connection, root).health_check()
    _reconcile_validation_execution_status(connection, health, root)
    if health.status != "ok":
        message = f": {health.message}" if health.message else ""
        return [f"broker health is {health.status}{message}"]
    return []


def _deterministic_client_order_id(order: dict[str, Any]) -> str:
    source = {
        "ticket_id": order.get("order_ticket_id") or order.get("id"),
        "broker": order.get("broker"),
        "symbol": order.get("symbol"),
        "side": order.get("side"),
        "quantity": str(order.get("quantity")),
    }
    return ("tcx-" + stable_hash(source)[:28])[:32]


def _expected_live_confirmation(order: dict[str, Any]) -> str:
    return "LIVE:{ticket}:{broker}:{symbol}:{side}:{quantity}".format(
        ticket=order.get("order_ticket_id") or order.get("id") or "",
        broker=order.get("broker") or order.get("broker_connection_id") or "",
        symbol=order.get("symbol") or "",
        side=order.get("side") or "",
        quantity=order.get("quantity") or "",
    )


def resolve_order_ticket_payload(root: Path, args: dict[str, Any]) -> dict[str, Any]:
    if isinstance(args.get("order"), dict):
        return args["order"]
    ticket_id = args.get("ticket_id") or args.get("order_ticket_id") or args.get("order_id")
    if ticket_id:
        try:
            return order_payload_from_ticket(get_order_ticket_model(root, {**args, "ticket_id": str(ticket_id)}))
        except ValueError as exc:
            return {"id": str(ticket_id), "order_ticket_id": str(ticket_id), ORDER_RESOLUTION_ERROR_FIELD: str(exc)}
    return {}


def resolve_approval_receipt(root: Path, args: dict[str, Any], order: dict[str, Any] | None = None) -> dict[str, Any]:
    if isinstance(args.get("approval_receipt"), dict):
        return args["approval_receipt"]
    if args.get("approval_receipt_path"):
        return read_json(safe_workspace_path(root, args["approval_receipt_path"], allowed_roots=APPROVAL_FILE_ROOTS), {})
    if args.get("approval_receipt_id"):
        return find_approval_receipt_by_id(root, args["approval_receipt_id"]) or {}
    if order and order.get("id"):
        return find_approval_receipt_by_order_id(root, order["id"]) or {}
    ticket_id = args.get("ticket_id") or args.get("order_ticket_id")
    if ticket_id:
        return find_approval_receipt_by_order_id(root, ticket_id) or {}
    return {}


def find_order_payload_by_id(root: Path, order_id: str) -> dict[str, Any] | None:
    try:
        ensure_runtime_database(root)
        from apps.orders.models import OrderTicket

        ticket = OrderTicket.objects.select_related("broker_connection", "broker_account").filter(ticket_id=order_id).first()
        if ticket is not None:
            return order_payload_from_ticket(ticket)
    except Exception:
        pass
    return None


def order_ticket_payload_conflict(root: Path, order: dict[str, Any]) -> str:
    order_id = order.get("order_ticket_id") or order.get("id")
    if not order_id:
        return ""
    try:
        ensure_runtime_database(root)
        from apps.orders.models import OrderTicket

        ticket = OrderTicket.objects.select_related("broker_connection", "broker_account").filter(ticket_id=order_id).first()
        if ticket is None:
            return ""
        stored_order = order_payload_from_ticket(ticket)
        if stable_hash(_order_conflict_payload(stored_order)) != stable_hash(_order_conflict_payload(order)):
            return "order ticket id already exists with a different payload"
    except Exception as exc:
        return f"order ticket DB conflict check unavailable: {exc}"
    return ""


def _order_conflict_payload(order: dict[str, Any]) -> dict[str, Any]:
    quantity = _number_or_none(order.get("quantity"))
    limit_price = _number_or_none(order.get("limit_price"))
    notional = _number_or_none(order.get("estimated_notional_krw") or order.get("estimated_notional"))
    return {
        "id": str(order.get("order_ticket_id") or order.get("id") or ""),
        "symbol": str(order.get("symbol") or "").upper(),
        "side": str(order.get("side") or "").lower(),
        "quantity": str(quantity) if quantity is not None else "",
        "limit_price": str(limit_price) if limit_price is not None else "",
        "currency": str(order.get("currency") or "KRW"),
        "broker": str(order.get("broker_connection_id") or order.get("broker") or "paper-trading"),
        "estimated_notional_krw": str(notional) if notional is not None else "",
        "portfolio_id": str(order.get("portfolio_id") or DEFAULT_PORTFOLIO_ID),
        "account_id": str(order.get("account_id") or DEFAULT_ACCOUNT_ID),
        "strategy_id": str(order.get("strategy_id") or DEFAULT_STRATEGY_ID),
        "order_type": str(order.get("order_type") or "limit"),
        "time_in_force": str(order.get("time_in_force") or "day"),
    }


def find_approval_receipt_by_id(root: Path, receipt_id: str) -> dict[str, Any] | None:
    try:
        ensure_runtime_database(root)
        from apps.orders.models import ApprovalReceipt

        stored = ApprovalReceipt.objects.filter(receipt_id=receipt_id).first()
        if stored:
            return stored.payload or {}
    except Exception:
        pass
    for path in (root / "trading" / "approvals").glob("*.json"):
        data = read_json(path, {})
        if data.get("id") == receipt_id:
            return data
    return None


def find_approval_receipt_by_order_id(root: Path, order_id: str) -> dict[str, Any] | None:
    try:
        ensure_runtime_database(root)
        from apps.orders.models import ApprovalReceipt

        stored = ApprovalReceipt.objects.filter(order_ticket_id=order_id, valid=True).order_by("-created_at", "-id").first()
        if stored:
            return stored.payload or {}
    except Exception:
        pass
    for path in (root / "trading" / "approvals").glob("*.json"):
        data = read_json(path, {})
        if data.get("order_ticket_id") == order_id:
            return data
    return None


def write_rejected_order(root: Path, order: dict[str, Any], reasons: list[str]) -> None:
    write_json(root / "trading" / "orders" / "rejected" / f"{sanitize_id(order.get('order_ticket_id') or order.get('id', 'unknown'))}.rejected_order.json", {
        "order": order,
        "order_ticket_id": order.get("order_ticket_id") or order.get("id", ""),
        "rejected_at": now_iso(),
        "reasons": reasons,
    })


def persist_approval_receipt_if_available(root: Path, receipt: dict[str, Any]) -> None:
    required = ["id", "order_ticket_id", "approved_by", "valid", "expires_at"]
    if any(receipt.get(field) in (None, "") for field in required):
        return
    try:
        ensure_runtime_database(root)
        from apps.orders.models import ApprovalReceipt

        ApprovalReceipt.objects.update_or_create(
            receipt_id=receipt["id"],
            defaults={
                "approved_by": receipt["approved_by"],
                "valid": bool(receipt["valid"]),
                "expires_at": _parse_datetime(receipt["expires_at"]) or datetime.now(timezone.utc),
                "exact_order_hash": receipt.get("exact_order_hash", ""),
                "order_ticket_id": receipt.get("order_ticket_id", ""),
                "broker_connection_id": receipt.get("broker_connection_id", ""),
                "broker_account_id": receipt.get("broker_account_id", ""),
                "max_notional": receipt.get("max_notional") or None,
                "max_price": receipt.get("max_price") or None,
                "max_slippage_bps": receipt.get("max_slippage_bps") or None,
                "approved_order_type": receipt.get("approved_order_type", ""),
                "approved_time_in_force": receipt.get("approved_time_in_force", ""),
                "valid_until": _parse_datetime(receipt.get("valid_until")) if receipt.get("valid_until") else None,
                "quote_as_of_requirement": receipt.get("quote_as_of_requirement", ""),
                "workspace_context": workspace_context_payload(root),
                "payload": receipt,
            },
        )
    except Exception as exc:
        raise RuntimeError(f"failed to persist approval receipt: {exc}") from exc

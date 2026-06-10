from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from tradingcodex_service.application.audit import write_audit_event
from tradingcodex_service.application.common import (
    _parse_datetime,
    _resolve_path,
    _validate_positive,
    now_iso,
    read_json,
    sanitize_id,
    write_json,
)
from tradingcodex_service.application.policy import evaluate_policy
from tradingcodex_service.application.portfolio import portfolio_keys, submit_paper_order
from tradingcodex_service.application.runtime import ensure_runtime_database, workspace_context_payload

def validate_order_intent(workspace_root: Path | str, args: dict[str, Any]) -> dict[str, Any]:
    order = resolve_order_intent(Path(workspace_root), args)
    reasons: list[str] = []
    for field in ["id", "symbol", "side", "quantity", "limit_price", "currency", "broker", "estimated_notional_krw", "created_by", "created_at"]:
        if order.get(field) in (None, ""):
            reasons.append(f"missing {field}")
    if order.get("side") not in ("buy", "sell"):
        reasons.append("side must be buy or sell")
    _validate_positive(order.get("quantity"), "quantity", reasons)
    _validate_positive(order.get("limit_price"), "limit_price", reasons)
    _validate_positive(order.get("estimated_notional_krw"), "estimated_notional_krw", reasons)
    policy = evaluate_policy(workspace_root, {**args, "action": args.get("action") or "order_intent.validate", "order_intent": order})
    all_reasons = list(dict.fromkeys(reasons + policy["reasons"]))
    result = {"valid": not all_reasons and policy["decision"] == "allow", "reasons": all_reasons, "policy": policy, "db_canonical": True, "workspace_context": workspace_context_payload(workspace_root)}
    persist_order_intent_if_available(Path(workspace_root), order, result)
    return result


def validate_approval_receipt(workspace_root: Path | str, args: dict[str, Any]) -> dict[str, Any]:
    root = Path(workspace_root)
    order = resolve_order_intent(root, args)
    receipt = resolve_approval_receipt(root, args, order)
    reasons: list[str] = []
    for field in ["id", "order_intent_id", "approved_by", "valid", "expires_at"]:
        if receipt.get(field) in (None, ""):
            reasons.append(f"missing {field}")
    if receipt.get("valid") is not True:
        reasons.append("approval_receipt.valid must be true")
    if order.get("id") and receipt.get("order_intent_id") != order["id"]:
        reasons.append("approval_receipt.order_intent_id does not match order_intent.id")
    if order.get("created_by") and receipt.get("approved_by") == order["created_by"]:
        reasons.append("order creator cannot approve the same order")
    expires_at = _parse_datetime(receipt.get("expires_at"))
    if receipt.get("expires_at") and expires_at is None:
        reasons.append("approval_receipt.expires_at is not a valid date")
    if expires_at and expires_at <= datetime.now(timezone.utc):
        reasons.append("approval receipt is expired")
    return {"valid": not reasons, "reasons": reasons, "db_canonical": True, "workspace_context": workspace_context_payload(workspace_root)}


def create_approval_receipt(workspace_root: Path | str, order: dict[str, Any], approved_by: str = "risk-manager", expires_hours: int = 24) -> dict[str, Any]:
    root = Path(workspace_root)
    validation = validate_order_intent(root, {"principal_id": approved_by, "order_intent": order})
    if not validation["valid"]:
        rejected = {"status": "rejected", "order_intent_id": order.get("id"), "reasons": validation["reasons"], "db_canonical": True, "workspace_context": workspace_context_payload(root)}
        write_rejected_order(root, order, validation["reasons"])
        write_audit_event(root, {"type": "approval.rejected", "payload": rejected}, principal_id=approved_by, source="service")
        return rejected
    approval_policy = evaluate_policy(root, {"principal_id": approved_by, "action": "approval_receipt.create", "order_intent": order})
    if approval_policy["decision"] != "allow":
        rejected = {"status": "rejected", "order_intent_id": order.get("id"), "reasons": approval_policy["reasons"], "db_canonical": True, "workspace_context": workspace_context_payload(root)}
        write_rejected_order(root, order, approval_policy["reasons"])
        write_audit_event(root, {"type": "approval.rejected", "payload": rejected}, principal_id=approved_by, source="service")
        return rejected
    if approved_by == order.get("created_by"):
        raise ValueError("order creator cannot approve the same order")
    created = datetime.now(timezone.utc)
    receipt = {
        "id": f"approval-{sanitize_id(order['id'])}-{created.strftime('%Y%m%dT%H%M%S%fZ')}",
        "order_intent_id": order["id"],
        "approved_by": approved_by,
        "valid": True,
        "created_at": created.isoformat().replace("+00:00", "Z"),
        "expires_at": (created + timedelta(hours=expires_hours)).isoformat().replace("+00:00", "Z"),
        "policy_decision": validation["policy"],
    }
    receipt_validation = validate_approval_receipt(root, {"order_intent": order, "approval_receipt": receipt})
    if not receipt_validation["valid"]:
        return {"status": "rejected", "order_intent_id": order.get("id"), "reasons": receipt_validation["reasons"], "db_canonical": True, "workspace_context": workspace_context_payload(root)}
    persist_order_intent_if_available(root, order, validation)
    persist_approval_receipt_if_available(root, receipt)
    write_json(root / "trading" / "orders" / "approved" / f"{sanitize_id(order['id'])}.order_intent.json", order)
    write_json(root / "trading" / "approvals" / f"{sanitize_id(order['id'])}.approval_receipt.json", receipt)
    result = {
        "status": "approved",
        "order_intent_id": order["id"],
        "approved_order_path": f"trading/orders/approved/{sanitize_id(order['id'])}.order_intent.json",
        "approval_receipt_path": f"trading/approvals/{sanitize_id(order['id'])}.approval_receipt.json",
        "db_canonical": True,
        "workspace_context": workspace_context_payload(root),
    }
    write_audit_event(root, {"type": "approval.accepted", "payload": result}, principal_id=approved_by, source="service")
    return result


def submit_approved_order(workspace_root: Path | str, args: dict[str, Any]) -> dict[str, Any]:
    root = Path(workspace_root)
    order = resolve_order_intent(root, args)
    receipt = resolve_approval_receipt(root, args, order)
    principal_id = args.get("principal_id") or "execution-operator"
    order_validation = validate_order_intent(root, {"principal_id": principal_id, "order_intent": order})
    receipt_validation = validate_approval_receipt(root, {"order_intent": order, "approval_receipt": receipt})
    policy = evaluate_policy(root, {
        "principal_id": principal_id,
        "action": "mcp.tradingcodex.submit_approved_order",
        "order_intent": order,
        "approval_receipt": receipt,
        "require_approval_check": True,
    })
    if not order_validation["valid"] or not receipt_validation["valid"] or policy["decision"] != "allow":
        rejected = {
            "status": "rejected",
            "order_intent_id": order.get("id"),
            "reasons": order_validation["reasons"] + receipt_validation["reasons"] + policy["reasons"],
            "db_canonical": True,
            "workspace_context": workspace_context_payload(root),
        }
        write_audit_event(root, {"type": "submit_approved_order.rejected", "payload": rejected}, principal_id=principal_id, source="mcp")
        return rejected
    ensure_runtime_database(root)
    from apps.orders.services import finalize_execution_reservation, reserve_execution

    portfolio_id, account_id, strategy_id = portfolio_keys(order)
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
            "order_intent_id": order.get("id"),
            "idempotency_key": reservation.idempotency_key,
            "reasons": [f"order already has an execution result: {reservation.execution.status}"],
            "db_canonical": True,
            "workspace_context": workspace_context_payload(root),
        }
        write_audit_event(root, {"type": "submit_approved_order.duplicate", "payload": rejected}, principal_id=principal_id, source="mcp")
        return rejected
    try:
        adapter_result = submit_with_adapter(root, order)
    except Exception as exc:
        rejected = {
            "status": "rejected",
            "order_intent_id": order.get("id"),
            "adapter": order.get("broker"),
            "reasons": [f"adapter error: {exc}"],
            "db_canonical": True,
            "workspace_context": workspace_context_payload(root),
        }
        finalize_execution_reservation(reservation.execution, rejected)
        write_audit_event(root, {"type": "submit_approved_order.adapter_error", "payload": rejected}, principal_id=principal_id, source="mcp")
        return rejected
    accepted = {"status": "accepted", "order_intent_id": order["id"], "adapter": order["broker"], "idempotency_key": reservation.idempotency_key, "result": adapter_result, "db_canonical": True, "workspace_context": workspace_context_payload(root)}
    persist_order_intent_if_available(root, order)
    persist_approval_receipt_if_available(root, receipt)
    finalize_execution_reservation(reservation.execution, accepted)
    write_json(root / "trading" / "orders" / "executed" / f"{sanitize_id(order['id'])}.execution_result.json", {
        "order_intent_id": order["id"],
        "approval_receipt_id": receipt.get("id"),
        "idempotency_key": reservation.idempotency_key,
        "result": accepted,
    })
    write_audit_event(root, {"type": "submit_approved_order.accepted", "payload": accepted}, principal_id=principal_id, source="mcp")
    return accepted


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
    raise ValueError(f"Adapter is not enabled: {broker}")

def resolve_order_intent(root: Path, args: dict[str, Any]) -> dict[str, Any]:
    if isinstance(args.get("order_intent"), dict):
        return args["order_intent"]
    if isinstance(args.get("order"), dict):
        return args["order"]
    if args.get("order_intent_path"):
        return read_json(_resolve_path(root, args["order_intent_path"]), {})
    if args.get("order_intent_id"):
        return find_order_intent_by_id(root, args["order_intent_id"]) or {}
    return {}


def resolve_approval_receipt(root: Path, args: dict[str, Any], order: dict[str, Any] | None = None) -> dict[str, Any]:
    if isinstance(args.get("approval_receipt"), dict):
        return args["approval_receipt"]
    if args.get("approval_receipt_path"):
        return read_json(_resolve_path(root, args["approval_receipt_path"]), {})
    if args.get("approval_receipt_id"):
        return find_approval_receipt_by_id(root, args["approval_receipt_id"]) or {}
    if order and order.get("id"):
        return find_approval_receipt_by_order_id(root, order["id"]) or {}
    return {}


def find_order_intent_by_id(root: Path, order_id: str) -> dict[str, Any] | None:
    try:
        ensure_runtime_database(root)
        from apps.orders.models import OrderIntent

        stored = OrderIntent.objects.filter(intent_id=order_id).first()
        if stored:
            payload = stored.payload or {}
            if isinstance(payload.get("order_intent"), dict):
                return payload["order_intent"]
            return {
                "id": stored.intent_id,
                "symbol": stored.symbol,
                "side": stored.side,
                "quantity": float(stored.quantity),
                "limit_price": float(stored.limit_price),
                "currency": stored.currency,
                "broker": stored.broker,
                "estimated_notional_krw": float(stored.estimated_notional_krw),
                "created_by": stored.created_by,
                "created_at": stored.created_at.isoformat(),
                "portfolio_id": stored.portfolio_id,
                "account_id": stored.account_id,
                "strategy_id": stored.strategy_id,
            }
    except Exception:
        pass
    for folder in ["approved", "draft", "rejected", "executed"]:
        for path in (root / "trading" / "orders" / folder).glob("*.json"):
            data = read_json(path, {})
            if data.get("id") == order_id or data.get("order_intent", {}).get("id") == order_id or data.get("order_intent_id") == order_id:
                return data.get("order_intent") or data
    return None


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

        stored = ApprovalReceipt.objects.filter(order_intent_id=order_id, valid=True).order_by("-created_at", "-id").first()
        if stored:
            return stored.payload or {}
    except Exception:
        pass
    for path in (root / "trading" / "approvals").glob("*.json"):
        data = read_json(path, {})
        if data.get("order_intent_id") == order_id:
            return data
    return None


def write_rejected_order(root: Path, order: dict[str, Any], reasons: list[str]) -> None:
    write_json(root / "trading" / "orders" / "rejected" / f"{sanitize_id(order.get('id', 'unknown'))}.rejected.json", {
        "order_intent": order,
        "rejected_at": now_iso(),
        "reasons": reasons,
    })


def persist_order_intent_if_available(root: Path, order: dict[str, Any], validation: dict[str, Any] | None = None) -> None:
    required = ["id", "symbol", "side", "quantity", "limit_price", "currency", "broker", "estimated_notional_krw", "created_by", "created_at"]
    if any(order.get(field) in (None, "") for field in required):
        return
    try:
        ensure_runtime_database(root)
        from apps.orders.models import OrderIntent

        portfolio_id, account_id, strategy_id = portfolio_keys(order)
        OrderIntent.objects.update_or_create(
            intent_id=order["id"],
            defaults={
                "symbol": str(order["symbol"]).upper(),
                "side": order["side"],
                "quantity": order["quantity"],
                "limit_price": order["limit_price"],
                "currency": order["currency"],
                "broker": order["broker"],
                "estimated_notional_krw": order["estimated_notional_krw"],
                "created_by": order["created_by"],
                "created_at": _parse_datetime(order["created_at"]) or datetime.now(timezone.utc),
                "portfolio_id": portfolio_id,
                "account_id": account_id,
                "strategy_id": strategy_id,
                "workspace_context": workspace_context_payload(root),
                "payload": {"order_intent": order, "validation": validation or {}},
            },
        )
    except Exception:
        return


def persist_approval_receipt_if_available(root: Path, receipt: dict[str, Any]) -> None:
    required = ["id", "order_intent_id", "approved_by", "valid", "expires_at"]
    if any(receipt.get(field) in (None, "") for field in required):
        return
    try:
        ensure_runtime_database(root)
        from apps.orders.models import ApprovalReceipt

        ApprovalReceipt.objects.update_or_create(
            receipt_id=receipt["id"],
            defaults={
                "order_intent_id": receipt["order_intent_id"],
                "approved_by": receipt["approved_by"],
                "valid": bool(receipt["valid"]),
                "expires_at": _parse_datetime(receipt["expires_at"]) or datetime.now(timezone.utc),
                "workspace_context": workspace_context_payload(root),
                "payload": receipt,
            },
        )
    except Exception:
        return


def persist_execution_result_if_available(root: Path, order: dict[str, Any], receipt: dict[str, Any], result: dict[str, Any]) -> None:
    if not order.get("id"):
        return
    try:
        ensure_runtime_database(root)
        from apps.orders.services import execution_idempotency_key
        from apps.orders.models import ExecutionResult

        portfolio_id, account_id, strategy_id = portfolio_keys(order)
        key = str(result.get("idempotency_key") or execution_idempotency_key(order, receipt))
        ExecutionResult.objects.update_or_create(
            idempotency_key=key,
            defaults={
                "order_intent_id": order["id"],
                "approval_receipt_id": receipt.get("id", ""),
                "adapter": result.get("adapter") or order.get("broker", ""),
                "status": result.get("status", "recorded"),
                "portfolio_id": portfolio_id,
                "account_id": account_id,
                "strategy_id": strategy_id,
                "workspace_context": workspace_context_payload(root),
                "payload": result,
            },
        )
    except Exception:
        return

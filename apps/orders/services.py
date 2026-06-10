from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from django.db import IntegrityError, transaction

from apps.orders.models import ExecutionResult


@dataclass(frozen=True)
class ExecutionReservation:
    created: bool
    execution: ExecutionResult
    idempotency_key: str


def execution_idempotency_key(
    order: dict[str, Any],
    receipt: dict[str, Any] | None = None,
    portfolio_id: str = "",
    account_id: str = "",
    strategy_id: str = "",
) -> str:
    explicit = order.get("idempotency_key") or (receipt or {}).get("idempotency_key")
    if explicit:
        return str(explicit)
    payload = {
        "order_intent_id": order.get("id"),
        "portfolio_id": portfolio_id or order.get("portfolio_id", ""),
        "account_id": account_id or order.get("account_id", ""),
        "strategy_id": strategy_id or order.get("strategy_id", ""),
        "execution_boundary": "submit_approved_order",
    }
    return "submit:" + hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def existing_execution_for_order(
    order_id: str,
    idempotency_key: str,
    portfolio_id: str,
    account_id: str,
    strategy_id: str,
) -> ExecutionResult | None:
    return (
        ExecutionResult.objects.filter(idempotency_key=idempotency_key).order_by("-created_at", "-id").first()
        or ExecutionResult.objects.filter(
            order_intent_id=order_id,
            portfolio_id=portfolio_id,
            account_id=account_id,
            strategy_id=strategy_id,
        ).order_by("-created_at", "-id").first()
    )


def reserve_execution(
    *,
    order: dict[str, Any],
    receipt: dict[str, Any],
    adapter: str,
    portfolio_id: str,
    account_id: str,
    strategy_id: str,
    workspace_context: dict[str, Any],
    principal_id: str,
) -> ExecutionReservation:
    key = execution_idempotency_key(order, receipt, portfolio_id, account_id, strategy_id)
    existing = existing_execution_for_order(str(order.get("id", "")), key, portfolio_id, account_id, strategy_id)
    if existing is not None:
        return ExecutionReservation(False, existing, key)

    payload = {
        "status": "pending",
        "order_intent_id": order.get("id"),
        "approval_receipt_id": receipt.get("id", ""),
        "principal_id": principal_id,
        "idempotency_key": key,
    }
    try:
        with transaction.atomic():
            execution = ExecutionResult.objects.create(
                order_intent_id=order["id"],
                approval_receipt_id=receipt.get("id", ""),
                adapter=adapter,
                status="pending",
                portfolio_id=portfolio_id,
                account_id=account_id,
                strategy_id=strategy_id,
                workspace_context=workspace_context,
                payload=payload,
                idempotency_key=key,
            )
    except IntegrityError:
        execution = existing_execution_for_order(str(order.get("id", "")), key, portfolio_id, account_id, strategy_id)
        if execution is None:
            raise
        return ExecutionReservation(False, execution, key)
    return ExecutionReservation(True, execution, key)


def finalize_execution_reservation(execution: ExecutionResult, result: dict[str, Any]) -> None:
    execution.status = str(result.get("status") or "recorded")
    execution.adapter = str(result.get("adapter") or execution.adapter)
    execution.payload = result
    execution.save(update_fields=["status", "adapter", "payload"])

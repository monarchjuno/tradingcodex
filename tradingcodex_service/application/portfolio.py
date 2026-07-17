from __future__ import annotations

import re
import time
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN
from pathlib import Path
from typing import Any

from tradingcodex_service.application.common import now_iso
from tradingcodex_service.application.research_objects import content_hash
from tradingcodex_service.application.runtime import (
    DEFAULT_BASE_CURRENCY,
    active_profile_for_workspace,
    base_currency_for_workspace,
    ensure_runtime_database,
    normalize_currency_code,
    workspace_context_payload,
)

DEFAULT_PAPER_CASH = Decimal("100000")
INTERNAL_MONEY_QUANTUM = Decimal("0.000001")


class PortfolioConcurrencyError(RuntimeError):
    pass


def submit_paper_order(root: Path, order: dict[str, Any]) -> dict[str, Any]:
    ensure_runtime_database(root)
    from django.db import OperationalError, transaction
    from apps.portfolio.models import PaperPortfolioState

    portfolio_id, account_id, strategy_id = portfolio_keys(order, root)
    symbol = str(order["symbol"]).upper()
    quantity = _positive_decimal(order.get("quantity"), "quantity")
    price = _positive_decimal(order.get("limit_price"), "limit_price")
    currency = normalize_currency_code(order.get("currency") or base_currency_for_workspace(root))
    notional = quantize_money(quantity * price, currency)
    side = str(order.get("side") or "").lower()
    if side not in {"buy", "sell"}:
        raise ValueError("side must be buy or sell")

    state: dict[str, Any] | None = None
    last_error: Exception | None = None
    for attempt in range(4):
        try:
            with transaction.atomic():
                row = _locked_state_row(root, portfolio_id, account_id, strategy_id)
                state = _normalize_state(row.payload, portfolio_id, account_id, strategy_id, root)
                current = dict((state.get("positions") or {}).get(symbol) or {
                    "quantity": "0",
                    "average_price": "0",
                    "currency": currency,
                })
                current_currency = str(current.get("currency") or currency).upper()
                if current_currency != currency:
                    raise ValueError(f"position currency mismatch: {current_currency} != {currency}")
                current_quantity = _decimal(current.get("quantity"), "position quantity")
                current_average = _decimal(current.get("average_price"), "position average_price")
                cash = {key.upper(): _decimal(value, f"cash.{key}") for key, value in (state.get("cash") or {}).items()}
                available = cash.get(currency, Decimal("0"))
                if side == "buy":
                    if available < notional:
                        raise ValueError(
                            f"insufficient paper cash in {currency}: required {notional}, available {available}"
                        )
                    next_quantity = current_quantity + quantity
                    average = ((current_quantity * current_average) + notional) / next_quantity
                    cash[currency] = quantize_money(available - notional, currency)
                else:
                    if current_quantity < quantity:
                        raise ValueError(
                            f"insufficient paper position: required {quantity}, available {current_quantity}"
                        )
                    next_quantity = current_quantity - quantity
                    average = Decimal("0") if next_quantity == 0 else current_average
                    cash[currency] = quantize_money(available + notional, currency)
                state["cash"] = {key: _decimal_text(value) for key, value in sorted(cash.items())}
                state["cash_base"] = state["cash"].get(state["base_currency"], "0")
                state.setdefault("positions", {})[symbol] = {
                    "quantity": _decimal_text(next_quantity),
                    "average_price": _decimal_text(average),
                    "currency": currency,
                }
                state["updated_at"] = now_iso()
                expected_version = int(row.version)
                state["version"] = expected_version + 1
                updated = PaperPortfolioState.objects.filter(pk=row.pk, version=expected_version).update(
                    version=expected_version + 1,
                    payload=_storage_state(state),
                )
                if updated != 1:
                    raise PortfolioConcurrencyError("paper portfolio state changed during order execution")
                _write_snapshot(root, state, portfolio_id, account_id, strategy_id, "paper-trading")
            break
        except (OperationalError, PortfolioConcurrencyError) as exc:
            last_error = exc
            if attempt == 3:
                raise PortfolioConcurrencyError("paper portfolio update could not be serialized") from exc
            time.sleep(0.02 * (attempt + 1))
    if state is None:
        raise PortfolioConcurrencyError("paper portfolio update failed") from last_error
    return {
        "adapter": "paper-trading",
        "broker_order_id": f"paper-{order['ticket_id']}",
        "status": "filled",
        "filled_quantity": _decimal_text(quantity),
        "average_price": _decimal_text(price),
        "currency": currency,
        "native_notional": _decimal_text(notional),
        "submitted_at": state["updated_at"],
        "portfolio_id": portfolio_id,
        "account_id": account_id,
        "strategy_id": strategy_id,
    }


def portfolio_keys(args: dict[str, Any], workspace_root: Path | str | None = None) -> tuple[str, str, str]:
    profile = active_profile_for_workspace(workspace_root)
    keys = tuple(str(profile[field]) for field in ("portfolio_id", "account_id", "strategy_id"))
    for field, expected in zip(("portfolio_id", "account_id", "strategy_id"), keys):
        supplied = str(args.get(field) or "")
        if supplied and supplied != expected:
            raise ValueError(f"{field} must match the active workspace profile")
    return keys


def default_paper_portfolio_state(
    portfolio_id: str,
    account_id: str,
    strategy_id: str,
    base_currency: str = DEFAULT_BASE_CURRENCY,
) -> dict[str, Any]:
    base_currency = normalize_currency_code(base_currency, "base_currency")
    return {
        "base_currency": base_currency,
        "cash_base": _decimal_text(DEFAULT_PAPER_CASH),
        "cash": {base_currency: _decimal_text(DEFAULT_PAPER_CASH)},
        "positions": {},
        "updated_at": now_iso(),
        "portfolio_id": portfolio_id,
        "account_id": account_id,
        "strategy_id": strategy_id,
        "source": "central-db",
        "db_canonical": True,
        "version": 1,
    }


def load_paper_portfolio_state(
    workspace_root: Path | str | None,
    portfolio_id: str,
    account_id: str,
    strategy_id: str,
) -> dict[str, Any]:
    ensure_runtime_database(workspace_root)
    from django.db import transaction

    with transaction.atomic():
        row = _locked_state_row(Path(workspace_root or "."), portfolio_id, account_id, strategy_id)
        state = _normalize_state(row.payload, portfolio_id, account_id, strategy_id, workspace_root)
        state["version"] = row.version
        return state


def persist_paper_portfolio_state(
    workspace_root: Path | str | None,
    state: dict[str, Any],
    portfolio_id: str,
    account_id: str,
    strategy_id: str,
    source: str = "paper-trading",
) -> None:
    ensure_runtime_database(workspace_root)
    from django.db import transaction
    from apps.portfolio.models import PaperPortfolioState

    root = Path(workspace_root or ".")
    normalized = _normalize_state(state, portfolio_id, account_id, strategy_id, root)
    with transaction.atomic():
        row = _locked_state_row(root, portfolio_id, account_id, strategy_id)
        expected = state.get("expected_version")
        if expected not in (None, "") and int(expected) != int(row.version):
            raise PortfolioConcurrencyError("paper portfolio compare-and-swap failed")
        next_version = int(row.version) + 1
        normalized["version"] = next_version
        updated = PaperPortfolioState.objects.filter(pk=row.pk, version=row.version).update(
            version=next_version,
            payload=_storage_state(normalized),
        )
        if updated != 1:
            raise PortfolioConcurrencyError("paper portfolio compare-and-swap failed")
        _write_snapshot(root, normalized, portfolio_id, account_id, strategy_id, source)


def _locked_state_row(root: Path, portfolio_id: str, account_id: str, strategy_id: str) -> Any:
    from django.db import IntegrityError, transaction
    from apps.portfolio.models import PaperPortfolioState

    row = PaperPortfolioState.objects.select_for_update().filter(
        portfolio_id=portfolio_id,
        account_id=account_id,
        strategy_id=strategy_id,
    ).first()
    if row is not None:
        return row
    seed = _normalize_state(
        default_paper_portfolio_state(
            portfolio_id,
            account_id,
            strategy_id,
            base_currency_for_workspace(root),
        ),
        portfolio_id,
        account_id,
        strategy_id,
        root,
    )
    try:
        with transaction.atomic():
            return PaperPortfolioState.objects.create(
                portfolio_id=portfolio_id,
                account_id=account_id,
                strategy_id=strategy_id,
                version=int(seed.get("version") or 1),
                payload=_storage_state(seed),
            )
    except IntegrityError:
        return PaperPortfolioState.objects.select_for_update().get(
            portfolio_id=portfolio_id,
            account_id=account_id,
            strategy_id=strategy_id,
        )


def _write_snapshot(
    root: Path,
    state: dict[str, Any],
    portfolio_id: str,
    account_id: str,
    strategy_id: str,
    source: str,
) -> None:
    from apps.portfolio.models import CashBalance, PortfolioSnapshot, Position

    stored = _storage_state(state)
    snapshot = PortfolioSnapshot.objects.create(
        source=source,
        portfolio_id=portfolio_id,
        account_id=account_id,
        strategy_id=strategy_id,
        workspace_context=workspace_context_payload(root),
        payload=stored,
    )
    CashBalance.objects.bulk_create([
        CashBalance(
            snapshot=snapshot,
            currency=currency,
            amount=_decimal(amount, f"cash.{currency}"),
            portfolio_id=portfolio_id,
            account_id=account_id,
            strategy_id=strategy_id,
        )
        for currency, amount in sorted(stored["cash"].items())
    ])
    Position.objects.bulk_create([
        Position(
            snapshot=snapshot,
            symbol=str(symbol).upper(),
            quantity=_decimal(position.get("quantity"), "position quantity"),
            average_price=_decimal(position.get("average_price"), "position average_price"),
            currency=normalize_currency_code(position.get("currency") or base_currency_for_workspace(root)),
            portfolio_id=portfolio_id,
            account_id=account_id,
            strategy_id=strategy_id,
        )
        for symbol, position in sorted(stored["positions"].items())
        if _decimal(position.get("quantity"), "position quantity") != 0
    ])


def _normalize_state(
    state: dict[str, Any],
    portfolio_id: str,
    account_id: str,
    strategy_id: str,
    workspace_root: Path | str | None,
) -> dict[str, Any]:
    if not isinstance(state, dict):
        raise ValueError("paper portfolio state must be an object")
    value = dict(state)
    required = (
        "base_currency",
        "cash",
        "positions",
        "updated_at",
        "portfolio_id",
        "account_id",
        "strategy_id",
        "version",
    )
    missing = [field for field in required if value.get(field) in (None, "")]
    if missing:
        raise ValueError("paper portfolio state is missing: " + ", ".join(missing))
    for field, expected in (
        ("portfolio_id", portfolio_id),
        ("account_id", account_id),
        ("strategy_id", strategy_id),
    ):
        if str(value[field]) != expected:
            raise ValueError(f"paper portfolio state {field} does not match its database scope")
    base_currency = normalize_currency_code(value["base_currency"], "base_currency")
    if base_currency != base_currency_for_workspace(workspace_root):
        raise ValueError("paper portfolio state base_currency does not match the active workspace profile")
    if not isinstance(value["cash"], dict):
        raise ValueError("paper portfolio state cash must be an object")
    if not isinstance(value["positions"], dict):
        raise ValueError("paper portfolio state positions must be an object")
    raw_cash = value["cash"]
    cash = {str(key).upper(): _decimal_text(_decimal(amount, f"cash.{key}")) for key, amount in raw_cash.items()}
    positions: dict[str, dict[str, str]] = {}
    for symbol, raw_position in value["positions"].items():
        if not isinstance(raw_position, dict):
            raise ValueError(f"paper portfolio position {symbol} must be an object")
        missing_position = [
            field
            for field in ("quantity", "average_price", "currency")
            if raw_position.get(field) in (None, "")
        ]
        if missing_position:
            raise ValueError(f"paper portfolio position {symbol} is missing: {', '.join(missing_position)}")
        positions[str(symbol).upper()] = {
            "quantity": _decimal_text(_decimal(raw_position["quantity"], "position quantity")),
            "average_price": _decimal_text(_decimal(raw_position["average_price"], "position average_price")),
            "currency": normalize_currency_code(raw_position["currency"]),
        }
    normalized = {
        "base_currency": base_currency,
        "cash": cash,
        "cash_base": cash.get(base_currency, "0"),
        "positions": positions,
        "updated_at": str(value["updated_at"]),
        "portfolio_id": portfolio_id,
        "account_id": account_id,
        "strategy_id": strategy_id,
        "source": "central-db",
        "db_canonical": True,
        "workspace_context": workspace_context_payload(workspace_root),
        "version": int(value["version"]),
    }
    if value.get("expected_version") not in (None, ""):
        normalized["expected_version"] = int(value["expected_version"])
    return normalized


def _storage_state(state: dict[str, Any]) -> dict[str, Any]:
    value = dict(state)
    value.pop("workspace_context", None)
    value.pop("expected_version", None)
    return value


def _decimal(value: Any, field: str) -> Decimal:
    try:
        number = Decimal(str(value if value not in (None, "") else 0))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{field} must be a finite decimal") from exc
    if not number.is_finite():
        raise ValueError(f"{field} must be a finite decimal")
    return number


def _positive_decimal(value: Any, field: str) -> Decimal:
    number = _decimal(value, field)
    if number <= 0:
        raise ValueError(f"{field} must be positive")
    return number


def currency_quantum(currency: str) -> Decimal:
    normalize_currency_code(currency)
    return INTERNAL_MONEY_QUANTUM


def quantize_money(value: Decimal, currency: str) -> Decimal:
    return value.quantize(currency_quantum(currency), rounding=ROUND_HALF_EVEN)


def _decimal_text(value: Decimal) -> str:
    return format(value, "f")


def list_positions(workspace_root: Path | str) -> dict[str, Any]:
    portfolio_id, account_id, strategy_id = portfolio_keys({}, workspace_root)
    state = load_paper_portfolio_state(Path(workspace_root), portfolio_id, account_id, strategy_id)
    ensure_runtime_database(workspace_root)
    from apps.portfolio.models import BrokerSyncRun, ReconciliationRun

    reconciliation = (
        ReconciliationRun.objects.select_related("broker_connection", "broker_account", "local_snapshot")
        .filter(local_snapshot__portfolio_id=portfolio_id, local_snapshot__account_id=account_id, local_snapshot__strategy_id=strategy_id)
        .order_by("-created_at", "-id")
        .first()
    )
    sync_run = None
    if reconciliation is not None:
        sync_run = (
            BrokerSyncRun.objects.filter(
                broker_connection=reconciliation.broker_connection,
                started_at__lte=reconciliation.created_at,
            )
            .order_by("-started_at", "-id")
            .first()
        )
        state["reconciliation"] = {
            "status": reconciliation.status,
            "diffs": reconciliation.diffs,
            "broker_id": reconciliation.broker_connection.broker_id,
            "broker_account_id": reconciliation.broker_account.broker_account_id if reconciliation.broker_account else "",
            "created_at": reconciliation.created_at.isoformat(),
        }
    if sync_run is not None:
        state["last_sync"] = {
            "status": sync_run.status,
            "broker_id": sync_run.broker_connection.broker_id,
            "started_at": sync_run.started_at.isoformat(),
            "finished_at": sync_run.finished_at.isoformat() if sync_run.finished_at else "",
        }
    state["private_calculation_binding"] = paper_portfolio_state_binding(
        workspace_root
    )
    return state


def paper_portfolio_state_binding(
    workspace_root: Path | str,
    *,
    expected_snapshot_id: str = "",
    expected_snapshot_hash: str = "",
) -> dict[str, str | int]:
    """Return or verify the active DB-canonical private calculation binding.

    Only the opaque state identity, version, and strict content hash leave this
    application boundary. The portfolio payload remains in the central ledger.
    """

    ensure_runtime_database(workspace_root)
    from apps.portfolio.models import PaperPortfolioState

    portfolio_id, account_id, strategy_id = portfolio_keys({}, workspace_root)
    requested_pk = 0
    requested_version = 0
    if expected_snapshot_id:
        match = re.fullmatch(
            r"paper-portfolio-state:([1-9][0-9]*):v([1-9][0-9]*)",
            str(expected_snapshot_id),
        )
        if match is None:
            raise ValueError("private ledger snapshot id is invalid")
        requested_pk = int(match.group(1))
        requested_version = int(match.group(2))
        row = PaperPortfolioState.objects.filter(
            pk=requested_pk,
            portfolio_id=portfolio_id,
            account_id=account_id,
            strategy_id=strategy_id,
        ).first()
    else:
        row = PaperPortfolioState.objects.filter(
            portfolio_id=portfolio_id,
            account_id=account_id,
            strategy_id=strategy_id,
        ).first()
    if row is None:
        raise ValueError("private ledger snapshot is unavailable for the active workspace")
    version = int(row.version)
    snapshot_id = f"paper-portfolio-state:{row.pk}:v{version}"
    if requested_version and requested_version != version:
        raise ValueError("private ledger snapshot version is no longer current")
    normalized = _normalize_state(
        row.payload,
        portfolio_id,
        account_id,
        strategy_id,
        workspace_root,
    )
    normalized["version"] = version
    material = {
        "portfolio_id": portfolio_id,
        "account_id": account_id,
        "strategy_id": strategy_id,
        "state_pk": int(row.pk),
        "version": version,
        "payload": _storage_state(normalized),
    }
    snapshot_hash = content_hash(material)
    if expected_snapshot_hash and expected_snapshot_hash != snapshot_hash:
        raise ValueError("private ledger snapshot hash does not match the central ledger")
    return {
        "ledger_snapshot_id": snapshot_id,
        "ledger_snapshot_hash": snapshot_hash,
        "version": version,
    }

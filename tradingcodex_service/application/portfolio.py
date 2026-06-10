from __future__ import annotations

from pathlib import Path
from typing import Any

from tradingcodex_service.application.common import now_iso
from tradingcodex_service.application.runtime import active_profile_for_workspace, ensure_runtime_database, workspace_context_payload

DEFAULT_PAPER_CASH_KRW = 100_000_000
DEFAULT_PORTFOLIO_ID = "default-paper"
DEFAULT_ACCOUNT_ID = "local-paper"
DEFAULT_STRATEGY_ID = "default-strategy"

def submit_paper_order(root: Path, order: dict[str, Any]) -> dict[str, Any]:
    portfolio_id, account_id, strategy_id = portfolio_keys(order, root)
    state = load_paper_portfolio_state(root, portfolio_id, account_id, strategy_id)
    symbol = str(order["symbol"]).upper()
    quantity = float(order["quantity"])
    price = float(order["limit_price"])
    notional = quantity * price
    current = state.setdefault("positions", {}).get(symbol, {"quantity": 0, "average_price": 0, "currency": order.get("currency", "KRW")})
    if order["side"] == "buy":
        if float(state.get("cash_krw", 0)) < notional:
            raise ValueError(f"insufficient paper cash: required {notional}, available {state.get('cash_krw', 0)}")
        next_quantity = float(current.get("quantity", 0)) + quantity
        current["average_price"] = 0 if next_quantity == 0 else ((float(current.get("quantity", 0)) * float(current.get("average_price", 0))) + notional) / next_quantity
        current["quantity"] = next_quantity
        state["cash_krw"] = float(state.get("cash_krw", 0)) - notional
    else:
        if float(current.get("quantity", 0)) < quantity:
            raise ValueError(f"insufficient paper position: required {quantity}, available {current.get('quantity', 0)}")
        current["quantity"] = float(current.get("quantity", 0)) - quantity
        state["cash_krw"] = float(state.get("cash_krw", 0)) + notional
        if current["quantity"] == 0:
            current["average_price"] = 0
    state["positions"][symbol] = current
    state["updated_at"] = now_iso()
    persist_paper_portfolio_state(root, state, portfolio_id, account_id, strategy_id, source="paper-trading")
    return {
        "adapter": "paper-trading",
        "broker_order_id": f"paper-{order['id']}",
        "status": "filled",
        "filled_quantity": quantity,
        "average_price": price,
        "submitted_at": state["updated_at"],
        "portfolio_id": portfolio_id,
        "account_id": account_id,
        "strategy_id": strategy_id,
    }


def portfolio_keys(args: dict[str, Any], workspace_root: Path | str | None = None) -> tuple[str, str, str]:
    profile = active_profile_for_workspace(workspace_root)
    return (
        str(args.get("portfolio_id") or profile.get("portfolio_id") or DEFAULT_PORTFOLIO_ID),
        str(args.get("account_id") or profile.get("account_id") or DEFAULT_ACCOUNT_ID),
        str(args.get("strategy_id") or profile.get("strategy_id") or DEFAULT_STRATEGY_ID),
    )


def default_paper_portfolio_state(portfolio_id: str = DEFAULT_PORTFOLIO_ID, account_id: str = DEFAULT_ACCOUNT_ID, strategy_id: str = DEFAULT_STRATEGY_ID) -> dict[str, Any]:
    return {
        "cash_krw": DEFAULT_PAPER_CASH_KRW,
        "positions": {},
        "updated_at": now_iso(),
        "portfolio_id": portfolio_id,
        "account_id": account_id,
        "strategy_id": strategy_id,
        "source": "central-db",
        "db_canonical": True,
    }


def load_paper_portfolio_state(
    workspace_root: Path | str | None = None,
    portfolio_id: str = DEFAULT_PORTFOLIO_ID,
    account_id: str = DEFAULT_ACCOUNT_ID,
    strategy_id: str = DEFAULT_STRATEGY_ID,
) -> dict[str, Any]:
    ensure_runtime_database(workspace_root)
    from apps.portfolio.models import PortfolioSnapshot

    snapshot = (
        PortfolioSnapshot.objects.filter(
            portfolio_id=portfolio_id,
            account_id=account_id,
            strategy_id=strategy_id,
            source="paper-trading",
        )
        .order_by("-created_at", "-id")
        .first()
    )
    if snapshot is None:
        state = default_paper_portfolio_state(portfolio_id, account_id, strategy_id)
        state["workspace_context"] = workspace_context_payload(workspace_root)
        persist_paper_portfolio_state(workspace_root, state, portfolio_id, account_id, strategy_id, source="paper-trading")
        return state
    state = dict(snapshot.payload or {})
    state.setdefault("cash_krw", 0)
    state.setdefault("positions", {})
    state.setdefault("updated_at", snapshot.created_at.isoformat())
    state.update({
        "portfolio_id": portfolio_id,
        "account_id": account_id,
        "strategy_id": strategy_id,
        "source": "central-db",
        "db_canonical": True,
        "workspace_context": workspace_context_payload(workspace_root),
    })
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
    from apps.portfolio.models import CashBalance, PortfolioSnapshot, Position

    state = dict(state)
    state.update({
        "portfolio_id": portfolio_id,
        "account_id": account_id,
        "strategy_id": strategy_id,
        "source": "central-db",
        "db_canonical": True,
        "workspace_context": workspace_context_payload(workspace_root),
    })
    snapshot = PortfolioSnapshot.objects.create(
        source=source,
        portfolio_id=portfolio_id,
        account_id=account_id,
        strategy_id=strategy_id,
        workspace_context=workspace_context_payload(workspace_root),
        payload=state,
    )
    CashBalance.objects.create(
        snapshot=snapshot,
        currency="KRW",
        amount=state.get("cash_krw", 0),
        portfolio_id=portfolio_id,
        account_id=account_id,
        strategy_id=strategy_id,
    )
    for symbol, position in sorted((state.get("positions") or {}).items()):
        if float(position.get("quantity", 0)) == 0:
            continue
        Position.objects.create(
            snapshot=snapshot,
            symbol=str(symbol).upper(),
            quantity=position.get("quantity", 0),
            average_price=position.get("average_price", 0),
            currency=position.get("currency") or "KRW",
            portfolio_id=portfolio_id,
            account_id=account_id,
            strategy_id=strategy_id,
        )


def list_positions(workspace_root: Path | str) -> dict[str, Any]:
    portfolio_id, account_id, strategy_id = portfolio_keys({}, workspace_root)
    return load_paper_portfolio_state(Path(workspace_root), portfolio_id, account_id, strategy_id)

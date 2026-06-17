from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from django.utils import timezone as django_timezone

from tradingcodex_service.application.audit import write_audit_event_if_available
from tradingcodex_service.application.common import now_iso, stable_hash
from tradingcodex_service.application.portfolio import (
    DEFAULT_ACCOUNT_ID,
    DEFAULT_PAPER_CASH_KRW,
    DEFAULT_PORTFOLIO_ID,
    DEFAULT_STRATEGY_ID,
    load_paper_portfolio_state,
    portfolio_keys,
)
from tradingcodex_service.application.runtime import ensure_runtime_database, workspace_context_payload


@dataclass(frozen=True)
class BrokerHealth:
    status: str
    message: str = ""
    details: dict[str, Any] | None = None


@dataclass(frozen=True)
class BrokerAccountDTO:
    broker_account_id: str
    account_label: str
    account_type: str = "paper"
    base_currency: str = "KRW"
    masked_identifier: str = "paper"
    trading_enabled: bool = False
    metadata: dict[str, Any] | None = None


@dataclass(frozen=True)
class CashDTO:
    currency: str
    amount: float


@dataclass(frozen=True)
class PositionDTO:
    symbol: str
    quantity: float
    average_price: float = 0
    currency: str = "KRW"
    instrument_id: str = ""


@dataclass(frozen=True)
class BrokerOrderDTO:
    broker_order_id: str
    broker_status: str
    raw: dict[str, Any] | None = None


@dataclass(frozen=True)
class FillDTO:
    fill_id: str
    broker_order_id: str
    quantity: float
    price: float
    currency: str = "KRW"
    fee: float = 0
    filled_at: str = ""


@dataclass(frozen=True)
class OrderValidationResult:
    valid: bool
    reasons: list[str]
    payload: dict[str, Any] | None = None


@dataclass(frozen=True)
class BrokerInstrumentConstraints:
    symbol: str
    asset_class: str
    product_type: str
    quantity_modes: tuple[str, ...]
    order_types: tuple[str, ...]
    time_in_force: tuple[str, ...]
    price_increment: str = ""
    quantity_increment: str = ""
    min_quantity: str = ""
    min_notional: str = ""
    currency: str = ""
    notes: tuple[str, ...] = ()


BLOCKED_BROKER_SURFACES = (
    "withdrawal",
    "transfer",
    "deposit_address",
    "travel_rule",
    "api_key_admin",
    "account_opening",
    "kyc",
    "subaccount_admin",
    "raw_order_submit",
    "raw_order_cancel",
)


BROKER_CONNECTOR_TEMPLATES: dict[str, dict[str, Any]] = {
    "alpaca_rest": {
        "display_name": "Alpaca REST",
        "family": "us_retail_rest",
        "venue": "broker",
        "region": "US",
        "asset_classes": ["equity", "etf", "option", "crypto"],
        "products": ["spot", "option_single", "option_multileg"],
        "environment": "paper",
        "auth_model": {"type": "api_key", "credential_ref_required": True},
        "account_model": {"multi_account": False, "balances": "cash", "positions": True, "buying_power": True},
        "instrument_model": {"identity": "symbol", "examples": ["AAPL", "SPY", "BTC/USD"]},
        "order_model": {
            "sides": ["buy", "sell"],
            "order_types": ["market", "limit", "stop", "stop_limit"],
            "time_in_force": ["day", "gtc", "ioc", "fok"],
            "quantity_modes": ["quantity", "notional"],
            "features": ["fractional_equity", "bracket", "oco", "oto"],
        },
        "validation_model": {"preview": False, "dry_run": False, "broker_validate": True},
        "event_model": {"polling": True, "streaming": True, "fills": True},
        "rate_limits": [{"scope": "account", "policy": "broker documented"}],
        "execution_posture": "live_disabled",
    },
    "tradier_rest": {
        "display_name": "Tradier REST",
        "family": "us_retail_rest",
        "venue": "broker",
        "region": "US",
        "asset_classes": ["equity", "etf", "option"],
        "products": ["spot", "option_single", "option_multileg"],
        "environment": "sandbox",
        "auth_model": {"type": "oauth_or_bearer", "credential_ref_required": True},
        "account_model": {"multi_account": True, "balances": "cash", "positions": True},
        "instrument_model": {"identity": "symbol_or_option_symbol", "examples": ["AAPL", "SPY250117C00450000"]},
        "order_model": {"sides": ["buy", "sell"], "order_types": ["market", "limit", "stop", "stop_limit"], "time_in_force": ["day", "gtc"], "quantity_modes": ["quantity"]},
        "validation_model": {"preview": True, "dry_run": False},
        "event_model": {"polling": True, "streaming": True},
        "rate_limits": [{"scope": "account", "policy": "broker documented"}],
        "execution_posture": "live_disabled",
    },
    "ibkr_gateway": {
        "display_name": "IBKR Gateway",
        "family": "multi_asset_gateway",
        "venue": "broker",
        "region": "global",
        "asset_classes": ["equity", "etf", "option", "future", "forex", "bond", "fund"],
        "products": ["spot", "option_single", "option_multileg", "future", "forex", "fixed_income", "mutual_fund"],
        "environment": "live",
        "auth_model": {"type": "oauth_or_gateway_session", "credential_ref_required": True},
        "account_model": {"multi_account": True, "balances": "cash_margin", "positions": True, "portfolio": True},
        "instrument_model": {"identity": "conid", "examples": ["265598", "AAPL conid"]},
        "order_model": {"sides": ["buy", "sell"], "order_types": ["market", "limit", "stop", "stop_limit"], "time_in_force": ["day", "gtc"], "quantity_modes": ["quantity", "contracts"]},
        "validation_model": {"preview": True, "dry_run": False, "broker_confirmation": True},
        "event_model": {"polling": True, "streaming": True},
        "rate_limits": [{"scope": "gateway_session", "policy": "pacing sensitive"}],
        "execution_posture": "service_adapter_required",
    },
    "tastytrade_openapi": {
        "display_name": "tastytrade Open API",
        "family": "options_futures_specialist",
        "venue": "broker",
        "region": "US",
        "asset_classes": ["equity", "etf", "option", "future", "crypto"],
        "products": ["spot", "option_single", "option_multileg", "future"],
        "environment": "sandbox",
        "auth_model": {"type": "session_token", "credential_ref_required": True},
        "account_model": {"multi_account": True, "balances": "cash_margin", "positions": True, "buying_power": True},
        "instrument_model": {"identity": "tastytrade_symbol", "examples": ["AAPL", ".AAPL250117C450"]},
        "order_model": {"sides": ["buy", "sell"], "order_types": ["market", "limit", "stop"], "time_in_force": ["day", "gtc"], "quantity_modes": ["quantity", "contracts"], "features": ["complex_orders"]},
        "validation_model": {"preview": True, "dry_run": True, "buying_power_effect": True},
        "event_model": {"polling": True, "streaming": True},
        "rate_limits": [{"scope": "session", "policy": "broker documented"}],
        "execution_posture": "live_disabled",
    },
    "kis_openapi": {
        "display_name": "Korea Investment Open API",
        "family": "korean_securities",
        "venue": "broker",
        "region": "KR",
        "asset_classes": ["equity", "etf", "cash"],
        "products": ["domestic_stock", "overseas_stock"],
        "environment": "mock",
        "auth_model": {"type": "app_key_secret_tr_id", "credential_ref_required": True},
        "account_model": {"multi_account": True, "balances": "cash", "positions": True, "account_product_code": True},
        "instrument_model": {"identity": "market_code", "examples": ["005930", "AAPL"]},
        "order_model": {"sides": ["buy", "sell"], "order_types": ["market", "limit"], "time_in_force": ["day"], "quantity_modes": ["quantity"]},
        "validation_model": {"preview": False, "dry_run": False, "tr_id_required": True},
        "event_model": {"polling": True, "streaming": True},
        "rate_limits": [{"scope": "app_key", "policy": "broker documented"}],
        "execution_posture": "live_disabled",
    },
    "binance_spot": {
        "display_name": "Binance Spot",
        "family": "crypto_exchange",
        "venue": "crypto_exchange",
        "region": "global",
        "asset_classes": ["crypto", "cash"],
        "products": ["spot"],
        "environment": "testnet",
        "auth_model": {"type": "hmac_or_rsa_or_ed25519", "credential_ref_required": True},
        "account_model": {"multi_account": False, "balances": "free_locked", "positions": False},
        "instrument_model": {"identity": "symbol", "examples": ["BTCUSDT", "ETHBTC"], "filters": ["PRICE_FILTER", "LOT_SIZE", "MIN_NOTIONAL", "NOTIONAL"]},
        "order_model": {"sides": ["buy", "sell"], "order_types": ["market", "limit", "stop_loss", "take_profit"], "time_in_force": ["GTC", "IOC", "FOK"], "quantity_modes": ["quantity", "quote_notional"], "features": ["stp", "iceberg", "oco"]},
        "validation_model": {"preview": True, "dry_run": True, "endpoint": "/api/v3/order/test", "rest_base_url": "https://testnet.binance.vision"},
        "event_model": {"polling": True, "streaming": True, "user_data_stream": True},
        "rate_limits": [{"scope": "ip_or_account", "policy": "request weight and order count"}],
        "execution_posture": "broker_validation_only",
    },
    "binance_usdm_futures": {
        "display_name": "Binance USD-M Futures",
        "family": "crypto_exchange",
        "venue": "crypto_exchange",
        "region": "global",
        "asset_classes": ["crypto"],
        "products": ["perpetual", "future"],
        "environment": "testnet",
        "auth_model": {"type": "hmac", "credential_ref_required": True},
        "account_model": {"multi_account": False, "balances": "margin", "positions": True, "position_mode": True},
        "instrument_model": {"identity": "symbol", "examples": ["BTCUSDT"], "filters": ["PRICE_FILTER", "LOT_SIZE", "MARKET_LOT_SIZE"]},
        "order_model": {"sides": ["buy", "sell"], "order_types": ["market", "limit", "stop", "take_profit", "trailing_stop_market"], "time_in_force": ["GTC", "IOC", "FOK", "GTX"], "quantity_modes": ["contracts"], "features": ["reduce_only", "position_side", "leverage", "margin_mode"]},
        "validation_model": {"preview": True, "dry_run": True},
        "event_model": {"polling": True, "streaming": True, "user_data_stream": True},
        "rate_limits": [{"scope": "account", "policy": "order count and request weight"}],
        "execution_posture": "live_disabled",
    },
    "upbit_spot_kr": {
        "display_name": "Upbit Spot KR",
        "family": "crypto_exchange",
        "venue": "crypto_exchange",
        "region": "KR",
        "asset_classes": ["crypto", "cash"],
        "products": ["spot"],
        "environment": "live",
        "auth_model": {"type": "jwt_query_hash", "credential_ref_required": True},
        "account_model": {"multi_account": False, "balances": "free_locked", "positions": False},
        "instrument_model": {"identity": "market", "examples": ["KRW-BTC", "BTC-ETH"]},
        "order_model": {"sides": ["bid", "ask"], "order_types": ["limit", "price", "market", "best"], "time_in_force": ["ioc", "fok", "post_only"], "quantity_modes": ["quantity", "quote_notional"], "features": ["smp"]},
        "validation_model": {"preview": True, "dry_run": True, "endpoints": ["/v1/orders/chance", "/v1/orders/test"]},
        "event_model": {"polling": True, "streaming": True, "private_streams": ["myOrder", "myAsset"]},
        "rate_limits": [{"scope": "account", "policy": "per-second groups"}],
        "execution_posture": "live_disabled",
    },
    "upbit_spot_global": {
        "display_name": "Upbit Spot Global",
        "family": "crypto_exchange",
        "venue": "crypto_exchange",
        "region": "SG_ID_TH",
        "asset_classes": ["crypto", "cash"],
        "products": ["spot"],
        "environment": "live",
        "auth_model": {"type": "jwt_query_hash", "credential_ref_required": True},
        "account_model": {"multi_account": False, "balances": "free_locked", "positions": False},
        "instrument_model": {"identity": "market", "examples": ["SGD-BTC", "USDT-BTC"]},
        "order_model": {"sides": ["bid", "ask"], "order_types": ["limit", "price", "market", "best"], "time_in_force": ["ioc", "fok", "post_only"], "quantity_modes": ["quantity", "quote_notional"], "features": ["smp"]},
        "validation_model": {"preview": True, "dry_run": True, "endpoints": ["/v1/orders/chance", "/v1/orders/test"]},
        "event_model": {"polling": True, "streaming": True, "private_streams": ["myOrder", "myAsset"]},
        "rate_limits": [{"scope": "account_or_ip", "policy": "per-second groups"}],
        "execution_posture": "live_disabled",
    },
    "oanda_v20": {
        "display_name": "OANDA v20",
        "family": "fx_cfd",
        "venue": "fx_dealer",
        "region": "global",
        "asset_classes": ["forex", "cfd", "cash"],
        "products": ["forex", "cfd"],
        "environment": "practice",
        "auth_model": {"type": "bearer_token", "credential_ref_required": True},
        "account_model": {"multi_account": True, "balances": "margin", "positions": True},
        "instrument_model": {"identity": "instrument", "examples": ["EUR_USD", "GBP_JPY"]},
        "order_model": {"sides": ["buy", "sell"], "order_types": ["market", "limit", "stop", "market_if_touched"], "time_in_force": ["GTC", "GTD", "FOK", "IOC"], "quantity_modes": ["units", "notional"], "features": ["trigger_condition"]},
        "validation_model": {"preview": True, "dry_run": False, "margin_check": True},
        "event_model": {"polling": True, "streaming": True},
        "rate_limits": [{"scope": "account", "policy": "broker documented"}],
        "execution_posture": "live_disabled",
    },
    "mt5_terminal": {
        "display_name": "MetaTrader 5 Terminal",
        "family": "terminal_bridge",
        "venue": "broker_terminal",
        "region": "broker_specific",
        "asset_classes": ["forex", "cfd", "future", "equity"],
        "products": ["forex", "cfd", "future", "spot"],
        "environment": "terminal",
        "auth_model": {"type": "local_terminal_session", "credential_ref_required": False},
        "account_model": {"multi_account": False, "balances": "margin", "positions": True},
        "instrument_model": {"identity": "terminal_symbol", "examples": ["EURUSD", "XAUUSD"]},
        "order_model": {"sides": ["buy", "sell"], "order_types": ["market", "limit", "stop", "stop_limit"], "time_in_force": ["day", "gtc"], "quantity_modes": ["lots"], "features": ["order_check"]},
        "validation_model": {"preview": True, "dry_run": True, "terminal_order_check": True},
        "event_model": {"polling": True, "streaming": False},
        "rate_limits": [{"scope": "terminal", "policy": "broker dependent"}],
        "execution_posture": "live_disabled",
    },
}


class BrokerAdapter:
    adapter_type = "base"

    def describe_capabilities(self) -> dict[str, Any]:
        return {}

    def discover_instruments(self, query: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        return []

    def get_instrument_constraints(self, symbol: str, args: dict[str, Any] | None = None) -> BrokerInstrumentConstraints:
        profile = self.describe_capabilities()
        order_model = profile.get("order_model") if isinstance(profile.get("order_model"), dict) else {}
        asset_class = str((profile.get("asset_classes") or ["unknown"])[0])
        product_type = str((profile.get("products") or ["spot"])[0])
        return BrokerInstrumentConstraints(
            symbol=symbol,
            asset_class=asset_class,
            product_type=product_type,
            quantity_modes=tuple(order_model.get("quantity_modes") or ["quantity"]),
            order_types=tuple(order_model.get("order_types") or ["market", "limit"]),
            time_in_force=tuple(order_model.get("time_in_force") or ["day"]),
        )

    def health_check(self) -> BrokerHealth:
        raise NotImplementedError

    def discover_accounts(self) -> list[BrokerAccountDTO]:
        raise NotImplementedError

    def get_cash(self, account_id: str) -> list[CashDTO]:
        raise NotImplementedError

    def get_positions(self, account_id: str) -> list[PositionDTO]:
        raise NotImplementedError

    def get_orders(self, account_id: str) -> list[BrokerOrderDTO]:
        return []

    def get_fills(self, account_id: str) -> list[FillDTO]:
        return []

    def validate_order(self, order: dict[str, Any]) -> OrderValidationResult:
        return OrderValidationResult(True, [])

    def validate_order_translation(self, order: dict[str, Any]) -> OrderValidationResult:
        return self.validate_order(order)

    def preview_order(self, order: dict[str, Any]) -> dict[str, Any]:
        validation = self.validate_order_translation(order)
        return {"valid": validation.valid, "reasons": validation.reasons, "payload": validation.payload or {}}

    def submit_order(self, order: dict[str, Any]) -> dict[str, Any]:
        raise ValueError("adapter does not support submit_order")

    def cancel_order(self, broker_order_id: str) -> dict[str, Any]:
        return {"status": "not_supported", "broker_order_id": broker_order_id}

    def get_order_status(self, broker_order_id: str) -> dict[str, Any]:
        return {"status": "local-only", "broker_order_id": broker_order_id}


BROKER_VALIDATION_EXECUTION_POSTURES = {"broker_validation_only", "testnet_order_test"}
NON_LIVE_EXECUTION_POSTURES = {"paper_only", *BROKER_VALIDATION_EXECUTION_POSTURES}


class PaperBrokerAdapter(BrokerAdapter):
    adapter_type = "paper"

    def __init__(self, workspace_root: Path | str | None = None) -> None:
        self.workspace_root = Path(workspace_root or ".").resolve()

    def health_check(self) -> BrokerHealth:
        return BrokerHealth("ok", "local paper broker adapter is available")

    def describe_capabilities(self) -> dict[str, Any]:
        return _profile_for_template("paper", "paper-trading", "paper", "KR", credential_ref="")

    def discover_accounts(self) -> list[BrokerAccountDTO]:
        return [
            BrokerAccountDTO(
                broker_account_id=DEFAULT_ACCOUNT_ID,
                account_label="Local Paper Account",
                account_type="paper",
                base_currency="KRW",
                masked_identifier="paper",
                trading_enabled=True,
                metadata={"default_cash_krw": DEFAULT_PAPER_CASH_KRW},
            )
        ]

    def get_cash(self, account_id: str) -> list[CashDTO]:
        portfolio_id, _, strategy_id = portfolio_keys({"account_id": account_id}, self.workspace_root)
        state = load_paper_portfolio_state(self.workspace_root, portfolio_id, account_id, strategy_id)
        return [CashDTO(currency="KRW", amount=float(state.get("cash_krw", 0)))]

    def get_positions(self, account_id: str) -> list[PositionDTO]:
        portfolio_id, _, strategy_id = portfolio_keys({"account_id": account_id}, self.workspace_root)
        state = load_paper_portfolio_state(self.workspace_root, portfolio_id, account_id, strategy_id)
        positions = state.get("positions") if isinstance(state.get("positions"), dict) else {}
        return [
            PositionDTO(
                symbol=str(symbol).upper(),
                quantity=float(position.get("quantity", 0)),
                average_price=float(position.get("average_price", 0)),
                currency=str(position.get("currency") or "KRW"),
                instrument_id=str(position.get("instrument_id") or symbol).upper(),
            )
            for symbol, position in sorted(positions.items())
            if float(position.get("quantity", 0)) != 0
        ]

    def validate_order(self, order: dict[str, Any]) -> OrderValidationResult:
        reasons: list[str] = []
        side = str(order.get("side") or "").lower()
        if side not in {"buy", "sell"}:
            reasons.append("side must be buy or sell")
        quantity = _float(order.get("quantity"))
        price = _float(order.get("limit_price") or order.get("estimated_price"))
        if quantity is None or quantity <= 0:
            reasons.append("quantity must be positive")
        if price is None or price <= 0:
            reasons.append("limit_price must be positive")
        if side == "buy" and quantity and price:
            cash = sum(item.amount for item in self.get_cash(str(order.get("account_id") or DEFAULT_ACCOUNT_ID)))
            if cash < quantity * price:
                reasons.append(f"insufficient paper cash: required {quantity * price}, available {cash}")
        if side == "sell" and quantity:
            symbol = str(order.get("symbol") or "").upper()
            available = next((item.quantity for item in self.get_positions(str(order.get("account_id") or DEFAULT_ACCOUNT_ID)) if item.symbol == symbol), 0)
            if available < quantity:
                reasons.append(f"insufficient paper position: required {quantity}, available {available}")
        return OrderValidationResult(not reasons, reasons, {"adapter": "paper-trading"})

    def submit_order(self, order: dict[str, Any]) -> dict[str, Any]:
        from tradingcodex_service.application.portfolio import submit_paper_order

        return submit_paper_order(self.workspace_root, order)


class ExternalMcpBrokerAdapter(BrokerAdapter):
    adapter_type = "external_mcp"

    def __init__(self, connection: Any) -> None:
        self.connection = connection

    def health_check(self) -> BrokerHealth:
        status = "ok" if self.connection.status in {"read_only", "trading_locked", "trading_enabled"} else "disabled"
        return BrokerHealth(status, "external MCP broker is manifest-backed; raw execution proxy is not available")

    def discover_accounts(self) -> list[BrokerAccountDTO]:
        accounts = self.connection.metadata.get("accounts") if isinstance(self.connection.metadata, dict) else None
        if not isinstance(accounts, list):
            return []
        return [
            BrokerAccountDTO(
                broker_account_id=str(item.get("broker_account_id") or item.get("id") or ""),
                account_label=str(item.get("account_label") or item.get("label") or ""),
                account_type=str(item.get("account_type") or "brokerage"),
                base_currency=str(item.get("base_currency") or "USD"),
                masked_identifier=str(item.get("masked_identifier") or ""),
                trading_enabled=False,
                metadata=item if isinstance(item, dict) else {},
            )
            for item in accounts
            if isinstance(item, dict) and (item.get("broker_account_id") or item.get("id"))
        ]

    def get_cash(self, account_id: str) -> list[CashDTO]:
        return []

    def get_positions(self, account_id: str) -> list[PositionDTO]:
        return []


class NativeApiBrokerAdapter(BrokerAdapter):
    adapter_type = "native_api"

    def __init__(self, connection: Any) -> None:
        self.connection = connection

    def health_check(self) -> BrokerHealth:
        return BrokerHealth("disabled", "native API broker adapters are manifest-backed and disabled until reviewed")

    def describe_capabilities(self) -> dict[str, Any]:
        metadata = self.connection.metadata if isinstance(self.connection.metadata, dict) else {}
        profile = metadata.get("capability_profile")
        return profile if isinstance(profile, dict) else {}

    def discover_accounts(self) -> list[BrokerAccountDTO]:
        return []

    def get_cash(self, account_id: str) -> list[CashDTO]:
        return []

    def get_positions(self, account_id: str) -> list[PositionDTO]:
        return []

    def get_instrument_constraints(self, symbol: str, args: dict[str, Any] | None = None) -> BrokerInstrumentConstraints:
        return _constraints_from_profile(self.describe_capabilities(), symbol, args or {})

    def validate_order_translation(self, order: dict[str, Any]) -> OrderValidationResult:
        profile = self.describe_capabilities()
        reasons = _translation_reasons(profile, order)
        payload = {
            "adapter": self.connection.adapter_type,
            "broker_id": self.connection.broker_id,
            "canonical_order_v2": canonical_order_v2_from_order(order, profile),
            "broker_payload_preview": _broker_payload_preview(profile, order),
            "execution_posture": profile.get("execution_posture") or "live_disabled",
        }
        return OrderValidationResult(not reasons, reasons, payload)

    def preview_order(self, order: dict[str, Any]) -> dict[str, Any]:
        validation = self.validate_order_translation(order)
        return {
            "status": "previewed" if validation.valid else "rejected",
            "valid": validation.valid,
            "reasons": validation.reasons,
            "translation": validation.payload or {},
        }


class BinanceSpotTestnetAdapter(NativeApiBrokerAdapter):
    adapter_type = "binance_spot_testnet"

    def health_check(self) -> BrokerHealth:
        try:
            self._public_request("GET", "/api/v3/time", {})
            credentials = self._credentials()
            if not credentials["available"]:
                return BrokerHealth("warning", credentials["reason"], _binance_health_details(credentials["reason"]))
            self._signed_request("GET", "/api/v3/account", {"omitZeroBalances": "true"})
            return BrokerHealth(
                "ok",
                "Binance Spot Testnet REST API reachable; signed account check succeeded",
                {
                    "code": "binance_signed_account_ok",
                    "endpoint": "/api/v3/account",
                    "testnet": True,
                    "execution_posture": "broker_validation_only",
                },
            )
        except Exception as exc:
            message = _safe_error(exc)
            return BrokerHealth("error", message, _binance_health_details(message))

    def discover_accounts(self) -> list[BrokerAccountDTO]:
        account = self._signed_request("GET", "/api/v3/account", {"omitZeroBalances": "true"})
        balances = account.get("balances") if isinstance(account.get("balances"), list) else []
        nonzero = [
            item
            for item in balances
            if _float(item.get("free")) or _float(item.get("locked"))
        ]
        return [
            BrokerAccountDTO(
                broker_account_id="spot-testnet",
                account_label="Binance Spot Testnet",
                account_type=str(account.get("accountType") or "spot_testnet").lower(),
                base_currency="USDT",
                masked_identifier=_mask_ref(self.connection.credential_ref),
                trading_enabled=self.connection.status == "trading_enabled",
                metadata={
                    "permissions": account.get("permissions") or [],
                    "balances_returned": len(balances),
                    "nonzero_balances": len(nonzero),
                    "can_trade": bool(account.get("canTrade", False)),
                    "source": "binance_spot_testnet",
                },
            )
        ]

    def get_cash(self, account_id: str) -> list[CashDTO]:
        account = self._signed_request("GET", "/api/v3/account", {"omitZeroBalances": "true"})
        balances = account.get("balances") if isinstance(account.get("balances"), list) else []
        cash = []
        for item in balances:
            asset = str(item.get("asset") or "").upper()
            free = _float(item.get("free")) or 0
            locked = _float(item.get("locked")) or 0
            amount = free + locked
            if asset and amount:
                cash.append(CashDTO(currency=asset, amount=amount))
        return cash

    def get_positions(self, account_id: str) -> list[PositionDTO]:
        return []

    def get_instrument_constraints(self, symbol: str, args: dict[str, Any] | None = None) -> BrokerInstrumentConstraints:
        normalized = _binance_symbol(symbol)
        exchange_info = self._public_request("GET", "/api/v3/exchangeInfo", {"symbol": normalized})
        symbols = exchange_info.get("symbols") if isinstance(exchange_info.get("symbols"), list) else []
        data = symbols[0] if symbols else {}
        filters = {item.get("filterType"): item for item in data.get("filters", []) if isinstance(item, dict)}
        lot = filters.get("LOT_SIZE") or {}
        price = filters.get("PRICE_FILTER") or {}
        min_notional = filters.get("MIN_NOTIONAL") or filters.get("NOTIONAL") or {}
        return BrokerInstrumentConstraints(
            symbol=normalized,
            asset_class="crypto",
            product_type="spot",
            quantity_modes=("quantity", "quote_notional"),
            order_types=tuple(data.get("orderTypes") or ("LIMIT", "MARKET")),
            time_in_force=tuple(data.get("timeInForce") or ("GTC", "IOC", "FOK")),
            price_increment=str(price.get("tickSize") or ""),
            quantity_increment=str(lot.get("stepSize") or ""),
            min_quantity=str(lot.get("minQty") or ""),
            min_notional=str(min_notional.get("minNotional") or min_notional.get("notional") or ""),
            currency=str(data.get("quoteAsset") or "USDT"),
            notes=("Binance Spot Testnet /api endpoints only", "SIGNED order validation uses /api/v3/order/test"),
        )

    def validate_order_translation(self, order: dict[str, Any]) -> OrderValidationResult:
        reasons = _translation_reasons(self.describe_capabilities(), order)
        payload = self._broker_order_payload(order)
        if not reasons:
            try:
                self._signed_request("POST", "/api/v3/order/test", payload)
            except Exception as exc:
                reasons.append(f"binance order-test rejected: {_safe_error(exc)}")
        return OrderValidationResult(
            not reasons,
            reasons,
            {
                "adapter": self.adapter_type,
                "broker_id": self.connection.broker_id,
                "testnet": True,
                "order_test_endpoint": "/api/v3/order/test",
                "canonical_order_v2": canonical_order_v2_from_order(order, self.describe_capabilities()),
                "broker_payload_preview": _redact_binance_payload(payload),
                "execution_posture": self.describe_capabilities().get("execution_posture") or "broker_validation_only",
            },
        )

    def preview_order(self, order: dict[str, Any]) -> dict[str, Any]:
        validation = self.validate_order_translation(order)
        return {
            "status": "validated_on_testnet" if validation.valid else "rejected",
            "valid": validation.valid,
            "reasons": validation.reasons,
            "translation": validation.payload or {},
        }

    def submit_order(self, order: dict[str, Any]) -> dict[str, Any]:
        payload = self._broker_order_payload(order)
        mode = os.environ.get("TRADINGCODEX_BINANCE_TESTNET_SUBMIT_MODE", "order_test").strip().lower()
        if mode not in {"order_test", "place_order"}:
            raise ValueError("TRADINGCODEX_BINANCE_TESTNET_SUBMIT_MODE must be order_test or place_order")
        if mode == "place_order" and os.environ.get("TRADINGCODEX_ENABLE_BINANCE_TESTNET_PLACE_ORDER", "").lower() not in {"1", "true", "yes", "on"}:
            raise ValueError("Binance testnet place_order requires TRADINGCODEX_ENABLE_BINANCE_TESTNET_PLACE_ORDER=1")
        if mode == "place_order":
            response = self._signed_request("POST", "/api/v3/order", payload)
            broker_order_id = str(response.get("orderId") or payload.get("newClientOrderId"))
            self._remember_order_submit(payload, "place_order")
            return {
                "adapter": self.adapter_type,
                "broker_order_id": broker_order_id,
                "client_order_id": payload.get("newClientOrderId"),
                "status": str(response.get("status") or "submitted").lower(),
                "submitted_at": now_iso(),
                "testnet": True,
                "mode": "place_order",
                "raw_status": _redact_binance_response(response),
            }
        self._signed_request("POST", "/api/v3/order/test", payload)
        self._remember_order_submit(payload, "order_test")
        return {
            "adapter": self.adapter_type,
            "broker_order_id": str(payload.get("newClientOrderId")),
            "client_order_id": payload.get("newClientOrderId"),
            "status": "validated",
            "submitted_at": now_iso(),
            "testnet": True,
            "mode": "order_test",
            "raw_status": {"endpoint": "/api/v3/order/test", "response": "empty_success"},
        }

    def get_order_status(self, broker_order_id: str) -> dict[str, Any]:
        if not broker_order_id:
            return {"status": "unknown", "reason": "broker_order_id required"}
        metadata = self.connection.metadata if isinstance(self.connection.metadata, dict) else {}
        order_test_ids = metadata.get("order_test_client_order_ids") if isinstance(metadata.get("order_test_client_order_ids"), list) else []
        if broker_order_id in order_test_ids:
            return {
                "status": "validated",
                "reason": "Binance /api/v3/order/test validates parameters and signature but does not create an exchange order.",
                "testnet": True,
                "mode": "order_test",
            }
        symbols = metadata.get("client_order_symbols") if isinstance(metadata.get("client_order_symbols"), dict) else {}
        symbol = str(symbols.get(broker_order_id) or metadata.get("last_order_symbol") or "")
        if not symbol:
            return {"status": "unknown", "reason": "symbol is required for Binance order status"}
        response = self._signed_request("GET", "/api/v3/order", {"symbol": symbol, "origClientOrderId": broker_order_id})
        return {"status": str(response.get("status") or "unknown").lower(), "raw_status": _redact_binance_response(response)}

    def _broker_order_payload(self, order: dict[str, Any]) -> dict[str, Any]:
        symbol = _binance_symbol(str(order.get("venue_symbol") or order.get("market") or order.get("symbol") or ""))
        if not symbol:
            raise ValueError("Binance order requires symbol")
        order_type = _binance_order_type(str(order.get("order_type") or "limit"))
        side = str(order.get("side") or "").upper()
        if side not in {"BUY", "SELL"}:
            raise ValueError("Binance order side must be buy or sell")
        payload: dict[str, Any] = {
            "symbol": symbol,
            "side": side,
            "type": order_type,
            "newClientOrderId": _binance_client_order_id(order),
        }
        quote_notional = order.get("quote_notional")
        if quote_notional not in (None, "") and str(order.get("quantity_mode") or "") == "quote_notional":
            payload["quoteOrderQty"] = _decimal_string(quote_notional)
        else:
            payload["quantity"] = _decimal_string(order.get("quantity"))
        if order_type in {"LIMIT", "STOP_LOSS_LIMIT", "TAKE_PROFIT_LIMIT", "LIMIT_MAKER"}:
            price = order.get("limit_price")
            if price in (None, ""):
                raise ValueError("Binance limit order requires limit_price")
            payload["price"] = _decimal_string(price)
            if order_type != "LIMIT_MAKER":
                payload["timeInForce"] = _binance_time_in_force(str(order.get("time_in_force") or "GTC"))
        if order_type in {"STOP_LOSS", "STOP_LOSS_LIMIT", "TAKE_PROFIT", "TAKE_PROFIT_LIMIT"} and order.get("stop_price") not in (None, ""):
            payload["stopPrice"] = _decimal_string(order.get("stop_price"))
        return payload

    def _credentials(self) -> dict[str, Any]:
        return _resolve_hmac_credentials(self.connection.credential_ref)

    def _signed_request(self, method: str, path: str, params: dict[str, Any]) -> dict[str, Any]:
        credentials = self._credentials()
        if not credentials["available"]:
            raise ValueError(credentials["reason"])
        payload = {key: value for key, value in params.items() if value not in (None, "")}
        payload.setdefault("recvWindow", "5000")
        payload["timestamp"] = int(time.time() * 1000)
        query = urlencode(payload)
        signature = hmac.new(str(credentials["secret_key"]).encode("utf-8"), query.encode("utf-8"), hashlib.sha256).hexdigest()
        return self._http_request(method, path, f"{query}&signature={signature}", {"X-MBX-APIKEY": str(credentials["api_key"])})

    def _public_request(self, method: str, path: str, params: dict[str, Any]) -> dict[str, Any]:
        query = urlencode({key: value for key, value in params.items() if value not in (None, "")})
        return self._http_request(method, path, query, {})

    def _http_request(self, method: str, path: str, query: str, headers: dict[str, str]) -> dict[str, Any]:
        base_url = str(self.describe_capabilities().get("validation_model", {}).get("rest_base_url") or "https://testnet.binance.vision")
        return _binance_http_request(method, base_url, path, query, headers)

    def _remember_order_submit(self, payload: dict[str, Any], mode: str) -> None:
        client_order_id = str(payload.get("newClientOrderId") or "")
        symbol = str(payload.get("symbol") or "")
        if not client_order_id:
            return
        metadata = dict(self.connection.metadata or {})
        symbols = dict(metadata.get("client_order_symbols") or {})
        if symbol:
            symbols[client_order_id] = symbol
            metadata["client_order_symbols"] = symbols
            metadata["last_order_symbol"] = symbol
        metadata["last_client_order_id"] = client_order_id
        metadata["last_submit_mode"] = mode
        if mode == "order_test":
            order_test_ids = list(metadata.get("order_test_client_order_ids") or [])
            order_test_ids = [item for item in order_test_ids if item != client_order_id]
            order_test_ids.append(client_order_id)
            metadata["order_test_client_order_ids"] = order_test_ids[-50:]
        self.connection.metadata = metadata
        try:
            self.connection.save(update_fields=["metadata", "updated_at"])
        except Exception:
            self.connection.save(update_fields=["metadata"])


class ManualBrokerAdapter(BrokerAdapter):
    adapter_type = "manual"

    def health_check(self) -> BrokerHealth:
        return BrokerHealth("ok", "manual broker adapter is read-only and import-backed")

    def discover_accounts(self) -> list[BrokerAccountDTO]:
        return []

    def get_cash(self, account_id: str) -> list[CashDTO]:
        return []

    def get_positions(self, account_id: str) -> list[PositionDTO]:
        return []


def adapter_for_connection(connection: Any, workspace_root: Path | str | None = None) -> BrokerAdapter:
    if connection.adapter_type == "paper" or connection.transport == "paper" or connection.broker_id == "paper-trading":
        return PaperBrokerAdapter(workspace_root)
    if connection.transport == "mcp":
        return ExternalMcpBrokerAdapter(connection)
    metadata = connection.metadata if isinstance(connection.metadata, dict) else {}
    profile = metadata.get("capability_profile") if isinstance(metadata.get("capability_profile"), dict) else {}
    if connection.transport == "api" and profile.get("template_id") == "binance_spot" and profile.get("environment") == "testnet":
        return BinanceSpotTestnetAdapter(connection)
    if connection.transport == "api" or connection.adapter_type == "native_api":
        return NativeApiBrokerAdapter(connection)
    if connection.transport == "manual" or connection.adapter_type == "manual":
        return ManualBrokerAdapter()
    raise ValueError(f"Unsupported broker adapter type: {connection.adapter_type}")


def list_broker_connector_templates(workspace_root: Path | str | None = None, args: dict[str, Any] | None = None) -> dict[str, Any]:
    args = args or {}
    family = str(args.get("family") or "")
    asset_class = str(args.get("asset_class") or args.get("asset") or "")
    templates = []
    for template_id, template in sorted(BROKER_CONNECTOR_TEMPLATES.items()):
        if family and template.get("family") != family:
            continue
        if asset_class and asset_class not in template.get("asset_classes", []):
            continue
        templates.append(_template_summary(template_id, template))
    return {
        "templates": templates,
        "blocked_surfaces": list(BLOCKED_BROKER_SURFACES),
        "native_profiles": True,
        "db_canonical": True,
        "workspace_context": workspace_context_payload(workspace_root),
    }


def register_broker_connector(workspace_root: Path | str | None, args: dict[str, Any]) -> dict[str, Any]:
    ensure_runtime_database(workspace_root)
    from apps.integrations.models import BrokerConnection

    template_id = str(args.get("template_id") or args.get("template") or "").strip()
    if template_id not in BROKER_CONNECTOR_TEMPLATES:
        raise ValueError(f"unknown broker connector template: {template_id}")
    broker_id = str(args.get("broker_id") or args.get("broker_connection_id") or template_id).strip()
    if not broker_id:
        raise ValueError("broker_id is required")
    credential_ref = str(args.get("credential_ref") or "")
    environment = str(args.get("environment") or BROKER_CONNECTOR_TEMPLATES[template_id].get("environment") or "live")
    region = str(args.get("region") or BROKER_CONNECTOR_TEMPLATES[template_id].get("region") or "")
    display_name = str(args.get("display_name") or args.get("label") or BROKER_CONNECTOR_TEMPLATES[template_id]["display_name"])
    profile = _profile_for_template(template_id, broker_id, environment, region, credential_ref=credential_ref)
    blockers = list(profile.get("blockers") or [])
    read_blockers = [blocker for blocker in blockers if not str(blocker).startswith("execution_")]
    status = "read_only" if not read_blockers else "disabled"
    if profile.get("execution_posture") in {"live_disabled", "service_adapter_required"} and status == "read_only":
        status = "trading_locked"
    if profile.get("execution_posture") in BROKER_VALIDATION_EXECUTION_POSTURES and status == "read_only":
        profile["enabled_mcp_tools"] = ["preview_order_translation", "run_order_checks", "submit_approved_order"]
    enabled_trade_scopes = sorted(set(_trade_scopes_from_profile(profile))) if status == "trading_enabled" else []
    metadata = {
        "connector_template": template_id,
        "capability_profile": profile,
        "blockers": blockers,
        "execution_enabled": False,
        "validation_execution_enabled": bool(enabled_trade_scopes) and status == "trading_enabled",
        "credential_validation_status": "not_checked" if profile.get("execution_posture") in BROKER_VALIDATION_EXECUTION_POSTURES else "not_required",
    }
    connection, created = BrokerConnection.objects.update_or_create(
        broker_id=broker_id,
        defaults={
            "display_name": display_name,
            "transport": "api",
            "adapter_type": "native_api",
            "status": status,
            "credential_ref": credential_ref,
            "capabilities": sorted(set(_capabilities_from_profile(profile))),
            "enabled_read_scopes": sorted(set(_read_scopes_from_profile(profile))),
            "enabled_trade_scopes": enabled_trade_scopes,
            "trust_level": "template",
            "last_health_status": "not_checked",
            "drift_status": "review_required" if blockers else "none",
            "metadata": metadata,
        },
    )
    result = {
        "status": "created" if created else "updated",
        "broker_id": connection.broker_id,
        "template_id": template_id,
        "connection": _serialize_connection(connection),
        "capability_profile": profile,
        "blockers": blockers,
        "db_canonical": True,
        "workspace_context": workspace_context_payload(workspace_root),
    }
    _audit("broker_connector.registered" if created else "broker_connector.updated", result, str(args.get("principal_id") or "head-manager"), workspace_root)
    return result


def get_broker_capability_profile(workspace_root: Path | str | None, args: dict[str, Any]) -> dict[str, Any]:
    connection = _get_connection(workspace_root, args.get("broker_id") or args.get("broker_connection_id") or "paper-trading")
    profile = adapter_for_connection(connection, workspace_root).describe_capabilities()
    return {
        "broker_id": connection.broker_id,
        "capability_profile": profile,
        "blockers": _profile_blockers(profile),
        "db_canonical": True,
        "workspace_context": workspace_context_payload(workspace_root),
    }


def get_broker_instrument_constraints(workspace_root: Path | str | None, args: dict[str, Any]) -> dict[str, Any]:
    connection = _get_connection(workspace_root, args.get("broker_id") or args.get("broker_connection_id") or "paper-trading")
    symbol = str(args.get("symbol") or args.get("instrument") or args.get("market") or "").upper()
    if not symbol:
        raise ValueError("symbol, instrument, or market is required")
    constraints = adapter_for_connection(connection, workspace_root).get_instrument_constraints(symbol, args)
    return {
        "broker_id": connection.broker_id,
        "constraints": asdict(constraints),
        "db_canonical": True,
        "workspace_context": workspace_context_payload(workspace_root),
    }


def preview_order_translation(workspace_root: Path | str | None, args: dict[str, Any]) -> dict[str, Any]:
    connection = _get_connection(workspace_root, args.get("broker_id") or args.get("broker_connection_id") or args.get("broker") or "paper-trading")
    order = _preview_order_payload(workspace_root, args, connection)
    adapter = adapter_for_connection(connection, workspace_root)
    preview = adapter.preview_order(order)
    result = {
        "broker_id": connection.broker_id,
        "order": order,
        **preview,
        "db_canonical": True,
        "workspace_context": workspace_context_payload(workspace_root),
    }
    _audit("broker_order_translation.previewed", result, str(args.get("principal_id") or "head-manager"), workspace_root)
    return result


def ensure_paper_broker_connection(workspace_root: Path | str | None = None, actor: str = "service") -> Any:
    ensure_runtime_database(workspace_root)
    from apps.integrations.models import BrokerAccount, BrokerConnection

    profile = _profile_for_template("paper", "paper-trading", "paper", "KR", credential_ref="")

    connection, created = BrokerConnection.objects.update_or_create(
        broker_id="paper-trading",
        defaults={
            "display_name": "Paper",
            "transport": "paper",
            "adapter_type": "paper",
            "status": "trading_enabled",
            "credential_ref": "",
            "capabilities": [
                "account.cash.read",
                "account.positions.read",
                "order.validate",
                "order.submit.paper",
                "order.status.read",
            ],
            "enabled_read_scopes": ["account.cash.read", "account.positions.read", "order.status.read"],
            "enabled_trade_scopes": ["order.submit.paper"],
            "trust_level": "built_in",
            "last_health_status": "ok",
            "drift_status": "none",
            "metadata": {"live_execution": False, "paper_only": True, "capability_profile": profile, "blockers": []},
        },
    )
    BrokerAccount.objects.update_or_create(
        broker_connection=connection,
        broker_account_id=DEFAULT_ACCOUNT_ID,
        defaults={
            "account_label": "Local Paper Account",
            "account_type": "paper",
            "base_currency": "KRW",
            "masked_identifier": "paper",
            "trading_enabled": True,
            "last_seen_at": django_timezone.now(),
            "metadata": {"portfolio_id": DEFAULT_PORTFOLIO_ID, "strategy_id": DEFAULT_STRATEGY_ID},
        },
    )
    if created and actor not in {"service", "read", "system-read"}:
        _audit("broker_connection.created", {"broker_id": connection.broker_id, "status": connection.status}, actor, workspace_root)
    return connection


def create_external_mcp_broker_connection(
    workspace_root: Path | str | None,
    *,
    broker_id: str,
    display_name: str,
    router_name: str,
    discovery_payload: str | dict[str, Any] | None = None,
    credential_ref: str = "",
    actor: str = "web",
) -> dict[str, Any]:
    ensure_runtime_database(workspace_root)
    from apps.integrations.models import BrokerConnection
    from apps.mcp.models import McpRouter
    from apps.mcp.services import create_or_update_router, import_external_mcp_discovery

    router = McpRouter.objects.filter(name=router_name).first()
    if router is None:
        router = create_or_update_router(name=router_name, label=display_name, transport="stdio", credential_ref=credential_ref, enabled=False, actor=actor)
    imported = {"imported": 0, "tool_ids": []}
    if discovery_payload:
        imported = import_external_mcp_discovery(router, discovery_payload, actor=actor)
    connection, created = BrokerConnection.objects.update_or_create(
        broker_id=broker_id,
        defaults={
            "display_name": display_name,
            "transport": "mcp",
            "adapter_type": "external_mcp",
            "status": "read_only",
            "credential_ref": credential_ref,
            "capabilities": _capabilities_for_router(router),
            "enabled_read_scopes": _enabled_read_scopes_for_router(router),
            "enabled_trade_scopes": [],
            "trust_level": "unreviewed",
            "last_health_status": "not_checked",
            "drift_status": "review_required",
            "metadata": {"router": router.name, "execution_enabled": False},
        },
    )
    _audit(
        "broker_connection.mcp_imported" if created else "broker_connection.mcp_updated",
        {"broker_id": connection.broker_id, "router": router.name, "imported": imported.get("imported", 0)},
        actor,
        workspace_root,
    )
    return {"broker_id": connection.broker_id, "router": router.name, "imported": imported, "status": connection.status}


def list_broker_connections(workspace_root: Path | str | None = None, args: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_runtime_database(workspace_root)
    from apps.integrations.models import BrokerConnection

    ensure_paper_broker_connection(workspace_root)
    return {
        "connections": [_serialize_connection(connection) for connection in BrokerConnection.objects.prefetch_related("accounts").all()],
        "db_canonical": True,
        "workspace_context": workspace_context_payload(workspace_root),
    }


def get_broker_connection_status(workspace_root: Path | str | None, args: dict[str, Any]) -> dict[str, Any]:
    connection = _get_connection(workspace_root, args.get("broker_id") or args.get("broker_connection_id") or "paper-trading")
    adapter = adapter_for_connection(connection, workspace_root)
    health = adapter.health_check()
    _reconcile_validation_execution_status(connection, health)
    return {
        "connection": _serialize_connection(connection),
        "health": asdict(health),
        "db_canonical": True,
        "workspace_context": workspace_context_payload(workspace_root),
    }


def sync_broker_account(workspace_root: Path | str | None, args: dict[str, Any] | None = None) -> dict[str, Any]:
    args = dict(args or {})
    connection = _get_connection(workspace_root, args.get("broker_id") or args.get("broker_connection_id") or "paper-trading")
    if connection.status not in {"read_only", "trading_locked", "trading_enabled"}:
        raise ValueError(f"broker connection is not enabled for read sync: {connection.broker_id}")
    adapter = adapter_for_connection(connection, workspace_root)
    ensure_runtime_database(workspace_root)
    from apps.integrations.models import BrokerAccount
    from apps.portfolio.models import BrokerSyncRun

    started_at = django_timezone.now()
    sync_run = BrokerSyncRun.objects.create(broker_connection=connection, status="started", started_at=started_at)
    requested_account = str(args.get("broker_account_id") or args.get("account_id") or "")
    synced_accounts: list[dict[str, Any]] = []
    warnings: list[str] = []
    cash_count = 0
    positions_count = 0
    try:
        accounts = adapter.discover_accounts()
        for account_dto in accounts:
            if requested_account and account_dto.broker_account_id != requested_account:
                continue
            broker_account, _ = BrokerAccount.objects.update_or_create(
                broker_connection=connection,
                broker_account_id=account_dto.broker_account_id,
                defaults={
                    "account_label": account_dto.account_label,
                    "account_type": account_dto.account_type,
                    "base_currency": account_dto.base_currency,
                    "masked_identifier": account_dto.masked_identifier,
                    "trading_enabled": account_dto.trading_enabled and connection.status == "trading_enabled",
                    "last_seen_at": django_timezone.now(),
                    "metadata": account_dto.metadata or {},
                },
            )
            cash = adapter.get_cash(account_dto.broker_account_id)
            positions = adapter.get_positions(account_dto.broker_account_id)
            snapshot = materialize_portfolio_snapshot_from_broker_state(
                workspace_root,
                connection=connection,
                broker_account=broker_account,
                cash=cash,
                positions=positions,
                sync_run_id=sync_run.id,
            )
            reconciliation = create_reconciliation_summary(connection, broker_account, snapshot, cash, positions)
            cash_count += len(cash)
            positions_count += len(positions)
            synced_accounts.append(
                {
                    "broker_account_id": broker_account.broker_account_id,
                    "snapshot_id": snapshot.id,
                    "reconciliation_id": reconciliation.id,
                    "reconciliation_status": reconciliation.status,
                }
            )
        if requested_account and not synced_accounts:
            warnings.append(f"broker account not discovered: {requested_account}")
        sync_run.status = "warning" if warnings else "ok"
        sync_run.pulled_cash_count = cash_count
        sync_run.pulled_positions_count = positions_count
        sync_run.warnings = warnings
        sync_run.payload_hash = stable_hash({"accounts": synced_accounts, "warnings": warnings})
        sync_run.finished_at = django_timezone.now()
        sync_run.save()
        _reconcile_validation_execution_status(connection, adapter.health_check())
        connection.last_sync_at = sync_run.finished_at
        connection.save(update_fields=["last_sync_at", "updated_at"])
    except Exception as exc:
        sync_run.status = "error"
        sync_run.error = str(exc)
        sync_run.finished_at = django_timezone.now()
        sync_run.save(update_fields=["status", "error", "finished_at"])
        error_message = _safe_error(exc)
        _reconcile_validation_execution_status(connection, BrokerHealth("error", error_message, _health_details_for_connection(connection, error_message)))
        _audit("broker_sync.failed", {"broker_id": connection.broker_id, "error": str(exc)}, str(args.get("principal_id") or "service"), workspace_root)
        raise
    result = {
        "status": sync_run.status,
        "broker_id": connection.broker_id,
        "sync_run_id": sync_run.id,
        "accounts": synced_accounts,
        "warnings": warnings,
        "db_canonical": True,
        "workspace_context": workspace_context_payload(workspace_root),
    }
    _audit("broker_sync.completed", result, str(args.get("principal_id") or "service"), workspace_root)
    return result


def materialize_portfolio_snapshot_from_broker_state(
    workspace_root: Path | str | None,
    *,
    connection: Any,
    broker_account: Any,
    cash: list[CashDTO],
    positions: list[PositionDTO],
    sync_run_id: int | None = None,
) -> Any:
    ensure_runtime_database(workspace_root)
    from apps.portfolio.models import CashBalance, PortfolioLedgerEvent, PortfolioSnapshot, Position

    portfolio_id, account_id, strategy_id = portfolio_keys(
        {
            "portfolio_id": broker_account.metadata.get("portfolio_id") if isinstance(broker_account.metadata, dict) else "",
            "account_id": broker_account.broker_account_id,
            "strategy_id": broker_account.metadata.get("strategy_id") if isinstance(broker_account.metadata, dict) else "",
        },
        workspace_root,
    )
    now = django_timezone.now()
    position_payload = {
        item.symbol: {
            "quantity": item.quantity,
            "average_price": item.average_price,
            "currency": item.currency,
            "instrument_id": item.instrument_id or item.symbol,
        }
        for item in positions
        if item.quantity != 0
    }
    cash_payload = {item.currency: item.amount for item in cash}
    payload = {
        "cash_krw": cash_payload.get("KRW", 0),
        "cash": cash_payload,
        "positions": position_payload,
        "updated_at": now.isoformat(),
        "portfolio_id": portfolio_id,
        "account_id": account_id,
        "strategy_id": strategy_id,
        "source": connection.broker_id,
        "broker_connection_id": connection.broker_id,
        "broker_account_id": broker_account.broker_account_id,
        "sync_run_id": sync_run_id,
        "db_canonical": True,
        "workspace_context": workspace_context_payload(workspace_root),
    }
    snapshot = PortfolioSnapshot.objects.create(
        source="paper-trading" if connection.broker_id == "paper-trading" else "broker-sync",
        portfolio_id=portfolio_id,
        account_id=account_id,
        strategy_id=strategy_id,
        workspace_context=workspace_context_payload(workspace_root),
        payload=payload,
    )
    for item in cash:
        CashBalance.objects.create(
            snapshot=snapshot,
            currency=item.currency,
            amount=item.amount,
            portfolio_id=portfolio_id,
            account_id=account_id,
            strategy_id=strategy_id,
        )
        raw = {"currency": item.currency, "amount": item.amount, "sync_run_id": sync_run_id}
        PortfolioLedgerEvent.objects.create(
            event_type="cash",
            broker_connection=connection,
            broker_account=broker_account,
            portfolio_id=portfolio_id,
            account_id=account_id,
            strategy_id=strategy_id,
            amount=item.amount,
            currency=item.currency,
            event_at=now,
            source_payload_hash=stable_hash(raw),
            raw_payload_ref=f"broker_sync_run:{sync_run_id}" if sync_run_id else "",
            metadata=raw,
        )
    for item in positions:
        if item.quantity == 0:
            continue
        Position.objects.create(
            snapshot=snapshot,
            symbol=item.symbol,
            quantity=item.quantity,
            average_price=item.average_price,
            currency=item.currency,
            portfolio_id=portfolio_id,
            account_id=account_id,
            strategy_id=strategy_id,
        )
        raw = {"symbol": item.symbol, "quantity": item.quantity, "average_price": item.average_price, "currency": item.currency, "sync_run_id": sync_run_id}
        PortfolioLedgerEvent.objects.create(
            event_type="position",
            broker_connection=connection,
            broker_account=broker_account,
            portfolio_id=portfolio_id,
            account_id=account_id,
            strategy_id=strategy_id,
            instrument_id=item.instrument_id or item.symbol,
            symbol=item.symbol,
            quantity=item.quantity,
            price=item.average_price,
            currency=item.currency,
            event_at=now,
            source_payload_hash=stable_hash(raw),
            raw_payload_ref=f"broker_sync_run:{sync_run_id}" if sync_run_id else "",
            metadata=raw,
        )
    return snapshot


def create_reconciliation_summary(connection: Any, broker_account: Any, snapshot: Any, cash: list[CashDTO], positions: list[PositionDTO]) -> Any:
    from apps.portfolio.models import ReconciliationRun

    diffs: list[dict[str, Any]] = []
    if not cash and not positions and connection.transport != "mcp":
        diffs.append({"severity": "warning", "message": "sync returned no cash or positions"})
    status = "warning" if any(diff.get("severity") == "warning" for diff in diffs) else "clean"
    return ReconciliationRun.objects.create(
        broker_connection=connection,
        broker_account=broker_account,
        local_snapshot=snapshot,
        broker_snapshot_ref=f"portfolio_snapshot:{snapshot.id}",
        status=status,
        diffs=diffs,
    )


def list_reconciliation_runs(workspace_root: Path | str | None = None, args: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_runtime_database(workspace_root)
    from apps.portfolio.models import ReconciliationRun

    args = args or {}
    limit = max(1, min(int(args.get("limit") or 20), 200))
    queryset = ReconciliationRun.objects.select_related("broker_connection", "broker_account", "local_snapshot")
    broker_id = args.get("broker_id") or args.get("broker_connection_id")
    if broker_id:
        queryset = queryset.filter(broker_connection__broker_id=broker_id)
    return {
        "reconciliation_runs": [_serialize_reconciliation(run) for run in queryset[:limit]],
        "db_canonical": True,
        "workspace_context": workspace_context_payload(workspace_root),
    }


def record_broker_mapping_review(workspace_root: Path | str | None, args: dict[str, Any]) -> dict[str, Any]:
    connection = _get_connection(workspace_root, args.get("broker_id") or args.get("broker_connection_id") or "")
    ensure_runtime_database(workspace_root)
    from apps.mcp.models import McpExternalTool

    router_name = (connection.metadata or {}).get("router")
    enabled_tools = []
    blocked_tools = []
    if router_name:
        for tool in McpExternalTool.objects.filter(router__name=router_name).order_by("external_name"):
            if tool.enabled and tool.review_status in {"reviewed", "approved"} and tool.proxy_mode in {"read_only", "summary_only", "service_adapter", "service_path"}:
                enabled_tools.append({"name": tool.external_name, "capability": tool.canonical_capability, "proxy_mode": tool.proxy_mode})
            else:
                blocked_tools.append({"name": tool.external_name, "category": tool.category, "proxy_mode": tool.proxy_mode, "review_status": tool.review_status})
    connection.capabilities = sorted({item["capability"] for item in enabled_tools if item.get("capability")})
    connection.enabled_read_scopes = sorted({item["capability"] for item in enabled_tools if str(item.get("proxy_mode")) in {"read_only", "summary_only"}})
    connection.enabled_trade_scopes = []
    connection.drift_status = "none" if enabled_tools else "review_required"
    metadata = dict(connection.metadata or {})
    metadata.update({"tool_mappings": enabled_tools, "blocked_tools": blocked_tools, "execution_enabled": False})
    connection.metadata = metadata
    connection.save()
    result = {"broker_id": connection.broker_id, "enabled_tools": enabled_tools, "blocked_tools": blocked_tools}
    _audit("broker_mapping.reviewed", result, str(args.get("principal_id") or args.get("actor") or "web"), workspace_root)
    return {"status": "recorded", **result, "db_canonical": True, "workspace_context": workspace_context_payload(workspace_root)}


def _template_summary(template_id: str, template: dict[str, Any]) -> dict[str, Any]:
    return {
        "template_id": template_id,
        "display_name": template["display_name"],
        "family": template["family"],
        "venue": template["venue"],
        "region": template["region"],
        "asset_classes": list(template["asset_classes"]),
        "products": list(template["products"]),
        "environment_default": template["environment"],
        "execution_posture": template["execution_posture"],
        "auth_type": template.get("auth_model", {}).get("type", ""),
    }


def _profile_for_template(template_id: str, broker_id: str, environment: str, region: str, *, credential_ref: str) -> dict[str, Any]:
    if template_id == "paper":
        template = {
            "display_name": "Paper",
            "family": "paper",
            "venue": "paper",
            "region": region or "KR",
            "asset_classes": ["equity", "etf", "cash"],
            "products": ["paper"],
            "environment": "paper",
            "auth_model": {"type": "none", "credential_ref_required": False},
            "account_model": {"multi_account": False, "balances": "cash", "positions": True},
            "instrument_model": {"identity": "symbol", "examples": ["005930", "AAPL"]},
            "order_model": {"sides": ["buy", "sell"], "order_types": ["limit"], "time_in_force": ["day"], "quantity_modes": ["quantity"]},
            "validation_model": {"preview": True, "dry_run": True},
            "event_model": {"polling": True, "streaming": False},
            "rate_limits": [],
            "execution_posture": "paper_only",
        }
    else:
        template = BROKER_CONNECTOR_TEMPLATES[template_id]
    profile = {
        "template_id": template_id,
        "broker_id": broker_id,
        "display_name": template["display_name"],
        "family": template["family"],
        "venue": template["venue"],
        "region": region or template["region"],
        "asset_classes": list(template["asset_classes"]),
        "products": list(template["products"]),
        "environment": environment or template["environment"],
        "execution_posture": template["execution_posture"],
        "auth_model": dict(template["auth_model"]),
        "account_model": dict(template["account_model"]),
        "instrument_model": dict(template["instrument_model"]),
        "order_model": dict(template["order_model"]),
        "validation_model": dict(template["validation_model"]),
        "event_model": dict(template["event_model"]),
        "rate_limits": list(template["rate_limits"]),
        "blocked_surfaces": list(BLOCKED_BROKER_SURFACES),
        "enabled_mcp_tools": [],
        "blockers": [],
    }
    if profile["auth_model"].get("credential_ref_required") and not credential_ref:
        profile["blockers"].append("credential_ref_missing")
    if profile["execution_posture"] in {"live_disabled", "service_adapter_required", "unsupported"}:
        profile["blockers"].append(f"execution_{profile['execution_posture']}")
    return profile


def _profile_blockers(profile: dict[str, Any]) -> list[str]:
    blockers = list(profile.get("blockers") or [])
    blocked = set(str(item) for item in profile.get("blocked_surfaces") or [])
    missing_blocked = sorted(set(BLOCKED_BROKER_SURFACES) - blocked)
    if missing_blocked:
        blockers.append("blocked_surface_incomplete:" + ",".join(missing_blocked))
    return list(dict.fromkeys(blockers))


def _capabilities_from_profile(profile: dict[str, Any]) -> list[str]:
    capabilities = ["broker.profile.read", "broker.instrument_constraints.read"]
    if profile.get("account_model", {}).get("balances"):
        capabilities.append("account.cash.read")
    if profile.get("account_model", {}).get("positions"):
        capabilities.append("account.positions.read")
    if profile.get("event_model", {}).get("polling"):
        capabilities.append("order.status.read")
    if profile.get("validation_model", {}).get("preview"):
        capabilities.append("order.preview")
    if profile.get("execution_posture") in BROKER_VALIDATION_EXECUTION_POSTURES:
        capabilities.append("order.submit.validation")
    return capabilities


def _read_scopes_from_profile(profile: dict[str, Any]) -> list[str]:
    return [capability for capability in _capabilities_from_profile(profile) if capability.endswith(".read") or capability == "order.preview"]


def _trade_scopes_from_profile(profile: dict[str, Any]) -> list[str]:
    if profile.get("execution_posture") in BROKER_VALIDATION_EXECUTION_POSTURES:
        return ["order.submit.validation"]
    return []


def _constraints_from_profile(profile: dict[str, Any], symbol: str, args: dict[str, Any]) -> BrokerInstrumentConstraints:
    order_model = profile.get("order_model") if isinstance(profile.get("order_model"), dict) else {}
    instrument_model = profile.get("instrument_model") if isinstance(profile.get("instrument_model"), dict) else {}
    asset_class = str(args.get("asset_class") or (profile.get("asset_classes") or ["unknown"])[0])
    product_type = str(args.get("product_type") or (profile.get("products") or ["spot"])[0])
    notes = []
    if instrument_model.get("filters"):
        notes.append("broker/exchange filters required: " + ", ".join(instrument_model["filters"]))
    if profile.get("execution_posture") not in NON_LIVE_EXECUTION_POSTURES:
        notes.append("live execution disabled until adapter review")
    return BrokerInstrumentConstraints(
        symbol=symbol,
        asset_class=asset_class,
        product_type=product_type,
        quantity_modes=tuple(order_model.get("quantity_modes") or ["quantity"]),
        order_types=tuple(order_model.get("order_types") or ["market", "limit"]),
        time_in_force=tuple(order_model.get("time_in_force") or ["day"]),
        price_increment=str(args.get("price_increment") or ""),
        quantity_increment=str(args.get("quantity_increment") or ""),
        min_quantity=str(args.get("min_quantity") or ""),
        min_notional=str(args.get("min_notional") or ""),
        currency=str(args.get("currency") or ""),
        notes=tuple(notes),
    )


def _translation_reasons(profile: dict[str, Any], order: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    order_model = profile.get("order_model") if isinstance(profile.get("order_model"), dict) else {}
    order_type = str(order.get("order_type") or "limit")
    tif = str(order.get("time_in_force") or "day")
    quantity_mode = str(order.get("quantity_mode") or ("quote_notional" if order.get("quote_notional") else "quantity"))
    supported_order_types = set(str(item) for item in order_model.get("order_types") or [])
    supported_tif = set(str(item) for item in order_model.get("time_in_force") or [])
    supported_quantity_modes = set(str(item) for item in order_model.get("quantity_modes") or [])
    if supported_order_types and order_type not in supported_order_types:
        reasons.append(f"order_type not supported by connector: {order_type}")
    if supported_tif and tif not in supported_tif:
        reasons.append(f"time_in_force not supported by connector: {tif}")
    if supported_quantity_modes and quantity_mode not in supported_quantity_modes:
        reasons.append(f"quantity_mode not supported by connector: {quantity_mode}")
    if profile.get("execution_posture") not in NON_LIVE_EXECUTION_POSTURES:
        reasons.append(f"execution posture blocks live adapter calls: {profile.get('execution_posture') or 'unknown'}")
    return reasons


def _broker_payload_preview(profile: dict[str, Any], order: dict[str, Any]) -> dict[str, Any]:
    family = str(profile.get("family") or "")
    symbol = str(order.get("venue_symbol") or order.get("market") or order.get("symbol") or "")
    side = str(order.get("side") or "").lower()
    quantity_mode = str(order.get("quantity_mode") or ("quote_notional" if order.get("quote_notional") else "quantity"))
    if profile.get("template_id") in {"upbit_spot_kr", "upbit_spot_global"}:
        upbit_side = "bid" if side == "buy" else "ask" if side == "sell" else side
        ord_type = "price" if upbit_side == "bid" and quantity_mode == "quote_notional" else "market" if upbit_side == "ask" and order.get("order_type") == "market" else order.get("order_type", "limit")
        return {"market": symbol, "side": upbit_side, "ord_type": ord_type, "identifier": order.get("client_order_id", "")}
    if family == "crypto_exchange":
        return {"symbol": symbol.replace("-", ""), "side": side.upper(), "type": str(order.get("order_type") or "limit").upper(), "newClientOrderId": order.get("client_order_id", "")}
    if profile.get("template_id") == "kis_openapi":
        return {"pdno": symbol, "ord_dvsn": order.get("order_type", "limit"), "tr_id": "template_selected_by_side_environment"}
    if profile.get("template_id") == "ibkr_gateway":
        return {"conid": order.get("conid") or order.get("instrument_id") or symbol, "side": side.upper(), "orderType": order.get("order_type", "limit")}
    return {"symbol": symbol, "side": side, "type": order.get("order_type", "limit"), "client_order_id": order.get("client_order_id", "")}


def canonical_order_v2_from_order(order: dict[str, Any], profile: dict[str, Any] | None = None) -> dict[str, Any]:
    profile = profile or {}
    symbol = str(order.get("venue_symbol") or order.get("market") or order.get("symbol") or "").upper()
    quantity_mode = str(order.get("quantity_mode") or ("quote_notional" if order.get("quote_notional") else "quantity"))
    return {
        "version": 2,
        "asset_class": str(order.get("asset_class") or (profile.get("asset_classes") or ["equity"])[0]),
        "product_type": str(order.get("product_type") or (profile.get("products") or ["spot"])[0]),
        "instrument": {
            "symbol": str(order.get("symbol") or symbol),
            "venue_symbol": symbol,
            "instrument_id": str(order.get("instrument_id") or order.get("conid") or ""),
            "base_asset": str(order.get("base_asset") or ""),
            "quote_asset": str(order.get("quote_asset") or order.get("currency") or ""),
        },
        "legs": order.get("legs") if isinstance(order.get("legs"), list) else [],
        "side": str(order.get("side") or "").lower(),
        "quantity_mode": quantity_mode,
        "quantity": order.get("quantity"),
        "quote_notional": order.get("quote_notional"),
        "order_style": {
            "order_type": str(order.get("order_type") or "limit"),
            "limit_price": order.get("limit_price"),
            "stop_price": order.get("stop_price"),
            "time_in_force": str(order.get("time_in_force") or "day"),
            "session": str(order.get("session") or ""),
            "routing": str(order.get("routing") or ""),
        },
        "margin": {
            "margin_mode": str(order.get("margin_mode") or ""),
            "position_side": str(order.get("position_side") or ""),
            "reduce_only": bool(order.get("reduce_only") or False),
            "leverage": order.get("leverage"),
        },
        "client_order_id": str(order.get("client_order_id") or order.get("identifier") or order.get("id") or ""),
        "approval_constraints": order.get("approval_constraints") if isinstance(order.get("approval_constraints"), dict) else {},
        "broker_translation": _broker_payload_preview(profile, order) if profile else {},
    }


def _preview_order_payload(workspace_root: Path | str | None, args: dict[str, Any], connection: Any) -> dict[str, Any]:
    if isinstance(args.get("order"), dict):
        order = dict(args["order"])
    elif args.get("ticket_id") or args.get("order_ticket_id") or args.get("order_id"):
        from tradingcodex_service.application.orders import resolve_order_ticket_payload

        order = resolve_order_ticket_payload(Path(workspace_root or "."), args)
    else:
        order = dict(args)
    order.setdefault("broker", connection.broker_id)
    order.setdefault("broker_connection_id", connection.broker_id)
    order.setdefault("symbol", args.get("symbol") or args.get("instrument") or args.get("market") or order.get("venue_symbol") or "")
    order.setdefault("order_type", args.get("order_type") or "limit")
    order.setdefault("time_in_force", args.get("time_in_force") or "day")
    if "quantity_mode" not in order and args.get("quote_notional"):
        order["quantity_mode"] = "quote_notional"
    return order


def _get_connection(workspace_root: Path | str | None, broker_id: str) -> Any:
    ensure_runtime_database(workspace_root)
    from apps.integrations.models import BrokerConnection

    if not broker_id or broker_id == "paper-trading":
        return ensure_paper_broker_connection(workspace_root)
    connection = BrokerConnection.objects.filter(broker_id=broker_id).first()
    if connection is None:
        raise ValueError(f"unknown broker connection: {broker_id}")
    return connection


def _reconcile_validation_execution_status(connection: Any, health: BrokerHealth) -> None:
    metadata = connection.metadata if isinstance(connection.metadata, dict) else {}
    profile = metadata.get("capability_profile") if isinstance(metadata.get("capability_profile"), dict) else {}
    posture = profile.get("execution_posture")
    update_fields = {"last_health_status", "updated_at"}
    connection.last_health_status = health.status
    if posture in BROKER_VALIDATION_EXECUTION_POSTURES:
        metadata = dict(metadata)
        profile = dict(profile)
        metadata["capability_profile"] = profile
        metadata["credential_validation_status"] = health.status
        if health.message:
            metadata["credential_validation_message"] = health.message[:500]
        if health.details:
            metadata["credential_validation_details"] = health.details
        if health.status == "ok":
            connection.status = "trading_enabled"
            connection.enabled_trade_scopes = sorted(set(_trade_scopes_from_profile(profile)))
            metadata["validation_execution_enabled"] = bool(connection.enabled_trade_scopes)
        else:
            if connection.status == "trading_enabled":
                connection.status = "read_only"
            connection.enabled_trade_scopes = []
            metadata["validation_execution_enabled"] = False
        connection.metadata = metadata
        update_fields.update({"status", "enabled_trade_scopes", "metadata"})
    connection.save(update_fields=sorted(update_fields))


def _health_details_for_connection(connection: Any, message: str) -> dict[str, Any]:
    metadata = connection.metadata if isinstance(connection.metadata, dict) else {}
    profile = metadata.get("capability_profile") if isinstance(metadata.get("capability_profile"), dict) else {}
    if profile.get("template_id") == "binance_spot" and profile.get("environment") == "testnet":
        return _binance_health_details(message)
    return {
        "code": "broker_health_error",
        "category": "credential_or_connectivity",
        "execution_posture": profile.get("execution_posture") or "unknown",
        "retry_after_external_fix": True,
    }


def _serialize_connection(connection: Any) -> dict[str, Any]:
    metadata = connection.metadata if isinstance(connection.metadata, dict) else {}
    profile = metadata.get("capability_profile") if isinstance(metadata.get("capability_profile"), dict) else {}
    return {
        "broker_id": connection.broker_id,
        "display_name": connection.display_name,
        "transport": connection.transport,
        "adapter_type": connection.adapter_type,
        "status": connection.status,
        "credential_ref": connection.credential_ref,
        "capabilities": connection.capabilities,
        "enabled_read_scopes": connection.enabled_read_scopes,
        "enabled_trade_scopes": connection.enabled_trade_scopes,
        "trust_level": connection.trust_level,
        "last_sync_at": connection.last_sync_at.isoformat() if connection.last_sync_at else "",
        "last_health_status": connection.last_health_status,
        "drift_status": connection.drift_status,
        "trading_status": "enabled" if connection.enabled_trade_scopes and connection.status == "trading_enabled" else "locked",
        "connector_template": metadata.get("connector_template", profile.get("template_id", "")),
        "capability_profile": profile,
        "blockers": metadata.get("blockers") or _profile_blockers(profile),
        "accounts_count": connection.accounts.count() if hasattr(connection, "accounts") else 0,
        "accounts": [
            {
                "broker_account_id": account.broker_account_id,
                "account_label": account.account_label,
                "account_type": account.account_type,
                "base_currency": account.base_currency,
                "masked_identifier": account.masked_identifier,
                "trading_enabled": account.trading_enabled,
                "last_seen_at": account.last_seen_at.isoformat() if account.last_seen_at else "",
            }
            for account in connection.accounts.all()
        ] if hasattr(connection, "accounts") else [],
        "metadata": connection.metadata,
    }


def _serialize_reconciliation(run: Any) -> dict[str, Any]:
    return {
        "id": run.id,
        "broker_id": run.broker_connection.broker_id,
        "broker_account_id": run.broker_account.broker_account_id if run.broker_account else "",
        "local_snapshot_id": run.local_snapshot_id,
        "broker_snapshot_ref": run.broker_snapshot_ref,
        "status": run.status,
        "diffs": run.diffs,
        "created_at": run.created_at.isoformat(),
    }


def _capabilities_for_router(router: Any) -> list[str]:
    return sorted(
        {
            tool.canonical_capability
            for tool in router.external_tools.all()
            if tool.canonical_capability and tool.proxy_mode in {"read_only", "summary_only"}
        }
    )


def _enabled_read_scopes_for_router(router: Any) -> list[str]:
    return sorted(
        {
            tool.canonical_capability
            for tool in router.external_tools.all()
            if tool.enabled and tool.review_status in {"reviewed", "approved"} and tool.proxy_mode in {"read_only", "summary_only"}
        }
    )


def _float(value: Any) -> float | None:
    try:
        return float(value)
    except Exception:
        return None


def _resolve_hmac_credentials(credential_ref: str) -> dict[str, Any]:
    ref = str(credential_ref or "").strip()
    if not ref:
        return {"available": False, "reason": "credential_ref is required"}
    if not ref.startswith("env:"):
        return {"available": False, "reason": "only env: credential_ref is supported for Binance testnet"}
    name = ref.split(":", 1)[1].strip()
    if not name:
        return {"available": False, "reason": "env credential_ref name is empty"}
    if "," in name:
        key_name, secret_name = [part.strip() for part in name.split(",", 1)]
    elif ":" in name:
        key_name, secret_name = [part.strip() for part in name.split(":", 1)]
    else:
        key_name = f"{name}_API_KEY"
        secret_name = f"{name}_SECRET_KEY"
    api_key = os.environ.get(key_name, "")
    secret_key = os.environ.get(secret_name, "")
    missing = [label for label, value in ((key_name, api_key), (secret_name, secret_key)) if not value]
    if missing:
        return {"available": False, "reason": "missing environment credential(s): " + ", ".join(missing)}
    return {"available": True, "api_key": api_key, "secret_key": secret_key, "key_ref": key_name, "secret_ref": secret_name}


def _binance_http_request(method: str, base_url: str, path: str, query: str, headers: dict[str, str]) -> dict[str, Any]:
    url = base_url.rstrip("/") + path
    if method.upper() == "GET" and query:
        url = f"{url}?{query}"
        body = None
    elif method.upper() in {"POST", "DELETE"}:
        body = query.encode("utf-8")
    else:
        body = None
    request_headers = {"User-Agent": "TradingCodex/0.2", **headers}
    if body is not None:
        request_headers["Content-Type"] = "application/x-www-form-urlencoded"
    request = Request(url, data=body, method=method.upper(), headers=request_headers)
    try:
        with urlopen(request, timeout=10) as response:
            text = response.read().decode("utf-8")
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise ValueError(_safe_binance_error(detail) or f"Binance HTTP {exc.code}") from exc
    except URLError as exc:
        raise ValueError(f"Binance request failed: {_safe_error(exc)}") from exc
    if not text.strip():
        return {}
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("Binance returned non-JSON response") from exc
    if not isinstance(parsed, dict):
        return {"data": parsed}
    return parsed


def _safe_binance_error(detail: str) -> str:
    try:
        data = json.loads(detail)
    except Exception:
        return detail[:300]
    if not isinstance(data, dict):
        return str(data)[:300]
    code = data.get("code", "")
    message = str(data.get("msg") or data.get("message") or "")
    return f"Binance error {code}: {message}".strip()


def _binance_health_details(message: str) -> dict[str, Any]:
    text = str(message or "")
    if "-2015" in text or "Invalid API-key, IP, or permissions" in text:
        return {
            "code": "binance_auth_rejected",
            "category": "credential_or_permission",
            "testnet": True,
            "signed_endpoint": "/api/v3/account",
            "execution_posture": "broker_validation_only",
            "retry_after_external_fix": True,
            "remediation": [
                "Use a Spot Testnet API key from testnet.binance.vision, not a live Binance key.",
                "Enable signed account/trading permissions required for USER_DATA and order-test endpoints.",
                "If the key has IP restrictions, allow the current machine or remove the restriction for this test key.",
                "Rotate the key if it was pasted into any non-local channel.",
            ],
        }
    if "missing environment credential" in text:
        return {
            "code": "credential_env_missing",
            "category": "configuration",
            "testnet": True,
            "execution_posture": "broker_validation_only",
            "retry_after_external_fix": True,
            "remediation": [
                "Set BINANCE_TESTNET_API_KEY and BINANCE_TESTNET_SECRET_KEY in the process environment.",
                "Keep credentials out of files and pass them only through the local shell environment.",
            ],
        }
    if "credential_ref" in text:
        return {
            "code": "credential_ref_invalid",
            "category": "configuration",
            "testnet": True,
            "execution_posture": "broker_validation_only",
            "retry_after_external_fix": True,
            "remediation": ["Use an env: credential_ref such as env:BINANCE_TESTNET."],
        }
    return {
        "code": "binance_health_error",
        "category": "transport_or_broker",
        "testnet": True,
        "execution_posture": "broker_validation_only",
        "retry_after_external_fix": True,
    }


def _safe_error(exc: Exception) -> str:
    text = str(exc)
    for marker in ("signature=", "X-MBX-APIKEY"):
        if marker in text:
            text = text.split(marker, 1)[0] + marker + "[redacted]"
    return text[:300]


def _binance_symbol(symbol: str) -> str:
    return str(symbol or "").upper().replace("/", "").replace("-", "").replace("_", "")


def _binance_order_type(order_type: str) -> str:
    normalized = str(order_type or "limit").strip().lower()
    mapping = {
        "limit": "LIMIT",
        "market": "MARKET",
        "stop_loss": "STOP_LOSS",
        "stop": "STOP_LOSS_LIMIT",
        "stop_limit": "STOP_LOSS_LIMIT",
        "take_profit": "TAKE_PROFIT",
        "take_profit_limit": "TAKE_PROFIT_LIMIT",
        "limit_maker": "LIMIT_MAKER",
    }
    return mapping.get(normalized, normalized.upper())


def _binance_time_in_force(value: str) -> str:
    normalized = str(value or "GTC").strip().upper()
    if normalized == "DAY":
        return "GTC"
    return normalized


def _binance_client_order_id(order: dict[str, Any]) -> str:
    existing = str(order.get("client_order_id") or "")
    if existing:
        return existing[:36]
    source = str(order.get("order_ticket_id") or order.get("id") or stable_hash(order))
    return ("tcx" + stable_hash({"source": source})[:29])[:36]


def _decimal_string(value: Any) -> str:
    text = str(value)
    if text in {"", "None"}:
        raise ValueError("decimal value is required")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _redact_binance_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if key not in {"signature"}}


def _redact_binance_response(response: dict[str, Any]) -> dict[str, Any]:
    redacted = dict(response)
    for key in ("apiKey", "signature"):
        if key in redacted:
            redacted[key] = "[redacted]"
    return redacted


def _mask_ref(value: str) -> str:
    text = str(value or "")
    if not text:
        return ""
    if len(text) <= 8:
        return text[:2] + "***"
    return text[:6] + "***" + text[-2:]


def _audit(action: str, payload: dict[str, Any], actor: str, workspace_root: Path | str | None) -> None:
    write_audit_event_if_available(workspace_root, actor, "service", {"type": action, "payload": payload})

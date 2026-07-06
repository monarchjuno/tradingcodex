from __future__ import annotations

import importlib.util
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

from django.utils import timezone as django_timezone

from tradingcodex_service.application.audit import write_audit_event_if_available
from tradingcodex_service.application.common import file_hash, stable_hash
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


class BrokerSubmissionUncertainError(RuntimeError):
    def __init__(self, message: str, *, broker_order_id: str = "", status_payload: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.broker_order_id = broker_order_id
        self.status_payload = status_payload or {}


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


@dataclass(frozen=True)
class BrokerAdapterProvider:
    provider_id: str
    display_name: str
    family: str = "custom"
    venue: str = "broker"
    region: str = "custom"
    asset_classes: tuple[str, ...] = ("equity",)
    products: tuple[str, ...] = ("spot",)
    default_environment: str = "live"
    auth_model: dict[str, Any] | None = None
    account_model: dict[str, Any] | None = None
    instrument_model: dict[str, Any] | None = None
    order_model: dict[str, Any] | None = None
    validation_model: dict[str, Any] | None = None
    event_model: dict[str, Any] | None = None
    rate_limits: tuple[dict[str, Any], ...] = ()
    execution_posture: str = "service_adapter_required"
    adapter_type: str = "provider"
    live: bool = False
    factory: Callable[[Any, Path | str | None], "BrokerAdapter"] | None = None

    def as_profile(
        self,
        *,
        broker_id: str,
        environment: str = "",
        region: str = "",
        credential_ref: str = "",
    ) -> dict[str, Any]:
        auth_model = dict(self.auth_model or {"type": "credential_ref", "credential_ref_required": bool(self.live)})
        profile = {
            "provider_id": self.provider_id,
            "template_id": "",
            "broker_id": broker_id,
            "display_name": self.display_name,
            "family": self.family,
            "venue": self.venue,
            "region": region or self.region,
            "asset_classes": list(self.asset_classes),
            "products": list(self.products),
            "environment": environment or self.default_environment,
            "credential_ref": credential_ref,
            "execution_posture": self.execution_posture,
            "auth_model": auth_model,
            "account_model": dict(self.account_model or {"multi_account": False, "balances": "cash", "positions": True}),
            "instrument_model": dict(self.instrument_model or {"identity": "symbol", "examples": []}),
            "order_model": dict(self.order_model or {"sides": ["buy", "sell"], "order_types": ["market", "limit"], "time_in_force": ["day"], "quantity_modes": ["quantity"]}),
            "validation_model": dict(self.validation_model or {"preview": True, "dry_run": not self.live}),
            "event_model": dict(self.event_model or {"polling": True, "streaming": False, "fills": self.live}),
            "rate_limits": list(self.rate_limits),
            "blocked_surfaces": list(BLOCKED_BROKER_SURFACES),
            "enabled_mcp_tools": [],
            "live": self.live,
            "blockers": [],
        }
        if auth_model.get("credential_ref_required") and not credential_ref:
            profile["blockers"].append("credential_ref_missing")
        if self.execution_posture in {"service_adapter_required", "unsupported"}:
            profile["blockers"].append(f"execution_{self.execution_posture}")
        return profile


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


BROKER_LIVE_EXECUTION_POSTURES = {"live_broker"}
BROKER_VALIDATION_EXECUTION_POSTURES = {"broker_validation_only", "testnet_order_test"}
NON_LIVE_EXECUTION_POSTURES = {"paper_only", *BROKER_VALIDATION_EXECUTION_POSTURES}
EXECUTION_ENABLED_POSTURES = {*NON_LIVE_EXECUTION_POSTURES, *BROKER_LIVE_EXECUTION_POSTURES}
PAPER_PROVIDER = BrokerAdapterProvider(
    provider_id="paper",
    display_name="Paper",
    family="paper",
    venue="paper",
    region="KR",
    asset_classes=("equity", "etf", "cash"),
    products=("paper",),
    default_environment="paper",
    auth_model={"type": "none", "credential_ref_required": False},
    account_model={"multi_account": False, "balances": "cash", "positions": True},
    instrument_model={"identity": "symbol", "examples": ["005930", "AAPL"]},
    order_model={"sides": ["buy", "sell"], "order_types": ["limit"], "time_in_force": ["day"], "quantity_modes": ["quantity"]},
    validation_model={"preview": True, "dry_run": True},
    event_model={"polling": True, "streaming": False, "fills": False},
    execution_posture="paper_only",
    adapter_type="paper",
    live=False,
    factory=lambda connection, workspace_root: PaperBrokerAdapter(workspace_root),
)
_BROKER_ADAPTER_PROVIDERS: dict[str, BrokerAdapterProvider] = {}
_WORKSPACE_PROVIDER_SOURCES: dict[str, dict[str, str]] = {}


def register_broker_adapter_provider(provider: BrokerAdapterProvider) -> None:
    if not provider.provider_id:
        raise ValueError("provider_id is required")
    _BROKER_ADAPTER_PROVIDERS[provider.provider_id] = provider


def get_broker_adapter_provider(provider_id: str, workspace_root: Path | str | None = None) -> BrokerAdapterProvider | None:
    provider_id = str(provider_id or "").strip()
    if provider_id == PAPER_PROVIDER.provider_id:
        return PAPER_PROVIDER
    return _BROKER_ADAPTER_PROVIDERS.get(provider_id) or _load_workspace_broker_adapter_provider(provider_id, workspace_root)


def list_broker_adapter_providers(workspace_root: Path | str | None = None, args: dict[str, Any] | None = None) -> dict[str, Any]:
    args = args or {}
    root = Path(workspace_root or os.environ.get("TRADINGCODEX_WORKSPACE_ROOT") or ".").expanduser().resolve()
    family = str(args.get("family") or "")
    asset_class = str(args.get("asset_class") or args.get("asset") or "")
    for provider_path in sorted((root / "trading" / "connectors").glob("*/provider.py")):
        provider_id = provider_path.parent.name
        if provider_id not in _BROKER_ADAPTER_PROVIDERS:
            _load_workspace_broker_adapter_provider(provider_id, root)
    providers = []
    for provider in sorted([PAPER_PROVIDER, *_BROKER_ADAPTER_PROVIDERS.values()], key=lambda item: item.provider_id):
        if family and provider.family != family:
            continue
        if asset_class and asset_class not in provider.asset_classes:
            continue
        providers.append(_provider_summary(provider, root))
    return {
        "providers": providers,
        "templates": [],
        "request_driven": True,
        "paper_provider_builtin": True,
        "named_broker_examples_builtin": False,
        "blocked_surfaces": list(BLOCKED_BROKER_SURFACES),
        "db_canonical": True,
        "workspace_context": workspace_context_payload(root),
    }


def _load_workspace_broker_adapter_provider(provider_id: str, workspace_root: Path | str | None = None) -> BrokerAdapterProvider | None:
    provider_id = _connector_safe_id(str(provider_id or ""))
    if not provider_id:
        return None
    root = Path(workspace_root or os.environ.get("TRADINGCODEX_WORKSPACE_ROOT") or ".").expanduser().resolve()
    provider_path = root / "trading" / "connectors" / provider_id / "provider.py"
    if not provider_path.exists():
        return None
    module_name = f"_tcx_broker_provider_{provider_id}_{stable_hash(str(provider_path))[:12]}"
    spec = importlib.util.spec_from_file_location(module_name, provider_path)
    if spec is None or spec.loader is None:
        raise ValueError(f"cannot load broker provider: {provider_id}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    provider = getattr(module, "PROVIDER", None)
    if provider is None and hasattr(module, "get_provider"):
        provider = module.get_provider()
    if not isinstance(provider, BrokerAdapterProvider):
        raise ValueError(f"workspace provider {provider_id} must expose BrokerAdapterProvider as PROVIDER")
    register_broker_adapter_provider(provider)
    _record_workspace_provider_source(provider.provider_id, provider_path, root)
    return provider


def _provider_summary(provider: BrokerAdapterProvider, workspace_root: Path | str | None = None) -> dict[str, Any]:
    return {
        "provider_id": provider.provider_id,
        "display_name": provider.display_name,
        "family": provider.family,
        "venue": provider.venue,
        "region": provider.region,
        "asset_classes": list(provider.asset_classes),
        "products": list(provider.products),
        "environment_default": provider.default_environment,
        "execution_posture": provider.execution_posture,
        "auth_type": (provider.auth_model or {}).get("type", ""),
        "live": provider.live,
        "adapter_type": provider.adapter_type,
        "provider_source": broker_provider_source_status(provider.provider_id, workspace_root),
    }


def broker_provider_source_status(
    provider_id: str,
    workspace_root: Path | str | None = None,
    *,
    expected_hash: str = "",
) -> dict[str, Any]:
    provider_id = _connector_safe_id(str(provider_id or ""))
    if not provider_id:
        return {"kind": "unknown", "service_restart_required": False, "drift_status": "none"}
    if provider_id == PAPER_PROVIDER.provider_id:
        return {"kind": "builtin", "provider_id": provider_id, "service_restart_required": False, "drift_status": "none"}
    root = Path(workspace_root or os.environ.get("TRADINGCODEX_WORKSPACE_ROOT") or ".").expanduser().resolve()
    provider_path = root / "trading" / "connectors" / provider_id / "provider.py"
    loaded = _WORKSPACE_PROVIDER_SOURCES.get(provider_id, {})
    current_hash = file_hash(provider_path) or ""
    loaded_hash = str(loaded.get("source_hash") or "")
    expected = str(expected_hash or "")
    restart_required = False
    drift_status = "none"
    if expected and current_hash and current_hash != expected:
        restart_required = bool(loaded_hash and loaded_hash != current_hash)
        drift_status = "source_changed"
    elif loaded_hash and current_hash and loaded_hash != current_hash:
        restart_required = True
        drift_status = "loaded_provider_stale"
    elif expected and loaded_hash and loaded_hash != expected:
        restart_required = True
        drift_status = "loaded_provider_mismatch"
    return {
        "kind": "workspace" if provider_path.exists() else "registered",
        "provider_id": provider_id,
        "path": str(provider_path.relative_to(root)) if provider_path.exists() else "",
        "source_hash": current_hash,
        "loaded_source_hash": loaded_hash,
        "registered_source_hash": expected,
        "service_restart_required": restart_required,
        "drift_status": drift_status,
    }


def broker_connection_provider_source_status(connection: Any, workspace_root: Path | str | None = None) -> dict[str, Any]:
    metadata = connection.metadata if isinstance(connection.metadata, dict) else {}
    profile = metadata.get("capability_profile") if isinstance(metadata.get("capability_profile"), dict) else {}
    provider_id = str(metadata.get("provider_id") or profile.get("provider_id") or connection.adapter_type or "")
    provider_source = profile.get("provider_source") if isinstance(profile.get("provider_source"), dict) else {}
    return broker_provider_source_status(provider_id, workspace_root, expected_hash=str(provider_source.get("source_hash") or ""))


def broker_connection_provider_review_reasons(connection: Any, workspace_root: Path | str | None = None) -> list[str]:
    source_status = broker_connection_provider_source_status(connection, workspace_root)
    if source_status.get("service_restart_required"):
        return ["broker provider source changed; restart TradingCodex service and revalidate connector"]
    if source_status.get("drift_status") not in {"", "none", None}:
        return ["broker provider source changed; revalidate connector before broker execution"]
    return []


def _record_workspace_provider_source(provider_id: str, provider_path: Path, root: Path) -> None:
    _WORKSPACE_PROVIDER_SOURCES[provider_id] = {
        "path": str(provider_path),
        "relative_path": str(provider_path.relative_to(root)),
        "source_hash": file_hash(provider_path) or "",
    }


def _provider_source_for_registration(provider_id: str, workspace_root: Path | str | None) -> dict[str, Any]:
    status = broker_provider_source_status(provider_id, workspace_root)
    source_hash = str(status.get("loaded_source_hash") or status.get("source_hash") or "")
    return {**status, "source_hash": source_hash, "registered_source_hash": source_hash}


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


class PaperBrokerAdapter(BrokerAdapter):
    adapter_type = "paper"

    def __init__(self, workspace_root: Path | str | None = None) -> None:
        self.workspace_root = Path(workspace_root or ".").resolve()

    def health_check(self) -> BrokerHealth:
        return BrokerHealth("ok", "local paper broker adapter is available")

    def describe_capabilities(self) -> dict[str, Any]:
        return _paper_profile("paper-trading", "paper", "KR", credential_ref="")

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
            "canonical_order": canonical_order_from_order(order, profile),
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


def adapter_for_connection(connection: Any, workspace_root: Path | str | None = None) -> BrokerAdapter:
    if connection.adapter_type == "paper" or connection.transport == "paper" or connection.broker_id == "paper-trading":
        return PaperBrokerAdapter(workspace_root)
    if connection.transport == "mcp":
        return ExternalMcpBrokerAdapter(connection)
    metadata = connection.metadata if isinstance(connection.metadata, dict) else {}
    profile = metadata.get("capability_profile") if isinstance(metadata.get("capability_profile"), dict) else {}
    provider_id = str(metadata.get("provider_id") or profile.get("provider_id") or connection.adapter_type or "")
    provider = get_broker_adapter_provider(provider_id, workspace_root)
    if provider and provider.factory is not None:
        return provider.factory(connection, workspace_root)
    if connection.transport == "api" or connection.adapter_type == "native_api":
        return NativeApiBrokerAdapter(connection)
    raise ValueError(f"Unsupported broker adapter type: {connection.adapter_type}")


def scaffold_broker_connector(workspace_root: Path | str | None, args: dict[str, Any]) -> dict[str, Any]:
    root = Path(workspace_root or os.environ.get("TRADINGCODEX_WORKSPACE_ROOT") or ".").expanduser().resolve()
    provider_id = str(args.get("provider_id") or args.get("provider") or args.get("template_id") or args.get("template") or "").strip()
    broker_id = _connector_safe_id(str(args.get("broker_id") or args.get("broker_connection_id") or provider_id).strip())
    if not broker_id:
        raise ValueError("broker_id is required")
    provider = get_broker_adapter_provider(provider_id, root) if provider_id else None
    credential_ref = str(args.get("credential_ref") or f"env:{broker_id.upper().replace('-', '_')}").strip()
    _validate_credential_ref(credential_ref)
    environment = str(args.get("environment") or (provider.default_environment if provider else "live"))
    region = str(args.get("region") or (provider.region if provider else "custom"))
    if provider is None:
        profile = {
            "provider_id": provider_id or broker_id,
            "broker_id": broker_id,
            "display_name": str(args.get("display_name") or args.get("label") or broker_id),
            "environment": environment,
            "region": region,
            "credential_ref": credential_ref,
            "execution_posture": "provider_development_required",
            "blocked_surfaces": list(BLOCKED_BROKER_SURFACES),
            "blockers": ["provider_not_installed"],
        }
    else:
        profile = provider.as_profile(broker_id=broker_id, environment=environment, region=region, credential_ref=credential_ref)
        profile["provider_source"] = _provider_source_for_registration(provider.provider_id, root)
    profile["build_lane"] = {
        "scaffolded": True,
        "provider_development_required": provider is None,
        "allowed_capabilities": _connector_build_capabilities(profile),
        "live_order_enabled": False,
        "live_capable_provider": bool(provider and provider.live),
        "live_execution_requires_gates": bool(provider and provider.live),
        "secret_policy": "credential_ref only; raw broker secrets must never be stored in workspace files, prompts, MCP responses, or audit output",
    }
    base = root / "trading" / "connectors" / broker_id
    base.mkdir(parents=True, exist_ok=True)
    profile_path = base / "connector-profile.json"
    secret_schema_path = base / "secret-schema.json"
    readme_path = base / "README.md"
    profile_path.write_text(json.dumps(profile, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    secret_schema = {
        "broker_id": broker_id,
        "provider_id": profile.get("provider_id") or provider_id or broker_id,
        "credential_ref": credential_ref,
        "required_secret_refs": _required_secret_refs(credential_ref, profile),
        "do_not_store_raw_values": True,
    }
    secret_schema_path.write_text(json.dumps(secret_schema, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    readme_path.write_text(
        "\n".join(
            [
                f"# {profile.get('display_name') or broker_id} Connector",
                "",
                "Generated by TradingCodex build mode.",
                "",
                "- Store only the credential reference in TradingCodex.",
                "- Do not paste raw API keys, tokens, or secrets into Codex chat or workspace files.",
                "- Live order submission requires installed provider code plus explicit policy, environment, approval, confirmation, and audit gates.",
                "- If provider_development_required is true, implement and register the provider before connector registration.",
                "",
            ]
        ),
        encoding="utf-8",
    )
    next_steps = [
        f"./tcx connectors register --provider {profile.get('provider_id') or provider_id or broker_id} --broker-id {broker_id} --credential-ref {credential_ref} --environment {environment}",
        f"./tcx connectors validate {broker_id}",
    ]
    if provider is None:
        next_steps = [
            f"Implement or install provider '{profile.get('provider_id') or provider_id or broker_id}' with tcx-build.",
            "./tcx connectors providers",
        ]
    result = {
        "status": "scaffolded",
        "broker_id": broker_id,
        "provider_id": profile.get("provider_id") or provider_id or broker_id,
        "environment": environment,
        "credential_ref": credential_ref,
        "allowed_capabilities": profile["build_lane"]["allowed_capabilities"],
        "live_order_enabled": False,
        "live_capable_provider": bool(provider and provider.live),
        "provider_development_required": provider is None,
        "files": {
            "profile": str(profile_path.relative_to(root)),
            "secret_schema": str(secret_schema_path.relative_to(root)),
            "readme": str(readme_path.relative_to(root)),
        },
        "next": next_steps,
        "db_canonical": True,
        "workspace_context": workspace_context_payload(root),
    }
    _audit("broker_connector.scaffolded", result, str(args.get("principal_id") or "head-manager"), root)
    return result


def validate_broker_connector_build(workspace_root: Path | str | None, args: dict[str, Any]) -> dict[str, Any]:
    root = Path(workspace_root or os.environ.get("TRADINGCODEX_WORKSPACE_ROOT") or ".").expanduser().resolve()
    broker_id = _connector_safe_id(str(args.get("broker_id") or args.get("broker_connection_id") or "").strip())
    if not broker_id:
        raise ValueError("broker_id is required")
    profile_path = root / "trading" / "connectors" / broker_id / "connector-profile.json"
    scaffold_profile = _read_json_file(profile_path, {})
    try:
        connection_payload = get_broker_connection_status(root, {"broker_id": broker_id, "promote_execution": args.get("promote_execution", True)})
    except Exception as exc:
        connection_payload = {"status": "not_registered", "error": str(exc)}
    connection = connection_payload.get("connection") if isinstance(connection_payload.get("connection"), dict) else {}
    profile = connection.get("metadata", {}).get("capability_profile") if isinstance(connection.get("metadata"), dict) else None
    profile = profile if isinstance(profile, dict) else scaffold_profile
    blockers = list(profile.get("blockers") or []) if isinstance(profile, dict) else []
    source_status = {}
    if isinstance(profile, dict):
        provider_source = profile.get("provider_source") if isinstance(profile.get("provider_source"), dict) else {}
        source_status = broker_provider_source_status(str(profile.get("provider_id") or broker_id), root, expected_hash=str(provider_source.get("source_hash") or ""))
        if source_status.get("service_restart_required"):
            blockers.append("provider_source_changed_restart_required")
    if not profile_path.exists() and connection_payload.get("status") == "not_registered":
        blockers.append("connector scaffold or registered connection not found")
    result = {
        "status": "ok" if not blockers else "blocked",
        "broker_id": broker_id,
        "scaffold_present": profile_path.exists(),
        "registered": connection_payload.get("status") != "not_registered",
        "allowed_capabilities": _connector_build_capabilities(profile if isinstance(profile, dict) else {}),
        "live_order_enabled": False,
        "live_capable_provider": bool(isinstance(profile, dict) and profile.get("live")),
        "service_restart_required": bool(source_status.get("service_restart_required")),
        "provider_source": source_status,
        "blockers": blockers,
        "connection": connection_payload,
        "db_canonical": True,
        "workspace_context": workspace_context_payload(root),
    }
    _audit("broker_connector.build_validated", result, str(args.get("principal_id") or "head-manager"), root)
    return result


def get_connector_build_status(workspace_root: Path | str | None, args: dict[str, Any] | None = None) -> dict[str, Any]:
    root = Path(workspace_root or os.environ.get("TRADINGCODEX_WORKSPACE_ROOT") or ".").expanduser().resolve()
    connector_root = root / "trading" / "connectors"
    scaffolds: list[dict[str, Any]] = []
    if connector_root.exists():
        for profile_path in sorted(connector_root.glob("*/connector-profile.json")):
            profile = _read_json_file(profile_path, {})
            scaffolds.append(
                {
                    "broker_id": profile_path.parent.name,
                    "provider_id": profile.get("provider_id") or profile.get("template_id") or "",
                    "environment": profile.get("environment") or "",
                    "allowed_capabilities": _connector_build_capabilities(profile),
                    "live_order_enabled": False,
                    "live_capable_provider": bool(profile.get("live")),
                    "provider_development_required": "provider_not_installed" in list(profile.get("blockers") or []),
                    "service_restart_required": bool(
                        broker_provider_source_status(
                            str(profile.get("provider_id") or profile_path.parent.name),
                            root,
                            expected_hash=str((profile.get("provider_source") if isinstance(profile.get("provider_source"), dict) else {}).get("source_hash") or ""),
                        ).get("service_restart_required")
                    ),
                    "path": str(profile_path.relative_to(root)),
                }
            )
    return {
        "status": "ok",
        "scaffolds": scaffolds,
        "count": len(scaffolds),
        "live_order_enabled": False,
        "live_capable_provider_count": sum(1 for item in scaffolds if item.get("live_capable_provider")),
        "providers": list_broker_adapter_providers(root, {}).get("providers", []),
        "db_canonical": True,
        "workspace_context": workspace_context_payload(root),
    }


def register_broker_connector(workspace_root: Path | str | None, args: dict[str, Any]) -> dict[str, Any]:
    root = Path(workspace_root or os.environ.get("TRADINGCODEX_WORKSPACE_ROOT") or ".").expanduser().resolve()
    ensure_runtime_database(root)
    from apps.integrations.models import AdapterDefinition, BrokerConnection

    provider_id = str(args.get("provider_id") or args.get("provider") or args.get("template_id") or args.get("template") or "").strip()
    provider = get_broker_adapter_provider(provider_id, root)
    if provider is None:
        raise ValueError(f"unknown broker provider: {provider_id or '(missing)'}; build or install a provider first")
    broker_id = str(args.get("broker_id") or args.get("broker_connection_id") or provider_id).strip()
    if not broker_id:
        raise ValueError("broker_id is required")
    credential_ref = str(args.get("credential_ref") or "")
    _validate_credential_ref(credential_ref)
    environment = str(args.get("environment") or provider.default_environment)
    region = str(args.get("region") or provider.region)
    display_name = str(args.get("display_name") or args.get("label") or provider.display_name)
    profile = provider.as_profile(broker_id=broker_id, environment=environment, region=region, credential_ref=credential_ref)
    provider_source = _provider_source_for_registration(provider.provider_id, root)
    profile["provider_source"] = provider_source
    blockers = list(profile.get("blockers") or [])
    if provider_source.get("service_restart_required"):
        blockers.append("provider_source_changed_restart_required")
        profile["blockers"] = blockers
    read_blockers = [blocker for blocker in blockers if not str(blocker).startswith("execution_")]
    status = "read_only" if not read_blockers else "disabled"
    if profile.get("execution_posture") in BROKER_VALIDATION_EXECUTION_POSTURES and status == "read_only":
        profile["enabled_mcp_tools"] = ["preview_order_translation", "run_order_checks", "submit_approved_order"]
    if profile.get("execution_posture") in BROKER_LIVE_EXECUTION_POSTURES and status == "read_only":
        profile["enabled_mcp_tools"] = ["preview_order_translation", "run_order_checks", "submit_approved_order", "cancel_approved_order", "refresh_broker_order_status"]
    enabled_trade_scopes = sorted(set(_trade_scopes_from_profile(profile))) if status == "trading_enabled" else []
    AdapterDefinition.objects.get_or_create(
        adapter_id=provider.provider_id,
        defaults={
            "kind": "execution",
            "enabled": not provider.live,
            "live": bool(provider.live),
            "config": {"provider_id": provider.provider_id, "display_name": provider.display_name},
        },
    )
    metadata = {
        "provider_id": provider.provider_id,
        "connector_template": "",
        "capability_profile": profile,
        "blockers": blockers,
        "execution_enabled": False,
        "live_execution_enabled": False,
        "validation_execution_enabled": bool(enabled_trade_scopes) and status == "trading_enabled",
        "credential_validation_status": "not_checked" if profile.get("execution_posture") in BROKER_VALIDATION_EXECUTION_POSTURES | BROKER_LIVE_EXECUTION_POSTURES else "not_required",
    }
    connection, created = BrokerConnection.objects.update_or_create(
        broker_id=broker_id,
        defaults={
            "display_name": display_name,
            "transport": "api",
            "adapter_type": provider.provider_id,
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
        "provider_id": provider.provider_id,
        "connection": _serialize_connection(connection, root),
        "capability_profile": profile,
        "blockers": blockers,
        "service_restart_required": bool(provider_source.get("service_restart_required")),
        "db_canonical": True,
        "workspace_context": workspace_context_payload(root),
    }
    _audit("broker_connector.registered" if created else "broker_connector.updated", result, str(args.get("principal_id") or "head-manager"), root)
    return result


def connect_broker_connector(workspace_root: Path | str | None, args: dict[str, Any]) -> dict[str, Any]:
    root = Path(workspace_root or os.environ.get("TRADINGCODEX_WORKSPACE_ROOT") or ".").expanduser().resolve()
    provider_id = str(args.get("provider_id") or args.get("provider") or args.get("broker") or args.get("broker_id") or "").strip()
    broker_id = _connector_safe_id(str(args.get("broker_id") or args.get("broker_connection_id") or provider_id).strip())
    mode = str(args.get("mode") or "read-only").strip().lower().replace("_", "-")
    if mode not in {"read-only", "validation", "live-request"}:
        raise ValueError("mode must be read-only, validation, or live-request")
    if not broker_id:
        raise ValueError("broker_id is required")
    credential_ref = str(args.get("credential_ref") or f"env:{broker_id.upper().replace('-', '_')}").strip()
    _validate_credential_ref(credential_ref)

    provider = get_broker_adapter_provider(provider_id, root) if provider_id else None
    scaffold = scaffold_broker_connector(
        root,
        {
            **args,
            "provider": provider_id,
            "broker_id": broker_id,
            "credential_ref": credential_ref,
            "principal_id": args.get("principal_id") or "head-manager",
        },
    )
    if provider is None:
        result = {
            "status": "provider_missing",
            "lifecycle_state": "provider_missing",
            "broker_id": broker_id,
            "provider_id": provider_id or broker_id,
            "mode": mode,
            "next": scaffold.get("next", []),
            "live_order_enabled": False,
            "db_canonical": True,
            "workspace_context": workspace_context_payload(root),
        }
        _audit("broker_connector.connect_provider_missing", result, str(args.get("principal_id") or "head-manager"), root)
        return result

    registered = register_broker_connector(
        root,
        {
            **args,
            "provider": provider.provider_id,
            "broker_id": broker_id,
            "credential_ref": credential_ref,
            "principal_id": args.get("principal_id") or "head-manager",
        },
    )
    connection = registered.get("connection") if isinstance(registered.get("connection"), dict) else {}
    if mode == "live-request":
        ensure_runtime_database(root)
        from apps.integrations.models import BrokerConnection

        model = BrokerConnection.objects.filter(broker_id=broker_id).first()
        if model is not None:
            metadata = dict(model.metadata or {})
            metadata["live_execution_requested"] = True
            metadata["live_execution_enabled"] = False
            model.metadata = metadata
            model.save(update_fields=["metadata", "updated_at"])
            connection = _serialize_connection(model, root)
    validated = validate_broker_connector_build(
        root,
        {
            "broker_id": broker_id,
            "principal_id": args.get("principal_id") or "head-manager",
            "promote_execution": mode != "read-only",
        },
    )
    status_connection = validated.get("connection", {}).get("connection", {}) if isinstance(validated.get("connection"), dict) else {}
    connection = status_connection or connection
    lifecycle = _connector_lifecycle_state(connection, requested_mode=mode)
    result = {
        "status": "connected" if lifecycle in {"read_only", "validation_ready", "trading_enabled", "live_requested"} else lifecycle,
        "lifecycle_state": lifecycle,
        "broker_id": broker_id,
        "provider_id": provider.provider_id,
        "mode": mode,
        "credential_ref": credential_ref,
        "connection": connection,
        "next": validated.get("next", []),
        "live_order_enabled": lifecycle == "trading_enabled" and "order.submit.live" in set(connection.get("enabled_trade_scopes") or []),
        "db_canonical": True,
        "workspace_context": workspace_context_payload(root),
    }
    _audit("broker_connector.connected", result, str(args.get("principal_id") or "head-manager"), root)
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

    profile = _paper_profile("paper-trading", "paper", "KR", credential_ref="")

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
        "connections": [_serialize_connection(connection, workspace_root) for connection in BrokerConnection.objects.prefetch_related("accounts").all()],
        "db_canonical": True,
        "workspace_context": workspace_context_payload(workspace_root),
    }


def get_broker_connection_status(workspace_root: Path | str | None, args: dict[str, Any]) -> dict[str, Any]:
    connection = _get_connection(workspace_root, args.get("broker_id") or args.get("broker_connection_id") or "paper-trading")
    source_status = broker_connection_provider_source_status(connection, workspace_root)
    if source_status.get("service_restart_required"):
        health = BrokerHealth("blocked", "broker provider source changed; restart TradingCodex service and revalidate connector", source_status)
    else:
        adapter = adapter_for_connection(connection, workspace_root)
        health = adapter.health_check()
    _reconcile_validation_execution_status(connection, health, workspace_root, enable_trade_scopes=bool(args.get("promote_execution", True)))
    return {
        "connection": _serialize_connection(connection, workspace_root),
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
        _reconcile_validation_execution_status(connection, adapter.health_check(), workspace_root)
        connection.last_sync_at = sync_run.finished_at
        connection.save(update_fields=["last_sync_at", "updated_at"])
    except Exception as exc:
        sync_run.status = "error"
        sync_run.error = str(exc)
        sync_run.finished_at = django_timezone.now()
        sync_run.save(update_fields=["status", "error", "finished_at"])
        error_message = _safe_error(exc)
        _reconcile_validation_execution_status(connection, BrokerHealth("error", error_message, _health_details_for_connection(connection, error_message)), workspace_root)
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
        source=connection.broker_id,
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


def _paper_profile(broker_id: str, environment: str, region: str, *, credential_ref: str) -> dict[str, Any]:
    return PAPER_PROVIDER.as_profile(broker_id=broker_id, environment=environment, region=region, credential_ref=credential_ref)


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
    if profile.get("execution_posture") in BROKER_LIVE_EXECUTION_POSTURES:
        capabilities.extend(["order.submit.live", "order.cancel.live", "order.status.live", "fills.read"])
    return capabilities


def _read_scopes_from_profile(profile: dict[str, Any]) -> list[str]:
    return [capability for capability in _capabilities_from_profile(profile) if capability.endswith(".read") or capability == "order.preview"]


def _trade_scopes_from_profile(profile: dict[str, Any]) -> list[str]:
    if profile.get("execution_posture") in BROKER_VALIDATION_EXECUTION_POSTURES:
        return ["order.submit.validation"]
    if profile.get("execution_posture") in BROKER_LIVE_EXECUTION_POSTURES:
        return ["order.submit.live", "order.cancel.live", "order.status.live", "fills.read"]
    return []


def _constraints_from_profile(profile: dict[str, Any], symbol: str, args: dict[str, Any]) -> BrokerInstrumentConstraints:
    order_model = profile.get("order_model") if isinstance(profile.get("order_model"), dict) else {}
    instrument_model = profile.get("instrument_model") if isinstance(profile.get("instrument_model"), dict) else {}
    asset_class = str(args.get("asset_class") or (profile.get("asset_classes") or ["unknown"])[0])
    product_type = str(args.get("product_type") or (profile.get("products") or ["spot"])[0])
    notes = []
    if instrument_model.get("filters"):
        notes.append("broker/exchange filters required: " + ", ".join(instrument_model["filters"]))
    if profile.get("execution_posture") in BROKER_LIVE_EXECUTION_POSTURES:
        notes.append("live execution requires installed provider, policy, env, approval, confirmation, and audit gates")
    elif profile.get("execution_posture") not in EXECUTION_ENABLED_POSTURES:
        notes.append("execution disabled until provider review")
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
    if profile.get("execution_posture") not in EXECUTION_ENABLED_POSTURES:
        reasons.append(f"execution posture is not supported by installed provider: {profile.get('execution_posture') or 'unknown'}")
    return reasons


def _broker_payload_preview(profile: dict[str, Any], order: dict[str, Any]) -> dict[str, Any]:
    family = str(profile.get("family") or "")
    symbol = str(order.get("venue_symbol") or order.get("market") or order.get("symbol") or "")
    side = str(order.get("side") or "").lower()
    if family == "crypto_exchange":
        return {"symbol": symbol.replace("-", ""), "side": side.upper(), "type": str(order.get("order_type") or "limit").upper(), "newClientOrderId": order.get("client_order_id", "")}
    return {"symbol": symbol, "side": side, "type": order.get("order_type", "limit"), "client_order_id": order.get("client_order_id", "")}


def canonical_order_from_order(order: dict[str, Any], profile: dict[str, Any] | None = None) -> dict[str, Any]:
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


def _reconcile_validation_execution_status(
    connection: Any,
    health: BrokerHealth,
    workspace_root: Path | str | None = None,
    *,
    enable_trade_scopes: bool = True,
) -> None:
    metadata = connection.metadata if isinstance(connection.metadata, dict) else {}
    profile = metadata.get("capability_profile") if isinstance(metadata.get("capability_profile"), dict) else {}
    posture = profile.get("execution_posture")
    update_fields = {"last_health_status", "updated_at"}
    connection.last_health_status = health.status
    source_status = broker_connection_provider_source_status(connection, workspace_root)
    service_restart_required = bool(source_status.get("service_restart_required"))
    if service_restart_required:
        connection.drift_status = "restart_required"
        update_fields.add("drift_status")
    if posture in BROKER_VALIDATION_EXECUTION_POSTURES | BROKER_LIVE_EXECUTION_POSTURES:
        adapter_enabled = True
        if posture in BROKER_LIVE_EXECUTION_POSTURES:
            try:
                from apps.integrations.models import AdapterDefinition

                provider_id = str(metadata.get("provider_id") or profile.get("provider_id") or connection.adapter_type or "")
                adapter_enabled = AdapterDefinition.objects.filter(adapter_id=provider_id, enabled=True, live=True).exists()
            except Exception:
                adapter_enabled = False
        metadata = dict(metadata)
        profile = dict(profile)
        metadata["capability_profile"] = profile
        metadata["credential_validation_status"] = health.status
        if health.message:
            metadata["credential_validation_message"] = health.message[:500]
        if health.details:
            metadata["credential_validation_details"] = health.details
        if service_restart_required:
            metadata["service_restart_required"] = True
            metadata["service_restart_reason"] = "broker provider source changed; restart TradingCodex service and revalidate connector"
        if health.status == "ok" and adapter_enabled and not service_restart_required:
            if source_status.get("source_hash"):
                profile["provider_source"] = {
                    **(profile.get("provider_source") if isinstance(profile.get("provider_source"), dict) else {}),
                    **source_status,
                    "source_hash": source_status["source_hash"],
                    "registered_source_hash": source_status["source_hash"],
                    "service_restart_required": False,
                    "drift_status": "none",
                }
            if enable_trade_scopes:
                connection.status = "trading_enabled"
                connection.enabled_trade_scopes = sorted(set(_trade_scopes_from_profile(profile)))
            else:
                if connection.status == "trading_enabled":
                    connection.status = "read_only"
                connection.enabled_trade_scopes = []
            connection.drift_status = "none"
            metadata["service_restart_required"] = False
            metadata["validation_execution_enabled"] = enable_trade_scopes and posture in BROKER_VALIDATION_EXECUTION_POSTURES and bool(connection.enabled_trade_scopes)
            metadata["live_execution_enabled"] = enable_trade_scopes and posture in BROKER_LIVE_EXECUTION_POSTURES and bool(connection.enabled_trade_scopes)
        else:
            if connection.status == "trading_enabled":
                connection.status = "read_only"
            connection.enabled_trade_scopes = []
            metadata["validation_execution_enabled"] = False
            metadata["live_execution_enabled"] = False
            if posture in BROKER_LIVE_EXECUTION_POSTURES and not adapter_enabled:
                metadata["live_adapter_enabled"] = False
                metadata["live_adapter_blocker"] = "live AdapterDefinition must be enabled before trading"
        connection.metadata = metadata
        update_fields.update({"status", "enabled_trade_scopes", "metadata", "drift_status"})
    connection.save(update_fields=sorted(update_fields))


def _health_details_for_connection(connection: Any, message: str) -> dict[str, Any]:
    metadata = connection.metadata if isinstance(connection.metadata, dict) else {}
    profile = metadata.get("capability_profile") if isinstance(metadata.get("capability_profile"), dict) else {}
    return {
        "code": "broker_health_error",
        "category": "credential_or_connectivity",
        "execution_posture": profile.get("execution_posture") or "unknown",
        "retry_after_external_fix": True,
    }


def _serialize_connection(connection: Any, workspace_root: Path | str | None = None) -> dict[str, Any]:
    metadata = connection.metadata if isinstance(connection.metadata, dict) else {}
    profile = metadata.get("capability_profile") if isinstance(metadata.get("capability_profile"), dict) else {}
    provider_source = broker_connection_provider_source_status(connection, workspace_root)
    provider_drifted = provider_source.get("drift_status") not in {"", "none", None}
    service_restart_required = bool(metadata.get("service_restart_required") or provider_source.get("service_restart_required"))
    locked_for_provider_source = bool(service_restart_required or provider_drifted)
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
        "drift_status": "restart_required" if service_restart_required else "review_required" if provider_drifted else connection.drift_status,
        "trading_status": "locked" if locked_for_provider_source else "enabled" if connection.enabled_trade_scopes and connection.status == "trading_enabled" else "locked",
        "lifecycle_state": "review_required" if locked_for_provider_source else _connector_lifecycle_state(connection),
        "provider_id": metadata.get("provider_id", profile.get("provider_id", "")),
        "connector_template": metadata.get("connector_template", profile.get("template_id", "")),
        "capability_profile": profile,
        "blockers": metadata.get("blockers") or _profile_blockers(profile),
        "provider_source": provider_source,
        "service_restart_required": service_restart_required,
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


def _safe_error(exc: Exception) -> str:
    text = str(exc)
    for marker in ("signature=", "X-MBX-APIKEY"):
        if marker in text:
            text = text.split(marker, 1)[0] + marker + "[redacted]"
    return text[:300]


def _connector_safe_id(value: str) -> str:
    normalized = "".join(ch.lower() if ch.isalnum() else "-" for ch in str(value or "").strip())
    while "--" in normalized:
        normalized = normalized.replace("--", "-")
    return normalized.strip("-")[:120]


def _connector_build_capabilities(profile: dict[str, Any]) -> list[str]:
    capabilities = {"market_data"}
    if profile.get("account_model"):
        capabilities.add("account_read")
    validation_model = profile.get("validation_model") if isinstance(profile.get("validation_model"), dict) else {}
    if validation_model:
        capabilities.add("order_preview")
    if profile.get("execution_posture") in BROKER_VALIDATION_EXECUTION_POSTURES:
        capabilities.add("broker_validation_only")
    if profile.get("execution_posture") in BROKER_LIVE_EXECUTION_POSTURES:
        capabilities.add("live_capable_provider")
    return sorted(capabilities)


def _connector_lifecycle_state(connection: Any, *, requested_mode: str = "") -> str:
    if not connection:
        return "scaffolded"
    if isinstance(connection, dict):
        status = str(connection.get("status") or "")
        enabled_trade_scopes = set(connection.get("enabled_trade_scopes") or [])
        blockers = set(str(item) for item in connection.get("blockers") or [])
        metadata = connection.get("metadata") if isinstance(connection.get("metadata"), dict) else {}
        service_restart_required = bool(connection.get("service_restart_required"))
    else:
        status = str(getattr(connection, "status", "") or "")
        enabled_trade_scopes = set(getattr(connection, "enabled_trade_scopes", None) or [])
        metadata = getattr(connection, "metadata", None) if isinstance(getattr(connection, "metadata", None), dict) else {}
        profile = metadata.get("capability_profile") if isinstance(metadata.get("capability_profile"), dict) else {}
        blockers = set(str(item) for item in metadata.get("blockers") or _profile_blockers(profile))
        service_restart_required = bool(metadata.get("service_restart_required"))
    if "provider_not_installed" in blockers:
        return "provider_missing"
    if service_restart_required:
        return "review_required"
    if status == "disabled":
        return "blocked"
    if requested_mode == "live-request" or metadata.get("live_execution_requested"):
        return "live_requested"
    if status == "trading_enabled" and "order.submit.live" in enabled_trade_scopes:
        return "trading_enabled"
    if status == "trading_enabled" and "order.submit.validation" in enabled_trade_scopes:
        return "validation_ready"
    if status == "read_only":
        return "read_only"
    return status or "scaffolded"


def _validate_credential_ref(credential_ref: str) -> None:
    if not credential_ref:
        return
    allowed_prefixes = ("env:", "os-keychain://")
    if any(ch.isspace() for ch in credential_ref) or not any(credential_ref.startswith(prefix) and len(credential_ref) > len(prefix) for prefix in allowed_prefixes):
        raise ValueError("credential_ref must be a reference such as env:NAME or os-keychain://broker/name; raw secrets are not accepted")


def _required_secret_refs(credential_ref: str, template: dict[str, Any]) -> list[str]:
    if not credential_ref.startswith("env:"):
        return [credential_ref]
    name = credential_ref.split(":", 1)[1].strip()
    auth_type = str((template.get("auth_model") or {}).get("type") or "")
    if "app_key_secret" in auth_type:
        return [f"{name}_APP_KEY", f"{name}_APP_SECRET"]
    if "hmac" in auth_type or "api_key" in auth_type or "jwt" in auth_type:
        return [f"{name}_API_KEY", f"{name}_SECRET_KEY"]
    return [name]


def _read_json_file(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _audit(action: str, payload: dict[str, Any], actor: str, workspace_root: Path | str | None) -> None:
    write_audit_event_if_available(workspace_root, actor, "service", {"type": action, "payload": payload})

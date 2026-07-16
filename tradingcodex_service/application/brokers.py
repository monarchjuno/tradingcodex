from __future__ import annotations

import ast
import hashlib
import ipaddress
import json
import os
import re
import shutil
import stat
import sys
import tempfile
import types
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit, urlunsplit

from django.db import transaction
from django.db.utils import DatabaseError
from django.utils import timezone as django_timezone

from tradingcodex_service.application.audit import write_audit_event_if_available, write_audit_event_required
from tradingcodex_service.application.common import (
    atomic_write_text,
    safe_provider_error,
    safe_workspace_path,
    stable_hash,
    workspace_launcher_command,
)
from tradingcodex_service.application.portfolio import (
    DEFAULT_PAPER_CASH,
    load_paper_portfolio_state,
    portfolio_keys,
)
from tradingcodex_service.application.operator_authority import (
    PROVIDER_SOURCE_APPROVE,
    PROVIDER_SOURCE_REVOKE,
    OperatorAuthority,
    OperatorServiceAuthority,
    consume_operator_authority,
    consume_service_operator_authority,
    provider_source_approval_resource,
    provider_source_revocation_resource,
)
from tradingcodex_service.application.runtime import (
    active_profile_for_workspace,
    base_currency_for_workspace,
    ensure_runtime_database,
    normalize_currency_code,
    tradingcodex_home,
    workspace_context_payload,
)


def _required_currency_code(value: Any, field: str = "currency") -> str:
    if value in (None, ""):
        raise ValueError(f"{field} is required")
    return normalize_currency_code(value, field)


@dataclass(frozen=True)
class BrokerHealth:
    status: str
    message: str = ""
    details: dict[str, Any] | None = None


BROKER_HEALTH_STATUSES = frozenset({"ok", "error", "blocked", "disabled"})
BROKER_HEALTH_MESSAGES = {
    "ok": "broker health check succeeded",
    "error": "broker health check failed",
    "blocked": "broker health check is blocked",
    "disabled": "broker health check is disabled",
}


def canonical_broker_health(value: Any) -> BrokerHealth:
    """Reduce untrusted adapter health output to service-owned fields."""

    status = value.status if isinstance(value, BrokerHealth) and isinstance(value.status, str) else "error"
    status = status.strip().lower()
    if status not in BROKER_HEALTH_STATUSES:
        status = "error"
    return BrokerHealth(
        status,
        BROKER_HEALTH_MESSAGES[status],
        {
            "code": f"broker_health_{status}",
            "retry_after_external_fix": status != "ok",
        },
    )


def broker_adapter_health(adapter: Any) -> BrokerHealth:
    """Call an adapter health probe without exposing provider text or details."""

    try:
        health = adapter.health_check()
    except Exception:
        health = BrokerHealth("error")
    return canonical_broker_health(health)


@dataclass(frozen=True)
class BrokerAccountDTO:
    broker_account_id: str
    account_label: str
    account_type: str
    base_currency: str
    masked_identifier: str
    trading_enabled: bool
    metadata: dict[str, Any] | None = None


BROKER_ACCOUNT_TYPES = frozenset(
    {
        "brokerage",
        "cash",
        "corporate",
        "individual",
        "ira",
        "joint",
        "live",
        "margin",
        "paper",
        "retirement",
        "tax-advantaged",
        "taxable",
        "unknown",
    }
)


def canonical_broker_account(value: Any, broker_id: str) -> BrokerAccountDTO:
    """Keep required account identity while replacing provider display metadata."""

    if not isinstance(value, BrokerAccountDTO):
        raise ValueError("broker account result must use BrokerAccountDTO")
    account_id = value.broker_account_id if isinstance(value.broker_account_id, str) else ""
    if (
        not account_id
        or account_id != account_id.strip()
        or len(account_id) > 160
        or any(character.isspace() or not character.isprintable() for character in account_id)
    ):
        raise ValueError("broker account identifier is invalid")
    account_type = str(value.account_type or "").strip().lower().replace("_", "-")
    if account_type not in BROKER_ACCOUNT_TYPES:
        account_type = "unknown"
    fingerprint = stable_hash({"broker_id": broker_id, "broker_account_id": account_id})[:10]
    return BrokerAccountDTO(
        broker_account_id=account_id,
        account_label=f"Broker account {fingerprint}",
        account_type=account_type,
        base_currency=_required_currency_code(value.base_currency, "base_currency"),
        masked_identifier=f"acct-{fingerprint}",
        trading_enabled=value.trading_enabled is True,
        metadata={},
    )


@dataclass(frozen=True)
class CashDTO:
    currency: str
    amount: float


@dataclass(frozen=True)
class PositionDTO:
    symbol: str
    quantity: float
    average_price: float = 0
    currency: str = ""
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
    currency: str = ""
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
            "enabled_native_actions": [],
            "live": self.live,
            "blockers": [],
        }
        if auth_model.get("credential_ref_required") and not credential_ref:
            profile["blockers"].append("credential_ref_missing")
        if self.execution_posture in BROKER_DISABLED_EXECUTION_POSTURES:
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
BROKER_VALIDATION_EXECUTION_POSTURES = {"broker_validation_only"}
BROKER_DISABLED_EXECUTION_POSTURES = {"provider_development_required", "service_adapter_required", "unsupported"}
NON_LIVE_EXECUTION_POSTURES = {"paper_only", *BROKER_VALIDATION_EXECUTION_POSTURES}
EXECUTION_ENABLED_POSTURES = {*NON_LIVE_EXECUTION_POSTURES, *BROKER_LIVE_EXECUTION_POSTURES}
SUPPORTED_BROKER_EXECUTION_POSTURES = {*EXECUTION_ENABLED_POSTURES, *BROKER_DISABLED_EXECUTION_POSTURES}
PAPER_PROVIDER = BrokerAdapterProvider(
    provider_id="paper",
    display_name="Paper",
    family="paper",
    venue="paper",
    region="global",
    asset_classes=("equity", "etf", "cash"),
    products=("paper",),
    default_environment="paper",
    auth_model={"type": "none", "credential_ref_required": False},
    account_model={"multi_account": False, "balances": "cash", "positions": True},
    instrument_model={"identity": "symbol", "examples": ["AAPL", "SAP"]},
    order_model={"sides": ["buy", "sell"], "order_types": ["limit"], "time_in_force": ["day"], "quantity_modes": ["quantity"]},
    validation_model={"preview": True, "dry_run": True},
    event_model={"polling": True, "streaming": False, "fills": False},
    execution_posture="paper_only",
    live=False,
    factory=lambda connection, workspace_root: PaperBrokerAdapter(workspace_root),
)
WORKSPACE_PROVIDER_ROOT = Path("trading/connectors")
WORKSPACE_PROVIDER_FILENAME = "provider.py"
PROVIDER_SOURCE_PROVENANCE_FILENAME = "source-provenance.json"
PROVIDER_SNAPSHOT_ROOT = Path("provider-snapshots/v1")
PROVIDER_SNAPSHOT_MANIFEST = ".tradingcodex-provider-snapshot.json"
CONNECTOR_RUNTIME_FILES = frozenset({"connector-profile.json", "secret-schema.json", "README.md"})
PROVIDER_VCS_METADATA_NAMES = frozenset({".git", ".hg", ".svn"})
PROVIDER_SECRET_FILENAMES = frozenset(
    {
        ".netrc",
        "_netrc",
        "credentials",
        "credentials.json",
        "credentials.toml",
        "credentials.yaml",
        "credentials.yml",
        "id_dsa",
        "id_ecdsa",
        "id_ed25519",
        "id_rsa",
        "secrets.json",
        "secrets.toml",
        "secrets.yaml",
        "secrets.yml",
    }
)
PROVIDER_SECRET_FILE_SUFFIXES = frozenset(
    {".der", ".jks", ".key", ".keystore", ".p12", ".pfx", ".pkcs12", ".pem"}
)
PROVIDER_PROVENANCE_SCHEMA_VERSION = 1
MAX_PROVIDER_PROVENANCE_SOURCES = 64
MAX_WORKSPACE_PROVIDER_FILE_BYTES = 2_000_000
MAX_WORKSPACE_PROVIDER_BUNDLE_BYTES = 10_000_000
MAX_WORKSPACE_PROVIDER_FILES = 256
_PROVIDER_RUNTIME_STARTED_AT = datetime.now(timezone.utc)
_BROKER_ADAPTER_PROVIDERS: dict[str, BrokerAdapterProvider] = {}
_WORKSPACE_PROVIDER_CACHE: dict[tuple[str, str, str], BrokerAdapterProvider] = {}
_WORKSPACE_PROVIDER_SOURCES: dict[tuple[str, str, str], dict[str, str]] = {}


@dataclass(frozen=True)
class WorkspaceProviderBundle:
    provider_dir: Path
    provider_path: Path
    relative_path: str
    files: tuple[tuple[str, bytes, str], ...]
    source_bytes: bytes
    source_sha256: str
    bundle_sha256: str
    source_provenance: dict[str, Any] | None


def _validate_provider_id(value: Any, *, required: bool = True) -> str:
    provider_id = str(value or "")
    if not provider_id:
        if required:
            raise ValueError("provider_id is required")
        return ""
    if _connector_safe_id(provider_id) != provider_id:
        raise ValueError("provider_id must be a lowercase connector-safe id using letters, digits, and hyphens")
    return provider_id


def register_broker_adapter_provider(provider: BrokerAdapterProvider) -> None:
    _validate_broker_adapter_provider(provider)
    _BROKER_ADAPTER_PROVIDERS[provider.provider_id] = provider


def _validate_broker_adapter_provider(provider: BrokerAdapterProvider) -> None:
    if not isinstance(provider, BrokerAdapterProvider):
        raise ValueError("broker provider must use BrokerAdapterProvider")
    _validate_provider_id(provider.provider_id)
    if provider.execution_posture not in SUPPORTED_BROKER_EXECUTION_POSTURES:
        supported = ", ".join(sorted(SUPPORTED_BROKER_EXECUTION_POSTURES))
        raise ValueError(f"unsupported broker execution posture: {provider.execution_posture}; expected one of {supported}")


def get_broker_adapter_provider(provider_id: str, workspace_root: Path | str | None = None) -> BrokerAdapterProvider | None:
    provider_id = _validate_provider_id(provider_id, required=False)
    if not provider_id:
        return None
    if provider_id == PAPER_PROVIDER.provider_id:
        return PAPER_PROVIDER
    return _BROKER_ADAPTER_PROVIDERS.get(provider_id) or _load_workspace_broker_adapter_provider(provider_id, workspace_root)


def list_broker_adapter_providers(workspace_root: Path | str | None = None, args: dict[str, Any] | None = None) -> dict[str, Any]:
    args = args or {}
    root = Path(workspace_root or os.environ.get("TRADINGCODEX_WORKSPACE_ROOT") or ".").expanduser().resolve()
    family = str(args.get("family") or "")
    asset_class = str(args.get("asset_class") or "")
    workspace_providers: list[BrokerAdapterProvider] = []
    workspace_sources: list[dict[str, Any]] = []
    connector_root = _safe_connector_root(root)
    if connector_root.exists():
        for provider_dir in sorted(connector_root.iterdir(), key=lambda item: item.name):
            provider_path = provider_dir / WORKSPACE_PROVIDER_FILENAME
            if not provider_path.exists():
                continue
            try:
                provider_id = _validate_provider_id(provider_dir.name)
                if provider_id in _BROKER_ADAPTER_PROVIDERS:
                    continue
                source_status = broker_provider_source_status(provider_id, root)
                workspace_sources.append(source_status)
                if source_status.get("approval_status") != "approved" or source_status.get("service_restart_required"):
                    continue
                workspace_providers.append(_load_workspace_broker_adapter_provider(provider_id, root))
            except (OSError, PermissionError, ValueError):
                workspace_sources.append(
                    {
                        "kind": "workspace",
                        "provider_id": provider_dir.name,
                        "path": (WORKSPACE_PROVIDER_ROOT / provider_dir.name / WORKSPACE_PROVIDER_FILENAME).as_posix(),
                        "approval_status": "blocked",
                        "drift_status": "unsafe_or_invalid_source",
                        "service_restart_required": True,
                    }
                )
    providers = []
    candidates = [PAPER_PROVIDER, *_BROKER_ADAPTER_PROVIDERS.values(), *workspace_providers]
    for provider in sorted(candidates, key=lambda item: item.provider_id):
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
        "workspace_provider_sources": workspace_sources,
        "blocked_surfaces": list(BLOCKED_BROKER_SURFACES),
        "db_canonical": True,
        "workspace_context": workspace_context_payload(root),
    }


def _load_workspace_broker_adapter_provider(provider_id: str, workspace_root: Path | str | None = None) -> BrokerAdapterProvider | None:
    provider_id = _validate_provider_id(provider_id)
    root = Path(workspace_root or os.environ.get("TRADINGCODEX_WORKSPACE_ROOT") or ".").expanduser().resolve()
    relative_path = _workspace_provider_relative_path(provider_id)
    safe_workspace_path(root, relative_path, allowed_roots=(WORKSPACE_PROVIDER_ROOT,))
    if not (root / relative_path).exists():
        return None
    workspace_bundle = _read_workspace_provider_bundle(root, provider_id)
    context = workspace_context_payload(root)
    ensure_runtime_database(root)
    approval = _approved_workspace_provider_source(
        context,
        provider_id,
        relative_path.as_posix(),
        workspace_bundle.source_sha256,
        workspace_bundle.bundle_sha256,
    )
    if approval is None:
        raise PermissionError(
            f"workspace broker provider {provider_id} requires explicit operator approval for bundle {workspace_bundle.bundle_sha256}"
        )
    if approval.approved_at > _PROVIDER_RUNTIME_STARTED_AT:
        raise PermissionError(
            f"workspace broker provider {provider_id} was approved after this process started; restart TradingCodex"
        )
    cache_key = _workspace_provider_key(context, provider_id)
    cached_source = _WORKSPACE_PROVIDER_SOURCES.get(cache_key, {})
    if cached_source and cached_source.get("bundle_hash") != workspace_bundle.bundle_sha256:
        raise PermissionError(
            f"workspace broker provider {provider_id} changed after this process loaded it; restart TradingCodex"
        )
    snapshot_bundle = _read_provider_snapshot(
        context,
        provider_id,
        workspace_bundle.source_sha256,
        workspace_bundle.bundle_sha256,
        str(approval.snapshot_relative_path),
    )
    if snapshot_bundle.relative_path != relative_path.as_posix():
        raise PermissionError("approved provider snapshot entry path does not match its DB binding")
    cached = _WORKSPACE_PROVIDER_CACHE.get(cache_key)
    if cached is not None and cached_source.get("bundle_hash") == workspace_bundle.bundle_sha256:
        return cached
    package_name = (
        f"_tcx_broker_provider_{provider_id.replace('-', '_')}_"
        f"{str(context['workspace_id'])[-12:]}_{str(context['path_hash'])[:12]}_{workspace_bundle.bundle_sha256[:12]}"
    )
    module_name = f"{package_name}.provider"
    package = types.ModuleType(package_name)
    package.__file__ = str(snapshot_bundle.provider_dir / "__init__.py")
    package.__package__ = package_name
    package.__path__ = [str(snapshot_bundle.provider_dir)]
    module = types.ModuleType(module_name)
    module.__file__ = str(snapshot_bundle.provider_path)
    module.__package__ = package_name
    sys.modules[package_name] = package
    sys.modules[module_name] = module
    try:
        code = compile(snapshot_bundle.source_bytes, str(snapshot_bundle.provider_path), "exec", dont_inherit=True)
        exec(code, module.__dict__)
    except Exception:
        _remove_workspace_provider_modules(package_name)
        raise ValueError(f"approved workspace broker provider {provider_id} failed to load") from None
    provider = getattr(module, "PROVIDER", None)
    if provider is None and hasattr(module, "get_provider"):
        try:
            provider = module.get_provider()
        except Exception:
            _remove_workspace_provider_modules(package_name)
            raise ValueError(f"approved workspace broker provider {provider_id} failed to load") from None
    try:
        _validate_broker_adapter_provider(provider)
    except Exception:
        _remove_workspace_provider_modules(package_name)
        raise ValueError(f"approved workspace broker provider {provider_id} failed validation") from None
    if provider.provider_id != provider_id:
        _remove_workspace_provider_modules(package_name)
        raise ValueError(
            f"workspace provider path {provider_id} does not match declared provider_id={provider.provider_id}"
        )
    _WORKSPACE_PROVIDER_CACHE[cache_key] = provider
    _record_workspace_provider_source(
        cache_key,
        relative_path=workspace_bundle.relative_path,
        source_sha256=workspace_bundle.source_sha256,
        bundle_sha256=workspace_bundle.bundle_sha256,
        snapshot_relative_path=str(approval.snapshot_relative_path),
        approval_id=str(approval.pk),
        module_prefix=package_name,
    )
    return provider


def approve_workspace_broker_provider_source(
    workspace_root: Path | str,
    provider_id: str,
    *,
    expected_bundle_sha256: str,
    operator_authority: OperatorAuthority | None = None,
) -> dict[str, Any]:
    """Approve exactly one inert workspace provider source from the operator CLI."""

    root = Path(workspace_root).expanduser().resolve()
    provider_id = _validate_provider_id(provider_id)
    expected_bundle_sha256 = str(expected_bundle_sha256 or "").lower()
    if len(expected_bundle_sha256) != 64 or any(character not in "0123456789abcdef" for character in expected_bundle_sha256):
        raise ValueError("expected provider bundle SHA-256 is invalid")
    consume_service_operator_authority(
        operator_authority,
        root,
        action=PROVIDER_SOURCE_APPROVE,
        resource=provider_source_approval_resource(provider_id, expected_bundle_sha256),
    )
    bundle = _read_workspace_provider_bundle(root, provider_id)
    if expected_bundle_sha256 != bundle.bundle_sha256:
        raise PermissionError("workspace broker provider bundle changed after operator review")
    relative_path = bundle.relative_path
    context = workspace_context_payload(root)
    ensure_runtime_database(root)
    snapshot_relative_path = _write_provider_snapshot(context, provider_id, bundle)

    from apps.integrations.models import BrokerProviderSourceApproval

    now = django_timezone.now()
    with transaction.atomic():
        BrokerProviderSourceApproval.objects.select_for_update().filter(
            workspace_id=str(context["workspace_id"]),
            workspace_path_hash=str(context["path_hash"]),
            provider_id=provider_id,
            status=BrokerProviderSourceApproval.STATUS_APPROVED,
        ).exclude(
            relative_path=relative_path,
            source_sha256=bundle.source_sha256,
            bundle_sha256=bundle.bundle_sha256,
        ).update(
            status=BrokerProviderSourceApproval.STATUS_REVOKED,
            revoked_at=now,
        )
        approval, _created = BrokerProviderSourceApproval.objects.update_or_create(
            workspace_id=str(context["workspace_id"]),
            workspace_path_hash=str(context["path_hash"]),
            provider_id=provider_id,
            relative_path=relative_path,
            source_sha256=bundle.source_sha256,
            bundle_sha256=bundle.bundle_sha256,
            defaults={
                "snapshot_relative_path": snapshot_relative_path,
                "status": BrokerProviderSourceApproval.STATUS_APPROVED,
                "approved_by": "local-operator",
                "approved_at": now,
                "revoked_at": None,
            },
        )
        payload = {
            "provider_id": provider_id,
            "workspace_id": str(context["workspace_id"]),
            "workspace_path_hash": str(context["path_hash"]),
            "relative_path": relative_path,
            "source_sha256": bundle.source_sha256,
            "bundle_sha256": bundle.bundle_sha256,
            "snapshot_relative_path": snapshot_relative_path,
            "approval_id": approval.pk,
        }
        write_audit_event_required(
            root,
            "local-operator",
            "operator-cli",
            {
                "type": "broker_provider_source.approved",
                "resource": provider_id,
                "decision": "approved",
                "payload": payload,
            },
        )
    _evict_workspace_provider_cache(_workspace_provider_key(context, provider_id))
    return {
        "status": "approved",
        **payload,
        "service_restart_required": True,
        "next": "Restart TradingCodex before listing, registering, validating, or using this provider.",
        "db_canonical": True,
        "workspace_context": context,
    }


def revoke_workspace_broker_provider_source(
    workspace_root: Path | str,
    provider_id: str,
    *,
    operator_authority: OperatorAuthority | None = None,
) -> dict[str, Any]:
    """Revoke all active source approvals for one workspace provider."""

    root = Path(workspace_root).expanduser().resolve()
    provider_id = _validate_provider_id(provider_id)
    consume_service_operator_authority(
        operator_authority,
        root,
        action=PROVIDER_SOURCE_REVOKE,
        resource=provider_source_revocation_resource(provider_id),
    )
    context = workspace_context_payload(root)
    ensure_runtime_database(root)

    from apps.integrations.models import BrokerProviderSourceApproval

    now = django_timezone.now()
    with transaction.atomic():
        approvals = BrokerProviderSourceApproval.objects.select_for_update().filter(
            workspace_id=str(context["workspace_id"]),
            workspace_path_hash=str(context["path_hash"]),
            provider_id=provider_id,
            status=BrokerProviderSourceApproval.STATUS_APPROVED,
        )
        revoked = approvals.update(
            status=BrokerProviderSourceApproval.STATUS_REVOKED,
            revoked_at=now,
        )
        payload = {
            "provider_id": provider_id,
            "workspace_id": str(context["workspace_id"]),
            "workspace_path_hash": str(context["path_hash"]),
            "revoked_count": revoked,
        }
        write_audit_event_required(
            root,
            "local-operator",
            "operator-cli",
            {
                "type": "broker_provider_source.revoked",
                "resource": provider_id,
                "decision": "revoked" if revoked else "not_approved",
                "payload": payload,
            },
        )
    cache_key = _workspace_provider_key(context, provider_id)
    _evict_workspace_provider_cache(cache_key)
    return {
        "status": "revoked" if revoked else "not_approved",
        **payload,
        "db_canonical": True,
        "workspace_context": context,
    }


def inspect_workspace_broker_provider_source(
    workspace_root: Path | str,
    provider_id: str,
) -> dict[str, Any]:
    """Return review metadata without importing or executing provider source."""

    root = Path(workspace_root).expanduser().resolve()
    provider_id = _validate_provider_id(provider_id)
    return broker_provider_source_status(provider_id, root, allow_ledger_unavailable=True)


def _connector_path_is_link_like(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise ValueError("workspace connector path metadata is unavailable") from exc
    attributes = int(getattr(metadata, "st_file_attributes", 0) or 0)
    return bool(
        stat.S_ISLNK(metadata.st_mode)
        or attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )


def _safe_connector_root(root: Path) -> Path:
    current = root
    for part in WORKSPACE_PROVIDER_ROOT.parts:
        current /= part
        if _connector_path_is_link_like(current):
            raise ValueError("workspace connector root must not contain symlinks")
    return safe_workspace_path(root, WORKSPACE_PROVIDER_ROOT, allowed_roots=(WORKSPACE_PROVIDER_ROOT,))


def _safe_connector_directory(root: Path, connector_id: str, *, create: bool = False) -> Path:
    connector_id = _connector_safe_id(connector_id)
    if not connector_id:
        raise ValueError("connector id is required")
    connector_root = _safe_connector_root(root)
    relative_path = WORKSPACE_PROVIDER_ROOT / connector_id
    raw_connector_dir = connector_root / connector_id
    if _connector_path_is_link_like(raw_connector_dir):
        raise ValueError("workspace connector directory must not be a symlink")
    connector_dir = safe_workspace_path(root, relative_path, allowed_roots=(WORKSPACE_PROVIDER_ROOT,))
    if create:
        raw_connector_dir.mkdir(parents=True, exist_ok=True)
        if (
            _connector_path_is_link_like(raw_connector_dir)
            or raw_connector_dir.resolve(strict=True) != connector_dir
        ):
            raise ValueError("workspace connector directory changed during creation")
    return connector_dir


def _workspace_provider_relative_path(provider_id: str) -> Path:
    return WORKSPACE_PROVIDER_ROOT / _validate_provider_id(provider_id) / WORKSPACE_PROVIDER_FILENAME


def _read_workspace_provider_bundle(root: Path, provider_id: str) -> WorkspaceProviderBundle:
    provider_id = _validate_provider_id(provider_id)
    provider_dir = _safe_connector_directory(root, provider_id)
    relative_path = _workspace_provider_relative_path(provider_id)
    raw_provider_path = root / relative_path
    if _connector_path_is_link_like(raw_provider_path):
        raise ValueError("workspace broker provider source must not be a symlink")
    provider_path = safe_workspace_path(root, relative_path, allowed_roots=(WORKSPACE_PROVIDER_ROOT,))
    if not provider_dir.exists() or not provider_dir.is_dir():
        raise ValueError(f"workspace broker provider directory is missing: {provider_dir.relative_to(root).as_posix()}")
    return _collect_provider_bundle(
        provider_dir,
        provider_path,
        logical_relative_path=relative_path.as_posix(),
        snapshot=False,
    )


def _collect_provider_bundle(
    provider_dir: Path,
    provider_path: Path,
    *,
    logical_relative_path: str,
    snapshot: bool,
) -> WorkspaceProviderBundle:
    try:
        provider_directory_metadata = provider_dir.lstat()
    except OSError as exc:
        raise ValueError("broker provider bundle directory metadata is unavailable") from exc
    provider_directory_attributes = int(
        getattr(provider_directory_metadata, "st_file_attributes", 0) or 0
    )
    if (
        stat.S_ISLNK(provider_directory_metadata.st_mode)
        or provider_directory_attributes
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        or provider_path.is_symlink()
    ):
        raise ValueError("broker provider bundle must not contain symlinks")
    if not stat.S_ISDIR(provider_directory_metadata.st_mode):
        raise ValueError("broker provider bundle root must be a directory")
    files: list[tuple[str, bytes, str]] = []
    total_bytes = 0
    for current_dir, directory_names, file_names in os.walk(provider_dir, topdown=True, followlinks=False):
        current_path = Path(current_dir)
        if current_path.is_symlink():
            raise ValueError("broker provider bundle must not traverse symlinks")
        retained_directories: list[str] = []
        for directory_name in sorted(directory_names):
            directory_path = current_path / directory_name
            try:
                directory_metadata = directory_path.lstat()
            except OSError as exc:
                raise ValueError("broker provider bundle directory metadata is unavailable") from exc
            directory_attributes = int(getattr(directory_metadata, "st_file_attributes", 0) or 0)
            if stat.S_ISLNK(directory_metadata.st_mode) or directory_attributes & getattr(
                stat,
                "FILE_ATTRIBUTE_REPARSE_POINT",
                0x400,
            ):
                raise ValueError("broker provider bundle must not contain symlinked directories")
            if not stat.S_ISDIR(directory_metadata.st_mode):
                raise ValueError("broker provider bundle contains a non-directory entry")
            if directory_name == "__pycache__":
                continue
            if directory_name.casefold() in PROVIDER_VCS_METADATA_NAMES:
                raise ValueError("broker provider bundle must not contain VCS metadata directories")
            _validate_provider_bundle_member(directory_path.relative_to(provider_dir))
            retained_directories.append(directory_name)
        directory_names[:] = retained_directories
        for file_name in sorted(file_names):
            file_path = current_path / file_name
            try:
                file_metadata = file_path.lstat()
            except OSError as exc:
                raise ValueError("broker provider bundle file metadata is unavailable") from exc
            file_attributes = int(getattr(file_metadata, "st_file_attributes", 0) or 0)
            if stat.S_ISLNK(file_metadata.st_mode) or file_attributes & getattr(
                stat,
                "FILE_ATTRIBUTE_REPARSE_POINT",
                0x400,
            ):
                raise ValueError("broker provider bundle files must be regular and symlink-free")
            if not stat.S_ISREG(file_metadata.st_mode):
                raise ValueError("broker provider bundle files must be regular and symlink-free")
            relative_member = file_path.relative_to(provider_dir)
            if len(relative_member.parts) == 1 and file_name in CONNECTOR_RUNTIME_FILES:
                continue
            if file_name.endswith((".pyc", ".pyo")):
                continue
            if file_name.casefold() in PROVIDER_VCS_METADATA_NAMES:
                raise ValueError("broker provider bundle must not contain VCS metadata files")
            if _provider_file_is_secret_like(file_name):
                raise ValueError("broker provider bundle must not contain secret-like files")
            if (
                file_name.casefold() == PROVIDER_SOURCE_PROVENANCE_FILENAME
                and relative_member.as_posix() != PROVIDER_SOURCE_PROVENANCE_FILENAME
            ):
                raise ValueError(f"{PROVIDER_SOURCE_PROVENANCE_FILENAME} must be at the provider bundle root")
            if file_name == PROVIDER_SNAPSHOT_MANIFEST:
                if snapshot:
                    continue
                raise ValueError(f"workspace provider bundle reserves {PROVIDER_SNAPSHOT_MANIFEST}")
            _validate_provider_bundle_member(relative_member)
            data = _read_regular_file_exact(file_path, max_bytes=MAX_WORKSPACE_PROVIDER_FILE_BYTES)
            total_bytes += len(data)
            if total_bytes > MAX_WORKSPACE_PROVIDER_BUNDLE_BYTES:
                raise ValueError("broker provider bundle exceeds the total size limit")
            files.append((relative_member.as_posix(), data, hashlib.sha256(data).hexdigest()))
            if len(files) > MAX_WORKSPACE_PROVIDER_FILES:
                raise ValueError("broker provider bundle contains too many files")
    files.sort(key=lambda item: item[0])
    source_entry = next((item for item in files if item[0] == WORKSPACE_PROVIDER_FILENAME), None)
    if source_entry is None:
        raise ValueError(f"broker provider bundle requires {WORKSPACE_PROVIDER_FILENAME}")
    _validate_provider_source_contract(source_entry[1])
    provenance_entry = next(
        (item for item in files if item[0] == PROVIDER_SOURCE_PROVENANCE_FILENAME),
        None,
    )
    source_provenance = _validate_provider_source_provenance(provenance_entry[1]) if provenance_entry else None
    digest = hashlib.sha256()
    digest.update(b"TradingCodexProviderBundle\x00v1\x00")
    for relative_member, data, _file_sha256 in files:
        encoded_path = relative_member.encode("utf-8")
        digest.update(len(encoded_path).to_bytes(8, "big"))
        digest.update(encoded_path)
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
    return WorkspaceProviderBundle(
        provider_dir=provider_dir,
        provider_path=provider_path,
        relative_path=logical_relative_path,
        files=tuple(files),
        source_bytes=source_entry[1],
        source_sha256=source_entry[2],
        bundle_sha256=digest.hexdigest(),
        source_provenance=source_provenance,
    )


def _validate_provider_bundle_member(relative_path: Path) -> None:
    if relative_path.is_absolute() or not relative_path.parts:
        raise ValueError("broker provider bundle paths must be relative")
    for part in relative_path.parts:
        if part in {"", ".", ".."} or "\\" in part or ":" in part:
            raise ValueError("broker provider bundle contains an unsafe path")
        if part.rstrip(" .") != part:
            raise ValueError("broker provider bundle paths must not end with a dot or space")


class _ProviderEntrypointVisitor(ast.NodeVisitor):
    """Collect module-scope loader entrypoints without evaluating source."""

    def __init__(self) -> None:
        self.entrypoints: set[str] = set()

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Store) and node.id in {"PROVIDER", "get_provider"}:
            self.entrypoints.add(node.id)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        # A bare annotation does not create the module attribute consumed by
        # the loader. An annotated assignment with a value does.
        if node.value is not None:
            self.visit(node.target)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            bound_name = alias.asname or alias.name.partition(".")[0]
            if bound_name in {"PROVIDER", "get_provider"}:
                self.entrypoints.add(bound_name)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for alias in node.names:
            bound_name = alias.asname or alias.name
            if bound_name in {"PROVIDER", "get_provider"}:
                self.entrypoints.add(bound_name)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        if node.name == "get_provider":
            self.entrypoints.add(node.name)
        # Function bodies are a different scope and cannot establish the
        # module attributes consumed by the runtime loader.

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        # The provider loader is synchronous and does not await factories.
        return

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        # Class bodies are a different scope and class objects are not loader
        # entrypoints merely because they use a reserved entrypoint name.
        return

    def visit_Lambda(self, node: ast.Lambda) -> None:
        return

    def visit_ListComp(self, node: ast.ListComp) -> None:
        return

    def visit_SetComp(self, node: ast.SetComp) -> None:
        return

    def visit_DictComp(self, node: ast.DictComp) -> None:
        return

    def visit_GeneratorExp(self, node: ast.GeneratorExp) -> None:
        return


def _validate_provider_source_contract(source: bytes) -> None:
    """Validate provider syntax and its loader entrypoint without importing it."""

    try:
        module = ast.parse(source, filename=WORKSPACE_PROVIDER_FILENAME, mode="exec")
    except (RecursionError, SyntaxError, UnicodeError, ValueError) as exc:
        raise ValueError("workspace broker provider source must use valid Python syntax") from exc
    visitor = _ProviderEntrypointVisitor()
    try:
        visitor.visit(module)
    except RecursionError as exc:
        raise ValueError(
            "workspace broker provider source exceeds static analysis complexity limits"
        ) from exc
    if not visitor.entrypoints:
        raise ValueError(
            "workspace broker provider source must define module-level PROVIDER or get_provider"
        )


def _provider_file_is_secret_like(file_name: str) -> bool:
    normalized = file_name.casefold()
    if (
        normalized in {".env", ".envrc"}
        or normalized.startswith(".env.")
        or normalized.endswith((".env", ".envrc"))
    ):
        return True
    if normalized in PROVIDER_SECRET_FILENAMES:
        return True
    path = Path(normalized)
    if path.suffix in PROVIDER_SECRET_FILE_SUFFIXES:
        return True
    sensitive_stems = {
        "access-token",
        "access_token",
        "api-key",
        "api-keys",
        "api_key",
        "api_keys",
        "apikey",
        "apikeys",
        "auth",
        "credential",
        "credentials",
        "key",
        "keys",
        "password",
        "passwords",
        "private-key",
        "private_key",
        "refresh-token",
        "refresh_token",
        "secret",
        "secrets",
        "service-account",
        "service_account",
        "token",
        "tokens",
    }
    data_suffixes = {"", ".cfg", ".conf", ".ini", ".json", ".txt", ".toml", ".yaml", ".yml"}
    if path.suffix not in data_suffixes:
        return False
    if path.stem in sensitive_stems:
        return True
    stem_tokens = tuple(token for token in re.split(r"[^a-z0-9]+", path.stem) if token)
    sensitive_tokens = {
        "apikey",
        "apikeys",
        "auth",
        "credential",
        "credentials",
        "password",
        "passwords",
        "secret",
        "secrets",
        "token",
        "tokens",
    }
    if any(token in sensitive_tokens for token in stem_tokens):
        return True
    sensitive_pairs = {
        ("access", "token"),
        ("api", "key"),
        ("api", "keys"),
        ("private", "key"),
        ("refresh", "token"),
        ("service", "account"),
    }
    return any(pair in sensitive_pairs for pair in zip(stem_tokens, stem_tokens[1:]))


def _validate_provider_source_provenance(data: bytes) -> dict[str, Any]:
    try:
        document = json.loads(data.decode("utf-8"), object_pairs_hook=_json_object_without_duplicates)
    except (RecursionError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"{PROVIDER_SOURCE_PROVENANCE_FILENAME} must be valid duplicate-free UTF-8 JSON") from exc
    if not isinstance(document, dict):
        raise ValueError(f"{PROVIDER_SOURCE_PROVENANCE_FILENAME} must contain a JSON object")
    expected_fields = {"schema_version", "sources"}
    if set(document) != expected_fields:
        raise ValueError(
            f"{PROVIDER_SOURCE_PROVENANCE_FILENAME} requires exactly schema_version and sources"
        )
    schema_version = document.get("schema_version")
    if type(schema_version) is not int or schema_version != PROVIDER_PROVENANCE_SCHEMA_VERSION:
        raise ValueError(
            f"{PROVIDER_SOURCE_PROVENANCE_FILENAME} schema_version must be {PROVIDER_PROVENANCE_SCHEMA_VERSION}"
        )
    sources = document.get("sources")
    if not isinstance(sources, list) or not sources:
        raise ValueError(f"{PROVIDER_SOURCE_PROVENANCE_FILENAME} sources must be a non-empty list")
    if len(sources) > MAX_PROVIDER_PROVENANCE_SOURCES:
        raise ValueError(f"{PROVIDER_SOURCE_PROVENANCE_FILENAME} contains too many sources")
    return {
        "schema_version": PROVIDER_PROVENANCE_SCHEMA_VERSION,
        "sources": [
            _validate_provider_provenance_source(source, index=index)
            for index, source in enumerate(sources)
        ],
    }


def _json_object_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON field: {key}")
        result[key] = value
    return result


def _validate_provider_provenance_source(value: Any, *, index: int) -> dict[str, Any]:
    label = f"{PROVIDER_SOURCE_PROVENANCE_FILENAME} sources[{index}]"
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    common_fields = {"kind", "url", "fetched_content_sha256", "retrieved_at"}
    optional_fields = {"requested_ref", "resolved_ref", "resolved_commit"}
    if not common_fields.issubset(value) or not set(value).issubset(common_fields | optional_fields):
        raise ValueError(f"{label} contains missing or unknown fields")
    kind = value.get("kind")
    if kind not in {"https", "git"}:
        raise ValueError(f"{label} kind must be https or git")
    normalized: dict[str, Any] = {
        "kind": kind,
        "url": _validate_public_provider_source_url(value.get("url"), label=label, require_path=kind == "git"),
        "fetched_content_sha256": _validate_sha256(
            value.get("fetched_content_sha256"),
            field=f"{label} fetched_content_sha256",
        ),
        "retrieved_at": _validate_rfc3339(value.get("retrieved_at"), field=f"{label} retrieved_at"),
    }
    resolved_fields = {field for field in {"resolved_ref", "resolved_commit"} if field in value}
    if len(resolved_fields) != 1:
        raise ValueError(f"{label} requires exactly one of resolved_ref or resolved_commit")
    if "requested_ref" in value:
        normalized["requested_ref"] = _validate_provider_source_ref(
            value.get("requested_ref"),
            field=f"{label} requested_ref",
        )
    if "resolved_ref" in value:
        normalized["resolved_ref"] = _validate_provider_source_ref(
            value.get("resolved_ref"),
            field=f"{label} resolved_ref",
        )
    if "resolved_commit" in value:
        if kind != "git":
            raise ValueError(f"{label} HTTPS sources must use resolved_ref")
        commit_value = value.get("resolved_commit")
        resolved_commit = commit_value if isinstance(commit_value, str) else ""
        if not re.fullmatch(r"[0-9a-fA-F]{40}|[0-9a-fA-F]{64}", resolved_commit):
            raise ValueError(f"{label} resolved_commit must be a 40- or 64-character hexadecimal object id")
        normalized["resolved_commit"] = resolved_commit.lower()
    return normalized


def _validate_sha256(value: Any, *, field: str) -> str:
    text = value if isinstance(value, str) else ""
    if not re.fullmatch(r"[0-9a-f]{64}", text):
        raise ValueError(f"{field} must be a lowercase SHA-256 digest")
    return text


def _validate_rfc3339(value: Any, *, field: str) -> str:
    text = value if isinstance(value, str) else ""
    if not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})",
        text,
    ):
        raise ValueError(f"{field} must be an RFC 3339 timestamp")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        normalized = parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    except (OverflowError, ValueError) as exc:
        raise ValueError(f"{field} must be an RFC 3339 timestamp") from exc
    return normalized


def _validate_provider_source_ref(value: Any, *, field: str) -> str:
    text = value if isinstance(value, str) else ""
    if (
        not text
        or len(text) > 256
        or text != text.strip()
        or text.startswith("-")
        or text.startswith("/")
        or text.endswith(("/", ".", ".lock"))
        or "//" in text
        or ".." in text
        or "@{" in text
        or any(character.isspace() or ord(character) < 32 or ord(character) == 127 for character in text)
        or any(character in "\\~^:?*[" for character in text)
    ):
        raise ValueError(f"{field} is invalid")
    return text


def _validate_public_provider_source_url(value: Any, *, label: str, require_path: bool) -> str:
    url = value if isinstance(value, str) else ""
    if (
        not url
        or len(url) > 4096
        or url != url.strip()
        or "\\" in url
        or any(character.isspace() or ord(character) < 32 or ord(character) == 127 for character in url)
    ):
        raise ValueError(f"{label} URL is invalid")
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise ValueError(f"{label} URL is invalid") from exc
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError(f"{label} URL must be public HTTPS")
    if parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment:
        raise ValueError(f"{label} URL must not contain credentials, query, or fragment")
    try:
        hostname = parsed.hostname or ""
        ascii_hostname = hostname.encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise ValueError(f"{label} URL host is invalid") from exc
    if port not in {None, 443}:
        raise ValueError(f"{label} URL must use the standard HTTPS port")
    reserved_host_suffixes = (
        ".localhost",
        ".local",
        ".localdomain",
        ".internal",
        ".lan",
        ".home",
        ".home.arpa",
        ".invalid",
        ".test",
        ".example",
        ".onion",
        ".alt",
    )
    if (
        not ascii_hostname
        or ascii_hostname.endswith(".")
        or ascii_hostname == "localhost"
        or ascii_hostname in {suffix.removeprefix(".") for suffix in reserved_host_suffixes}
        or ascii_hostname.endswith(reserved_host_suffixes)
    ):
        raise ValueError(f"{label} URL must use a public host")
    try:
        address = ipaddress.ip_address(ascii_hostname)
    except ValueError:
        labels = ascii_hostname.split(".")
        if len(labels) < 2 or any(
            not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", host_label)
            for host_label in labels
        ):
            raise ValueError(f"{label} URL must use a public host")
        numeric_label = re.compile(r"(?:0x[0-9a-f]+|[0-9]+)", re.IGNORECASE)
        if all(numeric_label.fullmatch(host_label) for host_label in labels) or labels[-1].isdigit():
            raise ValueError(f"{label} URL must use a public host")
    else:
        if not address.is_global:
            raise ValueError(f"{label} URL must use a public host")
    if require_path and (not parsed.path or parsed.path == "/"):
        raise ValueError(f"{label} Git URL must identify a repository path")
    display_host = f"[{ascii_hostname}]" if ":" in ascii_hostname else ascii_hostname
    netloc = display_host + (f":{port}" if port is not None else "")
    return urlunsplit(("https", netloc, parsed.path, "", ""))


def _read_regular_file_exact(path: Path, *, max_bytes: int) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_CLOEXEC", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ValueError("broker provider file could not be opened safely") from exc
    try:
        opened_stat = os.fstat(descriptor)
        if not stat.S_ISREG(opened_stat.st_mode):
            raise ValueError("broker provider bundle members must be regular files")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, 64 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise ValueError("broker provider bundle member exceeds the size limit")
            chunks.append(chunk)
    finally:
        os.close(descriptor)
    try:
        if path.is_symlink():
            raise ValueError("broker provider file changed during review")
        current_stat = path.stat(follow_symlinks=False)
    except OSError as exc:
        raise ValueError("broker provider file changed during review") from exc
    if (
        current_stat.st_dev != opened_stat.st_dev
        or current_stat.st_ino != opened_stat.st_ino
        or current_stat.st_size != opened_stat.st_size
        or current_stat.st_mtime_ns != opened_stat.st_mtime_ns
    ):
        raise ValueError("broker provider file changed during review")
    return b"".join(chunks)


def _provider_snapshot_relative_path(
    context: dict[str, Any],
    provider_id: str,
    bundle_sha256: str,
) -> Path:
    return (
        PROVIDER_SNAPSHOT_ROOT
        / str(context["workspace_id"])
        / str(context["path_hash"])
        / _validate_provider_id(provider_id)
        / bundle_sha256
    )


def _snapshot_home_path(relative_path: Path, *, create_parents: bool = False) -> Path:
    if relative_path.is_absolute() or any(part in {"", ".", ".."} for part in relative_path.parts):
        raise ValueError("provider snapshot path must be a safe relative path")
    raw_home = tradingcodex_home().expanduser()
    if raw_home.is_symlink():
        raise ValueError("TradingCodex home must not be a symlink for provider snapshots")
    home = raw_home.resolve(strict=False)
    if create_parents:
        home.mkdir(parents=True, exist_ok=True)
    candidate = home / relative_path
    current = home
    for part in relative_path.parts[:-1]:
        current /= part
        if current.is_symlink():
            raise ValueError("provider snapshot path must not traverse symlinks")
        if create_parents:
            current.mkdir(exist_ok=True)
    try:
        candidate.resolve(strict=False).relative_to(home)
    except ValueError as exc:
        raise ValueError("provider snapshot path escapes TradingCodex home") from exc
    if candidate.is_symlink():
        raise ValueError("provider snapshot must not be a symlink")
    return candidate


def _write_provider_snapshot(
    context: dict[str, Any],
    provider_id: str,
    bundle: WorkspaceProviderBundle,
) -> str:
    snapshot_relative = _provider_snapshot_relative_path(context, provider_id, bundle.bundle_sha256)
    snapshot_dir = _snapshot_home_path(snapshot_relative, create_parents=True)
    if snapshot_dir.exists():
        existing = _read_provider_snapshot(context, provider_id, bundle.source_sha256, bundle.bundle_sha256, snapshot_relative.as_posix())
        if existing.bundle_sha256 != bundle.bundle_sha256:
            raise ValueError("existing provider snapshot does not match its approved digest")
        return snapshot_relative.as_posix()
    stage = Path(tempfile.mkdtemp(prefix=".provider-snapshot-", dir=snapshot_dir.parent))
    try:
        for relative_member, data, _file_sha256 in bundle.files:
            member_path = stage / Path(relative_member)
            member_path.parent.mkdir(parents=True, exist_ok=True)
            _write_binary_atomic(member_path, data)
        manifest = {
            "version": 1,
            "workspace_id": str(context["workspace_id"]),
            "workspace_path_hash": str(context["path_hash"]),
            "provider_id": provider_id,
            "relative_path": bundle.relative_path,
            "source_sha256": bundle.source_sha256,
            "bundle_sha256": bundle.bundle_sha256,
            "files": [
                {"path": relative_member, "sha256": file_sha256, "size": len(data)}
                for relative_member, data, file_sha256 in bundle.files
            ],
        }
        atomic_write_text(stage / PROVIDER_SNAPSHOT_MANIFEST, json.dumps(manifest, indent=2, sort_keys=True) + "\n")
        installed = False
        try:
            os.replace(stage, snapshot_dir)
            installed = True
        except OSError:
            if not snapshot_dir.exists():
                raise
        if installed:
            _make_snapshot_read_only(snapshot_dir)
        verified = _read_provider_snapshot(
            context,
            provider_id,
            bundle.source_sha256,
            bundle.bundle_sha256,
            snapshot_relative.as_posix(),
        )
        if verified.bundle_sha256 != bundle.bundle_sha256:
            raise ValueError("provider snapshot verification failed")
    finally:
        if stage.exists():
            try:
                _make_snapshot_writable(stage)
                shutil.rmtree(stage)
            except OSError:
                pass
    return snapshot_relative.as_posix()


def _read_provider_snapshot(
    context: dict[str, Any],
    provider_id: str,
    source_sha256: str,
    bundle_sha256: str,
    snapshot_relative_path: str,
) -> WorkspaceProviderBundle:
    expected_relative = _provider_snapshot_relative_path(context, provider_id, bundle_sha256)
    if snapshot_relative_path != expected_relative.as_posix():
        raise PermissionError("approved provider snapshot path does not match its DB binding")
    snapshot_dir = _snapshot_home_path(expected_relative)
    manifest_path = snapshot_dir / PROVIDER_SNAPSHOT_MANIFEST
    if not snapshot_dir.is_dir() or snapshot_dir.is_symlink() or not manifest_path.is_file() or manifest_path.is_symlink():
        raise PermissionError("approved provider snapshot is missing or unsafe")
    try:
        manifest = json.loads(_read_regular_file_exact(manifest_path, max_bytes=256_000).decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise PermissionError("approved provider snapshot manifest is invalid") from exc
    expected_manifest = {
        "workspace_id": str(context["workspace_id"]),
        "workspace_path_hash": str(context["path_hash"]),
        "provider_id": provider_id,
        "source_sha256": source_sha256,
        "bundle_sha256": bundle_sha256,
    }
    if not isinstance(manifest, dict) or any(manifest.get(key) != value for key, value in expected_manifest.items()):
        raise PermissionError("approved provider snapshot manifest does not match its DB binding")
    provider_path = snapshot_dir / WORKSPACE_PROVIDER_FILENAME
    bundle = _collect_provider_bundle(
        snapshot_dir,
        provider_path,
        logical_relative_path=str(manifest.get("relative_path") or ""),
        snapshot=True,
    )
    if bundle.source_sha256 != source_sha256 or bundle.bundle_sha256 != bundle_sha256:
        raise PermissionError("approved provider snapshot digest verification failed")
    expected_files = [
        {"path": relative_member, "sha256": file_sha256, "size": len(data)}
        for relative_member, data, file_sha256 in bundle.files
    ]
    if manifest.get("files") != expected_files:
        raise PermissionError("approved provider snapshot file manifest verification failed")
    return bundle


def _write_binary_atomic(path: Path, data: bytes) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def _make_snapshot_read_only(root: Path) -> None:
    if os.name == "nt":
        return
    for path in sorted(root.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        path.chmod(0o555 if path.is_dir() else 0o444)
    root.chmod(0o555)


def _make_snapshot_writable(root: Path) -> None:
    if os.name == "nt":
        return
    root.chmod(0o755)
    for path in root.rglob("*"):
        path.chmod(0o755 if path.is_dir() else 0o644)


def _workspace_provider_key(context: dict[str, Any], provider_id: str) -> tuple[str, str, str]:
    return str(context["workspace_id"]), str(context["path_hash"]), provider_id


def _remove_workspace_provider_modules(module_prefix: str) -> None:
    for module_name in tuple(sys.modules):
        if module_name == module_prefix or module_name.startswith(f"{module_prefix}."):
            sys.modules.pop(module_name, None)


def _evict_workspace_provider_cache(cache_key: tuple[str, str, str]) -> None:
    source = _WORKSPACE_PROVIDER_SOURCES.pop(cache_key, {})
    _WORKSPACE_PROVIDER_CACHE.pop(cache_key, None)
    module_prefix = str(source.get("module_prefix") or "")
    if module_prefix:
        _remove_workspace_provider_modules(module_prefix)


def _approved_workspace_provider_source(
    context: dict[str, Any],
    provider_id: str,
    relative_path: str,
    source_sha256: str,
    bundle_sha256: str,
    *,
    approval_model: Any | None = None,
) -> Any | None:
    if approval_model is None:
        from apps.integrations.models import BrokerProviderSourceApproval

        approval_model = BrokerProviderSourceApproval

    return approval_model.objects.filter(
        workspace_id=str(context["workspace_id"]),
        workspace_path_hash=str(context["path_hash"]),
        provider_id=provider_id,
        relative_path=relative_path,
        source_sha256=source_sha256,
        bundle_sha256=bundle_sha256,
        status=approval_model.STATUS_APPROVED,
        revoked_at__isnull=True,
    ).first()


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
        "provider_source": broker_provider_source_status(provider.provider_id, workspace_root),
    }


def _provider_source_provenance_summary(provenance: dict[str, Any] | None) -> dict[str, Any]:
    if provenance is None:
        return {
            "status": "not_provided",
            "schema_version": None,
            "source_count": 0,
            "sources": [],
        }
    sources = [dict(source) for source in provenance["sources"]]
    return {
        "status": "validated",
        "schema_version": provenance["schema_version"],
        "source_count": len(sources),
        "sources": sources,
    }


def _bundle_only_provider_source_status(
    provider_id: str,
    bundle: WorkspaceProviderBundle,
    *,
    loaded_hash: str,
    expected_hash: str,
) -> dict[str, Any]:
    return {
        "kind": "workspace",
        "provider_id": provider_id,
        "path": bundle.relative_path,
        "source_hash": bundle.bundle_sha256,
        "bundle_sha256": bundle.bundle_sha256,
        "provider_py_sha256": bundle.source_sha256,
        "loaded_source_hash": loaded_hash,
        "registered_source_hash": expected_hash,
        "approval_status": "service_check_required",
        "approval_id": None,
        "approved_at": None,
        "snapshot_relative_path": "",
        "service_restart_required": True,
        "drift_status": "approval_status_unavailable",
        "source_provenance": _provider_source_provenance_summary(bundle.source_provenance),
        "inspection_scope": "bundle_only",
    }


def broker_provider_source_status(
    provider_id: str,
    workspace_root: Path | str | None = None,
    *,
    expected_hash: str = "",
    allow_ledger_unavailable: bool = False,
) -> dict[str, Any]:
    provider_id = _validate_provider_id(provider_id, required=False)
    if not provider_id:
        return {"kind": "unknown", "service_restart_required": False, "drift_status": "none"}
    if provider_id == PAPER_PROVIDER.provider_id:
        return {"kind": "builtin", "provider_id": provider_id, "service_restart_required": False, "drift_status": "none"}
    if provider_id in _BROKER_ADAPTER_PROVIDERS:
        return {"kind": "registered", "provider_id": provider_id, "service_restart_required": False, "drift_status": "none"}
    root = Path(workspace_root or os.environ.get("TRADINGCODEX_WORKSPACE_ROOT") or ".").expanduser().resolve()
    relative_path = _workspace_provider_relative_path(provider_id)
    raw_provider_path = root / relative_path
    if not raw_provider_path.exists():
        return {
            "kind": "unknown",
            "provider_id": provider_id,
            "path": "",
            "source_hash": "",
            "loaded_source_hash": "",
            "registered_source_hash": str(expected_hash or ""),
            "approval_status": "not_applicable",
            "service_restart_required": False,
            "drift_status": "none",
        }
    try:
        bundle = _read_workspace_provider_bundle(root, provider_id)
    except (OSError, ValueError):
        return {
            "kind": "workspace",
            "provider_id": provider_id,
            "path": relative_path.as_posix(),
            "source_hash": "",
            "loaded_source_hash": "",
            "registered_source_hash": str(expected_hash or ""),
            "approval_status": "blocked",
            "service_restart_required": True,
            "drift_status": "unsafe_or_invalid_source",
            "source_provenance": {
                "status": "unavailable",
                "schema_version": None,
                "source_count": 0,
                "sources": [],
            },
        }
    context = workspace_context_payload(root)
    cache_key = _workspace_provider_key(context, provider_id)
    loaded = _WORKSPACE_PROVIDER_SOURCES.get(cache_key, {})
    loaded_hash = str(loaded.get("bundle_hash") or "")
    current_hash = bundle.bundle_sha256
    expected = str(expected_hash or "")

    try:
        ensure_runtime_database(root)
        from apps.integrations.models import BrokerProviderSourceApproval

        approval = _approved_workspace_provider_source(
            context,
            provider_id,
            bundle.relative_path,
            bundle.source_sha256,
            bundle.bundle_sha256,
            approval_model=BrokerProviderSourceApproval,
        )
        has_other_approval = BrokerProviderSourceApproval.objects.filter(
            workspace_id=str(context["workspace_id"]),
            workspace_path_hash=str(context["path_hash"]),
            provider_id=provider_id,
            status=BrokerProviderSourceApproval.STATUS_APPROVED,
            revoked_at__isnull=True,
        ).exclude(
            source_sha256=bundle.source_sha256,
            bundle_sha256=bundle.bundle_sha256,
        ).exists()
    except (OSError, DatabaseError):
        if not allow_ledger_unavailable:
            raise
        return _bundle_only_provider_source_status(
            provider_id,
            bundle,
            loaded_hash=loaded_hash,
            expected_hash=expected,
        )
    approval_status = "approved" if approval is not None else "stale" if has_other_approval else "approval_required"
    restart_required = bool(approval is not None and approval.approved_at > _PROVIDER_RUNTIME_STARTED_AT)
    drift_status = "none"
    if approval_status == "stale":
        restart_required = True
        drift_status = "approval_stale"
    elif approval_status == "approval_required":
        restart_required = bool(expected or loaded_hash)
        drift_status = "operator_approval_required"
    elif restart_required:
        drift_status = "approved_restart_required"
    if expected and current_hash != expected:
        restart_required = True
        drift_status = "source_changed"
    elif loaded_hash and loaded_hash != current_hash:
        restart_required = True
        drift_status = "loaded_provider_stale"
    elif expected and loaded_hash and loaded_hash != expected:
        restart_required = True
        drift_status = "loaded_provider_mismatch"
    if approval is not None:
        try:
            snapshot_bundle = _read_provider_snapshot(
                context,
                provider_id,
                bundle.source_sha256,
                bundle.bundle_sha256,
                str(approval.snapshot_relative_path),
            )
            if snapshot_bundle.relative_path != bundle.relative_path:
                raise PermissionError("snapshot entry path mismatch")
        except (OSError, PermissionError, ValueError):
            approval_status = "blocked"
            restart_required = True
            drift_status = "approved_snapshot_invalid"
    return {
        "kind": "workspace",
        "provider_id": provider_id,
        "path": bundle.relative_path,
        "source_hash": current_hash,
        "bundle_sha256": bundle.bundle_sha256,
        "provider_py_sha256": bundle.source_sha256,
        "loaded_source_hash": loaded_hash,
        "registered_source_hash": expected,
        "approval_status": approval_status,
        "approval_id": approval.pk if approval is not None else None,
        "approved_at": approval.approved_at.isoformat() if approval is not None else None,
        "snapshot_relative_path": str(approval.snapshot_relative_path) if approval is not None else "",
        "service_restart_required": restart_required,
        "drift_status": drift_status,
        "source_provenance": _provider_source_provenance_summary(bundle.source_provenance),
    }


def broker_connection_provider_source_status(connection: Any, workspace_root: Path | str | None = None) -> dict[str, Any]:
    _, provider_id = _connection_identity(connection)
    metadata = connection.metadata if isinstance(connection.metadata, dict) else {}
    profile = metadata.get("capability_profile") if isinstance(metadata.get("capability_profile"), dict) else {}
    provider_source = profile.get("provider_source") if isinstance(profile.get("provider_source"), dict) else {}
    return broker_provider_source_status(provider_id, workspace_root, expected_hash=str(provider_source.get("source_hash") or ""))


def broker_connection_provider_review_reasons(connection: Any, workspace_root: Path | str | None = None) -> list[str]:
    source_status = broker_connection_provider_source_status(connection, workspace_root)
    if source_status.get("service_restart_required"):
        return ["broker provider source changed; restart TradingCodex service and revalidate connector"]
    if source_status.get("drift_status") not in {"", "none", None}:
        return ["broker provider source changed; revalidate connector before broker execution"]
    return []


def _record_workspace_provider_source(
    cache_key: tuple[str, str, str],
    *,
    relative_path: str,
    source_sha256: str,
    bundle_sha256: str,
    snapshot_relative_path: str,
    approval_id: str,
    module_prefix: str,
) -> None:
    _WORKSPACE_PROVIDER_SOURCES[cache_key] = {
        "relative_path": relative_path,
        "source_hash": source_sha256,
        "bundle_hash": bundle_sha256,
        "snapshot_relative_path": snapshot_relative_path,
        "approval_id": approval_id,
        "module_prefix": module_prefix,
    }


def _provider_source_for_registration(provider_id: str, workspace_root: Path | str | None) -> dict[str, Any]:
    status = broker_provider_source_status(provider_id, workspace_root)
    source_hash = str(status.get("loaded_source_hash") or status.get("source_hash") or "")
    return {**status, "source_hash": source_hash, "registered_source_hash": source_hash}


class BrokerAdapter:
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
        raise ValueError("adapter does not support cancel_order")

    def get_order_status(self, broker_order_id: str) -> dict[str, Any]:
        raise ValueError("adapter does not support get_order_status")


class PaperBrokerAdapter(BrokerAdapter):
    def __init__(self, workspace_root: Path | str | None = None) -> None:
        self.workspace_root = Path(workspace_root or ".").resolve()

    def health_check(self) -> BrokerHealth:
        return BrokerHealth("ok", "local paper broker adapter is available")

    def describe_capabilities(self) -> dict[str, Any]:
        return _paper_profile("paper-trading", "paper", "global", credential_ref="")

    def discover_accounts(self) -> list[BrokerAccountDTO]:
        profile = active_profile_for_workspace(self.workspace_root)
        return [
            BrokerAccountDTO(
                broker_account_id=profile["account_id"],
                account_label="Local Paper Account",
                account_type="paper",
                base_currency=profile["base_currency"],
                masked_identifier="paper",
                trading_enabled=True,
                metadata={
                    "default_cash_base": str(DEFAULT_PAPER_CASH),
                    "portfolio_id": profile["portfolio_id"],
                    "strategy_id": profile["strategy_id"],
                },
            )
        ]

    def get_cash(self, account_id: str) -> list[CashDTO]:
        portfolio_id, _, strategy_id = portfolio_keys({"account_id": account_id}, self.workspace_root)
        state = load_paper_portfolio_state(self.workspace_root, portfolio_id, account_id, strategy_id)
        return [
            CashDTO(currency=str(currency), amount=float(amount))
            for currency, amount in sorted((state.get("cash") or {}).items())
        ]

    def get_positions(self, account_id: str) -> list[PositionDTO]:
        portfolio_id, _, strategy_id = portfolio_keys({"account_id": account_id}, self.workspace_root)
        state = load_paper_portfolio_state(self.workspace_root, portfolio_id, account_id, strategy_id)
        positions = state.get("positions") if isinstance(state.get("positions"), dict) else {}
        return [
            PositionDTO(
                symbol=str(symbol).upper(),
                quantity=float(position.get("quantity", 0)),
                average_price=float(position.get("average_price", 0)),
                currency=normalize_currency_code(position.get("currency") or base_currency_for_workspace(self.workspace_root)),
                instrument_id=str(position.get("instrument_id") or symbol).upper(),
            )
            for symbol, position in sorted(positions.items())
            if float(position.get("quantity", 0)) != 0
        ]

    def validate_order(self, order: dict[str, Any]) -> OrderValidationResult:
        reasons: list[str] = []
        profile = active_profile_for_workspace(self.workspace_root)
        side = str(order.get("side") or "").lower()
        if side not in {"buy", "sell"}:
            reasons.append("side must be buy or sell")
        quantity = _float(order.get("quantity"))
        price = _float(order.get("limit_price"))
        if quantity is None or quantity <= 0:
            reasons.append("quantity must be positive")
        if price is None or price <= 0:
            reasons.append("limit_price must be positive")
        if side == "buy" and quantity and price:
            currency = normalize_currency_code(order.get("currency") or base_currency_for_workspace(self.workspace_root))
            cash = next(
                (
                    item.amount
                    for item in self.get_cash(str(order.get("account_id") or profile["account_id"]))
                    if item.currency == currency
                ),
                0,
            )
            if cash < quantity * price:
                reasons.append(f"insufficient paper cash: required {quantity * price}, available {cash}")
        if side == "sell" and quantity:
            symbol = str(order.get("symbol") or "").upper()
            available = next((item.quantity for item in self.get_positions(str(order.get("account_id") or profile["account_id"])) if item.symbol == symbol), 0)
            if available < quantity:
                reasons.append(f"insufficient paper position: required {quantity}, available {available}")
        return OrderValidationResult(not reasons, reasons, {"provider_id": PAPER_PROVIDER.provider_id})

    def submit_order(self, order: dict[str, Any]) -> dict[str, Any]:
        from tradingcodex_service.application.portfolio import submit_paper_order

        return submit_paper_order(self.workspace_root, order)


def _connection_identity(connection: Any) -> tuple[str, str]:
    transport = str(getattr(connection, "transport", "") or "").strip()
    provider_id = _validate_provider_id(getattr(connection, "provider_id", ""))
    if transport not in {"paper", "api"}:
        raise ValueError(f"unsupported broker transport: {transport or '(missing)'}")
    if transport == "paper" and provider_id != PAPER_PROVIDER.provider_id:
        raise ValueError(f"paper transport requires provider_id={PAPER_PROVIDER.provider_id}")
    if transport == "api" and provider_id == PAPER_PROVIDER.provider_id:
        raise ValueError(f"provider_id={provider_id} is not valid for api transport")
    metadata = connection.metadata if isinstance(connection.metadata, dict) else {}
    profile = metadata.get("capability_profile") if isinstance(metadata.get("capability_profile"), dict) else {}
    profile_provider_id = _validate_provider_id(profile.get("provider_id"), required=False)
    if profile_provider_id and profile_provider_id != provider_id:
        raise ValueError(
            f"broker connection provider_id={provider_id} does not match capability profile provider_id={profile_provider_id}"
        )
    return transport, provider_id


def adapter_for_connection(connection: Any, workspace_root: Path | str | None = None) -> BrokerAdapter:
    transport, provider_id = _connection_identity(connection)
    if transport == "paper":
        return PaperBrokerAdapter(workspace_root)
    provider = get_broker_adapter_provider(provider_id, workspace_root)
    if provider is None:
        raise ValueError(f"unknown broker provider: {provider_id}")
    if provider.factory is None:
        if provider.execution_posture == "live_broker":
            raise ValueError(f"live broker provider {provider_id} requires an adapter factory")
        return NativeApiBrokerAdapter(connection)
    adapter = provider.factory(connection, workspace_root)
    if not isinstance(adapter, BrokerAdapter):
        raise ValueError(f"broker provider {provider_id} returned an invalid adapter")
    return adapter


_MAX_CONNECTOR_SCAFFOLD_PREIMAGE_BYTES = 1_000_000


def _connector_scaffold_preimage(path: Path) -> dict[str, Any]:
    """Read one prospective scaffold target without following a final symlink."""

    if path.is_symlink():
        raise ValueError("workspace connector scaffold files must not be symlinks")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except FileNotFoundError:
        return {
            "preimage_exists": False,
            "preimage_sha256": None,
            "preimage_size": None,
        }
    except OSError as exc:
        raise ValueError(f"workspace connector scaffold target is unavailable: {path}") from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError("workspace connector scaffold targets must be regular files")
        if metadata.st_size > _MAX_CONNECTOR_SCAFFOLD_PREIMAGE_BYTES:
            raise ValueError("workspace connector scaffold preimage is too large")
        with os.fdopen(descriptor, "rb") as handle:
            descriptor = -1
            content_bytes = handle.read(_MAX_CONNECTOR_SCAFFOLD_PREIMAGE_BYTES + 1)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if len(content_bytes) > _MAX_CONNECTOR_SCAFFOLD_PREIMAGE_BYTES:
        raise ValueError("workspace connector scaffold preimage is too large")
    try:
        content_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("workspace connector scaffold preimage must be UTF-8 text") from exc
    return {
        "preimage_exists": True,
        "preimage_sha256": hashlib.sha256(content_bytes).hexdigest(),
        "preimage_size": len(content_bytes),
    }


def _rendered_scaffold_file(root: Path, path: Path, content: str) -> dict[str, Any]:
    relative_path = path.relative_to(root).as_posix()
    return {
        "path": relative_path,
        "content": content,
        "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        **_connector_scaffold_preimage(path),
    }


def render_broker_connector_scaffold(
    workspace_root: Path | str | None,
    args: dict[str, Any],
) -> dict[str, Any]:
    """Render an explicitly requested connector scaffold without writing files."""

    root = Path(workspace_root or os.environ.get("TRADINGCODEX_WORKSPACE_ROOT") or ".").expanduser().resolve()
    provider_id = str(args.get("provider_id") or "").strip()
    if not provider_id:
        raise ValueError("provider_id is required")
    broker_id = _connector_safe_id(str(args.get("broker_id") or "").strip())
    if not broker_id:
        raise ValueError("broker_id is required")
    provider = get_broker_adapter_provider(provider_id, root) if provider_id else None
    credential_ref = str(args.get("credential_ref") or f"env:{broker_id.upper().replace('-', '_')}").strip()
    _validate_credential_ref(credential_ref)
    environment = str(args.get("environment") or (provider.default_environment if provider else "live"))
    region = str(args.get("region") or (provider.region if provider else "custom"))
    display_name = str(args.get("display_name") or (provider.display_name if provider else broker_id))
    if provider is None:
        profile = {
            "provider_id": provider_id,
            "broker_id": broker_id,
            "display_name": display_name,
            "environment": environment,
            "region": region,
            "credential_ref": credential_ref,
            "execution_posture": "provider_development_required",
            "blocked_surfaces": list(BLOCKED_BROKER_SURFACES),
            "blockers": ["provider_not_installed"],
        }
    else:
        profile = provider.as_profile(broker_id=broker_id, environment=environment, region=region, credential_ref=credential_ref)
        profile["display_name"] = display_name
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
    base = _safe_connector_directory(root, broker_id)
    profile_path = base / "connector-profile.json"
    secret_schema_path = base / "secret-schema.json"
    readme_path = base / "README.md"
    if any(path.is_symlink() for path in (profile_path, secret_schema_path, readme_path)):
        raise ValueError("workspace connector scaffold files must not be symlinks")
    secret_schema = {
        "broker_id": broker_id,
        "provider_id": provider_id,
        "credential_ref": credential_ref,
        "required_secret_refs": _required_secret_refs(credential_ref, profile),
        "do_not_store_raw_values": True,
    }
    readme = "\n".join(
        [
            f"# {profile.get('display_name') or broker_id} Connector",
            "",
            "Rendered by TradingCodex connector scaffolding.",
            "",
            "- Store only the credential reference in TradingCodex.",
            "- Do not paste raw API keys, tokens, or secrets into Codex chat or workspace files.",
            "- Live order submission requires installed provider code plus explicit policy, environment, approval, confirmation, and audit gates.",
            "- If provider_development_required is true, implement and register the provider before connector registration.",
            "",
        ]
    )
    files = {
        "profile": _rendered_scaffold_file(
            root,
            profile_path,
            json.dumps(profile, indent=2, ensure_ascii=False) + "\n",
        ),
        "secret_schema": _rendered_scaffold_file(
            root,
            secret_schema_path,
            json.dumps(secret_schema, indent=2, ensure_ascii=False) + "\n",
        ),
        "readme": _rendered_scaffold_file(root, readme_path, readme),
    }
    render_sha256 = stable_hash(
        {
            "schema_version": 1,
            "files": [
                {
                    "path": file["path"],
                    "content_sha256": file["content_sha256"],
                    "preimage_exists": file["preimage_exists"],
                    "preimage_sha256": file["preimage_sha256"],
                    "preimage_size": file["preimage_size"],
                }
                for file in files.values()
            ],
        }
    )
    next_steps = [
        "Verify each preimage and apply the returned target contents with apply_patch.",
        f"Call register_broker_connector for broker_id={broker_id} after the files match this render.",
        f"Call validate_broker_connector_build for broker_id={broker_id} after registration.",
    ]
    if provider is None:
        next_steps = [
            f"Implement or install provider '{provider_id}' with tcx-build.",
            "Re-render the scaffold after the provider source is available.",
        ]
    return {
        "status": "rendered",
        "broker_id": broker_id,
        "provider_id": provider_id,
        "display_name": profile["display_name"],
        "environment": environment,
        "credential_ref": credential_ref,
        "allowed_capabilities": profile["build_lane"]["allowed_capabilities"],
        "live_order_enabled": False,
        "live_capable_provider": bool(provider and provider.live),
        "provider_development_required": provider is None,
        "files": files,
        "render_sha256": render_sha256,
        "writes_performed": False,
        "next": next_steps,
        "db_canonical": False,
        "workspace_context": workspace_context_payload(root),
    }


def _render_preimage_still_matches(root: Path, rendered_file: dict[str, Any]) -> bool:
    path = safe_workspace_path(
        root,
        str(rendered_file["path"]),
        allowed_roots=(WORKSPACE_PROVIDER_ROOT,),
    )
    current = _connector_scaffold_preimage(path)
    return (
        current["preimage_exists"] == rendered_file["preimage_exists"]
        and current["preimage_sha256"] == rendered_file["preimage_sha256"]
        and current["preimage_size"] == rendered_file["preimage_size"]
    )


def scaffold_broker_connector(workspace_root: Path | str | None, args: dict[str, Any]) -> dict[str, Any]:
    """Operator service that persists the same deterministic scaffold returned by render."""

    root = Path(workspace_root or os.environ.get("TRADINGCODEX_WORKSPACE_ROOT") or ".").expanduser().resolve()
    rendered = render_broker_connector_scaffold(root, args)
    base = _safe_connector_directory(root, str(rendered["broker_id"]), create=True)
    for rendered_file in rendered["files"].values():
        if not _render_preimage_still_matches(root, rendered_file):
            raise ValueError("workspace connector scaffold preimage changed before write")
        target = safe_workspace_path(
            root,
            str(rendered_file["path"]),
            allowed_roots=(WORKSPACE_PROVIDER_ROOT,),
        )
        if target.parent != base:
            raise ValueError("workspace connector scaffold target is outside its connector directory")
        atomic_write_text(target, str(rendered_file["content"]))
    launcher = workspace_launcher_command()
    next_steps = [
        f"{launcher} connectors register --provider-id {rendered['provider_id']} --broker-id {rendered['broker_id']} --credential-ref {rendered['credential_ref']} --environment {rendered['environment']}",
        f"{launcher} connectors validate {rendered['broker_id']}",
    ]
    if rendered["provider_development_required"]:
        next_steps = [
            f"Implement or install provider '{rendered['provider_id']}' with tcx-build.",
            f"{launcher} connectors providers",
        ]
    result = {
        **rendered,
        "status": "scaffolded",
        "files": {name: value["path"] for name, value in rendered["files"].items()},
        "render_sha256": rendered["render_sha256"],
        "writes_performed": True,
        "next": next_steps,
        "db_canonical": True,
    }
    _audit("broker_connector.scaffolded", result, str(args.get("principal_id") or "head-manager"), root)
    return result


def validate_broker_connector_build(workspace_root: Path | str | None, args: dict[str, Any]) -> dict[str, Any]:
    root = Path(workspace_root or os.environ.get("TRADINGCODEX_WORKSPACE_ROOT") or ".").expanduser().resolve()
    broker_id = _connector_safe_id(str(args.get("broker_id") or "").strip())
    if not broker_id:
        raise ValueError("broker_id is required")
    profile_path = root / "trading" / "connectors" / broker_id / "connector-profile.json"
    scaffold_profile = _read_json_file(profile_path) if profile_path.exists() else {}
    try:
        connection_payload = validate_broker_connection(
            root,
            {"broker_id": broker_id, "promote_execution": args.get("promote_execution", True)},
        )
    except Exception as exc:
        connection_payload = {"status": "not_registered", "error": str(exc)}
    connection = connection_payload.get("connection") if isinstance(connection_payload.get("connection"), dict) else {}
    profile = connection.get("metadata", {}).get("capability_profile") if isinstance(connection.get("metadata"), dict) else None
    profile = profile if isinstance(profile, dict) else scaffold_profile
    blockers = list(profile.get("blockers") or []) if isinstance(profile, dict) else []
    source_status = {}
    if isinstance(profile, dict):
        provider_id = _validate_provider_id(profile.get("provider_id"))
        provider_source = profile.get("provider_source") if isinstance(profile.get("provider_source"), dict) else {}
        source_status = broker_provider_source_status(provider_id, root, expected_hash=str(provider_source.get("source_hash") or ""))
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
            profile = _read_json_file(profile_path)
            try:
                provider_id = _validate_provider_id(profile.get("provider_id"))
            except ValueError as exc:
                raise ValueError(f"invalid connector profile {profile_path}: {exc}") from exc
            scaffolds.append(
                {
                    "broker_id": profile_path.parent.name,
                    "provider_id": provider_id,
                    "display_name": profile.get("display_name") or profile_path.parent.name,
                    "environment": profile.get("environment") or "",
                    "allowed_capabilities": _connector_build_capabilities(profile),
                    "live_order_enabled": False,
                    "live_capable_provider": bool(profile.get("live")),
                    "provider_development_required": "provider_not_installed" in list(profile.get("blockers") or []),
                    "service_restart_required": bool(
                        broker_provider_source_status(
                            provider_id,
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

    provider_id = str(args.get("provider_id") or "").strip()
    if not provider_id:
        raise ValueError("provider_id is required")
    provider = get_broker_adapter_provider(provider_id, root)
    if provider is None:
        raise ValueError(f"unknown broker provider: {provider_id or '(missing)'}; build or install a provider first")
    broker_id = str(args.get("broker_id") or "").strip()
    if not broker_id:
        raise ValueError("broker_id is required")
    credential_ref = str(args.get("credential_ref") or "")
    _validate_credential_ref(credential_ref)
    environment = str(args.get("environment") or provider.default_environment)
    region = str(args.get("region") or provider.region)
    display_name = str(args.get("display_name") or provider.display_name)
    profile = provider.as_profile(broker_id=broker_id, environment=environment, region=region, credential_ref=credential_ref)
    profile["display_name"] = display_name
    provider_source = _provider_source_for_registration(provider.provider_id, root)
    profile["provider_source"] = provider_source
    blockers = list(profile.get("blockers") or [])
    if provider_source.get("service_restart_required"):
        blockers.append("provider_source_changed_restart_required")
        profile["blockers"] = blockers
    read_blockers = [blocker for blocker in blockers if not str(blocker).startswith("execution_")]
    status = "read_only" if not read_blockers else "disabled"
    if profile.get("execution_posture") in BROKER_VALIDATION_EXECUTION_POSTURES and status == "read_only":
        profile["enabled_mcp_tools"] = ["preview_order_translation", "run_order_checks"]
        profile["enabled_native_actions"] = ["execution.submit_approved_order"]
    if profile.get("execution_posture") in BROKER_LIVE_EXECUTION_POSTURES and status == "read_only":
        profile["enabled_mcp_tools"] = ["preview_order_translation", "run_order_checks"]
        profile["enabled_native_actions"] = [
            "execution.submit_approved_order",
            "execution.cancel_submitted_order",
        ]
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
            "provider_id": provider.provider_id,
            "display_name": display_name,
            "transport": "paper" if provider.provider_id == PAPER_PROVIDER.provider_id else "api",
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
    provider_id = str(args.get("provider_id") or "").strip()
    if not provider_id:
        raise ValueError("provider_id is required")
    broker_id = _connector_safe_id(str(args.get("broker_id") or "").strip())
    mode = str(args.get("mode") or "read-only").strip().lower().replace("_", "-")
    if mode not in {"read-only", "validation", "live-request"}:
        raise ValueError("mode must be read-only, validation, or live-request")
    if not broker_id:
        raise ValueError("broker_id is required")
    credential_ref = str(args.get("credential_ref") or f"env:{broker_id.upper().replace('-', '_')}").strip()
    _validate_credential_ref(credential_ref)

    provider = get_broker_adapter_provider(provider_id, root) if provider_id else None
    if provider is None:
        result = {
            "status": "provider_missing",
            "lifecycle_state": "provider_missing",
            "broker_id": broker_id,
            "provider_id": provider_id,
            "mode": mode,
            "next": [
                f"Fetch official public source material and implement provider '{provider_id}' in a $tcx-build turn.",
                f"Inspect the inert provider bundle with {workspace_launcher_command()} connectors inspect-provider {provider_id}.",
                "Stop for interactive operator hash approval and a TradingCodex service restart before rendering or registering the connector.",
            ],
            "live_order_enabled": False,
            "connector_files_created": False,
            "db_canonical": True,
            "workspace_context": workspace_context_payload(root),
        }
        _audit("broker_connector.connect_provider_missing", result, str(args.get("principal_id") or "head-manager"), root)
        return result

    scaffold_broker_connector(
        root,
        {
            **args,
            "provider_id": provider_id,
            "broker_id": broker_id,
            "credential_ref": credential_ref,
            "principal_id": args.get("principal_id") or "head-manager",
        },
    )

    registered = register_broker_connector(
        root,
        {
            **args,
            "provider_id": provider.provider_id,
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
    connection = _get_connection(workspace_root, str(args.get("broker_id") or ""))
    profile = adapter_for_connection(connection, workspace_root).describe_capabilities()
    return {
        "broker_id": connection.broker_id,
        "capability_profile": profile,
        "blockers": _profile_blockers(profile),
        "db_canonical": True,
        "workspace_context": workspace_context_payload(workspace_root),
    }


def get_broker_instrument_constraints(workspace_root: Path | str | None, args: dict[str, Any]) -> dict[str, Any]:
    connection = _get_connection(workspace_root, str(args.get("broker_id") or ""))
    symbol = str(args.get("symbol") or "").strip().upper()
    if not symbol:
        raise ValueError("symbol is required")
    constraints = adapter_for_connection(connection, workspace_root).get_instrument_constraints(symbol, args)
    return {
        "broker_id": connection.broker_id,
        "constraints": asdict(constraints),
        "db_canonical": True,
        "workspace_context": workspace_context_payload(workspace_root),
    }


def preview_order_translation(workspace_root: Path | str | None, args: dict[str, Any]) -> dict[str, Any]:
    connection = _get_connection(workspace_root, str(args.get("broker_id") or ""))
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

    active_profile = active_profile_for_workspace(workspace_root)
    profile = _paper_profile("paper-trading", "paper", "global", credential_ref="")

    connection, created = BrokerConnection.objects.update_or_create(
        broker_id="paper-trading",
        defaults={
            "provider_id": PAPER_PROVIDER.provider_id,
            "display_name": "Paper",
            "transport": "paper",
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
        broker_account_id=active_profile["account_id"],
        defaults={
            "account_label": "Local Paper Account",
            "account_type": "paper",
            "base_currency": active_profile["base_currency"],
            "masked_identifier": "paper",
            "trading_enabled": True,
            "last_seen_at": django_timezone.now(),
            "metadata": {
                "portfolio_id": active_profile["portfolio_id"],
                "strategy_id": active_profile["strategy_id"],
            },
        },
    )
    if created and actor not in {"service", "read", "system-read"}:
        _audit("broker_connection.created", {"broker_id": connection.broker_id, "status": connection.status}, actor, workspace_root)
    return connection


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
    connection = _get_connection(workspace_root, str(args.get("broker_id") or ""))
    health = _probe_broker_connection(connection, workspace_root)
    return {
        "connection": _serialize_connection(connection, workspace_root),
        "health": asdict(health),
        "read_only": True,
        "db_canonical": True,
        "workspace_context": workspace_context_payload(workspace_root),
    }


def validate_broker_connection(workspace_root: Path | str | None, args: dict[str, Any]) -> dict[str, Any]:
    connection = _get_connection(workspace_root, str(args.get("broker_id") or ""))
    health = _probe_broker_connection(connection, workspace_root)
    _reconcile_validation_execution_status(
        connection,
        health,
        workspace_root,
        enable_trade_scopes=bool(args.get("promote_execution", True)),
    )
    connection.refresh_from_db()
    return {
        "connection": _serialize_connection(connection, workspace_root),
        "health": asdict(health),
        "read_only": False,
        "db_canonical": True,
        "workspace_context": workspace_context_payload(workspace_root),
    }


def _probe_broker_connection(connection: Any, workspace_root: Path | str | None) -> BrokerHealth:
    source_status = broker_connection_provider_source_status(connection, workspace_root)
    if source_status.get("service_restart_required"):
        return canonical_broker_health(BrokerHealth("blocked"))
    return broker_adapter_health(adapter_for_connection(connection, workspace_root))


def sync_broker_account(workspace_root: Path | str | None, args: dict[str, Any] | None = None) -> dict[str, Any]:
    args = dict(args or {})
    connection = _get_connection(workspace_root, str(args.get("broker_id") or ""))
    if connection.status not in {"read_only", "trading_locked", "trading_enabled"}:
        raise ValueError(f"broker connection is not enabled for read sync: {connection.broker_id}")
    adapter = adapter_for_connection(connection, workspace_root)
    ensure_runtime_database(workspace_root)
    from apps.integrations.models import BrokerAccount
    from apps.portfolio.models import BrokerSyncRun

    started_at = django_timezone.now()
    sync_run = BrokerSyncRun.objects.create(broker_connection=connection, status="started", started_at=started_at)
    requested_account = str(args.get("broker_account_id") or "")
    synced_accounts: list[dict[str, Any]] = []
    warnings: list[str] = []
    cash_count = 0
    positions_count = 0
    try:
        accounts = adapter.discover_accounts()
        if not isinstance(accounts, list):
            raise ValueError("broker account discovery must return a list")
        for raw_account_dto in accounts:
            account_dto = canonical_broker_account(raw_account_dto, connection.broker_id)
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
                    "metadata": {},
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
        _reconcile_validation_execution_status(connection, broker_adapter_health(adapter), workspace_root)
        connection.last_sync_at = sync_run.finished_at
        connection.save(update_fields=["last_sync_at", "updated_at"])
    except Exception as exc:
        safe_error = safe_provider_error("broker_sync_failed", exc)
        sync_run.status = "error"
        sync_run.error = json.dumps(safe_error, sort_keys=True, separators=(",", ":"))
        sync_run.finished_at = django_timezone.now()
        sync_run.save(update_fields=["status", "error", "finished_at"])
        _reconcile_validation_execution_status(connection, BrokerHealth("error"), workspace_root)
        _audit(
            "broker_sync.failed",
            {"broker_id": connection.broker_id, **safe_error},
            str(args.get("principal_id") or "service"),
            workspace_root,
        )
        raise RuntimeError("broker account sync failed") from None
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

    portfolio_id, account_id, strategy_id = portfolio_keys({}, workspace_root)
    now = django_timezone.now()
    position_payload = {
        item.symbol: {
            "quantity": item.quantity,
            "average_price": item.average_price,
            "currency": _required_currency_code(item.currency),
            "instrument_id": item.instrument_id or item.symbol,
        }
        for item in positions
        if item.quantity != 0
    }
    cash_payload = {_required_currency_code(item.currency): item.amount for item in cash}
    base_currency = _required_currency_code(broker_account.base_currency, "base_currency")
    payload = {
        "base_currency": base_currency,
        "cash_base": cash_payload.get(base_currency, 0),
        "cash": cash_payload,
        "positions": position_payload,
        "updated_at": now.isoformat(),
        "portfolio_id": portfolio_id,
        "account_id": account_id,
        "strategy_id": strategy_id,
        "source": connection.broker_id,
        "broker_id": connection.broker_id,
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
        currency = _required_currency_code(item.currency)
        CashBalance.objects.create(
            snapshot=snapshot,
            currency=currency,
            amount=item.amount,
            portfolio_id=portfolio_id,
            account_id=account_id,
            strategy_id=strategy_id,
        )
        raw = {"currency": currency, "amount": item.amount, "sync_run_id": sync_run_id}
        PortfolioLedgerEvent.objects.create(
            event_type="cash",
            broker_connection=connection,
            broker_account=broker_account,
            portfolio_id=portfolio_id,
            account_id=account_id,
            strategy_id=strategy_id,
            amount=item.amount,
            currency=currency,
            event_at=now,
            source_payload_hash=stable_hash(raw),
            raw_payload_ref=f"broker_sync_run:{sync_run_id}" if sync_run_id else "",
            metadata=raw,
        )
    for item in positions:
        if item.quantity == 0:
            continue
        currency = _required_currency_code(item.currency)
        Position.objects.create(
            snapshot=snapshot,
            symbol=item.symbol,
            quantity=item.quantity,
            average_price=item.average_price,
            currency=currency,
            portfolio_id=portfolio_id,
            account_id=account_id,
            strategy_id=strategy_id,
        )
        raw = {"symbol": item.symbol, "quantity": item.quantity, "average_price": item.average_price, "currency": currency, "sync_run_id": sync_run_id}
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
            currency=currency,
            event_at=now,
            source_payload_hash=stable_hash(raw),
            raw_payload_ref=f"broker_sync_run:{sync_run_id}" if sync_run_id else "",
            metadata=raw,
        )
    return snapshot


def create_reconciliation_summary(connection: Any, broker_account: Any, snapshot: Any, cash: list[CashDTO], positions: list[PositionDTO]) -> Any:
    from apps.portfolio.models import ReconciliationRun

    diffs: list[dict[str, Any]] = []
    if not cash and not positions:
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
    broker_id = args.get("broker_id")
    if broker_id:
        queryset = queryset.filter(broker_connection__broker_id=broker_id)
    return {
        "reconciliation_runs": [_serialize_reconciliation(run) for run in queryset[:limit]],
        "db_canonical": True,
        "workspace_context": workspace_context_payload(workspace_root),
    }


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
    symbol = str(order.get("venue_symbol") or order.get("symbol") or "")
    side = str(order.get("side") or "").lower()
    if family == "crypto_exchange":
        return {"symbol": symbol.replace("-", ""), "side": side.upper(), "type": str(order.get("order_type") or "limit").upper(), "newClientOrderId": order.get("client_order_id", "")}
    return {"symbol": symbol, "side": side, "type": order.get("order_type", "limit"), "client_order_id": order.get("client_order_id", "")}


def canonical_order_from_order(order: dict[str, Any], profile: dict[str, Any] | None = None) -> dict[str, Any]:
    profile = profile or {}
    symbol = str(order.get("symbol") or "").upper()
    venue_symbol = str(order.get("venue_symbol") or symbol).upper()
    quantity_mode = str(order.get("quantity_mode") or ("quote_notional" if order.get("quote_notional") else "quantity"))
    return {
        "version": 1,
        "asset_class": str(order.get("asset_class") or (profile.get("asset_classes") or ["equity"])[0]),
        "product_type": str(order.get("product_type") or (profile.get("products") or ["spot"])[0]),
        "instrument": {
            "symbol": symbol,
            "venue_symbol": venue_symbol,
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
        "client_order_id": str(order.get("client_order_id") or order.get("ticket_id") or ""),
        "approval_constraints": order.get("approval_constraints") if isinstance(order.get("approval_constraints"), dict) else {},
        "broker_translation": _broker_payload_preview(profile, order) if profile else {},
    }


def _preview_order_payload(workspace_root: Path | str | None, args: dict[str, Any], connection: Any) -> dict[str, Any]:
    if isinstance(args.get("order"), dict):
        order = dict(args["order"])
    elif args.get("ticket_id"):
        from tradingcodex_service.application.orders import resolve_order_ticket_payload

        order = resolve_order_ticket_payload(Path(workspace_root or "."), args)
    else:
        order = dict(args)
    order.setdefault("broker_id", connection.broker_id)
    order.setdefault("symbol", args.get("symbol") or "")
    if not str(order.get("symbol") or "").strip():
        raise ValueError("symbol is required")
    order.setdefault("order_type", args.get("order_type") or "limit")
    order.setdefault("time_in_force", args.get("time_in_force") or "day")
    if "quantity_mode" not in order and args.get("quote_notional"):
        order["quantity_mode"] = "quote_notional"
    return order


def _get_connection(workspace_root: Path | str | None, broker_id: str) -> Any:
    broker_id = str(broker_id or "").strip()
    if not broker_id:
        raise ValueError("broker_id is required")
    ensure_runtime_database(workspace_root)
    from apps.integrations.models import BrokerConnection

    if broker_id == "paper-trading":
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
    health = canonical_broker_health(health)
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

                adapter_enabled = AdapterDefinition.objects.filter(
                    adapter_id=connection.provider_id,
                    enabled=True,
                    live=True,
                ).exists()
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


def _serialize_connection(connection: Any, workspace_root: Path | str | None = None) -> dict[str, Any]:
    transport, provider_id = _connection_identity(connection)
    metadata = connection.metadata if isinstance(connection.metadata, dict) else {}
    profile = metadata.get("capability_profile") if isinstance(metadata.get("capability_profile"), dict) else {}
    provider_source = broker_connection_provider_source_status(connection, workspace_root)
    provider_drifted = provider_source.get("drift_status") not in {"", "none", None}
    service_restart_required = bool(metadata.get("service_restart_required") or provider_source.get("service_restart_required"))
    locked_for_provider_source = bool(service_restart_required or provider_drifted)
    return {
        "broker_id": connection.broker_id,
        "provider_id": provider_id,
        "display_name": connection.display_name,
        "transport": transport,
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


def _float(value: Any) -> float | None:
    try:
        return float(value)
    except Exception:
        return None


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


def _read_json_file(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid connector JSON: {path}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"connector JSON must be an object: {path}")
    return value


def _audit(action: str, payload: dict[str, Any], actor: str, workspace_root: Path | str | None) -> None:
    write_audit_event_if_available(workspace_root, actor, "service", {"type": action, "payload": payload})

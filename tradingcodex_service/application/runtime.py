from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import sys
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePath, PurePosixPath, PureWindowsPath
from typing import Any

from tradingcodex_service.application.common import (
    atomic_write_text,
    canonical_path_identity,
    exclusive_file_lock,
    now_iso,
    sanitize_id,
)
from tradingcodex_service.application.workspace_git import workspace_git_status

_RUNTIME_DB_READY = False
_RUNTIME_DB_NAME = ""
WORKSPACE_MANIFEST_REL = ".tradingcodex/workspace.json"
WORKSPACE_PROFILES_REL = ".tradingcodex/profiles.json"
WORKSPACE_FORMAT = "tradingcodex.workspace"
WORKSPACE_SCHEMA_VERSION = 1
WORKSPACE_PROFILES_FORMAT = "tradingcodex.profiles"
WORKSPACE_PROFILES_SCHEMA_VERSION = 1
WORKSPACE_MANIFEST_FIELDS = frozenset({
    "format",
    "schema_version",
    "workspace_id",
    "project_name",
    "created_at",
    "updated_at",
    "active_profile",
    "mcp_scope",
    "execution_mode",
})
ACTIVE_PROFILE_FIELDS = frozenset({"profile_id", "portfolio_id", "account_id", "strategy_id", "base_currency", "label"})
DEFAULT_STRATEGY_ID = "default-strategy"
DEFAULT_BASE_CURRENCY = "USD"
DEFAULT_EXECUTION_MODE = "non-live: paper/validation-only/broker-validation"
PROJECT_MIGRATION_APPS = frozenset({"audit", "harness", "integrations", "mcp", "orders", "policy", "portfolio", "workflows"})


class RuntimeMigrationError(RuntimeError):
    """Raised when canonical runtime schema preparation cannot complete safely."""


class RuntimeHomeResolutionError(RuntimeError):
    """Raised when the global TradingCodex home cannot be selected safely."""


@dataclass(frozen=True)
class HomeResolution:
    home: Path | PurePath
    home_source: str
    platform_default: Path | PurePath
    diagnostic: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "home": str(self.home),
            "home_source": self.home_source,
            "platform_default_home": str(self.platform_default),
            "diagnostic": self.diagnostic,
        }


def isolated_profile_for_workspace(workspace_id: str) -> dict[str, Any]:
    suffix = sanitize_id(workspace_id)[-12:]
    profile_id = f"paper-{suffix}"
    return {
        "profile_id": profile_id,
        "portfolio_id": profile_id,
        "account_id": f"local-{suffix}",
        "strategy_id": DEFAULT_STRATEGY_ID,
        "base_currency": DEFAULT_BASE_CURRENCY,
        "label": "isolated workspace paper account",
    }


def resolve_tradingcodex_home(
    *,
    environ: dict[str, str] | os._Environ[str] | None = None,
    platform_name: str | None = None,
    user_home: str | Path | PurePath | None = None,
) -> HomeResolution:
    """Resolve the platform-native v1 home or an explicit override."""

    env = os.environ if environ is None else environ
    platform_value = platform_name or sys.platform
    family = _platform_family(platform_value)
    home_dir = _user_home_path(env, family, platform_value, user_home)
    explicit = str(env.get("TRADINGCODEX_HOME") or "").strip()
    explicit_home = _expand_home_candidate(explicit, home_dir, family, platform_value) if explicit else None
    try:
        platform_default = _platform_default_home(env, family, platform_value, home_dir)
    except RuntimeHomeResolutionError:
        if explicit_home is None or family != "windows":
            raise
        platform_default = PureWindowsPath("%LOCALAPPDATA%") / "TradingCodex"
        return _validated_home_resolution(
            HomeResolution(
                home=explicit_home,
                home_source="environment_override",
                platform_default=platform_default,
                diagnostic="TRADINGCODEX_HOME explicitly selects the global home; LOCALAPPDATA is unavailable for default-path diagnostics.",
            ),
            env,
            family,
            platform_value,
            home_dir,
        )
    source_hint = str(env.get("TRADINGCODEX_HOME_SOURCE") or "").strip()
    if source_hint not in {"", "platform_default", "environment_override"}:
        raise RuntimeHomeResolutionError(f"unsupported TRADINGCODEX_HOME_SOURCE: {source_hint}")
    if source_hint and explicit_home is None:
        raise RuntimeHomeResolutionError("TRADINGCODEX_HOME_SOURCE requires TRADINGCODEX_HOME")
    if source_hint == "platform_default" and explicit_home is not None and not _paths_match(explicit_home, platform_default, family):
        raise RuntimeHomeResolutionError("projected platform-default TradingCodex home does not match this platform")
    if explicit_home is not None:
        source = source_hint or "environment_override"
        return _validated_home_resolution(
            HomeResolution(
                home=explicit_home,
                home_source=source,
                platform_default=platform_default,
                diagnostic=(
                    "Using the platform-default TradingCodex home."
                    if source == "platform_default"
                    else "TRADINGCODEX_HOME explicitly selects the global home."
                ),
            ),
            env,
            family,
            platform_value,
            home_dir,
        )
    return _validated_home_resolution(
        HomeResolution(
            home=platform_default,
            home_source="platform_default",
            platform_default=platform_default,
            diagnostic="Using the platform-default TradingCodex home.",
        ),
        env,
        family,
        platform_value,
        home_dir,
    )


def assert_runtime_home_outside_workspace(
    workspace_root: Path | str,
    runtime_home: Path | str,
) -> None:
    root = Path(workspace_root).expanduser().resolve(strict=False)
    home = Path(runtime_home).expanduser().resolve(strict=False)
    try:
        home.relative_to(root)
    except ValueError:
        return
    raise RuntimeHomeResolutionError(
        "TRADINGCODEX_HOME must be outside the generated workspace; use a sibling or platform runtime home"
    )


def _validated_home_resolution(
    resolution: HomeResolution,
    env: dict[str, str] | os._Environ[str],
    family: str,
    platform_name: str,
    home_dir: Path | PurePath,
) -> HomeResolution:
    raw_workspace = str(env.get("TRADINGCODEX_WORKSPACE_ROOT") or "").strip()
    if not raw_workspace:
        return resolution
    workspace = _expand_home_candidate(raw_workspace, home_dir, family, platform_name)
    home_path: PurePath
    workspace_path: PurePath
    if family == "windows":
        home_path = PureWindowsPath(resolution.home)
        workspace_path = PureWindowsPath(workspace)
    else:
        home_path = PurePosixPath(resolution.home)
        workspace_path = PurePosixPath(workspace)
    try:
        home_path.relative_to(workspace_path)
    except ValueError:
        return resolution
    raise RuntimeHomeResolutionError(
        "TRADINGCODEX_HOME must be outside TRADINGCODEX_WORKSPACE_ROOT; use a sibling or platform runtime home"
    )


def tradingcodex_home() -> Path:
    resolution = resolve_tradingcodex_home()
    if not isinstance(resolution.home, Path):
        raise RuntimeHomeResolutionError("TradingCodex home did not resolve to a native filesystem path")
    return resolution.home


def tradingcodex_state_dir() -> Path:
    return tradingcodex_home() / "state"


def tradingcodex_db_path() -> Path:
    configured = str(os.environ.get("TRADINGCODEX_DB_NAME") or "").strip()
    if configured:
        return Path(configured).expanduser().resolve(strict=False)
    return tradingcodex_state_dir() / "tradingcodex.sqlite3"


def runtime_home_status() -> dict[str, Any]:
    resolution = resolve_tradingcodex_home()
    payload = resolution.as_dict()
    configured_db = str(os.environ.get("TRADINGCODEX_DB_NAME") or "").strip()
    if configured_db:
        payload.update({
            "db_path": str(Path(configured_db).expanduser().resolve(strict=False)),
            "db_source": "environment_override",
        })
    else:
        payload.update({
            "db_path": str(resolution.home / "state" / "tradingcodex.sqlite3"),
            "db_source": "home_default",
        })
    payload["status"] = "ok"
    return payload


def _platform_family(platform_name: str) -> str:
    lowered = platform_name.lower()
    if lowered.startswith("win") or lowered == "nt":
        return "windows"
    if lowered == "darwin":
        return "macos"
    return "linux"


def _native_family() -> str:
    return _platform_family(sys.platform)


def _user_home_path(
    env: dict[str, str] | os._Environ[str],
    family: str,
    platform_name: str,
    supplied: str | Path | PurePath | None,
) -> Path | PurePath:
    if supplied is not None:
        return _canonical_home_candidate(supplied, family, platform_name)
    if family == "windows":
        raw = str(env.get("USERPROFILE") or "").strip()
        if not raw:
            raw = f"{env.get('HOMEDRIVE', '')}{env.get('HOMEPATH', '')}".strip()
        if not raw and family != _native_family():
            raise RuntimeHomeResolutionError("USERPROFILE is required when resolving a simulated Windows home")
    else:
        raw = str(env.get("HOME") or "").strip()
    raw = raw or str(Path.home())
    return _canonical_home_candidate(raw, family, platform_name)


def _platform_default_home(
    env: dict[str, str] | os._Environ[str],
    family: str,
    platform_name: str,
    home_dir: Path | PurePath,
) -> Path | PurePath:
    if family == "macos":
        candidate = home_dir / "Library" / "Application Support" / "TradingCodex"
    elif family == "windows":
        local_app_data = str(env.get("LOCALAPPDATA") or "").strip()
        if not local_app_data:
            raise RuntimeHomeResolutionError("LOCALAPPDATA is required for the native Windows TradingCodex home")
        candidate = _expand_home_candidate(local_app_data, home_dir, family, platform_name) / "TradingCodex"
    else:
        xdg_data_home = str(env.get("XDG_DATA_HOME") or "").strip()
        candidate = (
            _expand_home_candidate(xdg_data_home, home_dir, family, platform_name) / "tradingcodex"
            if xdg_data_home
            else home_dir / ".local" / "share" / "tradingcodex"
        )
    return _canonical_home_candidate(candidate, family, platform_name)


def _expand_home_candidate(
    raw: str,
    home_dir: Path | PurePath,
    family: str,
    platform_name: str,
) -> Path | PurePath:
    if raw == "~":
        return home_dir
    if raw.startswith("~/") or raw.startswith("~\\"):
        return _canonical_home_candidate(home_dir / raw[2:], family, platform_name)
    return _canonical_home_candidate(raw, family, platform_name)


def _canonical_home_candidate(value: str | Path | PurePath, family: str, platform_name: str) -> Path | PurePath:
    if (family == "windows") != (_native_family() == "windows"):
        pure_type = PureWindowsPath if family == "windows" else PurePosixPath
        return pure_type(str(value))
    return Path(value).expanduser().resolve(strict=False)


def _paths_match(left: Path | PurePath, right: Path | PurePath, family: str) -> bool:
    if family == "windows":
        return str(PureWindowsPath(left)).casefold() == str(PureWindowsPath(right)).casefold()
    return str(left) == str(right)


def workspace_manifest_path(workspace_root: Path | str) -> Path:
    return Path(workspace_root).expanduser().resolve() / WORKSPACE_MANIFEST_REL


def workspace_profiles_path(workspace_root: Path | str) -> Path:
    return Path(workspace_root).expanduser().resolve() / WORKSPACE_PROFILES_REL


def read_workspace_manifest(workspace_root: Path | str | None = None) -> dict[str, Any]:
    raw_root = workspace_root or os.environ.get("TRADINGCODEX_WORKSPACE_ROOT") or os.getcwd()
    path = workspace_manifest_path(raw_root)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid TradingCodex workspace manifest: {path}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"TradingCodex workspace manifest must be an object: {path}")
    validate_workspace_manifest(data)
    return data


def validate_workspace_manifest(manifest: dict[str, Any]) -> None:
    if (
        manifest.get("format") != WORKSPACE_FORMAT
        or type(manifest.get("schema_version")) is not int
        or manifest.get("schema_version") != WORKSPACE_SCHEMA_VERSION
    ):
        raise ValueError("unsupported pre-v1 TradingCodex workspace; attach v1 to a clean workspace")
    if set(manifest) != WORKSPACE_MANIFEST_FIELDS:
        raise ValueError("TradingCodex workspace manifest fields do not match the v1 schema")
    if not isinstance(manifest.get("workspace_id"), str) or not re.fullmatch(r"tcxw_[0-9a-f]{32}", manifest["workspace_id"]):
        raise ValueError("TradingCodex workspace_id is missing or invalid")
    project_name = manifest.get("project_name")
    if not isinstance(project_name, str) or not project_name.strip() or project_name != project_name.strip():
        raise ValueError("TradingCodex workspace project_name is required")
    for field in ("created_at", "updated_at"):
        _validate_iso_timestamp(manifest.get(field), f"workspace {field}")
    profile = manifest.get("active_profile")
    if not isinstance(profile, dict) or set(profile) != ACTIVE_PROFILE_FIELDS:
        raise ValueError("TradingCodex workspace active_profile is incomplete")
    if normalize_active_profile(profile) != profile:
        raise ValueError("TradingCodex workspace active_profile is not canonical")
    if manifest.get("mcp_scope") != "project-scoped":
        raise ValueError("TradingCodex workspace mcp_scope must be project-scoped")
    normalize_execution_mode(manifest["execution_mode"])


def normalize_execution_mode(value: Any = None) -> str:
    mode = str(value or DEFAULT_EXECUTION_MODE)
    if mode != DEFAULT_EXECUTION_MODE:
        raise ValueError(f"unsupported execution mode: {mode}")
    return mode


def _validate_iso_timestamp(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{field} is required")
    text = value
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must include a timezone")
    return text


def ensure_workspace_manifest(
    workspace_root: Path | str,
    project_name: str | None = None,
    generated_at: str | None = None,
    *,
    workspace_id: str | None = None,
) -> dict[str, Any]:
    root = Path(workspace_root).expanduser().resolve()
    existing = read_workspace_manifest(root)
    created_at = existing.get("created_at") or generated_at or now_iso()
    existing_id = str(existing.get("workspace_id") or "")
    if workspace_id and existing_id and workspace_id != existing_id:
        raise ValueError("TradingCodex workspace identity cannot be changed")
    workspace_id = str(existing_id or workspace_id or f"tcxw_{uuid.uuid4().hex}")
    is_new_workspace = not isinstance(existing.get("active_profile"), dict)
    active_profile = (
        existing.get("active_profile")
        if not is_new_workspace
        else isolated_profile_for_workspace(workspace_id)
    )
    manifest = {
        "format": WORKSPACE_FORMAT,
        "schema_version": WORKSPACE_SCHEMA_VERSION,
        "workspace_id": workspace_id,
        "project_name": project_name or existing.get("project_name") or root.name or "tradingcodex-workspace",
        "created_at": created_at,
        "updated_at": now_iso(),
        "active_profile": normalize_active_profile(active_profile),
        "mcp_scope": "project-scoped",
        "execution_mode": normalize_execution_mode(existing.get("execution_mode")),
    }
    validate_workspace_manifest(manifest)
    path = workspace_manifest_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    if is_new_workspace:
        write_workspace_profiles(root, {manifest["active_profile"]["profile_id"]: manifest["active_profile"]})
    return manifest


def normalize_active_profile(profile: dict[str, Any] | None = None) -> dict[str, Any]:
    if not isinstance(profile, dict):
        raise ValueError("active_profile must be an object")
    required = ("profile_id", "portfolio_id", "account_id", "strategy_id", "base_currency", "label")
    invalid_types = [field for field in required if not isinstance(profile.get(field), str)]
    if invalid_types:
        raise ValueError("active_profile fields must be strings: " + ", ".join(invalid_types))
    missing = [field for field in required if not str(profile.get(field) or "").strip()]
    if missing:
        raise ValueError("active_profile is missing: " + ", ".join(missing))
    base = {key: profile[key] for key in required}
    base["profile_id"] = sanitize_id(base["profile_id"])
    base["portfolio_id"] = sanitize_id(base["portfolio_id"])
    base["account_id"] = sanitize_id(base["account_id"])
    base["strategy_id"] = sanitize_id(base["strategy_id"])
    base["base_currency"] = normalize_currency_code(base.get("base_currency"))
    base["label"] = str(base["label"]).strip()
    return base


def normalize_currency_code(value: Any, field: str = "currency") -> str:
    code = str(value or DEFAULT_BASE_CURRENCY).strip().upper()
    if not re.fullmatch(r"[A-Z]{3}", code):
        raise ValueError(f"{field} must be a three-letter currency code")
    return code


def base_currency_for_workspace(workspace_root: Path | str | None = None) -> str:
    return str(active_profile_for_workspace(workspace_root)["base_currency"])


def active_profile_for_workspace(workspace_root: Path | str | None = None) -> dict[str, Any]:
    manifest = read_workspace_manifest(workspace_root)
    if not manifest:
        raise ValueError("TradingCodex workspace manifest is required")
    profile = manifest.get("active_profile") if isinstance(manifest.get("active_profile"), dict) else None
    return normalize_active_profile(profile)


def set_active_profile_for_workspace(workspace_root: Path | str, profile: dict[str, Any]) -> dict[str, Any]:
    root = Path(workspace_root).expanduser().resolve()
    normalized_profile = normalize_active_profile(profile)
    if set(profile) != ACTIVE_PROFILE_FIELDS or normalized_profile != profile:
        raise ValueError("TradingCodex workspace active_profile is not canonical")
    manifest = ensure_workspace_manifest(root)
    manifest["active_profile"] = normalized_profile
    manifest["updated_at"] = now_iso()
    path = workspace_manifest_path(root)
    atomic_write_text(path, json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    return manifest


def read_workspace_profiles(workspace_root: Path | str) -> dict[str, dict[str, Any]]:
    path = workspace_profiles_path(workspace_root)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid TradingCodex profile registry: {path}") from exc
    if (
        not isinstance(raw, dict)
        or set(raw) != {"format", "schema_version", "profiles"}
        or raw.get("format") != WORKSPACE_PROFILES_FORMAT
        or type(raw.get("schema_version")) is not int
        or raw.get("schema_version") != WORKSPACE_PROFILES_SCHEMA_VERSION
    ):
        raise ValueError("unsupported pre-v1 TradingCodex profile registry")
    profiles = raw["profiles"]
    if not isinstance(profiles, dict):
        raise ValueError("TradingCodex profile registry profiles must be an object")
    result: dict[str, dict[str, Any]] = {}
    for key, value in profiles.items():
        if not isinstance(value, dict) or set(value) != ACTIVE_PROFILE_FIELDS:
            raise ValueError(f"TradingCodex profile is invalid: {key}")
        normalized = normalize_active_profile(value)
        normalized_key = sanitize_id(key)
        if normalized_key != key or normalized != value:
            raise ValueError(f"TradingCodex profile is not canonical: {key}")
        if normalized["profile_id"] != normalized_key:
            raise ValueError(f"TradingCodex profile key does not match profile_id: {key}")
        result[normalized_key] = normalized
    return result


def write_workspace_profiles(workspace_root: Path | str, profiles: dict[str, dict[str, Any]]) -> None:
    path = workspace_profiles_path(workspace_root)
    normalized: dict[str, dict[str, Any]] = {}
    for key, value in profiles.items():
        if not isinstance(value, dict) or set(value) != ACTIVE_PROFILE_FIELDS:
            raise ValueError(f"TradingCodex profile is invalid: {key}")
        normalized_key = sanitize_id(key)
        normalized_profile = normalize_active_profile(value)
        if normalized_key != key or normalized_profile != value:
            raise ValueError(f"TradingCodex profile is not canonical: {key}")
        if normalized_profile["profile_id"] != normalized_key:
            raise ValueError(f"TradingCodex profile key does not match profile_id: {key}")
        normalized[normalized_key] = normalized_profile
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps({
        "format": WORKSPACE_PROFILES_FORMAT,
        "schema_version": WORKSPACE_PROFILES_SCHEMA_VERSION,
        "profiles": normalized,
    }, indent=2, ensure_ascii=False) + "\n")


def save_active_profile_for_workspace(workspace_root: Path | str, profile: dict[str, Any]) -> dict[str, Any]:
    manifest = set_active_profile_for_workspace(workspace_root, profile)
    registry = read_workspace_profiles(workspace_root)
    active = manifest["active_profile"]
    registry[active["profile_id"]] = active
    write_workspace_profiles(workspace_root, registry)
    return manifest


def configure_tradingcodex_database(workspace_root: Path | str | None = None) -> None:
    global _RUNTIME_DB_READY, _RUNTIME_DB_NAME
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tradingcodex_service.settings")
    if workspace_root is not None:
        assert_runtime_home_outside_workspace(workspace_root, tradingcodex_home())
    db_path = tradingcodex_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db_name = str(db_path)
    if _RUNTIME_DB_NAME == db_name:
        return
    if _RUNTIME_DB_NAME and _RUNTIME_DB_NAME != db_name:
        _RUNTIME_DB_READY = False
    from django.conf import settings
    from django.db import connections

    if settings.configured:
        current_name = settings.DATABASES["default"].get("NAME")
        settings.DATABASES["default"]["NAME"] = db_name
        settings.DATABASES["default"].setdefault("OPTIONS", {})["timeout"] = int(os.environ.get("TRADINGCODEX_SQLITE_TIMEOUT", "30"))
        connections["default"].settings_dict["NAME"] = db_name
        connections["default"].settings_dict.setdefault("OPTIONS", {})["timeout"] = int(os.environ.get("TRADINGCODEX_SQLITE_TIMEOUT", "30"))
        if current_name != db_name:
            connections.close_all()
            _RUNTIME_DB_READY = False
    _RUNTIME_DB_NAME = db_name


def configure_workspace_database(workspace_root: Path | str | None = None) -> None:
    configure_tradingcodex_database(workspace_root)


def workspace_context_payload(workspace_root: Path | str | None = None) -> dict[str, Any]:
    raw_root = workspace_root or os.environ.get("TRADINGCODEX_WORKSPACE_ROOT") or os.getcwd()
    root = Path(raw_root).expanduser().resolve()
    assert_runtime_home_outside_workspace(root, tradingcodex_home())
    manifest = read_workspace_manifest(root)
    if not manifest:
        raise ValueError("TradingCodex workspace manifest is required")
    path_hash = hashlib.sha256(canonical_path_identity(root).encode("utf-8")).hexdigest()
    workspace_id = str(manifest["workspace_id"])
    git = workspace_git_status(root)
    return {
        "workspace_id": workspace_id,
        "path_hash": path_hash,
        "project_name": str(manifest["project_name"]),
        "path": str(root),
        "git_root": git["git_root"],
        "git_dirty": git["git_dirty"],
        "git_remote": git["git_remote"],
        "git_branch": git["git_branch"],
        "db_path": str(tradingcodex_db_path()),
        "active_profile": active_profile_for_workspace(root),
        "mcp_scope": str(manifest.get("mcp_scope") or "project-scoped"),
        "execution_mode": normalize_execution_mode(manifest.get("execution_mode")),
    }


def persist_workspace_context_if_available(workspace_root: Path | str | None = None) -> dict[str, Any]:
    context = workspace_context_payload(workspace_root)
    ensure_runtime_database(None)
    from apps.harness.models import WorkspaceContext

    existing = (
        WorkspaceContext.objects.filter(workspace_id=context["workspace_id"]).first()
        or WorkspaceContext.objects.filter(path_hash=context["path_hash"]).first()
    )
    defaults = {
        "workspace_id": context["workspace_id"],
        "path_hash": context["path_hash"],
        "project_name": context["project_name"],
        "path": context["path"],
        "git_remote": context["git_remote"],
        "git_branch": context["git_branch"],
        "active_profile": context["active_profile"],
        "metadata": {
            "db_path": context["db_path"],
            "mcp_scope": context["mcp_scope"],
            "execution_mode": context["execution_mode"],
            "git_root": context["git_root"],
            "git_dirty": context["git_dirty"],
        },
    }
    if existing:
        for key, value in defaults.items():
            setattr(existing, key, value)
        existing.save(update_fields=[*defaults.keys(), "last_seen_at"])
    else:
        WorkspaceContext.objects.create(**defaults)
    return context


def runtime_migration_status(workspace_root: Path | str | None = None) -> dict[str, Any]:
    configure_tradingcodex_database(workspace_root)
    _setup_django()
    from django.apps import apps
    from django.db import connection
    from django.db.migrations.executor import MigrationExecutor

    executor = MigrationExecutor(connection)
    graph_nodes = set(executor.loader.graph.nodes)
    applied = set(executor.loader.applied_migrations)
    applied_project_apps = {app for app, _name in applied if app in PROJECT_MIGRATION_APPS}
    unknown = sorted(
        f"{app}.{name}"
        for app, name in applied - graph_nodes
        if app in PROJECT_MIGRATION_APPS
    )
    existing_tables = set(connection.introspection.table_names())
    project_models = [
        model
        for model in apps.get_models()
        if model._meta.app_label in PROJECT_MIGRATION_APPS
    ]
    v1_project_tables = {model._meta.db_table for model in project_models}
    for app_label, migration_name in applied:
        if app_label not in PROJECT_MIGRATION_APPS or (app_label, migration_name) not in graph_nodes:
            continue
        migration = executor.loader.get_migration(app_label, migration_name)
        for operation in migration.operations:
            if not hasattr(operation, "name") or operation.__class__.__name__ != "CreateModel":
                continue
            options = getattr(operation, "options", {}) or {}
            v1_project_tables.add(str(options.get("db_table") or f"{app_label}_{operation.name.lower()}"))
    owned_prefixes = tuple(f"{app}_" for app in sorted(PROJECT_MIGRATION_APPS))
    retired_or_unknown_tables = {
        table
        for table in existing_tables
        if table.startswith(owned_prefixes) and table not in v1_project_tables
    }
    current_tables_without_history = {
        model._meta.db_table
        for model in project_models
        if model._meta.app_label not in applied_project_apps
        and model._meta.db_table in existing_tables
    }
    untracked_tables = sorted(retired_or_unknown_tables | current_tables_without_history)
    pending = executor.migration_plan(executor.loader.graph.leaf_nodes())
    return {
        "compatible": not unknown and not untracked_tables,
        "unknown_applied": unknown,
        "untracked_project_tables": untracked_tables,
        "pending": [f"{migration.app_label}.{migration.name}" for migration, _backwards in pending],
        "applied_project": sorted(f"{app}.{name}" for app, name in applied if app in PROJECT_MIGRATION_APPS),
    }


def assert_runtime_database_compatible(workspace_root: Path | str | None = None) -> dict[str, Any]:
    status = runtime_migration_status(workspace_root)
    if status["unknown_applied"]:
        raise RuntimeMigrationError(
            _incompatible_v1_runtime_message(
                "migrations outside the clean v1 graph",
                status["unknown_applied"],
            )
        )
    if status["untracked_project_tables"]:
        raise RuntimeMigrationError(
            _incompatible_v1_runtime_message(
                "project tables without clean v1 migration history",
                status["untracked_project_tables"],
            )
        )
    return status


def _incompatible_v1_runtime_message(reason: str, details: list[str]) -> str:
    home = tradingcodex_home()
    database = tradingcodex_db_path()
    return "\n".join((
        f"TradingCodex v1 cannot use the selected runtime database because it contains {reason}: {', '.join(details)}.",
        f"Selected TRADINGCODEX_HOME: {home}",
        f"Selected database: {database}",
        "TradingCodex v1 will not migrate, delete, archive, or back up this prerelease/non-v1 state.",
        "Choose one recovery path and retry:",
        "- Set TRADINGCODEX_HOME to a new empty directory outside the workspace. If TRADINGCODEX_DB_NAME is set, unset it or point it to a new empty database outside the workspace.",
        "- Stop TradingCodex, then explicitly archive or remove the selected old home/database yourself. TradingCodex will not modify it for you.",
    ))


def migrate_runtime_database(workspace_root: Path | str | None = None) -> dict[str, Any]:
    global _RUNTIME_DB_READY
    configure_tradingcodex_database(workspace_root)
    from django.core.management import call_command
    _setup_django()
    try:
        with tradingcodex_file_lock("migrate"):
            before = assert_runtime_database_compatible(workspace_root)
            _sqlite_quick_check()
            backup_path = _backup_runtime_database() if before["pending"] and before["applied_project"] else None
            call_command("migrate", interactive=False, verbosity=0)
            after = assert_runtime_database_compatible(workspace_root)
            if after["pending"]:
                raise RuntimeMigrationError("runtime schema is incomplete after Django migrations")
            if not _runtime_model_tables_present():
                raise RuntimeMigrationError(
                    "runtime schema is incomplete after Django migrations; run `python manage.py migrate` and retry"
                )
            _RUNTIME_DB_READY = True
            return {**after, "backup_path": str(backup_path) if backup_path else ""}
    except RuntimeMigrationError:
        raise
    except Exception as exc:
        raise RuntimeMigrationError(
            "runtime database migration failed; inspect the database and run `python manage.py migrate`"
        ) from exc


def ensure_runtime_database(workspace_root: Path | str | None = None) -> None:
    global _RUNTIME_DB_READY
    configure_tradingcodex_database(workspace_root)
    _setup_django()
    if _RUNTIME_DB_READY:
        return
    status = assert_runtime_database_compatible(workspace_root)
    if status["pending"]:
        raise RuntimeMigrationError("runtime database has pending migrations; run `tcx db migrate` or `tcx update`")
    if not _runtime_model_tables_present():
        raise RuntimeMigrationError("runtime database schema is incomplete; run `tcx db migrate` or `tcx update`")
    _RUNTIME_DB_READY = True


def _setup_django() -> None:
    import django
    from django.apps import apps

    if not apps.ready:
        django.setup()


def _sqlite_quick_check() -> None:
    from django.db import connection

    with connection.cursor() as cursor:
        cursor.execute("PRAGMA quick_check")
        result = cursor.fetchone()
    if not result or result[0] != "ok":
        raise RuntimeMigrationError("runtime database failed SQLite quick_check")


def _backup_runtime_database() -> Path:
    source_path = tradingcodex_db_path()
    backup_dir = tradingcodex_home() / "backups" / "database"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    target_path = backup_dir / f"tradingcodex-{stamp}.sqlite3"
    with sqlite3.connect(source_path) as source, sqlite3.connect(target_path) as target:
        source.backup(target)
    return target_path


@contextmanager
def workspace_file_lock(workspace_root: Path | str, name: str):
    with tradingcodex_file_lock(name):
        yield


@contextmanager
def tradingcodex_file_lock(name: str):
    lock_path = tradingcodex_state_dir() / f"tradingcodex.{sanitize_id(name)}.lock"
    with exclusive_file_lock(lock_path, timeout_seconds=30):
        yield


def _runtime_model_tables_present() -> bool:
    try:
        from django.apps import apps
        from django.db import connection

        existing = set(connection.introspection.table_names())
        required = {
            model._meta.db_table
            for model in apps.get_models()
            if model._meta.managed and not model._meta.proxy
        }
        if not bool(required) or not required.issubset(existing):
            return False
        for model in apps.get_models():
            if not model._meta.managed or model._meta.proxy:
                continue
            columns = {
                column.name
                for column in connection.introspection.get_table_description(connection.cursor(), model._meta.db_table)
            }
            expected = {field.column for field in model._meta.local_concrete_fields}
            if not expected.issubset(columns):
                return False
        return True
    except Exception:
        return False

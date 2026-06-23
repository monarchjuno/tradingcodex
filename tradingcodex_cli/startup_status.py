from __future__ import annotations

import json
import os
import re
import shlex
import tempfile
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tradingcodex_cli.service_autostart import DEFAULT_SERVICE_ADDR, service_http_url, service_status as inspect_service_status
from tradingcodex_service.application.runtime import tradingcodex_db_path, tradingcodex_home
from tradingcodex_service.application.runtime_mode import get_runtime_mode_status
from tradingcodex_service.version import TRADINGCODEX_VERSION


UPDATE_PREFERENCES_REL = "preferences/update.json"
PYPI_JSON_URL = "https://pypi.org/pypi/tradingcodex/json"


def build_server_status(workspace_root: Path | str, addr: str | None = None) -> dict[str, Any]:
    root = Path(workspace_root).expanduser().resolve()
    service_addr = addr or os.environ.get("TRADINGCODEX_SERVICE_ADDR", DEFAULT_SERVICE_ADDR)
    dashboard_url = _safe_service_http_url(service_addr)
    health_url = f"{dashboard_url.rstrip('/')}/api/health"
    permission_status = detect_codex_permission_status(root)
    mode_status = get_runtime_mode_status(root, full_access_detected=permission_status["full_access_detected"])
    update_status = build_update_status(root, permission_status=permission_status, mode_status=mode_status)
    service_detail = inspect_service_status(service_addr)
    health = _read_health(health_url) if service_detail.get("reachable") else {}
    service_state = _service_state_from_detail(service_detail)
    mcp_config_present = _is_project_mcp_config_present(root)
    restart_codex_required = not mcp_config_present
    if restart_codex_required:
        recommended_action = "Run ./tcx update or ./tcx attach ., then fully quit and restart Codex and start a new thread."
    elif service_state == "ok":
        recommended_action = f"Open TradingCodex dashboard at {dashboard_url}"
    elif service_state == "incompatible":
        recommended_action = service_detail.get("next_action") or "Resolve the TradingCodex service mismatch before using the dashboard."
    else:
        recommended_action = service_detail.get("next_action") or "./tcx service ensure"
    startup_notice = build_startup_notice(service_detail=service_detail, service_status=service_state)
    allowed_next_actions = build_allowed_next_actions(
        mode_status=mode_status,
        permission_status=permission_status,
        update_status=update_status,
        service_status=service_state,
        service_detail=service_detail,
    )
    return {
        "marker": "tradingcodex-session-context",
        "checked_at": now(),
        "service_addr": service_addr,
        "dashboard_url": dashboard_url,
        "health_url": health_url,
        "service_status": service_state,
        "service_detail": service_detail,
        "service_health": health,
        "startup_notice": startup_notice,
        "mcp_config_present": mcp_config_present,
        "restart_codex_required": restart_codex_required,
        "permission_status": permission_status,
        "mode_status": mode_status,
        "update_status": update_status,
        "allowed_next_actions": allowed_next_actions,
        "recommended_action": recommended_action,
    }


def fallback_server_status(workspace_root: Path | str, exc: Exception, addr: str | None = None) -> dict[str, Any]:
    root = Path(workspace_root).expanduser().resolve()
    service_addr = addr or os.environ.get("TRADINGCODEX_SERVICE_ADDR", DEFAULT_SERVICE_ADDR)
    dashboard_url = _safe_service_http_url(service_addr)
    permission_status = detect_codex_permission_status(root)
    mode_status = get_runtime_mode_status(root, full_access_detected=permission_status["full_access_detected"])
    return {
        "marker": "tradingcodex-session-context",
        "checked_at": now(),
        "service_addr": service_addr,
        "dashboard_url": dashboard_url,
        "health_url": f"{dashboard_url.rstrip('/')}/api/health",
        "service_status": "unknown",
        "service_detail": {
            "addr": service_addr,
            "url": dashboard_url,
            "reachable": False,
            "compatible": False,
            "issue": "unknown",
            "next_action": "./tcx doctor --layer service",
        },
        "service_health": {},
        "startup_notice": f"TradingCodex startup status check failed: {exc}",
        "mcp_config_present": _is_project_mcp_config_present(root),
        "restart_codex_required": False,
        "permission_status": permission_status,
        "mode_status": mode_status,
        "update_status": fallback_update_status(permission_status=permission_status, mode_status=mode_status),
        "allowed_next_actions": ["Run ./tcx doctor --layer service"],
        "recommended_action": "./tcx doctor --layer service",
        "warning": f"server status check failed: {exc}",
    }


def write_server_status_snapshot(workspace_root: Path | str) -> dict[str, Any]:
    root = Path(workspace_root).expanduser().resolve()
    try:
        status = build_server_status(root)
    except Exception as exc:
        status = fallback_server_status(root, exc)
    path = root / ".tradingcodex" / "mainagent" / "server-status.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(status, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return status


def build_update_status(
    workspace_root: Path | str,
    *,
    permission_status: dict[str, Any] | None = None,
    mode_status: dict[str, Any] | None = None,
) -> dict[str, Any]:
    root = Path(workspace_root).expanduser().resolve()
    permission_status = permission_status or detect_codex_permission_status(root)
    mode_status = mode_status or get_runtime_mode_status(root, full_access_detected=permission_status["full_access_detected"])
    module_lock = _read_json(root / ".tradingcodex" / "generated" / "module-lock.json", {})
    preference_path = tradingcodex_home() / UPDATE_PREFERENCES_REL
    preferences = _read_json(preference_path, {})
    workspace_version = str(module_lock.get("tradingcodex_version") or "unknown")
    package_spec = str(module_lock.get("tradingcodex_package_spec") or os.environ.get("TRADINGCODEX_MCP_PACKAGE_SPEC") or "tradingcodex")
    installed_version = TRADINGCODEX_VERSION
    suppressed = bool(preferences.get("suppress_update_recommendation"))
    versions_match = workspace_version == installed_version
    workspace_update_available = workspace_version not in {"", "unknown"} and not versions_match
    if workspace_update_available:
        latest = latest_release_info()
    else:
        latest = {
            "latest_release_version": "not_checked",
            "latest_release_status": "not_needed",
            "latest_release_source": "versions_match" if versions_match else "workspace_version_unavailable",
        }
    latest_version = latest["latest_release_version"]
    latest_status = latest["latest_release_status"]
    installed_release_is_stale = latest_status == "ok" and version_less_than(installed_version, latest_version)
    package_update_required_first = workspace_update_available and installed_release_is_stale
    workspace_update_allowed = workspace_update_available and not package_update_required_first
    workspace_update_recommended = workspace_update_allowed and not suppressed
    blocked_reason = "installed tcx is older than the latest release; update the package before refreshing this workspace" if package_update_required_first else ""
    workspace_update_command = "./tcx update --skip-refresh"
    update_available = workspace_update_available or installed_release_is_stale
    user_update_command = f"uvx --refresh --from {shlex.quote(package_spec)} tcx update ."
    self_update_command = user_update_command if package_update_required_first else workspace_update_command
    can_self_update = bool(update_available and mode_status.get("build_enabled"))
    head_manager_update_allowed = can_self_update
    head_manager_update_command = self_update_command if can_self_update else ""
    if update_available and not mode_status.get("build_enabled"):
        head_manager_update_blocked_reason = "head-manager self-update requires Codex full access and explicit TradingCodex build mode"
    elif package_update_required_first and not can_self_update:
        head_manager_update_blocked_reason = "package refresh must run outside restricted Codex permissions or inside build mode with full access"
    else:
        head_manager_update_blocked_reason = ""
    if package_update_required_first:
        recommended_action = f"Use build mode with full access for self-update, or ask the user to run from a terminal: {user_update_command}"
    elif workspace_update_recommended:
        recommended_action = f"Use build mode with full access for self-update, or ask the user to run from a terminal: {workspace_update_command}"
    else:
        recommended_action = ""
    return {
        "checked_at": now(),
        "workspace_version": workspace_version,
        "installed_version": installed_version,
        "package_version": installed_version,
        "latest_release_version": latest_version,
        "latest_release_status": latest_status,
        "latest_release_source": latest["latest_release_source"],
        "package_spec": package_spec,
        "update_available": update_available,
        "versions_match": versions_match,
        "workspace_update_available": workspace_update_available,
        "workspace_update_required": workspace_update_available,
        "workspace_update_allowed": workspace_update_allowed,
        "workspace_update_recommended": workspace_update_recommended,
        "package_update_required": package_update_required_first,
        "package_update_required_first": package_update_required_first,
        "workspace_update_command": workspace_update_command,
        "command": self_update_command,
        "can_self_update": can_self_update,
        "head_manager_update_allowed": head_manager_update_allowed,
        "head_manager_update_command": head_manager_update_command,
        "head_manager_update_blocked_reason": head_manager_update_blocked_reason,
        "user_update_command": user_update_command,
        "restart_required_after_update": update_available,
        "self_update_requires": ["codex_full_access", "tradingcodex_build_mode", "explicit_user_request"],
        "installed_is_latest_release": latest_status == "ok" and installed_version == latest_version,
        "installed_release_is_stale": installed_release_is_stale,
        "update_recommendation_suppressed": suppressed,
        "preference_path": str(preference_path),
        "recommended_action": recommended_action,
        "blocked_reason": blocked_reason,
    }


def fallback_update_status(
    *,
    permission_status: dict[str, Any] | None = None,
    mode_status: dict[str, Any] | None = None,
) -> dict[str, Any]:
    package_spec = os.environ.get("TRADINGCODEX_MCP_PACKAGE_SPEC", "tradingcodex")
    mode_status = mode_status or {"build_enabled": False}
    return {
        "checked_at": now(),
        "workspace_version": "unknown",
        "installed_version": TRADINGCODEX_VERSION,
        "package_version": TRADINGCODEX_VERSION,
        "latest_release_version": "unknown",
        "latest_release_status": "unknown",
        "latest_release_source": "fallback",
        "package_spec": package_spec,
        "update_available": False,
        "versions_match": False,
        "workspace_update_available": False,
        "workspace_update_required": False,
        "workspace_update_allowed": False,
        "workspace_update_recommended": False,
        "package_update_required": False,
        "package_update_required_first": False,
        "workspace_update_command": "./tcx update --skip-refresh",
        "command": "./tcx update --skip-refresh",
        "can_self_update": False,
        "head_manager_update_allowed": False,
        "head_manager_update_command": "",
        "head_manager_update_blocked_reason": "head-manager self-update requires Codex full access and explicit TradingCodex build mode",
        "user_update_command": f"uvx --refresh --from {shlex.quote(package_spec)} tcx update .",
        "restart_required_after_update": False,
        "self_update_requires": ["codex_full_access", "tradingcodex_build_mode", "explicit_user_request"],
        "installed_is_latest_release": False,
        "installed_release_is_stale": False,
        "update_recommendation_suppressed": False,
        "preference_path": str(tradingcodex_home() / UPDATE_PREFERENCES_REL),
        "recommended_action": "",
        "blocked_reason": "",
    }


def detect_codex_permission_status(workspace_root: Path | str) -> dict[str, Any]:
    root = Path(workspace_root).expanduser().resolve()
    override = os.environ.get("TRADINGCODEX_CODEX_PERMISSION", "").strip().lower()
    sandbox_mode = os.environ.get("CODEX_SANDBOX_MODE", "").strip().lower()
    config_mode = _read_project_sandbox_mode(root)
    raw = override or sandbox_mode or config_mode or "unknown"
    normalized = _normalize_permission(raw)
    home_write = _can_write_tradingcodex_home()
    return {
        "codex_permission": normalized,
        "raw_permission": raw,
        "full_access_detected": normalized == "full_access",
        "writable_roots_ok": home_write,
        "tradingcodex_home": str(tradingcodex_home()),
        "detection_source": "env" if override or sandbox_mode else "project_config" if config_mode else "unknown",
    }


def build_allowed_next_actions(
    *,
    mode_status: dict[str, Any],
    permission_status: dict[str, Any],
    update_status: dict[str, Any],
    service_status: str,
    service_detail: dict[str, Any] | None = None,
) -> list[str]:
    actions: list[str] = []
    if service_status != "ok":
        service_detail = service_detail or {}
        next_action = str(service_detail.get("next_action") or "").strip()
        actions.append(next_action or "./tcx service ensure")
    if update_status.get("update_available"):
        if update_status.get("can_self_update"):
            actions.append(f"On explicit user request, run {update_status['command']} and then stop for Codex restart")
        else:
            actions.append("Switch Codex to full access and run `tcx mode set build --reason <reason>` for head-manager self-update")
            actions.append(f"Or run from a terminal: {update_status['command']}")
    if not mode_status.get("build_enabled"):
        actions.append("Operate mode: investment workflows and safe server/broker reads only")
    if permission_status.get("codex_permission") == "full_access" and not mode_status.get("tcx_build_mode_active"):
        actions.append("Full access detected; run `tcx mode set build --reason <reason>` before build/update work")
    if mode_status.get("build_enabled"):
        actions.append("Build mode: TradingCodex updates and connector scaffolds are allowed; live execution remains blocked")
    return actions


def _service_state_from_detail(service_detail: dict[str, Any]) -> str:
    if service_detail.get("compatible"):
        return "ok"
    if service_detail.get("reachable"):
        return "incompatible"
    return "not_running_or_unreachable"


def build_startup_notice(*, service_detail: dict[str, Any], service_status: str) -> str:
    issue = str(service_detail.get("issue") or "").strip()
    if service_status == "ok" or not issue:
        return ""
    next_action = str(service_detail.get("next_action") or "").strip()
    if issue == "version_mismatch":
        service_version = service_detail.get("version") or "unknown"
        package_version = service_detail.get("package_version") or TRADINGCODEX_VERSION
        return f"TradingCodex service version mismatch: service={service_version} package={package_version}. {next_action}"
    if issue == "db_mismatch":
        service_db = service_detail.get("db_path") or "unknown"
        expected_db = service_detail.get("expected_db_path") or str(tradingcodex_db_path())
        return f"TradingCodex service DB mismatch: service={service_db} package={expected_db}. {next_action}"
    if issue == "port_occupied":
        addr = service_detail.get("addr") or DEFAULT_SERVICE_ADDR
        return f"TradingCodex service port is occupied by a non-TradingCodex process at {addr}. {next_action}"
    if issue == "not_running":
        return ""
    return next_action


def latest_release_info() -> dict[str, str]:
    override = os.environ.get("TRADINGCODEX_LATEST_RELEASE_VERSION", "").strip()
    if override:
        return {
            "latest_release_version": override,
            "latest_release_status": "ok",
            "latest_release_source": "env",
        }
    if os.environ.get("TRADINGCODEX_DISABLE_LATEST_RELEASE_CHECK", "").lower() in {"1", "true", "yes", "on"}:
        return {
            "latest_release_version": "unknown",
            "latest_release_status": "unknown",
            "latest_release_source": "disabled",
        }
    try:
        timeout = float(os.environ.get("TRADINGCODEX_LATEST_RELEASE_TIMEOUT", "0.75"))
        with urllib.request.urlopen(PYPI_JSON_URL, timeout=timeout) as response:
            payload = response.read().decode("utf-8")
        data = json.loads(payload)
        version = str((data.get("info") or {}).get("version") or "").strip()
        if version:
            return {
                "latest_release_version": version,
                "latest_release_status": "ok",
                "latest_release_source": "pypi",
            }
    except Exception:
        pass
    return {
        "latest_release_version": "unknown",
        "latest_release_status": "unknown",
        "latest_release_source": "unavailable",
    }


def version_less_than(left: str, right: str) -> bool:
    left_key = _release_version_key(left)
    right_key = _release_version_key(right)
    if not left_key or not right_key:
        return False
    length = max(len(left_key), len(right_key))
    return left_key + (0,) * (length - len(left_key)) < right_key + (0,) * (length - len(right_key))


def now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _release_version_key(version: str) -> tuple[int, ...]:
    match = re.match(r"^\s*(\d+(?:\.\d+)*)", version)
    if not match:
        return ()
    return tuple(int(part) for part in match.group(1).split("."))


def _safe_service_http_url(addr: str) -> str:
    try:
        return service_http_url(addr)
    except Exception:
        return service_http_url(DEFAULT_SERVICE_ADDR)


def _is_project_mcp_config_present(root: Path) -> bool:
    config_path = root / ".codex" / "config.toml"
    try:
        text = config_path.read_text(encoding="utf-8")
    except Exception:
        return False
    return "[mcp_servers.tradingcodex]" in text and "TRADINGCODEX_MCP_AUTOSTART_SERVICE" in text


def _read_health(url: str) -> dict[str, Any]:
    try:
        with urllib.request.urlopen(url, timeout=0.5) as response:
            payload = response.read().decode("utf-8")
        data = json.loads(payload)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _is_compatible_health(health: dict[str, Any]) -> bool:
    return (
        health.get("service") == "tradingcodex"
        and health.get("version") == TRADINGCODEX_VERSION
        and str(health.get("db_path") or "") == str(tradingcodex_db_path())
    )


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _read_project_sandbox_mode(root: Path) -> str:
    try:
        text = (root / ".codex" / "config.toml").read_text(encoding="utf-8")
    except Exception:
        return ""
    match = re.search(r'^\s*sandbox_mode\s*=\s*"([^"]+)"', text, re.M)
    return match.group(1).strip().lower() if match else ""


def _normalize_permission(value: str) -> str:
    lowered = value.strip().lower().replace("_", "-")
    if lowered in {"danger-full-access", "full-access", "full", "unrestricted"}:
        return "full_access"
    if lowered in {"workspace-write", "read-only", "restricted", "tradingcodex"}:
        return "restricted"
    return "unknown"


def _can_write_tradingcodex_home() -> bool:
    state_dir = tradingcodex_home() / "state"
    try:
        state_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(prefix=".permission-check-", dir=state_dir, delete=True) as handle:
            handle.write(b"ok")
            handle.flush()
        return True
    except Exception:
        return False

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import tempfile
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tradingcodex_cli.service_autostart import (
    DEFAULT_SERVICE_ADDR,
    open_loopback_url,
    service_http_url,
    service_status as inspect_service_status,
)
from tradingcodex_cli.package_source import LOCAL_EXECUTABLE_SOURCE_PROVENANCE
from tradingcodex_cli.versioning import version_less_than
from tradingcodex_service.application.common import atomic_write_text, paths_equivalent, read_json as _read_json, workspace_launcher_command
from tradingcodex_service.application.runtime import tradingcodex_db_path, tradingcodex_home
from tradingcodex_service.application.runtime_mode import get_runtime_mode_status
from tradingcodex_service.version import TRADINGCODEX_VERSION


UPDATE_PREFERENCES_REL = "preferences/update.json"
PYPI_JSON_URL = "https://pypi.org/pypi/tradingcodex/json"
BUILD_SKILL_FIRST_LINE = "$tcx-build"
MANAGED_SKILL_FIRST_LINES = {
    "brain": "$tcx-brain",
    "strategy": "$tcx-strategy",
}


def build_server_status(workspace_root: Path | str, addr: str | None = None) -> dict[str, Any]:
    root = Path(workspace_root).expanduser().resolve()
    service_addr = addr or os.environ.get("TRADINGCODEX_SERVICE_ADDR", DEFAULT_SERVICE_ADDR)
    dashboard_url = service_http_url(service_addr)
    health_url = f"{dashboard_url.rstrip('/')}/api/health/ready"
    permission_status = detect_codex_permission_status(root)
    mode_status = get_runtime_mode_status(root, full_access_detected=permission_status["full_access_detected"])
    service_detail = inspect_service_status(service_addr)
    latest_version_hint = _latest_version_hint_from_service(service_detail)
    update_status = build_update_status(
        root,
        permission_status=permission_status,
        latest_version_hint=latest_version_hint,
    )
    health = _read_health(health_url) if service_detail.get("reachable") else {}
    service_state = _service_state_from_detail(service_detail)
    mcp_config_present = _is_project_mcp_config_present(root)
    restart_codex_required = not mcp_config_present
    if restart_codex_required:
        recommended_action = f"Run {_workspace_launcher()} update or tcx attach ., then fully quit and restart Codex and start a new thread."
    elif service_state == "ok":
        recommended_action = f"Open the TradingCodex workspace viewer at {dashboard_url}"
    elif service_state == "incompatible":
        recommended_action = service_detail.get("next_action") or "Resolve the TradingCodex service mismatch before using the workspace viewer."
    else:
        recommended_action = service_detail.get("next_action") or f"{_workspace_launcher()} service ensure"
    startup_notice = build_startup_notice(service_detail=service_detail, service_status=service_state)
    allowed_next_actions = build_allowed_next_actions(
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
        "build_authorization": build_turn_authorization_status(permission_status),
        "managed_skill_authorization": managed_skill_authorization_status(permission_status),
        # Compatibility only for generated hooks from the retired persistent
        # mode release. This projection is inert and always fail-closed.
        "mode_status": mode_status,
        "update_status": update_status,
        "allowed_next_actions": allowed_next_actions,
        "recommended_action": recommended_action,
    }


def write_server_status_snapshot(workspace_root: Path | str) -> dict[str, Any]:
    root = Path(workspace_root).expanduser().resolve()
    status = build_server_status(root)
    path = root / ".tradingcodex" / "mainagent" / "server-status.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps(status, indent=2, ensure_ascii=False) + "\n")
    return status


def build_update_status(
    workspace_root: Path | str,
    *,
    permission_status: dict[str, Any] | None = None,
    mode_status: dict[str, Any] | None = None,
    check_latest_release: bool = False,
    latest_version_hint: str = "",
) -> dict[str, Any]:
    from tradingcodex_cli.generator import read_module_lock

    root = Path(workspace_root).expanduser().resolve()
    permission_status = permission_status or detect_codex_permission_status(root)
    # Retained only so older callers do not fail during package/workspace
    # transition. A caller-provided build_enabled value is never authority.
    del mode_status
    module_lock = read_module_lock(root, allow_newer=True)
    preference_path = tradingcodex_home() / UPDATE_PREFERENCES_REL
    preferences = _read_json(preference_path, {})
    workspace_version = str(module_lock["tradingcodex_version"])
    package_provenance = module_lock["tradingcodex_package_spec"]
    local_source_requires_explicit = package_provenance == LOCAL_EXECUTABLE_SOURCE_PROVENANCE
    package_spec = "" if local_source_requires_explicit else package_provenance
    installed_version = TRADINGCODEX_VERSION
    suppressed = bool(preferences.get("suppress_update_recommendation"))
    versions_match = workspace_version == installed_version
    workspace_is_newer = version_less_than(installed_version, workspace_version)
    workspace_update_available = version_less_than(workspace_version, installed_version)
    latest_version_hint = latest_version_hint.strip()
    if workspace_update_available or check_latest_release:
        latest = latest_release_info()
        if latest["latest_release_status"] != "ok" and latest_version_hint and version_less_than(installed_version, latest_version_hint):
            latest = {
                "latest_release_version": latest_version_hint,
                "latest_release_status": "ok",
                "latest_release_source": "service_status",
            }
    elif latest_version_hint and version_less_than(installed_version, latest_version_hint):
        latest = {
            "latest_release_version": latest_version_hint,
            "latest_release_status": "ok",
            "latest_release_source": "service_status",
        }
    else:
        latest = {
            "latest_release_version": "not_checked",
            "latest_release_status": "not_needed",
            "latest_release_source": "workspace_is_newer" if workspace_is_newer else "versions_match",
        }
    latest_version = latest["latest_release_version"]
    latest_status = latest["latest_release_status"]
    installed_release_is_stale = latest_status == "ok" and version_less_than(installed_version, latest_version)
    package_update_required = installed_release_is_stale or workspace_is_newer
    package_update_required_first = package_update_required
    workspace_update_allowed = workspace_update_available and not package_update_required_first
    workspace_update_recommended = workspace_update_allowed and not suppressed
    if workspace_is_newer:
        blocked_reason = "workspace was generated by a newer TradingCodex release; update the package before refreshing this workspace"
    elif package_update_required:
        blocked_reason = "installed tcx is older than the latest release; update the package before refreshing this workspace"
    else:
        blocked_reason = ""
    workspace_update_command = f"{_workspace_launcher()} update --skip-refresh"
    update_available = workspace_update_available or package_update_required
    if local_source_requires_explicit:
        user_update_command = (
            "uvx --refresh --from <package-spec> tcx update . --from <package-spec>"
        )
    else:
        user_update_command = _display_command(
            [
                "uvx",
                "--refresh",
                "--from",
                package_spec,
                "tcx",
                "update",
                ".",
                "--from",
                package_spec,
            ]
        )
    workspace_build_update_supported = workspace_update_allowed
    workspace_writable = bool(
        permission_status.get("workspace_writable")
        or permission_status.get("workspace_write_detected")
        or permission_status.get("full_access_detected")
    )
    workspace_build_update_eligible = workspace_build_update_supported and workspace_writable
    package_refresh_user_terminal_required = package_update_required
    interactive_user_terminal_command = (
        user_update_command if package_update_required else workspace_update_command
    )
    # Static status cannot prove a future root user prompt. UserPromptSubmit is
    # the sole source of an exact current-turn build authorization.
    can_self_update = False
    head_manager_update_allowed = False
    head_manager_update_command = (
        workspace_update_command if workspace_build_update_supported else ""
    )
    if package_update_required:
        head_manager_update_blocked_reason = (
            "package refresh is restricted to an explicit interactive user-terminal action; "
            "Head Manager must not run uvx or refresh the installed package"
        )
        update_execution_surface = "interactive_user_terminal"
        self_update_requires = ["interactive_user_terminal", "explicit_user_request"]
    elif workspace_update_available:
        if workspace_writable:
            head_manager_update_blocked_reason = (
                "workspace-local self-update requires a current root native Codex turn "
                f"whose exact first line is `{BUILD_SKILL_FIRST_LINE}`"
            )
        else:
            head_manager_update_blocked_reason = (
                "workspace-local self-update requires the trading-build profile plus a current root "
                f"native Codex turn whose exact first line is `{BUILD_SKILL_FIRST_LINE}`"
            )
        update_execution_surface = "workspace_local_build_or_user_terminal"
        self_update_requires = [
            "codex_writable_session",
            "exact_tcx_build_turn",
            "explicit_user_request",
        ]
    else:
        head_manager_update_blocked_reason = ""
        update_execution_surface = "none"
        self_update_requires = []
    if package_update_required:
        recommended_action = (
            "Run the package refresh from an interactive user terminal; Head Manager must not "
            f"execute it: {user_update_command}"
        )
    elif workspace_update_recommended:
        recommended_action = (
            f"Use a writable root native Codex turn beginning with exact `{BUILD_SKILL_FIRST_LINE}` "
            f"and run only `{workspace_update_command}`, or run that command from an "
            "interactive user terminal"
        )
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
        "package_source_kind": (
            "local-explicit" if local_source_requires_explicit else "persistent"
        ),
        "package_source_requires_explicit": local_source_requires_explicit,
        "update_available": update_available,
        "versions_match": versions_match,
        "workspace_update_available": workspace_update_available,
        "workspace_is_newer_than_installed": workspace_is_newer,
        "workspace_update_required": workspace_update_available,
        "workspace_update_allowed": workspace_update_allowed,
        "workspace_update_recommended": workspace_update_recommended,
        "workspace_build_update_supported": workspace_build_update_supported,
        "workspace_build_update_eligible": workspace_build_update_eligible,
        "package_update_required": package_update_required,
        "package_update_required_first": package_update_required_first,
        "package_refresh_user_terminal_required": package_refresh_user_terminal_required,
        "workspace_update_command": workspace_update_command,
        # `command` is intentionally empty for package refreshes so a generic
        # status consumer cannot mistake an interactive uvx action for a
        # Head Manager-executable command.
        "command": head_manager_update_command,
        "can_self_update": can_self_update,
        "head_manager_update_allowed": head_manager_update_allowed,
        "head_manager_update_command": head_manager_update_command,
        "head_manager_update_blocked_reason": head_manager_update_blocked_reason,
        "user_update_command": user_update_command,
        "interactive_user_terminal_command": interactive_user_terminal_command,
        "update_execution_surface": update_execution_surface,
        "restart_required_after_update": update_available,
        "self_update_requires": self_update_requires,
        "build_authorization": build_turn_authorization_status(permission_status),
        "installed_is_latest_release": latest_status == "ok" and installed_version == latest_version,
        "installed_release_is_stale": installed_release_is_stale,
        "update_recommendation_suppressed": suppressed,
        "preference_path": str(preference_path),
        "recommended_action": recommended_action,
        "blocked_reason": blocked_reason,
    }


def detect_codex_permission_status(workspace_root: Path | str) -> dict[str, Any]:
    root = Path(workspace_root).expanduser().resolve()
    override = os.environ.get("TRADINGCODEX_CODEX_PERMISSION", "").strip().lower()
    sandbox_mode = os.environ.get("CODEX_SANDBOX_MODE", "").strip().lower()
    config_mode = _read_project_permission(root)
    raw = override or sandbox_mode or config_mode or "unknown"
    normalized = _normalize_permission(raw)
    home_write = _can_write_tradingcodex_home()
    managed_workspace_writable = normalized in {"workspace_write", "full_access"}
    ordinary_workspace_writable = managed_workspace_writable or raw in {
        "trading-research",
        "tradingcodex-research",
    }
    return {
        "codex_permission": normalized,
        "raw_permission": raw,
        "full_access_detected": normalized == "full_access",
        "workspace_write_detected": normalized == "workspace_write",
        # Compatibility field: this means managed/protected workspace changes
        # are available, not that every ordinary user-owned path is read-only.
        "workspace_writable": managed_workspace_writable,
        "managed_workspace_writable": managed_workspace_writable,
        "ordinary_workspace_writable": ordinary_workspace_writable,
        "writable_roots_ok": home_write,
        "tradingcodex_home": str(tradingcodex_home()),
        "detection_source": "env" if override or sandbox_mode else "project_config" if config_mode else "unknown",
    }


def build_allowed_next_actions(
    *,
    mode_status: dict[str, Any] | None = None,
    permission_status: dict[str, Any],
    update_status: dict[str, Any],
    service_status: str,
    service_detail: dict[str, Any] | None = None,
) -> list[str]:
    # Compatibility argument from the retired persistent-mode status shape.
    del mode_status
    actions: list[str] = []
    if service_status != "ok":
        service_detail = service_detail or {}
        next_action = str(service_detail.get("next_action") or "").strip()
        actions.append(next_action or f"{_workspace_launcher()} service ensure")
    if update_status.get("package_refresh_user_terminal_required") or update_status.get(
        "package_update_required_first"
    ):
        command = update_status.get("interactive_user_terminal_command") or update_status.get(
            "user_update_command"
        )
        actions.append(f"From an interactive user terminal only, run: {command}")
    elif update_status.get("workspace_update_recommended"):
        if (
            permission_status.get("workspace_writable")
            or permission_status.get("workspace_write_detected")
            or permission_status.get("full_access_detected")
        ):
            actions.append(
                f"Start a root native Codex turn with exact first line `{BUILD_SKILL_FIRST_LINE}` "
                "for the requested workspace-local update"
            )
        else:
            actions.append(
                "Select the trading-build permission profile, then start a root turn "
                f"with exact first line `{BUILD_SKILL_FIRST_LINE}`"
            )
        command = update_status["workspace_update_command"]
        actions.append(f"Within that Build turn, run only: {command}")
        actions.append(f"Or run from an interactive user terminal: {command}")
    return actions


def build_turn_authorization_status(permission_status: dict[str, Any] | None = None) -> dict[str, Any]:
    permission_status = permission_status or {}
    return {
        "status": "exact_turn_required",
        "authority": "user_prompt_submit_hook",
        "exact_first_line": BUILD_SKILL_FIRST_LINE,
        "root_native_turn_only": True,
        "persistent_mode": False,
        "active": False,
        "permission_is_advisory": True,
        "recommended_profile": "trading-build",
        "full_access_detected": bool(permission_status.get("full_access_detected")),
        "workspace_writable": bool(
            permission_status.get("workspace_writable")
            or permission_status.get("workspace_write_detected")
            or permission_status.get("full_access_detected")
        ),
    }


def managed_skill_authorization_status(
    permission_status: dict[str, Any] | None = None,
) -> dict[str, Any]:
    permission_status = permission_status or {}
    return {
        "status": "exact_capability_turn_required",
        "authority": "user_prompt_submit_hook",
        "exact_first_lines": dict(MANAGED_SKILL_FIRST_LINES),
        "root_native_turn_only": True,
        "persistent_mode": False,
        "active": False,
        "recommended_profile": "trading-research",
        "lifecycle_transport": "proof_protected_mcp",
        "runtime_filesystem_access": False,
        "cross_scope": False,
        "plan_mode_allowed": False,
        "ordinary_workspace_writable": bool(
            permission_status.get("ordinary_workspace_writable")
        ),
    }


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


def _latest_version_hint_from_service(service_detail: dict[str, Any]) -> str:
    if service_detail.get("service") != "tradingcodex":
        return ""
    service_db = str(service_detail.get("db_path") or "")
    expected_db = str(service_detail.get("expected_db_path") or tradingcodex_db_path())
    if not service_db or not paths_equivalent(service_db, expected_db):
        return ""
    return str(service_detail.get("version") or "")


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


def now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _is_project_mcp_config_present(root: Path) -> bool:
    config_path = root / ".codex" / "config.toml"
    try:
        text = config_path.read_text(encoding="utf-8")
    except Exception:
        return False
    return "[mcp_servers.tradingcodex]" in text and "TRADINGCODEX_MCP_AUTOSTART_SERVICE" in text


def _read_health(url: str) -> dict[str, Any]:
    try:
        with open_loopback_url(url, timeout=0.5) as response:
            payload = response.read().decode("utf-8")
        data = json.loads(payload)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _is_compatible_health(health: dict[str, Any]) -> bool:
    return (
        health.get("service") == "tradingcodex"
        and health.get("version") == TRADINGCODEX_VERSION
        and paths_equivalent(str(health.get("db_path") or ""), tradingcodex_db_path())
    )


def _read_project_permission(root: Path) -> str:
    try:
        text = (root / ".codex" / "config.toml").read_text(encoding="utf-8")
    except Exception:
        return ""
    match = re.search(r'^\s*default_permissions\s*=\s*"([^"]+)"', text, re.M)
    if match:
        return match.group(1).strip().lower()
    match = re.search(r'^\s*sandbox_mode\s*=\s*"([^"]+)"', text, re.M)
    return match.group(1).strip().lower() if match else ""


def _normalize_permission(value: str) -> str:
    lowered = value.strip().lower().replace("_", "-")
    if lowered in {"danger-full-access", "full-access", "full", "unrestricted"}:
        return "full_access"
    if lowered in {"workspace-write", "workspace-writable"}:
        return "workspace_write"
    if lowered in {"trading-build", "tradingcodex-build"}:
        return "workspace_write"
    if lowered in {"read-only", "readonly"}:
        return "read_only"
    if lowered in {"restricted", "tradingcodex", "trading-research", "tradingcodex-research"}:
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


def _workspace_launcher() -> str:
    return workspace_launcher_command()


def _display_command(argv: list[str]) -> str:
    return subprocess.list2cmdline(argv) if os.name == "nt" else shlex.join(argv)

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


MODE_FILE_RELATIVE_PATH = Path(".tradingcodex") / "runtime" / "mode.json"
DEFAULT_MODE = "operate"
BUILD_TTL_HOURS = 24
VALID_MODES = {"operate", "build"}


def get_runtime_mode_status(
    workspace_root: Path | str,
    *,
    full_access_detected: bool = False,
    checked_at: datetime | None = None,
) -> dict[str, Any]:
    root = Path(workspace_root).expanduser().resolve()
    current_time = checked_at or _now()
    raw = _read_mode_file(root)
    requested_mode = str(raw.get("mode") or DEFAULT_MODE)
    if requested_mode not in VALID_MODES:
        requested_mode = DEFAULT_MODE
    expires_at = str(raw.get("expires_at") or "")
    expired = _expired(expires_at, current_time) if requested_mode == "build" else False
    mode = DEFAULT_MODE if expired else requested_mode
    tcx_build_mode_active = mode == "build"
    build_enabled = tcx_build_mode_active and full_access_detected
    blocked_reason = ""
    if requested_mode == "build" and expired:
        blocked_reason = "TradingCodex build mode expired; run `tcx mode set build --reason <reason>` again."
    elif mode == "build" and not full_access_detected:
        blocked_reason = "Codex full access is required in addition to TradingCodex build mode."
    elif mode != "build":
        blocked_reason = "TradingCodex is in operate mode."
    return {
        "mode": mode,
        "requested_mode": requested_mode,
        "build_enabled": build_enabled,
        "build_blocked_reason": "" if build_enabled else blocked_reason,
        "tcx_build_mode_active": tcx_build_mode_active,
        "full_access_required": True,
        "full_access_detected": full_access_detected,
        "expires_at": expires_at,
        "expired": expired,
        "reason": str(raw.get("reason") or ""),
        "updated_at": str(raw.get("updated_at") or ""),
        "path": str(root / MODE_FILE_RELATIVE_PATH),
    }


def set_runtime_mode(workspace_root: Path | str, mode: str, *, reason: str = "", ttl_hours: int = BUILD_TTL_HOURS) -> dict[str, Any]:
    normalized = mode.strip().lower()
    if normalized not in VALID_MODES:
        raise ValueError(f"Unsupported TradingCodex mode: {mode}. Expected one of: {', '.join(sorted(VALID_MODES))}")
    root = Path(workspace_root).expanduser().resolve()
    now = _now()
    expires_at = ""
    if normalized == "build":
        expires_at = (now + timedelta(hours=ttl_hours)).isoformat().replace("+00:00", "Z")
    payload = {
        "mode": normalized,
        "reason": reason.strip(),
        "updated_at": now.isoformat().replace("+00:00", "Z"),
        "expires_at": expires_at,
        "ttl_hours": ttl_hours if normalized == "build" else 0,
    }
    path = root / MODE_FILE_RELATIVE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return get_runtime_mode_status(root)


def reset_runtime_mode(workspace_root: Path | str) -> dict[str, Any]:
    return set_runtime_mode(workspace_root, DEFAULT_MODE)


def _read_mode_file(root: Path) -> dict[str, Any]:
    path = root / MODE_FILE_RELATIVE_PATH
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _expired(value: str, checked_at: datetime) -> bool:
    parsed = _parse_time(value)
    if parsed is None:
        return True
    return parsed <= checked_at


def _parse_time(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None


def _now() -> datetime:
    return datetime.now(timezone.utc)

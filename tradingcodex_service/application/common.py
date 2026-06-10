from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def stable_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def sanitize_id(value: Any) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", str(value)).strip("-") or "unknown"


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def append_jsonl(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(value, ensure_ascii=False, default=str) + "\n")

def _status_class(value: Any) -> str:
    text = str(value).lower()
    if text in {"ok", "allow", "accepted", "approved", "enabled", "filled", "valid", "read", "true", "open"}:
        return "good"
    if text in {"deny", "denied", "rejected", "error", "blocked", "disabled", "false", "execution"}:
        return "bad"
    if text in {"proposed", "pending", "recorded", "stubbed", "write", "approval", "research-only"}:
        return "warn"
    return "neutral"


def _resolve_path(root: Path, raw: str) -> Path:
    path = Path(raw)
    return path if path.is_absolute() else root / path


def _safe_read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return ""


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except Exception:
        return None


def _validate_positive(value: Any, field: str, reasons: list[str]) -> None:
    if value in (None, ""):
        return
    number = _number(value)
    if number is None or number <= 0:
        reasons.append(f"{field} must be a positive number")


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _unique(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))

from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from tradingcodex_service.application.common import (
    exclusive_file_lock,
    safe_filename_component,
    safe_workspace_path,
    sanitize_id,
)


SHA256_PATTERN = r"[0-9a-f]{64}"


def canonical_json_bytes(value: Any) -> bytes:
    """Return the strict, portable JSON representation used by research objects."""

    try:
        text = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("research object contains a non-JSON or non-finite value") from exc
    return text.encode("utf-8")


def content_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def legacy_content_hash(value: Any) -> str:
    """Preserve the v1 research hash encoding for migration-free validation.

    SourceSnapshot, ResearchSpec, ReplayManifest, and ExperimentRun shipped
    before strict canonical JSON.  They now share the same object primitives,
    while this compatibility encoder keeps their existing IDs and hashes valid.
    New object types must use :func:`content_hash` instead.
    """

    return hashlib.sha256(
        json.dumps(value, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


def derive_content_id(prefix: str, value: Any, *, digest_length: int = 24) -> str:
    safe_prefix = safe_filename_component(prefix, max_length=48).lower()
    return f"{safe_prefix}-{content_hash(value)[:digest_length]}"


def normalize_timestamp(value: Any, field: str, *, required: bool = True) -> str:
    text = str(value or "").strip()
    if not text:
        if required:
            raise ValueError(f"{field} is required")
        return ""
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO-8601 datetime") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def validate_timestamp_order(values: Iterable[tuple[str, str]]) -> None:
    normalized = [(field, normalize_timestamp(value, field)) for field, value in values]
    for (left_field, left), (right_field, right) in zip(normalized, normalized[1:]):
        if timestamp_value(left) > timestamp_value(right):
            raise ValueError(f"times must satisfy {left_field} <= {right_field}")


def timestamp_value(value: Any) -> datetime:
    normalized = normalize_timestamp(value, "timestamp")
    return datetime.fromisoformat(normalized.replace("Z", "+00:00"))


def read_regular_json(path: Path, *, label: str) -> dict[str, Any]:
    try:
        metadata = path.lstat()
    except FileNotFoundError as exc:
        raise ValueError(f"{label} not found") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        raise ValueError(f"{label} must be a single-link regular non-symlink file")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} must contain valid JSON") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    canonical_json_bytes(value)
    return value


def atomic_write_bytes(path: Path, payload: bytes, *, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        if os.name != "nt":
            os.fchmod(fd, mode)
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def write_immutable_json(path: Path, value: dict[str, Any]) -> bool:
    """Create an immutable JSON record, or confirm an identical existing record."""

    rendered = json.dumps(
        value,
        indent=2,
        ensure_ascii=False,
        sort_keys=True,
        allow_nan=False,
    ).encode("utf-8") + b"\n"
    if path.exists() or path.is_symlink():
        existing = read_regular_json(path, label=path.name)
        if canonical_json_bytes(existing) != canonical_json_bytes(value):
            raise ValueError(f"immutable research object collision: {path.name}")
        return False
    atomic_write_bytes(path, rendered)
    return True


def research_object_path(
    workspace_root: Path,
    object_root: Path,
    object_id: Any,
) -> Path:
    """Return one traversal-safe canonical JSON object path."""

    return safe_workspace_path(
        workspace_root,
        object_root / f"{sanitize_id(object_id)}.json",
        allowed_roots=(object_root,),
    )


def verify_hashed_json(
    path: Path,
    *,
    hash_field: str,
    label: str,
    hash_function: Callable[[Any], str] = content_hash,
    schema_version: int | None = None,
) -> dict[str, Any]:
    """Read a regular JSON object and verify its service-derived digest."""

    artifact = read_regular_json(path, label=label)
    if schema_version is not None and artifact.get("schema_version") != schema_version:
        raise ValueError(f"{label} uses an unsupported schema")
    expected = str(artifact.get(hash_field) or "").strip().lower()
    if len(expected) != 64 or any(character not in "0123456789abcdef" for character in expected):
        raise ValueError(f"{label} {hash_field} must be a lowercase SHA-256 hash")
    payload = dict(artifact)
    payload.pop(hash_field, None)
    if hash_function(payload) != expected:
        raise ValueError(f"{label} hash mismatch: {path.stem}")
    return artifact


def store_immutable_hashed_json(
    path: Path,
    artifact: dict[str, Any],
    *,
    hash_field: str,
    label: str,
    object_id: str,
    hash_function: Callable[[Any], str] = content_hash,
    ignored_semantic_fields: Iterable[str] = ("system_recorded_at",),
) -> tuple[dict[str, Any], str]:
    """Atomically create a hash-verified object or confirm idempotency."""

    with exclusive_file_lock(path):
        if path.exists() or path.is_symlink():
            existing = verify_hashed_json(
                path,
                hash_field=hash_field,
                label=label,
                hash_function=hash_function,
            )
            if existing.get(hash_field) == artifact.get(hash_field):
                return existing, "existing"
            ignored = {hash_field, *ignored_semantic_fields}
            existing_semantics = {
                key: value for key, value in existing.items() if key not in ignored
            }
            requested_semantics = {
                key: value for key, value in artifact.items() if key not in ignored
            }
            if hash_function(existing_semantics) == hash_function(requested_semantics):
                return existing, "existing"
            raise ValueError(f"{label} is immutable and already exists: {object_id}")
        write_immutable_json(path, artifact)
    return artifact, "recorded"

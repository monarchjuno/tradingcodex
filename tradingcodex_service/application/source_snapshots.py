from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from tradingcodex_service.application.common import (
    safe_filename_component,
    sanitize_id,
)
from tradingcodex_service.application.research_objects import legacy_content_hash

SOURCE_SNAPSHOT_SCHEMA_VERSION = 1
SOURCE_SNAPSHOT_FIELDS = frozenset({
    "schema_version",
    "snapshot_id",
    "provider",
    "source_category",
    "source_locator",
    "provider_query",
    "as_of",
    "observed_at",
    "effective_at",
    "published_at",
    "retrieved_at",
    "known_at",
    "revision",
    "vintage",
    "timezone",
    "schema_hash",
    "corporate_action_policy",
    "price_adjustment_policy",
    "universe_membership",
    "delisting_policy",
    "coverage_note",
    "artifact_id",
    "warnings",
    "payload",
    "payload_hash",
    "created_by",
    "recorded_at",
    "system_recorded_at",
    "workspace_native",
    "snapshot_hash",
})
SOURCE_SNAPSHOT_STRING_FIELDS = SOURCE_SNAPSHOT_FIELDS - {
    "schema_version",
    "provider_query",
    "universe_membership",
    "warnings",
    "payload",
    "workspace_native",
}
SOURCE_SNAPSHOT_TIME_FIELDS = (
    "known_at",
    "retrieved_at",
    "recorded_at",
    "system_recorded_at",
)


def validate_source_snapshot(
    snapshot: Any,
    *,
    expected_snapshot_id: str | None = None,
) -> dict[str, Any]:
    """Validate the complete immutable source-snapshot v1 envelope."""

    label = f"source snapshot {expected_snapshot_id}" if expected_snapshot_id else "source snapshot"
    if not isinstance(snapshot, dict):
        raise ValueError(f"{label} must be an object")
    actual_fields = set(snapshot)
    if actual_fields != SOURCE_SNAPSHOT_FIELDS:
        details = []
        missing = sorted(SOURCE_SNAPSHOT_FIELDS - actual_fields)
        unknown = sorted(actual_fields - SOURCE_SNAPSHOT_FIELDS)
        if missing:
            details.append("missing: " + ", ".join(missing))
        if unknown:
            details.append("unknown: " + ", ".join(unknown))
        raise ValueError(f"{label} fields do not match v1 schema ({'; '.join(details)})")
    if (
        type(snapshot.get("schema_version")) is not int
        or snapshot["schema_version"] != SOURCE_SNAPSHOT_SCHEMA_VERSION
    ):
        raise ValueError(f"{label} schema_version must be {SOURCE_SNAPSHOT_SCHEMA_VERSION}")
    for field in SOURCE_SNAPSHOT_STRING_FIELDS:
        if not isinstance(snapshot[field], str):
            raise ValueError(f"{label} {field} must be a string")
    for field in ("provider_query", "universe_membership", "payload"):
        if not isinstance(snapshot[field], dict):
            raise ValueError(f"{label} {field} must be an object")
    if not isinstance(snapshot["warnings"], list) or not all(
        isinstance(item, str) for item in snapshot["warnings"]
    ):
        raise ValueError(f"{label} warnings must be an array of strings")
    if snapshot["workspace_native"] is not True:
        raise ValueError(f"{label} workspace_native must be true")
    for field in ("schema_hash", "payload_hash", "snapshot_hash"):
        if re.fullmatch(r"[0-9a-f]{64}", snapshot[field]) is None:
            raise ValueError(f"{label} {field} must be a lowercase SHA-256 hash")

    snapshot_id = snapshot["snapshot_id"].strip()
    if not snapshot_id:
        raise ValueError(f"{label} snapshot_id is required")
    if snapshot_id != sanitize_id(snapshot_id):
        raise ValueError(f"{label} snapshot_id is invalid")
    if expected_snapshot_id is not None and snapshot_id != expected_snapshot_id:
        raise ValueError(f"source snapshot id mismatch: {expected_snapshot_id}")
    derived_snapshot_id = source_snapshot_id(snapshot)
    if snapshot_id != derived_snapshot_id:
        raise ValueError(
            f"{label} snapshot_id does not match its content: expected {derived_snapshot_id}"
        )

    payload = snapshot["payload"]
    payload_hash = snapshot["payload_hash"]
    if payload_hash != legacy_content_hash(payload):
        raise ValueError(f"{label} payload hash mismatch")

    snapshot_hash = snapshot["snapshot_hash"]
    snapshot_seed = {
        key: value
        for key, value in snapshot.items()
        if key not in {"snapshot_id", "snapshot_hash"}
    }
    if snapshot_hash != legacy_content_hash(snapshot_seed):
        raise ValueError(f"{label} hash mismatch")

    timestamps = {
        field: _timestamp(snapshot.get(field), f"{label} {field}")
        for field in SOURCE_SNAPSHOT_TIME_FIELDS
    }
    if not (
        timestamps["known_at"]
        <= timestamps["retrieved_at"]
        <= timestamps["recorded_at"]
        <= timestamps["system_recorded_at"]
    ):
        raise ValueError(
            f"{label} times must satisfy "
            "known_at <= retrieved_at <= recorded_at <= system_recorded_at"
        )
    return snapshot


def source_snapshot_id(snapshot: dict[str, Any]) -> str:
    base = "-".join(
        filter(
            None,
            (
                sanitize_id(str(snapshot.get("provider") or "unknown")),
                sanitize_id(str(snapshot.get("source_category") or "unknown")),
                sanitize_id(str(snapshot.get("artifact_id") or "")),
            ),
        )
    )
    # The identifier is content-addressed but is not recursively part of its
    # own digest. All other envelope fields, including snapshot_hash, are bound.
    digest = legacy_content_hash(
        {key: value for key, value in snapshot.items() if key != "snapshot_id"}
    )[:12]
    prefix = safe_filename_component(base or "source-snapshot", max_length=115)
    return f"{prefix}-{digest}"


def _timestamp(value: Any, field: str) -> datetime:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field} is required")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO-8601 datetime") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc)

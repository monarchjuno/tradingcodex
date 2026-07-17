from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
import tempfile
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from tradingcodex_service.application.common import (
    exclusive_file_lock,
    file_hash,
    now_iso,
    safe_workspace_path,
    sanitize_id,
)
from tradingcodex_service.application.research_objects import (
    SHA256_PATTERN,
    canonical_json_bytes,
    content_hash,
    derive_content_id,
    normalize_timestamp,
    read_regular_json,
    timestamp_value,
    write_immutable_json,
)
from tradingcodex_service.application.runtime import workspace_context_payload
from tradingcodex_service.application.source_snapshots import validate_source_snapshot


DATASET_SCHEMA_VERSION = 1
DATASET_MANIFEST_ROOT = Path("trading/research/datasets/manifests")
DATASET_OBJECT_ROOT = Path("trading/research/datasets/objects")
DATASET_WITHDRAWAL_ROOT = Path("trading/research/datasets/withdrawals")
DATASET_LOCK = Path("trading/research/.index/datasets")
SOURCE_SNAPSHOT_ROOT = Path("trading/research/source-snapshots")
MAX_SOURCE_BYTES = 256 * 1024 * 1024
MAX_MATERIALIZED_BYTES = 256 * 1024 * 1024
MAX_MATERIALIZED_ROWS = 1_000_000
MAX_PROFILE_ROWS = 20
MAX_PROFILE_CELL_TEXT = 512

_MANIFEST_FIELDS = frozenset(
    {
        "schema_version",
        "artifact_type",
        "dataset_id",
        "title",
        "description",
        "tags",
        "payload",
        "source_snapshot_ids",
        "source_snapshot_hashes",
        "provider",
        "provider_query",
        "as_of",
        "vintage",
        "period_start",
        "period_end",
        "observed_at",
        "published_at",
        "known_at",
        "knowledge_cutoff",
        "recorded_at",
        "system_recorded_at",
        "timezone",
        "frequency",
        "instrument_ids",
        "symbols",
        "universe_membership_policy",
        "universe_membership",
        "columns",
        "adjustment_policy",
        "corporate_action_policy",
        "delisting_policy",
        "quality",
        "lineage",
        "license",
        "data_classification",
        "created_by",
        "workspace_native",
        "manifest_hash",
    }
)
_COLUMN_FIELDS = frozenset({"name", "type", "nullable", "unit", "currency", "description"})
_PAYLOAD_FIELDS = frozenset(
    {"sha256", "path", "format", "size_bytes", "row_count", "pyarrow_version", "compression"}
)
_QUALITY_FIELDS = frozenset({"null_counts", "duplicate_count", "warnings"})
_LINEAGE_FIELDS = frozenset({"parent_dataset_ids", "transformation_code_hash"})
_LICENSE_FIELDS = frozenset({"retention_policy", "redistribution", "notes"})
_WITHDRAWAL_FIELDS = frozenset(
    {
        "schema_version",
        "artifact_type",
        "withdrawal_id",
        "dataset_id",
        "reason_code",
        "reason",
        "requested_by",
        "recorded_at",
        "payload_removed",
        "workspace_native",
        "withdrawal_hash",
    }
)
_TYPE_PATTERN = re.compile(
    r"^(?:string|bool|int64|float64|date32|timestamp|decimal128\((\d{1,2}),(\d{1,2})\))$"
)


def record_dataset_snapshot(
    workspace_root: Path | str,
    args: dict[str, Any],
    *,
    scratch_root: Path | str | None = None,
) -> dict[str, Any]:
    """Normalize one scratch-local table into an immutable Parquet dataset."""

    root = Path(workspace_root).expanduser().resolve()
    if "dataset_id" in args or "manifest_hash" in args:
        raise ValueError("dataset_id and manifest_hash are derived by the service")
    if "created_by" in args:
        raise ValueError("created_by is derived from principal_id")
    classification = _required_choice(
        args,
        "data_classification",
        {"public", "licensed_research", "user_provided"},
        default="public",
    )
    source_name = _basename(args.get("source_filename"), "source_filename")
    source = _scratch_file(source_name, scratch_root=scratch_root, max_bytes=MAX_SOURCE_BYTES)
    columns = _column_contract(args.get("columns"))
    source_format = source.suffix.lower().lstrip(".")
    if source_format not in {"csv", "jsonl", "parquet"}:
        raise ValueError("source_filename must end in .csv, .jsonl, or .parquet")

    pa, pc, csv_module, json_module, pq = _pyarrow()
    schema = pa.schema([pa.field(item["name"], _arrow_type(pa, item["type"]), nullable=item["nullable"]) for item in columns])
    table = _read_source_table(
        source,
        source_format=source_format,
        schema=schema,
        max_bytes=MAX_SOURCE_BYTES,
        pa=pa,
        csv_module=csv_module,
        json_module=json_module,
        pq=pq,
    )
    table = _normalize_table(table, schema=schema, pc=pc)
    if table.num_rows < 1:
        raise ValueError("dataset source must contain at least one row")
    quality = _quality_profile(table, columns=columns, pc=pc)

    snapshot_ids, snapshot_hashes, snapshot_known_at = _source_snapshot_bindings(
        root, args.get("source_snapshot_ids")
    )
    known_at = normalize_timestamp(args.get("known_at") or snapshot_known_at or now_iso(), "known_at")
    knowledge_cutoff = normalize_timestamp(args.get("knowledge_cutoff"), "knowledge_cutoff")
    if snapshot_known_at and timestamp_value(snapshot_known_at) > timestamp_value(known_at):
        raise ValueError("dataset known_at must not be before source snapshot known_at")
    if timestamp_value(known_at) > timestamp_value(knowledge_cutoff):
        raise ValueError("dataset known_at must not be after knowledge_cutoff")
    system_recorded_at = normalize_timestamp(now_iso(), "system_recorded_at")
    if timestamp_value(knowledge_cutoff) > timestamp_value(system_recorded_at):
        raise ValueError("knowledge_cutoff must not be after system_recorded_at")

    title = _required_text(args, "title")
    description = str(args.get("description") or "").strip()
    tags = _string_list(args.get("tags"), "tags")
    provider = _required_text(args, "provider")
    provider_query = _json_object(args.get("provider_query"), "provider_query")
    as_of = normalize_timestamp(args.get("as_of"), "as_of")
    vintage = _required_text(args, "vintage")
    period_start = normalize_timestamp(args.get("period_start"), "period_start")
    period_end = normalize_timestamp(args.get("period_end"), "period_end")
    if timestamp_value(period_start) > timestamp_value(period_end):
        raise ValueError("period_start must not be after period_end")
    if timestamp_value(period_end) > timestamp_value(as_of):
        raise ValueError("period_end must not be after as_of")
    if timestamp_value(as_of) > timestamp_value(knowledge_cutoff):
        raise ValueError("as_of must not be after knowledge_cutoff")
    observed_at = _optional_timestamp(args.get("observed_at"), "observed_at")
    published_at = _optional_timestamp(args.get("published_at"), "published_at")
    instrument_ids = _string_list(args.get("instrument_ids"), "instrument_ids")
    symbols = _string_list(args.get("symbols"), "symbols")
    if not instrument_ids and not symbols:
        raise ValueError("dataset requires at least one instrument_id or symbol")
    timezone_name = _timezone(args.get("timezone"))
    frequency = _required_text(args, "frequency")
    universe_membership_policy = _required_text(args, "universe_membership_policy")
    universe_membership = _json_object(args.get("universe_membership"), "universe_membership")
    parent_dataset_ids = _validated_parent_datasets(
        root,
        args.get("parent_dataset_ids"),
        child_known_at=known_at,
        child_knowledge_cutoff=knowledge_cutoff,
    )
    if not snapshot_ids and not parent_dataset_ids:
        raise ValueError("dataset requires source_snapshot_ids or parent_dataset_ids lineage")
    transformation_code_hash = _optional_hash(args.get("transformation_code_hash"), "transformation_code_hash")
    retention_policy = _required_choice(
        args,
        "retention_policy",
        {"permanent_local", "locator_only", "time_limited"},
        default="permanent_local",
    )

    object_root = safe_workspace_path(
        root,
        DATASET_OBJECT_ROOT,
        allowed_roots=(Path("trading/research"),),
    )
    object_root.mkdir(parents=True, exist_ok=True)
    temporary_path, payload_hash, payload_size = _write_canonical_parquet(
        table,
        object_root=object_root,
        pq=pq,
    )
    payload_relative = DATASET_OBJECT_ROOT / f"{payload_hash}.parquet"
    payload_path = safe_workspace_path(
        root,
        payload_relative,
        allowed_roots=(Path("trading/research"),),
    )
    payload = {
        "sha256": payload_hash,
        "path": payload_relative.as_posix(),
        "format": "parquet",
        "size_bytes": payload_size,
        "row_count": table.num_rows,
        "pyarrow_version": str(pa.__version__),
        "compression": "zstd",
    }
    semantic = {
        "schema_version": DATASET_SCHEMA_VERSION,
        "artifact_type": "dataset_manifest",
        "title": title,
        "description": description,
        "tags": tags,
        "payload": payload,
        "source_snapshot_ids": snapshot_ids,
        "source_snapshot_hashes": snapshot_hashes,
        "provider": provider,
        "provider_query": provider_query,
        "as_of": as_of,
        "vintage": vintage,
        "period_start": period_start,
        "period_end": period_end,
        "observed_at": observed_at,
        "published_at": published_at,
        "known_at": known_at,
        "knowledge_cutoff": knowledge_cutoff,
        "timezone": timezone_name,
        "frequency": frequency,
        "instrument_ids": instrument_ids,
        "symbols": symbols,
        "universe_membership_policy": universe_membership_policy,
        "universe_membership": universe_membership,
        "columns": columns,
        "adjustment_policy": str(args.get("adjustment_policy") or "not_specified").strip(),
        "corporate_action_policy": str(args.get("corporate_action_policy") or "not_specified").strip(),
        "delisting_policy": str(args.get("delisting_policy") or "not_specified").strip(),
        "quality": quality,
        "lineage": {
            "parent_dataset_ids": parent_dataset_ids,
            "transformation_code_hash": transformation_code_hash,
        },
        "license": {
            "retention_policy": retention_policy,
            "redistribution": str(args.get("redistribution") or "not_specified").strip(),
            "notes": str(args.get("license_notes") or "").strip(),
        },
        "data_classification": classification,
    }
    dataset_id = derive_content_id("dataset", semantic)
    manifest_path = _manifest_path(root, dataset_id)
    manifest = {
        **semantic,
        "dataset_id": dataset_id,
        "recorded_at": system_recorded_at,
        "system_recorded_at": system_recorded_at,
        "created_by": str(args.get("principal_id") or "system"),
        "workspace_native": True,
    }
    manifest["manifest_hash"] = content_hash(manifest)
    try:
        validate_dataset_manifest(manifest, expected_dataset_id=dataset_id)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise
    with exclusive_file_lock(root / DATASET_LOCK):
        if manifest_path.exists() or manifest_path.is_symlink():
            temporary_path.unlink(missing_ok=True)
            existing = validate_dataset_manifest(
                read_regular_json(manifest_path, label=f"dataset manifest {dataset_id}"),
                expected_dataset_id=dataset_id,
            )
            validate_dataset_lineage(root, existing)
            if _dataset_semantic(existing) != semantic:
                raise ValueError(f"dataset id collision: {dataset_id}")
            return _record_result(root, existing, status="existing")
        if payload_path.exists() or payload_path.is_symlink():
            payload_stat = payload_path.lstat()
            if (
                payload_path.is_symlink()
                or not stat.S_ISREG(payload_stat.st_mode)
                or payload_stat.st_nlink != 1
                or file_hash(payload_path) != payload_hash
            ):
                temporary_path.unlink(missing_ok=True)
                raise ValueError("dataset payload object collision")
            temporary_path.unlink(missing_ok=True)
        else:
            os.replace(temporary_path, payload_path)
        write_immutable_json(manifest_path, manifest)
    return _record_result(root, manifest, status="recorded")


def get_dataset_manifest(workspace_root: Path | str, args: dict[str, Any]) -> dict[str, Any]:
    root = Path(workspace_root).expanduser().resolve()
    dataset_id = _dataset_id(args.get("dataset_id"))
    manifest = validate_dataset_manifest(
        read_regular_json(_manifest_path(root, dataset_id), label=f"dataset manifest {dataset_id}"),
        expected_dataset_id=dataset_id,
    )
    validate_dataset_lineage(root, manifest)
    payload_path = _payload_path(root, manifest)
    withdrawn = dataset_id in _withdrawn_dataset_ids(root)
    return {
        "dataset": manifest,
        "payload_available": _payload_available(payload_path, manifest["payload"]["sha256"]),
        "withdrawn": withdrawn,
        "file_sot": True,
        "workspace_native": True,
        "workspace_context": workspace_context_payload(root),
    }


def search_datasets(workspace_root: Path | str, args: dict[str, Any] | None = None) -> dict[str, Any]:
    from tradingcodex_service.application.research_object_catalog import search_research_objects

    args = args or {}
    catalog_args = {
        "query": str(args.get("query") or "").strip(),
        "object_type": "dataset_manifest",
        "symbol": str(args.get("symbol") or "").strip(),
        "instrument_id": str(args.get("instrument_id") or "").strip(),
        "provider": str(args.get("provider") or "").strip(),
        "frequency": str(args.get("frequency") or "").strip(),
        "column": str(args.get("column") or "").strip(),
        "period_start": str(args.get("period_start") or "").strip(),
        "period_end": str(args.get("period_end") or "").strip(),
        "knowledge_cutoff": str(args.get("knowledge_cutoff") or "").strip(),
        "exclude_withdrawn_datasets": not bool(args.get("include_withdrawn")),
        "limit": max(1, min(int(args.get("limit") or 20), 200)),
    }
    result = search_research_objects(workspace_root, catalog_args)
    cards = result["objects"]
    return {
        "query": catalog_args["query"],
        "datasets": cards,
        "count": len(cards),
        "index_path": result["index_path"],
        "fts_enabled": result["fts_enabled"],
        "file_sot": True,
        "workspace_native": True,
        "workspace_context": result["workspace_context"],
    }


def profile_dataset(workspace_root: Path | str, args: dict[str, Any]) -> dict[str, Any]:
    root = Path(workspace_root).expanduser().resolve()
    dataset_id = _dataset_id(args.get("dataset_id"))
    if dataset_id in _withdrawn_dataset_ids(root):
        raise ValueError("withdrawn datasets cannot be profiled")
    manifest = _load_manifest(root, dataset_id, require_payload=True)
    pa, pc, _csv, _json, pq = _pyarrow()
    requested = _selected_columns(args.get("columns"), manifest)
    table = pq.read_table(_payload_path(root, manifest), columns=requested)
    sample_size = max(0, min(int(args.get("sample_rows") or MAX_PROFILE_ROWS), MAX_PROFILE_ROWS))
    statistics = []
    for name in requested:
        column = table.column(name).combine_chunks()
        stats: dict[str, Any] = {
            "name": name,
            "type": str(column.type),
            "count": len(column),
            "null_count": column.null_count,
        }
        try:
            extrema = pc.min_max(column).as_py()
        except (TypeError, pa.ArrowInvalid, pa.ArrowNotImplementedError):
            extrema = None
        if isinstance(extrema, dict):
            stats["min"] = _json_value(extrema.get("min"))
            stats["max"] = _json_value(extrema.get("max"))
        statistics.append(stats)
    return {
        "dataset_id": dataset_id,
        "row_count": table.num_rows,
        "columns": statistics,
        "sample": [_json_object_row(row) for row in table.slice(0, sample_size).to_pylist()],
        "quality": manifest["quality"],
        "payload_hash": manifest["payload"]["sha256"],
        "file_sot": True,
        "workspace_native": True,
        "workspace_context": workspace_context_payload(root),
    }


def materialize_dataset_slice(
    workspace_root: Path | str,
    args: dict[str, Any],
    *,
    scratch_root: Path | str | None = None,
) -> dict[str, Any]:
    root = Path(workspace_root).expanduser().resolve()
    dataset_id = _dataset_id(args.get("dataset_id"))
    if dataset_id in _withdrawn_dataset_ids(root):
        raise ValueError("withdrawn datasets cannot be materialized")
    manifest = _load_manifest(root, dataset_id, require_payload=True)
    requested = _selected_columns(args.get("columns"), manifest)
    time_column = str(args.get("time_column") or "").strip()
    start = _optional_timestamp(args.get("start"), "start")
    end = _optional_timestamp(args.get("end"), "end")
    if (start or end) and not time_column:
        raise ValueError("time_column is required when start or end is supplied")
    instruments = _string_list(args.get("instrument_ids"), "instrument_ids")
    symbols = _string_list(args.get("symbols"), "symbols")
    filter_columns = [name for name in (time_column,) if name]
    if instruments:
        filter_columns.append("instrument_id")
    if symbols:
        filter_columns.append("symbol")
    read_columns = list(dict.fromkeys([*requested, *filter_columns]))
    manifest_names = {item["name"] for item in manifest["columns"]}
    unknown_filters = [name for name in filter_columns if name not in manifest_names]
    if unknown_filters:
        raise ValueError(f"filter column not present in dataset: {unknown_filters[0]}")
    if time_column:
        time_type = next(item["type"] for item in manifest["columns"] if item["name"] == time_column)
        if time_type != "timestamp":
            raise ValueError("time_column must use the timestamp dataset type")
    pa, pc, _csv, _json, pq = _pyarrow()
    table = pq.read_table(_payload_path(root, manifest), columns=read_columns)
    mask = None
    if start:
        mask = pc.greater_equal(table[time_column], pa.scalar(datetime.fromisoformat(start.replace("Z", "+00:00")), type=table[time_column].type))
    if end:
        current = pc.less_equal(table[time_column], pa.scalar(datetime.fromisoformat(end.replace("Z", "+00:00")), type=table[time_column].type))
        mask = current if mask is None else pc.and_(mask, current)
    if instruments:
        current = pc.is_in(table["instrument_id"], value_set=pa.array(instruments, type=table["instrument_id"].type))
        mask = current if mask is None else pc.and_(mask, current)
    if symbols:
        current = pc.is_in(table["symbol"], value_set=pa.array(symbols, type=table["symbol"].type))
        mask = current if mask is None else pc.and_(mask, current)
    if mask is not None:
        table = table.filter(mask)
    table = table.select(requested)
    max_rows = max(1, min(int(args.get("max_rows") or MAX_MATERIALIZED_ROWS), MAX_MATERIALIZED_ROWS))
    max_bytes = max(1, min(int(args.get("max_bytes") or MAX_MATERIALIZED_BYTES), MAX_MATERIALIZED_BYTES))
    if table.num_rows > max_rows:
        raise ValueError(f"dataset slice exceeds max_rows={max_rows}; narrow the selector")
    if table.nbytes > max_bytes:
        raise ValueError(f"dataset slice exceeds max_bytes={max_bytes}; narrow columns or range")
    selector = {
        "columns": requested,
        "time_column": time_column,
        "start": start,
        "end": end,
        "instrument_ids": instruments,
        "symbols": symbols,
    }
    materialization_id = derive_content_id(
        "materialization",
        {"dataset_id": dataset_id, "payload_hash": manifest["payload"]["sha256"], "selector": selector},
    )
    filename = f"{materialization_id}.parquet"
    destination = _scratch_destination(filename, scratch_root=scratch_root)
    temporary, digest, size = _write_canonical_parquet(table, object_root=destination.parent, pq=pq)
    if size > max_bytes:
        temporary.unlink(missing_ok=True)
        raise ValueError(f"dataset slice exceeds max_bytes={max_bytes}; narrow columns or range")
    if destination.exists() or destination.is_symlink():
        destination_stat = destination.lstat()
        if (
            destination.is_symlink()
            or not stat.S_ISREG(destination_stat.st_mode)
            or destination_stat.st_nlink != 1
            or file_hash(destination) != digest
        ):
            temporary.unlink(missing_ok=True)
            raise ValueError("materialization filename collision")
        temporary.unlink(missing_ok=True)
    else:
        os.replace(temporary, destination)
    sidecar_filename = f"{materialization_id}.materialization.json"
    sidecar_path = _scratch_destination(sidecar_filename, scratch_root=scratch_root)
    proof = {
        "schema_version": 1,
        "artifact_type": "dataset_materialization",
        "materialization_id": materialization_id,
        "dataset_id": dataset_id,
        "manifest_hash": manifest["manifest_hash"],
        "payload_hash": manifest["payload"]["sha256"],
        "selector": selector,
        "filename": filename,
        "row_count": table.num_rows,
        "size_bytes": size,
        "content_hash": digest,
        "signature_algorithm": "hmac-sha256",
    }
    # The signing key lives in the protected TradingCodex state directory,
    # outside agent-writable workspace and scratch roots. A self-hash would
    # detect accidental damage but would not authenticate a materialization.
    from tradingcodex_service.application.artifact_bindings import _receipt_signature

    proof["proof_hash"] = _receipt_signature(
        root,
        proof,
        create_signing_key=True,
    )
    write_immutable_json(sidecar_path, proof)
    return {
        "materialization_id": materialization_id,
        "dataset_id": dataset_id,
        "filename": filename,
        "sidecar_filename": sidecar_filename,
        "manifest_hash": manifest["manifest_hash"],
        "payload_hash": manifest["payload"]["sha256"],
        "selector": selector,
        "row_count": table.num_rows,
        "size_bytes": size,
        "content_hash": digest,
        "format": "parquet",
        "temporary": True,
        "workspace_context": workspace_context_payload(root),
    }


def withdraw_dataset_snapshot(workspace_root: Path | str, args: dict[str, Any]) -> dict[str, Any]:
    """Record a user-confirmed withdrawal and purge only an unshared payload."""

    root = Path(workspace_root).expanduser().resolve()
    if args.get("confirmed_by_user") is not True:
        raise ValueError("dataset withdrawal requires confirmed_by_user=true")
    dataset_id = _dataset_id(args.get("dataset_id"))
    reason_code = _required_choice(args, "reason_code", {"license", "legal"}, default="")
    reason = _required_text(args, "reason")
    manifest = _load_manifest(root, dataset_id, require_payload=False)
    existing = _withdrawal_for_dataset(root, dataset_id)
    if existing:
        return {
            "status": "already_withdrawn",
            "withdrawal_id": existing["withdrawal_id"],
            "dataset_id": dataset_id,
            "payload_removed": existing["payload_removed"],
            "workspace_context": workspace_context_payload(root),
        }
    recorded_at = normalize_timestamp(now_iso(), "recorded_at")
    seed = {
        "schema_version": 1,
        "artifact_type": "dataset_withdrawal",
        "dataset_id": dataset_id,
        "reason_code": reason_code,
        "reason": reason,
        "requested_by": str(args.get("principal_id") or "system"),
        "recorded_at": recorded_at,
    }
    withdrawal_id = derive_content_id("dataset-withdrawal", seed)
    payload_path = _payload_path(root, manifest)
    with exclusive_file_lock(root / DATASET_LOCK):
        shared = _payload_reference_count(root, manifest["payload"]["sha256"], excluding=dataset_id) > 0
        payload_removed = False
        if not shared and (payload_path.exists() or payload_path.is_symlink()):
            if payload_path.is_symlink() or not payload_path.is_file():
                raise ValueError("dataset payload must be a regular non-symlink file")
            if file_hash(payload_path) != manifest["payload"]["sha256"]:
                raise ValueError("dataset payload hash mismatch")
            payload_path.unlink()
            payload_removed = True
        event = {
            **seed,
            "withdrawal_id": withdrawal_id,
            "payload_removed": payload_removed,
            "workspace_native": True,
        }
        event["withdrawal_hash"] = content_hash(event)
        _validate_withdrawal(event)
        withdrawal_path = safe_workspace_path(
            root,
            DATASET_WITHDRAWAL_ROOT / f"{withdrawal_id}.json",
            allowed_roots=(Path("trading/research"),),
        )
        write_immutable_json(withdrawal_path, event)
    return {
        "status": "withdrawn",
        "withdrawal_id": withdrawal_id,
        "dataset_id": dataset_id,
        "payload_removed": payload_removed,
        "workspace_context": workspace_context_payload(root),
    }


def validate_dataset_manifest(value: Any, *, expected_dataset_id: str | None = None) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("dataset manifest must be an object")
    _exact_fields(value, _MANIFEST_FIELDS, "dataset manifest")
    if value["schema_version"] != DATASET_SCHEMA_VERSION or type(value["schema_version"]) is not int:
        raise ValueError(f"dataset manifest schema_version must be {DATASET_SCHEMA_VERSION}")
    if value["artifact_type"] != "dataset_manifest":
        raise ValueError("dataset manifest artifact_type must be dataset_manifest")
    dataset_id = _dataset_id(value["dataset_id"])
    if expected_dataset_id is not None and dataset_id != expected_dataset_id:
        raise ValueError("dataset id mismatch")
    if dataset_id != derive_content_id("dataset", _dataset_semantic(value)):
        raise ValueError("dataset_id does not match manifest content")
    for field in (
        "title",
        "provider",
        "timezone",
        "frequency",
        "vintage",
        "universe_membership_policy",
        "created_by",
    ):
        if not isinstance(value[field], str) or not value[field].strip():
            raise ValueError(f"dataset manifest {field} is required")
    for field in (
        "description",
        "as_of",
        "period_start",
        "period_end",
        "observed_at",
        "published_at",
        "adjustment_policy",
        "corporate_action_policy",
        "delisting_policy",
    ):
        if not isinstance(value[field], str):
            raise ValueError(f"dataset manifest {field} must be a string")
    for field in ("tags", "source_snapshot_ids", "instrument_ids", "symbols"):
        _string_list(value[field], field)
    _json_object(value["provider_query"], "provider_query")
    _json_object(value["universe_membership"], "universe_membership")
    _timezone(value["timezone"])
    if not value["instrument_ids"] and not value["symbols"]:
        raise ValueError("dataset manifest requires at least one instrument_id or symbol")
    for column in value["columns"] if isinstance(value["columns"], list) else []:
        _exact_fields(column, _COLUMN_FIELDS, "dataset column")
    _column_contract(value["columns"])
    _exact_fields(value["payload"], _PAYLOAD_FIELDS, "dataset payload")
    _exact_fields(value["quality"], _QUALITY_FIELDS, "dataset quality")
    _exact_fields(value["lineage"], _LINEAGE_FIELDS, "dataset lineage")
    _exact_fields(value["license"], _LICENSE_FIELDS, "dataset license")
    if re.fullmatch(SHA256_PATTERN, str(value["payload"].get("sha256") or "")) is None:
        raise ValueError("dataset payload sha256 must be a lowercase SHA-256 hash")
    expected_path = (DATASET_OBJECT_ROOT / f"{value['payload']['sha256']}.parquet").as_posix()
    if value["payload"]["path"] != expected_path or value["payload"]["format"] != "parquet":
        raise ValueError("dataset payload path or format is invalid")
    for field in ("size_bytes", "row_count"):
        if type(value["payload"][field]) is not int or value["payload"][field] < 0:
            raise ValueError(f"dataset payload {field} must be a non-negative integer")
    if not isinstance(value["payload"]["pyarrow_version"], str) or not value["payload"]["pyarrow_version"].strip():
        raise ValueError("dataset payload pyarrow_version is required")
    if value["payload"]["compression"] != "zstd":
        raise ValueError("dataset payload compression must be zstd")
    if value["workspace_native"] is not True:
        raise ValueError("dataset manifest workspace_native must be true")
    for field in (
        "as_of",
        "period_start",
        "period_end",
        "known_at",
        "knowledge_cutoff",
        "recorded_at",
        "system_recorded_at",
    ):
        if normalize_timestamp(value[field], field) != value[field]:
            raise ValueError(f"dataset manifest {field} must be normalized to UTC")
    for field in ("observed_at", "published_at"):
        if value[field] and normalize_timestamp(value[field], field) != value[field]:
            raise ValueError(f"dataset manifest {field} must be normalized to UTC")
    if not (
        timestamp_value(value["period_start"])
        <= timestamp_value(value["period_end"])
        <= timestamp_value(value["as_of"])
        <= timestamp_value(value["knowledge_cutoff"])
    ):
        raise ValueError("dataset period must satisfy period_start <= period_end <= as_of <= knowledge_cutoff")
    if not (
        timestamp_value(value["known_at"])
        <= timestamp_value(value["knowledge_cutoff"])
        <= timestamp_value(value["recorded_at"])
        <= timestamp_value(value["system_recorded_at"])
    ):
        raise ValueError("dataset times must satisfy known_at <= knowledge_cutoff <= recorded_at <= system_recorded_at")
    if value["data_classification"] not in {"public", "licensed_research", "user_provided"}:
        raise ValueError("dataset data_classification is invalid")
    if value["license"]["retention_policy"] not in {"permanent_local", "locator_only", "time_limited"}:
        raise ValueError("dataset retention_policy is invalid")
    hashes = value["source_snapshot_hashes"]
    if not isinstance(hashes, dict) or set(hashes) != set(value["source_snapshot_ids"]):
        raise ValueError("source_snapshot_hashes must match source_snapshot_ids")
    if any(re.fullmatch(SHA256_PATTERN, str(item)) is None for item in hashes.values()):
        raise ValueError("source snapshot hashes must be lowercase SHA-256 values")
    parent_ids = _string_list(value["lineage"].get("parent_dataset_ids"), "parent_dataset_ids")
    for parent_id in parent_ids:
        _dataset_id(parent_id)
    _optional_hash(value["lineage"].get("transformation_code_hash"), "transformation_code_hash")
    if not value["source_snapshot_ids"] and not parent_ids:
        raise ValueError("dataset manifest requires source or parent lineage")
    null_counts = value["quality"].get("null_counts")
    if not isinstance(null_counts, dict) or set(null_counts) != {item["name"] for item in value["columns"]}:
        raise ValueError("dataset quality null_counts must match columns")
    if any(type(count) is not int or count < 0 for count in null_counts.values()):
        raise ValueError("dataset quality null counts must be non-negative integers")
    duplicate_count = value["quality"].get("duplicate_count")
    if duplicate_count is not None and (type(duplicate_count) is not int or duplicate_count < 0):
        raise ValueError("dataset duplicate_count must be a non-negative integer or null")
    _string_list(value["quality"].get("warnings"), "quality warnings")
    for field in ("redistribution", "notes"):
        if not isinstance(value["license"].get(field), str):
            raise ValueError(f"dataset license {field} must be a string")
    manifest_hash = str(value.get("manifest_hash") or "")
    if re.fullmatch(SHA256_PATTERN, manifest_hash) is None:
        raise ValueError("dataset manifest_hash must be a lowercase SHA-256 hash")
    if manifest_hash != content_hash({key: item for key, item in value.items() if key != "manifest_hash"}):
        raise ValueError("dataset manifest hash mismatch")
    return value


def _dataset_semantic(value: dict[str, Any]) -> dict[str, Any]:
    excluded = {"dataset_id", "recorded_at", "system_recorded_at", "created_by", "workspace_native", "manifest_hash"}
    return {key: item for key, item in value.items() if key not in excluded}


def _record_result(root: Path, manifest: dict[str, Any], *, status: str) -> dict[str, Any]:
    return {
        "status": status,
        "dataset_id": manifest["dataset_id"],
        "manifest_path": (DATASET_MANIFEST_ROOT / f"{manifest['dataset_id']}.json").as_posix(),
        "payload_hash": manifest["payload"]["sha256"],
        "row_count": manifest["payload"]["row_count"],
        "known_at": manifest["known_at"],
        "knowledge_cutoff": manifest["knowledge_cutoff"],
        "file_sot": True,
        "workspace_native": True,
        "workspace_context": workspace_context_payload(root),
    }


def _load_manifest(root: Path, dataset_id: str, *, require_payload: bool) -> dict[str, Any]:
    manifest = validate_dataset_manifest(
        read_regular_json(_manifest_path(root, dataset_id), label=f"dataset manifest {dataset_id}"),
        expected_dataset_id=dataset_id,
    )
    validate_dataset_lineage(root, manifest)
    if require_payload and not _payload_available(_payload_path(root, manifest), manifest["payload"]["sha256"]):
        raise ValueError("dataset payload is missing or its hash does not match")
    return manifest


def _manifest_path(root: Path, dataset_id: str) -> Path:
    return safe_workspace_path(
        root,
        DATASET_MANIFEST_ROOT / f"{dataset_id}.json",
        allowed_roots=(Path("trading/research"),),
    )


def _payload_path(root: Path, manifest: dict[str, Any]) -> Path:
    return safe_workspace_path(root, manifest["payload"]["path"], allowed_roots=(DATASET_OBJECT_ROOT,))


def _payload_available(path: Path, expected_hash: str) -> bool:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return False
    return (
        not path.is_symlink()
        and stat.S_ISREG(metadata.st_mode)
        and metadata.st_nlink == 1
        and file_hash(path) == expected_hash
    )


def _source_snapshot_bindings(root: Path, raw_ids: Any) -> tuple[list[str], dict[str, str], str]:
    ids = _string_list(raw_ids, "source_snapshot_ids")
    hashes: dict[str, str] = {}
    known_values: list[str] = []
    for snapshot_id in ids:
        safe_id = sanitize_id(snapshot_id)
        if safe_id != snapshot_id:
            raise ValueError(f"invalid source snapshot id: {snapshot_id}")
        path = safe_workspace_path(
            root,
            SOURCE_SNAPSHOT_ROOT / f"{safe_id}.json",
            allowed_roots=(SOURCE_SNAPSHOT_ROOT,),
        )
        snapshot = validate_source_snapshot(
            read_regular_json(path, label=f"source snapshot {safe_id}"),
            expected_snapshot_id=safe_id,
        )
        hashes[safe_id] = file_hash(path) or ""
        known_values.append(snapshot["known_at"])
    return ids, hashes, max(known_values, default="")


def validate_dataset_lineage(
    workspace_root: Path | str,
    manifest: dict[str, Any],
    *,
    _visited: set[str] | None = None,
) -> None:
    """Validate source and parent bindings for one immutable Dataset manifest."""

    root = Path(workspace_root).expanduser().resolve()
    dataset_id = _dataset_id(manifest.get("dataset_id"))
    visited = set(_visited or ())
    if dataset_id in visited:
        raise ValueError(f"dataset lineage cycle detected: {dataset_id}")
    visited.add(dataset_id)
    cutoff = timestamp_value(manifest["knowledge_cutoff"])
    known_at = timestamp_value(manifest["known_at"])
    for snapshot_id in manifest["source_snapshot_ids"]:
        path = safe_workspace_path(
            root,
            SOURCE_SNAPSHOT_ROOT / f"{snapshot_id}.json",
            allowed_roots=(SOURCE_SNAPSHOT_ROOT,),
        )
        snapshot = validate_source_snapshot(
            read_regular_json(path, label=f"source snapshot {snapshot_id}"),
            expected_snapshot_id=snapshot_id,
        )
        if file_hash(path) != manifest["source_snapshot_hashes"].get(snapshot_id):
            raise ValueError(f"source snapshot hash mismatch: {snapshot_id}")
        if timestamp_value(snapshot["known_at"]) > cutoff:
            raise ValueError(
                f"source snapshot {snapshot_id} is later than dataset knowledge_cutoff"
            )
        if timestamp_value(snapshot["known_at"]) > known_at:
            raise ValueError(
                f"source snapshot {snapshot_id} is later than dataset known_at"
            )

    for parent_id in manifest["lineage"]["parent_dataset_ids"]:
        parent = validate_dataset_manifest(
            read_regular_json(
                _manifest_path(root, _dataset_id(parent_id)),
                label=f"parent dataset manifest {parent_id}",
            ),
            expected_dataset_id=parent_id,
        )
        if timestamp_value(parent["known_at"]) > known_at:
            raise ValueError(f"parent dataset {parent_id} is later than dataset known_at")
        if timestamp_value(parent["knowledge_cutoff"]) > cutoff:
            raise ValueError(
                f"parent dataset {parent_id} is later than dataset knowledge_cutoff"
            )
        validate_dataset_lineage(root, parent, _visited=visited)


def _validated_parent_datasets(
    root: Path,
    value: Any,
    *,
    child_known_at: str,
    child_knowledge_cutoff: str,
) -> list[str]:
    ids = _string_list(value, "parent_dataset_ids")
    for dataset_id in ids:
        parent = _load_manifest(root, _dataset_id(dataset_id), require_payload=False)
        if timestamp_value(parent["known_at"]) > timestamp_value(child_known_at):
            raise ValueError(f"parent dataset {dataset_id} is later than dataset known_at")
        if timestamp_value(parent["knowledge_cutoff"]) > timestamp_value(child_knowledge_cutoff):
            raise ValueError(
                f"parent dataset {dataset_id} is later than dataset knowledge_cutoff"
            )
    return ids


def _scratch_root(value: Path | str | None) -> Path:
    raw = value or os.environ.get("TRADINGCODEX_SCRATCH")
    if not raw:
        raise ValueError("TradingCodex scratch root is required")
    display = Path(raw).expanduser().absolute()
    try:
        metadata = display.lstat()
    except OSError as exc:
        raise ValueError("TradingCodex scratch root must be a real directory") from exc
    if _is_link_or_reparse_point(display, metadata) or not display.is_dir():
        raise ValueError("TradingCodex scratch root must be a real directory")
    return display.resolve()


def _scratch_file(name: str, *, scratch_root: Path | str | None, max_bytes: int) -> Path:
    root = _scratch_root(scratch_root)
    path = root / name
    try:
        path_metadata = path.lstat()
    except OSError as exc:
        raise ValueError("dataset source must be a readable scratch-local regular file") from exc
    if _is_link_or_reparse_point(path, path_metadata):
        raise ValueError("dataset source must not be a symlink or reparse point")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise ValueError("dataset source must be a readable scratch-local regular file") from exc
    try:
        metadata = os.fstat(fd)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise ValueError("dataset source must be a single-link regular file")
        if metadata.st_size > max_bytes:
            raise ValueError(f"dataset source exceeds {max_bytes} bytes")
        current_path_metadata = path.lstat()
        if _is_link_or_reparse_point(path, current_path_metadata):
            raise ValueError("dataset source must not be a symlink or reparse point")
        if not os.path.samestat(metadata, path.stat()):
            raise ValueError("dataset source changed while it was being validated")
    finally:
        os.close(fd)
    return path


def _scratch_destination(name: str, *, scratch_root: Path | str | None) -> Path:
    root = _scratch_root(scratch_root)
    return root / _basename(name, "materialization filename")


def _read_source_table(
    path: Path,
    *,
    source_format: str,
    schema: Any,
    max_bytes: int,
    pa: Any,
    csv_module: Any,
    json_module: Any,
    pq: Any,
) -> Any:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    try:
        metadata = os.fstat(fd)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise ValueError("dataset source must remain a single-link regular file")
        if metadata.st_size > max_bytes:
            raise ValueError(f"dataset source exceeds {max_bytes} bytes")
        current_path_metadata = path.lstat()
        if _is_link_or_reparse_point(path, current_path_metadata):
            raise ValueError("dataset source must not become a symlink or reparse point")
        if not os.path.samestat(metadata, path.stat()):
            raise ValueError("dataset source changed while it was being read")
        with os.fdopen(fd, "rb", closefd=False) as raw:
            source = pa.PythonFile(raw)
            if source_format == "csv":
                return csv_module.read_csv(
                    source,
                    convert_options=csv_module.ConvertOptions(
                        column_types=schema,
                        # Preserve textual NaN/Infinity as floating-point
                        # values so the explicit finite-value gate below sees
                        # and rejects them. Empty cells remain null.
                        null_values=[""],
                    ),
                )
            if source_format == "jsonl":
                return json_module.read_json(source, parse_options=json_module.ParseOptions(explicit_schema=schema))
            return pq.read_table(source)
    finally:
        os.close(fd)


def _is_link_or_reparse_point(path: Path, metadata: os.stat_result) -> bool:
    if path.is_symlink() or stat.S_ISLNK(metadata.st_mode):
        return True
    attributes = getattr(metadata, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return bool(reparse_flag and attributes & reparse_flag)


def _normalize_table(table: Any, *, schema: Any, pc: Any) -> Any:
    expected = list(schema.names)
    if set(table.column_names) != set(expected) or len(table.column_names) != len(expected):
        raise ValueError("dataset source columns must exactly match the declared schema")
    table = table.select(expected)
    try:
        table = table.cast(schema, safe=True).combine_chunks()
    except Exception as exc:
        raise ValueError("dataset source values do not match the declared schema") from exc
    for field in schema:
        column = table.column(field.name)
        if not field.nullable and column.null_count:
            raise ValueError(f"dataset column {field.name} is not nullable")
        if str(field.type) in {"float", "double"}:
            finite = pc.all(pc.or_(pc.is_finite(column), pc.is_null(column))).as_py()
            if finite is not True:
                raise ValueError(f"dataset column {field.name} contains NaN or Infinity")
    return table


def _write_canonical_parquet(table: Any, *, object_root: Path, pq: Any) -> tuple[Path, str, int]:
    fd, temporary_name = tempfile.mkstemp(prefix=".dataset-", suffix=".parquet", dir=object_root)
    os.close(fd)
    temporary = Path(temporary_name)
    try:
        pq.write_table(
            table,
            temporary,
            compression="zstd",
            use_dictionary=False,
            write_statistics=True,
            version="2.6",
            data_page_version="1.0",
        )
        if os.name != "nt":
            temporary.chmod(0o600)
        with temporary.open("rb") as handle:
            os.fsync(handle.fileno())
        digest = file_hash(temporary)
        if digest is None:
            raise ValueError("unable to hash canonical dataset payload")
        return temporary, digest, temporary.stat().st_size
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _quality_profile(table: Any, *, columns: list[dict[str, Any]], pc: Any) -> dict[str, Any]:
    null_counts = {name: table.column(name).null_count for name in table.column_names}
    warnings = [f"column {name} contains {count} null values" for name, count in null_counts.items() if count]
    row_columns = [item["name"] for item in columns]
    duplicate_count: int | None = None
    if row_columns and table.num_rows <= 100_000:
        seen: set[tuple[str, ...]] = set()
        duplicates = 0
        for row in table.select(row_columns).to_pylist():
            key = tuple(repr(row[name]) for name in row_columns)
            if key in seen:
                duplicates += 1
            else:
                seen.add(key)
        duplicate_count = duplicates
        if duplicates:
            warnings.append(f"dataset contains {duplicates} duplicate rows")
    elif row_columns:
        warnings.append("duplicate scan skipped above 100000 rows")
    return {"null_counts": null_counts, "duplicate_count": duplicate_count, "warnings": warnings}


def _column_contract(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise ValueError("columns must be a non-empty array")
    if len(value) > 512:
        raise ValueError("columns must contain at most 512 entries")
    normalized: list[dict[str, Any]] = []
    names: set[str] = set()
    for raw in value:
        if not isinstance(raw, dict):
            raise ValueError("each dataset column must be an object")
        unknown = set(raw) - _COLUMN_FIELDS
        if unknown:
            raise ValueError(f"dataset column has unknown fields: {', '.join(sorted(unknown))}")
        name = str(raw.get("name") or "").strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", name):
            raise ValueError(f"invalid dataset column name: {name}")
        if name in names:
            raise ValueError(f"duplicate dataset column name: {name}")
        names.add(name)
        type_name = str(raw.get("type") or "").strip()
        match = _TYPE_PATTERN.fullmatch(type_name)
        if match is None:
            raise ValueError(f"unsupported dataset column type: {type_name}")
        if type_name.startswith("decimal128") and int(match.group(2)) > int(match.group(1)):
            raise ValueError(f"decimal scale exceeds precision for column {name}")
        nullable = raw.get("nullable", True)
        if type(nullable) is not bool:
            raise ValueError(f"dataset column {name} nullable must be boolean")
        normalized.append(
            {
                "name": name,
                "type": type_name,
                "nullable": nullable,
                "unit": str(raw.get("unit") or "").strip(),
                "currency": str(raw.get("currency") or "").strip(),
                "description": str(raw.get("description") or "").strip(),
            }
        )
    return normalized


def _arrow_type(pa: Any, type_name: str) -> Any:
    simple = {
        "string": pa.string(),
        "bool": pa.bool_(),
        "int64": pa.int64(),
        "float64": pa.float64(),
        "date32": pa.date32(),
        "timestamp": pa.timestamp("us", tz="UTC"),
    }
    if type_name in simple:
        return simple[type_name]
    match = _TYPE_PATTERN.fullmatch(type_name)
    if match and match.group(1):
        return pa.decimal128(int(match.group(1)), int(match.group(2)))
    raise ValueError(f"unsupported dataset column type: {type_name}")


def _selected_columns(value: Any, manifest: dict[str, Any]) -> list[str]:
    available = [item["name"] for item in manifest["columns"]]
    selected = _string_list(value, "columns") if value is not None else available
    if not selected:
        raise ValueError("at least one dataset column is required")
    if len(selected) > 100:
        raise ValueError("at most 100 dataset columns may be selected at once")
    unknown = [name for name in selected if name not in available]
    if unknown:
        raise ValueError(f"unknown dataset column: {unknown[0]}")
    return selected


def _pyarrow() -> tuple[Any, Any, Any, Any, Any]:
    try:
        import pyarrow as pa
        import pyarrow.compute as pc
        import pyarrow.csv as csv_module
        import pyarrow.json as json_module
        import pyarrow.parquet as pq
    except ImportError as exc:
        raise RuntimeError("dataset operations require the pinned PyArrow calculation runtime") from exc
    return pa, pc, csv_module, json_module, pq


def _dataset_id(value: Any) -> str:
    text = str(value or "").strip()
    if not re.fullmatch(r"dataset-[0-9a-f]{24}", text):
        raise ValueError("dataset_id is invalid")
    return text


def _basename(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not text or text in {".", ".."} or "/" in text or "\\" in text or Path(text).name != text:
        raise ValueError(f"{field} must be one basename-only filename")
    return text


def _required_text(args: dict[str, Any], field: str) -> str:
    text = str(args.get(field) or "").strip()
    if not text:
        raise ValueError(f"{field} is required")
    return text


def _timezone(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError("timezone is required")
    try:
        ZoneInfo(text)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError("timezone must be a valid IANA timezone name") from exc
    return text


def _required_choice(args: dict[str, Any], field: str, choices: set[str], *, default: str) -> str:
    value = str(args.get(field) or default).strip()
    if value not in choices:
        raise ValueError(f"{field} must be one of: {', '.join(sorted(choices))}")
    return value


def _string_list(value: Any, field: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
        raise ValueError(f"{field} must be an array of non-empty strings")
    normalized = [item.strip() for item in value]
    if len(normalized) != len(set(normalized)):
        raise ValueError(f"{field} must not contain duplicates")
    return normalized


def _json_object(value: Any, field: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be an object")
    canonical_json_bytes(value)
    return value


def _optional_timestamp(value: Any, field: str) -> str:
    return normalize_timestamp(value, field, required=False)


def _optional_hash(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if text and re.fullmatch(SHA256_PATTERN, text) is None:
        raise ValueError(f"{field} must be a lowercase SHA-256 hash")
    return text


def _exact_fields(value: Any, expected: frozenset[str], label: str) -> None:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    actual = set(value)
    if actual != expected:
        missing = sorted(expected - actual)
        unknown = sorted(actual - expected)
        details = []
        if missing:
            details.append("missing: " + ", ".join(missing))
        if unknown:
            details.append("unknown: " + ", ".join(unknown))
        raise ValueError(f"{label} fields do not match schema ({'; '.join(details)})")


def _json_value(value: Any) -> Any:
    if isinstance(value, str) and len(value) > MAX_PROFILE_CELL_TEXT:
        return value[:MAX_PROFILE_CELL_TEXT] + "…"
    if isinstance(value, (datetime, date, Decimal)):
        return value.isoformat() if hasattr(value, "isoformat") else str(value)
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _json_object_row(row: dict[str, Any]) -> dict[str, Any]:
    return {key: _json_value(value) for key, value in row.items()}


def _withdrawal_files(root: Path) -> list[Path]:
    base = root / DATASET_WITHDRAWAL_ROOT
    if not base.exists() or base.is_symlink():
        return []
    return [path for path in sorted(base.glob("*.json")) if not path.is_symlink() and path.is_file()]


def _withdrawal_for_dataset(root: Path, dataset_id: str) -> dict[str, Any] | None:
    for path in _withdrawal_files(root):
        event = read_regular_json(path, label="dataset withdrawal")
        _validate_withdrawal(event)
        if event["dataset_id"] == dataset_id:
            return event
    return None


def _withdrawn_dataset_ids(root: Path) -> set[str]:
    values: set[str] = set()
    for path in _withdrawal_files(root):
        event = read_regular_json(path, label="dataset withdrawal")
        _validate_withdrawal(event)
        values.add(event["dataset_id"])
    return values


def _validate_withdrawal(value: dict[str, Any]) -> None:
    _exact_fields(value, _WITHDRAWAL_FIELDS, "dataset withdrawal")
    if value["schema_version"] != 1 or value["artifact_type"] != "dataset_withdrawal":
        raise ValueError("dataset withdrawal schema is invalid")
    _dataset_id(value["dataset_id"])
    if value["reason_code"] not in {"license", "legal"}:
        raise ValueError("dataset withdrawal reason_code must be license or legal")
    if not isinstance(value["reason"], str) or not value["reason"].strip():
        raise ValueError("dataset withdrawal reason is required")
    normalize_timestamp(value["recorded_at"], "recorded_at")
    if type(value["payload_removed"]) is not bool or value["workspace_native"] is not True:
        raise ValueError("dataset withdrawal flags are invalid")
    if value["withdrawal_id"] != derive_content_id(
        "dataset-withdrawal",
        {key: item for key, item in value.items() if key not in {"withdrawal_id", "payload_removed", "workspace_native", "withdrawal_hash"}},
    ):
        raise ValueError("dataset withdrawal id mismatch")
    if value["withdrawal_hash"] != content_hash({key: item for key, item in value.items() if key != "withdrawal_hash"}):
        raise ValueError("dataset withdrawal hash mismatch")


def _payload_reference_count(root: Path, payload_hash: str, *, excluding: str) -> int:
    base = root / DATASET_MANIFEST_ROOT
    count = 0
    if not base.exists() or base.is_symlink():
        return 0
    withdrawn = _withdrawn_dataset_ids(root)
    for path in base.glob("*.json"):
        if path.is_symlink() or not path.is_file():
            continue
        manifest = validate_dataset_manifest(read_regular_json(path, label="dataset manifest"))
        if (
            manifest["dataset_id"] != excluding
            and manifest["dataset_id"] not in withdrawn
            and manifest["payload"]["sha256"] == payload_hash
        ):
            count += 1
    return count

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from tradingcodex_service.application.artifact_catalog import _refresh_artifact_catalog
from tradingcodex_service.application.common import exclusive_file_lock, now_iso, safe_workspace_path
from tradingcodex_service.application.research_objects import canonical_json_bytes, content_hash, read_regular_json
from tradingcodex_service.application.runtime import workspace_context_payload


RESEARCH_OBJECT_CATALOG_PATH = Path("trading/research/.index/research-object-catalog-v3.sqlite3")
RESEARCH_OBJECT_CATALOG_SCHEMA_VERSION = 3
RESEARCH_OBJECT_CATALOG_PROJECTOR_VERSION = 3
DEFAULT_SEARCH_LIMIT = 20
MAX_SEARCH_LIMIT = 200
CARD_TITLE_LIMIT = 240
CARD_SUMMARY_LIMIT = 800
CARD_WARNING_LIMIT = 300
CARD_TAG_LIMIT = 100
CARD_LIST_LIMIT = 20
CARD_RELATION_LIMIT = 50


class _CatalogRebuildRequired(RuntimeError):
    pass


def refresh_research_object_catalog(workspace_root: Path | str) -> dict[str, Any]:
    root = Path(workspace_root).expanduser().resolve()
    path = _catalog_path(root)
    legacy = _refresh_artifact_catalog(root)
    with exclusive_file_lock(path.with_suffix("")):
        try:
            result = _refresh_locked(root, path, legacy)
        except (sqlite3.DatabaseError, _CatalogRebuildRequired):
            _remove_database(path)
            result = _refresh_locked(root, path, legacy)
            result["rebuilt"] = True
    return result


def rebuild_research_object_catalog(workspace_root: Path | str) -> dict[str, Any]:
    root = Path(workspace_root).expanduser().resolve()
    path = _catalog_path(root)
    legacy = _refresh_artifact_catalog(root)
    with exclusive_file_lock(path.with_suffix("")):
        _remove_database(path)
        result = _refresh_locked(root, path, legacy)
    return {**result, "status": "rebuilt", "rebuilt": True}


def list_research_objects(
    workspace_root: Path | str,
    args: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return search_research_objects(workspace_root, {**(args or {}), "query": ""})


def search_research_objects(workspace_root: Path | str, args: dict[str, Any]) -> dict[str, Any]:
    root = Path(workspace_root).expanduser().resolve()
    state = refresh_research_object_catalog(root)
    query = str(args.get("query") or "").strip()
    limit = max(1, min(int(args.get("limit") or DEFAULT_SEARCH_LIMIT), MAX_SEARCH_LIMIT))
    where: list[str] = ["o.compatibility <> 'invalid'"]
    parameters: list[Any] = []
    for field in ("object_type", "universe", "status", "role", "calculation_type", "provider", "frequency"):
        value = str(args.get(field) or "").strip()
        if value:
            where.append(f"lower(o.{field}) = lower(?)")
            parameters.append(value)
    symbol = str(args.get("symbol") or "").strip()
    if symbol:
        where.append(
            "(lower(o.symbol)=lower(?) OR EXISTS (SELECT 1 FROM dataset_instruments di "
            "WHERE di.record_key=o.record_key AND di.identifier_kind='symbol' AND lower(di.identifier)=lower(?)))"
        )
        parameters.extend((symbol, symbol))
    instrument_id = str(args.get("instrument_id") or "").strip()
    if instrument_id:
        where.append(
            "EXISTS (SELECT 1 FROM dataset_instruments di WHERE di.record_key=o.record_key "
            "AND di.identifier_kind='instrument' AND lower(di.identifier)=lower(?))"
        )
        parameters.append(instrument_id)
    column = str(args.get("column") or "").strip()
    if column:
        where.append(
            "EXISTS (SELECT 1 FROM dataset_columns dc WHERE dc.record_key=o.record_key AND lower(dc.name)=lower(?))"
        )
        parameters.append(column)
    period_start = str(args.get("period_start") or "").strip()
    if period_start:
        from tradingcodex_service.application.research_objects import normalize_timestamp

        where.append("julianday(o.period_end) >= julianday(?)")
        parameters.append(normalize_timestamp(period_start, "period_start"))
    period_end = str(args.get("period_end") or "").strip()
    if period_end:
        from tradingcodex_service.application.research_objects import normalize_timestamp

        where.append("julianday(o.period_start) <= julianday(?)")
        parameters.append(normalize_timestamp(period_end, "period_end"))
    cutoff = str(args.get("knowledge_cutoff") or "").strip()
    if cutoff:
        from tradingcodex_service.application.research_objects import normalize_timestamp

        cutoff = normalize_timestamp(cutoff, "knowledge_cutoff")
        where.append("coalesce(nullif(o.knowledge_cutoff, ''), nullif(o.known_at, '')) IS NOT NULL")
        where.append("julianday(coalesce(nullif(o.knowledge_cutoff, ''), o.known_at)) <= julianday(?)")
        parameters.append(cutoff)
    if args.get("exclude_withdrawn_datasets") is True:
        where.append(
            "NOT EXISTS ("
            "SELECT 1 FROM relations wr "
            "JOIN objects wo ON wo.record_key=wr.record_key "
            "WHERE wo.object_type='dataset_withdrawal' "
            "AND wo.compatibility<>'invalid' "
            "AND wr.relation_id=o.object_id"
            ")"
        )
    with sqlite3.connect(state["absolute_index_path"]) as connection:
        connection.row_factory = sqlite3.Row
        if query and state["fts_enabled"]:
            match_query = _fts_query(query)
            if not match_query:
                rows = []
            else:
                sql = f"""
                    SELECT o.*, bm25(objects_fts) AS rank
                    FROM objects_fts
                    JOIN objects AS o ON o.record_key = objects_fts.record_key
                    WHERE objects_fts MATCH ? AND {' AND '.join(where)}
                    ORDER BY rank ASC, o.updated_at DESC, o.object_id ASC
                    LIMIT ?
                """
                rows = connection.execute(sql, [match_query, *parameters, limit]).fetchall()
        else:
            local_where = list(where)
            local_parameters = list(parameters)
            if query:
                local_where.append("lower(o.search_text) LIKE ?")
                local_parameters.append(f"%{query.casefold()}%")
            sql = f"""
                SELECT o.*, NULL AS rank
                FROM objects AS o
                WHERE {' AND '.join(local_where)}
                ORDER BY o.updated_at DESC, o.object_id ASC
                LIMIT ?
            """
            rows = connection.execute(sql, [*local_parameters, limit]).fetchall()
    return {
        "query": query,
        "catalog_schema_version": RESEARCH_OBJECT_CATALOG_SCHEMA_VERSION,
        "projector_version": RESEARCH_OBJECT_CATALOG_PROJECTOR_VERSION,
        "index_path": RESEARCH_OBJECT_CATALOG_PATH.as_posix(),
        "fts_enabled": state["fts_enabled"],
        "objects": [_public_card(row) for row in rows],
        "file_sot": True,
        "workspace_native": True,
        "workspace_context": workspace_context_payload(root),
    }


def search_calculation_objects(workspace_root: Path | str, args: dict[str, Any]) -> dict[str, Any]:
    """Return CalculationRun L0 cards without reading full run payloads."""

    result = search_research_objects(
        workspace_root,
        {**args, "object_type": "calculation_run"},
    )
    return {
        "query": result["query"],
        "calculations": result["objects"],
        "count": len(result["objects"]),
        "index_path": result["index_path"],
        "fts_enabled": result["fts_enabled"],
        "file_sot": True,
        "workspace_native": True,
        "workspace_context": result["workspace_context"],
    }


def research_object_catalog_status(workspace_root: Path | str) -> dict[str, Any]:
    root = Path(workspace_root).expanduser().resolve()
    state = refresh_research_object_catalog(root)
    return {
        key: value
        for key, value in state.items()
        if key != "absolute_index_path"
    }


def _refresh_locked(root: Path, path: Path, legacy: dict[str, Any]) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=5)
    try:
        connection.row_factory = sqlite3.Row
        quick_check = connection.execute("PRAGMA quick_check").fetchone()
        if quick_check is None or quick_check[0] != "ok":
            raise _CatalogRebuildRequired("research object catalog failed integrity check")
        fts_enabled = _ensure_schema(connection)
        entries = {
            str(record_key): entry
            for record_key, entry in legacy.get("entries", {}).items()
            if isinstance(entry, dict)
        }
        dependency_hashes = _projection_dependency_hashes(legacy, entries)
        existing = {
            row["record_key"]: {
                "projection_hash": row["projection_hash"],
                "dependency_hash": row["dependency_hash"],
            }
            for row in connection.execute(
                "SELECT record_key, projection_hash, dependency_hash FROM objects"
            )
        }
        removed = set(existing) - set(entries)
        changed = {
            key
            for key in entries
            if existing.get(key, {}).get("dependency_hash") != dependency_hashes[key]
        }
        projections = {
            key: _projection(
                root,
                entries[key],
                dependency_hash=dependency_hashes[key],
            )
            for key in changed
        }
        file_changed = _sync_files(connection, legacy.get("files", {}))
        if removed or changed or file_changed:
            with connection:
                for record_key in sorted(removed | changed):
                    _delete_projection(connection, record_key, fts_enabled=fts_enabled)
                for record_key in sorted(changed):
                    _insert_projection(connection, projections[record_key], fts_enabled=fts_enabled)
                generated_at = now_iso()
                connection.execute(
                    "INSERT OR REPLACE INTO catalog_meta(key, value) VALUES ('generated_at', ?)",
                    (generated_at,),
                )
        generated_at_row = connection.execute(
            "SELECT value FROM catalog_meta WHERE key='generated_at'"
        ).fetchone()
        generated_at = generated_at_row[0] if generated_at_row else now_iso()
        object_count = connection.execute("SELECT count(*) FROM objects").fetchone()[0]
        invalid_count = connection.execute(
            "SELECT count(*) FROM objects WHERE compatibility='invalid'"
        ).fetchone()[0]
        return {
            "status": "refreshed" if removed or changed or file_changed else "current",
            "catalog_schema_version": RESEARCH_OBJECT_CATALOG_SCHEMA_VERSION,
            "projector_version": RESEARCH_OBJECT_CATALOG_PROJECTOR_VERSION,
            "index_path": RESEARCH_OBJECT_CATALOG_PATH.as_posix(),
            "absolute_index_path": str(path),
            "generated_at": generated_at,
            "fts_enabled": fts_enabled,
            "changed_count": len(changed),
            "removed_count": len(removed),
            "object_count": object_count,
            "invalid_count": invalid_count,
            "file_sot": True,
            "workspace_native": True,
            "rebuilt": False,
        }
    finally:
        connection.close()


def _ensure_schema(connection: sqlite3.Connection) -> bool:
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=NORMAL")
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS catalog_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS files (
            path TEXT PRIMARY KEY,
            mtime_ns INTEGER,
            size INTEGER,
            file_hash TEXT NOT NULL,
            status TEXT NOT NULL,
            projection_hash TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS objects (
            record_key TEXT PRIMARY KEY,
            object_id TEXT NOT NULL,
            object_type TEXT NOT NULL,
            title TEXT NOT NULL,
            summary TEXT NOT NULL,
            warnings TEXT NOT NULL,
            tags TEXT NOT NULL,
            status TEXT NOT NULL,
            symbol TEXT NOT NULL,
            universe TEXT NOT NULL,
            role TEXT NOT NULL,
            calculation_type TEXT NOT NULL,
            provider TEXT NOT NULL,
            frequency TEXT NOT NULL,
            period_start TEXT NOT NULL,
            period_end TEXT NOT NULL,
            knowledge_cutoff TEXT NOT NULL,
            known_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            source_path TEXT NOT NULL,
            source_format TEXT NOT NULL,
            compatibility TEXT NOT NULL,
            metadata_json TEXT NOT NULL,
            search_text TEXT NOT NULL,
            dependency_hash TEXT NOT NULL,
            projection_hash TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS objects_id_idx ON objects(object_id);
        CREATE INDEX IF NOT EXISTS objects_type_idx ON objects(object_type, updated_at);
        CREATE INDEX IF NOT EXISTS objects_symbol_idx ON objects(symbol, object_type);
        CREATE TABLE IF NOT EXISTS relations (
            record_key TEXT NOT NULL REFERENCES objects(record_key) ON DELETE CASCADE,
            relation_id TEXT NOT NULL,
            relation_type TEXT NOT NULL,
            PRIMARY KEY(record_key, relation_id, relation_type)
        );
        CREATE INDEX IF NOT EXISTS relations_id_idx ON relations(relation_id);
        CREATE TABLE IF NOT EXISTS dataset_columns (
            record_key TEXT NOT NULL REFERENCES objects(record_key) ON DELETE CASCADE,
            ordinal INTEGER NOT NULL,
            name TEXT NOT NULL,
            data_type TEXT NOT NULL,
            nullable INTEGER NOT NULL,
            unit TEXT NOT NULL,
            currency TEXT NOT NULL,
            PRIMARY KEY(record_key, ordinal)
        );
        CREATE INDEX IF NOT EXISTS dataset_columns_name_idx ON dataset_columns(name);
        CREATE TABLE IF NOT EXISTS dataset_instruments (
            record_key TEXT NOT NULL REFERENCES objects(record_key) ON DELETE CASCADE,
            identifier TEXT NOT NULL,
            identifier_kind TEXT NOT NULL,
            PRIMARY KEY(record_key, identifier, identifier_kind)
        );
        CREATE INDEX IF NOT EXISTS dataset_instruments_id_idx
            ON dataset_instruments(identifier_kind, identifier);
        CREATE TABLE IF NOT EXISTS calculation_metrics (
            record_key TEXT NOT NULL REFERENCES objects(record_key) ON DELETE CASCADE,
            name TEXT NOT NULL,
            value_json TEXT NOT NULL,
            unit TEXT NOT NULL,
            currency TEXT NOT NULL,
            PRIMARY KEY(record_key, name)
        );
        """
    )
    existing = connection.execute(
        "SELECT value FROM catalog_meta WHERE key='schema_version'"
    ).fetchone()
    try:
        if existing and int(existing[0]) != RESEARCH_OBJECT_CATALOG_SCHEMA_VERSION:
            raise _CatalogRebuildRequired("research object catalog schema changed")
    except (TypeError, ValueError) as exc:
        raise _CatalogRebuildRequired("research object catalog schema metadata is invalid") from exc
    existing_projector = connection.execute(
        "SELECT value FROM catalog_meta WHERE key='projector_version'"
    ).fetchone()
    try:
        if existing_projector and int(existing_projector[0]) != RESEARCH_OBJECT_CATALOG_PROJECTOR_VERSION:
            raise _CatalogRebuildRequired("research object catalog projector changed")
    except (TypeError, ValueError) as exc:
        raise _CatalogRebuildRequired("research object catalog projector metadata is invalid") from exc
    object_columns = {row[1] for row in connection.execute("PRAGMA table_info(objects)")}
    if not {
        "calculation_type",
        "provider",
        "frequency",
        "period_start",
        "period_end",
        "dependency_hash",
    }.issubset(object_columns):
        raise _CatalogRebuildRequired("research object catalog projection changed")
    connection.execute(
        "INSERT OR REPLACE INTO catalog_meta(key, value) VALUES ('schema_version', ?)",
        (str(RESEARCH_OBJECT_CATALOG_SCHEMA_VERSION),),
    )
    connection.execute(
        "INSERT OR REPLACE INTO catalog_meta(key, value) VALUES ('projector_version', ?)",
        (str(RESEARCH_OBJECT_CATALOG_PROJECTOR_VERSION),),
    )
    existing_fts = connection.execute(
        "SELECT value FROM catalog_meta WHERE key='fts_enabled'"
    ).fetchone()
    if existing_fts is not None:
        return existing_fts[0] == "1"
    try:
        connection.execute(
            "CREATE VIRTUAL TABLE objects_fts USING fts5(record_key UNINDEXED, title, summary, warnings, tags)"
        )
    except sqlite3.OperationalError:
        enabled = False
    else:
        enabled = True
    connection.execute(
        "INSERT OR REPLACE INTO catalog_meta(key, value) VALUES ('fts_enabled', ?)",
        ("1" if enabled else "0",),
    )
    connection.commit()
    return enabled


def _sync_files(connection: sqlite3.Connection, files: Any) -> bool:
    if not isinstance(files, dict):
        files = {}
    normalized: dict[str, dict[str, Any]] = {}
    for path, record in files.items():
        if not isinstance(record, dict):
            continue
        projection_hash = content_hash(record)
        normalized[str(path)] = {
            "mtime_ns": record.get("mtime_ns"),
            "size": record.get("size"),
            "file_hash": str(record.get("file_hash") or ""),
            "status": str(record.get("status") or "invalid"),
            "projection_hash": projection_hash,
        }
    existing = {
        row["path"]: row["projection_hash"]
        for row in connection.execute("SELECT path, projection_hash FROM files")
    }
    if existing == {path: value["projection_hash"] for path, value in normalized.items()}:
        return False
    with connection:
        connection.execute("DELETE FROM files")
        connection.executemany(
            "INSERT INTO files(path, mtime_ns, size, file_hash, status, projection_hash) VALUES (?, ?, ?, ?, ?, ?)",
            [
                (
                    path,
                    value["mtime_ns"],
                    value["size"],
                    value["file_hash"],
                    value["status"],
                    value["projection_hash"],
                )
                for path, value in sorted(normalized.items())
            ],
        )
    return True


def _projection_dependency_hashes(
    legacy: dict[str, Any],
    entries: dict[str, dict[str, Any]],
) -> dict[str, str]:
    """Hash source-file and transitive object dependencies without reopening files."""

    raw_files = legacy.get("files")
    files = raw_files if isinstance(raw_files, dict) else {}
    id_to_keys: dict[str, list[str]] = {}
    for record_key, entry in entries.items():
        object_id = str(entry.get("catalog_id") or "").strip()
        if object_id:
            id_to_keys.setdefault(object_id, []).append(record_key)
    memo: dict[str, str] = {}

    def visit(record_key: str, active: frozenset[str]) -> str:
        if record_key in memo:
            return memo[record_key]
        if record_key in active:
            return content_hash({"cycle": record_key})
        entry = entries[record_key]
        relative = str(entry.get("path") or "")
        file_record = files.get(relative)
        if not isinstance(file_record, dict):
            file_record = {"status": "missing", "path": relative}
        relation_ids = sorted(
            {
                str(item).strip()
                for item in entry.get("relation_ids", [])
                if str(item).strip()
            }
        )
        dependencies = []
        next_active = active | {record_key}
        for relation_id in relation_ids:
            dependency_keys = sorted(id_to_keys.get(relation_id, []))
            if not dependency_keys:
                dependencies.append(
                    {"relation_id": relation_id, "state": "missing"}
                )
                continue
            dependencies.append(
                {
                    "relation_id": relation_id,
                    "records": [
                        {
                            "record_key": dependency_key,
                            "state": visit(dependency_key, next_active),
                        }
                        for dependency_key in dependency_keys
                    ],
                }
            )
        digest = content_hash(
            {
                "record_key": record_key,
                "source": file_record,
                "dependencies": dependencies,
            }
        )
        memo[record_key] = digest
        return digest

    return {key: visit(key, frozenset()) for key in entries}


def _projection(
    root: Path,
    entry: dict[str, Any],
    *,
    dependency_hash: str,
) -> dict[str, Any]:
    try:
        return _projection_unchecked(
            root,
            entry,
            dependency_hash=dependency_hash,
        )
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        return _invalid_projection(
            entry,
            dependency_hash=dependency_hash,
            error=str(exc).strip() or exc.__class__.__name__,
        )


def _projection_unchecked(
    root: Path,
    entry: dict[str, Any],
    *,
    dependency_hash: str,
) -> dict[str, Any]:
    record_key = str(entry.get("record_key") or "")
    object_type = str(entry.get("artifact_type") or "structured_artifact")
    object_id = str(entry.get("catalog_id") or "")
    title = str(entry.get("title") or object_id)
    summary = ""
    warnings: list[str] = []
    tags: list[str] = []
    metadata: dict[str, Any] = {}
    columns: list[dict[str, Any]] = []
    metrics: list[dict[str, Any]] = []
    knowledge_cutoff = str(entry.get("knowledge_cutoff") or "")
    if object_type == "dataset_manifest" and entry.get("compatibility") != "invalid":
        from tradingcodex_service.application.datasets import (
            validate_dataset_lineage,
            validate_dataset_manifest,
        )

        relative = str(entry.get("path") or "")
        path = safe_workspace_path(root, relative, allowed_roots=(Path("trading/research"),))
        manifest = validate_dataset_manifest(
            read_regular_json(path, label="dataset manifest"),
            expected_dataset_id=object_id,
        )
        validate_dataset_lineage(root, manifest)
        summary = manifest["description"]
        warnings = list(manifest["quality"]["warnings"])
        tags = list(manifest["tags"])
        columns = list(manifest["columns"])
        metadata = {
            "provider": manifest["provider"],
            "frequency": manifest["frequency"],
            "timezone": manifest["timezone"],
            "as_of": manifest["as_of"],
            "vintage": manifest["vintage"],
            "period_start": manifest["period_start"],
            "period_end": manifest["period_end"],
            "row_count": manifest["payload"]["row_count"],
            "size_bytes": manifest["payload"]["size_bytes"],
            "payload_hash": manifest["payload"]["sha256"],
            "column_count": len(columns),
            "column_names": [item["name"] for item in columns[:10]],
            "columns_truncated": len(columns) > 10,
            "source_snapshot_count": len(manifest["source_snapshot_ids"]),
            "quality_warnings": warnings[:20],
            "instrument_ids": manifest["instrument_ids"][:10],
            "symbols": manifest["symbols"][:10],
            "retention_policy": manifest["license"]["retention_policy"],
            "data_classification": manifest["data_classification"],
            "universe_membership_policy": manifest["universe_membership_policy"],
            "relation_ids": [str(item) for item in entry.get("relation_ids", []) if str(item)],
        }
    elif object_type == "dataset_withdrawal" and entry.get("compatibility") != "invalid":
        from tradingcodex_service.application.datasets import _validate_withdrawal

        relative = str(entry.get("path") or "")
        path = safe_workspace_path(root, relative, allowed_roots=(Path("trading/research"),))
        document = read_regular_json(path, label="dataset withdrawal")
        _validate_withdrawal(document)
        metadata = {
            "relation_ids": [
                str(item)
                for item in entry.get("relation_ids", [])
                if str(item)
            ],
        }
    elif object_type == "calculation_spec" and entry.get("compatibility") != "invalid":
        relative = str(entry.get("path") or "")
        path = safe_workspace_path(root, relative, allowed_roots=(Path("trading/research"),))
        document = read_regular_json(path, label="calculation spec")
        calculation_type = str(document.get("calculation_type") or "")
        calculation_version = str(document.get("calculation_version") or "")
        fingerprint = str(document.get("fingerprint") or "")
        dataset_metadata = _calculation_dataset_metadata(root, document)
        knowledge_cutoff = str(document.get("knowledge_cutoff") or knowledge_cutoff)
        title = f"{calculation_type} {object_id}".strip()
        summary = " ".join(
            item
            for item in (
                calculation_type,
                calculation_version,
                fingerprint,
                " ".join(dataset_metadata["dataset_ids"]),
                " ".join(dataset_metadata["symbols"]),
                " ".join(dataset_metadata["instrument_ids"]),
            )
            if item
        )
        metadata = {
            "calculation_type": calculation_type,
            "calculation_version": calculation_version,
            "fingerprint": fingerprint,
            **dataset_metadata,
            "relation_ids": sorted(
                {
                    *[str(item) for item in entry.get("relation_ids", []) if str(item)],
                    *dataset_metadata["dataset_ids"],
                }
            ),
        }
    elif object_type == "calculation_run" and entry.get("compatibility") != "invalid":
        relative = str(entry.get("path") or "")
        path = safe_workspace_path(root, relative, allowed_roots=(Path("trading/research"),))
        document = read_regular_json(path, label="calculation run")
        spec_id = str(document.get("calculation_spec_id") or "")
        spec = _calculation_spec(root, spec_id)
        calculation_type = str(spec.get("calculation_type") or "")
        calculation_version = str(spec.get("calculation_version") or "")
        dataset_metadata = _calculation_dataset_metadata(root, spec, document)
        knowledge_cutoff = str(spec.get("knowledge_cutoff") or knowledge_cutoff)
        metric_cards = _calculation_metrics(document.get("metrics"))[:20]
        metric_text = " ".join(
            f"{metric['name']} {json.dumps(metric['value'], ensure_ascii=False, allow_nan=False)} {metric['unit']} {metric['currency']}"
            for metric in metric_cards
        )
        title = f"{calculation_type} {object_id}".strip()
        summary = " ".join(
            item
            for item in (
                calculation_type,
                calculation_version,
                str(document.get("fingerprint") or ""),
                " ".join(dataset_metadata["dataset_ids"]),
                " ".join(dataset_metadata["symbols"]),
                " ".join(dataset_metadata["instrument_ids"]),
                metric_text,
            )
            if item
        )
        warnings = [str(item) for item in document.get("warnings", []) if isinstance(item, str)]
        tags = [str(item) for item in document.get("tags", []) if isinstance(item, str)]
        metrics = metric_cards
        metadata = {
            "calculation_type": calculation_type,
            "calculation_version": calculation_version,
            "fingerprint": document.get("fingerprint") or "",
            "calculation_spec_id": spec_id,
            "original_run_id": document.get("original_run_id") or "",
            "workflow_run_id": document.get("workflow_run_id") or "",
            "metrics": metric_cards,
            **dataset_metadata,
            "relation_ids": sorted(
                {
                    *[str(item) for item in entry.get("relation_ids", []) if str(item)],
                    *dataset_metadata["dataset_ids"],
                }
            ),
        }
    else:
        metadata = {
            "relation_ids": [str(item) for item in entry.get("relation_ids", []) if str(item)]
        }
    title = _bounded_text(title, CARD_TITLE_LIMIT)
    summary = _bounded_text(summary, CARD_SUMMARY_LIMIT)
    warnings = _bounded_string_list(
        warnings,
        max_items=CARD_LIST_LIMIT,
        max_length=CARD_WARNING_LIMIT,
    )
    tags = _bounded_string_list(
        tags,
        max_items=CARD_LIST_LIMIT,
        max_length=CARD_TAG_LIMIT,
    )
    metadata = _bounded_card_value(metadata)
    relations = _bounded_string_list(
        metadata.get("relation_ids", []) if isinstance(metadata, dict) else [],
        max_items=CARD_RELATION_LIMIT,
        max_length=160,
    )
    projection = {
        "record_key": record_key,
        "object_id": object_id,
        "object_type": object_type,
        "title": title,
        "summary": summary,
        "warnings": warnings,
        "tags": tags,
        "status": str(entry.get("status") or ""),
        "symbol": str(entry.get("symbol") or (metadata.get("symbols") or [""])[0]),
        "universe": str(entry.get("universe") or ""),
        "role": str(entry.get("role") or ""),
        "calculation_type": str(metadata.get("calculation_type") or ""),
        "provider": str(metadata.get("provider") or ""),
        "frequency": str(metadata.get("frequency") or ""),
        "period_start": str(metadata.get("period_start") or ""),
        "period_end": str(metadata.get("period_end") or ""),
        "knowledge_cutoff": knowledge_cutoff,
        "known_at": str(entry.get("known_at") or ""),
        "updated_at": str(entry.get("updated_at") or ""),
        "source_path": str(entry.get("path") or ""),
        "source_format": str(entry.get("source_format") or ""),
        "compatibility": str(entry.get("compatibility") or "invalid"),
        "relations": relations,
        "metadata": metadata,
        "columns": columns,
        "metrics": metrics,
        "dependency_hash": dependency_hash,
    }
    projection["search_text"] = " ".join(
        [
            object_id,
            object_type,
            title,
            summary,
            *warnings,
            *tags,
            str(metadata.get("provider") or ""),
            " ".join(str(item) for item in metadata.get("symbols", [])),
            " ".join(str(item) for item in metadata.get("instrument_ids", [])),
        ]
    ).casefold()
    projection["projection_hash"] = content_hash(projection)
    return projection


def _invalid_projection(
    entry: dict[str, Any],
    *,
    dependency_hash: str,
    error: str,
) -> dict[str, Any]:
    object_id = _bounded_text(str(entry.get("catalog_id") or ""), 160)
    object_type = _bounded_text(
        str(entry.get("artifact_type") or "invalid_artifact"),
        100,
    )
    warning = _bounded_text(error, CARD_WARNING_LIMIT)
    projection = {
        "record_key": str(entry.get("record_key") or ""),
        "object_id": object_id,
        "object_type": object_type,
        "title": _bounded_text(str(entry.get("title") or object_id), CARD_TITLE_LIMIT),
        "summary": "",
        "warnings": [warning],
        "tags": [],
        "status": "invalid",
        "symbol": "",
        "universe": "",
        "role": "",
        "calculation_type": "",
        "provider": "",
        "frequency": "",
        "period_start": "",
        "period_end": "",
        "knowledge_cutoff": "",
        "known_at": "",
        "updated_at": _bounded_text(str(entry.get("updated_at") or ""), 64),
        "source_path": str(entry.get("path") or ""),
        "source_format": str(entry.get("source_format") or ""),
        "compatibility": "invalid",
        "relations": [],
        "metadata": {"error": warning},
        "columns": [],
        "metrics": [],
        "dependency_hash": dependency_hash,
    }
    projection["search_text"] = ""
    projection["projection_hash"] = content_hash(projection)
    return projection


def _delete_projection(connection: sqlite3.Connection, record_key: str, *, fts_enabled: bool) -> None:
    if fts_enabled:
        connection.execute("DELETE FROM objects_fts WHERE record_key=?", (record_key,))
    connection.execute("DELETE FROM objects WHERE record_key=?", (record_key,))


def _insert_projection(connection: sqlite3.Connection, value: dict[str, Any], *, fts_enabled: bool) -> None:
    connection.execute(
        """
        INSERT INTO objects(
            record_key, object_id, object_type, title, summary, warnings, tags,
            status, symbol, universe, role, calculation_type, provider, frequency,
            period_start, period_end, knowledge_cutoff, known_at, updated_at,
            source_path, source_format, compatibility, metadata_json, search_text,
            dependency_hash, projection_hash
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            value["record_key"],
            value["object_id"],
            value["object_type"],
            value["title"],
            value["summary"],
            json.dumps(value["warnings"], ensure_ascii=False, allow_nan=False),
            json.dumps(value["tags"], ensure_ascii=False, allow_nan=False),
            value["status"],
            value["symbol"],
            value["universe"],
            value["role"],
            value["calculation_type"],
            value["provider"],
            value["frequency"],
            value["period_start"],
            value["period_end"],
            value["knowledge_cutoff"],
            value["known_at"],
            value["updated_at"],
            value["source_path"],
            value["source_format"],
            value["compatibility"],
            canonical_json_bytes(value["metadata"]).decode("utf-8"),
            value["search_text"],
            value["dependency_hash"],
            value["projection_hash"],
        ),
    )
    connection.executemany(
        "INSERT INTO relations(record_key, relation_id, relation_type) VALUES (?, ?, 'references')",
        [(value["record_key"], relation_id) for relation_id in value["relations"]],
    )
    identifiers = [
        (value["record_key"], str(identifier), kind)
        for kind, field in (("instrument", "instrument_ids"), ("symbol", "symbols"))
        for identifier in value["metadata"].get(field, [])
    ]
    connection.executemany(
        "INSERT INTO dataset_instruments(record_key, identifier, identifier_kind) VALUES (?, ?, ?)",
        identifiers,
    )
    connection.executemany(
        """
        INSERT INTO dataset_columns(record_key, ordinal, name, data_type, nullable, unit, currency)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                value["record_key"],
                ordinal,
                column["name"],
                column["type"],
                int(column["nullable"]),
                column["unit"],
                column["currency"],
            )
            for ordinal, column in enumerate(value["columns"])
        ],
    )
    connection.executemany(
        """
        INSERT INTO calculation_metrics(record_key, name, value_json, unit, currency)
        VALUES (?, ?, ?, ?, ?)
        """,
        [
            (
                value["record_key"],
                metric["name"],
                canonical_json_bytes(metric["value"]).decode("utf-8"),
                metric["unit"],
                metric["currency"],
            )
            for metric in value["metrics"]
        ],
    )
    if fts_enabled:
        connection.execute(
            "INSERT INTO objects_fts(record_key, title, summary, warnings, tags) VALUES (?, ?, ?, ?, ?)",
            (
                value["record_key"],
                value["title"],
                value["summary"],
                " ".join(value["warnings"]),
                " ".join(value["tags"]),
            ),
        )


def _bounded_text(value: Any, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _bounded_string_list(
    value: Any,
    *,
    max_items: int,
    max_length: int,
) -> list[str]:
    if not isinstance(value, (list, tuple, set)):
        return []
    return [
        _bounded_text(item, max_length)
        for item in list(value)[:max_items]
        if str(item).strip()
    ]


def _bounded_card_value(value: Any, *, depth: int = 0) -> Any:
    if depth >= 4:
        return _bounded_text(value, CARD_WARNING_LIMIT)
    if isinstance(value, str):
        return _bounded_text(value, CARD_WARNING_LIMIT)
    if isinstance(value, list):
        return [
            _bounded_card_value(item, depth=depth + 1)
            for item in value[:CARD_LIST_LIMIT]
        ]
    if isinstance(value, dict):
        return {
            _bounded_text(key, 100): _bounded_card_value(item, depth=depth + 1)
            for key, item in list(value.items())[:40]
        }
    return value


def _calculation_metrics(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    metrics = []
    for item in value:
        if not isinstance(item, dict) or not str(item.get("name") or "").strip():
            continue
        metrics.append(
            {
                "name": str(item["name"]).strip(),
                "value": item.get("value"),
                "unit": str(item.get("unit") or ""),
                "currency": str(item.get("currency") or ""),
            }
        )
    return metrics


def _calculation_dataset_metadata(
    root: Path,
    spec: dict[str, Any],
    run: dict[str, Any] | None = None,
) -> dict[str, list[str]]:
    from tradingcodex_service.application.datasets import (
        DATASET_MANIFEST_ROOT,
        validate_dataset_manifest,
    )

    dataset_ids = {
        str(item.get("dataset_id") or "").strip()
        for item in spec.get("inputs", [])
        if isinstance(item, dict) and str(item.get("dataset_id") or "").strip()
    }
    if isinstance(run, dict):
        dataset_ids.update(
            str(item).strip()
            for item in run.get("derived_dataset_ids", [])
            if str(item).strip()
        )
    symbols: set[str] = set()
    instrument_ids: set[str] = set()
    for dataset_id in sorted(dataset_ids)[:20]:
        try:
            path = safe_workspace_path(
                root,
                DATASET_MANIFEST_ROOT / f"{dataset_id}.json",
                allowed_roots=(Path("trading/research"),),
            )
            manifest = validate_dataset_manifest(
                read_regular_json(path, label=f"dataset manifest {dataset_id}"),
                expected_dataset_id=dataset_id,
            )
        except (OSError, ValueError):
            continue
        symbols.update(str(item) for item in manifest["symbols"] if str(item))
        instrument_ids.update(
            str(item) for item in manifest["instrument_ids"] if str(item)
        )
    return {
        "dataset_ids": sorted(dataset_ids)[:20],
        "symbols": sorted(symbols)[:20],
        "instrument_ids": sorted(instrument_ids)[:20],
    }


def _calculation_spec(root: Path, spec_id: str) -> dict[str, Any]:
    if not re.fullmatch(r"calc-spec-[0-9a-f]{20}", spec_id):
        return {}
    path = safe_workspace_path(
        root,
        Path("trading/research/calculations/specs") / f"{spec_id}.json",
        allowed_roots=(Path("trading/research"),),
    )
    try:
        return read_regular_json(path, label=f"calculation spec {spec_id}")
    except ValueError:
        return {}


def _public_card(row: sqlite3.Row) -> dict[str, Any]:
    metadata = _bounded_card_value(json.loads(row["metadata_json"]))
    return {
        "object_id": _bounded_text(row["object_id"], 160),
        "object_type": _bounded_text(row["object_type"], 100),
        "title": _bounded_text(row["title"], CARD_TITLE_LIMIT),
        "summary": _bounded_text(row["summary"], CARD_SUMMARY_LIMIT),
        "status": _bounded_text(row["status"], 64),
        "symbol": _bounded_text(row["symbol"], 160),
        "universe": _bounded_text(row["universe"], 160),
        "role": _bounded_text(row["role"], 100),
        "calculation_type": _bounded_text(row["calculation_type"], 100),
        "provider": _bounded_text(row["provider"], CARD_WARNING_LIMIT),
        "frequency": _bounded_text(row["frequency"], 64),
        "period_start": _bounded_text(row["period_start"], 64),
        "period_end": _bounded_text(row["period_end"], 64),
        "knowledge_cutoff": _bounded_text(row["knowledge_cutoff"], 64),
        "known_at": _bounded_text(row["known_at"], 64),
        "updated_at": _bounded_text(row["updated_at"], 64),
        "relation_ids": _relations_for_card(row),
        "path": _bounded_text(row["source_path"], 500),
        "compatibility": _bounded_text(row["compatibility"], 32),
        "warnings": _bounded_string_list(
            json.loads(row["warnings"]),
            max_items=CARD_LIST_LIMIT,
            max_length=CARD_WARNING_LIMIT,
        ),
        "tags": _bounded_string_list(
            json.loads(row["tags"]),
            max_items=CARD_LIST_LIMIT,
            max_length=CARD_TAG_LIMIT,
        ),
        "details": metadata,
    }


def _relations_for_card(row: sqlite3.Row) -> list[str]:
    metadata = json.loads(row["metadata_json"])
    values = metadata.get("relation_ids")
    return _bounded_string_list(
        values,
        max_items=CARD_RELATION_LIMIT,
        max_length=160,
    )


def _fts_query(query: str) -> str:
    tokens = [token for token in re.findall(r"[\w.-]+", query, flags=re.UNICODE) if token]
    return " OR ".join(f'"{token.replace(chr(34), chr(34) * 2)}"' for token in tokens[:20])


def _catalog_path(root: Path) -> Path:
    return safe_workspace_path(
        root,
        RESEARCH_OBJECT_CATALOG_PATH,
        allowed_roots=(Path("trading/research"),),
    )


def _remove_database(path: Path) -> None:
    for candidate in (path, Path(f"{path}-wal"), Path(f"{path}-shm")):
        if candidate.is_symlink():
            raise ValueError("research object catalog files must not be symlinks")
        candidate.unlink(missing_ok=True)

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tradingcodex_service.application.common import (
    atomic_write_text,
    exclusive_file_lock,
    file_hash,
    now_iso,
    safe_workspace_path,
)
from tradingcodex_service.application.markdown_preview import split_markdown_frontmatter
from tradingcodex_service.application.runtime import workspace_context_payload


ARTIFACT_CATALOG_PATH = Path("trading/research/.index/artifact-catalog-v2.json")
ARTIFACT_CATALOG_SCHEMA_VERSION = 2
ARTIFACT_CATALOG_PROJECTOR_VERSION = 2
ARTIFACT_CATALOG_ROOTS = (
    Path("trading/research"),
    Path("trading/reports"),
    Path("trading/decisions"),
    Path("trading/forecasts"),
    Path("trading/evaluations"),
)

_INDEXED_SUFFIXES = (".md", ".json", ".jsonl")
_EXCLUDED_DIRECTORIES = {".drafts", ".index", ".versions"}
_EXCLUDED_FILES = {"forecast-chain-heads.json"}
_IDENTITY_FIELDS = (
    "artifact_id",
    "catalog_id",
    "decision_id",
    "forecast_id",
    "snapshot_id",
    "dataset_id",
    "withdrawal_id",
    "calculation_spec_id",
    "calculation_run_id",
    "spec_id",
    "manifest_id",
    "run_id",
    "analysis_id",
    "review_id",
    "corpus_id",
    "comparison_id",
    "assignment_id",
    "card_id",
    "id",
)
_SEARCHABLE_STRUCTURED_FIELDS = frozenset(
    {
        *_IDENTITY_FIELDS,
        "artifact_type",
        "path",
        "relation_ids",
        "title",
        "symbol",
        "instrument",
        "universe",
        "role",
        "created_by",
        "producer_role",
        "workflow_run_id",
        "evidence_lane",
        "status",
        "readiness_label",
        "handoff_state",
        "hypothesis",
        "economic_mechanism",
        "target",
        "forecast_target",
        "conclusion",
        "context_summary",
        "reader_summary",
        "scenario_summary",
        "original_thesis",
        "what_happened",
        "failed_assumption",
        "future_warning_pattern",
        "provider",
        "description",
        "tags",
        "quality_warnings",
        "retention_policy",
        "source_category",
        "source_locator",
        "coverage_note",
        "revision_reason",
        "resolution_rule",
        "resolution_source",
        "validation_summary",
        "warnings",
        "source_limitations",
        "missing_evidence",
        "contrary_evidence",
        "invalidation_conditions",
        "update_triggers",
        "lesson_candidates",
        "process_review",
        "judgment_review",
    }
)
_PRIMARY_ID_BY_TYPE = {
    "source_snapshot": "snapshot_id",
    "dataset_manifest": "dataset_id",
    "dataset_withdrawal": "withdrawal_id",
    "calculation_spec": "calculation_spec_id",
    "calculation_run": "calculation_run_id",
    "research_spec": "spec_id",
    "replay_manifest": "manifest_id",
    "experiment_run": "run_id",
    "causal_equity_analysis": "analysis_id",
    "blind_judgment_prior": "analysis_id",
    "two_pass_judgment_review": "analysis_id",
    "decision_package": "decision_id",
    "decision_snapshot": "decision_id",
    "forecast": "forecast_id",
    "postmortem_process_review": "id",
    "postmortem_report": "id",
    "evidence_run_card": "card_id",
    "validation_card": "card_id",
}


def list_artifact_catalog(
    workspace_root: Path | str,
    args: dict[str, Any] | None = None,
) -> dict[str, Any]:
    args = args or {}
    root = Path(workspace_root).expanduser().resolve()
    document = _refresh_artifact_catalog(root)
    entries = list(document["entries"].values())
    entries = _filter_entries(entries, args)
    include_invalid = bool(args.get("include_invalid"))
    if not include_invalid:
        entries = [entry for entry in entries if entry.get("compatibility") != "invalid"]
    entries.sort(key=lambda item: (str(item.get("updated_at") or ""), str(item.get("catalog_id") or "")), reverse=True)
    limit = max(1, min(int(args.get("limit") or 100), 1000))
    return {
        "catalog_schema_version": ARTIFACT_CATALOG_SCHEMA_VERSION,
        "projector_version": ARTIFACT_CATALOG_PROJECTOR_VERSION,
        "index_path": ARTIFACT_CATALOG_PATH.as_posix(),
        "generated_at": document["generated_at"],
        "file_sot": True,
        "workspace_native": True,
        "entries": [_public_entry(entry) for entry in entries[:limit]],
        "coverage": _coverage(document["entries"].values()),
        "workspace_context": workspace_context_payload(root),
    }


def search_artifact_catalog(
    workspace_root: Path | str,
    args: dict[str, Any],
) -> dict[str, Any]:
    query = str(args.get("query") or "").strip()
    if not query:
        raise ValueError("query is required")
    root = Path(workspace_root).expanduser().resolve()
    document = _refresh_artifact_catalog(root)
    entries = [
        entry
        for entry in document["entries"].values()
        if entry.get("compatibility") != "invalid"
    ]
    entries, cutoff_excluded = _filter_entries(entries, args, report_cutoff_exclusions=True)
    query_text = query.casefold()
    tokens = [token for token in re.findall(r"[\w.-]+", query_text, flags=re.UNICODE) if len(token) > 1]
    scored: list[tuple[int, dict[str, Any]]] = []
    for entry in entries:
        metadata = str(entry.get("metadata_search_text") or "")
        body = str(entry.get("body_search_text") or "")
        score = 0
        matched_fields: list[str] = []
        if query_text in metadata:
            score += 100
            matched_fields.append("metadata")
        if query_text in body:
            score += 50
            matched_fields.append("body")
        for token in tokens:
            if token in metadata:
                score += 10
                if "metadata" not in matched_fields:
                    matched_fields.append("metadata")
            if token in body:
                score += 2
                if "body" not in matched_fields:
                    matched_fields.append("body")
        if score:
            scored.append((score, {**entry, "score": score, "matched_fields": matched_fields}))
    scored.sort(
        key=lambda item: (
            item[0],
            str(item[1].get("updated_at") or ""),
            str(item[1].get("catalog_id") or ""),
        ),
        reverse=True,
    )
    limit = max(1, min(int(args.get("limit") or 20), 200))
    return {
        "query": query,
        "catalog_schema_version": ARTIFACT_CATALOG_SCHEMA_VERSION,
        "index_path": ARTIFACT_CATALOG_PATH.as_posix(),
        "file_sot": True,
        "workspace_native": True,
        "entries": [_public_entry(entry) for _, entry in scored[:limit]],
        "cutoff_excluded_count": cutoff_excluded,
        "coverage": _coverage(document["entries"].values()),
        "workspace_context": workspace_context_payload(root),
    }


def rebuild_artifact_catalog(workspace_root: Path | str) -> dict[str, Any]:
    root = Path(workspace_root).expanduser().resolve()
    path = safe_workspace_path(
        root,
        ARTIFACT_CATALOG_PATH,
        allowed_roots=(Path("trading/research"),),
    )
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    document = _refresh_artifact_catalog(root)
    return {
        "status": "rebuilt",
        "catalog_schema_version": ARTIFACT_CATALOG_SCHEMA_VERSION,
        "projector_version": ARTIFACT_CATALOG_PROJECTOR_VERSION,
        "index_path": ARTIFACT_CATALOG_PATH.as_posix(),
        "coverage": _coverage(document["entries"].values()),
        "file_sot": True,
        "workspace_native": True,
        "workspace_context": workspace_context_payload(root),
    }


def _refresh_artifact_catalog(root: Path) -> dict[str, Any]:
    index_path = safe_workspace_path(
        root,
        ARTIFACT_CATALOG_PATH,
        allowed_roots=(Path("trading/research"),),
    )
    lock_target = index_path.with_suffix("")
    with exclusive_file_lock(lock_target):
        existing = _read_existing_catalog(index_path)
        existing_files = existing.get("files") if isinstance(existing.get("files"), dict) else {}
        existing_entries = existing.get("entries") if isinstance(existing.get("entries"), dict) else {}
        paths = _catalog_paths(root)
        current_paths = {relative for relative, _ in paths}
        changed = set(existing_files) != current_paths
        files: dict[str, dict[str, Any]] = {}
        entries: dict[str, dict[str, Any]] = {}
        for relative, path in paths:
            try:
                stat = path.lstat()
            except OSError as exc:
                parsed_entries = [_invalid_entry(relative, "artifact file metadata is unreadable")]
                file_record = {
                    "status": "invalid",
                    "error": exc.__class__.__name__,
                    "record_keys": [parsed_entries[0]["record_key"]],
                }
                changed = True
            else:
                cached = existing_files.get(relative) if isinstance(existing_files.get(relative), dict) else {}
                cached_keys = cached.get("record_keys") if isinstance(cached.get("record_keys"), list) else []
                dependency_state = _catalog_dependency_state(root, relative)
                if (
                    cached.get("mtime_ns") == stat.st_mtime_ns
                    and cached.get("size") == stat.st_size
                    and cached.get("dependency") == dependency_state
                    and cached_keys
                    and all(key in existing_entries for key in cached_keys)
                ):
                    file_record = cached
                    parsed_entries = [existing_entries[key] for key in cached_keys]
                else:
                    parsed_entries, error = _parse_catalog_file(root, relative, path)
                    digest = file_hash(path)
                    file_record = {
                        "mtime_ns": stat.st_mtime_ns,
                        "size": stat.st_size,
                        "file_hash": digest or "",
                        "status": "invalid" if error else "valid",
                        "error": error,
                        "dependency": dependency_state,
                        "record_keys": [entry["record_key"] for entry in parsed_entries],
                    }
                    changed = True
            files[relative] = file_record
            for entry in parsed_entries:
                entries[entry["record_key"]] = entry
        if not existing or changed or not index_path.exists():
            document = {
                "schema_version": ARTIFACT_CATALOG_SCHEMA_VERSION,
                "projector_version": ARTIFACT_CATALOG_PROJECTOR_VERSION,
                "generated_at": now_iso(),
                "files": files,
                "entries": entries,
            }
            atomic_write_text(
                index_path,
                json.dumps(document, indent=2, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n",
            )
            return document
        return existing


def _read_existing_catalog(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(document, dict):
        return {}
    if document.get("schema_version") != ARTIFACT_CATALOG_SCHEMA_VERSION:
        return {}
    if document.get("projector_version") != ARTIFACT_CATALOG_PROJECTOR_VERSION:
        return {}
    return document


def _catalog_paths(root: Path) -> list[tuple[str, Path]]:
    resolved_root = root.expanduser().resolve()
    paths: list[tuple[str, Path]] = []
    for relative_root in ARTIFACT_CATALOG_ROOTS:
        base = resolved_root / relative_root
        if not base.exists() or base.is_symlink():
            continue
        for candidate in base.rglob("*"):
            try:
                relative = candidate.relative_to(resolved_root)
            except ValueError:
                continue
            if candidate.name in _EXCLUDED_FILES:
                continue
            if any(part in _EXCLUDED_DIRECTORIES or part.startswith(".") for part in relative.parts):
                continue
            if not candidate.name.endswith(_INDEXED_SUFFIXES):
                continue
            try:
                safe = safe_workspace_path(root, relative, allowed_roots=ARTIFACT_CATALOG_ROOTS)
            except ValueError:
                continue
            if candidate.is_symlink() or not safe.is_file():
                continue
            paths.append((relative.as_posix(), safe))
    return sorted(paths)


def _parse_catalog_file(root: Path, relative: str, path: Path) -> tuple[list[dict[str, Any]], str]:
    try:
        if relative == "trading/forecasts/forecast-ledger.jsonl":
            return _forecast_entries(root, relative, path), ""
        if path.suffix.lower() == ".md":
            return [_markdown_entry(relative, path)], ""
        if path.suffix.lower() == ".json":
            return [_json_entry(relative, path)], ""
        return [_invalid_entry(relative, "unsupported artifact catalog format")], "unsupported artifact catalog format"
    except (OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError) as exc:
        error = str(exc).strip() or exc.__class__.__name__
        return [_invalid_entry(relative, error)], error


def _markdown_entry(relative: str, path: Path) -> dict[str, Any]:
    document = split_markdown_frontmatter(path.read_text(encoding="utf-8"))
    metadata = document.frontmatter
    is_decision = relative.startswith("trading/decisions/")
    identity_field = "decision_id" if is_decision else "artifact_id"
    artifact_id = str(metadata.get(identity_field) or "").strip()
    artifact_type = str(metadata.get("artifact_type") or ("decision_package" if is_decision else "")).strip()
    missing = [field for field, value in ((identity_field, artifact_id), ("artifact_type", artifact_type)) if not value]
    if not is_decision and not str(metadata.get("universe") or "").strip():
        missing.append("universe")
    compatibility = "full" if not missing else "legacy_partial"
    catalog_id = artifact_id or _legacy_catalog_id(relative)
    body = document.body
    entry = _base_entry(
        relative,
        path,
        catalog_id=catalog_id,
        artifact_id=artifact_id,
        artifact_type=artifact_type or ("decision_package" if is_decision else "legacy_markdown"),
        compatibility=compatibility,
        missing_fields=missing,
        source_format="markdown",
        metadata=metadata,
        title=document.heading or str(metadata.get("title") or path.stem),
        body_search_text=body.casefold(),
    )
    return entry


def _json_entry(relative: str, path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("structured artifact JSON must be an object")
    inferred_type = _infer_structured_type(relative)
    declared_type = str(value.get("artifact_type") or "").strip()
    artifact_type = declared_type or inferred_type or "structured_artifact"
    identity_field = _PRIMARY_ID_BY_TYPE.get(artifact_type) or _first_identity_field(value)
    artifact_id = str(value.get(identity_field) or "").strip() if identity_field else ""
    missing = []
    if not artifact_id:
        missing.append(identity_field or "artifact_id")
    if not declared_type and not inferred_type:
        missing.append("artifact_type")
    compatibility = "full" if not missing else "legacy_partial"
    catalog_id = artifact_id or _legacy_catalog_id(relative)
    return _base_entry(
        relative,
        path,
        catalog_id=catalog_id,
        artifact_id=artifact_id,
        artifact_type=artifact_type,
        compatibility=compatibility,
        missing_fields=missing,
        source_format="json",
        metadata=value,
        title=str(value.get("title") or value.get("forecast_target") or value.get("hypothesis") or artifact_id or path.stem),
        body_search_text=_structured_search_text(value),
    )


def _forecast_entries(root: Path, relative: str, path: Path) -> list[dict[str, Any]]:
    from tradingcodex_service.application.forecasting import list_forecasts

    chain_heads = root / "trading/forecasts/forecast-chain-heads.json"
    if chain_heads.is_symlink():
        raise ValueError("forecast chain heads must not be a symlink")
    forecasts = list_forecasts(root, {"limit": 1000})["forecasts"]
    entries: list[dict[str, Any]] = []
    for forecast in forecasts:
        forecast_id = str(forecast.get("forecast_id") or "").strip()
        if not forecast_id:
            raise ValueError("forecast ledger entry requires forecast_id")
        entries.append(
            _base_entry(
                relative,
                path,
                catalog_id=forecast_id,
                artifact_id=forecast_id,
                artifact_type="forecast",
                compatibility="full",
                missing_fields=[],
                source_format="jsonl",
                metadata=forecast,
                title=str(forecast.get("forecast_target") or forecast_id),
                body_search_text=_structured_search_text(forecast),
                record_suffix=f"forecast:{forecast_id}",
            )
        )
    return entries


def _base_entry(
    relative: str,
    path: Path,
    *,
    catalog_id: str,
    artifact_id: str,
    artifact_type: str,
    compatibility: str,
    missing_fields: list[str],
    source_format: str,
    metadata: dict[str, Any],
    title: str,
    body_search_text: str,
    record_suffix: str = "",
) -> dict[str, Any]:
    stat = path.stat()
    record_key = relative if not record_suffix else f"{relative}#{record_suffix}"
    relation_ids = _relation_ids(metadata, artifact_id)
    searchable_metadata = {
        "catalog_id": catalog_id,
        "artifact_id": artifact_id,
        "artifact_type": artifact_type,
        "path": relative,
        "title": title,
        "symbol": metadata.get("symbol") or metadata.get("instrument") or "",
        "universe": metadata.get("universe") or "",
        "workflow_run_id": metadata.get("workflow_run_id") or "",
        "role": metadata.get("role") or metadata.get("producer_role") or metadata.get("created_by") or "",
        "status": metadata.get("status") or "",
        "readiness_label": metadata.get("readiness_label") or "",
        "handoff_state": metadata.get("handoff_state") or "",
        "relation_ids": relation_ids,
    }
    updated_at = _first_text(
        metadata,
        "recorded_at",
        "system_recorded_at",
        "updated_at",
        "created_at",
        "generated_at",
        "decided_at",
        "issued_at",
        "known_at",
    ) or datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat().replace("+00:00", "Z")
    return {
        "record_key": record_key,
        "catalog_id": catalog_id,
        "artifact_id": artifact_id,
        "canonical_id": bool(artifact_id),
        "artifact_type": artifact_type,
        "compatibility": compatibility,
        "missing_fields": sorted(set(missing_fields)),
        "path": relative,
        "source_format": source_format,
        "file_hash": file_hash(path) or "",
        "title": title,
        "symbol": str(metadata.get("symbol") or metadata.get("instrument") or ""),
        "universe": str(metadata.get("universe") or ""),
        "workflow_run_id": str(metadata.get("workflow_run_id") or ""),
        "role": str(metadata.get("role") or metadata.get("producer_role") or metadata.get("created_by") or ""),
        "knowledge_cutoff": str(metadata.get("knowledge_cutoff") or ""),
        "known_at": str(metadata.get("known_at") or ""),
        "source_as_of": str(metadata.get("source_as_of") or metadata.get("as_of") or ""),
        "updated_at": updated_at,
        "readiness_label": str(metadata.get("readiness_label") or ""),
        "handoff_state": str(metadata.get("handoff_state") or ""),
        "status": str(metadata.get("status") or metadata.get("event_type") or ""),
        "relation_ids": relation_ids,
        "metadata_search_text": _structured_search_text(searchable_metadata),
        "body_search_text": body_search_text,
    }


def _invalid_entry(relative: str, error: str) -> dict[str, Any]:
    return {
        "record_key": relative,
        "catalog_id": _legacy_catalog_id(relative),
        "artifact_id": "",
        "canonical_id": False,
        "artifact_type": "invalid_artifact",
        "compatibility": "invalid",
        "missing_fields": [],
        "path": relative,
        "source_format": Path(relative).suffix.lstrip("."),
        "file_hash": "",
        "title": Path(relative).stem,
        "symbol": "",
        "universe": "",
        "workflow_run_id": "",
        "role": "",
        "knowledge_cutoff": "",
        "known_at": "",
        "source_as_of": "",
        "updated_at": "",
        "readiness_label": "",
        "handoff_state": "",
        "status": "invalid",
        "relation_ids": [],
        "metadata_search_text": "",
        "body_search_text": "",
        "error": error,
    }


def _filter_entries(
    entries: list[dict[str, Any]],
    args: dict[str, Any],
    *,
    report_cutoff_exclusions: bool = False,
) -> list[dict[str, Any]] | tuple[list[dict[str, Any]], int]:
    filtered = entries
    for field in (
        "artifact_type",
        "universe",
        "symbol",
        "workflow_run_id",
        "readiness_label",
        "handoff_state",
        "compatibility",
    ):
        value = str(args.get(field) or "").strip()
        if value:
            filtered = [entry for entry in filtered if str(entry.get(field) or "").casefold() == value.casefold()]
    cutoff_excluded = 0
    cutoff = str(args.get("knowledge_cutoff") or "").strip()
    if cutoff:
        normalized_cutoff = _normalized_timestamp(cutoff, "knowledge_cutoff")
        cutoff_filtered = []
        for entry in filtered:
            entry_cutoff = str(
                entry.get("knowledge_cutoff") or entry.get("known_at") or ""
            ).strip()
            if not entry_cutoff:
                cutoff_excluded += 1
                continue
            try:
                if _normalized_timestamp(entry_cutoff, "artifact knowledge_cutoff") <= normalized_cutoff:
                    cutoff_filtered.append(entry)
                else:
                    cutoff_excluded += 1
            except ValueError:
                cutoff_excluded += 1
        filtered = cutoff_filtered
    if report_cutoff_exclusions:
        return filtered, cutoff_excluded
    return filtered


def _public_entry(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in entry.items()
        if key not in {"metadata_search_text", "body_search_text", "record_key"}
    }


def _coverage(entries: Any) -> dict[str, int]:
    counts = {"total": 0, "full": 0, "legacy_partial": 0, "invalid": 0}
    for entry in entries:
        counts["total"] += 1
        compatibility = str(entry.get("compatibility") or "invalid")
        counts[compatibility] = counts.get(compatibility, 0) + 1
    return counts


def _infer_structured_type(relative: str) -> str:
    if relative.endswith(".run-card.json"):
        return "evidence_run_card"
    if relative.endswith(".validation-card.json"):
        return "validation_card"
    if relative.endswith(".decision-snapshot.json"):
        return "decision_snapshot"
    if relative.endswith(".process-review.json"):
        return "postmortem_process_review"
    if relative.endswith(".postmortem_report.json"):
        return "postmortem_report"
    if relative.startswith("trading/research/source-snapshots/"):
        return "source_snapshot"
    if relative.startswith("trading/research/datasets/manifests/"):
        return "dataset_manifest"
    if relative.startswith("trading/research/datasets/withdrawals/"):
        return "dataset_withdrawal"
    if relative.startswith("trading/research/calculations/specs/"):
        return "calculation_spec"
    if relative.startswith("trading/research/calculations/runs/"):
        return "calculation_run"
    if relative.startswith("trading/research/specs/"):
        return "research_spec"
    if relative.startswith("trading/research/replay-manifests/"):
        return "replay_manifest"
    if relative.startswith("trading/research/experiments/"):
        return "experiment_run"
    return ""


def _first_identity_field(value: dict[str, Any]) -> str:
    return next((field for field in _IDENTITY_FIELDS if str(value.get(field) or "").strip()), "")


def _catalog_dependency_state(root: Path, relative: str) -> dict[str, Any]:
    if relative != "trading/forecasts/forecast-ledger.jsonl":
        return {}
    path = root / "trading/forecasts/forecast-chain-heads.json"
    if not path.exists() and not path.is_symlink():
        return {"present": False}
    stat = path.lstat()
    return {
        "present": True,
        "symlink": path.is_symlink(),
        "mtime_ns": stat.st_mtime_ns,
        "size": stat.st_size,
    }


def _legacy_catalog_id(relative: str) -> str:
    digest = hashlib.sha256(relative.encode("utf-8")).hexdigest()[:16]
    return f"legacy-path-{digest}"


def _relation_ids(metadata: dict[str, Any], own_id: str) -> list[str]:
    values: list[str] = []
    for field in (
        "input_artifact_ids",
        "source_snapshot_ids",
        "evidence_ids",
        "forecast_ids",
        "parent_dataset_ids",
        "input_dataset_ids",
        "derived_dataset_ids",
        "calculation_run_ids",
    ):
        raw = metadata.get(field)
        if isinstance(raw, list):
            values.extend(str(item).strip() for item in raw if str(item).strip())
    raw_inputs = metadata.get("inputs")
    if isinstance(raw_inputs, list):
        values.extend(
            str(item.get("dataset_id") or "").strip()
            for item in raw_inputs
            if isinstance(item, dict) and str(item.get("dataset_id") or "").strip()
        )
    lineage = metadata.get("lineage")
    if isinstance(lineage, dict):
        parent_ids = lineage.get("parent_dataset_ids")
        if isinstance(parent_ids, list):
            values.extend(
                str(item).strip() for item in parent_ids if str(item).strip()
            )
    for field in (
        "research_spec_id",
        "replay_manifest_id",
        "decision_snapshot_id",
        "artifact_id",
        "forecast_id",
        "dataset_id",
        "calculation_spec_id",
        "calculation_run_id",
        "reused_from_run_id",
        "original_run_id",
    ):
        value = str(metadata.get(field) or "").strip()
        if value:
            values.append(value)
    for field in ("decision_snapshot_ref", "decision_artifact_ref", "origin_artifact_ref", "parent_spec_ref"):
        raw = metadata.get(field)
        if isinstance(raw, dict):
            values.extend(
                str(raw.get(key) or "").strip()
                for key in _IDENTITY_FIELDS
                if str(raw.get(key) or "").strip()
            )
    return sorted({value for value in values if value and value != own_id})


def _structured_search_text(value: Any, *, parent_key: str = "") -> str:
    strings: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            key_text = str(key)
            if key_text in _SEARCHABLE_STRUCTURED_FIELDS or parent_key in _SEARCHABLE_STRUCTURED_FIELDS:
                strings.append(_structured_search_text(item, parent_key=key_text))
    elif isinstance(value, list):
        strings.extend(_structured_search_text(item, parent_key=parent_key) for item in value)
    elif value is not None and isinstance(value, (str, int, float, bool)):
        strings.append(str(value))
    return " ".join(item for item in strings if item).casefold()


def _first_text(value: dict[str, Any], *fields: str) -> str:
    return next((str(value.get(field) or "").strip() for field in fields if str(value.get(field) or "").strip()), "")


def _normalized_timestamp(value: str, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc)

from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import stat
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from tradingcodex_service.application.analysis_runs import (
    ANALYSIS_RUNS_ROOT,
    analysis_run_relpath,
    read_analysis_run,
)
from tradingcodex_service.application.common import (
    atomic_write_text,
    exclusive_file_lock,
    file_hash,
    read_json,
    safe_workspace_path,
    stable_hash,
    write_json,
)
from tradingcodex_service.application.runtime import (
    WORKSPACE_MANIFEST_REL,
    persist_workspace_context_if_available,
    read_workspace_manifest,
    require_workspace_context_binding,
    tradingcodex_state_dir,
)


ARTIFACT_BINDING_SCHEMA_VERSION = 4
ARTIFACT_BINDING_DIR = "artifact-bindings"
ARTIFACT_BINDING_SIGNING_KEY_FILE = "artifact-receipt-signing.key"
ARTIFACT_BINDING_SIGNATURE_ALGORITHM = "hmac-sha256"
RESEARCH_ARTIFACT_ROOTS = (Path("trading/research"), Path("trading/reports"))
RUN_LINEAGE_FIELDS = (
    "strategy_name",
    "strategy_hash",
    "investment_brain_id",
    "investment_brain_version",
    "investment_brain_content_digest",
    "investor_context_applied",
    "investor_context_hash",
)
ARTIFACT_BINDING_FIELDS_V1 = {
    "schema_version",
    "marker",
    "workspace_id",
    "workflow_run_id",
    "run_record_hash",
    "artifact_id",
    "artifact_path",
    "artifact_version",
    "artifact_schema_version",
    "content_hash",
    "file_sha256",
    "role",
    "producer_role",
    "created_by",
    "artifact_recorded_at",
    "input_artifact_ids",
    "input_artifact_hashes",
    "input_artifact_versions",
    "source_snapshot_hashes",
    "run_lineage",
    "signature_algorithm",
    "receipt_hash",
}
ARTIFACT_BINDING_FIELDS_V2 = ARTIFACT_BINDING_FIELDS_V1 | {
    "calculation_run_ids",
    "calculation_run_hashes",
    "calculation_reuse_origins",
}
ARTIFACT_BINDING_FIELDS_V3 = ARTIFACT_BINDING_FIELDS_V2 | {
    "dataset_ids",
    "dataset_manifest_hashes",
}
ARTIFACT_BINDING_FIELDS_V4 = ARTIFACT_BINDING_FIELDS_V3 | {"memory_ref_hashes"}
ARTIFACT_BINDING_FIELDS = ARTIFACT_BINDING_FIELDS_V4
_AUTHENTICATED_SERVICE_BINDING_WRITE = object()
_PENDING_AUTHENTICATED_ARTIFACT_WRITE = object()
_HISTORICAL_ARCHIVE_VERIFICATION = object()


def authenticated_service_artifact_binding_args(
    artifact: dict[str, Any],
    *,
    expected_file_sha256: str,
    pending_write: bool = False,
) -> dict[str, Any]:
    """Mark an in-process binding write authorized by the MCP service.

    Object identity prevents this marker from arriving through JSON, Markdown,
    CLI arguments, or an MCP request payload.
    """

    if not re.fullmatch(r"[0-9a-f]{64}", expected_file_sha256):
        raise ValueError("authenticated artifact binding requires an expected file hash")
    authorized = {
        **artifact,
        "_expected_file_sha256": expected_file_sha256,
        "_service_authority": _AUTHENTICATED_SERVICE_BINDING_WRITE,
    }
    if pending_write:
        authorized["_pending_artifact_write"] = (
            _PENDING_AUTHENTICATED_ARTIFACT_WRITE
        )
    return authorized


def historical_archive_artifact_binding_args(
    artifact: dict[str, Any],
) -> dict[str, Any]:
    """Mark an exact artifact loaded by the canonical archive resolver."""

    return {
        **artifact,
        "_historical_archive_verification": _HISTORICAL_ARCHIVE_VERIFICATION,
    }


def record_authenticated_artifact_binding(
    workspace_root: Path | str,
    artifact: dict[str, Any],
) -> dict[str, str]:
    if artifact.get("_service_authority") is not _AUTHENTICATED_SERVICE_BINDING_WRITE:
        raise PermissionError(
            "authenticated artifact receipts require an authorized TradingCodex MCP write"
        )
    root = Path(workspace_root).expanduser().resolve()
    persist_workspace_context_if_available(root)
    material = _expected_receipt_material(root, artifact)
    receipt = _seal_receipt(root, material, create_signing_key=True)
    path = _receipt_path(root, receipt)
    with exclusive_file_lock(path):
        _require_regular_workspace_path(root, path.relative_to(root), require_file=False)
        existing = read_json(path, None)
        if existing is not None:
            matches = _validate_existing_receipt_for_pending_write(
                root,
                artifact,
                existing,
                receipt,
                path,
            )
        else:
            matches = False
        if not matches:
            write_json(path, receipt)
        _require_regular_workspace_path(root, path.relative_to(root), require_file=True)
    return {
        "path": path.relative_to(root).as_posix(),
        "receipt_hash": receipt["receipt_hash"],
    }


def prepare_authenticated_artifact_binding_receipt(
    workspace_root: Path | str,
    artifact: dict[str, Any],
) -> dict[str, Any]:
    """Validate a service-authorized binding and expose rollback-safe receipt state."""

    if artifact.get("_service_authority") is not _AUTHENTICATED_SERVICE_BINDING_WRITE:
        raise PermissionError(
            "authenticated artifact receipts require an authorized TradingCodex MCP write"
        )
    root = Path(workspace_root).expanduser().resolve()
    persist_workspace_context_if_available(root)
    material = _expected_receipt_material(root, artifact)
    receipt = _seal_receipt(root, material, create_signing_key=True)
    path = _receipt_path(root, receipt)
    _require_regular_workspace_path(root, path.relative_to(root), require_file=False)
    existing_bytes: bytes | None = None
    if path.exists():
        _require_regular_workspace_path(root, path.relative_to(root), require_file=True)
        existing = read_json(path, None)
        _validate_existing_receipt_for_pending_write(
            root,
            artifact,
            existing,
            receipt,
            path,
        )
        existing_bytes = path.read_bytes()
    return {
        "path": path,
        "existing_bytes": existing_bytes,
    }


def verify_authenticated_artifact_binding(
    workspace_root: Path | str,
    artifact: dict[str, Any],
    *,
    _verification_stack: frozenset[tuple[str, str, int, str]] = frozenset(),
) -> dict[str, Any]:
    root = Path(workspace_root).expanduser().resolve()
    require_workspace_context_binding(root)
    path = _receipt_path(root, artifact)
    _require_regular_workspace_path(root, path.relative_to(root), require_file=False)
    receipt = read_json(path, None)
    if receipt is None:
        raise ValueError(
            f"run-bound research artifact has no authenticated service receipt: {artifact.get('artifact_id', '')}"
        )
    _require_regular_workspace_path(root, path.relative_to(root), require_file=True)
    _validate_receipt_signature(root, receipt, path)
    verified_artifact = artifact
    if int(artifact.get("artifact_schema_version") or 1) >= 2:
        run_lineage = (
            receipt.get("run_lineage")
            if isinstance(receipt.get("run_lineage"), dict)
            else {}
        )
        verified_artifact = {
            **artifact,
            **run_lineage,
            "role": receipt.get("role", artifact.get("producer_role", "")),
            "producer_role": receipt.get("producer_role", artifact.get("producer_role", "")),
            "created_by": receipt.get("created_by", ""),
            "recorded_at": receipt.get("artifact_recorded_at", artifact.get("recorded_at", "")),
            "input_artifact_hashes": receipt.get("input_artifact_hashes", {}),
            "source_snapshot_hashes": receipt.get("source_snapshot_hashes", {}),
            "dataset_manifest_hashes": receipt.get("dataset_manifest_hashes", {}),
            "calculation_run_hashes": receipt.get("calculation_run_hashes", {}),
            "calculation_reuse_origins": receipt.get("calculation_reuse_origins", {}),
        }
    if (
        artifact.get("_historical_archive_verification")
        is _HISTORICAL_ARCHIVE_VERIFICATION
    ):
        verified_artifact = {
            **verified_artifact,
            "_receipt_artifact_path": receipt["artifact_path"],
        }
    material = _expected_receipt_material(
        root,
        verified_artifact,
        verification_stack=_verification_stack,
        sealed_input_versions=receipt["input_artifact_versions"],
    )
    expected = _seal_receipt(root, material, create_signing_key=False)
    _validate_receipt(root, receipt, expected, path)
    verification = {
        "status": "verified",
        "path": path.relative_to(root).as_posix(),
        "receipt_hash": receipt["receipt_hash"],
        "file_sha256": receipt["file_sha256"],
        "producer_role": receipt["producer_role"],
        "run_record_hash": receipt["run_record_hash"],
    }
    if int(receipt.get("schema_version") or 1) >= 4:
        verification["run_lineage"] = dict(receipt.get("run_lineage") or {})
        verification["input_artifact_hashes"] = dict(
            receipt.get("input_artifact_hashes") or {}
        )
        verification["input_artifact_versions"] = dict(
            receipt.get("input_artifact_versions") or {}
        )
    return verification


def verify_current_artifact_binding_before_append(
    workspace_root: Path | str,
    artifact: dict[str, Any],
) -> dict[str, Any] | None:
    """Verify a current run-bound artifact before any new version derives from it."""

    root = Path(workspace_root).expanduser().resolve()
    if str(artifact.get("workflow_run_id") or "").strip():
        return verify_authenticated_artifact_binding(root, artifact)
    if _authenticated_binding_history_matches(root, artifact):
        raise ValueError(
            "previously run-bound research artifact lost its authenticated workflow binding"
        )
    return None


def _authenticated_binding_history_matches(
    root: Path,
    artifact: dict[str, Any],
) -> bool:
    artifact_id = str(artifact.get("artifact_id") or "").strip()
    artifact_path = str(artifact.get("path") or artifact.get("export_path") or "").strip()
    if not artifact_id and not artifact_path:
        return False
    runs_root = _require_regular_workspace_path(root, ANALYSIS_RUNS_ROOT, require_file=False)
    if not runs_root.exists():
        return False
    try:
        run_directories = sorted(runs_root.iterdir())
    except OSError as exc:
        raise ValueError("authenticated artifact run history is unreadable") from exc
    for run_directory in run_directories:
        if run_directory.is_symlink():
            raise ValueError("authenticated artifact run history must not contain symlinks")
        binding_directory = run_directory / ARTIFACT_BINDING_DIR
        if not binding_directory.exists():
            continue
        _require_regular_workspace_path(
            root,
            binding_directory.relative_to(root),
            require_file=False,
        )
        try:
            receipt_paths = sorted(binding_directory.glob("*.json"))
        except OSError as exc:
            raise ValueError("authenticated artifact receipt history is unreadable") from exc
        for receipt_path in receipt_paths:
            _require_regular_workspace_path(
                root,
                receipt_path.relative_to(root),
                require_file=True,
            )
            receipt = read_json(receipt_path, None)
            if not isinstance(receipt, dict) or set(receipt) != set(
                _receipt_fields_for_schema(receipt.get("schema_version"))
            ):
                raise ValueError(
                    f"authenticated artifact receipt schema is invalid: {receipt_path}"
                )
            if (
                str(receipt.get("artifact_id") or "") != artifact_id
                and str(receipt.get("artifact_path") or "") != artifact_path
            ):
                continue
            claimed_hash = str(receipt.get("receipt_hash") or "")
            material = {
                key: value
                for key, value in receipt.items()
                if key != "receipt_hash"
            }
            expected_hash = _receipt_signature(
                root,
                material,
                create_signing_key=False,
            )
            if not claimed_hash or not hmac.compare_digest(expected_hash, claimed_hash):
                raise ValueError(
                    f"authenticated artifact receipt integrity check failed: {receipt_path}"
                )
            return True
    return False


def _expected_receipt_material(
    root: Path,
    artifact: dict[str, Any],
    *,
    verification_stack: frozenset[tuple[str, str, int, str]] = frozenset(),
    sealed_input_versions: dict[str, Any] | None = None,
) -> dict[str, Any]:
    _require_regular_workspace_path(root, Path(WORKSPACE_MANIFEST_REL), require_file=True)
    manifest = read_workspace_manifest(root)
    workspace_id = str(manifest.get("workspace_id") or "")
    if not re.fullmatch(r"tcxw_[0-9a-f]{32}", workspace_id):
        raise ValueError("authenticated artifact binding requires a valid workspace identity")
    run_id = str(artifact.get("workflow_run_id") or "").strip()
    artifact_id = str(artifact.get("artifact_id") or "").strip()
    if not run_id or not artifact_id:
        raise ValueError("authenticated artifact binding requires workflow_run_id and artifact_id")
    run_relative = analysis_run_relpath(run_id)
    _require_regular_workspace_path(root, run_relative, require_file=True)
    run = read_analysis_run(root, run_id)
    if not run or str(run.get("workflow_run_id") or "") != run_id:
        raise ValueError("authenticated artifact binding requires a recorded analysis run")
    run_record_hash = str(run.get("record_hash") or "")
    if not re.fullmatch(r"[0-9a-f]{64}", run_record_hash):
        raise ValueError("authenticated artifact binding requires a valid run record hash")
    content_hash = str(artifact.get("content_hash") or "")
    if not re.fullmatch(r"[0-9a-f]{64}", content_hash):
        raise ValueError("authenticated artifact binding requires a valid body content hash")
    version = artifact.get("version")
    if type(version) is not int or version < 1:
        raise ValueError("authenticated artifact binding requires a positive artifact version")
    verification_key = (run_id, artifact_id, version, content_hash)
    if verification_key in verification_stack:
        raise ValueError("authenticated artifact input lineage contains a cycle")
    next_verification_stack = verification_stack | {verification_key}
    historical_archive = (
        artifact.get("_historical_archive_verification")
        is _HISTORICAL_ARCHIVE_VERIFICATION
    )
    pending_write = (
        artifact.get("_pending_artifact_write")
        is _PENDING_AUTHENTICATED_ARTIFACT_WRITE
    )
    if pending_write and (
        artifact.get("_service_authority")
        is not _AUTHENTICATED_SERVICE_BINDING_WRITE
    ):
        raise PermissionError(
            "pending authenticated artifact bindings require service authority"
        )
    if pending_write and historical_archive:
        raise ValueError("pending authenticated artifacts cannot be historical archives")
    raw_path = str(artifact.get("path") or artifact.get("export_path") or "")
    receipt_artifact_path = str(
        artifact.get("_receipt_artifact_path") or raw_path
    )
    artifact_path = _require_regular_workspace_path(
        root,
        Path(receipt_artifact_path),
        require_file=not historical_archive and not pending_write,
    )
    allowed_artifact_path = safe_workspace_path(
        root,
        receipt_artifact_path,
        allowed_roots=RESEARCH_ARTIFACT_ROOTS,
    )
    if artifact_path != allowed_artifact_path:
        raise ValueError("authenticated artifact path changed during validation")
    expected_file_hash = str(artifact.get("_expected_file_sha256") or "")
    if pending_write:
        if not re.fullmatch(r"[0-9a-f]{64}", expected_file_hash):
            raise ValueError(
                "pending authenticated artifact binding requires an intended file hash"
            )
        artifact_file_hash = expected_file_hash
    else:
        verification_path = (
            _require_regular_workspace_path(root, Path(raw_path), require_file=True)
            if historical_archive
            else artifact_path
        )
        verification_allowed = safe_workspace_path(
            root,
            raw_path,
            allowed_roots=RESEARCH_ARTIFACT_ROOTS,
        )
        if verification_path != verification_allowed:
            raise ValueError("authenticated artifact verification path changed")
        artifact_file_hash = file_hash(verification_path)
        if not artifact_file_hash:
            raise ValueError(
                "authenticated artifact binding requires a stored artifact file"
            )
        if expected_file_hash and not hmac.compare_digest(
            artifact_file_hash,
            expected_file_hash,
        ):
            raise ValueError("stored research artifact changed before receipt creation")
    input_ids = artifact.get("input_artifact_ids")
    if not isinstance(input_ids, list):
        raise ValueError("authenticated artifact binding requires array input_artifact_ids")
    normalized_input_ids = [str(value).strip() for value in input_ids]
    if any(not value for value in normalized_input_ids):
        raise ValueError("authenticated artifact input_artifact_ids must be non-empty strings")
    if len(normalized_input_ids) != len(set(normalized_input_ids)):
        raise ValueError("authenticated artifact input_artifact_ids must not contain duplicates")
    input_hashes = artifact.get("input_artifact_hashes")
    if not isinstance(input_hashes, dict):
        raise ValueError("authenticated artifact binding requires object input_artifact_hashes")
    normalized_input_hashes = {
        str(key).strip(): str(value).strip()
        for key, value in input_hashes.items()
    }
    if set(normalized_input_ids) != set(normalized_input_hashes):
        raise ValueError("authenticated artifact input ids and hashes must match exactly")
    if any(
        not key or not re.fullmatch(r"[0-9a-f]{64}", value)
        for key, value in normalized_input_hashes.items()
    ):
        raise ValueError("authenticated artifact input hashes must be SHA-256 values")
    if artifact_id in normalized_input_ids:
        raise ValueError("authenticated artifact cannot consume itself as an input")
    normalized_input_versions: dict[str, int] = {}
    if sealed_input_versions is not None:
        if not isinstance(sealed_input_versions, dict):
            raise ValueError("authenticated artifact input versions must be an object")
        if set(sealed_input_versions) != set(normalized_input_ids):
            raise ValueError("authenticated artifact input ids and versions must match exactly")
        for key, value in sealed_input_versions.items():
            if type(value) is not int or value < 1:
                raise ValueError("authenticated artifact input versions must be positive integers")
            normalized_input_versions[str(key)] = value
    if normalized_input_ids:
        from tradingcodex_service.application.research import (
            find_workspace_research_artifact_read_only,
            find_workspace_research_artifact_version,
        )

        for input_id in normalized_input_ids:
            if sealed_input_versions is None:
                input_artifact = find_workspace_research_artifact_read_only(
                    root,
                    input_id,
                )
            else:
                input_artifact = find_workspace_research_artifact_version(
                    root,
                    input_id,
                    version=normalized_input_versions[input_id],
                    content_hash=normalized_input_hashes[input_id],
                )
            if not input_artifact:
                raise ValueError(f"authenticated input research artifact is missing: {input_id}")
            if str(input_artifact.get("workflow_run_id") or "") != run_id:
                raise ValueError(f"authenticated input artifact belongs to another run: {input_id}")
            if str(input_artifact.get("content_hash") or "") != normalized_input_hashes[input_id]:
                raise ValueError(f"authenticated input artifact content hash changed: {input_id}")
            input_version = input_artifact.get("version")
            if type(input_version) is not int or input_version < 1:
                raise ValueError(f"authenticated input artifact version is invalid: {input_id}")
            if sealed_input_versions is None:
                normalized_input_versions[input_id] = input_version
            elif input_version != normalized_input_versions[input_id]:
                raise ValueError(f"authenticated input artifact version changed: {input_id}")
            verify_authenticated_artifact_binding(
                root,
                input_artifact,
                _verification_stack=next_verification_stack,
            )
    artifact_schema_version = artifact.get("artifact_schema_version")
    if type(artifact_schema_version) is not int or artifact_schema_version < 1:
        raise ValueError("authenticated artifact binding requires a positive schema version")
    from tradingcodex_service.application.research import (
        validated_research_artifact_data_lineage,
        validated_source_snapshot_hashes,
    )

    source_snapshot_ids = artifact.get("source_snapshot_ids")
    if not isinstance(source_snapshot_ids, list):
        raise ValueError("authenticated artifact binding requires array source_snapshot_ids")
    current_snapshot_hashes = validated_source_snapshot_hashes(
        root,
        source_snapshot_ids,
        str(artifact.get("knowledge_cutoff") or ""),
    )
    source_snapshot_hashes = artifact.get("source_snapshot_hashes")
    if source_snapshot_hashes != current_snapshot_hashes:
        raise ValueError(
            "authenticated artifact source snapshot ids and hashes must match current snapshots"
        )
    dataset_ids = artifact.get("dataset_ids", [])
    dataset_hashes = validated_research_artifact_data_lineage(
        root,
        dataset_ids=dataset_ids,
        knowledge_cutoff=str(artifact.get("knowledge_cutoff") or ""),
    )
    if artifact.get("dataset_manifest_hashes", {}) != dataset_hashes:
        raise ValueError(
            "authenticated artifact Dataset ids and manifest hashes must match "
            "current Datasets"
        )
    calculation_run_ids = artifact.get("calculation_run_ids", [])
    if not isinstance(calculation_run_ids, list):
        raise ValueError("authenticated artifact binding requires array calculation_run_ids")
    normalized_calculation_run_ids = [str(value).strip() for value in calculation_run_ids]
    if any(not value for value in normalized_calculation_run_ids):
        raise ValueError("authenticated artifact calculation_run_ids must be non-empty strings")
    if len(normalized_calculation_run_ids) != len(set(normalized_calculation_run_ids)):
        raise ValueError("authenticated artifact calculation_run_ids must not contain duplicates")
    calculation_hashes: dict[str, str] = {}
    calculation_reuse_origins: dict[str, dict[str, str]] = {}
    if normalized_calculation_run_ids:
        from tradingcodex_service.application.calculations import (
            verify_calculation_run_binding,
        )

        calculation_bindings = [
            verify_calculation_run_binding(
                root,
                calculation_run_id,
                workflow_run_id=run_id,
                knowledge_cutoff=str(artifact.get("knowledge_cutoff") or ""),
            )
            for calculation_run_id in normalized_calculation_run_ids
        ]
        calculation_hashes = {
            item["calculation_run_id"]: item["run_sha256"]
            for item in calculation_bindings
        }
        calculation_reuse_origins = {
            item["calculation_run_id"]: {
                "original_run_id": item["original_run_id"],
                "original_run_sha256": item["original_run_sha256"],
            }
            for item in calculation_bindings
            if item["original_run_id"]
        }
    if artifact.get("calculation_run_hashes", {}) != calculation_hashes:
        raise ValueError(
            "authenticated artifact calculation run ids and hashes must match current runs"
        )
    if artifact.get("calculation_reuse_origins", {}) != calculation_reuse_origins:
        raise ValueError(
            "authenticated artifact calculation reuse origins must match current runs"
        )
    has_data_lineage = bool(dataset_ids)
    if artifact_schema_version >= 2:
        receipt_schema_version = ARTIFACT_BINDING_SCHEMA_VERSION
    elif has_data_lineage:
        receipt_schema_version = 3
    elif normalized_calculation_run_ids:
        receipt_schema_version = 2
    else:
        receipt_schema_version = 1
    receipt = {
        "schema_version": receipt_schema_version,
        "marker": "tradingcodex-authenticated-research-artifact",
        "workspace_id": workspace_id,
        "workflow_run_id": run_id,
        "run_record_hash": run_record_hash,
        "artifact_id": artifact_id,
        "artifact_path": artifact_path.relative_to(root).as_posix(),
        "artifact_version": version,
        "artifact_schema_version": artifact_schema_version,
        "content_hash": content_hash,
        "file_sha256": artifact_file_hash,
        "role": str(artifact.get("role") or ""),
        "producer_role": str(artifact.get("producer_role") or ""),
        "created_by": str(artifact.get("created_by") or ""),
        "artifact_recorded_at": str(artifact.get("recorded_at") or ""),
        "input_artifact_ids": normalized_input_ids,
        "input_artifact_hashes": {
            key: normalized_input_hashes[key]
            for key in sorted(normalized_input_hashes)
        },
        "input_artifact_versions": {
            key: normalized_input_versions[key]
            for key in sorted(normalized_input_versions)
        },
        "source_snapshot_hashes": current_snapshot_hashes,
        "run_lineage": {field: artifact.get(field) for field in RUN_LINEAGE_FIELDS},
        "signature_algorithm": ARTIFACT_BINDING_SIGNATURE_ALGORITHM,
    }
    if artifact_schema_version >= 2:
        receipt["memory_ref_hashes"] = _memory_ref_hashes(
            root,
            artifact.get("memory"),
            str(artifact.get("knowledge_cutoff") or ""),
        )
    if artifact_schema_version >= 2 or normalized_calculation_run_ids or has_data_lineage:
        receipt.update(
            {
                "calculation_run_ids": normalized_calculation_run_ids,
                "calculation_run_hashes": {
                    key: calculation_hashes[key]
                    for key in sorted(calculation_hashes)
                },
                "calculation_reuse_origins": {
                    key: calculation_reuse_origins[key]
                    for key in sorted(calculation_reuse_origins)
                },
            }
        )
    if artifact_schema_version >= 2 or has_data_lineage:
        receipt.update(
            {
                "dataset_ids": list(dataset_ids),
                "dataset_manifest_hashes": {
                    key: dataset_hashes[key]
                    for key in sorted(dataset_hashes)
                },
            }
        )
    if not receipt["producer_role"] or receipt["role"] != receipt["producer_role"]:
        raise ValueError("authenticated artifact binding requires matching role and producer_role")
    if not receipt["created_by"] or not receipt["artifact_recorded_at"]:
        raise ValueError("authenticated artifact binding requires creator and recorded_at")
    return receipt


def _memory_ref_hashes(root: Path, memory: Any, artifact_cutoff: str) -> dict[str, str]:
    if memory in (None, {}):
        return {}
    if not isinstance(memory, dict):
        raise ValueError("artifact memory must be an object")
    cutoff = _receipt_datetime(str(memory.get("cutoff") or ""), "memory cutoff")
    if artifact_cutoff and cutoff > _receipt_datetime(artifact_cutoff, "artifact knowledge_cutoff"):
        raise ValueError("memory cutoff must not exceed artifact knowledge_cutoff")
    refs = memory.get("refs")
    if not isinstance(refs, list) or not refs:
        raise ValueError("artifact memory refs must be a non-empty array")
    hashes: dict[str, str] = {}
    for raw in refs:
        if not isinstance(raw, dict):
            raise ValueError("artifact memory refs must contain objects")
        kind = str(raw.get("kind") or "")
        ref_id = str(raw.get("id") or "")
        if kind == "judgment_snapshot":
            from tradingcodex_service.application.judgments import get_judgment_snapshot

            record = get_judgment_snapshot(root, ref_id)["judgment_snapshot"]
            digest = str(record.get("snapshot_hash") or "")
            known_at = str(record.get("recorded_at") or "")
        elif kind == "decision_snapshot":
            from tradingcodex_service.application.decision_packages import verify_decision_snapshot

            record = verify_decision_snapshot(root, ref_id)["decision_snapshot"]
            digest = str(record.get("snapshot_hash") or "")
            known_at = str(record.get("recorded_at") or record.get("created_at") or "")
        elif kind == "postmortem":
            from tradingcodex_service.application.postmortems import get_postmortem

            record = get_postmortem(root, ref_id)["postmortem"]
            digest = str(record.get("report_hash") or "")
            known_at = str(record.get("known_at") or record.get("recorded_at") or "")
        elif kind == "lesson":
            from tradingcodex_service.application.postmortems import verified_lesson_records

            record = next(
                (item for item in verified_lesson_records(root) if item.get("lesson_id") == ref_id),
                None,
            )
            if record is None:
                raise ValueError(f"memory lesson reference is unavailable: {ref_id}")
            digest = str(record.get("lesson_event_hash") or "")
            known_at = str(record.get("known_at") or record.get("recorded_at") or "")
        else:
            raise ValueError(f"unsupported memory reference kind: {kind}")
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError(f"memory reference has no canonical hash: {kind}:{ref_id}")
        if known_at and _receipt_datetime(known_at, f"memory ref {kind}:{ref_id} known_at") > cutoff:
            raise ValueError(f"memory reference was not known at cutoff: {kind}:{ref_id}")
        hashes[f"{kind}:{ref_id}"] = digest
    return {key: hashes[key] for key in sorted(hashes)}

def _receipt_datetime(value: str, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be RFC 3339") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must include timezone")
    return parsed


def _seal_receipt(
    root: Path,
    material: dict[str, Any],
    *,
    create_signing_key: bool,
) -> dict[str, Any]:
    receipt = dict(material)
    receipt["receipt_hash"] = _receipt_signature(
        root,
        material,
        create_signing_key=create_signing_key,
    )
    return receipt


def _receipt_path(root: Path, receipt: dict[str, Any]) -> Path:
    artifact_version = receipt.get("artifact_version", receipt.get("version"))
    if artifact_version in (None, ""):
        raise ValueError("authenticated artifact binding requires an artifact version")
    artifact_key = hashlib.sha256(str(receipt["artifact_id"]).encode("utf-8")).hexdigest()[:24]
    filename = (
        f"{artifact_key}-v{artifact_version}-"
        f"{str(receipt['content_hash'])[:16]}.json"
    )
    relative = (
        ANALYSIS_RUNS_ROOT
        / str(receipt["workflow_run_id"])
        / ARTIFACT_BINDING_DIR
        / filename
    )
    return _require_regular_workspace_path(root, relative, require_file=False)


def _validate_receipt(
    root: Path,
    value: Any,
    expected: dict[str, Any],
    path: Path,
) -> None:
    _validate_receipt_signature(root, value, path)
    if value != expected:
        raise ValueError(f"authenticated artifact receipt does not match the current artifact: {path}")


def _validate_existing_receipt_for_pending_write(
    root: Path,
    artifact: dict[str, Any],
    existing: Any,
    expected: dict[str, Any],
    path: Path,
) -> bool:
    """Accept an exact receipt or a signed but unpublished future receipt."""

    _validate_receipt_signature(root, existing, path)
    if existing == expected:
        return True
    pending_write = (
        artifact.get("_pending_artifact_write")
        is _PENDING_AUTHENTICATED_ARTIFACT_WRITE
    )
    identity_fields = (
        "schema_version",
        "marker",
        "workspace_id",
        "workflow_run_id",
        "artifact_id",
        "artifact_path",
        "artifact_version",
        "content_hash",
        "signature_algorithm",
    )
    if not pending_write or any(
        existing.get(field) != expected.get(field) for field in identity_fields
    ):
        raise ValueError(
            f"authenticated artifact receipt does not match the current artifact: {path}"
        )

    from tradingcodex_service.application.research import (
        find_workspace_research_artifact_read_only,
    )

    current = find_workspace_research_artifact_read_only(
        root,
        str(expected["artifact_id"]),
    )
    if current and (
        current.get("version") == existing.get("artifact_version")
        and str(current.get("content_hash") or "")
        == str(existing.get("content_hash") or "")
    ):
        raise ValueError(
            f"authenticated artifact receipt does not match the current artifact: {path}"
        )
    return False


def _validate_receipt_signature(
    root: Path,
    value: Any,
    path: Path,
) -> None:
    if not isinstance(value, dict):
        raise ValueError(f"authenticated artifact receipt schema is invalid: {path}")
    expected_fields = _receipt_fields_for_schema(value.get("schema_version"))
    if set(value) != set(expected_fields):
        raise ValueError(f"authenticated artifact receipt schema is invalid: {path}")
    claimed_hash = str(value.get("receipt_hash") or "")
    material = {key: item for key, item in value.items() if key != "receipt_hash"}
    expected_signature = _receipt_signature(root, material, create_signing_key=False)
    if not claimed_hash or not hmac.compare_digest(expected_signature, claimed_hash):
        raise ValueError(f"authenticated artifact receipt integrity check failed: {path}")


def _receipt_fields_for_schema(schema_version: Any) -> set[str]:
    if schema_version == 1:
        return ARTIFACT_BINDING_FIELDS_V1
    if schema_version == 2:
        return ARTIFACT_BINDING_FIELDS_V2
    if schema_version == 3:
        return ARTIFACT_BINDING_FIELDS_V3
    if schema_version == ARTIFACT_BINDING_SCHEMA_VERSION:
        return ARTIFACT_BINDING_FIELDS_V4
    return set()


def _receipt_signature(
    root: Path,
    material: dict[str, Any],
    *,
    create_signing_key: bool,
) -> str:
    key = _receipt_signing_key(root, create=create_signing_key)
    return hmac.new(key, stable_hash(material).encode("ascii"), hashlib.sha256).hexdigest()


def _receipt_signing_key(root: Path, *, create: bool) -> bytes:
    raw_state_dir = tradingcodex_state_dir().expanduser()
    if raw_state_dir.is_symlink():
        raise ValueError("artifact receipt signing key path must not contain a symlink")
    state_dir = raw_state_dir.resolve(strict=False)
    key_path = state_dir / ARTIFACT_BINDING_SIGNING_KEY_FILE
    try:
        key_path.relative_to(root)
    except ValueError:
        pass
    else:
        raise ValueError("artifact receipt signing key must be outside the workspace")

    state_dir.mkdir(parents=True, exist_ok=True)
    if state_dir.is_symlink() or key_path.is_symlink():
        raise ValueError("artifact receipt signing key path must not contain a symlink")
    with exclusive_file_lock(key_path):
        if key_path.is_symlink():
            raise ValueError("artifact receipt signing key must not be a symlink")
        if not key_path.exists():
            if not create:
                raise ValueError("artifact receipt signing key is unavailable")
            atomic_write_text(key_path, secrets.token_hex(32) + "\n")
        try:
            mode = key_path.stat().st_mode
            raw = key_path.read_text(encoding="ascii").strip()
        except (OSError, UnicodeError) as exc:
            raise ValueError("artifact receipt signing key is unreadable") from exc
        if not stat.S_ISREG(mode) or not re.fullmatch(r"[0-9a-f]{64}", raw):
            raise ValueError("artifact receipt signing key is invalid")
        if os.name != "nt":
            if stat.S_IMODE(mode) & 0o077:
                raise ValueError("artifact receipt signing key permissions are too broad")
            os.chmod(key_path, 0o600)
    return bytes.fromhex(raw)


def _require_regular_workspace_path(
    root: Path,
    relative: Path,
    *,
    require_file: bool,
) -> Path:
    text = str(relative).strip()
    path = Path(text)
    if path.is_absolute():
        try:
            path = path.relative_to(root)
        except ValueError as exc:
            raise ValueError("authenticated artifact path escapes the workspace") from exc
    current = root
    for part in path.parts:
        current /= part
        try:
            mode = current.lstat().st_mode
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise ValueError(f"authenticated artifact path is unreadable: {path}") from exc
        if stat.S_ISLNK(mode):
            raise ValueError(f"authenticated artifact path must not contain symlinks: {path}")
    candidate = safe_workspace_path(root, path)
    if require_file:
        try:
            mode = candidate.stat().st_mode
        except FileNotFoundError as exc:
            raise ValueError(f"authenticated artifact file does not exist: {path}") from exc
        except OSError as exc:
            raise ValueError(f"authenticated artifact file is unreadable: {path}") from exc
        if not stat.S_ISREG(mode):
            raise ValueError(f"authenticated artifact path is not a regular file: {path}")
    return candidate

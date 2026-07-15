from __future__ import annotations

import hashlib
import hmac
import json
import re
import stat
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tradingcodex_service.application.common import atomic_write_text, exclusive_file_lock, file_hash, now_iso, safe_workspace_path, sanitize_id, stable_hash
from tradingcodex_service.application.markdown_preview import split_markdown_frontmatter
from tradingcodex_service.application.research_specs import EVIDENCE_LANES
from tradingcodex_service.application.runtime import workspace_context_payload
from tradingcodex_service.application.source_snapshots import (
    SOURCE_SNAPSHOT_SCHEMA_VERSION,
    source_snapshot_id,
    validate_source_snapshot,
)

RESEARCH_FILE_ROOTS = (Path("trading/research"), Path("trading/reports"))
SOURCE_SNAPSHOT_ROOT = Path("trading/research/source-snapshots")
SOURCE_SNAPSHOT_ROOTS = (SOURCE_SNAPSHOT_ROOT,)
RESEARCH_INDEX_PATH = Path("trading/research/.index/research-index.json")
RESEARCH_INDEX_VERSION = 1
RESEARCH_DRAFT_ROOT = Path("trading/research/.drafts")
WORKFLOW_ARTIFACT_ROOTS = RESEARCH_FILE_ROOTS + (Path("trading/decisions"),)
ANTI_OVERFIT_CHECK_KEYS = (
    "leakage",
    "survivorship_bias",
    "data_snooping",
    "out_of_sample",
    "walk_forward_consistency",
    "monte_carlo_permutation",
    "bootstrap_sharpe_ci",
    "cost_assumptions",
    "capacity",
    "live_friction",
)
ROLE_REPORT_DIRECTORIES = {
    "head-manager": "head-manager",
    "fundamental-analyst": "fundamental",
    "technical-analyst": "technical",
    "news-analyst": "news",
    "macro-analyst": "macro",
    "instrument-analyst": "instrument",
    "valuation-analyst": "valuation",
    "judgment-reviewer": "judgment",
    "portfolio-manager": "portfolio",
    "risk-manager": "risk",
}
_AUTHENTICATED_SERVICE_WRITE = object()
_ATOMIC_AUTHENTICATED_RECEIPT = object()


def authenticated_service_research_args(args: dict[str, Any]) -> dict[str, Any]:
    """Mark an in-process, principal-authorized MCP write.

    The marker is an object identity, so it cannot arrive through CLI, JSON,
    YAML, Markdown frontmatter, or an MCP request payload.
    """

    return {**args, "_service_authority": _AUTHENTICATED_SERVICE_WRITE}


def store_authenticated_research_artifact(
    workspace_root: Path | str,
    args: dict[str, Any],
    *,
    append: bool = False,
) -> dict[str, Any]:
    """Atomically store a principal-authorized artifact and its run receipt."""

    if args.get("_service_authority") is not _AUTHENTICATED_SERVICE_WRITE:
        raise PermissionError(
            "authenticated research storage requires TradingCodex service authority"
        )
    payload = {
        **args,
        "_atomic_authenticated_receipt": _ATOMIC_AUTHENTICATED_RECEIPT,
    }
    if append:
        return append_research_artifact_version(workspace_root, payload)
    return create_research_artifact(workspace_root, payload)


def validate_research_artifact_path_declarations(
    canonical_path: str,
    *sources: dict[str, Any],
    operation: str,
) -> None:
    """Reject caller-authored path aliases that disagree with canonical identity."""

    for source in sources:
        if not isinstance(source, dict):
            continue
        for field in ("path", "export_path"):
            value = source.get(field)
            if value in (None, ""):
                continue
            if str(value) != canonical_path:
                raise ValueError(
                    f"{operation} research artifact {field} must match its "
                    f"canonical path: {canonical_path}"
                )


def _validate_research_artifact_destination(
    root: Path,
    path: Path,
    artifact_id: str,
) -> None:
    """Prevent a create or append from overwriting another path identity."""

    if not path.exists():
        return
    try:
        mode = path.lstat().st_mode
    except OSError as exc:
        raise ValueError("research artifact destination is unreadable") from exc
    if not stat.S_ISREG(mode):
        raise ValueError("research artifact destination must be a regular file")
    try:
        stored = _research_file_payload(root, path, include_markdown=False)
    except (OSError, UnicodeError, ValueError) as exc:
        raise ValueError(
            "research artifact destination is occupied by an invalid artifact"
        ) from exc
    stored_id = str(stored.get("artifact_id") or "")
    if stored_id != artifact_id:
        raise ValueError(
            "research artifact destination belongs to another artifact: "
            f"{stored_id}"
        )
    canonical_path = path.relative_to(root).as_posix()
    frontmatter, _, _ = _research_file_parts(path)
    validate_research_artifact_path_declarations(
        canonical_path,
        frontmatter,
        operation="stored",
    )


def list_workflow_artifacts(workspace_root: Path | str) -> dict[str, Any]:
    root = Path(workspace_root)
    files = []
    for prefix in ["trading/research", "trading/reports", "trading/decisions"]:
        base = root / prefix
        if base.exists():
            for path in base.rglob("*"):
                relative = path.relative_to(root)
                if path.is_file() and not any(
                    part.startswith(".") for part in relative.parts
                ):
                    files.append(relative.as_posix())
    return {
        "artifacts": sorted(files),
        "research_artifacts": list_research_artifacts(root, {"include_markdown": False}).get("artifacts", []),
        "workspace_native": True,
        "workspace_context": workspace_context_payload(root),
    }


def create_research_artifact(workspace_root: Path | str, args: dict[str, Any]) -> dict[str, Any]:
    root = Path(workspace_root)
    if "created_by" in args:
        raise ValueError("created_by is derived from principal_id")
    markdown = args.get("markdown")
    markdown_path = args.get("markdown_path")
    if not markdown and markdown_path:
        markdown = safe_workspace_path(root, markdown_path, allowed_roots=RESEARCH_FILE_ROOTS).read_text(encoding="utf-8")
    if not markdown:
        raise ValueError("research artifact markdown is required")

    source_document = split_markdown_frontmatter(str(markdown))
    source_frontmatter = source_document.frontmatter
    markdown_body = source_document.body or str(markdown)
    artifact_type = str(args.get("artifact_type") or source_frontmatter.get("artifact_type") or "research_memo")
    title = str(args.get("title") or source_frontmatter.get("title") or source_document.heading or args.get("artifact_id") or "Untitled research artifact")
    symbol = str(args.get("symbol") or source_frontmatter.get("symbol") or "").upper()
    universe = str(args.get("universe") or source_frontmatter.get("universe") or "").strip()
    if not universe:
        raise ValueError("research artifact universe is required")
    content_hash = hashlib.sha256(markdown_body.encode("utf-8")).hexdigest()
    artifact_id = str(args.get("artifact_id") or source_frontmatter.get("artifact_id") or f"{sanitize_id(artifact_type)}-{sanitize_id(symbol or title)}-{content_hash[:12]}")
    metadata = args.get("metadata") if isinstance(args.get("metadata"), dict) else {}
    if args.get("role") and not metadata.get("role"):
        metadata = {**metadata, "role": args.get("role")}
    append_version = bool(args.get("_append_version"))
    atomic_authenticated_receipt = (
        args.get("_atomic_authenticated_receipt") is _ATOMIC_AUTHENTICATED_RECEIPT
    )
    existing = (
        find_workspace_research_artifact_read_only(root, artifact_id)
        if append_version or atomic_authenticated_receipt
        else find_workspace_research_artifact(root, artifact_id)
    )
    run_bound = any(
        source.get("workflow_run_id") not in (None, "")
        for source in (args, metadata, source_frontmatter, existing or {})
        if isinstance(source, dict)
    )
    if run_bound and args.get("_service_authority") is not _AUTHENTICATED_SERVICE_WRITE:
        raise PermissionError(
            "run-bound research artifacts require an authenticated TradingCodex MCP principal"
        )
    if run_bound and not atomic_authenticated_receipt:
        raise PermissionError(
            "run-bound research storage must atomically commit its authenticated receipt"
        )
    created_by = str(
        args.get("principal_id")
        or (existing.get("created_by") if existing else "")
        or source_frontmatter.get("created_by")
        or "system"
    )
    recorded_at = (
        str(existing.get("recorded_at") or "")
        if existing and not args.get("_append_version")
        else now_iso()
    ) or now_iso()
    if existing:
        canonical_existing_path = str(
            existing.get("path") or existing.get("export_path") or ""
        )
        if not canonical_existing_path:
            raise ValueError("existing research artifact has no canonical path")
        validate_research_artifact_path_declarations(
            canonical_existing_path,
            args,
            metadata,
            source_frontmatter,
            operation="append" if append_version else "create",
        )
        requested_export_path = canonical_existing_path
    else:
        requested_export_path = str(
            args.get("export_path")
            or default_research_export_path_from_values(
                artifact_id,
                artifact_type,
                metadata,
            )
        )
    path = safe_workspace_path(
        root,
        requested_export_path,
        allowed_roots=RESEARCH_FILE_ROOTS,
    )
    resolved_root = root.expanduser().resolve(strict=False)
    export_path = path.relative_to(resolved_root).as_posix()
    if requested_export_path != export_path:
        raise ValueError(
            f"research artifact export_path must be canonical: {export_path}"
        )
    validate_research_artifact_path_declarations(
        export_path,
        args,
        metadata,
        source_frontmatter,
        operation="append" if append_version else "create",
    )
    _validate_research_artifact_destination(
        resolved_root,
        path,
        artifact_id,
    )
    frontmatter = {
        **source_frontmatter,
        "artifact_id": artifact_id,
        "artifact_type": artifact_type,
        "universe": universe,
        "workflow_type": args.get("workflow_type") or source_frontmatter.get("workflow_type") or "",
        "role": args.get("role") or metadata.get("role") or source_frontmatter.get("role") or (existing.get("role") if existing else ""),
        "symbol": symbol,
        "title": title,
        "source_as_of": args.get("source_as_of") or source_frontmatter.get("source_as_of") or "",
        "readiness_label": args.get("readiness_label") or source_frontmatter.get("readiness_label") or "",
        "context_summary": _frontmatter_value(args, metadata, source_frontmatter, "context_summary", ""),
        "reader_summary": _frontmatter_value(args, metadata, source_frontmatter, "reader_summary", ""),
        "handoff_state": _frontmatter_value(args, metadata, source_frontmatter, "handoff_state", ""),
        "confidence": _frontmatter_value(args, metadata, source_frontmatter, "confidence", ""),
        "missing_evidence": _frontmatter_list(args, metadata, source_frontmatter, "missing_evidence"),
        "next_recipient": _frontmatter_value(args, metadata, source_frontmatter, "next_recipient", ""),
        "next_action": _frontmatter_value(args, metadata, source_frontmatter, "next_action", ""),
        "blocked_actions": _frontmatter_list(args, metadata, source_frontmatter, "blocked_actions"),
        "source_snapshot_ids": _frontmatter_list(args, metadata, source_frontmatter, "source_snapshot_ids"),
        "source_snapshot_hashes": _frontmatter_value(
            args,
            metadata,
            source_frontmatter,
            "source_snapshot_hashes",
            existing.get("source_snapshot_hashes") if existing else {},
        ),
        "evidence_lane": _frontmatter_value(args, metadata, source_frontmatter, "evidence_lane", ""),
        "research_spec_id": _frontmatter_value(args, metadata, source_frontmatter, "research_spec_id", ""),
        "replay_manifest_id": _frontmatter_value(args, metadata, source_frontmatter, "replay_manifest_id", ""),
        "decision_snapshot_id": _frontmatter_value(args, metadata, source_frontmatter, "decision_snapshot_id", ""),
        "strategy_name": _frontmatter_value(args, metadata, source_frontmatter, "strategy_name", ""),
        "strategy_hash": _frontmatter_value(args, metadata, source_frontmatter, "strategy_hash", ""),
        "investment_brain_id": _frontmatter_value(args, metadata, source_frontmatter, "investment_brain_id", ""),
        "investment_brain_version": _frontmatter_value(args, metadata, source_frontmatter, "investment_brain_version", ""),
        "investment_brain_content_digest": _frontmatter_value(
            args,
            metadata,
            source_frontmatter,
            "investment_brain_content_digest",
            "",
        ),
        "investor_context_applied": bool(_frontmatter_value(args, metadata, source_frontmatter, "investor_context_applied", False)),
        "investor_context_hash": _frontmatter_value(args, metadata, source_frontmatter, "investor_context_hash", ""),
        "decision_memory_consulted": bool(_frontmatter_value(args, metadata, source_frontmatter, "decision_memory_consulted", False)),
        "decision_memory_cutoff": _frontmatter_value(args, metadata, source_frontmatter, "decision_memory_cutoff", ""),
        "forecast_required": _frontmatter_value(args, metadata, source_frontmatter, "forecast_required", False),
        "decision_quality_required": _frontmatter_value(args, metadata, source_frontmatter, "decision_quality_required", False),
        "investor_context_gate_required": _frontmatter_value(args, metadata, source_frontmatter, "investor_context_gate_required", False),
        "anti_overfit_required": _frontmatter_value(args, metadata, source_frontmatter, "anti_overfit_required", False),
        "anti_overfit_checks": _frontmatter_value(args, metadata, source_frontmatter, "anti_overfit_checks", {}),
        "forecast_allowed": _frontmatter_value(args, metadata, source_frontmatter, "forecast_allowed", ""),
        "forecast_block_reason": _frontmatter_value(args, metadata, source_frontmatter, "forecast_block_reason", ""),
        "forecast_target": _frontmatter_value(args, metadata, source_frontmatter, "forecast_target", ""),
        "forecast_horizon": _frontmatter_value(args, metadata, source_frontmatter, "forecast_horizon", ""),
        "probability": _frontmatter_value(args, metadata, source_frontmatter, "probability", ""),
        "probability_range": _frontmatter_value(args, metadata, source_frontmatter, "probability_range", ""),
        "base_rate": _frontmatter_value(args, metadata, source_frontmatter, "base_rate", ""),
        "missing_base_rate_note": _frontmatter_value(args, metadata, source_frontmatter, "missing_base_rate_note", ""),
        "evidence_ids": _frontmatter_list(args, metadata, source_frontmatter, "evidence_ids"),
        "contrary_evidence": _frontmatter_list(args, metadata, source_frontmatter, "contrary_evidence"),
        "resolution_source": _frontmatter_value(args, metadata, source_frontmatter, "resolution_source", ""),
        "review_date": _frontmatter_value(args, metadata, source_frontmatter, "review_date", ""),
        "update_triggers": _frontmatter_list(args, metadata, source_frontmatter, "update_triggers"),
        "invalidation_conditions": _frontmatter_list(args, metadata, source_frontmatter, "invalidation_conditions"),
        "source_trust_notes": _frontmatter_list(args, metadata, source_frontmatter, "source_trust_notes"),
        "scenario_cases": _frontmatter_list(args, metadata, source_frontmatter, "scenario_cases"),
        "scenario_summary": _frontmatter_value(args, metadata, source_frontmatter, "scenario_summary", ""),
        "thesis_lifecycle": _frontmatter_value(args, metadata, source_frontmatter, "thesis_lifecycle", {}),
        "current_price_as_of": _frontmatter_value(args, metadata, source_frontmatter, "current_price_as_of", ""),
        "market_anchor_as_of": _frontmatter_value(args, metadata, source_frontmatter, "market_anchor_as_of", ""),
        "investor_context_gaps": _frontmatter_list(args, metadata, source_frontmatter, "investor_context_gaps"),
        "workflow_run_id": _frontmatter_value(args, metadata, source_frontmatter, "workflow_run_id", ""),
        "producer_role": _frontmatter_value(args, metadata, source_frontmatter, "producer_role", existing.get("producer_role") if existing else ""),
        "artifact_schema_version": _int_value(_frontmatter_value(args, metadata, source_frontmatter, "artifact_schema_version", 1), default=1),
        "input_artifact_ids": _frontmatter_list(args, metadata, source_frontmatter, "input_artifact_ids"),
        "input_artifact_hashes": _frontmatter_value(args, metadata, source_frontmatter, "input_artifact_hashes", {}),
        "knowledge_cutoff": _frontmatter_value(args, metadata, source_frontmatter, "knowledge_cutoff", existing.get("knowledge_cutoff") if existing else ""),
        "follow_up_requests": _frontmatter_list(args, metadata, source_frontmatter, "follow_up_requests"),
        "improvements": _frontmatter_list(args, metadata, source_frontmatter, "improvements"),
        "version": 1,
        "content_hash": content_hash,
        "workspace_native": True,
        "created_by": created_by,
        "recorded_at": recorded_at,
    }
    if not frontmatter["anti_overfit_required"] and frontmatter["anti_overfit_checks"] == {}:
        frontmatter.pop("anti_overfit_checks")
    cutoff_text = str(frontmatter.get("knowledge_cutoff") or "").strip()
    if cutoff_text:
        normalized_cutoff = _normalized_iso(cutoff_text, "knowledge_cutoff")
        normalized_recorded_at = _normalized_iso(recorded_at, "recorded_at")
        if normalized_cutoff > normalized_recorded_at:
            raise ValueError(
                f"knowledge_cutoff {normalized_cutoff} must not be after service "
                f"recorded_at {normalized_recorded_at}; omit knowledge_cutoff when "
                "the exact current cutoff time is unavailable"
            )
    if frontmatter["evidence_lane"] and frontmatter["evidence_lane"] not in EVIDENCE_LANES:
        raise ValueError(f"evidence_lane must be one of: {', '.join(sorted(EVIDENCE_LANES))}")
    workspace_context = workspace_context_payload(root)
    lock_path = root / RESEARCH_FILE_ROOTS[0] / ".research-artifacts"
    with exclusive_file_lock(lock_path):
        current = (
            find_workspace_research_artifact_read_only(root, artifact_id)
            if append_version or atomic_authenticated_receipt
            else find_workspace_research_artifact(root, artifact_id)
        )
        if current:
            locked_canonical_path = str(
                current.get("path") or current.get("export_path") or ""
            )
            if locked_canonical_path != export_path:
                raise ValueError(
                    "research artifact canonical path changed before storage"
                )
        _validate_research_artifact_destination(
            resolved_root,
            path,
            artifact_id,
        )
        expected_hash = str(args.get("expected_content_hash") or "")
        if expected_hash and (not current or current.get("content_hash") != expected_hash):
            raise ValueError("research artifact compare-and-swap failed: content hash changed")
        if current and current.get("content_hash") != content_hash and not args.get("_append_version"):
            raise ValueError("research artifact already exists in this workspace; use append_research_artifact_version to create a new version")
        current_version = _int_value(current.get("version") if current else None, default=0)
        version = current_version + 1 if args.get("_append_version") else current_version or 1
        frontmatter["version"] = version
        snapshot_hashes = validated_source_snapshot_hashes(
            root,
            frontmatter["source_snapshot_ids"],
            str(frontmatter.get("knowledge_cutoff") or ""),
        )
        supplied_snapshot_hashes = frontmatter.get("source_snapshot_hashes")
        if supplied_snapshot_hashes not in ({}, snapshot_hashes):
            raise ValueError(
                "source_snapshot_hashes are service-derived from source_snapshot_ids"
        )
        frontmatter["source_snapshot_hashes"] = snapshot_hashes
        original_stable_bytes = path.read_bytes() if path.is_file() else None
        archive: Path | None = None
        archive_created = False
        receipt_state: dict[str, Any] | None = None
        try:
            if current and append_version:
                from tradingcodex_service.application import artifact_bindings

                locked_verification = (
                    artifact_bindings.verify_current_artifact_binding_before_append(
                        root,
                        current,
                    )
                )
                if (
                    locked_verification
                    and args.get("_service_authority")
                    is not _AUTHENTICATED_SERVICE_WRITE
                ):
                    raise PermissionError(
                        "run-bound research artifacts require an authenticated "
                        "TradingCodex MCP principal"
                    )
                current_path = safe_workspace_path(
                    root,
                    current["path"],
                    allowed_roots=RESEARCH_FILE_ROOTS,
                )
                current_bytes = current_path.read_bytes()
                current_file_sha256 = hashlib.sha256(current_bytes).hexdigest()
                expected_current_file_sha256 = str(
                    args.get("_expected_current_file_sha256") or ""
                )
                if locked_verification:
                    if not re.fullmatch(r"[0-9a-f]{64}", expected_current_file_sha256):
                        raise ValueError(
                            "run-bound append requires a verified current artifact file hash"
                        )
                    if not hmac.compare_digest(
                        str(locked_verification["file_sha256"]),
                        expected_current_file_sha256,
                    ):
                        raise ValueError(
                            "current authenticated research artifact receipt changed "
                            "before append"
                        )
                    if not hmac.compare_digest(
                        current_file_sha256,
                        str(locked_verification["file_sha256"]),
                    ):
                        raise ValueError(
                            "current authenticated research artifact changed before append"
                        )
                archive = research_artifact_version_archive_path(
                    root,
                    artifact_id,
                    current_version,
                    str(current.get("content_hash") or ""),
                )
                archive_exists = _validate_version_archive_destination(
                    root,
                    archive,
                    current_bytes,
                )
                if not archive_exists:
                    archive_created = True
                    atomic_write_text(archive, current_bytes.decode("utf-8"))
                    _validate_version_archive_destination(
                        root,
                        archive,
                        current_bytes,
                    )
            rendered_artifact = _render_research_markdown(frontmatter, markdown_body)
            if run_bound and frontmatter.get("handoff_state") == "accepted":
                from tradingcodex_service.application.artifact_quality import (
                    evaluate_artifact_quality_text,
                )

                quality = evaluate_artifact_quality_text(
                    export_path,
                    rendered_artifact,
                    strict=True,
                )
                if quality["status"] != "pass":
                    issues = [
                        *quality["required_fields_missing"],
                        *quality["warnings"],
                    ]
                    raise ValueError(
                        "accepted run-bound research artifact failed strict quality: "
                        + "; ".join(dict.fromkeys(str(issue) for issue in issues))
                    )
            artifact_file_sha256 = hashlib.sha256(
                rendered_artifact.encode("utf-8")
            ).hexdigest()
            result = {
                "status": "updated" if current else "stored",
                "db_canonical": False,
                "file_sot": True,
                "workspace_native": True,
                "artifact_id": artifact_id,
                "version": version,
                "content_hash": content_hash,
                "file_sha256": artifact_file_sha256,
                "recorded_at": recorded_at,
                "export_path": path.relative_to(root).as_posix(),
                "workspace_context": workspace_context,
            }
            if run_bound:
                from tradingcodex_service.application import artifact_bindings

                stored_artifact = _artifact_binding_payload_from_rendered(
                    root,
                    path,
                    rendered_artifact,
                )
                authorized_artifact = (
                    artifact_bindings.authenticated_service_artifact_binding_args(
                        stored_artifact,
                        expected_file_sha256=artifact_file_sha256,
                        pending_write=True,
                    )
                )
                receipt_state = (
                    artifact_bindings.prepare_authenticated_artifact_binding_receipt(
                        root,
                        authorized_artifact,
                    )
                )
                receipt = artifact_bindings.record_authenticated_artifact_binding(
                    root,
                    authorized_artifact,
                )
                result["authentication"] = {
                    "status": "verified",
                    **receipt,
                }
            atomic_write_text(path, rendered_artifact)
            return result
        except Exception:
            if run_bound:
                _rollback_authenticated_research_write(
                    root,
                    stable_path=path,
                    original_stable_bytes=original_stable_bytes,
                    archive_path=archive,
                    archive_created=archive_created,
                    receipt_state=receipt_state,
                )
            raise


def _artifact_binding_payload_from_rendered(
    root: Path,
    path: Path,
    rendered_artifact: str,
) -> dict[str, Any]:
    """Project receipt material from intended bytes before publishing stable."""

    document = split_markdown_frontmatter(rendered_artifact)
    frontmatter = document.frontmatter
    body_hash = hashlib.sha256(document.body.encode("utf-8")).hexdigest()
    declared_hash = str(frontmatter.get("content_hash") or "")
    if declared_hash != body_hash:
        raise ValueError(
            "intended research artifact content_hash does not match its body"
        )
    return {
        "artifact_id": str(frontmatter.get("artifact_id") or ""),
        "path": path.relative_to(root).as_posix(),
        "export_path": path.relative_to(root).as_posix(),
        "workflow_run_id": str(frontmatter.get("workflow_run_id") or ""),
        "content_hash": body_hash,
        "version": _int_value(frontmatter.get("version"), default=1),
        "artifact_schema_version": _int_value(
            frontmatter.get("artifact_schema_version"),
            default=1,
        ),
        "role": str(frontmatter.get("role") or ""),
        "producer_role": str(frontmatter.get("producer_role") or ""),
        "created_by": str(frontmatter.get("created_by") or ""),
        "recorded_at": str(frontmatter.get("recorded_at") or ""),
        "input_artifact_ids": _coerce_list(
            frontmatter.get("input_artifact_ids")
        ),
        "input_artifact_hashes": (
            frontmatter.get("input_artifact_hashes")
            if isinstance(frontmatter.get("input_artifact_hashes"), dict)
            else {}
        ),
        "source_snapshot_ids": _coerce_list(
            frontmatter.get("source_snapshot_ids")
        ),
        "source_snapshot_hashes": (
            frontmatter.get("source_snapshot_hashes")
            if isinstance(frontmatter.get("source_snapshot_hashes"), dict)
            else {}
        ),
        "knowledge_cutoff": str(frontmatter.get("knowledge_cutoff") or ""),
        "strategy_name": str(frontmatter.get("strategy_name") or ""),
        "strategy_hash": str(frontmatter.get("strategy_hash") or ""),
        "investment_brain_id": str(
            frontmatter.get("investment_brain_id") or ""
        ),
        "investment_brain_version": str(
            frontmatter.get("investment_brain_version") or ""
        ),
        "investment_brain_content_digest": str(
            frontmatter.get("investment_brain_content_digest") or ""
        ),
        "investor_context_applied": bool(
            frontmatter.get("investor_context_applied")
        ),
        "investor_context_hash": str(
            frontmatter.get("investor_context_hash") or ""
        ),
    }


def append_research_artifact_version(workspace_root: Path | str, args: dict[str, Any]) -> dict[str, Any]:
    if "created_by" in args:
        raise ValueError("created_by is derived from principal_id")
    if not args.get("artifact_id"):
        raise ValueError("artifact_id is required")
    root = Path(workspace_root)
    current = find_workspace_research_artifact_read_only(
        root,
        str(args["artifact_id"]),
        include_markdown=True,
    )
    if not current:
        raise ValueError(
            f"research artifact not found in workspace: {args['artifact_id']}"
        )
    canonical_path = str(
        current.get("path") or current.get("export_path") or ""
    )
    if not canonical_path:
        raise ValueError("existing research artifact has no canonical path")
    requested_markdown = args.get("markdown")
    if not requested_markdown and args.get("markdown_path"):
        requested_markdown = safe_workspace_path(
            root,
            str(args["markdown_path"]),
            allowed_roots=RESEARCH_FILE_ROOTS,
        ).read_text(encoding="utf-8")
    requested_frontmatter = split_markdown_frontmatter(
        str(requested_markdown or "")
    ).frontmatter
    requested_metadata = (
        args.get("metadata") if isinstance(args.get("metadata"), dict) else {}
    )
    validate_research_artifact_path_declarations(
        canonical_path,
        args,
        requested_metadata,
        requested_frontmatter,
        operation="append",
    )
    from tradingcodex_service.application.artifact_bindings import (
        verify_current_artifact_binding_before_append,
    )

    verification = verify_current_artifact_binding_before_append(
        workspace_root,
        current,
    )
    if verification and args.get("_service_authority") is not _AUTHENTICATED_SERVICE_WRITE:
        raise PermissionError(
            "run-bound research artifacts require an authenticated TradingCodex MCP principal"
        )
    payload = {
        **current,
        **args,
        "_append_version": True,
        "expected_content_hash": args.get("expected_content_hash") or current.get("content_hash"),
        "metadata": args.get("metadata") or current.get("metadata") or {},
        "path": canonical_path,
        "export_path": canonical_path,
    }
    if verification:
        payload["_expected_current_file_sha256"] = verification["file_sha256"]
    payload.pop("created_by", None)
    if args.get("markdown"):
        payload["markdown"] = args["markdown"]
    elif args.get("markdown_path"):
        payload.pop("markdown", None)
    else:
        payload["markdown"] = current.get("markdown")
    return create_research_artifact(workspace_root, payload)


def get_research_artifact(workspace_root: Path | str, args: dict[str, Any]) -> dict[str, Any]:
    artifact_id = args.get("artifact_id")
    if not artifact_id:
        raise ValueError("artifact_id is required")
    artifact = find_workspace_research_artifact(Path(workspace_root), str(artifact_id))
    if not artifact:
        raise ValueError(f"research artifact not found in workspace: {artifact_id}")
    if args.get("include_markdown", True) is not False:
        path = safe_workspace_path(workspace_root, artifact["path"], allowed_roots=RESEARCH_FILE_ROOTS)
        artifact["markdown"] = _read_research_markdown_body(path)
    return artifact


def list_research_artifacts(workspace_root: Path | str, args: dict[str, Any] | None = None) -> dict[str, Any]:
    args = args or {}
    root = Path(workspace_root)
    artifacts = list_workspace_research_artifacts(root, include_markdown=args.get("include_markdown") is True)
    for field in ["artifact_type", "universe", "workflow_type", "workflow_run_id", "symbol", "readiness_label", "handoff_state", "created_by"]:
        value = args.get(field)
        if value:
            artifacts = [artifact for artifact in artifacts if str(artifact.get(field) or "").lower() == str(value).lower()]
    limit = max(1, min(int(args.get("limit") or 50), 200))
    return {
        "db_canonical": False,
        "file_sot": True,
        "workspace_native": True,
        "workspace_context": workspace_context_payload(workspace_root),
        "artifacts": artifacts[:limit],
        "invalid_artifacts": research_repository_diagnostics(root),
    }


def search_research_artifacts(workspace_root: Path | str, args: dict[str, Any]) -> dict[str, Any]:
    query = str(args.get("query") or "").strip()
    if not query:
        raise ValueError("query is required")
    root = Path(workspace_root)
    indexed = _refresh_research_index(root)
    query_lower = query.lower()
    candidates = [
        entry
        for entry in indexed.values()
        if query_lower in str(entry.get("metadata_search_text") or "")
        or query_lower in str(entry.get("body_search_text") or "")
    ]
    artifacts = []
    for entry in candidates:
        path = safe_workspace_path(root, entry["path"], allowed_roots=RESEARCH_FILE_ROOTS)
        artifact = _indexed_payload(root, entry)
        if query_lower not in str(entry.get("metadata_search_text") or ""):
            body = _read_research_markdown_body(path)
            if query_lower not in body.lower():
                continue
        artifacts.append(artifact)
    for field in ["universe", "artifact_type"]:
        if args.get(field):
            artifacts = [artifact for artifact in artifacts if str(artifact.get(field) or "").lower() == str(args[field]).lower()]
    limit = max(1, min(int(args.get("limit") or 20), 100))
    for artifact in artifacts:
        artifact.pop("markdown", None)
    return {
        "query": query,
        "db_canonical": False,
        "file_sot": True,
        "workspace_native": True,
        "workspace_context": workspace_context_payload(workspace_root),
        "artifacts": artifacts[:limit],
    }


def export_research_artifact_md(workspace_root: Path | str, args: dict[str, Any]) -> dict[str, Any]:
    root = Path(workspace_root)
    artifact_id = args.get("artifact_id")
    if not artifact_id:
        raise ValueError("artifact_id is required")
    artifact = get_research_artifact(root, {"artifact_id": artifact_id, "include_markdown": True})
    target_rel = str(args.get("export_path") or artifact["path"])
    target = safe_workspace_path(root, target_rel, allowed_roots=RESEARCH_FILE_ROOTS)
    source = safe_workspace_path(root, artifact["path"], allowed_roots=RESEARCH_FILE_ROOTS)
    if target.resolve() != source.resolve():
        target.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(target, source.read_text(encoding="utf-8"))
    return {
        "status": "exported",
        "artifact_id": artifact["artifact_id"],
        "export_path": target.relative_to(root).as_posix(),
        "db_canonical": False,
        "file_sot": True,
        "workspace_native": True,
        "workspace_context": workspace_context_payload(root),
    }


def record_source_snapshot(workspace_root: Path | str, args: dict[str, Any]) -> dict[str, Any]:
    root = Path(workspace_root)
    if "snapshot_id" in args:
        raise ValueError("snapshot_id is derived by the service")
    if "created_by" in args:
        raise ValueError("created_by is derived from principal_id")
    system_recorded_at = _normalized_iso(now_iso(), "system_recorded_at")
    recorded_at = _normalized_iso(args.get("recorded_at") or system_recorded_at, "recorded_at")
    retrieved_at = _normalized_iso(args.get("retrieved_at") or recorded_at, "retrieved_at")
    known_at = _normalized_iso(args.get("known_at") or retrieved_at, "known_at")
    if known_at > retrieved_at or retrieved_at > recorded_at:
        raise ValueError("source snapshot times must satisfy known_at <= retrieved_at <= recorded_at")
    if recorded_at > system_recorded_at:
        raise ValueError("recorded_at must not be after system_recorded_at")
    source_payload = args.get("payload") if isinstance(args.get("payload"), dict) else {}
    provider = str(args.get("provider") or "").strip()
    source_category = str(args.get("source_category") or "").strip()
    if not provider:
        raise ValueError("provider is required")
    if not source_category:
        raise ValueError("source_category is required")
    payload = {
        "schema_version": SOURCE_SNAPSHOT_SCHEMA_VERSION,
        "provider": provider,
        "source_category": source_category,
        "source_locator": args.get("source_locator") or f"provider:{sanitize_id(provider)}:{sanitize_id(source_category)}",
        "provider_query": args.get("provider_query") or {},
        "as_of": args.get("as_of") or "",
        "observed_at": args.get("observed_at") or "",
        "effective_at": args.get("effective_at") or "",
        "published_at": args.get("published_at") or "",
        "retrieved_at": retrieved_at,
        "known_at": known_at,
        "revision": args.get("revision") or "not_applicable",
        "vintage": args.get("vintage") or "not_applicable",
        "timezone": args.get("timezone") or "UTC",
        "schema_hash": args.get("schema_hash") or stable_hash({key: type(value).__name__ for key, value in sorted(source_payload.items())}),
        "corporate_action_policy": args.get("corporate_action_policy") or "not_specified",
        "price_adjustment_policy": args.get("price_adjustment_policy") or "not_specified",
        "universe_membership": args.get("universe_membership") if isinstance(args.get("universe_membership"), dict) else {},
        "delisting_policy": args.get("delisting_policy") or "not_specified",
        "coverage_note": args.get("coverage_note") or "coverage and licensing not specified",
        "artifact_id": args.get("artifact_id") or "",
        "warnings": args.get("warnings") if isinstance(args.get("warnings"), list) else [],
        "payload": source_payload,
        "payload_hash": stable_hash(source_payload),
        "created_by": args.get("principal_id") or "system",
        "recorded_at": recorded_at,
        "system_recorded_at": system_recorded_at,
        "workspace_native": True,
    }
    payload["snapshot_hash"] = stable_hash(payload)
    snapshot_id = source_snapshot_id(payload)
    rel_path = SOURCE_SNAPSHOT_ROOT / f"{snapshot_id}.json"
    path = safe_workspace_path(root, rel_path, allowed_roots=SOURCE_SNAPSHOT_ROOTS)
    document = {**payload, "snapshot_id": snapshot_id}
    validate_source_snapshot(document, expected_snapshot_id=snapshot_id)
    if path.exists() and json.loads(path.read_text(encoding="utf-8")) != document:
        raise ValueError(f"source snapshot id collision: {snapshot_id}")
    atomic_write_text(path, json.dumps(document, indent=2, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n")
    result = {
        "status": "recorded",
        "snapshot_id": snapshot_id,
        "artifact_id": payload["artifact_id"],
        "provider": payload["provider"],
        "source_category": payload["source_category"],
        "known_at": payload["known_at"],
        "retrieved_at": payload["retrieved_at"],
        "recorded_at": payload["recorded_at"],
        "system_recorded_at": payload["system_recorded_at"],
        "export_path": path.relative_to(root).as_posix(),
        "db_canonical": False,
        "file_sot": True,
        "workspace_native": True,
        "workspace_context": workspace_context_payload(root),
    }
    return result


def create_evidence_run_card(workspace_root: Path | str, args: dict[str, Any]) -> dict[str, Any]:
    root = Path(workspace_root)
    if "created_by" in args:
        raise ValueError("created_by is derived from principal_id")
    related_rel, related_hash, artifact_hashes = _related_artifact_card_values(root, args)
    config_hash = str(args.get("config_hash") or stable_hash(args.get("config") if args.get("config") is not None else {}))
    card_seed = {"related_artifact_path": related_rel, "related_artifact_hash": related_hash, "config_hash": config_hash}
    card = {
        "schema_version": 1,
        "artifact_type": "evidence_run_card",
        "card_id": str(args.get("card_id") or f"run-card-{sanitize_id(Path(related_rel).stem)}-{stable_hash(card_seed)[:12]}"),
        "related_artifact_path": related_rel,
        "generated_at": str(args.get("generated_at") or now_iso()),
        "created_by": str(args.get("principal_id") or "system"),
        "config_hash": config_hash,
        "input_refs": _coerce_list(args.get("input_refs")),
        "data_source_refs": _coerce_list(args.get("data_source_refs")),
        "artifact_hashes": artifact_hashes,
        "metrics": args.get("metrics") if isinstance(args.get("metrics"), dict) else {},
        "validation_summary": args.get("validation_summary") or "",
        "warnings": _coerce_list(args.get("warnings")),
        "source_limitations": _coerce_list(args.get("source_limitations")),
        "authority": "evidence_only",
        "blocked_actions": list(dict.fromkeys(["order_drafting", "order_approval", "order_execution", *_coerce_list(args.get("blocked_actions"))])),
    }
    rel_path = str(args.get("export_path") or default_evidence_run_card_path(related_rel))
    if not rel_path.endswith(".run-card.json"):
        raise ValueError("evidence run card export_path must end with .run-card.json")
    path = safe_workspace_path(root, rel_path, allowed_roots=WORKFLOW_ARTIFACT_ROOTS)
    atomic_write_text(path, json.dumps(card, indent=2, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n")
    return {
        "status": "recorded",
        "card_id": card["card_id"],
        "artifact_type": "evidence_run_card",
        "related_artifact_path": related_rel,
        "export_path": path.relative_to(root).as_posix(),
        "config_hash": card["config_hash"],
        "workspace_native": True,
        "file_sot": True,
        "db_canonical": False,
        "workspace_context": workspace_context_payload(root),
    }


def create_validation_card(workspace_root: Path | str, args: dict[str, Any]) -> dict[str, Any]:
    root = Path(workspace_root)
    if "created_by" in args:
        raise ValueError("created_by is derived from principal_id")
    related_rel, related_hash, artifact_hashes = _related_artifact_card_values(root, args)
    checks = _normalize_validation_checks(args.get("checks"))
    card_seed = {"related_artifact_path": related_rel, "related_artifact_hash": related_hash, "validation_scope": args.get("validation_scope") or "evidence_quality"}
    card = {
        "schema_version": 1,
        "artifact_type": "validation_card",
        "card_id": str(args.get("card_id") or f"validation-card-{sanitize_id(Path(related_rel).stem)}-{stable_hash(card_seed)[:12]}"),
        "related_artifact_path": related_rel,
        "generated_at": str(args.get("generated_at") or now_iso()),
        "created_by": str(args.get("principal_id") or "system"),
        "validation_scope": str(args.get("validation_scope") or "evidence_quality"),
        "evidence_quality_label": str(args.get("evidence_quality_label") or "not_validated"),
        "input_refs": _coerce_list(args.get("input_refs")),
        "data_source_refs": _coerce_list(args.get("data_source_refs")),
        "artifact_hashes": artifact_hashes,
        "checks": checks,
        "metrics": args.get("metrics") if isinstance(args.get("metrics"), dict) else {},
        "validation_summary": args.get("validation_summary") or "",
        "warnings": _coerce_list(args.get("warnings")),
        "source_limitations": _coerce_list(args.get("source_limitations")),
        "authority": "evidence_only",
        "blocked_actions": list(dict.fromkeys(["order_drafting", "order_approval", "order_execution", *_coerce_list(args.get("blocked_actions"))])),
    }
    if card["evidence_quality_label"] == "validated":
        incomplete = [
            key
            for key, check in checks.items()
            if check["status"] not in {"pass", "not_applicable"} or not check["evidence_refs"]
        ]
        if incomplete:
            raise ValueError(f"validated cards require completed evidence-backed checks: {', '.join(incomplete)}")
    rel_path = str(args.get("export_path") or default_validation_card_path(related_rel))
    if not rel_path.endswith(".validation-card.json"):
        raise ValueError("validation card export_path must end with .validation-card.json")
    path = safe_workspace_path(root, rel_path, allowed_roots=WORKFLOW_ARTIFACT_ROOTS)
    atomic_write_text(path, json.dumps(card, indent=2, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n")
    return {
        "status": "recorded",
        "card_id": card["card_id"],
        "artifact_type": "validation_card",
        "related_artifact_path": related_rel,
        "export_path": path.relative_to(root).as_posix(),
        "evidence_quality_label": card["evidence_quality_label"],
        "workspace_native": True,
        "file_sot": True,
        "db_canonical": False,
        "workspace_context": workspace_context_payload(root),
    }


def _related_artifact_card_values(root: Path, args: dict[str, Any]) -> tuple[str, str, dict[str, str]]:
    related_arg = args.get("related_artifact_path")
    if not related_arg:
        raise ValueError("related_artifact_path is required")
    related = safe_workspace_path(root, str(related_arg), allowed_roots=WORKFLOW_ARTIFACT_ROOTS)
    if not related.exists() or not related.is_file():
        raise ValueError("related artifact path does not exist")
    related_rel = related.relative_to(root).as_posix()
    related_hash = file_hash(related) or ""
    artifact_hashes = args.get("artifact_hashes") if isinstance(args.get("artifact_hashes"), dict) else {}
    artifact_hashes = {str(key): str(value) for key, value in artifact_hashes.items() if value not in (None, "")}
    artifact_hashes.setdefault(related_rel, related_hash)
    return related_rel, related_hash, artifact_hashes


def list_workspace_research_artifacts(root: Path, *, include_markdown: bool = False) -> list[dict[str, Any]]:
    records = []
    for entry in _refresh_research_index(root).values():
        if entry.get("status") == "invalid":
            continue
        payload = _indexed_payload(root, entry)
        if include_markdown:
            path = safe_workspace_path(root, entry["path"], allowed_roots=RESEARCH_FILE_ROOTS)
            payload["markdown"] = _read_research_markdown_body(path)
        records.append(payload)
    if not records:
        diagnostics = research_repository_diagnostics(root)
        if diagnostics:
            raise ValueError(str(diagnostics[0]["error"]))
    return sorted(records, key=lambda item: item["updated_at"], reverse=True)


def research_repository_diagnostics(root: Path) -> list[dict[str, str]]:
    return [
        {"path": str(entry["path"]), "error": str(entry["error"])}
        for entry in _refresh_research_index(root).values()
        if entry.get("status") == "invalid"
    ]


def rebuild_research_index(workspace_root: Path | str) -> dict[str, Any]:
    root = Path(workspace_root)
    path = safe_workspace_path(root, RESEARCH_INDEX_PATH, allowed_roots=(Path("trading/research"),))
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    entries = _refresh_research_index(root)
    invalid_artifacts = research_repository_diagnostics(root)
    return {
        "status": "rebuilt",
        "artifact_count": len(entries) - len(invalid_artifacts),
        "invalid_artifacts": invalid_artifacts,
        "index_path": RESEARCH_INDEX_PATH.as_posix(),
        "workspace_native": True,
        "workspace_context": workspace_context_payload(root),
    }


def _refresh_research_index(root: Path) -> dict[str, dict[str, Any]]:
    resolved_root = root.expanduser().resolve(strict=False)
    index_path = safe_workspace_path(root, RESEARCH_INDEX_PATH, allowed_roots=(Path("trading/research"),))
    lock_target = root / "trading/research/.index/research-index"
    with exclusive_file_lock(lock_target):
        existing: dict[str, Any] = {}
        if index_path.exists():
            try:
                document = json.loads(index_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                document = {}
            if isinstance(document, dict) and document.get("schema_version") == RESEARCH_INDEX_VERSION:
                raw_entries = document.get("entries")
                existing = raw_entries if isinstance(raw_entries, dict) else {}
        paths: list[Path] = []
        for rel_root in RESEARCH_FILE_ROOTS:
            base = root / rel_root
            if base.exists():
                for candidate in base.rglob("*.md"):
                    if (
                        candidate.name == ".gitkeep"
                        or any(part in {".versions", ".index", ".drafts"} for part in candidate.parts)
                        or candidate.name.endswith((".run-card.md", ".validation-card.md"))
                    ):
                        continue
                    try:
                        safe = safe_workspace_path(root, candidate.relative_to(root), allowed_roots=RESEARCH_FILE_ROOTS)
                    except ValueError:
                        continue
                    if safe.is_file():
                        paths.append(safe)
        entries: dict[str, dict[str, Any]] = {}
        changed = set(existing) != {path.relative_to(resolved_root).as_posix() for path in paths}
        for path in sorted(paths):
            rel = path.relative_to(resolved_root).as_posix()
            stat = path.stat()
            cached = existing.get(rel) if isinstance(existing.get(rel), dict) else {}
            if (
                cached.get("status") in {"valid", "invalid"}
                and cached.get("mtime_ns") == stat.st_mtime_ns
                and cached.get("size") == stat.st_size
            ):
                entries[rel] = cached
                continue
            try:
                payload = _research_file_payload(root, path, include_markdown=True)
            except (OSError, UnicodeError, ValueError) as exc:
                entries[rel] = {
                    "path": rel,
                    "mtime_ns": stat.st_mtime_ns,
                    "size": stat.st_size,
                    "file_hash": file_hash(path),
                    "status": "invalid",
                    "error": str(exc) or exc.__class__.__name__,
                }
                changed = True
                continue
            body = str(payload.pop("markdown", ""))
            stored_payload = {
                key: value.isoformat() if isinstance(value, datetime) else value
                for key, value in payload.items()
                if key != "workspace_context"
            }
            metadata_search_text = " ".join(
                str(payload.get(key) or "").lower()
                for key in (
                    "artifact_id", "path", "artifact_type", "universe", "role", "symbol", "title",
                    "context_summary", "reader_summary", "evidence_lane", "decision_snapshot_id", "strategy_name",
                    "investment_brain_id", "investment_brain_version",
                )
            )
            entries[rel] = {
                "path": rel,
                "mtime_ns": stat.st_mtime_ns,
                "size": stat.st_size,
                "file_hash": file_hash(path),
                "status": "valid",
                "payload": stored_payload,
                "metadata_search_text": metadata_search_text,
                "body_search_text": body.lower(),
            }
            changed = True
        if changed or not index_path.exists():
            atomic_write_text(
                index_path,
                json.dumps(
                    {
                        "schema_version": RESEARCH_INDEX_VERSION,
                        "generated_at": now_iso(),
                        "entries": entries,
                    },
                    indent=2,
                    ensure_ascii=False,
                    sort_keys=True,
                    allow_nan=False,
                ) + "\n",
            )
        return entries


def _indexed_payload(root: Path, entry: dict[str, Any]) -> dict[str, Any]:
    raw = entry.get("payload") if isinstance(entry.get("payload"), dict) else {}
    payload = dict(raw)
    payload["workspace_context"] = workspace_context_payload(root)
    return payload


def find_workspace_research_artifact(root: Path, artifact_id: str) -> dict[str, Any] | None:
    matches: list[dict[str, Any]] = []
    for entry in _refresh_research_index(root).values():
        if entry.get("status") == "invalid":
            continue
        artifact = _indexed_payload(root, entry)
        if artifact["artifact_id"] == artifact_id:
            matches.append(artifact)
    if len(matches) > 1:
        paths = ", ".join(sorted(str(item.get("path") or "") for item in matches))
        raise ValueError(f"duplicate research artifact_id {artifact_id}: {paths}")
    return matches[0] if matches else None


def find_workspace_research_artifact_read_only(
    root: Path,
    artifact_id: str,
    *,
    include_markdown: bool = False,
) -> dict[str, Any] | None:
    """Read one canonical artifact without mutating the rebuildable index."""

    resolved_root = root.expanduser().resolve(strict=False)
    matches: list[dict[str, Any]] = []
    for rel_root in RESEARCH_FILE_ROOTS:
        base = resolved_root / rel_root
        if not base.exists():
            continue
        for candidate in base.rglob("*.md"):
            if (
                candidate.name == ".gitkeep"
                or any(
                    part in {".versions", ".index", ".drafts"}
                    for part in candidate.parts
                )
                or candidate.name.endswith((".run-card.md", ".validation-card.md"))
            ):
                continue
            try:
                safe = safe_workspace_path(
                    resolved_root,
                    candidate.relative_to(resolved_root),
                    allowed_roots=RESEARCH_FILE_ROOTS,
                )
                document = split_markdown_frontmatter(
                    safe.read_text(encoding="utf-8")
                )
            except (OSError, UnicodeError, ValueError):
                continue
            if str(document.frontmatter.get("artifact_id") or "").strip() != artifact_id:
                continue
            matches.append(
                _research_file_payload(
                    resolved_root,
                    safe,
                    include_markdown=include_markdown,
                )
            )
    if len(matches) > 1:
        paths = ", ".join(sorted(str(item.get("path") or "") for item in matches))
        raise ValueError(f"duplicate research artifact_id {artifact_id}: {paths}")
    return matches[0] if matches else None


def research_artifact_version_archive_path(
    root: Path,
    artifact_id: str,
    version: int,
    content_hash: str,
) -> Path:
    resolved_root = root.expanduser().resolve(strict=False)
    relative = (
        Path("trading/research/.versions")
        / sanitize_id(artifact_id)
        / f"v{version}-{content_hash[:12]}.md"
    )
    safe_workspace_path(
        resolved_root,
        relative,
        allowed_roots=(Path("trading/research"),),
    )
    return resolved_root / relative


def _validate_version_archive_destination(
    root: Path,
    archive: Path,
    expected_bytes: bytes,
) -> bool:
    if not _validate_version_archive_path(root, archive):
        return False
    try:
        existing_bytes = archive.read_bytes()
    except OSError as exc:
        raise ValueError("research artifact version archive is unreadable") from exc
    if existing_bytes != expected_bytes:
        raise ValueError(
            "existing research artifact version archive does not match current stable bytes"
        )
    return True


def _validate_version_archive_path(root: Path, archive: Path) -> bool:
    """Require a lexical archive path with only regular, symlink-free nodes."""

    resolved_root = root.expanduser().resolve(strict=False)
    try:
        relative = archive.relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError("research artifact version archive escapes the workspace") from exc
    current = resolved_root
    for index, part in enumerate(relative.parts):
        current /= part
        try:
            mode = current.lstat().st_mode
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise ValueError("research artifact version archive is unreadable") from exc
        if stat.S_ISLNK(mode):
            raise ValueError("research artifact version archive must not contain symlinks")
        if index < len(relative.parts) - 1 and not stat.S_ISDIR(mode):
            raise ValueError("research artifact version archive ancestor must be a directory")
        if index == len(relative.parts) - 1 and not stat.S_ISREG(mode):
            raise ValueError("research artifact version archive must be a regular file")
    return archive.exists()


def _rollback_authenticated_research_write(
    root: Path,
    *,
    stable_path: Path,
    original_stable_bytes: bytes | None,
    archive_path: Path | None,
    archive_created: bool,
    receipt_state: dict[str, Any] | None,
) -> None:
    if receipt_state:
        receipt_path = Path(receipt_state["path"])
        existing_receipt = receipt_state.get("existing_bytes")
        if existing_receipt is None:
            receipt_path.unlink(missing_ok=True)
            _remove_empty_parents(receipt_path.parent, stop=root)
        else:
            atomic_write_text(receipt_path, bytes(existing_receipt).decode("utf-8"))
    if original_stable_bytes is None:
        stable_path.unlink(missing_ok=True)
    else:
        atomic_write_text(stable_path, original_stable_bytes.decode("utf-8"))
    if archive_created and archive_path is not None:
        archive_path.unlink(missing_ok=True)
        _remove_empty_parents(
            archive_path.parent,
            stop=root / "trading/research",
        )


def _remove_empty_parents(path: Path, *, stop: Path) -> None:
    current = path
    resolved_stop = stop.expanduser().resolve(strict=False)
    while current != resolved_stop:
        try:
            current.rmdir()
        except OSError:
            return
        current = current.parent


def find_workspace_research_artifact_version(
    root: Path,
    artifact_id: str,
    *,
    version: int,
    content_hash: str,
) -> dict[str, Any] | None:
    current = find_workspace_research_artifact_read_only(root, artifact_id)
    if current and (
        current.get("version") == version
        and str(current.get("content_hash") or "") == content_hash
    ):
        return current
    archive = research_artifact_version_archive_path(
        root,
        artifact_id,
        version,
        content_hash,
    )
    if not _validate_version_archive_path(root, archive):
        return None
    archived = _research_file_payload(root, archive, include_markdown=False)
    if (
        archived.get("artifact_id") != artifact_id
        or archived.get("version") != version
        or str(archived.get("content_hash") or "") != content_hash
    ):
        raise ValueError(
            f"archived research artifact does not match its authenticated version: {artifact_id}"
        )
    from tradingcodex_service.application.artifact_bindings import (
        historical_archive_artifact_binding_args,
    )

    return historical_archive_artifact_binding_args(archived)


def _research_file_payload(root: Path, path: Path, *, include_markdown: bool = False) -> dict[str, Any]:
    resolved_root = root.expanduser().resolve(strict=False)
    candidate = path.expanduser().resolve(strict=False)
    try:
        raw = candidate.relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError("research artifact path escapes the workspace root") from exc
    path = safe_workspace_path(resolved_root, raw, allowed_roots=RESEARCH_FILE_ROOTS)
    rel = path.relative_to(resolved_root).as_posix()
    frontmatter, heading, body = _research_file_parts(path)
    content_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
    declared_content_hash = str(frontmatter.get("content_hash") or "")
    if declared_content_hash and declared_content_hash != content_hash:
        raise ValueError(f"research artifact content_hash does not match its body: {rel}")
    artifact_id = str(frontmatter.get("artifact_id") or "").strip()
    if not artifact_id:
        raise ValueError(f"research artifact frontmatter requires artifact_id: {rel}")
    artifact_type = _required_frontmatter_text(frontmatter, "artifact_type", rel)
    universe = _required_frontmatter_text(frontmatter, "universe", rel)
    payload = {
        "artifact_id": artifact_id,
        "path": rel,
        "export_path": rel,
        "artifact_type": artifact_type,
        "universe": universe,
        "workflow_type": str(frontmatter.get("workflow_type") or ""),
        "role": str(frontmatter.get("role") or ""),
        "symbol": str(frontmatter.get("symbol") or ""),
        "title": str(frontmatter.get("title") or heading or path.stem.replace("-", " ").title()),
        "metadata": {},
        "workspace_context": workspace_context_payload(root),
        "source_as_of": str(frontmatter.get("source_as_of") or ""),
        "readiness_label": str(frontmatter.get("readiness_label") or ""),
        "context_summary": str(frontmatter.get("context_summary") or ""),
        "reader_summary": str(frontmatter.get("reader_summary") or ""),
        "handoff_state": str(frontmatter.get("handoff_state") or ""),
        "confidence": str(frontmatter.get("confidence") or ""),
        "missing_evidence": _coerce_list(frontmatter.get("missing_evidence")),
        "next_recipient": str(frontmatter.get("next_recipient") or ""),
        "next_action": str(frontmatter.get("next_action") or ""),
        "blocked_actions": _coerce_list(frontmatter.get("blocked_actions")),
        "source_snapshot_ids": _coerce_list(frontmatter.get("source_snapshot_ids")),
        "source_snapshot_hashes": (
            frontmatter.get("source_snapshot_hashes")
            if isinstance(frontmatter.get("source_snapshot_hashes"), dict)
            else {}
        ),
        "evidence_lane": str(frontmatter.get("evidence_lane") or ""),
        "research_spec_id": str(frontmatter.get("research_spec_id") or ""),
        "replay_manifest_id": str(frontmatter.get("replay_manifest_id") or ""),
        "decision_snapshot_id": str(frontmatter.get("decision_snapshot_id") or ""),
        "strategy_name": str(frontmatter.get("strategy_name") or ""),
        "strategy_hash": str(frontmatter.get("strategy_hash") or ""),
        "investment_brain_id": str(frontmatter.get("investment_brain_id") or ""),
        "investment_brain_version": str(frontmatter.get("investment_brain_version") or ""),
        "investment_brain_content_digest": str(frontmatter.get("investment_brain_content_digest") or ""),
        "investor_context_applied": bool(frontmatter.get("investor_context_applied")),
        "investor_context_hash": str(frontmatter.get("investor_context_hash") or ""),
        "decision_memory_consulted": bool(frontmatter.get("decision_memory_consulted")),
        "decision_memory_cutoff": str(frontmatter.get("decision_memory_cutoff") or ""),
        "forecast_required": bool(frontmatter.get("forecast_required")),
        "decision_quality_required": bool(frontmatter.get("decision_quality_required")),
        "investor_context_gate_required": bool(frontmatter.get("investor_context_gate_required")),
        "anti_overfit_required": bool(frontmatter.get("anti_overfit_required")),
        "anti_overfit_checks": frontmatter.get("anti_overfit_checks") if isinstance(frontmatter.get("anti_overfit_checks"), dict) else {},
        "forecast_allowed": frontmatter.get("forecast_allowed", ""),
        "forecast_block_reason": str(frontmatter.get("forecast_block_reason") or ""),
        "forecast_target": str(frontmatter.get("forecast_target") or ""),
        "forecast_horizon": str(frontmatter.get("forecast_horizon") or ""),
        "probability": frontmatter.get("probability", ""),
        "probability_range": frontmatter.get("probability_range", ""),
        "base_rate": frontmatter.get("base_rate", ""),
        "missing_base_rate_note": str(frontmatter.get("missing_base_rate_note") or ""),
        "evidence_ids": _coerce_list(frontmatter.get("evidence_ids")),
        "contrary_evidence": _coerce_list(frontmatter.get("contrary_evidence")),
        "resolution_source": str(frontmatter.get("resolution_source") or ""),
        "review_date": str(frontmatter.get("review_date") or ""),
        "update_triggers": _coerce_list(frontmatter.get("update_triggers")),
        "invalidation_conditions": _coerce_list(frontmatter.get("invalidation_conditions")),
        "source_trust_notes": _coerce_list(frontmatter.get("source_trust_notes")),
        "scenario_cases": _coerce_list(frontmatter.get("scenario_cases")),
        "scenario_summary": str(frontmatter.get("scenario_summary") or ""),
        "thesis_lifecycle": frontmatter.get("thesis_lifecycle") if isinstance(frontmatter.get("thesis_lifecycle"), dict) else {},
        "current_price_as_of": str(frontmatter.get("current_price_as_of") or ""),
        "market_anchor_as_of": str(frontmatter.get("market_anchor_as_of") or ""),
        "investor_context_gaps": _coerce_list(frontmatter.get("investor_context_gaps")),
        "workflow_run_id": str(frontmatter.get("workflow_run_id") or ""),
        "producer_role": str(frontmatter.get("producer_role") or ""),
        "artifact_schema_version": _int_value(frontmatter.get("artifact_schema_version"), default=1),
        "input_artifact_ids": _coerce_list(frontmatter.get("input_artifact_ids")),
        "input_artifact_hashes": frontmatter.get("input_artifact_hashes") if isinstance(frontmatter.get("input_artifact_hashes"), dict) else {},
        "knowledge_cutoff": str(frontmatter.get("knowledge_cutoff") or ""),
        "follow_up_requests": _coerce_list(frontmatter.get("follow_up_requests")),
        "improvements": _coerce_list(frontmatter.get("improvements")),
        "created_by": str(frontmatter.get("created_by") or "workspace"),
        "recorded_at": str(frontmatter.get("recorded_at") or ""),
        "content_hash": content_hash,
        "version": _int_value(frontmatter.get("version"), default=1),
        "parent_artifact_id": str(frontmatter.get("parent_artifact_id") or ""),
        "updated_at": datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc),
        "created_at": datetime.fromtimestamp(path.stat().st_ctime, tz=timezone.utc),
        "db_canonical": False,
        "file_sot": True,
        "workspace_native": True,
    }
    if include_markdown:
        payload["markdown"] = body
    return payload


def _research_file_parts(path: Path) -> tuple[dict[str, Any], str, str]:
    text = path.read_text(encoding="utf-8")
    document = split_markdown_frontmatter(text)
    return document.frontmatter, document.heading, document.body


def _read_research_markdown_body(path: Path) -> str:
    return _research_file_parts(path)[2]


def _render_research_markdown(frontmatter: dict[str, Any], markdown: str) -> str:
    header = "---\n" + "\n".join(f"{key}: {json.dumps(value, ensure_ascii=False)}" for key, value in frontmatter.items()) + "\n---\n\n"
    return header + markdown.rstrip() + "\n"


def _frontmatter_value(args: dict[str, Any], metadata: dict[str, Any], source_frontmatter: dict[str, Any], field: str, default: Any) -> Any:
    for container in (args, metadata, source_frontmatter):
        value = container.get(field)
        if value not in (None, ""):
            return value
    return default


def _frontmatter_list(args: dict[str, Any], metadata: dict[str, Any], source_frontmatter: dict[str, Any], field: str) -> list[Any]:
    return _coerce_list(_frontmatter_value(args, metadata, source_frontmatter, field, []))


def _coerce_list(value: Any) -> list[Any]:
    if value in (None, ""):
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except Exception:
            return [item.strip() for item in value.replace(";", ",").split(",") if item.strip()]
        if isinstance(parsed, list):
            return parsed
        if parsed in (None, ""):
            return []
        return [parsed]
    return [value]


def _normalize_validation_checks(value: Any) -> dict[str, dict[str, Any]]:
    raw_checks = value if isinstance(value, dict) else {}
    checks: dict[str, dict[str, Any]] = {}
    for key in ANTI_OVERFIT_CHECK_KEYS:
        raw = raw_checks.get(key)
        if isinstance(raw, dict):
            status = str(raw.get("status") or "not_assessed")
            reason = str(raw.get("reason") or "")
            evidence_refs = _coerce_list(raw.get("evidence_refs"))
        else:
            text = str(raw or "not_assessed")
            status = text if text in {"pass", "fail", "not_applicable", "not_assessed"} else "not_assessed"
            reason = "" if status == "not_assessed" else text
            evidence_refs = []
        if status not in {"pass", "fail", "not_applicable", "not_assessed"}:
            raise ValueError(f"validation check {key} has an invalid status")
        checks[key] = {"status": status, "reason": reason, "evidence_refs": evidence_refs}
    return checks


def validated_source_snapshot_hashes(
    root: Path | str,
    snapshot_ids: list[Any],
    knowledge_cutoff: str,
) -> dict[str, str]:
    """Validate exact content-addressed snapshots and return their sealed hashes."""

    workspace = Path(root)
    cutoff_text = str(knowledge_cutoff or "").strip()
    cutoff = _normalized_iso(cutoff_text, "knowledge_cutoff") if cutoff_text else ""
    if not snapshot_ids:
        return {}
    if not cutoff:
        raise ValueError(
            "knowledge_cutoff is required when source_snapshot_ids are supplied"
        )
    normalized_ids = [str(value or "").strip() for value in snapshot_ids]
    if any(not value or value != sanitize_id(value) for value in normalized_ids):
        raise ValueError("source snapshot ids must be canonical service-issued ids")
    if len(normalized_ids) != len(set(normalized_ids)):
        raise ValueError("source snapshot ids must not contain duplicates")
    hashes: dict[str, str] = {}
    for snapshot_id in normalized_ids:
        path = safe_workspace_path(
            workspace,
            SOURCE_SNAPSHOT_ROOT / f"{snapshot_id}.json",
            allowed_roots=SOURCE_SNAPSHOT_ROOTS,
        )
        if not path.exists():
            raise ValueError(f"source snapshot not found: {snapshot_id}")
        try:
            snapshot = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"source snapshot is invalid: {snapshot_id}") from exc
        if not isinstance(snapshot, dict):
            raise ValueError(f"source snapshot must be an object: {snapshot_id}")
        validate_source_snapshot(snapshot, expected_snapshot_id=snapshot_id)
        known_at = _normalized_iso(
            snapshot.get("known_at"),
            f"source snapshot {snapshot_id} known_at",
        )
        if cutoff and known_at > cutoff:
            raise ValueError(
                f"source snapshot {snapshot_id} known_at {known_at} is after artifact "
                f"knowledge_cutoff {cutoff}; set knowledge_cutoff at or after {known_at}"
            )
        hashes[snapshot_id] = str(snapshot["snapshot_hash"])
    return {snapshot_id: hashes[snapshot_id] for snapshot_id in sorted(hashes)}


def _normalized_iso(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field} is required")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO-8601 datetime") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _int_value(value: Any, *, default: int) -> int:
    if value in (None, ""):
        return default
    if type(value) is not int:
        raise ValueError("research artifact integer fields must use JSON/YAML integer values")
    return value


def _required_frontmatter_text(frontmatter: dict[str, Any], field: str, relative_path: str) -> str:
    value = frontmatter.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"research artifact frontmatter requires {field}: {relative_path}")
    return value.strip()


def default_research_export_path_from_values(artifact_id: str, artifact_type: str, metadata: dict[str, Any]) -> str:
    stem = sanitize_id(artifact_id)
    role = str(metadata.get("role") or "") if isinstance(metadata, dict) else ""
    if artifact_type == "evidence_pack":
        return f"trading/research/{stem}.evidence.md"
    if role in ROLE_REPORT_DIRECTORIES:
        return f"trading/reports/{ROLE_REPORT_DIRECTORIES[role]}/{stem}.md"
    return f"trading/research/{stem}.md"


def default_evidence_run_card_path(related_artifact_path: str) -> str:
    related = Path(related_artifact_path)
    return (related.parent / f"{related.stem}.run-card.json").as_posix()


def default_validation_card_path(related_artifact_path: str) -> str:
    related = Path(related_artifact_path)
    return (related.parent / f"{related.stem}.validation-card.json").as_posix()

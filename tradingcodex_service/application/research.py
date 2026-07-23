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
from tradingcodex_service.application.artifact_v2 import (
    ARTIFACT_SCHEMA_VERSION,
    compact_frontmatter,
)
from tradingcodex_service.application.markdown_preview import split_markdown_frontmatter
from tradingcodex_service.application.research_specs import EVIDENCE_LANES
from tradingcodex_service.application.runtime import (
    RuntimeMigrationError,
    persist_workspace_context_if_available,
    require_workspace_context_binding,
    workspace_context_payload,
)
from tradingcodex_service.application.source_snapshots import (
    SOURCE_SNAPSHOT_SCHEMA_VERSION,
    source_snapshot_id,
    validate_source_snapshot,
)

RESEARCH_FILE_ROOTS = (Path("trading/research"), Path("trading/reports"))
SOURCE_SNAPSHOT_ROOT = Path("trading/research/source-snapshots")
SOURCE_SNAPSHOT_ROOTS = (SOURCE_SNAPSHOT_ROOT,)
MAX_SOURCE_SNAPSHOT_PAYLOAD_CHARS = 20_000
RESEARCH_INDEX_PATH = Path("trading/research/.index/research-index.json")
RESEARCH_INDEX_VERSION = 1
RESEARCH_DRAFT_ROOT = Path("trading/research/.drafts")
_RESEARCH_ARTIFACT_EXPORT_SCHEMA_VERSION = 2
_RESEARCH_ARTIFACT_EXPORT_MARKER = "tradingcodex-research-artifact-export"
_RESEARCH_ARTIFACT_EXPORT_SIGNATURE_DOMAIN = (
    b"tradingcodex-research-artifact-export-v2:"
)
_RESEARCH_ARTIFACT_EXPORT_RESERVED_DIRECTORIES = frozenset(
    {".versions", ".index", ".drafts", "source-snapshots"}
)
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

RESEARCH_ARTIFACT_DETAIL_LEVELS = ("full", "review", "card")
RESEARCH_ARTIFACT_MARKDOWN_WINDOW_DEFAULT_CHARS = 8_000
RESEARCH_ARTIFACT_MARKDOWN_WINDOW_MAX_CHARS = 12_000
RESEARCH_ARTIFACT_CARD_MAX_SERIALIZED_CHARS = 10_000
RESEARCH_ARTIFACT_REVIEW_MAX_SERIALIZED_CHARS = 18_000
_RESEARCH_ARTIFACT_CARD_RESERVED_CHARS = 1_024
_RESEARCH_ARTIFACT_CARD_MAX_ITEMS = 8
_RESEARCH_ARTIFACT_CARD_MAX_DICT_ITEMS = 12
_RESEARCH_ARTIFACT_CARD_MAX_DEPTH = 3
_RESEARCH_ARTIFACT_CARD_DEFAULT_STRING_CHARS = 512
_RESEARCH_ARTIFACT_MAX_DATA_LINEAGE_IDS = 50
_RESEARCH_ARTIFACT_CARD_STRING_LIMITS = {
    "title": 256,
    "context_summary": 800,
    "reader_summary": 800,
    "missing_evidence": 320,
    "next_action": 512,
    "blocked_actions": 320,
}
_RESEARCH_ARTIFACT_CARD_EXACT_FIELDS = (
    "artifact_id",
    "content_hash",
    "version",
)
_REVIEW_ARTIFACT_EXCLUDED_FIELDS = frozenset(
    {
        "metadata",
        "workspace_context",
        "export_path",
        "db_canonical",
        "file_sot",
        "created_at",
        "updated_at",
    }
)
_RESEARCH_ARTIFACT_REVIEW_EXACT_FIELDS = (
    "artifact_id",
    "content_hash",
    "version",
    "workflow_run_id",
    "producer_role",
    "input_artifact_ids",
    "input_artifact_hashes",
    "source_snapshot_ids",
    "source_snapshot_hashes",
    "dataset_ids",
    "dataset_manifest_hashes",
    "calculation_run_ids",
    "calculation_run_hashes",
)
_REVIEW_ARTIFACT_FIELDS = (
    "path",
    "artifact_type",
    "universe",
    "workflow_type",
    "role",
    "symbol",
    "title",
    "source_as_of",
    "knowledge_cutoff",
    "readiness_label",
    "handoff_state",
    "confidence",
    "context_summary",
    "reader_summary",
    "evidence_grade",
    "source_freshness",
    "source_quality",
    "conflict_status",
    "decision_readiness",
    "source_trust_notes",
    "contrary_evidence",
    "missing_evidence",
    "scenario_cases",
    "update_triggers",
    "invalidation_conditions",
    "follow_up_requests",
    "improvements",
    "thesis_lifecycle",
    "forecast_required",
    "forecast_allowed",
    "forecast_block_reason",
    "decision_quality_required",
    "anti_overfit_required",
    "investor_context_applied",
    "investor_context_gate_required",
    "next_recipient",
    "next_action",
    "blocked_actions",
    "recorded_at",
    "created_by",
    "workspace_native",
)
_CARD_ARTIFACT_FIELDS = (
    "artifact_id",
    "path",
    "artifact_type",
    "universe",
    "workflow_type",
    "role",
    "symbol",
    "title",
    "source_as_of",
    "readiness_label",
    "context_summary",
    "reader_summary",
    "handoff_state",
    "confidence",
    "missing_evidence",
    "next_recipient",
    "next_action",
    "blocked_actions",
    "workflow_run_id",
    "producer_role",
    "knowledge_cutoff",
    "content_hash",
    "version",
    "workspace_native",
)
_EMPTY_PROJECTION_VALUE = object()


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


def list_workflow_artifacts(
    workspace_root: Path | str,
    args: dict[str, Any] | None = None,
) -> dict[str, Any]:
    args = args or {}
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
    research_args = {
        key: value
        for key, value in args.items()
        if key
        in {
            "artifact_type",
            "universe",
            "workflow_type",
            "workflow_run_id",
            "symbol",
            "evidence_readiness",
            "action_readiness",
            "handoff_state",
            "producer_role",
            "limit",
            "detail_level",
        }
    }
    research = list_research_artifacts(root, research_args)
    response = {
        "artifacts": sorted(files),
        "research_artifacts": research.get("artifacts", []),
        "workspace_native": True,
        "workspace_context": workspace_context_payload(root),
    }
    if args.get("detail_level") == "card":
        response["artifacts"] = [
            str(artifact.get("path") or "")
            for artifact in response["research_artifacts"]
            if isinstance(artifact, dict) and artifact.get("path")
        ]
        response["invalid_artifact_count"] = int(
            research.get("invalid_artifact_count") or 0
        )
    return response


def create_research_artifact(workspace_root: Path | str, args: dict[str, Any]) -> dict[str, Any]:
    root = Path(workspace_root)
    if (
        any(field in args for field in ("status", "lineage", "requirements", "memory"))
        and args.get("_service_authority") is not _AUTHENTICATED_SERVICE_WRITE
    ):
        raise PermissionError(
            "v2 research artifacts require an authenticated TradingCodex MCP principal"
        )
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
    workflow_run_id = str(
        args.get("workflow_run_id") or source_frontmatter.get("workflow_run_id") or ""
    ).strip()
    if artifact_type == "synthesis_report" and workflow_run_id:
        artifact_id = f"synthesis-{workflow_run_id}"
    else:
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
        requested_export_path = (
            f"trading/reports/head-manager/{artifact_id}.md"
            if artifact_type == "synthesis_report" and workflow_run_id
            else str(
                args.get("export_path")
                or default_research_export_path_from_values(
                    artifact_id,
                    artifact_type,
                    metadata,
                )
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
        "confidence_basis": _frontmatter_value(args, metadata, source_frontmatter, "confidence_basis", ""),
        "summary": _frontmatter_value(args, metadata, source_frontmatter, "summary", ""),
        "evidence_readiness": _frontmatter_value(args, metadata, source_frontmatter, "evidence_readiness", ""),
        "action_readiness": _frontmatter_value(args, metadata, source_frontmatter, "action_readiness", ""),
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
        "dataset_ids": _frontmatter_list(
            args,
            metadata,
            source_frontmatter,
            "dataset_ids",
        ),
        "dataset_manifest_hashes": _frontmatter_value(
            args,
            metadata,
            source_frontmatter,
            "dataset_manifest_hashes",
            existing.get("dataset_manifest_hashes") if existing else {},
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
        "calculation_run_ids": _frontmatter_list(args, metadata, source_frontmatter, "calculation_run_ids"),
        "calculation_run_hashes": _frontmatter_value(
            args,
            metadata,
            source_frontmatter,
            "calculation_run_hashes",
            {},
        ),
        "calculation_reuse_origins": _frontmatter_value(
            args,
            metadata,
            source_frontmatter,
            "calculation_reuse_origins",
            {},
        ),
        "knowledge_cutoff": _frontmatter_value(args, metadata, source_frontmatter, "knowledge_cutoff", existing.get("knowledge_cutoff") if existing else ""),
        "requirements": _frontmatter_list(args, metadata, source_frontmatter, "requirements"),
        "decision_quality": _frontmatter_value(args, metadata, source_frontmatter, "decision_quality", {}),
        "memory": _frontmatter_value(args, metadata, source_frontmatter, "memory", {}),
        "forecast": _frontmatter_value(args, metadata, source_frontmatter, "forecast", {}),
        "valuation": _frontmatter_value(args, metadata, source_frontmatter, "valuation", {}),
        "anti_overfit": _frontmatter_value(args, metadata, source_frontmatter, "anti_overfit", {}),
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
        if _iso_datetime(normalized_cutoff) > _iso_datetime(
            normalized_recorded_at
        ):
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
        dataset_hashes = validated_research_artifact_data_lineage(
            root,
            dataset_ids=frontmatter["dataset_ids"],
            knowledge_cutoff=str(frontmatter.get("knowledge_cutoff") or ""),
        )
        if frontmatter.get("dataset_manifest_hashes") not in ({}, dataset_hashes):
            raise ValueError(
                "dataset_manifest_hashes are service-derived from dataset_ids"
            )
        frontmatter["dataset_manifest_hashes"] = dataset_hashes
        calculation_run_ids = frontmatter["calculation_run_ids"]
        if calculation_run_ids:
            if not str(frontmatter.get("workflow_run_id") or "").strip():
                raise ValueError("calculation_run_ids require workflow_run_id")
            if not str(frontmatter.get("knowledge_cutoff") or "").strip():
                raise ValueError("calculation_run_ids require knowledge_cutoff")
            from tradingcodex_service.application.calculations import (
                verify_calculation_run_binding,
            )

            calculation_bindings = [
                verify_calculation_run_binding(
                    root,
                    run_id,
                    workflow_run_id=str(frontmatter["workflow_run_id"]),
                    knowledge_cutoff=str(frontmatter["knowledge_cutoff"]),
                )
                for run_id in calculation_run_ids
            ]
            calculation_hashes = {
                item["calculation_run_id"]: item["run_sha256"]
                for item in calculation_bindings
            }
            reuse_origins = {
                item["calculation_run_id"]: {
                    "original_run_id": item["original_run_id"],
                    "original_run_sha256": item["original_run_sha256"],
                }
                for item in calculation_bindings
                if item["original_run_id"]
            }
        else:
            calculation_hashes = {}
            reuse_origins = {}
        if frontmatter.get("calculation_run_hashes") not in ({}, calculation_hashes):
            raise ValueError(
                "calculation_run_hashes are service-derived from calculation_run_ids"
            )
        if frontmatter.get("calculation_reuse_origins") not in ({}, reuse_origins):
            raise ValueError(
                "calculation_reuse_origins are service-derived from calculation_run_ids"
            )
        frontmatter["calculation_run_hashes"] = calculation_hashes
        frontmatter["calculation_reuse_origins"] = reuse_origins
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
            rendered_frontmatter = (
                compact_frontmatter(frontmatter)
                if int(frontmatter.get("artifact_schema_version") or 1) >= ARTIFACT_SCHEMA_VERSION
                else frontmatter
            )
            rendered_artifact = _render_research_markdown(rendered_frontmatter, markdown_body)
            if (
                run_bound
                and frontmatter.get("handoff_state") == "accepted"
                and int(frontmatter.get("artifact_schema_version") or 1) < ARTIFACT_SCHEMA_VERSION
            ):
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
                "path": export_path,
                "export_path": export_path,
                "handoff_state": str(frontmatter.get("handoff_state") or ""),
                "workspace_context": workspace_context,
            }
            if run_bound:
                from tradingcodex_service.application import artifact_bindings

                stored_artifact = (
                    _artifact_binding_payload_from_internal_envelope(
                        root,
                        path,
                        rendered_artifact,
                        frontmatter,
                    )
                    if int(frontmatter.get("artifact_schema_version") or 1) >= ARTIFACT_SCHEMA_VERSION
                    else _artifact_binding_payload_from_rendered(
                        root,
                        path,
                        rendered_artifact,
                    )
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
    payload = {
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
        "calculation_run_ids": _coerce_list(
            frontmatter.get("calculation_run_ids")
        ),
        "calculation_run_hashes": (
            frontmatter.get("calculation_run_hashes")
            if isinstance(frontmatter.get("calculation_run_hashes"), dict)
            else {}
        ),
        "calculation_reuse_origins": (
            frontmatter.get("calculation_reuse_origins")
            if isinstance(frontmatter.get("calculation_reuse_origins"), dict)
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
        "dataset_ids": _coerce_list(frontmatter.get("dataset_ids")),
        "dataset_manifest_hashes": (
            frontmatter.get("dataset_manifest_hashes")
            if isinstance(frontmatter.get("dataset_manifest_hashes"), dict)
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
    _validate_stored_research_artifact_data_lineage(root, payload)
    return payload


def _artifact_binding_payload_from_internal_envelope(
    root: Path,
    path: Path,
    rendered_artifact: str,
    envelope: dict[str, Any],
) -> dict[str, Any]:
    """Build v2 receipt material without rehydrating receipt-only data from Markdown."""

    document = split_markdown_frontmatter(rendered_artifact)
    body_hash = hashlib.sha256(document.body.encode("utf-8")).hexdigest()
    if str(envelope.get("content_hash") or "") != body_hash:
        raise ValueError("intended research artifact content_hash does not match its body")
    payload = {
        **envelope,
        "path": path.relative_to(root).as_posix(),
        "export_path": path.relative_to(root).as_posix(),
        "content_hash": body_hash,
    }
    _validate_stored_research_artifact_data_lineage(root, payload)
    return payload


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
    requested_overrides: dict[str, Any] = {}
    for container in (requested_frontmatter, requested_metadata, args):
        requested_overrides.update(
            {
                key: value
                for key, value in container.items()
                if value not in (None, "")
            }
        )
    payload = {
        **current,
        **requested_overrides,
        "_append_version": True,
        "expected_content_hash": args.get("expected_content_hash") or current.get("content_hash"),
        "metadata": args.get("metadata") or current.get("metadata") or {},
        "path": canonical_path,
        "export_path": canonical_path,
    }
    # Derived bindings belong to the IDs declared for the new version. Do not
    # carry hashes from the current version into validation when the append did
    # not explicitly supply them; create_research_artifact recalculates them.
    for derived_field in (
        "source_snapshot_hashes",
        "dataset_manifest_hashes",
        "calculation_run_hashes",
        "calculation_reuse_origins",
    ):
        if derived_field not in requested_overrides:
            payload[derived_field] = {}
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
    detail_level = _research_artifact_detail_level(args.get("detail_level"))
    artifact = find_workspace_research_artifact_read_only(
        Path(workspace_root),
        str(artifact_id),
    )
    if not artifact:
        raise ValueError(f"research artifact not found in workspace: {artifact_id}")
    include_markdown = (
        args.get("include_markdown", True) is not False and detail_level != "card"
    )
    markdown_start, markdown_max_chars = _research_artifact_markdown_window(args)
    if detail_level == "card" and markdown_start is not None:
        raise ValueError("card detail_level does not accept Markdown window arguments")
    if markdown_start is not None and not include_markdown:
        raise ValueError("Markdown window arguments require include_markdown=true")
    if detail_level == "review" and include_markdown and markdown_start is None:
        markdown_start, markdown_max_chars = _validated_markdown_window_values(
            None,
            None,
        )
    if include_markdown:
        path = safe_workspace_path(workspace_root, artifact["path"], allowed_roots=RESEARCH_FILE_ROOTS)
        artifact["markdown"] = _read_research_markdown_body(path)
    return project_research_artifact(
        artifact,
        detail_level=detail_level,
        markdown_start=markdown_start,
        markdown_max_chars=markdown_max_chars,
    )


def project_research_artifact(
    artifact: dict[str, Any],
    *,
    detail_level: str = "full",
    markdown_start: int | None = None,
    markdown_max_chars: int | None = None,
) -> dict[str, Any]:
    """Project a hash-checked full artifact for a bounded response surface."""

    normalized_level = _research_artifact_detail_level(detail_level)
    if (
        normalized_level == "review"
        and markdown_start is None
        and markdown_max_chars is None
        and isinstance(artifact.get("markdown"), str)
    ):
        markdown_start, markdown_max_chars = _validated_markdown_window_values(
            None,
            None,
        )
    projected_artifact = artifact
    if markdown_start is not None or markdown_max_chars is not None:
        if normalized_level == "card":
            raise ValueError("card detail_level does not accept Markdown window arguments")
        start, max_chars = _validated_markdown_window_values(
            markdown_start,
            markdown_max_chars,
        )
        markdown = artifact.get("markdown")
        if not isinstance(markdown, str):
            raise ValueError("Markdown window arguments require include_markdown=true")
        if start > len(markdown):
            raise ValueError("markdown_start exceeds the Markdown body length")
        end = min(len(markdown), start + max_chars)
        projected_artifact = dict(artifact)
        projected_artifact["markdown"] = markdown[start:end]
        projected_artifact["markdown_window"] = {
            "start": start,
            "end": end,
            "total_chars": len(markdown),
            "has_more": end < len(markdown),
            "next_start": end if end < len(markdown) else None,
        }
    if normalized_level == "full":
        return projected_artifact
    if normalized_level == "card":
        return _bounded_research_artifact_card(projected_artifact)
    else:
        selected = {
            field: value
            for field, value in projected_artifact.items()
            if field not in _REVIEW_ARTIFACT_EXCLUDED_FIELDS
        }
    compact = _compact_research_artifact_projection(selected)
    if not isinstance(compact, dict):
        return {}
    return _bounded_research_artifact_review(compact)


def _research_artifact_detail_level(value: Any) -> str:
    detail_level = str(value or "full").strip().lower()
    if detail_level not in RESEARCH_ARTIFACT_DETAIL_LEVELS:
        raise ValueError(
            "detail_level must be one of: "
            + ", ".join(RESEARCH_ARTIFACT_DETAIL_LEVELS)
        )
    return detail_level


def _research_artifact_markdown_window(
    args: dict[str, Any],
) -> tuple[int | None, int | None]:
    start = args.get("markdown_start")
    max_chars = args.get("markdown_max_chars")
    if start is None and max_chars is None:
        return None, None
    return _validated_markdown_window_values(start, max_chars)


def _validated_markdown_window_values(
    start: Any,
    max_chars: Any,
) -> tuple[int, int]:
    normalized_start = 0 if start is None else start
    normalized_max = (
        RESEARCH_ARTIFACT_MARKDOWN_WINDOW_DEFAULT_CHARS
        if max_chars is None
        else max_chars
    )
    if isinstance(normalized_start, bool) or not isinstance(normalized_start, int):
        raise ValueError("markdown_start must be an integer")
    if normalized_start < 0:
        raise ValueError("markdown_start must be >= 0")
    if isinstance(normalized_max, bool) or not isinstance(normalized_max, int):
        raise ValueError("markdown_max_chars must be an integer")
    if not 1 <= normalized_max <= RESEARCH_ARTIFACT_MARKDOWN_WINDOW_MAX_CHARS:
        raise ValueError(
            "markdown_max_chars must be between 1 and "
            f"{RESEARCH_ARTIFACT_MARKDOWN_WINDOW_MAX_CHARS}"
        )
    return normalized_start, normalized_max


def _bounded_research_artifact_card(artifact: dict[str, Any]) -> dict[str, Any]:
    selected: dict[str, Any] = {
        "card_max_serialized_chars": RESEARCH_ARTIFACT_CARD_MAX_SERIALIZED_CHARS,
    }
    truncated_fields: set[str] = set()
    for field in _RESEARCH_ARTIFACT_CARD_EXACT_FIELDS:
        if field in artifact:
            selected[field] = artifact[field]
    if _serialized_projection_size(selected) > RESEARCH_ARTIFACT_CARD_MAX_SERIALIZED_CHARS:
        raise ValueError("research artifact identity exceeds the card response bound")

    response_budget = (
        RESEARCH_ARTIFACT_CARD_MAX_SERIALIZED_CHARS
        - _RESEARCH_ARTIFACT_CARD_RESERVED_CHARS
    )
    for field in _CARD_ARTIFACT_FIELDS:
        if field in _RESEARCH_ARTIFACT_CARD_EXACT_FIELDS or field not in artifact:
            continue
        bounded, was_truncated = _bounded_research_artifact_card_value(
            artifact[field],
            max_string_chars=_RESEARCH_ARTIFACT_CARD_STRING_LIMITS.get(
                field,
                _RESEARCH_ARTIFACT_CARD_DEFAULT_STRING_CHARS,
            ),
        )
        compact = _compact_research_artifact_projection(bounded)
        if compact is _EMPTY_PROJECTION_VALUE:
            continue
        candidate = {**selected, field: compact}
        if _serialized_projection_size(candidate) > response_budget:
            truncated_fields.add(field)
            continue
        selected[field] = compact
        if was_truncated:
            truncated_fields.add(field)

    if truncated_fields:
        selected["card_truncated_fields"] = sorted(truncated_fields)
    if _serialized_projection_size(selected) > RESEARCH_ARTIFACT_CARD_MAX_SERIALIZED_CHARS:
        raise ValueError("research artifact card response exceeds its service bound")
    return selected


def _bounded_research_artifact_review(artifact: dict[str, Any]) -> dict[str, Any]:
    """Keep review lineage and quality while enforcing one transport-safe envelope."""

    selected: dict[str, Any] = {
        "review_max_serialized_chars": RESEARCH_ARTIFACT_REVIEW_MAX_SERIALIZED_CHARS,
    }
    truncated_fields: set[str] = set()
    for field in _RESEARCH_ARTIFACT_REVIEW_EXACT_FIELDS:
        if field in artifact:
            selected[field] = artifact[field]
    metadata_budget = 7_000
    if _serialized_projection_size(selected) > metadata_budget:
        raise ValueError("research artifact review lineage exceeds the response bound")

    for field in _REVIEW_ARTIFACT_FIELDS:
        if field not in artifact or field in selected:
            continue
        bounded, was_truncated = _bounded_research_artifact_card_value(
            artifact[field],
            max_string_chars=_RESEARCH_ARTIFACT_CARD_STRING_LIMITS.get(
                field,
                _RESEARCH_ARTIFACT_CARD_DEFAULT_STRING_CHARS,
            ),
        )
        compact = _compact_research_artifact_projection(bounded)
        if compact is _EMPTY_PROJECTION_VALUE:
            continue
        candidate = {**selected, field: compact}
        if _serialized_projection_size(candidate) > metadata_budget:
            truncated_fields.add(field)
            continue
        selected[field] = compact
        if was_truncated:
            truncated_fields.add(field)

    ignored = set(artifact) - set(selected) - {"markdown", "markdown_window"}
    truncated_fields.update(ignored)
    if truncated_fields:
        selected["review_truncated_fields"] = sorted(truncated_fields)

    markdown = artifact.get("markdown")
    window = artifact.get("markdown_window")
    if isinstance(markdown, str) and isinstance(window, dict):
        start = int(window.get("start") or 0)
        total_chars = int(window.get("total_chars") or len(markdown))
        low = 0
        high = len(markdown)
        best: dict[str, Any] | None = None
        while low <= high:
            length = (low + high) // 2
            end = start + length
            candidate = {
                **selected,
                "markdown": markdown[:length],
                "markdown_window": {
                    "start": start,
                    "end": end,
                    "total_chars": total_chars,
                    "has_more": end < total_chars,
                },
            }
            if end < total_chars:
                candidate["markdown_window"]["next_start"] = end
            if _serialized_projection_size(candidate) <= RESEARCH_ARTIFACT_REVIEW_MAX_SERIALIZED_CHARS:
                best = candidate
                low = length + 1
            else:
                high = length - 1
        if best is None:
            raise ValueError("research artifact review metadata leaves no Markdown response budget")
        selected = best

    if _serialized_projection_size(selected) > RESEARCH_ARTIFACT_REVIEW_MAX_SERIALIZED_CHARS:
        raise ValueError("research artifact review response exceeds its service bound")
    return selected


def _bounded_research_artifact_card_value(
    value: Any,
    *,
    max_string_chars: int,
    depth: int = 0,
) -> tuple[Any, bool]:
    if isinstance(value, str):
        if len(value) <= max_string_chars:
            return value, False
        suffix = "…"
        return value[: max(0, max_string_chars - len(suffix))] + suffix, True
    if isinstance(value, list):
        truncated = len(value) > _RESEARCH_ARTIFACT_CARD_MAX_ITEMS
        bounded_items: list[Any] = []
        for item in value[:_RESEARCH_ARTIFACT_CARD_MAX_ITEMS]:
            bounded, child_truncated = _bounded_research_artifact_card_value(
                item,
                max_string_chars=max_string_chars,
                depth=depth + 1,
            )
            bounded_items.append(bounded)
            truncated = truncated or child_truncated
        return bounded_items, truncated
    if isinstance(value, dict):
        if depth >= _RESEARCH_ARTIFACT_CARD_MAX_DEPTH:
            return {}, bool(value)
        items = list(value.items())
        truncated = len(items) > _RESEARCH_ARTIFACT_CARD_MAX_DICT_ITEMS
        bounded_dict: dict[str, Any] = {}
        for key, item in items[:_RESEARCH_ARTIFACT_CARD_MAX_DICT_ITEMS]:
            bounded, child_truncated = _bounded_research_artifact_card_value(
                item,
                max_string_chars=max_string_chars,
                depth=depth + 1,
            )
            bounded_dict[str(key)[:128]] = bounded
            truncated = truncated or child_truncated or len(str(key)) > 128
        return bounded_dict, truncated
    return value, False


def _serialized_projection_size(value: dict[str, Any]) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")))


def _compact_research_artifact_projection(value: Any) -> Any:
    if value is None or value == "":
        return _EMPTY_PROJECTION_VALUE
    if isinstance(value, dict):
        compact: dict[str, Any] = {}
        for key, item in value.items():
            projected = _compact_research_artifact_projection(item)
            if projected is not _EMPTY_PROJECTION_VALUE:
                compact[key] = projected
        return compact if compact else _EMPTY_PROJECTION_VALUE
    if isinstance(value, list):
        compact_items = []
        for item in value:
            projected = _compact_research_artifact_projection(item)
            if projected is not _EMPTY_PROJECTION_VALUE:
                compact_items.append(projected)
        return compact_items if compact_items else _EMPTY_PROJECTION_VALUE
    return value


def list_research_artifacts(workspace_root: Path | str, args: dict[str, Any] | None = None) -> dict[str, Any]:
    args = args or {}
    root = Path(workspace_root)
    artifacts = list_workspace_research_artifacts(root, include_markdown=args.get("include_markdown") is True)
    for field in [
        "artifact_type",
        "universe",
        "workflow_type",
        "workflow_run_id",
        "symbol",
        "handoff_state",
        "producer_role",
    ]:
        value = args.get(field)
        if value:
            artifacts = [artifact for artifact in artifacts if str(artifact.get(field) or "").lower() == str(value).lower()]
    for field in ("evidence_readiness", "action_readiness"):
        value = args.get(field)
        if value:
            from tradingcodex_service.application.artifact_v2 import project_artifact

            artifacts = [
                artifact
                for artifact in artifacts
                if str(project_artifact(artifact)["artifact"]["status"].get(field) or "").casefold()
                == str(value).casefold()
            ]
    limit = max(1, min(int(args.get("limit") or 50), 200))
    detail_level = str(args.get("detail_level") or "full").strip().lower()
    if detail_level not in {"full", "card"}:
        raise ValueError("list detail_level must be one of: full, card")
    if detail_level == "card":
        artifacts = [
            project_research_artifact(artifact, detail_level="card")
            for artifact in artifacts
        ]
    invalid_artifacts = research_repository_diagnostics(root)
    response = {
        "db_canonical": False,
        "file_sot": True,
        "workspace_native": True,
        "workspace_context": workspace_context_payload(workspace_root),
        "artifacts": artifacts[:limit],
        "invalid_artifact_count": len(invalid_artifacts),
    }
    if detail_level == "full":
        response["invalid_artifacts"] = invalid_artifacts
    return response


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
    root = Path(workspace_root).expanduser().resolve(strict=False)
    artifact_id = args.get("artifact_id")
    if not artifact_id:
        raise ValueError("artifact_id is required")
    requested_export_path = args.get("export_path")
    lock_path = root / RESEARCH_FILE_ROOTS[0] / ".research-artifacts"
    with exclusive_file_lock(lock_path):
        artifact = get_research_artifact(
            root,
            {"artifact_id": artifact_id, "include_markdown": True},
        )
        target_rel = str(requested_export_path or artifact["path"])
        target = safe_workspace_path(
            root,
            target_rel,
            allowed_roots=RESEARCH_FILE_ROOTS,
        )
        export_path = target.relative_to(root).as_posix()
        if target_rel != export_path:
            raise ValueError(
                f"research artifact export_path must be canonical: {export_path}"
            )
        source = safe_workspace_path(
            root,
            artifact["path"],
            allowed_roots=RESEARCH_FILE_ROOTS,
        )
        if target != source:
            source_bytes = source.read_bytes()
            _validate_research_artifact_export_destination(
                root,
                target,
                source,
                artifact,
            )
            _prepare_research_artifact_export_workspace_binding(root, artifact)
            _publish_research_artifact_export_copy(
                root,
                target,
                source,
                artifact,
                source_bytes,
            )
    return {
        "status": "exported",
        "artifact_id": artifact["artifact_id"],
        "export_path": export_path,
        "db_canonical": False,
        "file_sot": True,
        "workspace_native": True,
        "workspace_context": workspace_context_payload(root),
    }


def migrate_legacy_research_artifact_exports(
    workspace_root: Path | str,
) -> dict[str, Any]:
    """Recover only receipt-proven exact pre-1.2 export copies.

    Older releases copied Markdown without recording whether the original lived
    under ``trading/research`` or ``trading/reports``. Directory placement is
    not provenance, so an upgrade may mark a copy only when the retained,
    signed artifact receipt proves the original path and exact stored bytes.
    """

    root = Path(workspace_root).expanduser().resolve(strict=False)
    lock_path = root / RESEARCH_FILE_ROOTS[0] / ".research-artifacts"
    with exclusive_file_lock(lock_path):
        records = _legacy_research_artifact_records(root)
        records_by_artifact_id: dict[
            str,
            list[tuple[Path, dict[str, Any], bytes, tuple[str, int, str]]],
        ] = {}
        for record in records:
            records_by_artifact_id.setdefault(record[3][0], []).append(record)
        migrations: list[tuple[Path, Path, dict[str, Any], bytes]] = []
        verified_identities: set[tuple[str, int, str]] = set()
        for record in records:
            path, payload, stored_bytes, identity = record
            if len(records_by_artifact_id[identity[0]]) < 2:
                continue
            source = _legacy_authenticated_export_source(root, record)
            if source is None or source == path:
                continue
            migrations.append((path, source, payload, stored_bytes))
            verified_identities.add(identity)
        migrations.sort(key=lambda item: item[0].relative_to(root).as_posix())
        migration_targets = {target for target, *_rest in migrations}
        for artifact_id, artifact_records in records_by_artifact_id.items():
            unresolved = [
                record
                for record in artifact_records
                if record[0] not in migration_targets
            ]
            if len(unresolved) > 1:
                paths = ", ".join(
                    record[0].relative_to(root).as_posix()
                    for record in sorted(unresolved)
                )
                raise ValueError(
                    "legacy research artifact export is ambiguous without a "
                    f"verified receipt: {artifact_id} ({paths})"
                )

        written: list[Path] = []
        try:
            for target, source, artifact, source_bytes in migrations:
                _prepare_research_artifact_export_workspace_binding(root, artifact)
                manifest_path = _research_artifact_export_manifest_path(target)
                written.append(manifest_path)
                _write_research_artifact_export_manifest(
                    root,
                    target,
                    source,
                    artifact,
                    source_bytes,
                )
                if not is_research_artifact_export_copy(root, target):
                    raise ValueError("legacy research artifact export verification failed")
            from tradingcodex_service.application.artifact_bindings import (
                verify_authenticated_artifact_binding,
            )

            for artifact_id, version, content_hash in sorted(verified_identities):
                artifact = find_workspace_research_artifact_version(
                    root,
                    artifact_id,
                    version=version,
                    content_hash=content_hash,
                )
                if artifact is None:
                    raise ValueError(
                        "legacy research artifact export receipt has no "
                        f"recoverable canonical artifact: {artifact_id}"
                    )
                verify_authenticated_artifact_binding(
                    root,
                    artifact,
                )
        except Exception:
            for manifest_path in reversed(written):
                try:
                    manifest_path.unlink(missing_ok=True)
                except OSError:
                    pass
            raise

    return {
        "status": "migrated",
        "migrated_paths": [target.relative_to(root).as_posix() for target, *_ in migrations],
    }


def _legacy_research_artifact_records(
    root: Path,
) -> list[tuple[Path, dict[str, Any], bytes, tuple[str, int, str]]]:
    records: list[tuple[Path, dict[str, Any], bytes, tuple[str, int, str]]] = []
    for directory in RESEARCH_FILE_ROOTS:
        for path in _legacy_research_artifact_markdown_paths(root, directory):
            relative = path.relative_to(root).as_posix()
            manifest_path = _research_artifact_export_manifest_path(path)
            if manifest_path.exists() or manifest_path.is_symlink():
                if not is_research_artifact_export_copy(root, path):
                    raise ValueError(
                        "legacy research artifact export has invalid sidecar: "
                        f"{relative}"
                    )
                continue
            try:
                payload = _research_file_payload(root, path)
                identity = _legacy_research_artifact_identity(path, payload)
                stored_bytes = path.read_bytes()
            except (OSError, UnicodeError, ValueError):
                continue
            records.append((path, payload, stored_bytes, identity))
    return records


def _legacy_authenticated_export_source(
    root: Path,
    record: tuple[Path, dict[str, Any], bytes, tuple[str, int, str]],
) -> Path | None:
    """Return the signed source path for one exact legacy copy, if available."""

    path, payload, stored_bytes, identity = record
    run_id = str(payload.get("workflow_run_id") or "").strip()
    if not run_id:
        return None
    try:
        from tradingcodex_service.application import artifact_bindings

        context = require_workspace_context_binding(root)
        receipt_path = artifact_bindings._receipt_path(
            root,
            {
                "workflow_run_id": run_id,
                "artifact_id": identity[0],
                "artifact_version": identity[1],
                "content_hash": identity[2],
            },
        )
        if receipt_path.is_symlink() or not receipt_path.is_file():
            raise ValueError("authenticated artifact receipt is unavailable")
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        artifact_bindings._validate_receipt_signature(root, receipt, receipt_path)
        source = safe_workspace_path(
            root,
            str(receipt.get("artifact_path") or ""),
            allowed_roots=RESEARCH_FILE_ROOTS,
        )
        if (
            receipt.get("workspace_id") != context["workspace_id"]
            or receipt.get("workflow_run_id") != run_id
            or receipt.get("artifact_id") != identity[0]
            or receipt.get("artifact_version") != identity[1]
            or receipt.get("content_hash") != identity[2]
            or not hmac.compare_digest(
                str(receipt.get("file_sha256") or ""),
                hashlib.sha256(stored_bytes).hexdigest(),
            )
            or source.is_symlink()
            or not source.is_file()
        ):
            return None
        if source.read_bytes() == stored_bytes:
            return source
        archive = research_artifact_version_archive_path(
            root,
            identity[0],
            identity[1],
            identity[2],
        )
        if _validate_version_archive_path(root, archive) and archive.read_bytes() == stored_bytes:
            return source
    except (OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError):
        return None
    return None


def _legacy_research_artifact_markdown_paths(root: Path, directory: Path) -> list[Path]:
    base = root / directory
    if not base.exists():
        return []
    try:
        mode = base.lstat().st_mode
    except OSError as exc:
        raise ValueError("legacy research artifact directory is unreadable") from exc
    if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
        raise ValueError("legacy research artifact directory must be a real directory")

    paths: list[Path] = []
    for candidate in sorted(base.rglob("*.md")):
        relative = candidate.relative_to(root)
        if (
            candidate.name == ".gitkeep"
            or candidate.name.endswith((".run-card.md", ".validation-card.md"))
            or any(
                part in _RESEARCH_ARTIFACT_EXPORT_RESERVED_DIRECTORIES
                for part in relative.parts
            )
        ):
            continue
        try:
            mode = candidate.lstat().st_mode
        except OSError as exc:
            raise ValueError("legacy research artifact file is unreadable") from exc
        if not stat.S_ISREG(mode):
            continue
        paths.append(
            safe_workspace_path(root, relative, allowed_roots=(directory,))
        )
    return paths


def _legacy_research_artifact_identity(
    path: Path,
    payload: dict[str, Any],
) -> tuple[str, int, str]:
    frontmatter, _, _ = _research_file_parts(path)
    artifact_id = str(frontmatter.get("artifact_id") or "").strip()
    version = frontmatter.get("version")
    content_hash = str(frontmatter.get("content_hash") or "")
    if (
        not artifact_id
        or type(version) is not int
        or version < 1
        or re.fullmatch(r"[0-9a-f]{64}", content_hash) is None
        or artifact_id != str(payload.get("artifact_id") or "")
        or version != payload.get("version")
        or not hmac.compare_digest(content_hash, str(payload.get("content_hash") or ""))
    ):
        raise ValueError("research artifact has no stable export identity")
    return artifact_id, version, content_hash


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


def get_source_snapshot(workspace_root: Path | str, args: dict[str, Any]) -> dict[str, Any]:
    """Read an authenticated SourceSnapshot with an opt-in bounded payload."""

    root = Path(workspace_root).expanduser().resolve()
    snapshot_id = str(args.get("snapshot_id") or "").strip()
    if not snapshot_id or sanitize_id(snapshot_id) != snapshot_id:
        raise ValueError("snapshot_id is invalid")
    path = safe_workspace_path(
        root,
        SOURCE_SNAPSHOT_ROOT / f"{snapshot_id}.json",
        allowed_roots=SOURCE_SNAPSHOT_ROOTS,
    )
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"source snapshot not found: {snapshot_id}")
    document = validate_source_snapshot(
        json.loads(path.read_text(encoding="utf-8")),
        expected_snapshot_id=snapshot_id,
    )
    include_payload = args.get("include_payload") is True
    max_chars = max(
        1,
        min(
            int(args.get("max_payload_chars") or MAX_SOURCE_SNAPSHOT_PAYLOAD_CHARS),
            MAX_SOURCE_SNAPSHOT_PAYLOAD_CHARS,
        ),
    )
    payload_json = json.dumps(
        document["payload"],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    projected = {key: value for key, value in document.items() if key != "payload"}
    payload_truncated = include_payload and len(payload_json) > max_chars
    if include_payload and not payload_truncated:
        projected["payload"] = document["payload"]
    response = {
        "snapshot": projected,
        "payload_available": True,
        "payload_included": include_payload and not payload_truncated,
        "payload_truncated": payload_truncated,
        "payload_size_chars": len(payload_json),
        "export_path": path.relative_to(root).as_posix(),
        "file_sot": True,
        "workspace_native": True,
        "workspace_context": workspace_context_payload(root),
    }
    if payload_truncated:
        response["payload_preview_json"] = payload_json[:max_chars]
    return response


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
    from tradingcodex_service.application.artifact_catalog import (
        _refresh_artifact_catalog,
    )

    index_path = safe_workspace_path(root, RESEARCH_INDEX_PATH, allowed_roots=(Path("trading/research"),))
    lock_target = root / "trading/research/.index/research-index"
    compatibility_source = _refresh_artifact_catalog(root)
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
        source_files = compatibility_source.get("files")
        paths: list[tuple[str, Path, dict[str, Any]]] = []
        if isinstance(source_files, dict):
            for relative, raw_record in source_files.items():
                relative_path = Path(str(relative))
                if (
                    relative_path.suffix.lower() != ".md"
                    or relative_path.name == ".gitkeep"
                    or relative_path.name.endswith((".run-card.md", ".validation-card.md"))
                    or any(part in {".versions", ".index", ".drafts"} for part in relative_path.parts)
                    or not any(relative_path.is_relative_to(base) for base in RESEARCH_FILE_ROOTS)
                ):
                    continue
                try:
                    safe = safe_workspace_path(
                        root,
                        relative_path,
                        allowed_roots=RESEARCH_FILE_ROOTS,
                    )
                except ValueError:
                    continue
                if (
                    safe.is_file()
                    and not safe.is_symlink()
                    and not is_research_artifact_export_copy(root, safe)
                ):
                    record = raw_record if isinstance(raw_record, dict) else {}
                    paths.append((relative_path.as_posix(), safe, record))
        entries: dict[str, dict[str, Any]] = {}
        changed = set(existing) != {relative for relative, _path, _record in paths}
        for rel, path, source_record in sorted(paths, key=lambda item: item[0]):
            stat = path.stat()
            mtime_ns = int(source_record.get("mtime_ns") or stat.st_mtime_ns)
            size = int(source_record.get("size") or stat.st_size)
            source_hash = str(source_record.get("file_hash") or file_hash(path) or "")
            cached = existing.get(rel) if isinstance(existing.get(rel), dict) else {}
            if (
                cached.get("status") in {"valid", "invalid"}
                and cached.get("mtime_ns") == mtime_ns
                and cached.get("size") == size
                and cached.get("file_hash") == source_hash
            ):
                entries[rel] = cached
                continue
            try:
                payload = _research_file_payload(root, path, include_markdown=True)
            except (OSError, UnicodeError, ValueError) as exc:
                entries[rel] = {
                    "path": rel,
                    "mtime_ns": mtime_ns,
                    "size": size,
                    "file_hash": source_hash,
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
                "mtime_ns": mtime_ns,
                "size": size,
                "file_hash": source_hash,
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
    if int(payload.get("artifact_schema_version") or 1) < ARTIFACT_SCHEMA_VERSION:
        _validate_stored_research_artifact_data_lineage(root, payload)
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
                if is_research_artifact_export_copy(resolved_root, safe):
                    continue
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


def _research_artifact_export_manifest_path(path: Path) -> Path:
    return path.with_name(f".{path.name}.tcx-export.json")


def _read_research_artifact_export_manifest(path: Path) -> dict[str, Any] | None:
    manifest_path = _research_artifact_export_manifest_path(path)
    try:
        mode = manifest_path.lstat().st_mode
    except FileNotFoundError:
        return None
    except OSError:
        return None
    if not stat.S_ISREG(mode):
        return None
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return manifest if isinstance(manifest, dict) else None


def _write_research_artifact_export_manifest(
    root: Path,
    target: Path,
    source: Path,
    artifact: dict[str, Any],
    source_bytes: bytes,
) -> None:
    source_hash = hashlib.sha256(source_bytes).hexdigest()
    manifest = {
        "schema_version": _RESEARCH_ARTIFACT_EXPORT_SCHEMA_VERSION,
        "marker": _RESEARCH_ARTIFACT_EXPORT_MARKER,
        "workflow_run_id": str(artifact.get("workflow_run_id") or ""),
        "artifact_id": str(artifact["artifact_id"]),
        "source_path": source.relative_to(root).as_posix(),
        "export_path": target.relative_to(root).as_posix(),
        "version": int(artifact["version"]),
        "content_hash": str(artifact["content_hash"]),
        "source_file_sha256": source_hash,
        "export_file_sha256": source_hash,
    }
    if (
        manifest["workflow_run_id"]
        and not _research_artifact_export_receipt_matches(root, manifest)
    ):
        raise ValueError("research artifact export requires an authenticated receipt")
    manifest["sidecar_signature"] = _research_artifact_export_sidecar_signature(
        root,
        manifest,
        create_signing_key=True,
    )
    atomic_write_text(
        _research_artifact_export_manifest_path(target),
        json.dumps(manifest, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
    )


def _validate_research_artifact_export_destination(
    root: Path,
    target: Path,
    source: Path,
    artifact: dict[str, Any],
) -> None:
    """Allow only a fresh or same-source managed export destination."""

    relative = target.relative_to(root)
    if target.suffix.lower() != ".md":
        raise ValueError("research artifact export_path must end with .md")
    if any(
        part in _RESEARCH_ARTIFACT_EXPORT_RESERVED_DIRECTORIES
        for part in relative.parts
    ):
        raise ValueError("research artifact export_path uses a reserved directory")

    manifest_path = _research_artifact_export_manifest_path(target)
    try:
        target_mode = target.lstat().st_mode
    except FileNotFoundError:
        target_mode = None
    except OSError as exc:
        raise ValueError("research artifact export destination is unreadable") from exc

    if target_mode is None:
        if manifest_path.exists() or manifest_path.is_symlink():
            raise ValueError("research artifact export destination has stale metadata")
        return
    if not stat.S_ISREG(target_mode):
        raise ValueError("research artifact export destination must be a regular file")
    if not is_research_artifact_export_copy(root, target):
        raise ValueError("research artifact export destination is occupied")

    manifest = _read_research_artifact_export_manifest(target)
    if manifest is None:
        raise ValueError("research artifact export destination has invalid metadata")
    if (
        manifest.get("source_path") != source.relative_to(root).as_posix()
        or manifest.get("artifact_id") != str(artifact["artifact_id"])
    ):
        raise ValueError(
            "research artifact export destination belongs to another artifact"
        )


def _publish_research_artifact_export_copy(
    root: Path,
    target: Path,
    source: Path,
    artifact: dict[str, Any],
    source_bytes: bytes,
) -> None:
    """Publish an export with rollback if either member of the pair fails."""

    manifest_path = _research_artifact_export_manifest_path(target)
    previous_target = target.read_bytes() if target.exists() else None
    previous_manifest = manifest_path.read_bytes() if manifest_path.exists() else None
    try:
        atomic_write_text(target, source_bytes.decode("utf-8"))
        _write_research_artifact_export_manifest(
            root,
            target,
            source,
            artifact,
            source_bytes,
        )
        if not is_research_artifact_export_copy(root, target):
            raise ValueError("research artifact export verification failed")
    except Exception:
        _restore_research_artifact_export_member(target, previous_target)
        _restore_research_artifact_export_member(manifest_path, previous_manifest)
        raise


def _prepare_research_artifact_export_workspace_binding(
    root: Path,
    artifact: dict[str, Any],
) -> None:
    if str(artifact.get("workflow_run_id") or ""):
        require_workspace_context_binding(root)
    else:
        persist_workspace_context_if_available(root)


def _restore_research_artifact_export_member(
    path: Path,
    previous: bytes | None,
) -> None:
    if previous is None:
        path.unlink(missing_ok=True)
        return
    atomic_write_text(path, previous.decode("utf-8"))


def is_research_artifact_export_copy(root: Path | str, path: Path) -> bool:
    """Return true only for an intact service-created noncanonical export."""

    try:
        resolved_root = Path(root).expanduser().resolve(strict=False)
        candidate = safe_workspace_path(
            resolved_root,
            path.relative_to(resolved_root),
            allowed_roots=RESEARCH_FILE_ROOTS,
        )
        relative = candidate.relative_to(resolved_root).as_posix()
        manifest = _read_research_artifact_export_manifest(candidate)
        if manifest is None:
            return False
        if (
            manifest.get("schema_version") != _RESEARCH_ARTIFACT_EXPORT_SCHEMA_VERSION
            or manifest.get("marker") != _RESEARCH_ARTIFACT_EXPORT_MARKER
            or manifest.get("export_path") != relative
            or not isinstance(manifest.get("workflow_run_id"), str)
            or not isinstance(manifest.get("artifact_id"), str)
            or not isinstance(manifest.get("source_path"), str)
            or type(manifest.get("version")) is not int
            or manifest["version"] < 1
            or any(
                not isinstance(manifest.get(field), str)
                or not re.fullmatch(r"[0-9a-f]{64}", manifest[field])
                for field in (
                    "content_hash",
                    "source_file_sha256",
                    "export_file_sha256",
                    "sidecar_signature",
                )
            )
        ):
            return False
        source_path = str(manifest["source_path"])
        if source_path == relative:
            return False
        if not hmac.compare_digest(
            str(manifest["sidecar_signature"]),
            _research_artifact_export_sidecar_signature(
                resolved_root,
                manifest,
                create_signing_key=False,
            ),
        ):
            return False
        if (
            manifest["workflow_run_id"]
            and not _research_artifact_export_receipt_matches(resolved_root, manifest)
        ):
            return False
        target_bytes = candidate.read_bytes()
        if not hmac.compare_digest(
            hashlib.sha256(target_bytes).hexdigest(),
            str(manifest["export_file_sha256"]),
        ) or not hmac.compare_digest(
            str(manifest["source_file_sha256"]),
            str(manifest["export_file_sha256"]),
        ):
            return False
        source = safe_workspace_path(
            resolved_root,
            source_path,
            allowed_roots=RESEARCH_FILE_ROOTS,
        )
        source_bytes: bytes | None = None
        if source.is_file() and not source.is_symlink():
            current = source.read_bytes()
            if hmac.compare_digest(
                hashlib.sha256(current).hexdigest(),
                str(manifest["source_file_sha256"]),
            ):
                source_bytes = current
        if source_bytes is None:
            archive = research_artifact_version_archive_path(
                resolved_root,
                str(manifest["artifact_id"]),
                int(manifest["version"]),
                str(manifest["content_hash"]),
            )
            if _validate_version_archive_path(resolved_root, archive):
                archived = archive.read_bytes()
                if hmac.compare_digest(
                    hashlib.sha256(archived).hexdigest(),
                    str(manifest["source_file_sha256"]),
                ):
                    source_bytes = archived
        if source_bytes is None or source_bytes != target_bytes:
            return False
        document = split_markdown_frontmatter(source_bytes.decode("utf-8"))
        return (
            str(document.frontmatter.get("artifact_id") or "")
            == str(manifest["artifact_id"])
            and _int_value(document.frontmatter.get("version"), default=0)
            == int(manifest["version"])
            and hmac.compare_digest(
                hashlib.sha256(document.body.encode("utf-8")).hexdigest(),
                str(manifest["content_hash"]),
            )
            and hmac.compare_digest(
                str(document.frontmatter.get("content_hash") or ""),
                str(manifest["content_hash"]),
            )
        )
    except (
        OSError,
        UnicodeError,
        ValueError,
        TypeError,
        json.JSONDecodeError,
        RuntimeMigrationError,
    ):
        return False


def _research_artifact_export_sidecar_signature(
    root: Path,
    manifest: dict[str, Any],
    *,
    create_signing_key: bool,
) -> str:
    from tradingcodex_service.application import artifact_bindings

    resolved_root = root.expanduser().resolve(strict=False)
    material = {
        "workspace_root": str(resolved_root),
        "sidecar": {
            key: value
            for key, value in manifest.items()
            if key != "sidecar_signature"
        },
    }
    return hmac.new(
        artifact_bindings._receipt_signing_key(
            resolved_root,
            create=create_signing_key,
        ),
        (
            _RESEARCH_ARTIFACT_EXPORT_SIGNATURE_DOMAIN
            + stable_hash(material).encode("ascii")
        ),
        hashlib.sha256,
    ).hexdigest()


def _research_artifact_export_receipt_matches(
    root: Path,
    manifest: dict[str, Any],
) -> bool:
    """Require the signed artifact receipt for this exact workspace binding."""

    try:
        from tradingcodex_service.application import artifact_bindings

        context = require_workspace_context_binding(root)
        receipt_path = artifact_bindings._receipt_path(
            root,
            {
                "workflow_run_id": manifest["workflow_run_id"],
                "artifact_id": manifest["artifact_id"],
                "artifact_version": manifest["version"],
                "content_hash": manifest["content_hash"],
            },
        )
        if receipt_path.is_symlink() or not receipt_path.is_file():
            return False
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        artifact_bindings._validate_receipt_signature(root, receipt, receipt_path)
        return (
            receipt.get("marker") == "tradingcodex-authenticated-research-artifact"
            and receipt.get("workspace_id") == context["workspace_id"]
            and receipt.get("workflow_run_id") == manifest["workflow_run_id"]
            and receipt.get("artifact_id") == manifest["artifact_id"]
            and receipt.get("artifact_version") == manifest["version"]
            and receipt.get("content_hash") == manifest["content_hash"]
            and receipt.get("artifact_path") == manifest["source_path"]
            and hmac.compare_digest(
                str(receipt.get("file_sha256") or ""),
                str(manifest["source_file_sha256"]),
            )
        )
    except (
        OSError,
        UnicodeError,
        ValueError,
        TypeError,
        json.JSONDecodeError,
        RuntimeMigrationError,
    ):
        return False


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
        "confidence_basis": str(frontmatter.get("confidence_basis") or ""),
        "summary": str(frontmatter.get("summary") or ""),
        "evidence_readiness": str(frontmatter.get("evidence_readiness") or ""),
        "action_readiness": str(frontmatter.get("action_readiness") or ""),
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
        "dataset_ids": _coerce_list(frontmatter.get("dataset_ids")),
        "dataset_manifest_hashes": (
            frontmatter.get("dataset_manifest_hashes")
            if isinstance(frontmatter.get("dataset_manifest_hashes"), dict)
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
        "calculation_run_ids": _coerce_list(frontmatter.get("calculation_run_ids")),
        "calculation_run_hashes": frontmatter.get("calculation_run_hashes") if isinstance(frontmatter.get("calculation_run_hashes"), dict) else {},
        "calculation_reuse_origins": frontmatter.get("calculation_reuse_origins") if isinstance(frontmatter.get("calculation_reuse_origins"), dict) else {},
        "knowledge_cutoff": str(frontmatter.get("knowledge_cutoff") or ""),
        "requirements": _coerce_list(frontmatter.get("requirements")),
        "decision_quality": frontmatter.get("decision_quality") if isinstance(frontmatter.get("decision_quality"), dict) else {},
        "memory": frontmatter.get("memory") if isinstance(frontmatter.get("memory"), dict) else {},
        "forecast": frontmatter.get("forecast") if isinstance(frontmatter.get("forecast"), dict) else {},
        "valuation": frontmatter.get("valuation") if isinstance(frontmatter.get("valuation"), dict) else {},
        "anti_overfit": frontmatter.get("anti_overfit") if isinstance(frontmatter.get("anti_overfit"), dict) else {},
        "follow_up_requests": _coerce_list(frontmatter.get("follow_up_requests")),
        "improvements": _coerce_list(frontmatter.get("improvements")),
        "created_by": str(frontmatter.get("created_by") or "workspace"),
        "recorded_at": str(frontmatter.get("recorded_at") or ""),
        "content_hash": content_hash,
        "version": _int_value(frontmatter.get("version"), default=1),
        "parent_artifact_id": str(frontmatter.get("parent_artifact_id") or ""),
        "updated_at": datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat(),
        "created_at": datetime.fromtimestamp(path.stat().st_ctime, tz=timezone.utc).isoformat(),
        "db_canonical": False,
        "file_sot": True,
        "workspace_native": True,
    }
    if payload["artifact_schema_version"] < ARTIFACT_SCHEMA_VERSION:
        _validate_stored_research_artifact_data_lineage(resolved_root, payload)
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
        if cutoff and _iso_datetime(known_at) > _iso_datetime(cutoff):
            raise ValueError(
                f"source snapshot {snapshot_id} known_at {known_at} is after artifact "
                f"knowledge_cutoff {cutoff}; set knowledge_cutoff at or after {known_at}"
            )
        hashes[snapshot_id] = str(snapshot["snapshot_hash"])
    return {snapshot_id: hashes[snapshot_id] for snapshot_id in sorted(hashes)}


def validated_research_artifact_data_lineage(
    root: Path | str,
    *,
    dataset_ids: list[Any],
    knowledge_cutoff: str,
) -> dict[str, str]:
    """Authenticate Dataset bindings and derive their sealed hashes."""

    workspace = Path(root).expanduser().resolve(strict=False)
    normalized_dataset_ids = _canonical_lineage_ids(
        dataset_ids,
        field="dataset_ids",
        pattern=r"dataset-[0-9a-f]{24}",
    )
    if not normalized_dataset_ids:
        return {}

    cutoff_text = str(knowledge_cutoff or "").strip()
    if not cutoff_text:
        raise ValueError("knowledge_cutoff is required when dataset_ids are supplied")
    cutoff = _normalized_iso(cutoff_text, "knowledge_cutoff")

    from tradingcodex_service.application.datasets import get_dataset_manifest

    dataset_hashes: dict[str, str] = {}
    for dataset_id in normalized_dataset_ids:
        manifest = get_dataset_manifest(
            workspace,
            {"dataset_id": dataset_id},
        )["dataset"]
        dataset_cutoff = _normalized_iso(
            manifest.get("knowledge_cutoff"),
            f"dataset {dataset_id} knowledge_cutoff",
        )
        if _iso_datetime(dataset_cutoff) > _iso_datetime(cutoff):
            raise ValueError(
                f"dataset {dataset_id} knowledge_cutoff {dataset_cutoff} is after "
                f"artifact knowledge_cutoff {cutoff}; set knowledge_cutoff at or "
                f"after {dataset_cutoff}"
            )
        dataset_hashes[dataset_id] = str(manifest["manifest_hash"])

    return {dataset_id: dataset_hashes[dataset_id] for dataset_id in sorted(dataset_hashes)}


def _validate_stored_research_artifact_data_lineage(
    root: Path,
    artifact: dict[str, Any],
) -> None:
    dataset_hashes = validated_research_artifact_data_lineage(
        root,
        dataset_ids=artifact.get("dataset_ids", []),
        knowledge_cutoff=str(artifact.get("knowledge_cutoff") or ""),
    )
    if artifact.get("dataset_manifest_hashes", {}) != dataset_hashes:
        raise ValueError(
            "research artifact dataset manifest hashes do not match current Datasets"
        )


def _canonical_lineage_ids(
    values: list[Any],
    *,
    field: str,
    pattern: str,
) -> list[str]:
    if not isinstance(values, list):
        raise ValueError(f"{field} must be an array")
    if len(values) > _RESEARCH_ARTIFACT_MAX_DATA_LINEAGE_IDS:
        raise ValueError(
            f"{field} must contain at most "
            f"{_RESEARCH_ARTIFACT_MAX_DATA_LINEAGE_IDS} ids"
        )
    normalized: list[str] = []
    for value in values:
        if not isinstance(value, str) or value != value.strip() or not value:
            raise ValueError(f"{field} must contain canonical service-issued ids")
        if re.fullmatch(pattern, value) is None:
            raise ValueError(f"{field} must contain canonical service-issued ids")
        normalized.append(value)
    if len(normalized) != len(set(normalized)):
        raise ValueError(f"{field} must not contain duplicates")
    return normalized


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


def _iso_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


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

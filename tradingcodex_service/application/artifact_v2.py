from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping


ARTIFACT_SCHEMA_VERSION = 2
HANDOFF_STATES = frozenset({"accepted", "revise", "blocked", "waiting"})
EVIDENCE_READINESS = frozenset({"factual", "screen", "decision-grade", "insufficient"})
ACTION_READINESS = frozenset({"research-only", "portfolio-review", "draft-eligible", "blocked"})
CONFIDENCE_LEVELS = frozenset({"low", "medium", "high"})
REQUIREMENTS = frozenset({"decision_quality", "forecast", "investor_context", "anti_overfit"})
MEMORY_REF_KINDS = frozenset({"decision_snapshot", "judgment_snapshot", "postmortem", "lesson"})
MEMORY_DELTA_DIRECTIONS = frozenset({"unchanged", "strengthened", "weakened", "reversed"})

LEGACY_READINESS_MAP: dict[str, tuple[str, str]] = {
    "factual-baseline": ("factual", "research-only"),
    "screen-grade": ("screen", "research-only"),
    "not-decision-ready": ("insufficient", "research-only"),
    "ready-for-portfolio-risk": ("decision-grade", "portfolio-review"),
    "ready-for-draft": ("decision-grade", "draft-eligible"),
    "blocked": ("insufficient", "blocked"),
}

LEGACY_WRITE_FIELDS = frozenset(
    {
        "workflow_type",
        "role",
        "created_by",
        "source_as_of",
        "readiness_label",
        "context_summary",
        "reader_summary",
        "next_recipient",
        "next_action",
        "input_artifact_hashes",
        "source_snapshot_hashes",
        "dataset_manifest_hashes",
        "calculation_run_hashes",
        "calculation_reuse_origins",
        "strategy_name",
        "strategy_hash",
        "investment_brain_id",
        "investment_brain_version",
        "investment_brain_content_digest",
        "investor_context_applied",
        "investor_context_hash",
        "decision_memory_consulted",
        "decision_memory_cutoff",
        "forecast_required",
        "decision_quality_required",
        "investor_context_gate_required",
        "anti_overfit_required",
        "anti_overfit_checks",
        "forecast_allowed",
        "forecast_block_reason",
        "forecast_target",
        "forecast_horizon",
        "probability",
        "probability_range",
        "base_rate",
        "missing_base_rate_note",
        "evidence_ids",
        "contrary_evidence",
        "resolution_source",
        "review_date",
        "update_triggers",
        "invalidation_conditions",
        "source_trust_notes",
        "scenario_cases",
        "scenario_summary",
        "thesis_lifecycle",
        "current_price_as_of",
        "market_anchor_as_of",
        "investor_context_gaps",
        "improvements",
        "export_path",
        "markdown_path",
    }
)
V2_WRITE_FIELDS = frozenset(
    {
        "artifact_id", "artifact_type", "title", "universe", "symbol", "markdown",
        "summary", "status", "lineage", "requirements", "decision_quality", "memory",
        "forecast", "valuation", "anti_overfit", "follow_up_requests", "principal_id",
    }
)


def canonicalize_write_request(args: Mapping[str, Any], *, append: bool = False) -> dict[str, Any]:
    """Validate the public v2 shape and flatten it for the canonical writer."""

    supplied_legacy = sorted(field for field in LEGACY_WRITE_FIELDS if field in args)
    if supplied_legacy:
        raise ValueError(
            "artifact v2 does not accept legacy fields: " + ", ".join(supplied_legacy)
        )
    unsupported = sorted(set(args) - V2_WRITE_FIELDS)
    if unsupported:
        raise ValueError("artifact v2 does not accept fields: " + ", ".join(unsupported))
    status = _object(args.get("status"), "status")
    lineage = _object(args.get("lineage"), "lineage")
    decision_quality = _optional_object(args.get("decision_quality"), "decision_quality")
    memory = _optional_object(args.get("memory"), "memory")
    forecast = _optional_object(args.get("forecast"), "forecast")
    valuation = _optional_object(args.get("valuation"), "valuation")
    anti_overfit = _optional_object(args.get("anti_overfit"), "anti_overfit")

    artifact_type = _required_text(args, "artifact_type")
    title = _required_text(args, "title")
    universe = _required_text(args, "universe")
    markdown = _required_text(args, "markdown")
    summary = _required_text(args, "summary")
    handoff = _enum(status, "handoff", HANDOFF_STATES)
    evidence_readiness = _enum(status, "evidence_readiness", EVIDENCE_READINESS)
    action_readiness = _enum(status, "action_readiness", ACTION_READINESS)
    confidence = _enum(status, "confidence", CONFIDENCE_LEVELS)
    confidence_basis = _required_text(status, "confidence_basis")
    _validate_readiness(evidence_readiness, action_readiness)

    requirements = _string_list(args.get("requirements"), "requirements")
    invalid_requirements = sorted(set(requirements) - REQUIREMENTS)
    if invalid_requirements:
        raise ValueError("unsupported artifact requirements: " + ", ".join(invalid_requirements))
    if len(requirements) != len(set(requirements)):
        raise ValueError("requirements must not contain duplicates")

    workflow_run_id = _text(lineage.get("workflow_run_id"))
    if not workflow_run_id and artifact_type == "synthesis_report":
        raise ValueError("synthesis_report requires lineage.workflow_run_id")
    artifact_id = _text(args.get("artifact_id"))
    if artifact_type == "synthesis_report" and artifact_id:
        raise ValueError("synthesis_report artifact_id is service-derived from workflow_run_id")
    if append and not artifact_id and artifact_type != "synthesis_report":
        raise ValueError("append_research_artifact_version requires artifact_id")

    missing_evidence = _string_list(status.get("missing_evidence"), "status.missing_evidence")
    blocked_actions = _string_list(status.get("blocked_actions"), "status.blocked_actions")
    input_ids = _string_list(lineage.get("input_artifact_ids"), "lineage.input_artifact_ids")
    source_ids = _string_list(lineage.get("source_snapshot_ids"), "lineage.source_snapshot_ids")
    dataset_ids = _string_list(lineage.get("dataset_ids"), "lineage.dataset_ids")
    calculation_ids = _string_list(lineage.get("calculation_run_ids"), "lineage.calculation_run_ids")
    if artifact_type == "synthesis_report" and not input_ids:
        raise ValueError("synthesis_report requires at least one lineage.input_artifact_id")

    normalized_memory = _normalize_memory(memory) if memory is not None else None
    normalized_decision_quality = (
        _normalize_decision_quality(decision_quality)
        if decision_quality is not None
        else None
    )
    normalized_forecast = _normalize_forecast(forecast) if forecast is not None else None
    normalized_valuation = _compact(valuation) if valuation is not None else None
    normalized_anti_overfit = _compact(anti_overfit) if anti_overfit is not None else None

    if "decision_quality" in requirements and normalized_decision_quality is None:
        raise ValueError("decision_quality requirement requires decision_quality")
    if "forecast" in requirements and normalized_forecast is None:
        raise ValueError("forecast requirement requires forecast posture")
    if "anti_overfit" in requirements and normalized_anti_overfit is None:
        raise ValueError("anti_overfit requirement requires anti_overfit")

    flat = {
        "artifact_schema_version": ARTIFACT_SCHEMA_VERSION,
        "artifact_id": artifact_id,
        "artifact_type": artifact_type,
        "universe": universe,
        "symbol": _text(args.get("symbol")).upper(),
        "title": title,
        "markdown": markdown,
        "summary": summary,
        "handoff_state": handoff,
        "evidence_readiness": evidence_readiness,
        "action_readiness": action_readiness,
        "confidence": confidence,
        "confidence_basis": confidence_basis,
        "missing_evidence": missing_evidence,
        "blocked_actions": blocked_actions,
        "requirements": requirements,
        "workflow_run_id": workflow_run_id,
        "knowledge_cutoff": _text(lineage.get("knowledge_cutoff")),
        "input_artifact_ids": input_ids,
        "source_snapshot_ids": source_ids,
        "dataset_ids": dataset_ids,
        "calculation_run_ids": calculation_ids,
        "evidence_lane": _text(lineage.get("evidence_lane")),
        "research_spec_id": _text(lineage.get("research_spec_id")),
        "replay_manifest_id": _text(lineage.get("replay_manifest_id")),
        "decision_quality": normalized_decision_quality,
        "memory": normalized_memory,
        "forecast": normalized_forecast,
        "valuation": normalized_valuation,
        "anti_overfit": normalized_anti_overfit,
        "follow_up_requests": _object_list(args.get("follow_up_requests"), "follow_up_requests"),
    }
    return {key: value for key, value in flat.items() if value not in (None, "")}


def compact_frontmatter(canonical: Mapping[str, Any]) -> dict[str, Any]:
    """Return the human-readable v2 envelope; receipt-only fields stay internal."""

    ordered_fields = (
        "artifact_schema_version",
        "artifact_id",
        "artifact_type",
        "title",
        "universe",
        "symbol",
        "workflow_run_id",
        "producer_role",
        "version",
        "recorded_at",
        "knowledge_cutoff",
        "content_hash",
        "handoff_state",
        "evidence_readiness",
        "action_readiness",
        "confidence",
        "confidence_basis",
        "summary",
        "requirements",
        "input_artifact_ids",
        "source_snapshot_ids",
        "dataset_ids",
        "calculation_run_ids",
        "evidence_lane",
        "research_spec_id",
        "replay_manifest_id",
        "missing_evidence",
        "blocked_actions",
        "decision_quality",
        "memory",
        "forecast",
        "valuation",
        "anti_overfit",
        "follow_up_requests",
    )
    result: dict[str, Any] = {}
    for field in ordered_fields:
        value = _compact(canonical.get(field))
        if value not in (None, "", [], {}, False):
            result[field] = value
    return result


def project_artifact(flat: Mapping[str, Any], *, include_markdown: bool = False) -> dict[str, Any]:
    """Expose only the public v2 response, adapting immutable v1 files as needed."""

    schema_version = int(flat.get("artifact_schema_version") or 1)
    authentication = (
        flat.get("authentication")
        if isinstance(flat.get("authentication"), dict)
        else {}
    )
    receipt_lineage = authentication.get("run_lineage")
    if schema_version >= ARTIFACT_SCHEMA_VERSION and isinstance(receipt_lineage, dict):
        flat = {**flat, **receipt_lineage}
    warnings: list[str] = []
    if schema_version >= ARTIFACT_SCHEMA_VERSION:
        evidence_readiness = _text(flat.get("evidence_readiness")) or "insufficient"
        action_readiness = _text(flat.get("action_readiness")) or "research-only"
        summary = _text(flat.get("summary"))
        confidence = _text(flat.get("confidence")) or "low"
        confidence_basis = _text(flat.get("confidence_basis"))
    else:
        legacy_readiness = _text(flat.get("readiness_label"))
        mapped = LEGACY_READINESS_MAP.get(legacy_readiness)
        if mapped is None:
            mapped = ("insufficient", "research-only")
            if legacy_readiness:
                warnings.append(f"legacy_readiness_unmapped:{legacy_readiness}")
        evidence_readiness, action_readiness = mapped
        summary = _text(flat.get("reader_summary")) or _text(flat.get("context_summary"))
        raw_confidence = flat.get("confidence")
        confidence = _text(raw_confidence)
        if confidence not in CONFIDENCE_LEVELS:
            confidence = "low"
            if raw_confidence not in (None, ""):
                warnings.append(f"legacy_confidence_unmapped:{raw_confidence}")
        confidence_basis = "Legacy artifact; confidence rationale was not stored separately."
        warnings.append("legacy_artifact_v1")

    flat_input_hashes = (
        flat.get("input_artifact_hashes")
        if isinstance(flat.get("input_artifact_hashes"), dict)
        else {}
    )
    flat_input_versions = (
        flat.get("input_artifact_versions")
        if isinstance(flat.get("input_artifact_versions"), dict)
        else {}
    )
    input_hashes = flat_input_hashes or authentication.get(
        "input_artifact_hashes", {}
    )
    input_versions = flat_input_versions or authentication.get(
        "input_artifact_versions", {}
    )
    inputs = [
        {
            "id": artifact_id,
            **({"version": input_versions[artifact_id]} if artifact_id in input_versions else {}),
            **({"content_hash": input_hashes[artifact_id]} if artifact_id in input_hashes else {}),
        }
        for artifact_id in _strings(flat.get("input_artifact_ids"))
    ]
    artifact = {
        "schema_version": ARTIFACT_SCHEMA_VERSION,
        "source_schema_version": schema_version,
        "id": _text(flat.get("artifact_id")),
        "type": _text(flat.get("artifact_type")),
        "title": _text(flat.get("title")),
        "summary": summary,
        "subject": _compact({
            "universe": _text(flat.get("universe")),
            "symbol": _text(flat.get("symbol")),
        }),
        "status": {
            "handoff": _text(flat.get("handoff_state")) or "waiting",
            "evidence_readiness": evidence_readiness,
            "action_readiness": action_readiness,
            "confidence": confidence,
            "confidence_basis": confidence_basis,
            "missing_evidence": _strings(flat.get("missing_evidence")),
            "blocked_actions": _strings(flat.get("blocked_actions")),
        },
        "lineage": {
            "workflow_run_id": _text(flat.get("workflow_run_id")),
            "producer_role": _text(flat.get("producer_role")) or _text(flat.get("role")),
            "version": int(flat.get("version") or 1),
            "recorded_at": _text(flat.get("recorded_at")),
            "knowledge_cutoff": _text(flat.get("knowledge_cutoff")),
            "input_artifacts": inputs,
            "source_snapshot_ids": _strings(flat.get("source_snapshot_ids")),
            "dataset_ids": _strings(flat.get("dataset_ids")),
            "calculation_run_ids": _strings(flat.get("calculation_run_ids")),
            "evidence_lane": _text(flat.get("evidence_lane")),
            "research_spec_id": _text(flat.get("research_spec_id")),
            "replay_manifest_id": _text(flat.get("replay_manifest_id")),
            "strategy": _compact({
                "name": _text(flat.get("strategy_name")),
                "content_hash": _text(flat.get("strategy_hash")),
            }),
            "investment_brain": _compact({
                "id": _text(flat.get("investment_brain_id")),
                "version": _text(flat.get("investment_brain_version")),
                "content_digest": _text(flat.get("investment_brain_content_digest")),
            }),
            "investor_context": _compact({
                "applied": bool(flat.get("investor_context_applied")),
                "content_hash": _text(flat.get("investor_context_hash")),
            }),
        },
        "requirements": _strings(flat.get("requirements")),
        "decision_quality": _compact(flat.get("decision_quality")),
        "memory": _compact(flat.get("memory")),
        "forecast": _compact(flat.get("forecast")),
        "valuation": _compact(flat.get("valuation")),
        "anti_overfit": _compact(flat.get("anti_overfit")),
        "follow_up_requests": _compact(flat.get("follow_up_requests")),
        "path": _text(flat.get("path")) or _text(flat.get("export_path")),
        "content_hash": _text(flat.get("content_hash")),
        "authentication": _compact(flat.get("authentication")),
        "compatibility_warnings": warnings,
    }
    artifact = _compact_preserving_empty_status(artifact)
    response: dict[str, Any] = {"artifact": artifact}
    if include_markdown and "markdown" in flat:
        response["markdown"] = str(flat.get("markdown") or "")
    if isinstance(flat.get("markdown_window"), dict):
        response["markdown_window"] = flat["markdown_window"]
    return response


def project_card(flat: Mapping[str, Any]) -> dict[str, Any]:
    projected = project_artifact(flat)["artifact"]
    return {
        key: projected[key]
        for key in (
            "schema_version",
            "source_schema_version",
            "id",
            "type",
            "title",
            "summary",
            "subject",
            "status",
            "requirements",
            "path",
            "compatibility_warnings",
        )
        if key in projected
    } | {
        "workflow_run_id": projected.get("lineage", {}).get("workflow_run_id", ""),
        "producer_role": projected.get("lineage", {}).get("producer_role", ""),
        "recorded_at": projected.get("lineage", {}).get("recorded_at", ""),
    }


def _normalize_decision_quality(value: Mapping[str, Any]) -> dict[str, Any]:
    result = {
        "review_artifact_ids": _string_list(value.get("review_artifact_ids"), "decision_quality.review_artifact_ids"),
        "update_triggers": _string_list(value.get("update_triggers"), "decision_quality.update_triggers"),
        "invalidation_conditions": _string_list(value.get("invalidation_conditions"), "decision_quality.invalidation_conditions"),
    }
    compact = _compact(result)
    if not isinstance(compact, dict):
        raise ValueError("decision_quality must contain review artifacts, update triggers, or invalidation conditions")
    return compact


def _normalize_memory(value: Mapping[str, Any]) -> dict[str, Any]:
    cutoff = _required_text(value, "cutoff")
    _parse_datetime(cutoff, "memory.cutoff")
    initial_view = _required_text(value, "initial_view")
    refs = _object_list(value.get("refs"), "memory.refs")
    if not refs:
        raise ValueError("memory.refs must contain at least one reference")
    normalized_refs = []
    for index, ref in enumerate(refs):
        kind = _enum(ref, "kind", MEMORY_REF_KINDS, prefix=f"memory.refs[{index}]")
        normalized_refs.append({"kind": kind, "id": _required_text(ref, "id")})
    delta = _object(value.get("delta"), "memory.delta")
    return {
        "cutoff": cutoff,
        "initial_view": initial_view,
        "refs": normalized_refs,
        "delta": {
            "direction": _enum(delta, "direction", MEMORY_DELTA_DIRECTIONS, prefix="memory.delta"),
            "summary": _required_text(delta, "summary"),
        },
    }


def _normalize_forecast(value: Mapping[str, Any]) -> dict[str, Any]:
    posture = _enum(value, "posture", frozenset({"eligible", "blocked"}))
    block_reason = _text(value.get("block_reason"))
    if posture == "blocked" and not block_reason:
        raise ValueError("forecast.block_reason is required when forecast posture is blocked")
    if posture == "eligible" and block_reason:
        raise ValueError("forecast.block_reason must be omitted when forecast posture is eligible")
    return {"posture": posture, **({"block_reason": block_reason} if block_reason else {})}


def _validate_readiness(evidence: str, action: str) -> None:
    if action in {"portfolio-review", "draft-eligible"} and evidence != "decision-grade":
        raise ValueError(f"action_readiness {action} requires decision-grade evidence_readiness")


def _parse_datetime(value: str, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be an RFC 3339 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must include a timezone")
    return parsed


def _object(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be an object")
    return dict(value)


def _optional_object(value: Any, field: str) -> dict[str, Any] | None:
    if value in (None, ""):
        return None
    return _object(value, field)


def _object_list(value: Any, field: str) -> list[dict[str, Any]]:
    if value in (None, ""):
        return []
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise ValueError(f"{field} must be an array of objects")
    return [dict(item) for item in value]


def _required_text(value: Mapping[str, Any], field: str) -> str:
    text = _text(value.get(field))
    if not text:
        raise ValueError(f"{field} is required")
    return text


def _text(value: Any) -> str:
    return str(value).strip() if value not in (None, "") else ""


def _strings(value: Any) -> list[str]:
    return [str(item) for item in value] if isinstance(value, list) else []


def _string_list(value: Any, field: str) -> list[str]:
    if value in (None, ""):
        return []
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise ValueError(f"{field} must be an array of non-empty strings")
    return [item.strip() for item in value]


def _enum(
    value: Mapping[str, Any],
    field: str,
    allowed: frozenset[str],
    *,
    prefix: str = "",
) -> str:
    text = _required_text(value, field)
    if text not in allowed:
        label = f"{prefix}.{field}" if prefix else field
        raise ValueError(f"{label} must be one of: {', '.join(sorted(allowed))}")
    return text


def _compact(value: Any) -> Any:
    if value in (None, "", False):
        return None
    if isinstance(value, list):
        items = [_compact(item) for item in value]
        compact = [item for item in items if item not in (None, "", [], {})]
        return compact or None
    if isinstance(value, dict):
        compact = {
            str(key): projected
            for key, item in value.items()
            if (projected := _compact(item)) not in (None, "", [], {})
        }
        return compact or None
    return value


def _compact_preserving_empty_status(value: dict[str, Any]) -> dict[str, Any]:
    result = _compact(value)
    result = result if isinstance(result, dict) else {}
    status = result.setdefault("status", {})
    if isinstance(status, dict):
        status.setdefault("missing_evidence", [])
        status.setdefault("blocked_actions", [])
    result.setdefault("requirements", [])
    result.setdefault("compatibility_warnings", [])
    return result


def authenticate_artifact_for_read(
    workspace_root: Any,
    flat: Mapping[str, Any],
) -> dict[str, Any]:
    """Verify a run-bound artifact before any public read projection."""

    artifact = dict(flat)
    if _text(artifact.get("workflow_run_id")):
        from tradingcodex_service.application.artifact_bindings import (
            verify_authenticated_artifact_binding,
        )

        artifact["authentication"] = verify_authenticated_artifact_binding(
            workspace_root,
            artifact,
        )
    return artifact


def project_authenticated_card(
    workspace_root: Any,
    flat: Mapping[str, Any],
) -> dict[str, Any]:
    """Return a compact card only after the run-bound receipt verifies."""

    return project_card(authenticate_artifact_for_read(workspace_root, flat))

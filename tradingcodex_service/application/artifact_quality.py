from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from tradingcodex_service.application.common import safe_workspace_path
from tradingcodex_service.application.markdown_preview import split_markdown_frontmatter
from tradingcodex_service.application.research import RESEARCH_FILE_ROOTS

FORECAST_FILE_ROOTS = (Path("trading/forecasts"),)
QUALITY_FILE_ROOTS = RESEARCH_FILE_ROOTS + (
    *FORECAST_FILE_ROOTS,
    Path("trading/decisions"),
    Path("trading/audit"),
)

HANDOFF_STATES = {"accepted", "revise", "blocked", "waiting"}
FOLLOW_UP_TRIGGERS = {
    "coverage_gap",
    "freshness_gap",
    "contradiction",
    "material_driver",
    "assumption_change",
    "method_gap",
    "scope_boundary",
    "forecast_gap",
    "investor_context_gap",
}
FOLLOW_UP_MATERIALITY = {"low", "medium", "high"}
FOLLOW_UP_CONSENT_POSTURE = {"no_consent_expected", "consent_required", "unknown"}
FOLLOW_UP_ROLES = {
    "fundamental-analyst",
    "technical-analyst",
    "news-analyst",
    "macro-analyst",
    "instrument-analyst",
    "valuation-analyst",
    "portfolio-manager",
    "risk-manager",
    "judgment-reviewer",
}
IMPROVEMENT_TYPES = {
    "evidence_gap",
    "source_quality",
    "thesis_update",
    "assumption_error",
    "valuation_sensitivity",
    "forecast_calibration",
    "risk_miss",
    "portfolio_context_gap",
    "decision_readiness",
    "contradiction",
}
CLAIM_TAG_PATTERN = re.compile(r"\[(factual|inference|assumption)\]", re.IGNORECASE)
READINESS_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*")
ABSENCE_PLACEHOLDER_PATTERN = re.compile(
    r"^(?:not\s+provided|not\s+available|not\s+applicable|unknown|n/?a|none|null|missing|tbd)\b",
    re.IGNORECASE,
)
STRICT_MARKDOWN_REQUIRED_FIELDS = (
    "artifact_id",
    "artifact_type",
    "universe",
    "role",
    "title",
    "source_as_of",
    "readiness_label",
    "context_summary",
    "handoff_state",
    "confidence",
    "next_recipient",
)
STRICT_MARKDOWN_REQUIRED_KEYS = (
    "missing_evidence",
    "blocked_actions",
    "source_snapshot_ids",
)
READER_UX_RECOMMENDED_FIELDS = (
    "reader_summary",
    "next_action",
)
JUDGMENT_REVIEW_FIELDS = (
    "contrary_evidence",
    "update_triggers",
    "invalidation_conditions",
    "source_trust_notes",
)
RUN_CARD_REQUIRED_KEYS = (
    "schema_version",
    "artifact_type",
    "card_id",
    "related_artifact_path",
    "generated_at",
    "config_hash",
    "input_refs",
    "data_source_refs",
    "artifact_hashes",
    "warnings",
    "source_limitations",
    "authority",
    "blocked_actions",
)
RUN_CARD_NONBLANK_FIELDS = (
    "artifact_type",
    "card_id",
    "related_artifact_path",
    "generated_at",
    "config_hash",
    "authority",
)
RUN_CARD_LIST_FIELDS = ("input_refs", "data_source_refs", "warnings", "source_limitations", "blocked_actions")
RUN_CARD_DICT_FIELDS = ("artifact_hashes", "metrics")
VALIDATION_CARD_REQUIRED_KEYS = (
    "schema_version",
    "artifact_type",
    "card_id",
    "related_artifact_path",
    "generated_at",
    "validation_scope",
    "evidence_quality_label",
    "input_refs",
    "data_source_refs",
    "artifact_hashes",
    "checks",
    "warnings",
    "source_limitations",
    "authority",
    "blocked_actions",
)
VALIDATION_CARD_NONBLANK_FIELDS = (
    "artifact_type",
    "card_id",
    "related_artifact_path",
    "generated_at",
    "validation_scope",
    "evidence_quality_label",
    "authority",
)
VALIDATION_CARD_LIST_FIELDS = ("input_refs", "data_source_refs", "warnings", "source_limitations", "blocked_actions")
VALIDATION_CARD_DICT_FIELDS = ("artifact_hashes", "checks", "metrics")
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
EVIDENCE_QUALITY_LABELS = {"not_validated", "weak", "suggestive", "validated", "blocked"}
THESIS_LIFECYCLE_STATES = {"exploring", "testing", "validated", "rejected", "monitoring"}
VALIDATION_CHECK_STATUSES = {"pass", "fail", "not_applicable", "not_assessed"}
FORECAST_EVENT_TYPES = {"issued", "revised", "resolved", "dispute_resolved", "scored"}
FORECAST_TARGET_TYPES = {"binary", "categorical", "continuous"}


def evaluate_artifact_quality(workspace_root: Path | str, artifact_path: str, *, strict: bool = False) -> dict[str, Any]:
    root = Path(workspace_root).expanduser().resolve(strict=False)
    result = _empty_quality_result(artifact_path, strict=strict)

    try:
        path = safe_workspace_path(root, artifact_path, allowed_roots=QUALITY_FILE_ROOTS)
    except ValueError as exc:
        result["status"] = "fail"
        result["warnings"].append(str(exc))
        return result

    rel = path.relative_to(root).as_posix()
    result["path"] = rel
    result["artifact_type"] = classify_artifact_path(rel)
    if not path.exists() or not path.is_file():
        result["status"] = "fail"
        result["warnings"].append("artifact path does not exist")
        return result

    return _evaluate_artifact_text(
        rel,
        path.read_text(encoding="utf-8"),
        result,
        strict=strict,
    )


def evaluate_artifact_quality_text(
    artifact_path: str,
    text: str,
    *,
    strict: bool = False,
) -> dict[str, Any]:
    """Evaluate intended artifact bytes before their stable file is published."""

    result = _empty_quality_result(artifact_path, strict=strict)
    return _evaluate_artifact_text(
        artifact_path,
        text,
        result,
        strict=strict,
    )


def _empty_quality_result(artifact_path: str, *, strict: bool) -> dict[str, Any]:
    return {
        "path": artifact_path,
        "exists": False,
        "bytes": 0,
        "non_empty": False,
        "artifact_type": classify_artifact_path(artifact_path),
        "json_valid": None,
        "strict": strict,
        "frontmatter": {},
        "claim_tags": {"factual": 0, "inference": 0, "assumption": 0},
        "context_efficiency": {
            "estimated_tokens": 0,
            "body_estimated_tokens": 0,
            "context_summary_present": False,
            "context_summary_chars": 0,
            "recommended_use": "pass by artifact path; inspect full content only when needed",
        },
        "decision_quality": {"status": "not_evaluated", "checks": []},
        "required_fields_missing": [],
        "warnings": [],
    }


def _evaluate_artifact_text(
    artifact_path: str,
    text: str,
    result: dict[str, Any],
    *,
    strict: bool,
) -> dict[str, Any]:
    result["path"] = artifact_path
    result["artifact_type"] = classify_artifact_path(artifact_path)
    result["exists"] = True
    result["bytes"] = len(text.encode("utf-8"))
    result["non_empty"] = bool(text.strip())
    result["context_efficiency"]["estimated_tokens"] = estimate_tokens(text)

    if artifact_path.endswith(".jsonl"):
        _evaluate_jsonl(text, result, strict=strict)
    elif artifact_path.endswith(".json"):
        _evaluate_json(text, result, strict=strict)
    elif artifact_path.endswith(".md"):
        _evaluate_markdown(text, result, strict=strict)

    blocking_missing = bool(result["required_fields_missing"]) if strict else False
    result["status"] = "fail" if not result["non_empty"] or result["json_valid"] is False or blocking_missing else "pass"
    return result


def evaluate_decision_quality(
    workspace_root: Path | str,
    artifact_path: str,
    *,
    strict: bool = True,
) -> dict[str, Any]:
    return evaluate_artifact_quality(workspace_root, artifact_path, strict=strict)


def _evaluate_json(text: str, result: dict[str, Any], *, strict: bool) -> None:
    try:
        payload = _loads_json_strict(text)
        result["json_valid"] = True
    except Exception:
        result["json_valid"] = False
        return
    if result.get("artifact_type") == "evidence_run_card" and not isinstance(payload, dict):
        _run_card_issue(result, strict, "json_object")
        return
    if result.get("artifact_type") == "validation_card" and not isinstance(payload, dict):
        _validation_card_issue(result, strict, "json_object")
        return
    if result.get("artifact_type") == "source_snapshot" and not isinstance(payload, dict):
        result["warnings"].append("source snapshot must be a JSON object")
        if strict:
            result["required_fields_missing"].append("source_snapshot.json_object")
        return
    if isinstance(payload, dict) and _is_run_card_payload(payload, result):
        _evaluate_run_card(payload, result, strict=strict)
    elif isinstance(payload, dict) and _is_validation_card_payload(payload, result):
        _evaluate_validation_card(payload, result, strict=strict)
    elif isinstance(payload, dict) and result.get("artifact_type") == "source_snapshot":
        _evaluate_source_snapshot(payload, result, strict=strict)


def _evaluate_jsonl(text: str, result: dict[str, Any], *, strict: bool) -> None:
    records = []
    errors = []
    for index, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            record = _loads_json_strict(line)
        except Exception:
            errors.append(f"line {index} is not valid JSON")
            continue
        if not isinstance(record, dict):
            errors.append(f"line {index} must be a JSON object")
            continue
        records.append(record)
        errors.extend(_forecast_record_errors(record, index))
    result["json_valid"] = not errors
    result["forecast_record_count"] = len(records)
    if errors:
        result["warnings"].extend(errors)
        if strict:
            result["required_fields_missing"].extend("forecast_ledger_record" for _ in errors[:1])
    result["decision_quality"] = {
        "status": "fail" if errors else "pass",
        "checks": ["forecast_ledger_schema"],
    }


def _evaluate_markdown(text: str, result: dict[str, Any], *, strict: bool) -> None:
    document = split_markdown_frontmatter(text)
    frontmatter = document.frontmatter
    result["frontmatter"] = {
        key: frontmatter.get(key)
        for key in (
            "artifact_id",
            "artifact_type",
            "universe",
            "role",
            "source_as_of",
            "readiness_label",
            "context_summary",
            "reader_summary",
            "handoff_state",
            "confidence",
            "next_recipient",
            "next_action",
            "missing_evidence",
            "blocked_actions",
            "source_snapshot_ids",
            "workflow_run_id",
            "producer_role",
            "artifact_schema_version",
            "input_artifact_ids",
            "input_artifact_hashes",
            "knowledge_cutoff",
            "workflow_type",
            "decision_quality_required",
            "forecast_required",
            "investor_context_gate_required",
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
            "source_trust_notes",
            "resolution_source",
            "review_date",
            "update_triggers",
            "invalidation_conditions",
            "scenario_cases",
            "scenario_summary",
            "thesis_lifecycle",
            "current_price_as_of",
            "market_anchor_as_of",
            "investor_context_gaps",
            "anti_overfit_required",
            "anti_overfit_checks",
            "follow_up_requests",
            "improvements",
        )
        if key in frontmatter
    }
    body = document.body or text
    context_summary = str(frontmatter.get("context_summary") or "")
    result["context_efficiency"].update({
        "body_estimated_tokens": estimate_tokens(body),
        "context_summary_present": bool(context_summary.strip()),
        "context_summary_chars": len(context_summary),
        "recommended_use": "pass artifact path plus context_summary; open full markdown only for load-bearing evidence checks",
    })
    tags = [match.group(1).lower() for match in CLAIM_TAG_PATTERN.finditer(body)]
    result["claim_tags"] = {name: tags.count(name) for name in ("factual", "inference", "assumption")}
    if _is_run_card_payload(frontmatter, result):
        _evaluate_run_card(frontmatter, result, strict=strict)
        result["frontmatter"] = {key: frontmatter.get(key) for key in RUN_CARD_REQUIRED_KEYS + ("metrics", "validation_summary", "created_by") if key in frontmatter}
        return
    if _is_validation_card_payload(frontmatter, result):
        _evaluate_validation_card(frontmatter, result, strict=strict)
        result["frontmatter"] = {key: frontmatter.get(key) for key in VALIDATION_CARD_REQUIRED_KEYS + ("metrics", "validation_summary", "created_by") if key in frontmatter}
        return

    missing_fields = [field for field in STRICT_MARKDOWN_REQUIRED_FIELDS if _is_blank(frontmatter.get(field))]
    missing_keys = [field for field in STRICT_MARKDOWN_REQUIRED_KEYS if field not in frontmatter]
    missing_reader_fields = [field for field in READER_UX_RECOMMENDED_FIELDS if _is_blank(frontmatter.get(field))]
    if strict:
        result["required_fields_missing"].extend(missing_fields + missing_keys)
        if not tags:
            result["required_fields_missing"].append("claim_tags")
    else:
        result["warnings"].extend(f"missing {field}" for field in missing_fields + missing_keys)
        if not tags:
            result["warnings"].append("missing claim tags")
    result["warnings"].extend(f"missing {field} for non-expert first-read UX" for field in missing_reader_fields)

    handoff_state = str(frontmatter.get("handoff_state") or "").strip()
    if handoff_state and handoff_state not in HANDOFF_STATES:
        message = f"handoff_state must be one of {sorted(HANDOFF_STATES)}"
        if strict:
            result["required_fields_missing"].append("valid_handoff_state")
        result["warnings"].append(message)
    if handoff_state == "accepted" and frontmatter.get("workflow_run_id"):
        binding_fields = (
            "producer_role",
            "artifact_schema_version",
            "input_artifact_ids",
            "input_artifact_hashes",
            "source_snapshot_hashes",
            "content_hash",
        )
        for field in binding_fields:
            if field in {
                "input_artifact_ids",
                "input_artifact_hashes",
                "source_snapshot_hashes",
            }:
                missing = field not in frontmatter
            else:
                missing = _is_blank(frontmatter.get(field))
            if missing:
                _decision_issue(result, strict, f"artifact_binding.{field}")
        if str(frontmatter.get("artifact_type") or "") == "synthesis_report" and not frontmatter.get("input_artifact_ids"):
            _decision_issue(result, strict, "artifact_binding.synthesis_inputs")

    confidence = frontmatter.get("confidence")
    if confidence not in (None, "") and not _confidence_looks_valid(confidence):
        result["warnings"].append("confidence should be low/medium/high or a numeric probability/score")
    if result["context_efficiency"]["body_estimated_tokens"] > 6000:
        result["warnings"].append("large artifact body; downstream roles should consume context_summary and targeted excerpts")
    if context_summary and len(context_summary) > 1200:
        result["warnings"].append("context_summary is long; keep it brief enough for subagent handoffs")
    reader_summary = str(frontmatter.get("reader_summary") or "")
    if reader_summary and len(reader_summary) > 800:
        result["warnings"].append("reader_summary is long; keep it short enough for non-expert first-read UX")

    for field in ("missing_evidence", "blocked_actions", "source_snapshot_ids"):
        if field in frontmatter and not isinstance(frontmatter.get(field), list):
            result["warnings"].append(f"{field} should be a list")

    _evaluate_follow_up_requests(frontmatter, result, strict=strict)
    _evaluate_improvements(frontmatter, result, strict=strict)

    if handoff_state in {"revise", "blocked"}:
        has_missing = bool(frontmatter.get("missing_evidence"))
        has_blocked = bool(frontmatter.get("blocked_actions"))
        if not has_missing and not has_blocked:
            message = f"{handoff_state} handoffs should name missing evidence or blocked actions"
            result["warnings"].append(message)
            if strict:
                result["required_fields_missing"].append("missing_evidence_or_blocked_actions")

    _evaluate_decision_quality(frontmatter, result, strict=strict)


def _evaluate_follow_up_requests(frontmatter: dict[str, Any], result: dict[str, Any], *, strict: bool) -> None:
    if "follow_up_requests" not in frontmatter:
        return
    requests = frontmatter.get("follow_up_requests")
    if requests in (None, ""):
        return
    if not isinstance(requests, list):
        _follow_up_issue(result, strict, "follow_up_requests must be a list")
        return
    for index, item in enumerate(requests, start=1):
        if not isinstance(item, dict):
            _follow_up_issue(result, strict, f"follow_up_requests[{index}] must be an object")
            continue
        missing = [field for field in ("trigger", "suggested_role", "question", "reason", "materiality") if _is_blank(item.get(field))]
        if missing:
            _follow_up_issue(result, strict, f"follow_up_requests[{index}] missing fields: {', '.join(missing)}")
        trigger = str(item.get("trigger") or "")
        if trigger and trigger not in FOLLOW_UP_TRIGGERS:
            _follow_up_issue(result, strict, f"follow_up_requests[{index}] trigger must be one of {sorted(FOLLOW_UP_TRIGGERS)}")
        role = str(item.get("suggested_role") or "")
        if role and role not in FOLLOW_UP_ROLES:
            _follow_up_issue(result, strict, f"follow_up_requests[{index}] suggested_role must be a non-execution fixed role")
        materiality = str(item.get("materiality") or "")
        if materiality and materiality not in FOLLOW_UP_MATERIALITY:
            _follow_up_issue(result, strict, f"follow_up_requests[{index}] materiality must be low, medium, or high")
        consent = str(item.get("suggested_consent_posture") or "unknown")
        if consent not in FOLLOW_UP_CONSENT_POSTURE:
            _follow_up_issue(result, strict, f"follow_up_requests[{index}] suggested_consent_posture is advisory and must be no_consent_expected, consent_required, or unknown")
        if "within_current_lane" in item or "requires_user_consent" in item:
            _follow_up_issue(result, strict, f"follow_up_requests[{index}] must not declare authoritative lane scope or consent fields")
        for list_field in ("required_inputs", "trigger_evidence_refs", "blocked_actions"):
            if list_field in item and not isinstance(item.get(list_field), list):
                _follow_up_issue(result, strict, f"follow_up_requests[{index}] {list_field} must be a list")


def _follow_up_issue(result: dict[str, Any], strict: bool, message: str) -> None:
    result["warnings"].append(message)
    if strict and "follow_up_requests" not in result["required_fields_missing"]:
        result["required_fields_missing"].append("follow_up_requests")


def _evaluate_improvements(frontmatter: dict[str, Any], result: dict[str, Any], *, strict: bool) -> None:
    if "improvements" not in frontmatter:
        return
    improvements = frontmatter.get("improvements")
    if improvements in (None, ""):
        return
    if not isinstance(improvements, list):
        _improvement_issue(result, strict, "improvements must be a list")
        return
    for index, item in enumerate(improvements, start=1):
        if not isinstance(item, dict):
            _improvement_issue(result, strict, f"improvements[{index}] must be an object")
            continue
        improvement_type = str(item.get("improvement_type") or "")
        missing = [field for field in ("improvement", "reason") if _is_blank(item.get(field))]
        if _is_blank(improvement_type):
            missing.append("improvement_type")
        if missing:
            _improvement_issue(result, strict, f"improvements[{index}] missing fields: {', '.join(missing)}")
        if improvement_type and improvement_type not in IMPROVEMENT_TYPES:
            _improvement_issue(result, strict, f"improvements[{index}] improvement_type must be one of {sorted(IMPROVEMENT_TYPES)}")
        materiality = str(item.get("materiality") or "medium")
        if materiality not in FOLLOW_UP_MATERIALITY:
            _improvement_issue(result, strict, f"improvements[{index}] materiality must be low, medium, or high")
        role = str(item.get("suggested_role") or "")
        if role and role not in FOLLOW_UP_ROLES and role != "head-manager":
            _improvement_issue(result, strict, f"improvements[{index}] suggested_role must be head-manager or a non-execution fixed role")
        for list_field in ("evidence_refs", "applies_to", "blocked_actions"):
            if list_field in item and not isinstance(item.get(list_field), list):
                _improvement_issue(result, strict, f"improvements[{index}] {list_field} must be a list")


def _improvement_issue(result: dict[str, Any], strict: bool, message: str) -> None:
    result["warnings"].append(message)
    if strict and "improvements" not in result["required_fields_missing"]:
        result["required_fields_missing"].append("improvements")


def _evaluate_decision_quality(frontmatter: dict[str, Any], result: dict[str, Any], *, strict: bool) -> None:
    role = str(frontmatter.get("role") or "").strip()
    artifact_type = str(frontmatter.get("artifact_type") or result.get("artifact_type") or "").strip()
    checks: list[str] = []
    thesis_lifecycle = frontmatter.get("thesis_lifecycle")
    if isinstance(thesis_lifecycle, dict) and thesis_lifecycle:
        checks.append("thesis_lifecycle")
        _evaluate_thesis_lifecycle(thesis_lifecycle, frontmatter, result, strict=strict)

    if _truthy(frontmatter.get("forecast_negated")) and _has_any(frontmatter, ("probability", "probability_range")):
        _decision_issue(result, strict, "probability_for_forecast_negated")

    forecast_required = _truthy(frontmatter.get("forecast_required"))
    if forecast_required:
        checks.append("forecast_contract")
        if _truthy(frontmatter.get("forecast_allowed")):
            _require_any(result, strict, frontmatter, ("probability", "probability_range"), "probability_or_range")
            _require_fields(result, strict, frontmatter, ("forecast_target", "forecast_horizon", "base_rate", "evidence_ids", "contrary_evidence", "resolution_source", "review_date", "update_triggers", "invalidation_conditions"))
        else:
            _require_fields(result, strict, frontmatter, ("forecast_allowed", "forecast_block_reason"))
            _require_any(result, strict, frontmatter, ("base_rate", "missing_base_rate_note"), "base_rate_or_missing_base_rate_note")
    _check_probability_range(frontmatter, result, strict)

    if role == "valuation-analyst" or "valuation" in artifact_type:
        checks.append("valuation_market_anchor")
        if not _has_valuation_market_anchor(frontmatter) and not _readiness_has_exact_token(
            frontmatter.get("readiness_label"),
            "not-decision-ready",
        ):
            _decision_issue(result, strict, "current_price_or_market_anchor")

    anti_overfit_required = _truthy(frontmatter.get("anti_overfit_required"))
    if anti_overfit_required:
        checks.append("anti_overfit")
        _evaluate_inline_anti_overfit_checks(
            frontmatter.get("anti_overfit_checks"),
            result,
            strict=strict,
        )

    if _truthy(frontmatter.get("investor_context_gate_required")):
        checks.append("investor_context_gaps")
        if not isinstance(frontmatter.get("investor_context_gaps"), list):
            _decision_issue(result, strict, "investor_context_gaps")

    accepted_thesis_or_decision = str(frontmatter.get("handoff_state") or "") == "accepted" and (
        artifact_type in {"thesis", "decision", "decision_package", "valuation"}
    )
    decision_quality_required = _truthy(frontmatter.get("decision_quality_required"))
    if decision_quality_required or accepted_thesis_or_decision:
        checks.append("agent_judgment_review")
        _require_fields(result, strict, frontmatter, JUDGMENT_REVIEW_FIELDS)
        if decision_quality_required or artifact_type in {"decision", "decision_package"}:
            _require_fields(result, strict, frontmatter, ("thesis_lifecycle",))

    if accepted_thesis_or_decision:
        checks.append("accepted_decision_fields")
        _require_fields(result, strict, frontmatter, ("forecast_allowed",))
        _require_any(result, strict, frontmatter, ("scenario_cases", "scenario_summary"), "scenario_cases")

    result["decision_quality"] = {
        "status": "fail" if strict and any(field.startswith("decision_quality.") for field in result["required_fields_missing"]) else "pass",
        "checks": checks,
    }


def _evaluate_inline_anti_overfit_checks(
    value: Any,
    result: dict[str, Any],
    *,
    strict: bool,
) -> None:
    if not isinstance(value, dict):
        _decision_issue(result, strict, "anti_overfit_checks")
        return
    unexpected_checks = sorted(set(value) - set(ANTI_OVERFIT_CHECK_KEYS))
    if unexpected_checks:
        _decision_issue(result, strict, "anti_overfit_checks.unexpected_fields")
    allowed_statuses = {"pass", "fail", "not_applicable"}
    for key in ANTI_OVERFIT_CHECK_KEYS:
        check = value.get(key)
        if not isinstance(check, dict):
            _decision_issue(result, strict, f"anti_overfit_checks.{key}")
            continue
        if set(check) - {"status", "reason", "evidence_refs"}:
            _decision_issue(result, strict, f"anti_overfit_checks.{key}.unexpected_fields")
        status = str(check.get("status") or "")
        if status not in allowed_statuses:
            _decision_issue(result, strict, f"anti_overfit_checks.{key}.status")
        if not isinstance(check.get("reason"), str) or _is_blank(check.get("reason")):
            _decision_issue(result, strict, f"anti_overfit_checks.{key}.reason")
        evidence_refs = check.get("evidence_refs")
        if (
            not isinstance(evidence_refs, list)
            or any(not isinstance(item, str) or not item.strip() for item in evidence_refs)
            or (status in {"pass", "fail"} and not evidence_refs)
        ):
            _decision_issue(result, strict, f"anti_overfit_checks.{key}.evidence_refs")


def _is_run_card_payload(payload: dict[str, Any], result: dict[str, Any]) -> bool:
    return result.get("artifact_type") == "evidence_run_card" or str(payload.get("artifact_type") or "") == "evidence_run_card"


def _evaluate_run_card(card: dict[str, Any], result: dict[str, Any], *, strict: bool) -> None:
    result["artifact_type"] = "evidence_run_card"
    result["run_card"] = {key: card.get(key) for key in RUN_CARD_REQUIRED_KEYS + ("metrics", "validation_summary", "created_by") if key in card}
    for key in RUN_CARD_REQUIRED_KEYS:
        if key not in card:
            _run_card_issue(result, strict, key)
    for field in RUN_CARD_NONBLANK_FIELDS:
        if field in card and _is_blank(card.get(field)):
            _run_card_issue(result, strict, field)
    if str(card.get("artifact_type") or "") != "evidence_run_card":
        _run_card_issue(result, strict, "artifact_type")
    if str(card.get("authority") or "") != "evidence_only":
        _run_card_issue(result, strict, "authority")
    generated_at = card.get("generated_at")
    if generated_at and not _iso_datetime(generated_at):
        _run_card_issue(result, strict, "generated_at")
    for field in RUN_CARD_LIST_FIELDS:
        if field in card and not isinstance(card.get(field), list):
            _run_card_issue(result, strict, field)
    for field in RUN_CARD_DICT_FIELDS:
        if field in card and not isinstance(card.get(field), dict):
            _run_card_issue(result, strict, field)
    if not isinstance(card.get("artifact_hashes"), dict) or not card.get("artifact_hashes"):
        _run_card_issue(result, strict, "artifact_hashes")
    metrics = card.get("metrics")
    has_metrics = isinstance(metrics, dict) and bool(metrics)
    if not has_metrics and _is_blank(card.get("validation_summary")):
        _run_card_issue(result, strict, "metrics_or_validation_summary")
    result["decision_quality"] = {"status": "pass", "checks": ["evidence_run_card_shape"]}


def _run_card_issue(result: dict[str, Any], strict: bool, field: str) -> None:
    message = f"run card missing or invalid {field}"
    result["warnings"].append(message)
    if strict:
        result["required_fields_missing"].append(f"run_card.{field}")


def _iso_datetime(value: Any) -> bool:
    try:
        datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def _is_validation_card_payload(payload: dict[str, Any], result: dict[str, Any]) -> bool:
    return result.get("artifact_type") == "validation_card" or str(payload.get("artifact_type") or "") == "validation_card"


def _evaluate_validation_card(card: dict[str, Any], result: dict[str, Any], *, strict: bool) -> None:
    result["artifact_type"] = "validation_card"
    result["validation_card"] = {key: card.get(key) for key in VALIDATION_CARD_REQUIRED_KEYS + ("metrics", "validation_summary", "created_by") if key in card}
    for key in VALIDATION_CARD_REQUIRED_KEYS:
        if key not in card:
            _validation_card_issue(result, strict, key)
    for field in VALIDATION_CARD_NONBLANK_FIELDS:
        if field in card and _is_blank(card.get(field)):
            _validation_card_issue(result, strict, field)
    if str(card.get("artifact_type") or "") != "validation_card":
        _validation_card_issue(result, strict, "artifact_type")
    if str(card.get("authority") or "") != "evidence_only":
        _validation_card_issue(result, strict, "authority")
    label = str(card.get("evidence_quality_label") or "")
    if label and label not in EVIDENCE_QUALITY_LABELS:
        _validation_card_issue(result, strict, "evidence_quality_label")
    generated_at = card.get("generated_at")
    if generated_at and not _iso_datetime(generated_at):
        _validation_card_issue(result, strict, "generated_at")
    for field in VALIDATION_CARD_LIST_FIELDS:
        if field in card and not isinstance(card.get(field), list):
            _validation_card_issue(result, strict, field)
    for field in VALIDATION_CARD_DICT_FIELDS:
        if field in card and not isinstance(card.get(field), dict):
            _validation_card_issue(result, strict, field)
    if not isinstance(card.get("artifact_hashes"), dict) or not card.get("artifact_hashes"):
        _validation_card_issue(result, strict, "artifact_hashes")
    checks = card.get("checks") if isinstance(card.get("checks"), dict) else {}
    missing_checks = [key for key in ANTI_OVERFIT_CHECK_KEYS if _is_blank(checks.get(key))]
    for key in missing_checks:
        _validation_card_issue(result, strict, f"checks.{key}")
    assessed = False
    for key, value in checks.items():
        if not isinstance(value, dict):
            _validation_card_issue(result, strict, f"checks.{key}.typed_result")
            continue
        status = str(value.get("status") or "")
        if status not in VALIDATION_CHECK_STATUSES:
            _validation_card_issue(result, strict, f"checks.{key}.status")
            continue
        assessed = assessed or status != "not_assessed"
        if status != "not_assessed" and _is_blank(value.get("reason")):
            _validation_card_issue(result, strict, f"checks.{key}.reason")
        evidence_refs = value.get("evidence_refs")
        if status in {"pass", "fail"} and (not isinstance(evidence_refs, list) or not evidence_refs):
            _validation_card_issue(result, strict, f"checks.{key}.evidence_refs")
        if status == "not_applicable" and not isinstance(evidence_refs, list):
            _validation_card_issue(result, strict, f"checks.{key}.evidence_refs")
        if label == "validated" and status not in {"pass", "not_applicable"}:
            _validation_card_issue(result, strict, f"validated_checks.{key}")
    if _is_blank(card.get("validation_summary")) and not assessed:
        _validation_card_issue(result, strict, "validation_summary")
    has_issues = any(str(item).startswith("validation_card.") for item in result["required_fields_missing"])
    result["decision_quality"] = {
        "status": "fail" if strict and has_issues else "pass",
        "checks": ["validation_card_shape", "anti_overfit_evidence_metadata"],
    }


def _validation_card_issue(result: dict[str, Any], strict: bool, field: str) -> None:
    message = f"validation card missing or invalid {field}"
    result["warnings"].append(message)
    if strict:
        result["required_fields_missing"].append(f"validation_card.{field}")


def _evaluate_source_snapshot(snapshot: dict[str, Any], result: dict[str, Any], *, strict: bool) -> None:
    result["artifact_type"] = "source_snapshot"
    result["source_snapshot"] = {
        key: snapshot.get(key)
        for key in (
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
            "payload_hash",
            "snapshot_hash",
            "artifact_id",
            "warnings",
            "recorded_at",
            "snapshot_id",
        )
        if key in snapshot
    }
    for field in (
        "provider",
        "source_category",
        "retrieved_at",
        "known_at",
        "recorded_at",
        "timezone",
        "schema_hash",
        "payload_hash",
        "snapshot_hash",
    ):
        if _is_blank(snapshot.get(field)):
            _source_snapshot_warning(result, strict, field, "source snapshot missing required metadata")
    if _is_blank(snapshot.get("as_of")):
        _source_snapshot_warning(result, strict, "as_of", "timezone or as-of ambiguity")
    parsed_times: dict[str, datetime] = {}
    for field in ("retrieved_at", "known_at", "recorded_at"):
        value = snapshot.get(field)
        if value not in (None, ""):
            parsed = _aware_iso_datetime(value)
            if parsed is None:
                _source_snapshot_warning(result, strict, field, f"source snapshot {field} must be a timezone-aware ISO-8601 datetime")
            else:
                parsed_times[field] = parsed
    if parsed_times.get("known_at") and parsed_times.get("recorded_at") and parsed_times["known_at"] > parsed_times["recorded_at"]:
        _source_snapshot_warning(result, strict, "known_at", "source snapshot known_at must not be after recorded_at")
    warnings = snapshot.get("warnings")
    if warnings in (None, ""):
        warnings = []
    if not isinstance(warnings, list):
        _source_snapshot_warning(result, strict, "warnings", "source snapshot warnings should be a list")
    else:
        result["warnings"].extend(f"source snapshot warning: {warning}" for warning in warnings if warning)
    payload = snapshot.get("payload")
    if payload not in (None, "") and not isinstance(payload, dict):
        _source_snapshot_warning(result, strict, "payload", "source snapshot payload should be an object")
        return
    if isinstance(payload, dict):
        _evaluate_market_data_payload(payload, result)


def _source_snapshot_warning(result: dict[str, Any], strict: bool, field: str, message: str) -> None:
    result["warnings"].append(message)
    if strict:
        result["required_fields_missing"].append(f"source_snapshot.{field}")


def _evaluate_market_data_payload(payload: dict[str, Any], result: dict[str, Any]) -> None:
    bars = _market_bars(payload)
    if not bars:
        return
    timestamps: list[str] = []
    missing_bar_fields = False
    non_positive = False
    ohlc_failure = False
    timezone_ambiguous = False
    for index, bar in enumerate(bars, start=1):
        if not isinstance(bar, dict):
            missing_bar_fields = True
            continue
        timestamp = str(bar.get("timestamp") or bar.get("time") or bar.get("date") or "")
        if timestamp:
            timestamps.append(timestamp)
            if "T" in timestamp and not re.search(r"(Z|[+-]\d{2}:?\d{2})$", timestamp):
                timezone_ambiguous = True
        values = {name: _float_or_none(bar.get(name)) for name in ("open", "high", "low", "close")}
        if any(value is None for value in values.values()):
            missing_bar_fields = True
            continue
        if any(value <= 0 for value in values.values() if value is not None):
            non_positive = True
        high = values["high"]
        low = values["low"]
        if high is not None and low is not None and (high < max(values["open"], values["close"], low) or low > min(values["open"], values["close"], high)):
            ohlc_failure = True
    if len(bars) <= 1 or missing_bar_fields:
        result["warnings"].append("sparse or missing bars")
    if len(timestamps) != len(set(timestamps)):
        result["warnings"].append("duplicate timestamps")
    if non_positive:
        result["warnings"].append("non-positive prices")
    if ohlc_failure:
        result["warnings"].append("OHLC invariant failures")
    if timezone_ambiguous or not timestamps:
        result["warnings"].append("timezone or as-of ambiguity")
    if not _has_any(payload, ("adjusted", "adjustment", "price_adjustment", "adjusted_prices")):
        result["warnings"].append("adjusted versus unadjusted price ambiguity")
    if _truthy(payload.get("fallback_used")) and _is_blank(payload.get("fallback_policy")):
        result["warnings"].append("explicit source fallback policy missing")


def _market_bars(payload: dict[str, Any]) -> list[Any]:
    for key in ("bars", "ohlc", "prices"):
        value = payload.get(key)
        if isinstance(value, list):
            return value
    return []


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _loads_json_strict(text: str) -> Any:
    return json.loads(text, parse_constant=_reject_json_constant)


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"invalid JSON constant: {value}")


def _forecast_record_errors(record: dict[str, Any], line: int) -> list[str]:
    errors: list[str] = []
    required = (
        "schema_version",
        "event_id",
        "event_type",
        "forecast_id",
        "artifact_id",
        "role",
        "forecast_target",
        "target_type",
        "horizon",
        "issued_at",
        "knowledge_cutoff",
        "evidence_ids",
        "contrary_evidence",
        "invalidation_conditions",
        "update_triggers",
        "resolution_rule",
        "resolution_source",
        "review_date",
        "status",
    )
    missing = [field for field in required if _is_blank(record.get(field))]
    if missing:
        errors.append(f"line {line} missing forecast fields: {', '.join(missing)}")
    event_type = str(record.get("event_type") or "")
    if event_type not in FORECAST_EVENT_TYPES:
        errors.append(f"line {line} event_type must be one of {sorted(FORECAST_EVENT_TYPES)}")
    target_type = str(record.get("target_type") or "")
    if target_type not in FORECAST_TARGET_TYPES:
        errors.append(f"line {line} target_type must be one of {sorted(FORECAST_TARGET_TYPES)}")
    if str(record.get("status") or "") not in {"open", "closed"}:
        errors.append(f"line {line} status must be open or closed")
    if target_type == "binary":
        if not _has_any(record, ("probability", "probability_range")):
            errors.append(f"line {line} missing probability or probability_range")
        if _is_blank(record.get("base_rate")):
            errors.append(f"line {line} missing base_rate")
        range_error = _probability_range_error(record)
        if range_error:
            errors.append(f"line {line} {range_error}")
        base_rate_error = _probability_value_error(record.get("base_rate"), "base_rate")
        if base_rate_error:
            errors.append(f"line {line} {base_rate_error}")
    elif target_type == "categorical":
        probabilities = record.get("probabilities")
        if not isinstance(probabilities, dict) or len(probabilities) < 2:
            errors.append(f"line {line} categorical forecasts require at least two probabilities")
        else:
            values: list[float] = []
            for key, value in probabilities.items():
                value_error = _probability_value_error(value, f"probabilities.{key}")
                if value_error:
                    errors.append(f"line {line} {value_error}")
                else:
                    values.append(float(value))
            if values and abs(sum(values) - 1.0) > 1e-9:
                errors.append(f"line {line} categorical probabilities must sum to 1")
    elif target_type == "continuous":
        if not _has_any(record, ("prediction", "interval", "quantiles")):
            errors.append(f"line {line} continuous forecasts require prediction, interval, or quantiles")
        interval = record.get("interval")
        if interval not in (None, ""):
            if not isinstance(interval, dict) or _float_or_none(interval.get("lower")) is None or _float_or_none(interval.get("upper")) is None:
                errors.append(f"line {line} interval must contain numeric lower and upper bounds")
            elif float(interval["lower"]) > float(interval["upper"]):
                errors.append(f"line {line} interval lower bound must not exceed upper bound")
    if str(record.get("status") or "") == "closed":
        for field in ("outcome", "resolver", "resolution_source_snapshot_id", "resolved_at"):
            if _is_blank(record.get(field)):
                errors.append(f"line {line} closed forecast missing {field}")
    if event_type == "revised":
        for field in ("prior_event_id", "revision_reason", "revised_at"):
            if _is_blank(record.get(field)):
                errors.append(f"line {line} revised forecast missing {field}")
    if event_type == "dispute_resolved" and _is_blank(record.get("resolution_supersedes_event_id")):
        errors.append(f"line {line} dispute-resolved forecast missing resolution_supersedes_event_id")
    if event_type == "scored" and (not isinstance(record.get("scores"), dict) or not record.get("scores")):
        errors.append(f"line {line} scored forecast missing scores")
    for field in ("evidence_ids", "contrary_evidence", "invalidation_conditions", "update_triggers"):
        if field in record and not isinstance(record.get(field), list):
            errors.append(f"line {line} {field} must be a list")
    return errors


def _require_fields(result: dict[str, Any], strict: bool, fields: dict[str, Any], names: tuple[str, ...]) -> None:
    for name in names:
        if _is_blank(fields.get(name)):
            _decision_issue(result, strict, name)


def _require_any(result: dict[str, Any], strict: bool, fields: dict[str, Any], names: tuple[str, ...], label: str) -> None:
    if not _has_any(fields, names):
        _decision_issue(result, strict, label)


def _decision_issue(result: dict[str, Any], strict: bool, field: str, message: str | None = None) -> None:
    result["warnings"].append(message or f"decision quality missing {field}")
    if strict:
        result["required_fields_missing"].append(f"decision_quality.{field}")


def _evaluate_thesis_lifecycle(lifecycle: dict[str, Any], frontmatter: dict[str, Any], result: dict[str, Any], *, strict: bool) -> None:
    state = str(lifecycle.get("state") or "").strip()
    if state not in THESIS_LIFECYCLE_STATES:
        _decision_issue(result, strict, "thesis_lifecycle.state")
        return
    source_refs = _coerce_frontmatter_list(frontmatter.get("source_snapshot_ids")) + _coerce_frontmatter_list(frontmatter.get("evidence_ids")) + _coerce_frontmatter_list(lifecycle.get("evidence_refs"))
    if state == "testing" and not source_refs:
        _decision_issue(
            result,
            strict,
            "thesis_lifecycle.testing_evidence",
            "thesis_lifecycle.state=testing requires evidence_refs, source_snapshot_ids, or evidence_ids",
        )
    if state == "validated":
        if _is_blank(lifecycle.get("evidence_run_card")) and not _coerce_frontmatter_list(lifecycle.get("evidence_run_cards")):
            _decision_issue(result, strict, "thesis_lifecycle.evidence_run_card")
        if (
            _is_blank(lifecycle.get("validation_card"))
            and not _coerce_frontmatter_list(lifecycle.get("validation_cards"))
            and not _coerce_frontmatter_list(lifecycle.get("validation_artifacts"))
        ):
            _decision_issue(
                result,
                strict,
                "thesis_lifecycle.validation_card",
                "thesis_lifecycle.state=validated requires validation_card or validation_cards",
            )
        if _is_blank(lifecycle.get("reviewer_acceptance")):
            _decision_issue(result, strict, "thesis_lifecycle.reviewer_acceptance")
    if state == "rejected" and _is_blank(lifecycle.get("invalidation_note")):
        _decision_issue(result, strict, "thesis_lifecycle.invalidation_note")
    if state == "monitoring" and _is_blank(lifecycle.get("monitoring_artifact")) and _is_blank(lifecycle.get("review_cadence")):
        _decision_issue(
            result,
            strict,
            "thesis_lifecycle.monitoring_artifact_or_cadence",
            "thesis_lifecycle.state=monitoring requires either monitoring_artifact or review_cadence",
        )


def _coerce_frontmatter_list(value: Any) -> list[Any]:
    if value in (None, ""):
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _has_any(fields: dict[str, Any], names: tuple[str, ...]) -> bool:
    return any(not _is_blank(fields.get(name)) for name in names)


def _has_valuation_market_anchor(frontmatter: dict[str, Any]) -> bool:
    return any(
        not _is_blank(value) and not _is_absence_placeholder(value)
        for value in (
            frontmatter.get("current_price_as_of"),
            frontmatter.get("market_anchor_as_of"),
        )
    )


def _is_absence_placeholder(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    return ABSENCE_PLACEHOLDER_PATTERN.match(value.strip()) is not None


def _readiness_has_exact_token(value: Any, token: str) -> bool:
    if not isinstance(value, str):
        return False
    return token in READINESS_TOKEN_PATTERN.findall(value)


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "required", "allowed"}


def _check_probability_range(frontmatter: dict[str, Any], result: dict[str, Any], strict: bool) -> None:
    error = _probability_range_error(frontmatter)
    if error:
        result["warnings"].append(error)
        if strict:
            result["required_fields_missing"].append("decision_quality.valid_probability_range")


def _probability_range_error(fields: dict[str, Any]) -> str:
    probability: float | None = None
    if not _is_blank(fields.get("probability")):
        probability_error = _probability_value_error(fields.get("probability"), "probability", allow_percent=True)
        if probability_error:
            return probability_error
        probability = _normalized_probability(fields.get("probability"))
    if not _is_blank(fields.get("probability_range")):
        value = fields.get("probability_range")
        parsed = _parse_probability_range(value)
        if not parsed:
            return "probability_range must contain two bounds such as 30-40%, 0.30-0.40, or [0.30, 0.40]"
        low, high = parsed
        if not 0 <= low <= high <= 1:
            return "probability_range bounds must be between 0 and 1"
        if probability is not None and not low <= probability <= high:
            return "probability must fall inside probability_range"
    return ""


def _parse_probability_range(value: Any) -> tuple[float, float] | None:
    if isinstance(value, (list, tuple)) and len(value) == 2:
        try:
            low = float(value[0])
            high = float(value[1])
        except (TypeError, ValueError):
            return None
        return (low, high) if low <= high else (high, low)
    text = str(value or "")
    match = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*%?\s*[-–]\s*([0-9]+(?:\.[0-9]+)?)\s*%?", text)
    if not match:
        return None
    low = float(match.group(1))
    high = float(match.group(2))
    if "%" in text or low > 1 or high > 1:
        low /= 100
        high /= 100
    if low > high:
        low, high = high, low
    return low, high


def _normalized_probability(value: Any) -> float:
    text = str(value).strip()
    number = float(text.rstrip("%"))
    if text.endswith("%") or number > 1:
        number /= 100
    return number


def _probability_value_error(value: Any, field: str, *, allow_percent: bool = False) -> str:
    if _is_blank(value):
        return ""
    if isinstance(value, dict):
        value = value.get("value")
    try:
        number = _normalized_probability(value) if allow_percent else float(value)
    except (TypeError, ValueError):
        return f"{field} must be numeric"
    if not 0 <= number <= 1:
        return f"{field} must be between 0 and 1"
    return ""


def _aware_iso_datetime(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _is_blank(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple, dict, set)):
        return not value
    return False


def _confidence_looks_valid(value: Any) -> bool:
    if isinstance(value, (int, float)):
        return 0 <= float(value) <= 100
    text = str(value).strip().lower()
    if text in {"low", "medium", "high", "low-medium", "medium-high"}:
        return True
    try:
        number = float(text.rstrip("%"))
    except ValueError:
        return False
    return 0 <= number <= 100


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, (len(text) + 3) // 4)


def classify_artifact_path(rel: str) -> str:
    if rel.endswith(".run-card.json") or rel.endswith(".run-card.md"):
        return "evidence_run_card"
    if rel.endswith(".validation-card.json") or rel.endswith(".validation-card.md"):
        return "validation_card"
    if rel.startswith("trading/research/source-snapshots/"):
        return "source_snapshot"
    if rel.startswith("trading/forecasts/"):
        return "forecast_ledger"
    if rel.startswith("trading/decisions/"):
        return "decision_package"
    if rel.startswith("trading/research/"):
        return "evidence_pack"
    if "order_ticket" in rel:
        return "order_ticket"
    if "approval_receipt" in rel:
        return "approval_receipt"
    if rel.startswith("trading/reports/"):
        return "report"
    return "artifact"

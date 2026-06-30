from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from tradingcodex_service.application.common import safe_workspace_path
from tradingcodex_service.application.markdown_preview import split_markdown_frontmatter
from tradingcodex_service.application.research import RESEARCH_FILE_ROOTS

FORECAST_FILE_ROOTS = (Path("trading/forecasts"),)
QUALITY_FILE_ROOTS = RESEARCH_FILE_ROOTS + (
    *FORECAST_FILE_ROOTS,
    Path("trading/orders"),
    Path("trading/approvals"),
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
    "profile_gap",
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
}
FOLLOW_UP_FORBIDDEN_PATTERN = re.compile(
    r"\b(secret|api[_ -]?key|raw broker|broker api|approval|approve|execution|execute|submit|policy mutation|policy write|self-approve)\b",
    re.I,
)
CLAIM_TAG_PATTERN = re.compile(r"\[(factual|inference|assumption)\]", re.IGNORECASE)
STRICT_MARKDOWN_REQUIRED_FIELDS = (
    "artifact_id",
    "artifact_type",
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


def evaluate_artifact_quality(workspace_root: Path | str, artifact_path: str, *, strict: bool = False) -> dict[str, Any]:
    root = Path(workspace_root)
    result: dict[str, Any] = {
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

    text = path.read_text(encoding="utf-8")
    result["exists"] = True
    result["bytes"] = len(text.encode("utf-8"))
    result["non_empty"] = bool(text.strip())
    result["context_efficiency"]["estimated_tokens"] = estimate_tokens(text)

    if rel.endswith(".jsonl"):
        _evaluate_jsonl(text, result, strict=strict)
    elif rel.endswith(".json"):
        _evaluate_json(text, result)
    elif rel.endswith(".md"):
        _evaluate_markdown(text, result, strict=strict)

    blocking_missing = bool(result["required_fields_missing"]) if strict else False
    result["status"] = "fail" if not result["non_empty"] or result["json_valid"] is False or blocking_missing else "pass"
    return result


def evaluate_decision_quality(
    workspace_root: Path | str,
    artifact_path: str,
    workflow_lane: str = "",
    *,
    strict: bool = True,
) -> dict[str, Any]:
    result = evaluate_artifact_quality(workspace_root, artifact_path, strict=strict)
    if workflow_lane and result.get("exists") and str(result.get("path") or "").endswith(".md"):
        path = safe_workspace_path(workspace_root, str(result["path"]), allowed_roots=QUALITY_FILE_ROOTS)
        document = split_markdown_frontmatter(path.read_text(encoding="utf-8"))
        frontmatter = {**document.frontmatter, "workflow_lane": workflow_lane}
        _evaluate_decision_quality(frontmatter, document.body, result, strict=strict)
        if strict and result["required_fields_missing"]:
            result["status"] = "fail"
    return result


def _evaluate_json(text: str, result: dict[str, Any]) -> None:
    try:
        json.loads(text)
        result["json_valid"] = True
    except Exception:
        result["json_valid"] = False


def _evaluate_jsonl(text: str, result: dict[str, Any], *, strict: bool) -> None:
    records = []
    errors = []
    for index, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
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
            "workflow_lane",
            "decision_quality_required",
            "forecast_required",
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
            "current_price_as_of",
            "market_anchor_as_of",
            "investor_profile_gaps",
            "follow_up_requests",
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

    if handoff_state in {"revise", "blocked"}:
        has_missing = bool(frontmatter.get("missing_evidence"))
        has_blocked = bool(frontmatter.get("blocked_actions"))
        if not has_missing and not has_blocked:
            message = f"{handoff_state} handoffs should name missing evidence or blocked actions"
            result["warnings"].append(message)
            if strict:
                result["required_fields_missing"].append("missing_evidence_or_blocked_actions")

    _evaluate_decision_quality(frontmatter, body, result, strict=strict)


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
        request_text = " ".join(str(item.get(field) or "") for field in ("question", "reason", "suggested_role"))
        if FOLLOW_UP_FORBIDDEN_PATTERN.search(request_text):
            _follow_up_issue(result, strict, f"follow_up_requests[{index}] must not request approval, execution, raw broker access, secrets, or policy mutation")


def _follow_up_issue(result: dict[str, Any], strict: bool, message: str) -> None:
    result["warnings"].append(message)
    if strict and "follow_up_requests" not in result["required_fields_missing"]:
        result["required_fields_missing"].append("follow_up_requests")


def _evaluate_decision_quality(frontmatter: dict[str, Any], body: str, result: dict[str, Any], *, strict: bool) -> None:
    lane = str(frontmatter.get("workflow_lane") or frontmatter.get("workflow_type") or "").strip()
    role = str(frontmatter.get("role") or "").strip()
    artifact_type = str(frontmatter.get("artifact_type") or result.get("artifact_type") or "").strip()
    checks: list[str] = []

    if _truthy(frontmatter.get("forecast_negated")) and _has_any(frontmatter, ("probability", "probability_range")):
        _decision_issue(result, strict, "probability_for_forecast_negated")

    forecast_required = (
        _truthy(frontmatter.get("forecast_required"))
        or _truthy(frontmatter.get("forecast_contract_required"))
        or lane == "thesis_review_then_portfolio_risk_review"
    )
    if forecast_required:
        checks.append("forecast_contract")
        if _truthy(frontmatter.get("forecast_allowed")):
            _require_any(result, strict, frontmatter, ("probability", "probability_range"), "probability_or_range")
            _require_fields(result, strict, frontmatter, ("forecast_target", "forecast_horizon", "base_rate", "evidence_ids", "contrary_evidence", "resolution_source", "review_date", "update_triggers"))
        else:
            _require_fields(result, strict, frontmatter, ("forecast_allowed", "forecast_block_reason"))
            _require_any(result, strict, frontmatter, ("base_rate", "missing_base_rate_note"), "base_rate_or_missing_base_rate_note")
    _check_probability_range(frontmatter, result, strict)

    if role == "valuation-analyst" or "valuation" in artifact_type:
        checks.append("valuation_market_anchor")
        if not _has_any(frontmatter, ("current_price_as_of", "market_anchor_as_of")) and str(frontmatter.get("readiness_label") or "") != "not-decision-ready":
            _decision_issue(result, strict, "current_price_or_market_anchor")

    anti_overfit_required = _truthy(frontmatter.get("anti_overfit_required")) or bool(
        re.search(r"\b(backtest|signal|model performance)\b", body, re.I)
    )
    if anti_overfit_required:
        checks.append("anti_overfit")
        if not re.search(r"\b(anti[- ]overfit|look[- ]ahead|survivorship|out[- ]of[- ]sample|walk[- ]forward|data snooping)\b", body, re.I):
            _decision_issue(result, strict, "anti_overfit_validation")

    if role in {"portfolio-manager", "risk-manager"} and (
        lane in {"thesis_review_then_portfolio_risk_review", "portfolio_risk_review"}
        or _truthy(frontmatter.get("profile_gate_required"))
    ):
        checks.append("investor_profile_gaps")
        if not _has_any(frontmatter, ("investor_profile_gaps", "missing_evidence")):
            _decision_issue(result, strict, "investor_profile_gaps")

    accepted_thesis_or_decision = str(frontmatter.get("handoff_state") or "") == "accepted" and (
        lane in {"thesis_review", "thesis_review_then_portfolio_risk_review"}
        or artifact_type in {"thesis", "decision", "valuation"}
    )
    if accepted_thesis_or_decision:
        checks.append("accepted_decision_fields")
        _require_fields(
            result,
            strict,
            frontmatter,
            ("contrary_evidence", "update_triggers", "invalidation_conditions", "forecast_allowed"),
        )
        _require_any(result, strict, frontmatter, ("scenario_cases", "scenario_summary"), "scenario_cases")

    result["decision_quality"] = {
        "status": "fail" if strict and any(field.startswith("decision_quality.") for field in result["required_fields_missing"]) else "pass",
        "checks": checks,
    }


def _forecast_record_errors(record: dict[str, Any], line: int) -> list[str]:
    errors: list[str] = []
    required = (
        "forecast_id",
        "artifact_id",
        "role",
        "forecast_target",
        "horizon",
        "evidence_ids",
        "contrary_evidence",
        "resolution_source",
        "review_date",
        "status",
    )
    missing = [field for field in required if _is_blank(record.get(field))]
    if missing:
        errors.append(f"line {line} missing forecast fields: {', '.join(missing)}")
    if not _has_any(record, ("probability", "probability_range")):
        errors.append(f"line {line} missing probability or probability_range")
    if str(record.get("status") or "") not in {"open", "closed"}:
        errors.append(f"line {line} status must be open or closed")
    probability = record.get("probability")
    if probability not in (None, ""):
        try:
            number = float(probability)
        except (TypeError, ValueError):
            errors.append(f"line {line} probability must be numeric")
        else:
            if not 0 <= number <= 1:
                errors.append(f"line {line} probability must be between 0 and 1")
    range_error = _probability_range_error(record)
    if range_error:
        errors.append(f"line {line} {range_error}")
    for field in ("evidence_ids", "contrary_evidence"):
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


def _decision_issue(result: dict[str, Any], strict: bool, field: str) -> None:
    result["warnings"].append(f"decision quality missing {field}")
    if strict:
        result["required_fields_missing"].append(f"decision_quality.{field}")


def _has_any(fields: dict[str, Any], names: tuple[str, ...]) -> bool:
    return any(not _is_blank(fields.get(name)) for name in names)


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
    if _is_blank(fields.get("probability")) or _is_blank(fields.get("probability_range")):
        return ""
    try:
        probability = float(str(fields.get("probability")).rstrip("%"))
    except ValueError:
        return "probability must be numeric"
    if probability > 1:
        probability = probability / 100
    parsed = _parse_probability_range(str(fields.get("probability_range") or ""))
    if not parsed:
        return "probability_range must look like 30-40% or 0.30-0.40"
    low, high = parsed
    if not low <= probability <= high:
        return "probability must fall inside probability_range"
    return ""


def _parse_probability_range(text: str) -> tuple[float, float] | None:
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
    if rel.startswith("trading/forecasts/"):
        return "forecast_ledger"
    if rel.startswith("trading/research/"):
        return "evidence_pack"
    if "order_ticket" in rel:
        return "order_ticket"
    if "approval_receipt" in rel:
        return "approval_receipt"
    if rel.startswith("trading/reports/"):
        return "report"
    return "artifact"

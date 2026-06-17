from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from tradingcodex_service.application.common import safe_workspace_path
from tradingcodex_service.application.markdown_preview import split_markdown_frontmatter
from tradingcodex_service.application.research import RESEARCH_FILE_ROOTS

QUALITY_FILE_ROOTS = RESEARCH_FILE_ROOTS + (
    Path("trading/orders"),
    Path("trading/approvals"),
    Path("trading/audit"),
)

HANDOFF_STATES = {"accepted", "revise", "blocked", "waiting"}
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

    if rel.endswith(".json"):
        _evaluate_json(text, result)
    elif rel.endswith(".md"):
        _evaluate_markdown(text, result, strict=strict)

    blocking_missing = bool(result["required_fields_missing"]) if strict else False
    result["status"] = "fail" if not result["non_empty"] or result["json_valid"] is False or blocking_missing else "pass"
    return result


def _evaluate_json(text: str, result: dict[str, Any]) -> None:
    try:
        json.loads(text)
        result["json_valid"] = True
    except Exception:
        result["json_valid"] = False


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
            "handoff_state",
            "confidence",
            "next_recipient",
            "missing_evidence",
            "blocked_actions",
            "source_snapshot_ids",
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
    if strict:
        result["required_fields_missing"].extend(missing_fields + missing_keys)
        if not tags:
            result["required_fields_missing"].append("claim_tags")
    else:
        result["warnings"].extend(f"missing {field}" for field in missing_fields + missing_keys)
        if not tags:
            result["warnings"].append("missing claim tags")

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

    for field in ("missing_evidence", "blocked_actions", "source_snapshot_ids"):
        if field in frontmatter and not isinstance(frontmatter.get(field), list):
            result["warnings"].append(f"{field} should be a list")

    if handoff_state in {"revise", "blocked"}:
        has_missing = bool(frontmatter.get("missing_evidence"))
        has_blocked = bool(frontmatter.get("blocked_actions"))
        if not has_missing and not has_blocked:
            result["warnings"].append(f"{handoff_state} handoffs should name missing evidence or blocked actions")


def _is_blank(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
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
    if rel.startswith("trading/research/"):
        return "evidence_pack"
    if "order_ticket" in rel:
        return "order_ticket"
    if "approval_receipt" in rel:
        return "approval_receipt"
    if rel.startswith("trading/reports/"):
        return "report"
    return "artifact"

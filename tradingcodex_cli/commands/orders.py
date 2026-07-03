from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

from tradingcodex_service.application.artifact_quality import evaluate_artifact_quality
from tradingcodex_service.application.audit import write_audit_event
from tradingcodex_service.application.common import sanitize_id, write_json
from tradingcodex_service.application.orders import request_order_approval, run_order_checks
from tradingcodex_cli.commands.utils import _option_value, classify_artifact_path, print_json

def validate(root: Path, argv: list[str]) -> None:
    if len(argv) < 2 or argv[0] != "order":
        raise ValueError("Usage: tcx validate order <ticket-id>")
    result = run_order_checks(root, {"principal_id": "portfolio-manager", "ticket_id": argv[1]})
    write_audit_event(root, {"type": "order_ticket.validated" if result["approval_ready"] else "order_ticket.validation_failed", "payload": result}, "portfolio-manager", "cli")
    print_json(result)
    if not result.get("approval_ready"):
        sys.exit(1)


def risk_check(root: Path, argv: list[str]) -> None:
    ticket_id = argv[0] if argv else _option_value(argv, "--ticket-id")
    if not ticket_id:
        raise ValueError("Usage: tcx risk-check <ticket-id>")
    checks = run_order_checks(root, {"principal_id": "risk-manager", "ticket_id": ticket_id})
    reasons = [reason for check in checks["checks"] if check["decision"] == "fail" for reason in check["reasons"]]
    result = {"decision": "go" if checks["approval_ready"] else "revise", "order_ticket_id": ticket_id, "reasons": reasons, "checks": checks["checks"]}
    write_audit_event(root, {"type": "risk_check", "payload": result}, "risk-manager", "cli")
    print_json(result)
    if result["decision"] != "go":
        sys.exit(1)


def approve(root: Path, argv: list[str]) -> None:
    ticket_id = argv[0] if argv else _option_value(argv, "--ticket-id")
    if not ticket_id:
        raise ValueError("Usage: tcx approve <ticket-id> [--approved-by risk-manager]")
    approved_by = _option_value(argv, "--approved-by") or "risk-manager"
    result = request_order_approval(root, {"principal_id": approved_by, "approved_by": approved_by, "ticket_id": ticket_id, "expires_hours": int(_option_value(argv, "--expires-hours") or 24)})
    print_json(result)
    if result.get("status") == "rejected":
        sys.exit(1)


def quality_check(root: Path, argv: list[str]) -> None:
    if not argv or argv[0] in {"--help", "-h", "help"}:
        print("Usage: tcx quality-check <artifact-path> [--strict]")
        print("Canonical paths: trading/research/*.evidence.md; trading/reports/<role>/*; trading/forecasts/*.jsonl; *.run-card.json; *.validation-card.json")
        return
    strict = "--strict" in argv
    path_arg = next((arg for arg in argv if not arg.startswith("--")), "")
    result = evaluate_artifact_quality(root, path_arg, strict=strict)
    if "artifact_type" not in result:
        result["artifact_type"] = classify_artifact_path(path_arg)
    print_json(result)
    if result["status"] != "pass":
        sys.exit(1)


def audit(root: Path, argv: list[str]) -> None:
    tail = int(_option_value(argv, "--tail") or 20)
    entries = []
    for path in sorted((root / "trading" / "audit").glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                entries.append((path.name, line))
    for file, line in entries[-tail:]:
        print(f"{file}\t{line}")


def postmortem(root: Path, argv: list[str]) -> None:
    if not argv or argv[0] != "create":
        raise ValueError("Usage: tcx postmortem create --trigger <trigger> [--tail n]")
    trigger = _option_value(argv, "--trigger") or "manual"
    report = {
        "id": f"postmortem-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')}",
        "created_by": _option_value(argv, "--created-by") or "head-manager",
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "trigger": trigger,
        "findings": [{"category": "audit-summary", "summary": "Reviewed recent audit events.", "evidence_count": 0}],
        "investment_judgment_review": {
            "original_thesis": "",
            "what_happened": "",
            "failed_assumption": "",
            "role_evidence_miss_or_overstatement": "",
            "stale_weak_or_misleading_source": "",
            "confidence_calibration": "",
            "future_warning_pattern": "",
        },
        "next_actions": ["Review rejected or adapter_error events before the next execution-sensitive workflow."],
        "improvements": [
            {
                "improvement_type": "decision_readiness",
                "improvement": "Before the next execution-sensitive workflow, confirm whether rejected or adapter_error events point to an unresolved investment or readiness gap.",
                "reason": "Postmortems should preserve reusable judgment context without changing policy, skills, or execution authority.",
                "materiality": "medium",
                "suggested_role": "head-manager",
                "applies_to": ["execution_sensitive_workflow", "postmortem_review"],
                "blocked_actions": ["order_execution"],
            }
        ],
    }
    path = root / "trading" / "reports" / "postmortem" / f"{sanitize_id(report['id'])}.postmortem_report.json"
    write_json(path, report)
    write_audit_event(root, {"type": "postmortem.created", "payload": {"id": report["id"], "path": path.relative_to(root).as_posix()}}, "head-manager", "cli")
    print_json({"status": "created", "id": report["id"], "path": path.relative_to(root).as_posix()})

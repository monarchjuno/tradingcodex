from __future__ import annotations

import sys
from pathlib import Path

from tradingcodex_service.application.artifact_quality import evaluate_artifact_quality
from tradingcodex_service.application.audit import write_audit_event
from tradingcodex_service.application.orders import request_order_approval, run_order_checks
from tradingcodex_service.application.postmortems import create_postmortem, get_postmortem, list_postmortems, record_postmortem_process_review
from tradingcodex_cli.commands.utils import _option_value, classify_artifact_path, json_object_input, print_json


def validate(root: Path, argv: list[str]) -> None:
    if len(argv) < 2 or argv[0] != "order":
        raise ValueError("Usage: tcx validate order <ticket-id>")
    result = run_order_checks(root, {"principal_id": "portfolio-manager", "ticket_id": argv[1]})
    write_audit_event(
        root,
        {
            "type": "order_ticket.validated" if result["approval_ready"] else "order_ticket.validation_failed",
            "resource": str(result.get("ticket_id") or ""),
            "decision": "approved" if result["approval_ready"] else "rejected",
            "payload": result,
        },
        "portfolio-manager",
        "cli",
    )
    print_json(result)
    if not result.get("approval_ready"):
        sys.exit(1)


def risk_check(root: Path, argv: list[str]) -> None:
    ticket_id = argv[0] if argv else _option_value(argv, "--ticket-id")
    if not ticket_id:
        raise ValueError("Usage: tcx risk-check <ticket-id>")
    checks = run_order_checks(root, {"principal_id": "risk-manager", "ticket_id": ticket_id})
    reasons = [reason for check in checks["checks"] if check["decision"] == "fail" for reason in check["reasons"]]
    result = {"decision": "go" if checks["approval_ready"] else "revise", "ticket_id": ticket_id, "reasons": reasons, "checks": checks["checks"]}
    write_audit_event(
        root,
        {"type": "risk_check", "resource": ticket_id, "decision": result["decision"], "payload": result},
        "risk-manager",
        "cli",
    )
    print_json(result)
    if result["decision"] != "go":
        sys.exit(1)


def approve(root: Path, argv: list[str]) -> None:
    ticket_id = argv[0] if argv else _option_value(argv, "--ticket-id")
    if not ticket_id:
        raise ValueError("Usage: tcx approve <ticket-id> [--approved-by risk-manager]")
    approved_by = _option_value(argv, "--approved-by") or "risk-manager"
    result = request_order_approval(root, {"principal_id": approved_by, "ticket_id": ticket_id, "expires_hours": int(_option_value(argv, "--expires-hours") or 24)})
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
    if not argv:
        print_json(list_postmortems(root))
        return
    if argv[0] == "list":
        print_json(list_postmortems(root, int(_option_value(argv[1:], "--limit") or 50)))
        return
    if argv[0] == "show":
        report_id = argv[1] if len(argv) > 1 and not argv[1].startswith("--") else _option_value(argv[1:], "--id")
        if not report_id:
            raise ValueError("Usage: tcx postmortem show <report-id>")
        print_json(get_postmortem(root, report_id))
        return
    if argv[0] == "promote-lesson":
        raise PermissionError("lesson promotion is unavailable from CLI; dispatch an authenticated judgment-reviewer MCP call")
    if argv[0] == "process-review":
        args = argv[1:]
        usage = "Usage: tcx postmortem process-review <payload.json|-> [--created-by head-manager]"
        input_path = _option_value(args, "--json-file") or (args[0] if args and not args[0].startswith("--") else None)
        payload = json_object_input(root, input_path, usage)
        created_by = _option_value(args, "--created-by") or str(payload.get("created_by") or "head-manager")
        if payload.get("judgment_id"):
            from tradingcodex_service.application.judgment_postmortems import record_judgment_process_review
            result = record_judgment_process_review(root, {**payload, "created_by": created_by})
        else:
            result = record_postmortem_process_review(root, {**payload, "created_by": created_by})
        write_audit_event(
            root,
            {
                "type": "postmortem.process_review_locked",
                "payload": {
                    "id": result["process_review"]["id"],
                    "path": result["export_path"],
                    "process_review_hash": result["process_review"]["process_review_hash"],
                },
            },
            created_by,
            "cli",
        )
        print_json(result)
        return
    if argv[0] != "create":
        raise ValueError("Usage: tcx postmortem list|process-review|create|show ...")
    args = argv[1:]
    usage = "Usage: tcx postmortem create <payload.json|-> [--created-by head-manager]"
    input_path = _option_value(args, "--json-file") or (args[0] if args and not args[0].startswith("--") else None)
    payload = json_object_input(root, input_path, usage)
    created_by = _option_value(args, "--created-by") or str(payload.get("created_by") or "head-manager")
    if payload.get("judgment_id"):
        from tradingcodex_service.application.judgment_postmortems import create_judgment_postmortem
        result = create_judgment_postmortem(root, {**payload, "created_by": created_by})
    else:
        result = create_postmortem(root, {**payload, "created_by": created_by})
    report = result["postmortem"]
    write_audit_event(
        root,
        {"type": "postmortem.created", "payload": {"id": report["id"], "path": result["export_path"], "report_hash": report["report_hash"]}},
        created_by,
        "cli",
    )
    print_json(result)

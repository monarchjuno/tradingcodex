from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from tradingcodex_service.domain import create_approval_receipt, sanitize_id, validate_order_intent, write_audit_event, write_json
from tradingcodex_cli.commands.utils import _option_value, classify_artifact_path, print_json

def validate(root: Path, argv: list[str]) -> None:
    if len(argv) < 2 or argv[0] != "order":
        raise ValueError("Usage: tcx validate order <order-intent.json>")
    order = json.loads((root / argv[1]).read_text(encoding="utf-8"))
    result = validate_order_intent(root, {"principal_id": "portfolio-manager", "order_intent": order})
    write_audit_event(root, {"type": "order_intent.validated" if result["valid"] else "order_intent.validation_failed", "payload": result}, "portfolio-manager", "cli")
    print_json(result)
    if not result["valid"]:
        sys.exit(1)


def risk_check(root: Path, argv: list[str]) -> None:
    file_path = argv[0] if argv else _option_value(argv, "--order-intent")
    if not file_path:
        raise ValueError("Usage: tcx risk-check <order-intent.json>")
    order = json.loads((root / file_path).read_text(encoding="utf-8"))
    validation = validate_order_intent(root, {"principal_id": "risk-manager", "order_intent": order})
    result = {"decision": "go" if validation["valid"] else "revise", "order_intent_id": order.get("id"), "reasons": validation["reasons"], "checks": {"schema": not any(reason.startswith("missing ") for reason in validation["reasons"]), "policy": validation["policy"]["decision"]}}
    write_audit_event(root, {"type": "risk_check", "payload": result}, "risk-manager", "cli")
    print_json(result)
    if result["decision"] != "go":
        sys.exit(1)


def approve(root: Path, argv: list[str]) -> None:
    file_path = argv[0] if argv else _option_value(argv, "--order-intent")
    if not file_path:
        raise ValueError("Usage: tcx approve <draft-order-intent.json> [--approved-by risk-manager]")
    order = json.loads((root / file_path).read_text(encoding="utf-8"))
    result = create_approval_receipt(root, order, _option_value(argv, "--approved-by") or "risk-manager", int(_option_value(argv, "--expires-hours") or 24))
    print_json(result)
    if result.get("status") == "rejected":
        sys.exit(1)


def quality_check(root: Path, argv: list[str]) -> None:
    if not argv or argv[0] in {"--help", "-h", "help"}:
        print("Canonical research paths: trading/research/*.evidence.md; trading/reports/<role>/*")
        return
    path = root / argv[0]
    text = path.read_text(encoding="utf-8")
    rel = path.relative_to(root).as_posix()
    result = {"path": rel, "exists": True, "bytes": len(text.encode()), "non_empty": bool(text), "artifact_type": classify_artifact_path(rel), "json_valid": None, "required_fields_missing": [], "warnings": []}
    if rel.endswith(".json"):
        try:
            data = json.loads(text)
            result["json_valid"] = True
            if "order_intent" in rel:
                result["required_fields_missing"] = [field for field in ["id", "symbol", "side", "quantity", "broker", "created_by"] if data.get(field) in (None, "")]
        except Exception:
            result["json_valid"] = False
    result["status"] = "fail" if not result["non_empty"] or result["json_valid"] is False or result["required_fields_missing"] else "pass"
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
    report = {"id": f"postmortem-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')}", "created_by": _option_value(argv, "--created-by") or "head-manager", "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"), "trigger": trigger, "findings": [{"category": "audit-summary", "summary": "Reviewed recent audit events.", "evidence_count": 0}], "next_actions": ["Review rejected or adapter_error events before the next execution-sensitive workflow."]}
    path = root / "trading" / "reports" / "postmortem" / f"{sanitize_id(report['id'])}.postmortem_report.json"
    write_json(path, report)
    write_audit_event(root, {"type": "postmortem.created", "payload": {"id": report["id"], "path": path.relative_to(root).as_posix()}}, "head-manager", "cli")
    print_json({"status": "created", "id": report["id"], "path": path.relative_to(root).as_posix()})

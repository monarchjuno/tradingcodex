#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


REQUIRED_FIELDS = {
    "template_id",
    "broker_id",
    "venue",
    "asset_classes",
    "products",
    "auth_model",
    "account_model",
    "instrument_model",
    "order_model",
    "validation_model",
    "event_model",
    "blocked_surfaces",
    "execution_posture",
}

BLOCKED_SURFACES = {
    "withdrawal",
    "transfer",
    "deposit_address",
    "travel_rule",
    "api_key_admin",
    "account_opening",
    "kyc",
    "subaccount_admin",
    "raw_order_submit",
    "raw_order_cancel",
}


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: validate_connector_profile.py <profile.json>", file=sys.stderr)
        return 2
    path = Path(sys.argv[1])
    profile = json.loads(path.read_text(encoding="utf-8"))
    errors = validate(profile)
    result = {"valid": not errors, "errors": errors}
    print(json.dumps(result, indent=2, sort_keys=True))
    return 1 if errors else 0


def validate(profile: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(profile, dict):
        return ["profile must be a JSON object"]
    missing = sorted(REQUIRED_FIELDS - set(profile))
    if missing:
        errors.append("missing fields: " + ", ".join(missing))
    for field in ("asset_classes", "products", "blocked_surfaces"):
        if field in profile and not isinstance(profile[field], list):
            errors.append(f"{field} must be a list")
    blocked = set(str(item) for item in profile.get("blocked_surfaces") or [])
    missing_blocked = sorted(BLOCKED_SURFACES - blocked)
    if missing_blocked:
        errors.append("blocked_surfaces must include: " + ", ".join(missing_blocked))
    enabled_tools = profile.get("enabled_mcp_tools") or []
    if isinstance(enabled_tools, list):
        unsafe = sorted(BLOCKED_SURFACES & {str(item) for item in enabled_tools})
        if unsafe:
            errors.append("blocked surfaces exposed as enabled MCP tools: " + ", ".join(unsafe))
    else:
        errors.append("enabled_mcp_tools must be a list when present")
    posture = str(profile.get("execution_posture") or "")
    if posture not in {"read_only", "paper_only", "broker_validation_only", "testnet_order_test", "live_disabled", "service_adapter_required", "unsupported"}:
        errors.append("execution_posture is invalid")
    return errors


if __name__ == "__main__":
    raise SystemExit(main())

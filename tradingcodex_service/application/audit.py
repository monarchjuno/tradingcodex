from __future__ import annotations

from pathlib import Path
from typing import Any

from tradingcodex_service.application.common import append_jsonl, now_iso, stable_hash
from tradingcodex_service.application.runtime import ensure_runtime_database, workspace_context_payload

def write_audit_event(workspace_root: Path | str, event: dict[str, Any], principal_id: str = "system", source: str = "service") -> dict[str, Any]:
    root = Path(workspace_root)
    record = {"ts": now_iso(), "event": event}
    append_jsonl(root / "trading" / "audit" / "tradingcodex-mcp.jsonl", record)
    write_audit_event_if_available(root, principal_id, source, event)
    return {"written": True, "db_canonical": True, "export_path": "trading/audit/tradingcodex-mcp.jsonl", "workspace_context": workspace_context_payload(root)}

def write_audit_event_if_available(
    workspace_root_or_principal: Path | str | None,
    principal_id_or_source: str,
    source_or_event: str | dict[str, Any],
    event: dict[str, Any] | None = None,
) -> None:
    if event is None:
        workspace_root = None
        principal_id = str(workspace_root_or_principal)
        source = str(principal_id_or_source)
        event = source_or_event if isinstance(source_or_event, dict) else {}
    else:
        workspace_root = workspace_root_or_principal
        principal_id = str(principal_id_or_source)
        source = str(source_or_event)
    try:
        if workspace_root is not None:
            ensure_runtime_database(workspace_root)
        from apps.audit.models import AuditEvent

        AuditEvent.objects.create(
            actor_principal=principal_id,
            source=source,
            action=str(event.get("type") or event.get("action") or "event"),
            resource=str(event.get("resource") or event.get("payload", {}).get("order_intent_id") or ""),
            decision=str(event.get("decision") or event.get("payload", {}).get("status") or "recorded"),
            request_hash=stable_hash(event),
            result_hash=stable_hash(event.get("payload", event)),
            workspace_context=workspace_context_payload(workspace_root),
            payload=event,
        )
    except Exception:
        return


def write_policy_decision_if_available(workspace_root_or_result: Path | str | dict[str, Any] | None, result: dict[str, Any] | None = None) -> None:
    workspace_root = None
    if result is None:
        result = workspace_root_or_result if isinstance(workspace_root_or_result, dict) else {}
    else:
        workspace_root = workspace_root_or_result
    try:
        if workspace_root is not None:
            ensure_runtime_database(workspace_root)
        from apps.policy.models import PolicyDecision

        PolicyDecision.objects.create(
            principal_id=result["principal_id"],
            action=result["action"],
            resource=result.get("resource") or "",
            decision=result["decision"],
            reasons=result["reasons"],
            workspace_context=workspace_context_payload(workspace_root),
        )
    except Exception:
        return

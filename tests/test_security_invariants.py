from __future__ import annotations

import json
import tomllib
import uuid
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import Client

from tradingcodex_cli.generator import bootstrap_workspace
from tradingcodex_service.application.execution_gateway import parse_native_execution_invocation
from tradingcodex_service.application.orders import (
    cancel_submitted_order,
    create_order_ticket,
    request_order_approval,
    run_order_checks,
    submit_approved_order,
)
from tradingcodex_service.application.runtime import ensure_runtime_database
from tradingcodex_service.mcp_runtime import call_mcp_tool, handle_mcp_rpc


def approved_ticket(tmp_path: Path) -> tuple[Path, str, dict]:
    workspace = tmp_path / f"workspace-{uuid.uuid4().hex[:8]}"
    bootstrap_workspace(workspace)
    ensure_runtime_database(workspace)
    ticket_id = f"security-{uuid.uuid4().hex}"
    create_order_ticket(
        workspace,
        {
            "principal_id": "portfolio-manager",
            "ticket_id": ticket_id,
            "symbol": "MSFT",
            "side": "buy",
            "quantity": 1,
            "limit_price": 100,
        },
    )
    checks = run_order_checks(workspace, {"principal_id": "portfolio-manager", "ticket_id": ticket_id})
    assert checks["approval_ready"] is True
    approval = request_order_approval(workspace, {"principal_id": "risk-manager", "ticket_id": ticket_id})
    assert approval["status"] == "approved"
    return workspace, ticket_id, approval["approval_receipt"]


def native_submit(workspace: Path, ticket_id: str, receipt_id: str, live_confirmation: str = "") -> dict:
    prompt = f"$tcx-order-submit --ticket-id {ticket_id} --approval-receipt-id {receipt_id}"
    if live_confirmation:
        prompt += f" --live-confirmation {live_confirmation}"
    mandate = parse_native_execution_invocation(prompt, workspace)
    assert mandate is not None
    return submit_approved_order(
        workspace,
        mandate.service_arguments(),
        native_mandate=mandate,
    )


def native_cancel(
    workspace: Path,
    ticket_id: str,
    broker_order_id: str,
    receipt_id: str,
    live_confirmation: str = "",
) -> dict:
    prompt = (
        f"$tcx-order-cancel --ticket-id {ticket_id} --broker-order-id {broker_order_id} "
        f"--approval-receipt-id {receipt_id}"
    )
    if live_confirmation:
        prompt += f" --live-confirmation {live_confirmation}"
    mandate = parse_native_execution_invocation(prompt, workspace)
    assert mandate is not None
    return cancel_submitted_order(
        workspace,
        mandate.service_arguments(),
        native_mandate=mandate,
    )


def test_submission_rejects_inline_receipts_before_adapter(monkeypatch, tmp_path: Path) -> None:
    workspace, ticket_id, receipt = approved_ticket(tmp_path)
    adapter_calls: list[dict] = []
    monkeypatch.setattr(
        "tradingcodex_service.application.orders.submit_with_adapter",
        lambda root, order: adapter_calls.append(order) or {},
    )

    mandate = parse_native_execution_invocation(
        f"$tcx-order-submit --ticket-id {ticket_id} --approval-receipt-id {receipt['approval_receipt_id']}",
        workspace,
    )
    assert mandate is not None
    with pytest.raises(PermissionError, match="do not match"):
        submit_approved_order(
            workspace,
            {
                **mandate.service_arguments(),
                "approval_receipt": {**receipt, "exact_order_hash": "forged"},
            },
            native_mandate=mandate,
        )

    assert adapter_calls == []


def test_mcp_transport_principal_cannot_be_spoofed_or_omitted(monkeypatch, tmp_path: Path) -> None:
    workspace = tmp_path / "identity-workspace"
    bootstrap_workspace(workspace)
    monkeypatch.delenv("TRADINGCODEX_MCP_PRINCIPAL", raising=False)
    message = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "create_order_ticket",
            "arguments": {"principal_id": "portfolio-manager", "ticket_id": "missing"},
        },
    }

    anonymous = handle_mcp_rpc(workspace, message)
    assert anonymous and "transport principal is required" in anonymous["error"]["message"]

    spoofed = handle_mcp_rpc(workspace, message, transport_principal="risk-manager")
    assert spoofed and "does not match" in spoofed["error"]["message"]


def test_mcp_resource_template_discovery_returns_an_empty_supported_list(tmp_path: Path) -> None:
    workspace = tmp_path / "resource-template-workspace"
    bootstrap_workspace(workspace)

    response = handle_mcp_rpc(
        workspace,
        {"jsonrpc": "2.0", "id": 1, "method": "resources/templates/list"},
    )

    assert response == {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {"resourceTemplates": []},
    }


def test_mcp_registry_failure_exposes_only_static_safe_reads(monkeypatch, tmp_path: Path) -> None:
    import tradingcodex_service.mcp_runtime as runtime

    workspace = tmp_path / "registry-failure-workspace"
    bootstrap_workspace(workspace)
    monkeypatch.setattr(
        "tradingcodex_service.application.runtime.ensure_runtime_database",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("registry database unavailable")),
    )
    runtime._REGISTRY_SYNCED = False
    runtime._REGISTRY_SYNCED_DB = ""
    runtime._REGISTRY_ERROR = ""
    try:
        listed = handle_mcp_rpc(workspace, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        assert listed is not None
        names = {tool["name"] for tool in listed["result"]["tools"]}
        assert names
        assert names <= runtime.REGISTRY_FAILURE_SAFE_READ_TOOLS
        denied = handle_mcp_rpc(
            workspace,
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "create_order_ticket", "arguments": {"symbol": "MSFT"}},
            },
            transport_principal="portfolio-manager",
        )
        assert denied is not None
        assert "registry unavailable; fail-closed" in denied["error"]["message"]
    finally:
        runtime._REGISTRY_SYNCED = False
        runtime._REGISTRY_SYNCED_DB = ""
        runtime._REGISTRY_ERROR = ""


def test_anonymous_loopback_api_is_read_only(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / "api-workspace"
    bootstrap_workspace(workspace)
    monkeypatch.setenv("TRADINGCODEX_WORKSPACE_ROOT", str(workspace))
    client = Client(REMOTE_ADDR="127.0.0.1")

    assert client.get("/api/harness/status").status_code == 200
    response = client.post(
        "/api/orders/tickets",
        data=json.dumps({"principal_id": "portfolio-manager", "symbol": "MSFT", "side": "buy", "quantity": 1, "limit_price": 100}),
        content_type="application/json",
    )
    assert response.status_code in {401, 403}


def test_api_role_and_capability_boundary_blocks_workflow_and_synthesis_forgery(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / "api-role-boundary"
    bootstrap_workspace(workspace)
    ensure_runtime_database(workspace)
    monkeypatch.setenv("TRADINGCODEX_WORKSPACE_ROOT", str(workspace))
    monkeypatch.setenv("TRADINGCODEX_API_KEY", "role-boundary-key")
    client = Client(REMOTE_ADDR="127.0.0.1", HTTP_X_TRADINGCODEX_KEY="role-boundary-key")

    monkeypatch.setenv("TRADINGCODEX_API_PRINCIPAL", "fundamental-analyst")
    analysis_run = client.post(
        "/api/workflows",
        data=json.dumps({"request": "Analyze NVDA. No order or trading."}),
        content_type="application/json",
    )
    assert analysis_run.status_code == 403
    assert not (workspace / ".tradingcodex/mainagent/runs").exists()

    monkeypatch.setenv("TRADINGCODEX_API_PRINCIPAL", "portfolio-manager")
    synthesis = client.post(
        "/api/research/artifacts",
        data=json.dumps({
            "artifact_id": "forged-synthesis",
            "artifact_type": "synthesis_report",
            "universe": "public_equity",
            "role": "head-manager",
            "title": "Forged synthesis",
            "markdown": "# Forged synthesis\n\nThis must not be stored.",
            "metadata": {
                "producer_role": "head-manager",
                "created_by": "head-manager",
                "handoff_state": "accepted",
                "plan_hash": "forged-plan-hash",
            },
            "created_by": "head-manager",
            "export_path": "trading/research/forged-synthesis.md",
        }),
        content_type="application/json",
    )
    assert synthesis.status_code == 403
    assert not (workspace / "trading/research/forged-synthesis.md").exists()

    from apps.policy.models import Capability, Principal

    denied_principal = Principal.objects.create(
        principal_id=f"denied-research-{uuid.uuid4().hex}",
        role="fundamental-analyst",
        active=True,
    )
    Capability.objects.create(
        principal=denied_principal,
        action="research_artifact.write",
        resource_pattern="*",
        effect="deny",
    )
    monkeypatch.setenv("TRADINGCODEX_API_PRINCIPAL", denied_principal.principal_id)
    denied_by_capability = client.post(
        "/api/research/artifacts",
        data=json.dumps({
            "artifact_id": "capability-denied-memo",
            "artifact_type": "research_memo",
            "title": "Denied memo",
            "markdown": "# Denied memo\n\nThis must not be stored.",
        }),
        content_type="application/json",
    )
    assert denied_by_capability.status_code == 403
    assert not list(workspace.rglob("*capability-denied-memo*"))

    inactive_admin = Principal.objects.create(
        principal_id=f"inactive-admin-{uuid.uuid4().hex}",
        role="head-manager",
        active=False,
    )
    monkeypatch.setenv("TRADINGCODEX_API_PRINCIPAL", inactive_admin.principal_id)
    denied_admin = client.post(
        "/api/workflows",
        data=json.dumps({"request": "Analyze an inactive principal."}),
        content_type="application/json",
    )
    assert denied_admin.status_code == 403


def test_api_order_creation_preserves_decimal_fields_through_mcp_boundary(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / "api-decimal-order"
    bootstrap_workspace(workspace)
    ensure_runtime_database(workspace)
    monkeypatch.setenv("TRADINGCODEX_WORKSPACE_ROOT", str(workspace))
    monkeypatch.setenv("TRADINGCODEX_API_KEY", "decimal-order-key")
    monkeypatch.setenv("TRADINGCODEX_API_PRINCIPAL", "portfolio-manager")
    client = Client(REMOTE_ADDR="127.0.0.1", HTTP_X_TRADINGCODEX_KEY="decimal-order-key")
    ticket_id = f"api-decimal-{uuid.uuid4().hex}"

    response = client.post(
        "/api/orders/tickets",
        data=json.dumps({
            "ticket_id": ticket_id,
            "symbol": "MSFT",
            "side": "buy",
            "quantity": "1.25",
            "limit_price": "100.50",
        }),
        content_type="application/json",
    )

    assert response.status_code == 200, response.content
    assert response.json()["ticket"]["ticket_id"] == ticket_id
    from apps.mcp.models import McpToolCall

    ledgers = [
        item
        for item in McpToolCall.objects.filter(
            tool_name="create_order_ticket",
            principal_id="portfolio-manager",
        )
        if item.request.get("arguments", {}).get("ticket_id") == ticket_id
    ]
    assert len(ledgers) == 1
    ledger = ledgers[0]
    assert ledger.request["arguments"]["quantity"] == "1.25"
    assert ledger.request["arguments"]["limit_price"] == "100.50"


def test_staff_api_mutations_require_csrf_without_elevating_staff_identity(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / "staff-csrf-boundary"
    bootstrap_workspace(workspace)
    ensure_runtime_database(workspace)
    monkeypatch.setenv("TRADINGCODEX_WORKSPACE_ROOT", str(workspace))
    username = f"staff-operator-{uuid.uuid4().hex}"
    user = get_user_model().objects.create(username=username, is_staff=True)

    no_csrf = Client(enforce_csrf_checks=True, REMOTE_ADDR="127.0.0.1")
    no_csrf.force_login(user)
    rejected = no_csrf.post(
        "/api/workflows",
        data=json.dumps({"request": "Analyze NVDA. No order or trading."}),
        content_type="application/json",
    )
    assert rejected.status_code == 403
    assert not (workspace / ".tradingcodex/mainagent/runs").exists()

    staff = Client(enforce_csrf_checks=True, REMOTE_ADDR="127.0.0.1")
    staff.force_login(user)
    staff.get("/")
    csrf = staff.cookies["csrftoken"].value
    accepted_admin_intake = staff.post(
        "/api/workflows",
        data=json.dumps({"request": "Analyze NVDA. No order or trading."}),
        content_type="application/json",
        HTTP_X_CSRFTOKEN=csrf,
    )
    assert accepted_admin_intake.status_code == 200
    strategy_name = f"strategy-csrf-{uuid.uuid4().hex[:12]}"
    accepted_overlay = staff.post(
        "/api/harness/strategies",
        data=json.dumps({
            "name": strategy_name,
            "description": "CSRF-protected staff overlay.",
            "body": "# CSRF-protected staff overlay\n\nReview evidence without changing authority.",
            "status": "draft",
        }),
        content_type="application/json",
        HTTP_X_CSRFTOKEN=csrf,
    )
    assert accepted_overlay.status_code == 200
    role_authored = staff.post(
        "/api/research/artifacts",
        data=json.dumps({
            "artifact_id": "staff-forged-role",
            "artifact_type": "research_memo",
            "role": "fundamental-analyst",
            "title": "Staff-forged role",
            "markdown": "# Staff-forged role\n\nThis must not be stored.",
        }),
        content_type="application/json",
        HTTP_X_CSRFTOKEN=csrf,
    )
    assert role_authored.status_code == 403
    assert not list(workspace.rglob("*staff-forged-role*"))

    monkeypatch.setenv("TRADINGCODEX_API_KEY", "csrf-independent-api-key")
    monkeypatch.setenv("TRADINGCODEX_API_PRINCIPAL", "head-manager")
    api_key = Client(
        enforce_csrf_checks=True,
        REMOTE_ADDR="127.0.0.1",
        HTTP_X_TRADINGCODEX_KEY="csrf-independent-api-key",
    )
    api_key_response = api_key.post(
        "/api/workflows",
        data=json.dumps({"request": "Analyze MSFT. No order or trading."}),
        content_type="application/json",
    )
    assert api_key_response.status_code == 200


def test_staff_username_collision_cannot_claim_agent_principal(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / "staff-principal-collision"
    bootstrap_workspace(workspace)
    ensure_runtime_database(workspace)
    monkeypatch.setenv("TRADINGCODEX_WORKSPACE_ROOT", str(workspace))
    from tradingcodex_service.mcp_runtime import prepare_mcp_runtime

    prepare_mcp_runtime(workspace)
    user, _ = get_user_model().objects.get_or_create(username="head-manager")
    user.is_staff = True
    user.save(update_fields=["is_staff"])
    client = Client(enforce_csrf_checks=True, REMOTE_ADDR="127.0.0.1")
    client.force_login(user)
    client.get("/")
    csrf = client.cookies["csrftoken"].value

    response = client.post(
        "/api/research/artifacts",
        data=json.dumps({
            "artifact_id": "staff-collision-synthesis",
            "artifact_type": "synthesis_report",
            "role": "head-manager",
            "title": "Staff collision synthesis",
            "markdown": "# Staff collision synthesis\n\nThis must not be stored.",
            "export_path": "trading/reports/head-manager/staff-collision-synthesis.md",
        }),
        content_type="application/json",
        HTTP_X_CSRFTOKEN=csrf,
    )

    assert response.status_code == 403
    assert not (workspace / "trading/reports/head-manager/staff-collision-synthesis.md").exists()


def test_order_admin_is_read_only_for_needs_review_and_execution_ledgers(tmp_path: Path) -> None:
    workspace, ticket_id, _ = approved_ticket(tmp_path)
    from django.contrib import admin
    from django.test import RequestFactory
    from django.urls import reverse

    from apps.orders.admin import AppendOnlyAdmin
    from apps.orders.models import (
        ApprovalReceipt,
        BrokerOrder,
        ExecutionResult,
        Fill,
        OrderCheckRun,
        OrderEvent,
        OrderTicket,
    )

    OrderTicket.objects.filter(ticket_id=ticket_id).update(status="NEEDS_REVIEW", current_state="NEEDS_REVIEW")
    ticket = OrderTicket.objects.get(ticket_id=ticket_id)
    original_payload = ticket.payload
    user = get_user_model().objects.create(
        username=f"order-admin-{uuid.uuid4().hex}",
        is_staff=True,
        is_superuser=True,
    )
    request = RequestFactory().get("/admin/")
    request.user = user
    for model in (ApprovalReceipt, ExecutionResult, BrokerOrder, Fill, OrderCheckRun, OrderEvent, OrderTicket):
        model_admin = admin.site._registry[model]
        assert isinstance(model_admin, AppendOnlyAdmin)
        assert set(model_admin.get_readonly_fields(request)) == {field.name for field in model._meta.fields}
        assert model_admin.has_add_permission(request) is False
        assert model_admin.has_change_permission(request) is False
        assert model_admin.has_delete_permission(request) is False

    client = Client(REMOTE_ADDR="127.0.0.1")
    client.force_login(user)
    response = client.post(
        reverse("admin:orders_orderticket_change", args=[ticket.pk]),
        data={"status": "DRAFT", "current_state": "DRAFT", "payload": json.dumps({"forged": True})},
    )

    assert response.status_code == 403
    ticket.refresh_from_db()
    assert ticket.status == "NEEDS_REVIEW"
    assert ticket.current_state == "NEEDS_REVIEW"
    assert ticket.payload == original_payload


def test_mandatory_audit_failure_rolls_back_intent_before_provider(monkeypatch, tmp_path: Path) -> None:
    workspace, ticket_id, receipt = approved_ticket(tmp_path)
    adapter_calls: list[dict] = []
    monkeypatch.setattr(
        "tradingcodex_service.application.orders.submit_with_adapter",
        lambda root, order: adapter_calls.append(order) or {},
    )
    monkeypatch.setattr(
        "apps.orders.services.write_audit_event_required",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("audit unavailable")),
    )

    with pytest.raises(RuntimeError, match="audit unavailable"):
        native_submit(workspace, ticket_id, receipt["approval_receipt_id"])

    from apps.orders.models import ApprovalReceipt, ExecutionResult, OrderTicket

    assert adapter_calls == []
    assert not ExecutionResult.objects.filter(order_ticket_id=ticket_id).exists()
    assert OrderTicket.objects.get(ticket_id=ticket_id).current_state == "APPROVED"
    assert ApprovalReceipt.objects.get(approval_receipt_id=receipt["approval_receipt_id"]).consumed_at is None


def test_provider_exception_is_durable_needs_review(monkeypatch, tmp_path: Path) -> None:
    workspace, ticket_id, receipt = approved_ticket(tmp_path)
    monkeypatch.setattr(
        "tradingcodex_service.application.orders.submit_with_adapter",
        lambda root, order: (_ for _ in ()).throw(RuntimeError("connection reset after send")),
    )

    result = native_submit(workspace, ticket_id, receipt["approval_receipt_id"])

    from apps.orders.models import BrokerOrder, ExecutionResult, OrderTicket

    assert result["status"] == "needs_review"
    execution = ExecutionResult.objects.get(order_ticket_id=ticket_id)
    assert execution.status == "needs_review"
    assert execution.provider_invoked_at is not None
    assert execution.payload["result"]["client_order_id"].startswith("tcx-")
    assert execution.payload["result"]["broker_order_id"] == ""
    assert not BrokerOrder.objects.filter(ticket__ticket_id=ticket_id).exists()
    assert OrderTicket.objects.get(ticket_id=ticket_id).current_state == "NEEDS_REVIEW"


def test_order_transition_rolls_back_when_required_audit_fails(monkeypatch, tmp_path: Path) -> None:
    workspace = tmp_path / "transition-audit-failure"
    bootstrap_workspace(workspace)
    ticket_id = f"transition-{uuid.uuid4().hex}"
    create_order_ticket(
        workspace,
        {
            "principal_id": "portfolio-manager",
            "ticket_id": ticket_id,
            "symbol": "MSFT",
            "side": "buy",
            "quantity": 1,
            "limit_price": 100,
        },
    )
    from apps.orders.models import OrderEvent, OrderTicket
    from tradingcodex_service.application.orders import transition_order_ticket

    ticket = OrderTicket.objects.get(ticket_id=ticket_id)
    monkeypatch.setattr(
        "tradingcodex_service.application.orders.write_audit_event_required",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("audit unavailable")),
    )

    with pytest.raises(RuntimeError, match="audit unavailable"):
        transition_order_ticket(ticket, "PRECHECKED", "portfolio-manager", {"check": "test"})

    ticket.refresh_from_db()
    assert ticket.current_state == "DRAFT"
    assert not OrderEvent.objects.filter(ticket=ticket, event_type="prechecked").exists()


def test_local_cancel_rolls_back_broker_and_ticket_when_final_audit_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, ticket_id, receipt = approved_ticket(tmp_path)
    from apps.audit.models import AuditEvent
    from apps.orders.models import BrokerOrder, OrderTicket
    from tradingcodex_service.application import orders as order_service

    ticket = OrderTicket.objects.get(ticket_id=ticket_id)
    ticket.current_state = "ACKED"
    ticket.status = "ACKED"
    ticket.save(update_fields=["current_state", "status"])
    broker_order = BrokerOrder.objects.create(
        ticket=ticket,
        broker_order_id=f"local-{ticket_id}",
        broker_status="submitted",
    )
    required_audit = order_service.write_audit_event_required

    def fail_final_audit(root, principal_id, source, event):
        if event.get("type") == "order_ticket.cancel.finalized":
            raise RuntimeError("final audit unavailable")
        return required_audit(root, principal_id, source, event)

    monkeypatch.setattr(order_service, "write_audit_event_required", fail_final_audit)

    result = native_cancel(
        workspace,
        ticket_id,
        broker_order.broker_order_id,
        receipt["approval_receipt_id"],
    )

    ticket.refresh_from_db()
    broker_order.refresh_from_db()
    assert result["status"] == "not_cancelable"
    assert result["error_code"] == "local_cancel_failed"
    assert ticket.current_state == "ACKED"
    assert broker_order.broker_status == "submitted"
    assert "local_cancel" not in broker_order.metadata
    assert not AuditEvent.objects.filter(
        action__in={"order_ticket.cancel.intent", "order_ticket.cancel.finalized"},
        resource=ticket_id,
    ).exists()


def test_invalid_provider_timestamp_is_durable_needs_review(monkeypatch, tmp_path: Path) -> None:
    workspace, ticket_id, receipt = approved_ticket(tmp_path)
    monkeypatch.setattr(
        "tradingcodex_service.application.orders.submit_with_adapter",
        lambda _root, _order: {
            "adapter": "paper-trading",
            "broker_order_id": f"provider-{ticket_id}",
            "status": "submitted",
            "submitted_at": "2026-01-01T00:00:00",
        },
    )

    result = native_submit(workspace, ticket_id, receipt["approval_receipt_id"])

    from apps.audit.models import AuditEvent
    from apps.orders.models import BrokerOrder, ExecutionResult, OrderTicket

    assert result["status"] == "needs_review"
    assert result["result"]["error_code"] == "submission_uncertain"
    assert "inspect canonical broker status" in "\n".join(result["reasons"])
    assert ExecutionResult.objects.get(order_ticket_id=ticket_id).status == "needs_review"
    assert OrderTicket.objects.get(ticket_id=ticket_id).current_state == "NEEDS_REVIEW"
    assert BrokerOrder.objects.get(ticket__ticket_id=ticket_id).broker_status == "unknown"
    assert AuditEvent.objects.filter(action="execution.needs_review", resource=ticket_id).exists()
    assert AuditEvent.objects.filter(action="order_ticket.needs_review", resource=ticket_id).exists()


def test_post_fill_sync_failure_is_durable_needs_review(monkeypatch, tmp_path: Path) -> None:
    workspace, ticket_id, receipt = approved_ticket(tmp_path)
    monkeypatch.setattr(
        "tradingcodex_service.application.orders._sync_ticket_account",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("account sync unavailable")),
    )

    result = native_submit(workspace, ticket_id, receipt["approval_receipt_id"])

    from apps.audit.models import AuditEvent
    from apps.orders.models import ExecutionResult, Fill, OrderTicket

    assert result["status"] == "needs_review"
    assert result["result"]["error_code"] == "local_finalization_failed"
    assert "local finalization failed" in "\n".join(result["reasons"])
    assert Fill.objects.filter(ticket__ticket_id=ticket_id).exists()
    assert ExecutionResult.objects.get(order_ticket_id=ticket_id).status == "needs_review"
    assert OrderTicket.objects.get(ticket_id=ticket_id).current_state == "NEEDS_REVIEW"
    assert AuditEvent.objects.filter(action="execution.needs_review", resource=ticket_id).exists()


def test_status_refresh_failure_is_durable_needs_review(monkeypatch, tmp_path: Path) -> None:
    workspace, ticket_id, _receipt = approved_ticket(tmp_path)
    from apps.audit.models import AuditEvent
    from apps.orders.models import BrokerOrder, OrderTicket
    from tradingcodex_service.application.orders import _refresh_broker_order_status_for_reconciliation

    ticket = OrderTicket.objects.get(ticket_id=ticket_id)
    ticket.current_state = "ACKED"
    ticket.status = "ACKED"
    ticket.save(update_fields=["current_state", "status"])
    broker_order = BrokerOrder.objects.create(
        ticket=ticket,
        broker_order_id=f"refresh-{ticket_id}",
        broker_status="submitted",
    )

    class FailingStatusAdapter:
        def get_order_status(self, _broker_order_id: str) -> dict:
            raise RuntimeError("status endpoint unavailable")

    monkeypatch.setattr(
        "tradingcodex_service.application.brokers.adapter_for_connection",
        lambda *_args, **_kwargs: FailingStatusAdapter(),
    )

    result = _refresh_broker_order_status_for_reconciliation(
        workspace,
        {
            "principal_id": "system",
            "ticket_id": ticket_id,
            "broker_order_id": broker_order.broker_order_id,
        },
    )

    assert result["status"] == "needs_review"
    assert result["error_code"] == "status_refresh_failed"
    assert "diagnostics were withheld" in "\n".join(result["reasons"])
    assert OrderTicket.objects.get(ticket_id=ticket_id).current_state == "NEEDS_REVIEW"
    assert AuditEvent.objects.filter(action="broker_order_status.needs_review", resource=ticket_id).exists()
    assert AuditEvent.objects.filter(action="order_ticket.needs_review", resource=ticket_id).exists()


def test_draft_discard_is_distinct_from_submitted_cancel(tmp_path: Path) -> None:
    workspace = tmp_path / "discard-workspace"
    bootstrap_workspace(workspace)
    ticket_id = f"draft-{uuid.uuid4().hex}"
    create_order_ticket(
        workspace,
        {
            "principal_id": "portfolio-manager",
            "ticket_id": ticket_id,
            "symbol": "MSFT",
            "side": "buy",
            "quantity": 1,
            "limit_price": 100,
        },
    )

    with pytest.raises(ValueError, match="Unknown TradingCodex tool"):
        call_mcp_tool(
            workspace,
            "cancel_submitted_order",
            {"principal_id": "execution-operator", "ticket_id": ticket_id},
        )

    discarded = call_mcp_tool(
        workspace,
        "discard_draft_order",
        {"principal_id": "portfolio-manager", "ticket_id": ticket_id},
    )
    assert discarded["status"] == "discarded"
    assert discarded["ticket"]["current_state"] == "CANCELED"


def test_revoked_approval_cannot_cancel_recorded_broker_order(tmp_path: Path) -> None:
    workspace, ticket_id, receipt = approved_ticket(tmp_path)
    from apps.orders.models import ApprovalReceipt, BrokerOrder, OrderTicket

    ticket = OrderTicket.objects.get(ticket_id=ticket_id)
    ticket.current_state = "ACKED"
    ticket.status = "ACKED"
    ticket.save(update_fields=["current_state", "status"])
    broker_order = BrokerOrder.objects.create(
        ticket=ticket,
        broker_order_id=f"broker-{ticket_id}",
        broker_status="submitted",
    )
    ApprovalReceipt.objects.filter(approval_receipt_id=receipt["approval_receipt_id"]).update(valid=False)

    result = native_cancel(
        workspace,
        ticket_id,
        broker_order.broker_order_id,
        receipt["approval_receipt_id"],
    )

    broker_order.refresh_from_db()
    ticket.refresh_from_db()
    assert result["status"] == "not_cancelable"
    assert "approval_receipt.valid must be true" in "\n".join(result["reasons"])
    assert broker_order.broker_status == "submitted"
    assert ticket.current_state == "ACKED"


def test_audit_events_are_append_only(tmp_path: Path) -> None:
    workspace = tmp_path / "audit-workspace"
    bootstrap_workspace(workspace)
    ensure_runtime_database(workspace)
    from apps.audit.models import AuditEvent

    event = AuditEvent.objects.create(action=f"security.test.{uuid.uuid4().hex}")
    event.action = "security.test.changed"
    with pytest.raises(ValidationError, match="append-only"):
        event.save()
    with pytest.raises(ValidationError, match="append-only"):
        event.delete()


def test_audit_writes_redact_secret_fields_and_error_text(monkeypatch, tmp_path: Path) -> None:
    workspace = tmp_path / "audit-redaction-workspace"
    bootstrap_workspace(workspace)
    ensure_runtime_database(workspace)
    canary = f"audit-secret-{uuid.uuid4().hex}"
    monkeypatch.setenv("BROKER_API_KEY", canary)
    from apps.audit.models import AuditEvent
    from tradingcodex_service.application.audit import write_audit_event

    result = write_audit_event(
        workspace,
        {
            "type": "security.redaction",
            "payload": {
                "api_key": canary,
                "credential_ref": "env:BROKER_API_KEY",
                "error": f"provider failed with {canary}",
            },
        },
    )

    persisted = AuditEvent.objects.get(pk=result["audit_event_id"]).payload
    exported = (workspace / result["export_path"]).read_text(encoding="utf-8")
    assert canary not in json.dumps(persisted)
    assert canary not in exported
    assert persisted["payload"]["credential_ref"] == "env:BROKER_API_KEY"


def test_generated_roles_expose_no_execution_mutation_tools(tmp_path: Path) -> None:
    workspace = tmp_path / "role-tools-workspace"
    bootstrap_workspace(workspace)
    portfolio = tomllib.loads((workspace / ".codex" / "agents" / "portfolio-manager.toml").read_text(encoding="utf-8"))
    portfolio_tools = set(portfolio["mcp_servers"]["tradingcodex"]["enabled_tools"])
    assert "discard_draft_order" in portfolio_tools
    retired_tools = {"submit_approved_order", "cancel_submitted_order", "refresh_broker_order_status"}
    configs = [workspace / ".codex" / "config.toml", *sorted((workspace / ".codex" / "agents").glob("*.toml"))]
    for config_path in configs:
        config = tomllib.loads(config_path.read_text(encoding="utf-8"))
        assert retired_tools.isdisjoint(config["mcp_servers"]["tradingcodex"]["enabled_tools"])
    assert not (workspace / ".codex" / "agents" / "execution-operator.toml").exists()
    assert (workspace / ".agents" / "skills" / "tcx-order-allow" / "SKILL.md").is_file()
    assert (workspace / ".agents" / "skills" / "tcx-order-submit" / "SKILL.md").is_file()
    assert (workspace / ".agents" / "skills" / "tcx-order-cancel" / "SKILL.md").is_file()
    assert portfolio["mcp_servers"]["tradingcodex"]["env"]["TRADINGCODEX_MCP_PRINCIPAL"] == "portfolio-manager"

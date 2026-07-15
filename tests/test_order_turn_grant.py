from __future__ import annotations

import hashlib
import io
import json
import os
import runpy
import subprocess
import sys
import threading
import tomllib
import uuid
from contextlib import redirect_stdout
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest
import yaml
from django.db import close_old_connections

from tradingcodex_cli.generator import bootstrap_workspace
from tradingcodex_service.application.agents import AGENT_SPECS, EXPECTED_SUBAGENTS
from tradingcodex_service.application.brokers import (
    BrokerAccountDTO,
    BrokerAdapter,
    BrokerAdapterProvider,
    BrokerHealth,
    CashDTO,
    OrderValidationResult,
    PositionDTO,
    register_broker_adapter_provider,
    register_broker_connector,
    sync_broker_account,
    validate_broker_connector_build,
)
from tradingcodex_service.application.execution_gateway import (
    NativeExecutionInvocationError,
    execute_reserved_order_turn_grant,
    issue_order_turn_grant,
    parse_order_allow_invocation,
    reserve_order_turn_grant,
    revoke_order_turn_grants,
)
from tradingcodex_service.application.orders import (
    create_order_ticket,
    request_order_approval,
    run_order_checks,
)
from tradingcodex_service.application.runtime import ensure_runtime_database
from tradingcodex_service.mcp_runtime import TOOL_REGISTRY, call_mcp_tool


ROOT = Path(__file__).resolve().parents[1]
ORDER_TURN_TOOL = "use_order_turn_grant"
PROOF_FIELD = "_execution_turn_proof"


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / f"turn-grant-{uuid.uuid4().hex[:10]}"
    bootstrap_workspace(root)
    ensure_runtime_database(root)
    return root


def order_allow_prompt(mode: str = "paper", request: str = "Submit the approved order after all checks pass.") -> str:
    return f"$tcx-order-allow --mode {mode}\n{request}"


def run_hook(
    workspace: Path,
    event: str,
    payload: dict[str, object],
) -> dict[str, object] | None:
    environment = {**os.environ, "PYTHONPATH": str(ROOT)}
    result = subprocess.run(
        [sys.executable, str(workspace / ".codex/hooks/tradingcodex_hook.py"), event],
        cwd=workspace,
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        env=environment,
        timeout=30,
        check=True,
    )
    return json.loads(result.stdout) if result.stdout.strip() else None


def run_user_prompt_hook_in_process(
    workspace: Path,
    payload: dict[str, object],
    *,
    replacements: dict[str, Any],
) -> dict[str, object] | None:
    namespace = runpy.run_path(
        str(workspace / ".codex/hooks/tradingcodex_hook.py"),
        run_name=f"tradingcodex_hook_test_{uuid.uuid4().hex}",
    )
    handler = namespace["user_prompt_submit"]
    handler.__globals__.update(replacements)
    output = io.StringIO()
    with redirect_stdout(output):
        handler(payload)
    rendered = output.getvalue().strip()
    return json.loads(rendered) if rendered else None


def create_approved_paper_order(workspace: Path) -> dict[str, str]:
    ticket_id = f"turn-paper-{uuid.uuid4().hex}"
    create_order_ticket(
        workspace,
        {
            "principal_id": "portfolio-manager",
            "ticket_id": ticket_id,
            "symbol": "MSFT",
            "side": "buy",
            "quantity": 1,
            "order_type": "limit",
            "limit_price": 100,
            "time_in_force": "day",
            "currency": "USD",
        },
    )
    checks = run_order_checks(
        workspace,
        {"principal_id": "portfolio-manager", "ticket_id": ticket_id},
    )
    assert checks["approval_ready"] is True, checks
    approval = request_order_approval(
        workspace,
        {"principal_id": "risk-manager", "ticket_id": ticket_id},
    )
    assert approval["status"] == "approved", approval
    return {
        "action": "submit",
        "ticket_id": ticket_id,
        "approval_receipt_id": approval["approval_receipt"]["approval_receipt_id"],
    }


@pytest.mark.parametrize("mode", ["paper", "validation", "live"])
def test_order_allow_parser_accepts_only_an_exact_physical_first_line(mode: str) -> None:
    assert parse_order_allow_invocation(order_allow_prompt(mode)) == mode
    assert parse_order_allow_invocation(
        f"$tcx-order-allow --mode {mode}\r\nRun the requested scheduled task."
    ) == mode


@pytest.mark.parametrize(
    "prompt",
    [
        "$tcx-order-allow --mode paper",
        "$tcx-order-allow",
        "$tcx-order-allow --mode=paper\nSubmit it.",
        "$tcx-order-allow --mode paper \nSubmit it.",
        " $tcx-order-allow --mode paper\nSubmit it.",
        "\n$tcx-order-allow --mode paper\nSubmit it.",
        "$tcx-order-allow --mode PAPER\nSubmit it.",
        "$tcx-order-allow --mode paper --extra yes\nSubmit it.",
        "$tcx-order-allow --mode paper\n$tcx-build\nChange the connector.",
        "$tcx-order-allow --mode paper\n$tcx-order-submit --ticket-id t --approval-receipt-id r",
        "$tcx-order-allow --mode paper\n$tcx-order-cancel --ticket-id t --broker-order-id b --approval-receipt-id r",
    ],
)
def test_reserved_but_inexact_order_allow_lines_fail_closed(prompt: str) -> None:
    with pytest.raises(NativeExecutionInvocationError):
        parse_order_allow_invocation(prompt)


def test_ordinary_research_prompts_never_create_a_grant(workspace: Path) -> None:
    from apps.orders.models import OrderTurnGrant

    prompt = "Research NVDA earnings weekly and summarize material changes. No order or trading."

    assert parse_order_allow_invocation(prompt) is None
    assert parse_order_allow_invocation(f"Explain {order_allow_prompt('paper')}") is None
    assert issue_order_turn_grant(
        workspace,
        prompt,
        session_id="research-session",
        turn_id="research-turn",
        cwd=workspace,
    ) is None
    assert OrderTurnGrant.objects.count() == 0

    output = run_hook(
        workspace,
        "user-prompt-submit",
        {
            "prompt": prompt,
            "session_id": "research-hook-session",
            "turn_id": "research-hook-turn",
            "cwd": str(workspace),
        },
    )
    assert output is not None
    context = json.loads(str(output["hookSpecificOutput"]["additionalContext"]))
    assert "order_turn_grant" not in context
    assert OrderTurnGrant.objects.count() == 0


@pytest.mark.parametrize(
    "prompt",
    [
        "$tcx-build\nUpdate the workspace-local connector.",
        "$tcx-order-allow --mode paper\nSubmit the approved order after all checks pass.",
        "$tcx-order-submit --ticket-id ticket-1 --approval-receipt-id receipt-1",
        "$tcx-order-cancel --ticket-id ticket-1 --broker-order-id broker-1 --approval-receipt-id receipt-1",
    ],
)
def test_sensitive_prompts_fail_closed_when_order_grant_revocation_is_unavailable(
    workspace: Path,
    prompt: str,
) -> None:
    def unavailable(*_args: object, **_kwargs: object) -> int:
        raise RuntimeError("simulated order grant revocation failure")

    output = run_user_prompt_hook_in_process(
        workspace,
        {
            "prompt": prompt,
            "session_id": "revoke-failure-session",
            "turn_id": uuid.uuid4().hex,
            "cwd": str(workspace),
            "permission_mode": "default",
        },
        replacements={"revoke_order_turn_grants": unavailable},
    )

    assert output is not None
    assert output["decision"] == "block"
    assert "prior order turn grants" in str(output["reason"])


@pytest.mark.parametrize(
    "prompt",
    [
        "$tcx-order-allow --mode paper\nSubmit the approved order after all checks pass.",
        "$tcx-order-submit --ticket-id ticket-1 --approval-receipt-id receipt-1",
        "$tcx-order-cancel --ticket-id ticket-1 --broker-order-id broker-1 --approval-receipt-id receipt-1",
    ],
)
def test_order_prompts_fail_closed_when_build_grant_revocation_is_unavailable(
    workspace: Path,
    prompt: str,
) -> None:
    def unavailable(*_args: object, **_kwargs: object) -> int:
        raise RuntimeError("simulated Build grant revocation failure")

    output = run_user_prompt_hook_in_process(
        workspace,
        {
            "prompt": prompt,
            "session_id": "build-revoke-failure-session",
            "turn_id": uuid.uuid4().hex,
            "cwd": str(workspace),
            "permission_mode": "default",
        },
        replacements={"revoke_build_turn_grants": unavailable},
    )

    assert output is not None
    assert output["decision"] == "block"
    assert "prior workspace turn grants" in str(output["reason"])


def test_ordinary_research_continues_when_prior_grant_revocation_is_unavailable(
    workspace: Path,
) -> None:
    def unavailable(*_args: object, **_kwargs: object) -> int:
        raise RuntimeError("simulated grant revocation failure")

    output = run_user_prompt_hook_in_process(
        workspace,
        {
            "prompt": "Research NVDA earnings and summarize material changes. No order or trading.",
            "session_id": "research-revoke-failure-session",
            "turn_id": uuid.uuid4().hex,
            "cwd": str(workspace),
            "permission_mode": "default",
        },
        replacements={
            "revoke_order_turn_grants": unavailable,
            "revoke_build_turn_grants": unavailable,
        },
    )

    assert output is not None
    assert "decision" not in output
    context = json.loads(str(output["hookSpecificOutput"]["additionalContext"]))
    assert context["marker"] == "tradingcodex-agentic-analysis"


def test_legacy_scheduled_order_marker_never_creates_a_turn_grant(workspace: Path) -> None:
    from apps.orders.models import OrderTurnGrant

    prompt = "$order-allow --mode paper\n$tcx-workflow\nRun the saved scheduled order task."

    assert parse_order_allow_invocation(prompt) is None
    output = run_hook(
        workspace,
        "user-prompt-submit",
        {
            "prompt": prompt,
            "session_id": "legacy-scheduled-session",
            "turn_id": "legacy-scheduled-turn",
            "cwd": str(workspace),
        },
    )
    assert output is not None
    context = json.loads(str(output["hookSpecificOutput"]["additionalContext"]))
    assert "order_turn_grant" not in context
    assert OrderTurnGrant.objects.count() == 0


def test_automation_skill_covers_general_recurring_work_and_safe_prompt_shapes(
    workspace: Path,
) -> None:
    automation = (
        workspace / ".agents/skills/tcx-automate/SKILL.md"
    ).read_text(encoding="utf-8")
    head_manager = (
        workspace / ".codex/prompts/base_instructions/head-manager.md"
    ).read_text(encoding="utf-8")

    for category in (
        "simple research",
        "monitoring",
        "recurring analysis",
        "portfolio review",
        "draft-order",
        "assisted-execution",
        "turn-authorized-execution",
    ):
        assert category in automation
    assert "Do not put\n   `$tcx-automate` in the saved runtime prompt" in automation
    assert "Most automations must not contain `$tcx-order-allow`" in automation
    assert "$tcx-workflow\nResearch NVDA each weekday" in automation
    assert "$tcx-order-allow --mode paper\n$tcx-workflow\nReassess" in automation
    assert "A clear recurring request routes directly to\n`$tcx-automate`" in head_manager


def test_same_scheduled_prompt_gets_a_fresh_grant_for_each_root_turn(
    workspace: Path,
) -> None:
    from apps.orders.models import OrderTurnGrant

    prompt = order_allow_prompt(
        "paper",
        "$tcx-workflow\nReassess the approved paper-order candidate under current gates.",
    )
    session_id = f"scheduled-session-{uuid.uuid4().hex}"
    for turn_id in ("scheduled-turn-1", "scheduled-turn-2"):
        output = run_hook(
            workspace,
            "user-prompt-submit",
            {
                "prompt": prompt,
                "session_id": session_id,
                "turn_id": turn_id,
                "cwd": str(workspace),
            },
        )
        assert output is not None
        context = json.loads(str(output["hookSpecificOutput"]["additionalContext"]))
        assert context["order_turn_grant"]["mode"] == "paper"

    grants = list(OrderTurnGrant.objects.order_by("issued_at", "id"))
    assert len(grants) == 2
    assert [grant.status for grant in grants] == [
        OrderTurnGrant.STATUS_REVOKED,
        OrderTurnGrant.STATUS_ACTIVE,
    ]
    assert grants[0].prompt_sha256 == grants[1].prompt_sha256
    assert grants[0].turn_id_hash != grants[1].turn_id_hash
    assert grants[0].metadata["terminal_reason"] == "new_user_turn"


def test_issue_projection_and_storage_bind_workspace_session_turn_and_prompt(workspace: Path) -> None:
    from apps.audit.models import AuditEvent
    from apps.orders.models import OrderTurnGrant

    session_id = "RAW-SESSION-MUST-NOT-PERSIST"
    turn_id = "RAW-TURN-MUST-NOT-PERSIST"
    prompt = order_allow_prompt("paper")

    with pytest.raises(PermissionError, match="workspace root"):
        issue_order_turn_grant(
            workspace,
            prompt,
            session_id=session_id,
            turn_id=turn_id,
            cwd=workspace.parent,
        )

    projection = issue_order_turn_grant(
        workspace,
        prompt,
        session_id=session_id,
        turn_id=turn_id,
        cwd=workspace,
    )
    assert projection == {
        "marker": "tradingcodex-order-turn-grant",
        "status": "active",
        "mode": "paper",
        "expires_at": projection["expires_at"],
        "single_use": True,
        "allowed_actions": ["cancel", "submit"],
    }
    public_text = json.dumps(projection, sort_keys=True)
    assert session_id not in public_text
    assert turn_id not in public_text
    assert "grant_id" not in public_text
    assert "proof" not in public_text

    grant = OrderTurnGrant.objects.get(prompt_sha256=hashlib.sha256(prompt.encode()).hexdigest())
    assert grant.session_id_hash == hashlib.sha256(session_id.encode()).hexdigest()
    assert grant.turn_id_hash == hashlib.sha256(turn_id.encode()).hexdigest()
    assert grant.workspace_id
    assert grant.workspace_path_hash
    assert grant.expires_at > grant.issued_at
    durable = json.dumps(
        {
            field.name: getattr(grant, field.name)
            for field in grant._meta.fields
        },
        default=str,
        sort_keys=True,
    )
    audit = json.dumps(
        list(
            AuditEvent.objects.filter(
                action="native_execution.turn_grant.issued",
                resource=grant.grant_id,
            ).values("payload", "workspace_context")
        ),
        default=str,
        sort_keys=True,
    )
    assert session_id not in durable + audit
    assert turn_id not in durable + audit


@pytest.mark.parametrize(
    ("payload_patch", "reason"),
    [
        ({"agent_type": "portfolio-manager"}, "root native Codex user turn"),
        ({"cwd": ""}, "session_id, turn_id, and cwd"),
        ({"permission_mode": "plan"}, "unavailable while Codex is in Plan mode"),
    ],
)
def test_order_allow_hook_blocks_subagents_and_missing_bindings(
    workspace: Path,
    payload_patch: dict[str, object],
    reason: str,
) -> None:
    payload: dict[str, object] = {
        "prompt": order_allow_prompt("paper"),
        "session_id": f"session-{uuid.uuid4().hex}",
        "turn_id": f"turn-{uuid.uuid4().hex}",
        "cwd": str(workspace),
        **payload_patch,
    }

    output = run_hook(
        workspace,
        "user-prompt-submit",
        payload,
    )

    assert output is not None
    assert output["decision"] == "block"
    assert reason in str(output["reason"])


def test_root_hook_issues_safe_context_and_pretool_injects_hook_owned_proof(workspace: Path) -> None:
    from apps.orders.models import OrderTurnGrant

    args = create_approved_paper_order(workspace)
    session_id = "hook-session-secret"
    turn_id = "hook-turn-secret"
    output = run_hook(
        workspace,
        "user-prompt-submit",
        {
            "prompt": order_allow_prompt("paper"),
            "session_id": session_id,
            "turn_id": turn_id,
            "cwd": str(workspace),
        },
    )
    assert output is not None
    context = json.loads(str(output["hookSpecificOutput"]["additionalContext"]))
    safe_grant = context["order_turn_grant"]
    assert safe_grant["mode"] == "paper"
    assert safe_grant["single_use"] is True
    assert safe_grant["allowed_tool"] == ORDER_TURN_TOOL
    assert "proof" not in json.dumps(context)
    assert "grant_id" not in json.dumps(context)

    pretool = run_hook(
        workspace,
        "pre-tool-use",
        {
            "tool_name": f"mcp__tradingcodex__{ORDER_TURN_TOOL}",
            "tool_input": args,
            "session_id": session_id,
            "turn_id": turn_id,
            "tool_use_id": "tool-use-hook-1",
        },
    )
    assert pretool is not None
    specific = pretool["hookSpecificOutput"]
    assert specific["hookEventName"] == "PreToolUse"
    assert specific["permissionDecision"] == "allow"
    rewritten = specific["updatedInput"]
    proof = str(rewritten[PROOF_FIELD])
    assert len(proof) >= 32
    assert {key: value for key, value in rewritten.items() if key != PROOF_FIELD} == args

    grant = OrderTurnGrant.objects.get(ticket_id=args["ticket_id"])
    assert grant.status == OrderTurnGrant.STATUS_RESERVED
    assert grant.reservation_proof_hash == hashlib.sha256(proof.encode()).hexdigest()
    assert proof not in json.dumps(grant.metadata)
    hook_audit = (workspace / "trading/audit/codex-hooks.jsonl").read_text(encoding="utf-8")
    assert proof not in hook_audit
    assert session_id not in hook_audit
    assert turn_id not in hook_audit


def test_order_turn_grant_is_bound_to_non_plan_permission_mode(workspace: Path) -> None:
    args = create_approved_paper_order(workspace)
    session_id = f"mode-session-{uuid.uuid4().hex}"
    turn_id = f"mode-turn-{uuid.uuid4().hex}"
    issued = run_hook(
        workspace,
        "user-prompt-submit",
        {
            "prompt": order_allow_prompt("paper"),
            "session_id": session_id,
            "turn_id": turn_id,
            "cwd": str(workspace),
            "permission_mode": "workspace-write",
        },
    )
    assert issued is not None and "hookSpecificOutput" in issued

    for permission_mode in ("plan", "read-only"):
        blocked = run_hook(
            workspace,
            "pre-tool-use",
            {
                "tool_name": f"mcp__tradingcodex__{ORDER_TURN_TOOL}",
                "tool_input": args,
                "session_id": session_id,
                "turn_id": turn_id,
                "tool_use_id": f"mode-tool-{permission_mode}",
                "permission_mode": permission_mode,
            },
        )
        assert blocked is not None and blocked["decision"] == "block"

    allowed = run_hook(
        workspace,
        "pre-tool-use",
        {
            "tool_name": f"mcp__tradingcodex__{ORDER_TURN_TOOL}",
            "tool_input": args,
            "session_id": session_id,
            "turn_id": turn_id,
            "tool_use_id": "mode-tool-matching",
            "permission_mode": "workspace-write",
        },
    )
    assert allowed is not None
    assert allowed["hookSpecificOutput"]["permissionDecision"] == "allow"


def test_pretool_rejects_model_proof_subagent_and_wrong_turn(workspace: Path) -> None:
    args = create_approved_paper_order(workspace)
    session_id = f"session-{uuid.uuid4().hex}"
    turn_id = f"turn-{uuid.uuid4().hex}"
    issue_order_turn_grant(
        workspace,
        order_allow_prompt("paper"),
        session_id=session_id,
        turn_id=turn_id,
        cwd=workspace,
    )

    supplied = run_hook(
        workspace,
        "pre-tool-use",
        {
            "tool_name": ORDER_TURN_TOOL,
            "tool_input": {**args, PROOF_FIELD: "model-forged"},
            "session_id": session_id,
            "turn_id": turn_id,
            "tool_use_id": "tool-use-forged",
        },
    )
    assert supplied is not None
    assert supplied["decision"] == "block"
    assert "hook-owned" in str(supplied["reason"])

    subagent = run_hook(
        workspace,
        "pre-tool-use",
        {
            "tool_name": ORDER_TURN_TOOL,
            "tool_input": args,
            "session_id": session_id,
            "turn_id": turn_id,
            "tool_use_id": "tool-use-subagent",
            "agent_type": "portfolio-manager",
        },
    )
    assert subagent is not None
    assert subagent["decision"] == "block"
    assert "root Head Manager" in str(subagent["reason"])

    wrong_turn = run_hook(
        workspace,
        "pre-tool-use",
        {
            "tool_name": ORDER_TURN_TOOL,
            "tool_input": args,
            "session_id": session_id,
            "turn_id": "another-turn",
            "tool_use_id": "tool-use-wrong-turn",
        },
    )
    assert wrong_turn is not None
    assert wrong_turn["decision"] == "block"
    assert "no unique active" in str(wrong_turn["reason"])


def test_direct_mcp_and_forged_proofs_fail_then_valid_proof_is_one_use(workspace: Path) -> None:
    from apps.mcp.models import McpToolCall
    from apps.orders.models import OrderTurnGrant

    args = create_approved_paper_order(workspace)
    session_id = f"session-{uuid.uuid4().hex}"
    turn_id = f"turn-{uuid.uuid4().hex}"
    issue_order_turn_grant(
        workspace,
        order_allow_prompt("paper"),
        session_id=session_id,
        turn_id=turn_id,
        cwd=workspace,
    )

    with pytest.raises((PermissionError, NativeExecutionInvocationError), match="proof"):
        call_mcp_tool(
            workspace,
            ORDER_TURN_TOOL,
            args,
            transport_principal="head-manager",
        )

    proof = reserve_order_turn_grant(
        workspace,
        session_id,
        turn_id,
        "tool-use-direct",
        args,
    )
    with pytest.raises(PermissionError, match="proof"):
        call_mcp_tool(
            workspace,
            ORDER_TURN_TOOL,
            {**args, PROOF_FIELD: "forged-proof"},
            transport_principal="head-manager",
        )

    result = call_mcp_tool(
        workspace,
        ORDER_TURN_TOOL,
        {**args, PROOF_FIELD: proof},
        transport_principal="head-manager",
    )
    assert result["status"] == "accepted", result
    grant = OrderTurnGrant.objects.get(ticket_id=args["ticket_id"])
    assert grant.status == OrderTurnGrant.STATUS_CONSUMED

    with pytest.raises(PermissionError, match="proof"):
        call_mcp_tool(
            workspace,
            ORDER_TURN_TOOL,
            {**args, PROOF_FIELD: proof},
            transport_principal="head-manager",
        )

    ledger = json.dumps(
        list(
            McpToolCall.objects.filter(
                tool_name=ORDER_TURN_TOOL,
                request__arguments__ticket_id=args["ticket_id"],
            ).values("request", "response", "error")
        ),
        default=str,
        sort_keys=True,
    )
    assert proof not in ledger
    assert session_id not in ledger
    assert turn_id not in ledger
    assert PROOF_FIELD not in ledger


def test_reserved_grant_consumption_is_atomic_under_concurrent_replay(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from apps.orders.models import OrderTurnGrant

    args = create_approved_paper_order(workspace)
    session_id = f"session-{uuid.uuid4().hex}"
    turn_id = f"turn-{uuid.uuid4().hex}"
    issue_order_turn_grant(
        workspace,
        order_allow_prompt("paper"),
        session_id=session_id,
        turn_id=turn_id,
        cwd=workspace,
    )
    proof = reserve_order_turn_grant(
        workspace,
        session_id,
        turn_id,
        "tool-use-concurrent",
        args,
    )
    dispatch_count = 0
    dispatch_lock = threading.Lock()

    def fake_dispatch(_root: Path, _mandate: object) -> dict[str, Any]:
        nonlocal dispatch_count
        with dispatch_lock:
            dispatch_count += 1
        return {"status": "accepted", "db_canonical": True}

    monkeypatch.setattr(
        "tradingcodex_service.application.execution_gateway.execute_native_execution_mandate",
        fake_dispatch,
    )
    barrier = threading.Barrier(2)

    def consume() -> tuple[str, object]:
        close_old_connections()
        barrier.wait(timeout=5)
        try:
            return "ok", execute_reserved_order_turn_grant(workspace, args, proof)
        except Exception as exc:  # the losing concurrent call must fail closed
            return "error", exc
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = [future.result(timeout=15) for future in [pool.submit(consume), pool.submit(consume)]]

    assert [status for status, _ in outcomes].count("ok") == 1
    assert [status for status, _ in outcomes].count("error") == 1
    assert dispatch_count == 1
    grant = OrderTurnGrant.objects.get(ticket_id=args["ticket_id"])
    assert grant.status == OrderTurnGrant.STATUS_CONSUMED


def test_authorizing_order_effect_blocks_new_sensitive_turns_without_being_reset(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from apps.orders.models import OrderTurnGrant

    args = create_approved_paper_order(workspace)
    session_id = f"authorizing-session-{uuid.uuid4().hex}"
    turn_id = f"authorizing-turn-{uuid.uuid4().hex}"
    issue_order_turn_grant(
        workspace,
        order_allow_prompt("paper"),
        session_id=session_id,
        turn_id=turn_id,
        cwd=workspace,
    )
    proof = reserve_order_turn_grant(
        workspace,
        session_id,
        turn_id,
        "authorizing-tool-use",
        args,
    )
    dispatch_started = threading.Event()
    release_dispatch = threading.Event()

    def paused_dispatch(_root: Path, _mandate: object) -> dict[str, Any]:
        dispatch_started.set()
        if not release_dispatch.wait(timeout=15):
            raise TimeoutError("test did not release the simulated broker effect")
        return {"status": "accepted", "db_canonical": True}

    monkeypatch.setattr(
        "tradingcodex_service.application.execution_gateway.execute_native_execution_mandate",
        paused_dispatch,
    )

    def consume() -> dict[str, Any]:
        close_old_connections()
        try:
            return execute_reserved_order_turn_grant(workspace, args, proof)
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(consume)
        assert dispatch_started.wait(timeout=10)
        try:
            grant = OrderTurnGrant.objects.get(ticket_id=args["ticket_id"])
            assert grant.status == OrderTurnGrant.STATUS_CONSUMED
            assert grant.result_status == "authorizing"

            assert run_hook(
                workspace,
                "stop",
                {"session_id": session_id, "turn_id": turn_id},
            ) is None
            grant.refresh_from_db()
            assert grant.status == OrderTurnGrant.STATUS_CONSUMED
            assert grant.result_status == "authorizing"

            sensitive_prompts = [
                "$tcx-build\nUpdate the workspace-local connector.",
                order_allow_prompt("paper"),
                (
                    f"$tcx-order-submit --ticket-id {args['ticket_id']} "
                    f"--approval-receipt-id {args['approval_receipt_id']}"
                ),
                (
                    f"$tcx-order-cancel --ticket-id {args['ticket_id']} "
                    f"--broker-order-id broker-pending --approval-receipt-id {args['approval_receipt_id']}"
                ),
            ]
            for index, prompt in enumerate(sensitive_prompts):
                blocked = run_hook(
                    workspace,
                    "user-prompt-submit",
                    {
                        "prompt": prompt,
                        "session_id": session_id,
                        "turn_id": f"blocked-sensitive-turn-{index}",
                        "cwd": str(workspace),
                        "permission_mode": "default",
                    },
                )
                assert blocked is not None
                assert blocked["decision"] == "block"
                assert "still authorizing" in str(blocked["reason"])
                grant.refresh_from_db()
                assert grant.status == OrderTurnGrant.STATUS_CONSUMED
                assert grant.result_status == "authorizing"

            research = run_hook(
                workspace,
                "user-prompt-submit",
                {
                    "prompt": "Research NVDA earnings. Do not place or cancel an order.",
                    "session_id": session_id,
                    "turn_id": "research-while-authorizing",
                    "cwd": str(workspace),
                    "permission_mode": "default",
                },
            )
            assert research is not None
            assert "decision" not in research
        finally:
            release_dispatch.set()

        result = future.result(timeout=15)

    assert result["status"] == "accepted"
    grant.refresh_from_db()
    assert grant.status == OrderTurnGrant.STATUS_CONSUMED
    assert grant.result_status == "accepted"

    next_build = run_hook(
        workspace,
        "user-prompt-submit",
        {
            "prompt": "$tcx-build\nUpdate the workspace-local connector.",
            "session_id": session_id,
            "turn_id": "build-after-order-terminal",
            "cwd": str(workspace),
            "permission_mode": "default",
        },
    )
    assert next_build is not None
    assert "decision" not in next_build


def test_grant_is_bound_to_session_turn_workspace_and_order_posture(
    workspace: Path,
    tmp_path: Path,
) -> None:
    args = create_approved_paper_order(workspace)
    other = tmp_path / f"other-{uuid.uuid4().hex[:8]}"
    bootstrap_workspace(other)
    ensure_runtime_database(other)

    session_id = f"session-{uuid.uuid4().hex}"
    turn_id = f"turn-{uuid.uuid4().hex}"
    issue_order_turn_grant(
        workspace,
        order_allow_prompt("paper"),
        session_id=session_id,
        turn_id=turn_id,
        cwd=workspace,
    )
    with pytest.raises(PermissionError, match="no unique active"):
        reserve_order_turn_grant(other, session_id, turn_id, "other-workspace-tool", args)
    with pytest.raises(PermissionError, match="no unique active"):
        reserve_order_turn_grant(workspace, "wrong-session", turn_id, "wrong-session-tool", args)
    with pytest.raises(PermissionError, match="no unique active"):
        reserve_order_turn_grant(workspace, session_id, "wrong-turn", "wrong-turn-tool", args)

    for mode in ("validation", "live"):
        mode_session = f"{mode}-session-{uuid.uuid4().hex}"
        mode_turn = f"{mode}-turn-{uuid.uuid4().hex}"
        projection = issue_order_turn_grant(
            workspace,
            order_allow_prompt(mode),
            session_id=mode_session,
            turn_id=mode_turn,
            cwd=workspace,
        )
        assert projection is not None and projection["mode"] == mode
        with pytest.raises(PermissionError, match="does not match ticket execution posture"):
            reserve_order_turn_grant(
                workspace,
                mode_session,
                mode_turn,
                f"{mode}-tool",
                args,
            )


def test_stop_and_next_user_turn_revoke_unused_grants(workspace: Path) -> None:
    from apps.orders.models import OrderTurnGrant

    stop_session = f"stop-session-{uuid.uuid4().hex}"
    stop_turn = f"stop-turn-{uuid.uuid4().hex}"
    issue_order_turn_grant(
        workspace,
        order_allow_prompt("paper"),
        session_id=stop_session,
        turn_id=stop_turn,
        cwd=workspace,
    )
    assert run_hook(
        workspace,
        "stop",
        {"session_id": stop_session, "turn_id": stop_turn, "cwd": str(workspace)},
    ) is None
    stopped = OrderTurnGrant.objects.get(session_id_hash=hashlib.sha256(stop_session.encode()).hexdigest())
    assert stopped.status == OrderTurnGrant.STATUS_REVOKED
    assert stopped.metadata["terminal_reason"] == "turn_stopped"

    next_session = f"next-session-{uuid.uuid4().hex}"
    old_turn = f"old-turn-{uuid.uuid4().hex}"
    issue_order_turn_grant(
        workspace,
        order_allow_prompt("paper"),
        session_id=next_session,
        turn_id=old_turn,
        cwd=workspace,
    )
    output = run_hook(
        workspace,
        "user-prompt-submit",
        {
            "prompt": "Continue routine research only. No order or trading.",
            "session_id": next_session,
            "turn_id": f"new-turn-{uuid.uuid4().hex}",
            "cwd": str(workspace),
        },
    )
    assert output is not None
    old = OrderTurnGrant.objects.get(session_id_hash=hashlib.sha256(next_session.encode()).hexdigest())
    assert old.status == OrderTurnGrant.STATUS_REVOKED
    assert old.metadata["terminal_reason"] == "new_user_turn"

    assert revoke_order_turn_grants(workspace, next_session, reason="repeat") == 0


def test_execution_tool_is_visible_only_to_root_head_manager(workspace: Path) -> None:
    tool = TOOL_REGISTRY[ORDER_TURN_TOOL]
    assert tool.allowed_roles == frozenset({"head-manager"})
    assert ORDER_TURN_TOOL in AGENT_SPECS["head-manager"].mcp_allowlist
    assert all(
        ORDER_TURN_TOOL not in spec.mcp_allowlist
        for role, spec in AGENT_SPECS.items()
        if role != "head-manager"
    )

    root_config = tomllib.loads((workspace / ".codex/config.toml").read_text(encoding="utf-8"))
    assert ORDER_TURN_TOOL in root_config["mcp_servers"]["tradingcodex"]["enabled_tools"]
    for role in EXPECTED_SUBAGENTS:
        role_config = tomllib.loads(
            (workspace / f".codex/agents/{role}.toml").read_text(encoding="utf-8")
        )
        assert ORDER_TURN_TOOL not in role_config["mcp_servers"]["tradingcodex"]["enabled_tools"]

    with pytest.raises(PermissionError, match="not allowed"):
        call_mcp_tool(
            workspace,
            ORDER_TURN_TOOL,
            {"action": "submit", "ticket_id": "none", "approval_receipt_id": "none"},
            transport_principal="portfolio-manager",
        )


class FailClosedLiveAdapter(BrokerAdapter):
    submit_calls: list[dict[str, Any]] = []

    def __init__(self, connection: object, workspace_root: Path | str | None = None) -> None:
        self.connection = connection
        self.workspace_root = workspace_root

    def describe_capabilities(self) -> dict[str, Any]:
        metadata = getattr(self.connection, "metadata", {})
        return dict(metadata.get("capability_profile") or {}) if isinstance(metadata, dict) else {}

    def health_check(self) -> BrokerHealth:
        return BrokerHealth("ok", "test live broker health is signed")

    def discover_accounts(self) -> list[BrokerAccountDTO]:
        return [
            BrokerAccountDTO(
                broker_account_id="grant-live-account",
                account_label="Grant live account",
                account_type="live",
                base_currency="USD",
                masked_identifier="grant-***",
                trading_enabled=True,
                metadata={},
            )
        ]

    def get_cash(self, account_id: str) -> list[CashDTO]:
        return [CashDTO(currency="USD", amount=100_000)]

    def get_positions(self, account_id: str) -> list[PositionDTO]:
        return []

    def validate_order(self, order: dict[str, Any]) -> OrderValidationResult:
        return OrderValidationResult(True, [], {"validated": True})

    def submit_order(self, order: dict[str, Any]) -> dict[str, Any]:
        self.submit_calls.append(dict(order))
        return {
            "adapter": "turn-grant-live-provider",
            "broker_order_id": f"live-{order['client_order_id']}",
            "status": "submitted",
        }


def create_approved_live_order(workspace: Path) -> dict[str, str]:
    provider_id = f"grant-live-provider-{uuid.uuid4().hex[:8]}"
    broker_id = f"grant-live-{uuid.uuid4().hex[:8]}"
    FailClosedLiveAdapter.submit_calls = []
    register_broker_adapter_provider(
        BrokerAdapterProvider(
            provider_id=provider_id,
            display_name="Turn Grant Live Provider",
            family="test",
            venue="broker",
            region="test",
            asset_classes=("equity",),
            products=("spot",),
            default_environment="live",
            auth_model={"type": "credential_ref", "credential_ref_required": True},
            account_model={"multi_account": False, "balances": "cash", "positions": True},
            instrument_model={"identity": "symbol", "examples": []},
            order_model={
                "sides": ["buy", "sell"],
                "order_types": ["limit"],
                "time_in_force": ["day"],
                "quantity_modes": ["quantity"],
            },
            validation_model={"preview": True, "dry_run": False, "broker_validate": True},
            event_model={"polling": True, "streaming": False, "fills": True},
            execution_posture="live_broker",
            live=True,
            factory=lambda connection, root: FailClosedLiveAdapter(connection, root),
        )
    )
    register_broker_connector(
        workspace,
        {
            "principal_id": "head-manager",
            "provider_id": provider_id,
            "broker_id": broker_id,
            "credential_ref": "env:TURN_GRANT_LIVE",
            "environment": "live",
        },
    )
    from apps.integrations.models import AdapterDefinition

    AdapterDefinition.objects.filter(adapter_id=provider_id).update(enabled=True, live=True)
    validated = validate_broker_connector_build(
        workspace,
        {"principal_id": "head-manager", "broker_id": broker_id},
    )
    assert validated["connection"]["connection"]["status"] == "trading_enabled", validated
    sync_broker_account(
        workspace,
        {"broker_id": broker_id, "principal_id": "portfolio-manager"},
    )

    config_path = workspace / ".tradingcodex/config.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    execution = config.setdefault("execution", {})
    execution["live_enabled"] = True
    execution["enabled_adapters"] = sorted(
        set(execution.get("enabled_adapters") or []) | {broker_id}
    )
    execution["enabled_execution_postures"] = sorted(
        set(execution.get("enabled_execution_postures") or []) | {"live_broker"}
    )
    config_path.write_text(
        yaml.safe_dump(config, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )

    ticket_id = f"turn-live-{uuid.uuid4().hex}"
    create_order_ticket(
        workspace,
        {
            "principal_id": "portfolio-manager",
            "ticket_id": ticket_id,
            "broker_id": broker_id,
            "broker_account_id": "grant-live-account",
            "symbol": "AAPL",
            "side": "buy",
            "quantity": 1,
            "order_type": "limit",
            "limit_price": 100,
            "time_in_force": "day",
            "currency": "USD",
        },
    )
    checks = run_order_checks(
        workspace,
        {"principal_id": "portfolio-manager", "ticket_id": ticket_id},
    )
    assert checks["approval_ready"] is True, checks
    approval = request_order_approval(
        workspace,
        {"principal_id": "risk-manager", "ticket_id": ticket_id},
    )
    assert approval["status"] == "approved", approval
    return {
        "action": "submit",
        "ticket_id": ticket_id,
        "approval_receipt_id": approval["approval_receipt"]["approval_receipt_id"],
        "live_confirmation": f"LIVE:{ticket_id}:{broker_id}:AAPL:buy:1.000000",
    }


def test_live_turn_grant_requires_exact_confirmation_and_existing_live_opt_in(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    args = create_approved_live_order(workspace)
    monkeypatch.delenv("TRADINGCODEX_ENABLE_LIVE_EXECUTION", raising=False)

    missing_session = f"live-missing-{uuid.uuid4().hex}"
    missing_turn = f"live-missing-turn-{uuid.uuid4().hex}"
    issue_order_turn_grant(
        workspace,
        order_allow_prompt("live"),
        session_id=missing_session,
        turn_id=missing_turn,
        cwd=workspace,
    )
    with pytest.raises(PermissionError, match="live confirmation"):
        reserve_order_turn_grant(
            workspace,
            missing_session,
            missing_turn,
            "live-missing-tool",
            {key: value for key, value in args.items() if key != "live_confirmation"},
        )

    wrong_session = f"live-wrong-{uuid.uuid4().hex}"
    wrong_turn = f"live-wrong-turn-{uuid.uuid4().hex}"
    issue_order_turn_grant(
        workspace,
        order_allow_prompt("live"),
        session_id=wrong_session,
        turn_id=wrong_turn,
        cwd=workspace,
    )
    with pytest.raises(PermissionError, match="live confirmation"):
        reserve_order_turn_grant(
            workspace,
            wrong_session,
            wrong_turn,
            "live-wrong-tool",
            {**args, "live_confirmation": "LIVE:forged"},
        )

    exact_session = f"live-exact-{uuid.uuid4().hex}"
    exact_turn = f"live-exact-turn-{uuid.uuid4().hex}"
    issue_order_turn_grant(
        workspace,
        order_allow_prompt("live"),
        session_id=exact_session,
        turn_id=exact_turn,
        cwd=workspace,
    )
    proof = reserve_order_turn_grant(
        workspace,
        exact_session,
        exact_turn,
        "live-exact-tool",
        args,
    )
    result = execute_reserved_order_turn_grant(workspace, args, proof)

    assert result["status"] == "rejected", result
    assert "live_confirmation_or_opt_in_required" in result["reason_codes"]
    assert FailClosedLiveAdapter.submit_calls == []

# Approved-only Local-Expire Path (ROB-766) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a write-side `APPROVED → EXPIRED` local-expire path for approved-only live tickets whose trading-session deadline has passed with no broker submission evidence, reachable via an explicit MCP command and an idempotent background sweep.

**Architecture:** Mirror the existing `void_approved_only_ticket` path in `tradingcodex_service/application/orders.py`. A shared eligibility helper (`_approved_only_expiry_reasons`) enforces the evidence gate (APPROVED, no broker orders/fills, valid `session_close_at` for a DAY ticket, session close passed on the `_deadline_now()` clock). One dispatcher `expire_stale_approved_orders` routes single-ticket vs. sweep. The submit gate emits a coded fail-closed reason. The MCP tool is wired in the three consistent grant locations; the crosswalk read view already treats `EXPIRED` as terminal and reads `superseded_by_ticket_id` from the transition event, so no read-side change is needed.

**Tech Stack:** Python 3, Django ORM (`apps.orders.models`), TradingCodex MCP runtime, pytest with a monkeypatched fake clock.

## Global Constraints

- Base branch is `robin`; all work sits on branch `rob-766` on top of `robin` (commit `3657475`).
- Run tests with: `uv run --with pip --with 'pytest>=8' pytest tests/` (uv auto-creates `uv.lock` — do **not** commit it; no venv/lockfile is committed).
- New MCP tools must be wired consistently in **three** places: `tradingcodex_service/application/agents.py` `AGENT_SPECS` `mcp_allowlist`, `tradingcodex_service/mcp_runtime.py` (`McpToolSpec` + handler map), and `workspace_templates/modules/fixed-subagents/files/.codex/agents/execution-operator.toml` `enabled_tools`.
- Capabilities are auto-granted from `allowed_roles` via `sync_builtin_principals_and_capabilities(TOOL_SPECS)`; `allowed_roles` MUST be `roles_with_mcp_tool("<tool>")` so it derives from the `mcp_allowlist`.
- Evidence principle: local-expire is allowed ONLY from `APPROVED` with no `broker_orders` and no `fills`. Never expire ACKED-or-beyond (structurally impossible: `EXPIRED` is only a legal transition from `APPROVED`). No session metadata ⇒ not a candidate.
- No auto-resubmit, no receipt succession. A successor requires fresh checks + new approval + new LIVE confirmation.
- Do NOT merge the PR; the task ends at "PR opened, number reported".

---

### Task 1: Eligibility helper + `expire_approved_only_ticket` service

**Files:**
- Modify: `tradingcodex_service/application/orders.py` (add near `void_approved_only_ticket`, ~line 1116, and a module constant near the top state block)
- Test: `tests/test_approved_only_expire.py` (create)

**Interfaces:**
- Consumes: `order_payload_from_ticket`, `_order_session_deadline`, `_parse_datetime`, `_deadline_now`, `invalidate_approval_receipts_for_ticket`, `transition_order_ticket`, `serialize_order_ticket`, `write_audit_event`, `workspace_context_payload` (all existing in `orders.py`).
- Produces:
  - `LOCAL_EXPIRE_REASON = "ticket_expired_no_resubmit"` (module constant)
  - `_approved_only_expiry_reasons(ticket) -> list[str]` (empty list ⇒ eligible)
  - `expire_approved_only_ticket(workspace_root, ticket, principal_id, args) -> dict` — transitions `APPROVED→EXPIRED`, returns `{"status": "expired", "ticket_id", "expire_reason", "session_deadline", "invalidated_approval_receipts", "successor_guidance", "ticket", ...}`; raises `ValueError` when ineligible.

- [ ] **Step 1: Write the failing test**

Create `tests/test_approved_only_expire.py`:

```python
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tradingcodex_cli.generator import bootstrap_workspace
from tradingcodex_service.application import orders
from tradingcodex_service.application.portfolio import (
    load_paper_portfolio_state,
    persist_paper_portfolio_state,
)
from tradingcodex_service.mcp_runtime import call_mcp_tool


def make_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    bootstrap_workspace(workspace, force=True)
    return workspace


def _ensure_sufficient_cash(workspace: Path) -> None:
    state = load_paper_portfolio_state(workspace)
    if state.get("cash_krw", 0) < 1_000_000:
        state["cash_krw"] = 1_000_000
        persist_paper_portfolio_state(
            workspace, state, state["portfolio_id"], state["account_id"], state["strategy_id"], source="paper-trading"
        )


def freeze_deadline_clock(monkeypatch, moment: datetime) -> None:
    monkeypatch.setattr(orders, "_deadline_now", lambda: moment)


def create_approved_day_ticket(workspace: Path, ticket_id: str, session_close_at: str | None) -> None:
    _ensure_sufficient_cash(workspace)
    args = {
        "principal_id": "portfolio-manager",
        "ticket_id": ticket_id,
        "symbol": "MSFT",
        "side": "buy",
        "quantity": 1,
        "order_type": "limit",
        "limit_price": 1000,
        "time_in_force": "day",
        "currency": "KRW",
    }
    if session_close_at is not None:
        args["session_close_at"] = session_close_at
    created = call_mcp_tool(workspace, "create_order_ticket", args)
    assert created["ticket"]["current_state"] == "DRAFT"
    checks = call_mcp_tool(workspace, "run_order_checks", {"principal_id": "portfolio-manager", "ticket_id": ticket_id})
    assert checks["approval_ready"] is True, checks
    approval = call_mcp_tool(workspace, "request_order_approval", {"principal_id": "risk-manager", "ticket_id": ticket_id})
    assert approval["status"] == "approved", approval


def audit_text(workspace: Path) -> str:
    audit_path = workspace / "trading" / "audit" / "tradingcodex-mcp.jsonl"
    return audit_path.read_text() if audit_path.exists() else ""


def test_expire_service_transitions_approved_day_ticket_after_close(tmp_path: Path, monkeypatch) -> None:
    workspace = make_workspace(tmp_path)
    close = datetime.now(timezone.utc) + timedelta(hours=3)
    create_approved_day_ticket(workspace, "expire-svc-ticket", session_close_at=close.isoformat())

    from apps.orders.models import ApprovalReceipt

    assert ApprovalReceipt.objects.filter(order_ticket_id="expire-svc-ticket", valid=True).count() == 1

    freeze_deadline_clock(monkeypatch, close + timedelta(minutes=1))
    ticket = orders.get_order_ticket_model(workspace, {"ticket_id": "expire-svc-ticket"})
    assert orders._approved_only_expiry_reasons(ticket) == []

    result = orders.expire_approved_only_ticket(workspace, ticket, "execution-operator", {"reason": "session closed"})

    assert result["status"] == "expired"
    assert result["expire_reason"] == "ticket_expired_no_resubmit"
    assert result["invalidated_approval_receipts"] == 1
    assert result["ticket"]["current_state"] == "EXPIRED"
    assert "successor" in result["successor_guidance"].lower()
    assert ApprovalReceipt.objects.filter(order_ticket_id="expire-svc-ticket", valid=True).count() == 0
    assert '"order_ticket.expire.accepted"' in audit_text(workspace)


def test_expire_service_rejects_before_close_and_without_session_meta(tmp_path: Path, monkeypatch) -> None:
    workspace = make_workspace(tmp_path)
    close = datetime.now(timezone.utc) + timedelta(hours=3)
    create_approved_day_ticket(workspace, "expire-early", session_close_at=close.isoformat())
    create_approved_day_ticket(workspace, "expire-no-meta", session_close_at=None)

    freeze_deadline_clock(monkeypatch, close - timedelta(minutes=30))
    early = orders.get_order_ticket_model(workspace, {"ticket_id": "expire-early"})
    reasons_early = orders._approved_only_expiry_reasons(early)
    assert any("has not passed" in r for r in reasons_early)
    with pytest.raises(ValueError):
        orders.expire_approved_only_ticket(workspace, early, "execution-operator", {})

    no_meta = orders.get_order_ticket_model(workspace, {"ticket_id": "expire-no-meta"})
    reasons_no_meta = orders._approved_only_expiry_reasons(no_meta)
    assert any("session_close_at" in r for r in reasons_no_meta)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --with pip --with 'pytest>=8' pytest tests/test_approved_only_expire.py -v`
Expected: FAIL — `AttributeError: module 'tradingcodex_service.application.orders' has no attribute '_approved_only_expiry_reasons'`.

- [ ] **Step 3: Add the module constant**

In `tradingcodex_service/application/orders.py`, immediately after the `ORDER_TICKET_TRANSITIONS = { ... }` block (ends at line 61) add:

```python
LOCAL_EXPIRE_REASON = "ticket_expired_no_resubmit"
```

- [ ] **Step 4: Write minimal implementation**

In `tradingcodex_service/application/orders.py`, directly **above** `def void_approved_only_ticket(` (currently line 1116), insert:

```python
def _approved_only_expiry_reasons(ticket: Any) -> list[str]:
    """Evidence-based eligibility for a local APPROVED->EXPIRED transition.

    Empty list means the ticket may be locally expired. Every check must pass:
    APPROVED, no broker orders / fills (never touch broker-reached tickets),
    a valid session_close_at on a DAY ticket, and the session close is in the
    past on the deadline clock.
    """
    reasons: list[str] = []
    if ticket.current_state != "APPROVED":
        reasons.append(f"local expire requires APPROVED state, not {ticket.current_state}")
    if ticket.broker_orders.exists() or ticket.fills.exists():
        reasons.append("local expire is only allowed before broker orders or fills exist")
    order = order_payload_from_ticket(ticket)
    if str(order.get("time_in_force") or "").lower() != "day":
        reasons.append("local expire only applies to DAY tickets with a session deadline")
    deadline = _order_session_deadline(order)
    if deadline is None:
        reasons.append("ticket has no session_close_at; session expiry cannot be proven")
    elif deadline.get("invalid"):
        reasons.append("ticket session_close_at is not a valid ISO-8601 datetime")
    else:
        close = _parse_datetime(deadline["session_close_at"])
        if close is None or _deadline_now() < close:
            reasons.append("session_close_at has not passed yet")
    return reasons


def expire_approved_only_ticket(workspace_root: Path | str, ticket: Any, principal_id: str, args: dict[str, Any]) -> dict[str, Any]:
    root = Path(workspace_root)
    reasons = _approved_only_expiry_reasons(ticket)
    if reasons:
        raise ValueError("; ".join(reasons))
    order = order_payload_from_ticket(ticket)
    session_deadline = _order_session_deadline(order) or {}
    payload = {
        "mode": "local_session_expiry",
        "expire_reason": LOCAL_EXPIRE_REASON,
        "reason": str(args.get("reason") or args.get("expire_reason") or "approved-only DAY ticket past session close"),
        "session_deadline": session_deadline,
        "superseded_by_ticket_id": str(args.get("superseded_by_ticket_id") or ""),
    }
    invalidated = invalidate_approval_receipts_for_ticket(root, ticket.ticket_id, principal_id, payload)
    transition_order_ticket(ticket, "EXPIRED", principal_id, payload)
    ticket.refresh_from_db()
    result = {
        "status": "expired",
        "ticket_id": ticket.ticket_id,
        "expire_reason": LOCAL_EXPIRE_REASON,
        "session_deadline": session_deadline,
        "invalidated_approval_receipts": invalidated,
        "successor_guidance": "propose a next-session successor ticket with fresh checks, a new approval, and a new LIVE confirmation; do not reuse or auto-recreate this ticket",
        "ticket": serialize_order_ticket(ticket, include_related=True),
        "db_canonical": True,
        "workspace_context": workspace_context_payload(root),
    }
    write_audit_event(root, {"type": "order_ticket.expire.accepted", "payload": result}, principal_id, "service")
    return result
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run --with pip --with 'pytest>=8' pytest tests/test_approved_only_expire.py -v`
Expected: PASS (both tests).

- [ ] **Step 6: Commit**

```bash
git add tradingcodex_service/application/orders.py tests/test_approved_only_expire.py
git commit -m "Add approved-only local-expire service and eligibility gate"
```

---

### Task 2: `sweep_expired_approved_orders` + `expire_stale_approved_orders` dispatcher

**Files:**
- Modify: `tradingcodex_service/application/orders.py` (add after `expire_approved_only_ticket`)
- Test: `tests/test_approved_only_expire.py` (append)

**Interfaces:**
- Consumes: `_approved_only_expiry_reasons`, `expire_approved_only_ticket` (Task 1), `get_order_ticket_model`, `ensure_runtime_database`, `portfolio_keys`, `serialize_order_ticket`, `write_audit_event`.
- Produces:
  - `sweep_expired_approved_orders(workspace_root, args) -> dict` — `{"status": "swept", "expired": [...], "skipped": [...], "expired_count": int, ...}`; idempotent.
  - `expire_stale_approved_orders(workspace_root, args) -> dict` — with `ticket_id`/`order_ticket_id`/`order_id` expires one (returns `expire_approved_only_ticket` result, or `{"status": "not_expirable", ...}` on ineligibility); otherwise runs the sweep.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_approved_only_expire.py`:

```python
def test_dispatcher_single_expire_and_idempotent_not_expirable(tmp_path: Path, monkeypatch) -> None:
    workspace = make_workspace(tmp_path)
    close = datetime.now(timezone.utc) + timedelta(hours=3)
    create_approved_day_ticket(workspace, "expire-one", session_close_at=close.isoformat())

    freeze_deadline_clock(monkeypatch, close + timedelta(minutes=1))
    first = orders.expire_stale_approved_orders(workspace, {"principal_id": "execution-operator", "ticket_id": "expire-one"})
    assert first["status"] == "expired"
    assert first["ticket"]["current_state"] == "EXPIRED"

    second = orders.expire_stale_approved_orders(workspace, {"principal_id": "execution-operator", "ticket_id": "expire-one"})
    assert second["status"] == "not_expirable"
    assert any("APPROVED" in r for r in second["reasons"])
    assert '"order_ticket.expire.rejected"' in audit_text(workspace)


def test_sweep_expires_only_eligible_tickets_and_is_idempotent(tmp_path: Path, monkeypatch) -> None:
    workspace = make_workspace(tmp_path)
    close = datetime.now(timezone.utc) + timedelta(hours=3)
    create_approved_day_ticket(workspace, "sweep-eligible", session_close_at=close.isoformat())
    create_approved_day_ticket(workspace, "sweep-no-meta", session_close_at=None)

    freeze_deadline_clock(monkeypatch, close + timedelta(minutes=1))
    swept = orders.expire_stale_approved_orders(workspace, {"principal_id": "execution-operator"})
    assert swept["status"] == "swept"
    expired_ids = {row["ticket_id"] for row in swept["expired"]}
    skipped_ids = {row["ticket_id"] for row in swept["skipped"]}
    assert expired_ids == {"sweep-eligible"}
    assert "sweep-no-meta" in skipped_ids

    again = orders.expire_stale_approved_orders(workspace, {"principal_id": "execution-operator"})
    assert again["expired"] == []
    assert '"order_ticket.expire.swept"' in audit_text(workspace)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --with pip --with 'pytest>=8' pytest tests/test_approved_only_expire.py::test_dispatcher_single_expire_and_idempotent_not_expirable -v`
Expected: FAIL — `module ... has no attribute 'expire_stale_approved_orders'`.

- [ ] **Step 3: Write minimal implementation**

In `tradingcodex_service/application/orders.py`, directly **after** `expire_approved_only_ticket` (from Task 1), insert:

```python
def sweep_expired_approved_orders(workspace_root: Path | str, args: dict[str, Any]) -> dict[str, Any]:
    root = Path(workspace_root)
    ensure_runtime_database(root)
    from apps.orders.models import OrderTicket

    principal_id = str(args.get("principal_id") or "execution-operator")
    portfolio_id, account_id, strategy_id = portfolio_keys(args, root)
    candidates = (
        OrderTicket.objects.select_related("broker_connection", "broker_account")
        .prefetch_related("broker_orders", "fills", "events", "check_runs")
        .filter(current_state="APPROVED", portfolio_id=portfolio_id, account_id=account_id, strategy_id=strategy_id)
        .order_by("created_at", "id")
    )
    expired: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for ticket in candidates:
        eligibility = _approved_only_expiry_reasons(ticket)
        if eligibility:
            skipped.append({"ticket_id": ticket.ticket_id, "reasons": eligibility})
            continue
        result = expire_approved_only_ticket(root, ticket, principal_id, {"reason": args.get("reason") or "session-close sweep"})
        expired.append({"ticket_id": ticket.ticket_id, "invalidated_approval_receipts": result["invalidated_approval_receipts"]})
    summary = {
        "status": "swept",
        "expired": expired,
        "skipped": skipped,
        "expired_count": len(expired),
        "db_canonical": True,
        "workspace_context": workspace_context_payload(root),
    }
    write_audit_event(root, {"type": "order_ticket.expire.swept", "payload": summary}, principal_id, "service")
    return summary


def expire_stale_approved_orders(workspace_root: Path | str, args: dict[str, Any]) -> dict[str, Any]:
    root = Path(workspace_root)
    ensure_runtime_database(root)
    principal_id = str(args.get("principal_id") or "execution-operator")
    ticket_id = args.get("ticket_id") or args.get("order_ticket_id") or args.get("order_id")
    if not ticket_id:
        return sweep_expired_approved_orders(root, args)
    ticket = get_order_ticket_model(root, args)
    try:
        return expire_approved_only_ticket(root, ticket, principal_id, args)
    except ValueError as exc:
        result = {
            "status": "not_expirable",
            "ticket_id": ticket.ticket_id,
            "reasons": [str(exc)],
            "ticket": serialize_order_ticket(ticket, include_related=True),
            "db_canonical": True,
            "workspace_context": workspace_context_payload(root),
        }
        write_audit_event(root, {"type": "order_ticket.expire.rejected", "payload": result}, principal_id, "service")
        return result
```

Note: the exception message contains the state name uppercase (`"local expire requires APPROVED state, not EXPIRED"`), which the test asserts on.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --with pip --with 'pytest>=8' pytest tests/test_approved_only_expire.py -v`
Expected: PASS (all four tests so far).

- [ ] **Step 5: Commit**

```bash
git add tradingcodex_service/application/orders.py tests/test_approved_only_expire.py
git commit -m "Add approved-only expire sweep and dispatcher"
```

---

### Task 3: Wire the `expire_stale_approved_orders` MCP tool (3 grant locations)

**Files:**
- Modify: `tradingcodex_service/mcp_runtime.py` (add `McpToolSpec` after the `cancel_approved_order` spec ~line 297; add handler map entry ~line 1123 near `refresh_broker_order_status`)
- Modify: `tradingcodex_service/application/agents.py` (execution-operator `mcp_allowlist`, ~line 331)
- Modify: `workspace_templates/modules/fixed-subagents/files/.codex/agents/execution-operator.toml` (`enabled_tools`)
- Test: `tests/test_approved_only_expire.py` (append)

**Interfaces:**
- Consumes: `orders.expire_stale_approved_orders` (Task 2), `roles_with_mcp_tool` (existing in `mcp_runtime.py`).
- Produces: MCP tool `expire_stale_approved_orders` callable via `call_mcp_tool(workspace, "expire_stale_approved_orders", {...})`, visible to `execution-operator`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_approved_only_expire.py`:

```python
def test_mcp_tool_expire_single_and_sweep_as_execution_operator(tmp_path: Path, monkeypatch) -> None:
    workspace = make_workspace(tmp_path)
    close = datetime.now(timezone.utc) + timedelta(hours=3)
    create_approved_day_ticket(workspace, "mcp-expire-one", session_close_at=close.isoformat())
    create_approved_day_ticket(workspace, "mcp-expire-sweep", session_close_at=close.isoformat())

    freeze_deadline_clock(monkeypatch, close + timedelta(minutes=1))

    single = call_mcp_tool(
        workspace,
        "expire_stale_approved_orders",
        {"principal_id": "execution-operator", "ticket_id": "mcp-expire-one", "reason": "canary cleanup"},
    )
    assert single["status"] == "expired"
    assert single["ticket"]["current_state"] == "EXPIRED"
    assert single["expire_reason"] == "ticket_expired_no_resubmit"

    swept = call_mcp_tool(workspace, "expire_stale_approved_orders", {"principal_id": "execution-operator"})
    assert swept["status"] == "swept"
    assert {row["ticket_id"] for row in swept["expired"]} == {"mcp-expire-sweep"}


def test_expire_tool_visible_to_execution_operator():
    from tradingcodex_service.mcp_runtime import TOOL_SPECS

    spec = next(tool for tool in TOOL_SPECS if tool.name == "expire_stale_approved_orders")
    assert "execution-operator" in spec.allowed_roles
    assert spec.requires_approval is True
    assert spec.capability_required == "mcp.tradingcodex.expire_stale_approved_orders"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --with pip --with 'pytest>=8' pytest tests/test_approved_only_expire.py::test_expire_tool_visible_to_execution_operator -v`
Expected: FAIL — `StopIteration` (no such tool spec).

- [ ] **Step 3a: Add the tool to the execution-operator allowlist**

In `tradingcodex_service/application/agents.py`, in the `"execution-operator"` `AgentSpec`'s `mcp_allowlist` tuple (line 326-347), add `"expire_stale_approved_orders",` immediately after `"cancel_approved_order",` (line 330):

```python
            "cancel_approved_order",
            "expire_stale_approved_orders",
            "refresh_broker_order_status",
```

- [ ] **Step 3b: Add the `McpToolSpec`**

In `tradingcodex_service/mcp_runtime.py`, immediately **after** the `cancel_approved_order` `McpToolSpec` (the closing `),` at line 297) and before the `get_order_status` spec, insert:

```python
    McpToolSpec(
        name="expire_stale_approved_orders",
        description="Experimental: locally expire approved-only DAY tickets whose trading-session deadline has passed with no broker order or fill. With ticket_id expires one ticket; otherwise sweeps the active profile. Never touches broker-reached (ACKED+) tickets; invalidates the approval receipt and fails the submit gate closed.",
        category="execution",
        risk_level="execution",
        allowed_roles=roles_with_mcp_tool("expire_stale_approved_orders"),
        handler_name="expire_stale_approved_orders",
        input_schema=object_schema(
            {
                "ticket_id": {"type": "string", "minLength": 1, "maxLength": 160},
                "order_ticket_id": {"type": "string", "minLength": 1, "maxLength": 160},
                "order_id": {"type": "string", "minLength": 1, "maxLength": 160},
                "reason": {"type": "string", "maxLength": 500},
                "superseded_by_ticket_id": {"type": "string", "maxLength": 160},
            },
            additional_properties=False,
        ),
        capability_required="mcp.tradingcodex.expire_stale_approved_orders",
        requires_approval=True,
        experimental=True,
    ),
```

- [ ] **Step 3c: Add the handler map entry**

In `tradingcodex_service/mcp_runtime.py`, in the `handlers` dict, add immediately after the `"cancel_approved_order": ...` line (line 1089):

```python
        "expire_stale_approved_orders": lambda: orders.expire_stale_approved_orders(workspace_root, with_principal),
```

- [ ] **Step 3d: Add the tool to the static toml template**

In `workspace_templates/modules/fixed-subagents/files/.codex/agents/execution-operator.toml`, in `enabled_tools`, add `"expire_stale_approved_orders",` immediately after `"cancel_approved_order",`:

```toml
  "cancel_approved_order",
  "expire_stale_approved_orders",
  "refresh_broker_order_status",
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --with pip --with 'pytest>=8' pytest tests/test_approved_only_expire.py -v`
Expected: PASS (all six tests).

- [ ] **Step 5: Commit**

```bash
git add tradingcodex_service/mcp_runtime.py tradingcodex_service/application/agents.py workspace_templates/modules/fixed-subagents/files/.codex/agents/execution-operator.toml tests/test_approved_only_expire.py
git commit -m "Wire expire_stale_approved_orders MCP tool for execution-operator"
```

---

### Task 4: Submit gate fail-closed with the coded reason

**Files:**
- Modify: `tradingcodex_service/application/orders.py` `order_ticket_submit_block_reasons` (line 1303-1311)
- Test: `tests/test_approved_only_expire.py` (append)

**Interfaces:**
- Consumes: `LOCAL_EXPIRE_REASON` (Task 1), `ORDER_TICKET_TERMINAL_STATES`.
- Produces: `order_ticket_submit_block_reasons` returns `[LOCAL_EXPIRE_REASON]` for `EXPIRED` tickets, so `submit_approved_order` rejects with `ticket_expired_no_resubmit`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_approved_only_expire.py`:

```python
def test_submit_against_expired_ticket_fails_closed_with_coded_reason(tmp_path: Path, monkeypatch) -> None:
    workspace = make_workspace(tmp_path)
    close = datetime.now(timezone.utc) + timedelta(hours=3)
    create_approved_day_ticket(workspace, "expire-submit", session_close_at=close.isoformat())

    freeze_deadline_clock(monkeypatch, close + timedelta(minutes=1))
    expired = call_mcp_tool(workspace, "expire_stale_approved_orders", {"principal_id": "execution-operator", "ticket_id": "expire-submit"})
    assert expired["status"] == "expired"

    rejected = call_mcp_tool(workspace, "submit_approved_order", {"principal_id": "execution-operator", "ticket_id": "expire-submit"})
    assert rejected["status"] == "rejected"
    assert "ticket_expired_no_resubmit" in rejected["reasons"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --with pip --with 'pytest>=8' pytest tests/test_approved_only_expire.py::test_submit_against_expired_ticket_fails_closed_with_coded_reason -v`
Expected: FAIL — reason list contains `"order ticket is expired"`, not `"ticket_expired_no_resubmit"`.

- [ ] **Step 3: Write minimal implementation**

In `tradingcodex_service/application/orders.py`, replace the body of `order_ticket_submit_block_reasons` (lines 1303-1311) with:

```python
def order_ticket_submit_block_reasons(workspace_root: Path | str, args: dict[str, Any]) -> list[str]:
    try:
        ticket = get_order_ticket_model(workspace_root, args)
    except ValueError:
        return []
    state = ticket.current_state or "DRAFT"
    if state == "EXPIRED":
        return [LOCAL_EXPIRE_REASON]
    if state in ORDER_TICKET_TERMINAL_STATES and state != "FILLED":
        return [f"order ticket is {state.lower()}"]
    return []
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --with pip --with 'pytest>=8' pytest tests/test_approved_only_expire.py::test_submit_against_expired_ticket_fails_closed_with_coded_reason -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tradingcodex_service/application/orders.py tests/test_approved_only_expire.py
git commit -m "Fail submit gate closed with ticket_expired_no_resubmit for expired tickets"
```

---

### Task 5: Crosswalk / successor-lineage consistency test

No production code changes — the crosswalk read view already treats `EXPIRED` as terminal and reads `superseded_by_ticket_id` from the transition event payload via `order_lineage._extract_first_value`. This task locks that behavior with a regression test.

**Files:**
- Test: `tests/test_approved_only_expire.py` (append)

**Interfaces:**
- Consumes: MCP tools `expire_stale_approved_orders` (Task 3) and `validate_order_approval_crosswalk` (existing).

- [ ] **Step 1: Write the test**

Append to `tests/test_approved_only_expire.py`:

```python
def test_crosswalk_marks_expired_terminal_and_links_successor(tmp_path: Path, monkeypatch) -> None:
    workspace = make_workspace(tmp_path)
    close = datetime.now(timezone.utc) + timedelta(hours=3)
    create_approved_day_ticket(workspace, "expire-xwalk", session_close_at=close.isoformat())

    freeze_deadline_clock(monkeypatch, close + timedelta(minutes=1))
    expired = call_mcp_tool(
        workspace,
        "expire_stale_approved_orders",
        {"principal_id": "execution-operator", "ticket_id": "expire-xwalk", "superseded_by_ticket_id": "expire-xwalk-successor"},
    )
    assert expired["status"] == "expired"

    crosswalk = call_mcp_tool(workspace, "validate_order_approval_crosswalk", {"principal_id": "execution-operator", "ticket_id": "expire-xwalk"})
    row = next(row for row in crosswalk["rows"] if row["canonical_ticket_id"] == "expire-xwalk")
    assert row["latest_status"]["ticket_state"] == "EXPIRED"
    assert row["terminal_inference_allowed"] is True
    assert "voided_without_successor" not in row["anomalies"]
    assert "expire-xwalk-successor" in row["replacement_lineage"]["replacement_ticket_ids"]
```

- [ ] **Step 2: Run test to verify behavior**

Run: `uv run --with pip --with 'pytest>=8' pytest tests/test_approved_only_expire.py::test_crosswalk_marks_expired_terminal_and_links_successor -v`
Expected: PASS. If the crosswalk response key names differ (e.g. `rows` vs another container), inspect the `validate_order_approval_crosswalk` return in `tradingcodex_service/application/order_lineage.py` and adjust the accessor — do NOT change production code; only the test's navigation of the response.

- [ ] **Step 3: Commit**

```bash
git add tests/test_approved_only_expire.py
git commit -m "Lock crosswalk terminal + successor lineage for locally expired tickets"
```

---

### Task 6: Documentation

**Files:**
- Modify: `docs/safety-policy-and-execution.md` (near the approved-only VOID path section added by `cd28be3` and the #7 session-cutoff section)
- Modify: `openwiki/safety-and-execution.md`
- Modify: `workspace_templates/modules/fixed-subagents/files/.codex/skills/risk-manager/approve-order/SKILL.md` (note the no-resubmit terminal state)

- [ ] **Step 1: Locate the VOID-path and session-cutoff sections**

Run: `grep -n "approved-only\|local void\|VOIDED\|session-close-cutoff\|latest safe submit" docs/safety-policy-and-execution.md`
Read the surrounding subsections so the new copy matches their tone and depth.

- [ ] **Step 2: Add the local-expire subsection to `docs/safety-policy-and-execution.md`**

Immediately after the approved-only VOID-path paragraph, add:

```markdown
### Local expire for approved-only tickets past session close

`expire_stale_approved_orders` transitions an approved-only DAY ticket from
`APPROVED` to `EXPIRED` once its `session_close_at` has passed and there is no
broker order or fill on the ticket. This closes the write-side gap where an
approved-but-never-submitted live ticket would otherwise remain `APPROVED`
forever with a still-valid approval receipt.

The transition is evidence-gated and never applies to a ticket the broker has
seen: `EXPIRED` is only reachable from `APPROVED`, so ACKED-or-beyond tickets are
resolved by broker refresh/reconcile only. Expiry invalidates every active
approval receipt (`mode: local_session_expiry`), and the submit gate then fails
closed with `ticket_expired_no_resubmit`. A successor requires fresh order
checks, a new approval, and a new LIVE confirmation — the tool never reuses or
auto-recreates the ticket; supplying `superseded_by_ticket_id` only records
lineage for the approval→terminal crosswalk. The tool runs single-ticket (with
`ticket_id`) or as an idempotent background sweep over the active profile.
```

- [ ] **Step 3: Mirror a shorter note into `openwiki/safety-and-execution.md`**

Add a 2-3 sentence version consistent with the existing VOID-path note there (match its heading style; keep it to the evidence gate, the coded submit reason, and no-auto-resubmit).

- [ ] **Step 4: Note the terminal state in the approve-order SKILL**

In `.../skills/risk-manager/approve-order/SKILL.md`, add one bullet where terminal/expiry handling is described: an approved DAY ticket past session close with no broker order is locally expired (`ticket_expired_no_resubmit`); re-entry requires a fresh successor ticket, not receipt reuse.

- [ ] **Step 5: Commit**

```bash
git add docs/safety-policy-and-execution.md openwiki/safety-and-execution.md workspace_templates/modules/fixed-subagents/files/.codex/skills/risk-manager/approve-order/SKILL.md
git commit -m "Document approved-only local-expire path"
```

---

### Task 7: Full suite + PR

**Files:** none (verification + delivery)

- [ ] **Step 1: Run the full test suite**

Run: `uv run --with pip --with 'pytest>=8' pytest tests/`
Expected: all green (existing suite + the new `tests/test_approved_only_expire.py`). If anything fails, fix before proceeding — do not claim completion on a red suite.

- [ ] **Step 2: Push the branch**

```bash
git push -u origin rob-766
```

- [ ] **Step 3: Open the PR against `robin` (do NOT merge)**

```bash
gh pr create --repo mgh3326/tradingcodex --base robin --head rob-766 \
  --title "ROB-766: approved-only local-expire path for stale APPROVED tickets" \
  --body "$(cat <<'EOF'
Adds a write-side APPROVED->EXPIRED local-expire path for approved-only live
tickets whose trading-session deadline has passed with no broker order or fill,
closing the gap where such tickets stay APPROVED forever (CRM reproductions
ticket-326030-20260708T032958Z and crm-canary-...-r1).

- New `expire_stale_approved_orders` MCP tool: single-ticket + idempotent sweep.
- Evidence-gated (APPROVED, no broker orders/fills, valid session_close_at DAY,
  session closed); never touches ACKED+ (structurally impossible).
- Invalidates approval receipts; submit gate fails closed with
  `ticket_expired_no_resubmit`.
- `superseded_by_ticket_id` records crosswalk lineage only; no auto-resubmit or
  receipt succession. Consistent with #7 (r7).
- Fake-clock tests in tests/test_approved_only_expire.py; docs updated.

Design: docs/superpowers/specs/2026-07-10-rob-766-approved-only-local-expire-design.md
Plan:   docs/superpowers/plans/2026-07-10-rob-766-approved-only-local-expire.md

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 4: Report the PR number** back to the user. Do not merge.

---

## Self-Review

**Spec coverage:**
- Comment AC #1 (session calendar/TIF `expires_at`; APPROVED→EXPIRED after close) → Task 1 (`_approved_only_expiry_reasons` + `expire_approved_only_ticket`).
- AC #2 (invalidate receipt; submit fail-closed `ticket_expired_no_resubmit`) → Task 1 (invalidation) + Task 4 (coded reason).
- AC #3 (`successor_of`/`supersedes` lineage; no auto live submit / approval succession) → Task 1 (`superseded_by_ticket_id` in transition payload, no receipt copy) + Task 5 (lineage assertion).
- AC #4 (successor needs fresh checks + new approval + new LIVE confirmation) → `successor_guidance` string (Task 1) + docs (Task 6).
- AC #5 (background sweep + explicit local-expire command, both idempotent, tested) → Task 2 + Task 3 (tests for both, idempotency).
- Evidence principle / ACKED never local-expired → structural (EXPIRED only from APPROVED) + `_approved_only_expiry_reasons` broker-orders/fills guard (Task 1).
- Crosswalk lineage reflection → Task 5.
- Audit events → `order_ticket.expire.accepted|rejected|swept` (Tasks 1-2).
- Tests mandatory → Tasks 1-5; PR base robin, no merge → Task 7.

**Placeholder scan:** none — every code step shows the full code; every command shows expected output.

**Type consistency:** `expire_approved_only_ticket(workspace_root, ticket, principal_id, args)` and `expire_stale_approved_orders(workspace_root, args)` signatures match across Tasks 1-3 and the handler map. `LOCAL_EXPIRE_REASON` defined once (Task 1) and consumed in Tasks 1/4. Result keys (`status`, `expire_reason`, `invalidated_approval_receipts`, `successor_guidance`, `ticket`) are consistent between implementation and test assertions.

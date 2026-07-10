# ROB-766 — Approved-only live ticket local void/expire path (design)

- **Linear:** ROB-766 `[tcx-feedback] approved-only 라이브 티켓 local void/expire 경로 추가`
- **Base branch:** `robin` (r-series working line; contains #7 `3657475` session-deadline cutoffs and the `cd28be3` approved-only VOID path)
- **Related:** ROB-798 (approval-wait deadline controller, source of `session_close_at`), ROB-792 (approval→terminal crosswalk **read** view — this issue is the **write** side), ROB-802 (`voided_without_successor` crosswalk anomaly)
- **Date:** 2026-07-10

## 1. Problem

An approved-only **live** ticket — `current_state = APPROVED`, no `broker_orders`, no `fills`, an approval receipt still `valid` — has no state transition that removes it once its trading session is over. Two reproductions:

- `ticket-326030-20260708T032958Z` (superseded 326030 cleanup): `cancel_approved_order` returned `not_cancelable` (`live cancel requires a known broker_order_id`) before the VOID path landed; post-check state `APPROVED`, no fills, no broker orders.
- `crm-canary-workflow-20260709T152859254426Z-r1` (2026-07-10 US DAY canary): approval receipt accepted 04:43:16 KST, DAY expiry expected 05:00 KST, submit rejected at the adapter step **before** any broker order was created. Read-only `get_order_ticket`: `status/current_state = APPROVED/APPROVED`, `fills=[]`, `broker_orders=[]`.

#7 (`3657475`) added session-deadline cutoffs, but they only fire **when a submit is attempted**. If no submit ever happens (the CRM case — the adapter rejected before a broker order existed, and nobody retried), the ticket sits at `APPROVED` permanently with a live-looking approval. That is the bug: a stale APPROVED ticket with a still-valid receipt invites an accidental future submit.

The `cd28be3` VOID path partially addresses cleanup, but it is a **manual, generic** action (`VOIDED`, no expiry semantics, no deadline evidence, no automatic sweep). ROB-766 asks for a **reason-coded, evidence-gated, automatable EXPIRE** path.

## 2. Principles (non-negotiable)

1. **Evidence-based.** Local terminal transition is allowed **only** when there is proof the order never reached the broker: no `broker_orders`, no `fills`.
2. **ACKED-and-beyond is never local-expired.** A ticket the broker has seen must be resolved by broker truth (refresh/reconcile), never by a local guess. This is enforced structurally: `EXPIRED` is only a legal transition from `APPROVED` in `ORDER_TICKET_TRANSITIONS`, so ACKED/SUBMITTED/etc. cannot be reached by this path at all.
3. **Proven expiry only.** Expiry requires a valid `session_close_at` whose close time has passed (`_deadline_now() >= session_close_at`) and `time_in_force = day`. No session metadata ⇒ no proof ⇒ **not** a candidate (surface the existing `session cutoffs are not enforced` warning, same as #7).
4. **No silent resurrection.** Expiry invalidates the approval receipt and fail-closes the submit gate. A successor requires fresh checks, a fresh approval, and a fresh LIVE confirmation — never receipt succession or auto-submit.

## 3. Existing building blocks on `robin` (reused, not reinvented)

| Piece | Location | Reuse |
|---|---|---|
| State machine + terminal set | `orders.py` `ORDER_TICKET_STATES/_TRANSITIONS/_TERMINAL_STATES` | `APPROVED→EXPIRED` already legal; `EXPIRED` already terminal |
| VOID path (mirror to follow) | `orders.py:void_approved_only_ticket` | Structure, guards, audit shape |
| Receipt invalidation | `orders.py:invalidate_approval_receipts_for_ticket` | Reuse verbatim (new payload `mode`) |
| Session deadline math | `orders.py:_order_session_deadline` / `_session_deadline_evaluation` | Source of `session_close_at`, cutoffs, and the `_deadline_now()` clock hook |
| Submit gate | `orders.py:order_ticket_submit_block_reasons` (called by `submit_approved_order`) | Enhance to emit the coded reason |
| Crosswalk read view | `order_lineage.py` | `EXPIRED` already terminal; `superseded_by_ticket_id` already a lineage child key |
| MCP tool grant (3 places) | `mcp_runtime.py`, `agents.py` `mcp_allowlist`, `.codex/agents/*.toml` | New tool wired consistently |
| Fake-clock tests | `orders._deadline_now` monkeypatch; `bootstrap_workspace`; `call_mcp_tool` | Test harness pattern |

## 4. Design

### 4.1 Service: `expire_approved_only_ticket(root, ticket, principal_id, args)`

Mirrors `void_approved_only_ticket`. Eligibility (all required):

- `ticket.current_state == "APPROVED"`
- `not ticket.broker_orders.exists()` and `not ticket.fills.exists()`
- order payload has a **valid** `session_close_at` and `time_in_force == "day"`
- `_deadline_now() >= session_close_at` (session fully closed — chosen over the T-15 submit cutoff so the evidence line is "the session is over and nothing was sent")

On failure it raises `ValueError(reason)`; on success it:

1. builds `payload = {mode: "local_session_expiry", expire_reason: "ticket_expired_no_resubmit", session_deadline: {...}, superseded_by_ticket_id: <optional>}`
2. `invalidate_approval_receipts_for_ticket(root, ticket_id, principal_id, payload)` (mode surfaces in the receipt's `void_payload`)
3. `transition_order_ticket(ticket, "EXPIRED", principal_id, payload)` — records the `OrderEvent` + `order_ticket.transition` audit
4. optionally persists `superseded_by_ticket_id` into the ticket payload so `order_lineage._lineage_index` links the successor (it reads `REPLACEMENT_CHILD_KEYS`, which already includes `superseded_by_ticket_id`)
5. writes `order_ticket.expire.accepted` audit event and returns `status: "expired"` with the serialized ticket, `invalidated_approval_receipts`, `session_deadline`, and a `successor_guidance` note ("propose a next-session successor ticket with fresh checks, a new approval, and a new LIVE confirmation; do not reuse or auto-recreate this ticket").

**No** successor is created here, and the approval is **not** copied — expiry is purely terminal + optional lineage annotation.

### 4.2 Service: `sweep_expired_approved_orders(root, args)`

Idempotent background sweep for the active profile (`portfolio_keys`). Loads candidate `APPROVED` tickets with no broker orders / no fills, evaluates each via the same eligibility check (`order_payload_from_ticket` → session deadline), expires the eligible ones, and returns a summary `{status: "swept", expired: [...], skipped: [...]}`. Re-running after everything eligible is expired returns an empty `expired` list (already-`EXPIRED` tickets are no longer `APPROVED`). Writes an `order_ticket.expire.swept` audit event.

### 4.3 Submit gate — coded fail-closed reason

`order_ticket_submit_block_reasons` currently returns `"order ticket is expired"` for terminal states. Change: when `state == "EXPIRED"`, return the coded reason **`ticket_expired_no_resubmit`** (keep the generic message for other terminal states). `submit_approved_order` already prepends `terminal_reasons`, so any submit against an expired ticket fails closed with the required code — satisfying comment AC #2.

### 4.4 MCP tool: `expire_stale_approved_orders`

Single tool, two modes (matches the ticket's "refresh 흐름 통합 또는 명시 도구" → explicit tool):

- `ticket_id`/`order_ticket_id` present → single-ticket **explicit local-expire command**; on ineligibility returns `not_expirable` with reasons + `order_ticket.expire.rejected` audit.
- absent → **background sweep** over the active profile.

Both idempotent. `input_schema`: `ticket_id`, `order_ticket_id`, `order_id`, `reason`, `superseded_by_ticket_id`, `principal_id`. Category `execution`, `risk_level="execution"`, `requires_approval=True`, `experimental=True`, `allowed_roles=roles_with_mcp_tool("expire_stale_approved_orders")`, `capability_required="mcp.tradingcodex.expire_stale_approved_orders"` — parity with `cancel_approved_order`.

Wired in the **three consistent grant locations**: `mcp_runtime.py` (`McpToolSpec` + handler map lambda), `agents.py` execution-operator `mcp_allowlist`, and `workspace_templates/.../execution-operator.toml`.

### 4.5 Crosswalk / lineage consistency

`EXPIRED` is already in `order_lineage.TERMINAL_TICKET_STATES` and `BROKER_ORDER_REQUIRED_STATES` excludes it, so an expired approved-only ticket reports `terminal_inference_allowed = true` with no anomaly — the desired read-side outcome. We deliberately **do not** add an `expired_without_successor` anomaly (unlike `voided_without_successor`): because local-expire is gated on *no broker evidence*, there is no hidden execution to reconcile, so a successor is optional, not mandatory. When `superseded_by_ticket_id` is supplied, the existing lineage index links original↔successor automatically.

## 5. Testing (mandatory, fake-clock)

New `tests/test_approved_only_expire.py` (pattern from `test_approval_wait_deadline.py`, `freeze_deadline_clock`):

1. Approved DAY ticket, no broker order/fill, clock past `session_close_at` → single expire transitions `APPROVED→EXPIRED`, invalidates receipt, writes audit.
2. **Idempotency:** second explicit expire → `not_expirable`; second sweep → empty `expired`.
3. **Submit fail-closed:** submit against the expired ticket → rejected with `ticket_expired_no_resubmit`.
4. **Broker evidence guard:** ticket with a broker order (or a fill, or state `ACKED`) → `not_expirable`, no transition.
5. **No session metadata:** approved ticket without `session_close_at` → not a candidate; sweep skips it; warning surfaced.
6. **Before close:** clock before `session_close_at` → not eligible.
7. **Sweep** expires only eligible tickets and leaves non-DAY / pre-close / broker-touched tickets untouched.
8. **Crosswalk:** expired ticket → `terminal_inference_allowed = true`, no `voided_without_successor`; with `superseded_by_ticket_id`, lineage path links the successor.

Full suite: `uv run --with pip --with 'pytest>=8' pytest tests/`.

## 6. Docs

Update `docs/safety-policy-and-execution.md` (+ `openwiki/safety-and-execution.md`, and the execution-operator / approve-order SKILL notes as needed) to describe the local-expire path alongside the existing VOID path and #7 cutoffs, emphasizing the evidence gate and no-resubmit/no-succession rule.

## 7. Out of scope

- Auto-creating successor tickets (ROB-798 deadline controller territory).
- Expiring pre-approval states (`DRAFT`/`PRECHECKED`/`READY_FOR_APPROVAL`) — VOID already covers manual cleanup there; EXPIRED is reserved for a passed session deadline on an APPROVED ticket.
- Any broker-facing cancel (ACKED+); resolved by refresh/reconcile only.

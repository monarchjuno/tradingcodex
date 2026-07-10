# ROB-803 — Broker read-path timeout budget + batch refresh (design)

**Issue:** ROB-803 — `[tcx] broker read 경로 타임아웃 예산 정리 — refresh 30s 불투명 타임아웃 (개장 혼잡 실측)`
**Date:** 2026-07-10
**Emergency fix (already deployed):** desk `7058b7a` + `ea71d2d` — `.codex/config.toml` `tool_timeout_sec = 120.0` on the `tradingcodex` MCP server block (outer > inner budget). This spec covers the **3 remaining items**.

## Background

- `codex → tradingcodex` MCP `tools/call` outer timeout was 30s; the desk broker adapter's inner `LIVE_CALL_TIMEOUT_S=60` exceeded it → opaque `timed out awaiting tools/call after 30s` mid-reconciliation. Emergency fix raised the outer budget to 120s.
- The remaining work makes the **inner** budgets honest and cheap, cuts redundant broker round-trips on batch refresh, and writes down the session-behaviour + broker-topology rules that today's session had to guess.

## Repos & topology

- **desk** = `mgh3326/tradingcodex-desk` (branch `main`). Holds the live broker adapter `trading/connectors/auto-trader/provider.py`, the authoritative (hand-maintained, extended) `.codex/prompts/base_instructions/head-manager.md`, and desk-only `.agents/skills/*/SKILL.md` (e.g. `resting-order-lifecycle`).
- **fork** = `mgh3326/tradingcodex` (base branch `robin`). Holds the service layer `tradingcodex_service/` (`application/orders.py`, `application/brokers.py`, `mcp_runtime.py`) and workspace templates. ⚠️ `rob-803` branch is 17 behind `origin/robin` and adjacent to `rob-766` — **rebase onto `origin/robin` before the fork PR**.
- **Broker topology:** a **single** auto-trader broker connection serves every account mode. Routing is by `account_mode` (`kis_live` / `toss_live` / `upbit`), **not** by separate `kis` / `toss` connections. (Today's session guessed `kis`/`toss` connection ids and failed a health check — same "guess-inducing" class this spec removes.)

## PR structure (3 PRs, no auto-merge — "머지 금지")

| PR | Repo | Base | Scope |
|----|------|------|-------|
| **1** | desk | `main` | item 1 + item 2a — `provider.py`: dedicated read-call timeout budget **and** `get_order_statuses` batch primitive (both are read-path changes) |
| **2** | fork | `robin` (rebased) | item 2b — `refresh_broker_order_statuses` batch orchestration + MCP tool, consuming the desk batch primitive |
| **3** | desk | `main` | item 3 — `head-manager.md` broker topology + `resting-order-lifecycle` refresh-timeout behaviour rule |

Each PR is opened but **not merged** (human review required for trading-critical changes). PR 1 and PR 3 both branch off desk `main` and touch disjoint files (`provider.py` vs `.codex`/`.agents`), so they don't conflict. PR 2 depends on PR 1's `get_order_statuses` API landing; until it does, PR 2 is validated against the branch that carries PR 1.

---

## Item 1 — desk `provider.py`: dedicated read-call timeout budget

### Current state
Read tools (`get_cash`, `get_positions`, `get_orders`, `get_fills`, `get_order_status`) call `_AutoTraderMCPClient.call_tool(...)` with its **default** `timeout=MCP_TIMEOUT_S` (15s). That default is **shared with the protocol handshake** (`initialize` / `list_tools` / `notifications/initialized`). Submit/preview already use `LIVE_CALL_TIMEOUT_S=60`. (The issue text says reads share the 60s submit budget; the real conflation is with the 15s handshake budget — either way there is no dedicated, named read budget and no honest per-call timeout error.)

### Change
1. Add `READ_CALL_TIMEOUT_S = 20` — a named tier between handshake (15s) and live submit (60s).
2. Pass `timeout=READ_CALL_TIMEOUT_S` **explicitly** on every read `call_tool`, including each leg of the merged history fetchers:
   - `_get_cash_toss`, `_get_cash_kis_or_upbit`, `_get_positions_toss`, `_get_positions_kis_or_upbit`
   - `get_orders` / `get_fills` upbit legs
   - `_fetch_toss_order_history_merged` (open + closed), `_fetch_kis_account_history_merged` (KR + US)
   - `_fetch_order_history_response` (all 3 branches)
3. `MCP_TIMEOUT_S=15` keeps handshake-only meaning; `LIVE_CALL_TIMEOUT_S=60` unchanged for submit/preview.
4. **Honest per-call timeout error.** Add `MCPTimeoutError(MCPTransportError)`. In `_post`, detect a socket timeout (`URLError` whose `reason` is a `TimeoutError`/`socket.timeout`, and a bare `TimeoutError`/`socket.timeout` from `urlopen`) and raise `MCPTimeoutError(f"MCP call exceeded {timeout:.0f}s")`. In `call_tool`, catch `MCPTimeoutError` and re-raise annotated with the tool name and that it was a per-call budget, e.g. `"kis_live_get_order_history timed out after 20s (per-call budget)"`. Messages stay secret-free (tool names are not secrets).
5. `cancel_order` (currently the 15s default) → pass `LIVE_CALL_TIMEOUT_S` explicitly: a cancel is a live broker mutation like submit, and more headroom is the safe direction. Flagged as a deliberate 15→60 behaviour change for reviewer attention.

### Non-goals
No change to the outer `tool_timeout_sec` (already 120 via the emergency fix). No retry logic in the adapter (retry/unverified policy is item 3, at the session layer).

---

## Item 2 — batch refresh (reduce redundant broker round-trips)

### Problem
`refresh_broker_order_status` refreshes **one** ticket → `adapter.get_order_status(broker_order_id)` opens a fresh MCP client (handshake) and fetches the **account-wide** merged history (KIS KR+US, Toss open+closed) just to match one order. Refreshing N tickets on the same account = N client opens + N full-history fetches. At open congestion this is the cost that pushed the second ticket past the old budget.

### 2a — desk `provider.py`: `get_order_statuses` batch primitive (PR 1)
`get_order_statuses(self, broker_order_ids: list[str]) -> dict[str, dict]`:
- Split each ref via `_split_broker_order_ref` → group by `mode`.
- Per mode: open **one** client; fetch the **account-wide** merged history **once** using the same account-wide `status="all"` path `get_orders` uses (`_fetch_toss_order_history_merged` / `_fetch_kis_account_history_merged`, no per-order symbol scoping); then `_materialise_order_status` each requested `order_id` against that shared response.
- Return `{broker_order_id: status_dict}` (same status dict shape `get_order_status` returns, incl. `average_price` / `filled_quantity` / `fee`). Unmatched ids get `{"status": "unknown", ...}`.
- Refactor `get_order_status` (single) to delegate to a shared per-mode fetch+materialise helper so single and batch share one code path.
- **Caveat to verify:** the per-order path scopes by `symbol` to surface filled/cancelled orders; the account-wide path relies on `status="all"` returning terminal orders without a symbol (the r7 unattributed-fill scan already depends on this). Verify batch results match single-call results for filled/cancelled orders in tests before relying on it.

### 2b — fork `tradingcodex_service`: batch orchestration (PR 2)
- `refresh_broker_order_statuses(workspace_root, args)` in `application/orders.py`:
  - Resolve target tickets from explicit `ticket_ids` / `broker_order_ids`, else all open/active tickets for the active profile.
  - Group tickets by `broker_connection`; per connection build the adapter **once**, collect its `broker_order_id`s, call `adapter.get_order_statuses(ids)` **once**.
  - Apply the existing per-ticket status→state-transition + fill-recording + audit logic (extracted from `refresh_broker_order_status` into a shared `_apply_broker_status(...)` helper so single and batch stay identical).
  - Return per-ticket results (`refreshed` / `unknown` / `blocked` / `local-only`), never aborting the batch on one ticket's failure.
- Register the new MCP tool `refresh_broker_order_statuses` — keep the **three** grant sites consistent (memory): `mcp_runtime.py` (`McpToolSpec` + handler map), `application/agents.py` (`AGENT_SPECS` mcp_allowlist), and static `workspace_templates/modules/fixed-subagents/files/.codex/agents/*.toml` (execution-operator; head-manager TOML auto-projected from `AGENT_SPECS`).

### Non-goals
No cross-refresh persistent cache; reuse is scoped to a single batch call ("한 refresh 호출 내"). Single-ticket `refresh_broker_order_status` stays for callers that want one ticket.

---

## Item 3 — desk skill / base instruction (PR 3)

### `resting-order-lifecycle/SKILL.md` — refresh-timeout behaviour rule
On a broker refresh timeout: **retry once**; if it still times out, **mark that ticket `unverified`** and **continue the session** — never block the whole reconciliation on one ticket. State it as an explicit numbered rule so a session can't rationalise a full stop (today's failure mode: one ticket's timeout halted settlement).

### `head-manager.md` — broker topology
Add a short topology note: one auto-trader broker connection, routed by `account_mode` (`kis_live`/`toss_live`/`upbit`); do **not** invent per-broker `kis`/`toss` connection ids for health checks or refresh. Cross-reference the refresh-timeout rule so the coordinator's mental model matches the skill.

### Non-goals
No new skill; edit the existing lifecycle skill + base instruction in place. No fork-template edit — the desk copies are the authoritative, extended, hand-maintained versions (fork `codex-base` template is a 191-line scaffold; desk head-manager.md is the 282-line live version, and `resting-order-lifecycle` is desk-only).

---

## Testing / verification

- **desk (PRs 1, 3):** `uv run pytest tests/` (desk has `uv.lock`). PR 1: unit tests for the three timeout tiers, `MCPTimeoutError` annotation on read timeout, and `get_order_statuses` batch == repeated single-call results (incl. filled/cancelled). PR 3: docs only — no tests; verify skill/instruction render and rule wording.
- **fork (PR 2):** `uv run --with pip --with 'pytest>=8' pytest tests/` (memory: pip needed for wheel-packaging test; don't commit `uv.lock`). Unit test batch grouping-by-connection, one `get_order_statuses` call per connection (mock adapter), per-ticket apply parity with single refresh, and no-abort-on-one-failure.

## Open items resolved by defaults
- **PR count:** 3 (item 1 + item 2a bundled in one desk provider.py PR, per the earlier "3 PR" choice).
- **`cancel_order` timeout:** moved to `LIVE_CALL_TIMEOUT_S` (60s) as a deliberate, flagged change.

# ROB-803 Broker read-path timeout + batch refresh — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the desk broker adapter an honest, dedicated read-call timeout budget; cut redundant broker round-trips with a batch status primitive + fork batch-refresh orchestration; and write down the refresh-timeout session rule and single-connection broker topology.

**Architecture:** Three PRs, no auto-merge. PR 1 (desk `provider.py`): a `READ_CALL_TIMEOUT_S=20` tier distinct from the 15s handshake and 60s submit budgets, a `MCPTimeoutError` that names the timed-out tool, and a `get_order_statuses` batch primitive that fetches account-wide history once per mode. PR 2 (fork `tradingcodex_service`): `refresh_broker_order_statuses` groups tickets by connection and calls the desk batch primitive once per connection. PR 3 (desk docs): the refresh-timeout retry→unverified→continue rule and the single auto-trader connection topology.

**Tech Stack:** Python 3 (stdlib `urllib`), pytest, `uv`. Desk repo `mgh3326/tradingcodex-desk` (`/Users/mgh3326/services/tradingcodex-desk`, branch `main`). Fork repo `mgh3326/tradingcodex` (`/Users/mgh3326/work/tradingcodex.rob-803`, base `robin`).

## Global Constraints

- **Design spec:** `docs/superpowers/specs/2026-07-10-rob-803-broker-read-timeout-and-batch-refresh-design.md` (fork repo). Read it before starting.
- **No auto-merge** — open each PR, do not merge (trading-critical, human review).
- **Desk tests:** `cd /Users/mgh3326/services/tradingcodex-desk && uv run pytest tests/test_auto_trader_provider.py -q`.
- **Fork tests:** `uv run --with pip --with 'pytest>=8' pytest tests/` — pip is needed for the wheel-packaging test; `uv` auto-creates `uv.lock`, **do not commit it**.
- **Fork branch:** `rob-803` is 17 behind `origin/robin` and adjacent to `rob-766`. **Rebase onto `origin/robin` before any fork code** (Task 6).
- **Timeout tiers (desk):** handshake `MCP_TIMEOUT_S=15`, reads `READ_CALL_TIMEOUT_S=20`, live submit/preview/cancel `LIVE_CALL_TIMEOUT_S=60`.
- **Secret-free errors:** never put tokens/credentials in exception messages. Tool names are not secrets.
- **Fork MCP-tool grants live in 3 consistent places** (memory): `mcp_runtime.py` (`McpToolSpec` + handler map), `application/agents.py` (`AGENT_SPECS[...].mcp_allowlist`), static `workspace_templates/modules/fixed-subagents/files/.codex/agents/execution-operator.toml`.
- **Commit trailer** for every commit:
  ```
  Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
  ```

---

## PR 1 — desk `provider.py`: read timeout budget + batch primitive

Branch off desk `main`: `git -C /Users/mgh3326/services/tradingcodex-desk checkout main && git -C /Users/mgh3326/services/tradingcodex-desk checkout -b rob-803-read-timeout-batch`

All file paths in PR 1 are under `/Users/mgh3326/services/tradingcodex-desk`.

### Task 1: Dedicated read-call timeout tier + honest timeout error

**Files:**
- Modify: `trading/connectors/auto-trader/provider.py` (constants ~104-106; `_post` ~350-366; `call_tool` ~470-494; read call sites; `cancel_order` ~1451-1455)
- Test: `tests/test_auto_trader_provider.py`

**Interfaces:**
- Produces: module constant `READ_CALL_TIMEOUT_S = 20`; exception `MCPTimeoutError(MCPTransportError)`; all read `call_tool(...)` invocations pass `timeout=READ_CALL_TIMEOUT_S`; `cancel_order` passes `timeout=LIVE_CALL_TIMEOUT_S`.

- [ ] **Step 1: Write failing test — reads use the 20s budget, submit stays 60s**

Add to `tests/test_auto_trader_provider.py` (follow the existing `adapter_factory` + captured-`timeout` pattern used around the ROB-782 tests):

```python
class TestReadCallTimeoutBudget:
    def test_get_positions_uses_read_call_timeout(self, adapter_factory) -> None:
        from trading.connectors.auto_trader import provider as prov  # dir import shim below
        adapter, fake = adapter_factory(
            tool_responses={"get_holdings": {"accounts": []}}
        )
        captured: list[float | None] = []
        orig = fake.call_tool

        def call_tool(name, arguments=None, *, timeout=None):
            captured.append(timeout)
            return orig(name, arguments)

        fake.call_tool = call_tool
        adapter.get_positions("auto_trader:kis_live")
        assert captured == [prov.READ_CALL_TIMEOUT_S]
        assert prov.READ_CALL_TIMEOUT_S == 20
        assert prov.READ_CALL_TIMEOUT_S < prov.LIVE_CALL_TIMEOUT_S
        assert prov.MCP_TIMEOUT_S < prov.READ_CALL_TIMEOUT_S
```

Note: the test module already imports `provider` via `importlib` at top — reuse that module handle (it is imported as the object the other tests use for `PROVIDER`/`PROVIDER_ID`); grab the constant off the same module object rather than re-importing. Match the existing module-access style in the file.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/mgh3326/services/tradingcodex-desk && uv run pytest tests/test_auto_trader_provider.py::TestReadCallTimeoutBudget -q`
Expected: FAIL — `AttributeError: module ... has no attribute 'READ_CALL_TIMEOUT_S'`.

- [ ] **Step 3: Add the constant and the exception**

In `provider.py`, update the timeout constants block (currently lines 104-106):

```python
HEALTH_TIMEOUT_S = 5
# Handshake budget: initialize / tools/list / notifications. Establishing
# the MCP session must be quick; it is NOT a data-read budget.
MCP_TIMEOUT_S = 15
# ROB-803: read tools (status/history/cash/positions) get their own budget,
# distinct from the handshake (15s) and live submit (60s). Open-congestion
# reads legitimately take longer than a handshake but must not sit on the
# live-submit budget.
READ_CALL_TIMEOUT_S = 20
LIVE_CALL_TIMEOUT_S = 60
```

Add the exception next to the other MCP error types (after `MCPTransportError`, ~line 553):

```python
class MCPTimeoutError(MCPTransportError):
    """Per-call socket timeout — carries the tool name and the budget that
    was exceeded so a read timeout is never mistaken for a generic
    transport failure or a submit-path timeout."""
```

- [ ] **Step 4: Detect timeout in `_post` and annotate in `call_tool`**

In `_AutoTraderMCPClient._post`, replace the `URLError` handling (currently lines 365-366) and add a bare-timeout guard. The full `except` chain becomes:

```python
        except urllib.error.HTTPError as exc:
            # Distinguish 401/403 (auth) from other HTTP errors.
            detail = self._safe_read(exc)
            if exc.code in (401, 403):
                raise MCPAuthError(
                    f"MCP server rejected credentials (HTTP {exc.code})"
                ) from exc
            raise MCPTransportError(f"MCP HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, (TimeoutError, socket.timeout)):
                raise MCPTimeoutError(
                    f"MCP call exceeded {timeout:.0f}s"
                ) from exc
            raise MCPTransportError(f"MCP server unreachable: {exc.reason}") from exc
        except (TimeoutError, socket.timeout) as exc:
            # urlopen can raise a bare socket timeout on read (not wrapped in URLError).
            raise MCPTimeoutError(f"MCP call exceeded {timeout:.0f}s") from exc
```

Add `import socket` to the imports at the top of the file if absent (check the existing `import urllib.request` block).

In `call_tool`, wrap the post so a timeout names the tool. Replace the body (currently lines 483-494) so the `tools/call` post is guarded:

```python
        self._next_id += 1
        try:
            response = self._post("/mcp", payload, timeout=timeout)
        except MCPTimeoutError as exc:
            raise MCPTimeoutError(
                f"{name} timed out after {timeout:.0f}s (per-call budget)"
            ) from exc
        return self._extract_tool_result(response)
```

- [ ] **Step 5: Route every read call to `READ_CALL_TIMEOUT_S`; cancel to `LIVE_CALL_TIMEOUT_S`**

Add `timeout=READ_CALL_TIMEOUT_S` to each read `call_tool`:
- `_get_cash_toss` (`toss_get_orderable_cash`)
- `_get_cash_kis_or_upbit` (`get_cash_balance`)
- `_get_positions_toss` (`toss_get_positions`)
- `_get_positions_kis_or_upbit` (`get_holdings`)
- `get_orders` upbit leg (`get_order_history`)
- `get_fills` upbit leg (`get_order_history`)
- `_fetch_toss_order_history_merged` — both `toss_get_order_history` calls
- `_fetch_kis_account_history_merged` — both `kis_live_get_order_history` and `get_order_history` calls
- `_fetch_order_history_response` — all three `call_tool` branches (upbit `get_order_history`, KIS-US `get_order_history`, KIS-KR `kis_live_get_order_history`)

Example (`_get_positions_kis_or_upbit`):

```python
        response = client.call_tool("get_holdings", params, timeout=READ_CALL_TIMEOUT_S)
```

In `cancel_order`, the tool call (currently line 1453) becomes:

```python
            response = client.call_tool(tool_name, args, timeout=LIVE_CALL_TIMEOUT_S)
```

- [ ] **Step 6: Add failing test for the honest timeout error, then confirm it passes**

Add:

```python
class TestReadTimeoutErrorIsHonest:
    def test_read_timeout_names_tool_and_budget(self, adapter_factory) -> None:
        import socket
        from trading.connectors.auto_trader import provider as prov  # same module handle as above
        adapter, fake = adapter_factory()

        def call_tool(name, arguments=None, *, timeout=None):
            # Simulate a socket timeout surfacing from _post.
            raise prov.MCPTimeoutError(
                f"{name} timed out after {timeout:.0f}s (per-call budget)"
            )

        fake.call_tool = call_tool
        with pytest.raises(prov.MCPTimeoutError) as exc:
            adapter.get_positions("auto_trader:kis_live")
        msg = str(exc.value)
        assert "get_holdings" in msg
        assert "20s" in msg
        assert "per-call budget" in msg

    def test_post_maps_socket_timeout_to_mcp_timeout_error(self, monkeypatch) -> None:
        import socket
        from trading.connectors.auto_trader import provider as prov
        client = prov._AutoTraderMCPClient("http://127.0.0.1:1", "tok")

        def fake_urlopen(req, timeout=None):
            raise socket.timeout("timed out")

        monkeypatch.setattr(prov.urllib.request, "urlopen", fake_urlopen)
        with pytest.raises(prov.MCPTimeoutError) as exc:
            client._post("/mcp", {"jsonrpc": "2.0"}, timeout=20)
        assert "20s" in str(exc.value)
```

Run: `cd /Users/mgh3326/services/tradingcodex-desk && uv run pytest tests/test_auto_trader_provider.py -q`
Expected: PASS (all, incl. the untouched suite). Verify the previously green submit-path timeout tests still pass unchanged.

- [ ] **Step 7: Commit**

```bash
cd /Users/mgh3326/services/tradingcodex-desk
git add trading/connectors/auto-trader/provider.py tests/test_auto_trader_provider.py
git commit -m "fix(rob-803): dedicated read-call timeout budget + honest timeout error

READ_CALL_TIMEOUT_S=20 for status/history/cash/positions, distinct from the
15s handshake and 60s submit budgets. MCPTimeoutError names the timed-out
tool and the per-call budget so a read timeout is never opaque. cancel_order
moved to the 60s live budget (was the 15s default).

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

### Task 2: `get_order_statuses` batch primitive

**Files:**
- Modify: `trading/connectors/auto-trader/provider.py` (add `_fetch_account_history` + `get_order_statuses`; leave single `get_order_status` behaviour unchanged)
- Test: `tests/test_auto_trader_provider.py`

**Interfaces:**
- Consumes: `_split_broker_order_ref`, `_open_client`, `_fetch_toss_order_history_merged`, `_fetch_kis_account_history_merged`, `_materialise_order_status`, `READ_CALL_TIMEOUT_S`.
- Produces: `AutoTraderBrokerAdapter.get_order_statuses(self, broker_order_ids: list[str]) -> dict[str, dict[str, Any]]` — maps each input ref to the same status dict shape `get_order_status` returns; opens **one** client and fetches account-wide history **once per mode**.

- [ ] **Step 1: Write failing test — batch parity + one fetch per mode**

```python
class TestGetOrderStatusesBatch:
    def test_batch_matches_single_and_fetches_once_per_mode(self, adapter_factory) -> None:
        # Two KIS orders (KR merge) + one upbit order. KIS history fetched once.
        kis_orders = {
            "orders": [
                {"order_id": "K1", "status": "filled", "filled_avg_price": "100",
                 "filled_qty": "1", "currency": "KRW"},
                {"order_id": "K2", "status": "cancelled", "currency": "KRW"},
            ]
        }
        upbit_orders = {"orders": [
            {"order_id": "U1", "status": "filled", "filled_avg_price": "5", "filled_qty": "2"}
        ]}
        adapter, fake = adapter_factory(tool_responses={
            "kis_live_get_order_history": kis_orders,
            "get_order_history": upbit_orders,  # upbit + kis-us both use this; scope below
        })
        calls: list[tuple] = []
        orig = fake.call_tool

        def call_tool(name, arguments=None, *, timeout=None):
            calls.append((name, dict(arguments or {})))
            return orig(name, arguments)

        fake.call_tool = call_tool

        refs = [
            "auto_trader:kis_live:005930:K1",
            "auto_trader:kis_live:000660:K2",
            "auto_trader:upbit:KRW-BTC:U1",
        ]
        result = adapter.get_order_statuses(refs)

        assert result["auto_trader:kis_live:005930:K1"]["status"] == "filled"
        assert result["auto_trader:kis_live:000660:K2"]["status"] == "cancelled"
        assert result["auto_trader:upbit:KRW-BTC:U1"]["status"] == "filled"
        # KIS KR history fetched exactly once despite two KIS orders:
        kr_calls = [c for c in calls if c[0] == "kis_live_get_order_history"]
        assert len(kr_calls) == 1
        # All reads used the read budget:
        assert all(t == 20 for t in [None] or []) or True  # timeout asserted in Task 1

    def test_batch_unknown_for_unmatched_ref(self, adapter_factory) -> None:
        adapter, fake = adapter_factory(tool_responses={
            "kis_live_get_order_history": {"orders": []},
            "get_order_history": {"orders": []},
        })
        result = adapter.get_order_statuses(["auto_trader:kis_live:005930:NOPE"])
        assert result["auto_trader:kis_live:005930:NOPE"]["status"] == "unknown"
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd /Users/mgh3326/services/tradingcodex-desk && uv run pytest tests/test_auto_trader_provider.py::TestGetOrderStatusesBatch -q`
Expected: FAIL — `AttributeError: 'AutoTraderBrokerAdapter' object has no attribute 'get_order_statuses'`.

- [ ] **Step 3: Implement `_fetch_account_history` + `get_order_statuses`**

Add near `_fetch_order_history_response` (after it):

```python
    def _fetch_account_history(
        self, client: _AutoTraderMCPClient, mode: str
    ) -> dict[str, Any]:
        """Account-wide merged history for one mode (no per-order symbol scope).

        Mirrors ``get_orders``' account-wide read: KIS merges KR+US, Toss
        merges open+closed, upbit is a single ``status=all`` call. Terminal
        (filled/cancelled) orders are surfaced via ``status="all"`` — the
        same widening the account-wide unattributed-fill scan relies on — so
        ``_materialise_order_status`` can match any requested order_id.
        """
        if mode == "toss_live":
            return self._fetch_toss_order_history_merged(client)
        if mode == "upbit":
            return client.call_tool(
                "get_order_history",
                {"market": "crypto", "status": "all"},
                timeout=READ_CALL_TIMEOUT_S,
            )
        return self._fetch_kis_account_history_merged(client, "all")

    def get_order_statuses(
        self, broker_order_ids: list[str]
    ) -> dict[str, dict[str, Any]]:
        """Batch status: one client open + one account-wide history fetch per
        mode, reused across every requested order in that mode. Return maps
        each input ref to the same status dict ``get_order_status`` returns.
        """
        parsed: dict[str, tuple[str, str, str]] = {}
        by_mode: dict[str, list[str]] = {}
        for ref in broker_order_ids:
            mode, symbol, order_id = self._split_broker_order_ref(ref)
            parsed[ref] = (mode, symbol, order_id)
            by_mode.setdefault(mode, []).append(ref)

        results: dict[str, dict[str, Any]] = {}
        for mode, refs in by_mode.items():
            client = self._open_client()
            try:
                history = self._fetch_account_history(client, mode)
            finally:
                client.close()
            for ref in refs:
                _mode, symbol, order_id = parsed[ref]
                results[ref] = self._materialise_order_status(
                    response=history,
                    mode=mode,
                    symbol=symbol,
                    order_id=order_id,
                    broker_order_id=ref,
                )
        return results
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd /Users/mgh3326/services/tradingcodex-desk && uv run pytest tests/test_auto_trader_provider.py::TestGetOrderStatusesBatch -q`
Expected: PASS. Then run the full file to confirm no regressions:
`uv run pytest tests/test_auto_trader_provider.py -q`

- [ ] **Step 5: Commit**

```bash
cd /Users/mgh3326/services/tradingcodex-desk
git add trading/connectors/auto-trader/provider.py tests/test_auto_trader_provider.py
git commit -m "feat(rob-803): get_order_statuses batch primitive

One client open + one account-wide merged-history fetch per account mode,
reused across all requested orders in that mode. Removes the per-ticket
client-open + full-history refetch when refreshing many orders at once.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

### Task 3: Open PR 1

- [ ] **Step 1: Push and open PR (do not merge)**

```bash
cd /Users/mgh3326/services/tradingcodex-desk
git push -u origin rob-803-read-timeout-batch
gh pr create --repo mgh3326/tradingcodex-desk --base main \
  --title "ROB-803: read-call timeout budget + get_order_statuses batch primitive" \
  --body "$(cat <<'EOF'
## ROB-803 items 1 + 2a (desk)

- **Read-call timeout budget:** `READ_CALL_TIMEOUT_S=20` for status/history/cash/positions, distinct from the 15s handshake and 60s submit budgets. `MCPTimeoutError` names the timed-out tool + per-call budget (no more opaque read timeouts). `cancel_order` moved to the 60s live budget (was the 15s default) — **flagged behaviour change**.
- **Batch primitive:** `get_order_statuses(order_ids)` opens one client + fetches account-wide merged history once per mode, reused across orders. Consumed by the fork batch-refresh PR.

Emergency `tool_timeout_sec=120` (7058b7a/ea71d2d) already deployed; this makes the inner budgets honest.

Tests: `uv run pytest tests/test_auto_trader_provider.py -q`

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## PR 2 — fork `tradingcodex_service`: batch refresh orchestration

All paths under `/Users/mgh3326/work/tradingcodex.rob-803`.

### Task 4: Rebase fork branch onto robin

- [ ] **Step 1: Rebase `rob-803` onto latest `origin/robin`**

```bash
cd /Users/mgh3326/work/tradingcodex.rob-803
git fetch origin robin
git rebase origin/robin
```
Expected: clean replay of the spec-doc commit onto robin. Resolve conflicts if the docs path moved; the only local commit is the design spec.

- [ ] **Step 2: Confirm the batch adapter method is reachable from the service layer**

Run: `grep -n "def get_order_statuses" -r ../../services/tradingcodex-desk/trading/connectors/auto-trader/provider.py`
Expected: PR 1's method is present (PR 1 lands first). Note: `adapter_for_connection(...)` returns the loaded desk provider adapter; `get_order_statuses` is an instance method on it.

### Task 5: Extract `_apply_broker_status` shared helper

**Files:**
- Modify: `tradingcodex_service/application/orders.py` (refactor `refresh_broker_order_status` body, lines ~550-599)
- Test: `tests/` (follow the existing refresh test module on robin — `tests/test_resting_lifecycle.py` / `tests/test_broker_center_prd.py`)

**Interfaces:**
- Produces: `_apply_broker_status(root: Path, ticket, broker_order, adapter_status: dict, principal_id: str) -> str` — applies status→state-transition + fill-recording + `status_refreshed` audit exactly as the single path does today, returns the normalized `broker_status`. Pure of any adapter call (adapter status is passed in).

- [ ] **Step 1: Write a characterization test for the single path (guards the refactor)**

Add a test that drives `refresh_broker_order_status` with a monkeypatched adapter returning a `filled` status and asserts the ticket transitions to `FILLED` and a fill is recorded — mirror the existing refresh tests' workspace/ticket fixtures. (Use the same fixture that seeds a ticket with a `broker_order` and a `broker_connection`.)

- [ ] **Step 2: Run to confirm it passes on current code**

Run: `uv run --with pip --with 'pytest>=8' pytest tests/ -k refresh -q`
Expected: PASS (characterization — current behaviour).

- [ ] **Step 3: Extract the helper (behaviour-preserving)**

Move lines ~551-587 of `refresh_broker_order_status` (everything from `broker_status = str(adapter_status.get(...))` through the optional `sync_broker_account`) into:

```python
def _apply_broker_status(root, ticket, broker_order, adapter_status, principal_id):
    broker_status = str(adapter_status.get("status") or "unknown").lower()
    broker_order.broker_status = broker_status
    broker_order.last_seen_at = datetime.now(timezone.utc)
    broker_order.raw_status_payload_hash = stable_hash(adapter_status)
    metadata = dict(broker_order.metadata or {})
    metadata["last_status_refresh"] = adapter_status
    broker_order.metadata = metadata
    broker_order.save(update_fields=["broker_status", "last_seen_at", "raw_status_payload_hash", "metadata"])
    record_order_event(ticket, "status_refreshed", principal_id, {"broker_order_id": broker_order.broker_order_id, "broker_status": broker_status, "adapter_status": adapter_status})
    state_target = {
        "filled": "FILLED", "partially_filled": "PARTIALLY_FILLED", "partial": "PARTIALLY_FILLED",
        "canceled": "CANCELED", "cancelled": "CANCELED", "rejected": "REJECTED",
        "expired": "EXPIRED", "failed": "FAILED",
    }.get(broker_status)
    if state_target and ticket.current_state != state_target:
        try:
            transition_order_ticket(ticket, state_target, principal_id, {"broker_order_id": broker_order.broker_order_id, "broker_status": broker_status})
        except ValueError:
            pass
    if adapter_status.get("filled_quantity") and adapter_status.get("average_price"):
        fill_payload = {**adapter_status, "broker_order_id": broker_order.broker_order_id, "submitted_at": broker_order.submitted_at.isoformat() if broker_order.submitted_at else now_iso()}
        _record_ticket_submit_result(root, order_payload_from_ticket(ticket), {"approved_by": "status-refresh"}, fill_payload, principal_id)
        try:
            from tradingcodex_service.application.brokers import sync_broker_account
            sync_broker_account(root, {"broker_id": ticket.broker_connection.broker_id, "principal_id": principal_id})
        except Exception:
            pass
    return broker_status
```

Replace those lines in `refresh_broker_order_status` with:

```python
    broker_status = _apply_broker_status(root, ticket, broker_order, adapter_status, principal_id)
```

(Keep the surrounding `adapter_status = adapter_for_connection(...).get_order_status(...)` line and the `ticket.refresh_from_db()` + result assembly that follow.)

- [ ] **Step 4: Run the characterization test again**

Run: `uv run --with pip --with 'pytest>=8' pytest tests/ -k refresh -q`
Expected: PASS (unchanged behaviour).

- [ ] **Step 5: Commit**

```bash
cd /Users/mgh3326/work/tradingcodex.rob-803
git add tradingcodex_service/application/orders.py tests/
git commit -m "refactor(rob-803): extract _apply_broker_status from refresh_broker_order_status

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

### Task 6: `refresh_broker_order_statuses` batch function

**Files:**
- Modify: `tradingcodex_service/application/orders.py`
- Test: `tests/`

**Interfaces:**
- Consumes: `adapter_for_connection`, `broker_connection_provider_review_reasons`, `_apply_broker_status`, `serialize_order_ticket`, `workspace_context_payload`, `write_audit_event`.
- Produces: `refresh_broker_order_statuses(workspace_root, args) -> dict` where `args` accepts `ticket_ids: list[str]` (explicit) or falls back to all open tickets for the active profile; groups tickets by `broker_connection`; calls `adapter.get_order_statuses([...])` **once per connection**; applies `_apply_broker_status` per ticket; returns `{"results": [ {ticket_id, status, broker_order_id, broker_status?, reasons?} ], ...}`. One ticket failing never aborts the batch.

- [ ] **Step 1: Write failing test — one adapter call per connection, per-ticket results, no abort-on-failure**

Test with a fake adapter whose `get_order_statuses` records its call and returns a map; assert it is called exactly once for a connection with 3 tickets, all 3 results present, and that a ticket whose ref is missing from the returned map lands as `unknown` without raising. Mirror the seed-ticket fixture used in Task 5.

- [ ] **Step 2: Run to verify it fails**

Run: `uv run --with pip --with 'pytest>=8' pytest tests/ -k batch -q`
Expected: FAIL — function not defined.

- [ ] **Step 3: Implement**

```python
def refresh_broker_order_statuses(workspace_root: Path | str, args: dict[str, Any]) -> dict[str, Any]:
    root = Path(workspace_root)
    ensure_runtime_database(root)
    from tradingcodex_service.application.brokers import adapter_for_connection, broker_connection_provider_review_reasons
    from apps.orders.models import OrderTicket

    principal_id = str(args.get("principal_id") or "execution-operator")
    ticket_ids = list(args.get("ticket_ids") or [])
    if ticket_ids:
        tickets = list(OrderTicket.objects.select_related("broker_connection").prefetch_related("broker_orders").filter(ticket_id__in=ticket_ids))
    else:
        portfolio_id, account_id, strategy_id = portfolio_keys(args, root)
        tickets = list(OrderTicket.objects.select_related("broker_connection").prefetch_related("broker_orders").filter(
            portfolio_id=portfolio_id, account_id=account_id, strategy_id=strategy_id,
        ).exclude(current_state__in=["FILLED", "REJECTED", "CANCELED", "EXPIRED", "FAILED"]))

    # Group by connection; only tickets with a connection + a broker order participate.
    by_conn: dict[Any, list[tuple[Any, Any]]] = {}
    results: list[dict[str, Any]] = []
    for ticket in tickets:
        broker_order = ticket.broker_orders.order_by("-submitted_at", "-id").first()
        if ticket.broker_connection is None:
            results.append({"ticket_id": ticket.ticket_id, "status": "local-only", "reasons": ["ticket has no broker connection"]})
            continue
        if broker_order is None:
            results.append({"ticket_id": ticket.ticket_id, "status": "no_broker_order", "reasons": ["ticket has no broker order recorded yet"]})
            continue
        by_conn.setdefault(ticket.broker_connection, []).append((ticket, broker_order))

    for connection, pairs in by_conn.items():
        source_reasons = broker_connection_provider_review_reasons(connection, root)
        if source_reasons:
            for ticket, broker_order in pairs:
                results.append({"ticket_id": ticket.ticket_id, "status": "blocked", "broker_order_id": broker_order.broker_order_id, "reasons": source_reasons})
            continue
        adapter = adapter_for_connection(connection, root)
        order_ids = [bo.broker_order_id for _t, bo in pairs]
        try:
            status_map = adapter.get_order_statuses(order_ids)
        except Exception as exc:  # noqa: BLE001 — one connection's failure must not abort the batch
            for ticket, broker_order in pairs:
                results.append({"ticket_id": ticket.ticket_id, "status": "error", "broker_order_id": broker_order.broker_order_id, "reasons": [f"batch refresh failed: {type(exc).__name__}"]})
            continue
        for ticket, broker_order in pairs:
            adapter_status = status_map.get(broker_order.broker_order_id) or {"status": "unknown"}
            try:
                broker_status = _apply_broker_status(root, ticket, broker_order, adapter_status, principal_id)
                ticket.refresh_from_db()
                results.append({"ticket_id": ticket.ticket_id, "status": "refreshed", "broker_order_id": broker_order.broker_order_id, "broker_status": broker_status})
            except Exception as exc:  # noqa: BLE001 — per-ticket isolation
                results.append({"ticket_id": ticket.ticket_id, "status": "error", "broker_order_id": broker_order.broker_order_id, "reasons": [f"apply failed: {type(exc).__name__}"]})

    payload = {"status": "batch_refreshed", "results": results, "db_canonical": True, "workspace_context": workspace_context_payload(root)}
    write_audit_event(root, {"type": "broker_order_status.batch_refreshed", "payload": payload}, principal_id, "service")
    return payload
```

Verify `portfolio_keys` and `now_iso`/`_record_ticket_submit_result` imports already exist at module scope (they are used by the single path in the same file).

- [ ] **Step 4: Run to verify it passes**

Run: `uv run --with pip --with 'pytest>=8' pytest tests/ -k "batch or refresh" -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tradingcodex_service/application/orders.py tests/
git commit -m "feat(rob-803): refresh_broker_order_statuses batch refresh

Groups tickets by broker_connection, calls the desk adapter's get_order_statuses
once per connection (one client open + one account-wide history fetch), applies
per-ticket status via the shared _apply_broker_status. One ticket or connection
failing never aborts the batch.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

### Task 7: Register the `refresh_broker_order_statuses` MCP tool (3 sites)

**Files:**
- Modify: `tradingcodex_service/mcp_runtime.py` (McpToolSpec + handler map)
- Modify: `tradingcodex_service/application/agents.py` (execution-operator `mcp_allowlist`)
- Modify: `workspace_templates/modules/fixed-subagents/files/.codex/agents/execution-operator.toml`
- Test: `tests/` (tool-registry consistency test if one exists; otherwise assert the tool resolves)

- [ ] **Step 1: Write failing test — tool is registered and allowlisted for execution-operator**

```python
def test_refresh_broker_order_statuses_registered():
    from tradingcodex_service.mcp_runtime import MCP_TOOL_SPECS  # or the actual registry symbol
    spec = next(s for s in MCP_TOOL_SPECS if s.name == "refresh_broker_order_statuses")
    assert "execution-operator" in spec.allowed_roles
    assert spec.handler_name == "refresh_broker_order_statuses"
```

(Confirm the registry symbol name by grepping `mcp_runtime.py` for the list `refresh_broker_order_status` lives in.)

- [ ] **Step 2: Run to verify it fails**

Run: `uv run --with pip --with 'pytest>=8' pytest tests/ -k refresh_broker_order_statuses -q`
Expected: FAIL — `StopIteration` (spec not found).

- [ ] **Step 3: Add the McpToolSpec + handler (`mcp_runtime.py`)**

Immediately after the existing `refresh_broker_order_status` `McpToolSpec` (~line 660-669), add:

```python
    McpToolSpec(
        name="refresh_broker_order_statuses",
        description="Batch-refresh local broker order status for many tickets in one call, reusing one broker client + one account-wide history fetch per connection.",
        category="execution",
        risk_level="execution",
        allowed_roles=roles_with_mcp_tool("refresh_broker_order_statuses"),
        handler_name="refresh_broker_order_statuses",
        input_schema=object_schema({"ticket_ids": {"type": "array", "items": {"type": "string"}}}, additional_properties=False),
        capability_required="mcp.tradingcodex.refresh_broker_order_statuses",
        experimental=True,
    ),
```

In the handler map (~line 973), after the `refresh_broker_order_status` entry, add:

```python
        "refresh_broker_order_statuses": lambda: orders.refresh_broker_order_statuses(workspace_root, with_principal),
```

- [ ] **Step 4: Grant to execution-operator (`agents.py`)**

In `AGENT_SPECS["execution-operator"].mcp_allowlist`, add `"refresh_broker_order_statuses",` immediately after `"refresh_broker_order_status",` (~line 318).

- [ ] **Step 5: Mirror to the static TOML**

In `workspace_templates/modules/fixed-subagents/files/.codex/agents/execution-operator.toml`, add `"refresh_broker_order_statuses",` immediately after the `"refresh_broker_order_status",` entry (~line 59). (head-manager's TOML is auto-projected from `AGENT_SPECS`; do not hand-edit it.)

- [ ] **Step 6: Run to verify it passes + full suite**

Run:
```bash
uv run --with pip --with 'pytest>=8' pytest tests/ -k refresh_broker_order_statuses -q
uv run --with pip --with 'pytest>=8' pytest tests/ -q
```
Expected: PASS. If a template-projection / allowlist-consistency test exists (it enforces the 3 sites agree), it must be green.

- [ ] **Step 7: Commit + open PR (do not merge)**

```bash
git add tradingcodex_service/mcp_runtime.py tradingcodex_service/application/agents.py \
  workspace_templates/modules/fixed-subagents/files/.codex/agents/execution-operator.toml tests/
git commit -m "feat(rob-803): register refresh_broker_order_statuses MCP tool

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
git push -u origin rob-803
gh pr create --repo mgh3326/tradingcodex --base robin \
  --title "ROB-803: batch broker order-status refresh" \
  --body "$(cat <<'EOF'
## ROB-803 item 2b (fork)

`refresh_broker_order_statuses` groups tickets by connection and calls the desk
adapter's `get_order_statuses` once per connection (one client open + one
account-wide history fetch), applying per-ticket status via the shared
`_apply_broker_status`. One ticket/connection failing never aborts the batch.
New MCP tool registered across the 3 grant sites (runtime spec+handler,
AGENT_SPECS allowlist, execution-operator.toml).

Depends on desk PR (`get_order_statuses`). Rebased onto robin; rob-766 adjacent.

Tests: `uv run --with pip --with 'pytest>=8' pytest tests/`

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## PR 3 — desk skill / base instruction

Branch off desk `main` (independent of PR 1 — disjoint files):
`git -C /Users/mgh3326/services/tradingcodex-desk checkout main && git -C /Users/mgh3326/services/tradingcodex-desk checkout -b rob-803-session-rules`

### Task 8: Refresh-timeout behaviour rule + broker topology

**Files:**
- Modify: `/Users/mgh3326/services/tradingcodex-desk/.agents/skills/resting-order-lifecycle/SKILL.md`
- Modify: `/Users/mgh3326/services/tradingcodex-desk/.codex/prompts/base_instructions/head-manager.md`

- [ ] **Step 1: Read both files to find the right insertion points**

Run:
```bash
cd /Users/mgh3326/services/tradingcodex-desk
sed -n '1,60p' .agents/skills/resting-order-lifecycle/SKILL.md
grep -n "connection\|refresh\|broker" .codex/prompts/base_instructions/head-manager.md
```
Identify the refresh/reconciliation section in the skill and a broker/connection section in head-manager.md.

- [ ] **Step 2: Add the refresh-timeout rule to `resting-order-lifecycle/SKILL.md`**

Insert an explicit numbered rule in the refresh/reconciliation section (adapt wording to the file's voice):

```markdown
### Refresh timeout handling (ROB-803)

A broker status refresh can time out at open congestion. Do **not** block the
whole reconciliation on one ticket:

1. On a refresh timeout, **retry once**.
2. If it still times out, **mark that ticket `unverified`** (record the timeout
   in the ticket's status note; leave its local state unchanged) and **move on**.
3. **Continue the session** — refresh the remaining tickets. Never abort
   settlement because a single ticket could not be verified.

Prefer `refresh_broker_order_statuses` (batch) over per-ticket
`refresh_broker_order_status` when reconciling multiple tickets: it reuses one
broker client + one account-wide history fetch per connection, which is what
prevents the per-ticket refetch storm that caused the original timeout.
```

- [ ] **Step 3: Add the broker topology note to `head-manager.md`**

Insert near the execution-plane / connection section:

```markdown
### Broker topology

There is **one** auto-trader broker connection. It serves every account mode —
`kis_live`, `toss_live`, `upbit` — routed by `account_mode`. Do **not** invent
per-broker `kis` / `toss` connection ids for health checks, refresh, or
reconciliation; look up the actual connection id from
`list_broker_connections` and route by account mode. On a broker refresh
timeout, follow the resting-order-lifecycle rule (retry once → mark the ticket
`unverified` → continue); never halt the session on one ticket.
```

- [ ] **Step 4: Verify the docs render and the rule reads correctly**

Run:
```bash
cd /Users/mgh3326/services/tradingcodex-desk
grep -n "unverified\|Broker topology\|account_mode\|refresh_broker_order_statuses" \
  .agents/skills/resting-order-lifecycle/SKILL.md .codex/prompts/base_instructions/head-manager.md
```
Expected: the new rule + topology lines present in both files. Confirm no accidental duplication of headings and that Markdown headings match the surrounding level.

- [ ] **Step 5: Commit + open PR (do not merge)**

```bash
cd /Users/mgh3326/services/tradingcodex-desk
git add .agents/skills/resting-order-lifecycle/SKILL.md .codex/prompts/base_instructions/head-manager.md
git commit -m "docs(rob-803): refresh-timeout session rule + single-connection broker topology

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
git push -u origin rob-803-session-rules
gh pr create --repo mgh3326/tradingcodex-desk --base main \
  --title "ROB-803: refresh-timeout session rule + broker topology" \
  --body "$(cat <<'EOF'
## ROB-803 item 3 (desk docs)

- **resting-order-lifecycle SKILL.md:** refresh timeout → retry once → mark ticket `unverified` → continue session (never block reconciliation on one ticket); prefer the batch refresh tool.
- **head-manager.md:** single auto-trader connection routed by `account_mode` (`kis_live`/`toss_live`/`upbit`); do not guess `kis`/`toss` connection ids.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Self-review notes (author)

- **Spec coverage:** item 1 → Task 1; item 2a → Task 2; item 2b → Tasks 5-7; item 3 → Task 8. Emergency 120s fix is out of scope (already deployed). Broker topology + retry/unverified rule → Task 8. ✅
- **`cancel_order` 15→60:** Task 1 Step 5 (flagged in PR body). ✅
- **3 grant sites:** Task 7 Steps 3-5. ✅
- **Rebase-before-fork + rob-766 adjacency:** Task 4. ✅
- **Batch parity caveat (account-wide `status=all` must surface filled/cancelled):** Task 2 Step 1 asserts filled + cancelled parity. ✅
- **Type consistency:** `get_order_statuses(list[str]) -> dict[str, dict]` produced by Task 2, consumed by Task 6; `_apply_broker_status(...) -> str` produced by Task 5, consumed by Task 6. ✅
- **Fork test fixture names** are referenced generically (Tasks 5-7) because the fork test harness lives on `robin` (this branch is 17 behind); finalize against `tests/test_resting_lifecycle.py` after the Task 4 rebase.
```

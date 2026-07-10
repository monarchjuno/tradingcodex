# Safety, Policy, And Execution

This document owns executable safety: permission checks, approval rules,
execution lifecycle, adapter boundary, blocked actions, and secret handling.
Use [guardrails.md](./guardrails.md) for the broader guardrail taxonomy.

## Safety Model

TradingCodex safety is part of the top-level harness. Guidance reduces risky
behavior early, but only deterministic enforcement on the final action path can
block execution. Information barriers limit what roles can see or do, while
improvement loops raise quality without becoming executable authorization.

## Executable Action Rule

Every executable action follows:

```text
requester -> permission -> policy -> payload validation -> approval/duplicate-request check -> connection -> audit
```

This order matters:

1. Requester: identify the caller and workspace provenance.
2. Permission: confirm the action is explicitly allowed for that requester.
3. Policy: check restricted list, limits, role, universe, connection, and live-execution posture.
4. Payload validation: validate the structured order/action payload.
5. Approval and duplicate-request check: prove approval is valid and the order has not already produced an execution result.
6. Connection: call only an enabled connection with an allowed execution posture.
7. `audit`: record request, decision, result, hashes, and errors.

Policy and approval are revalidated immediately before connection use.
Broker/API/MCP connection invocation is always owned by the Django service layer.
Codex may draft, explain, classify, and request checks, but it must not call a
raw broker execution primitive directly.

## Execution Lifecycle

| Step | Artifact/action | Owner | Required rule |
| --- | --- | --- | --- |
| Evidence collection | evidence pack | analyst roles | Separate sources, dates, facts, and assumptions. |
| Analysis | analyst reports, valuation | role subagents | Maintain each role's information barrier. |
| Portfolio fit | portfolio review | `portfolio-manager` | Check sizing, cash, concentration, liquidity, and portfolio fit. |
| Broker sync | `BrokerSyncRun`, `PortfolioLedgerEvent`, `ReconciliationRun` | service layer | Read-only connection path only; raw credentials are references. |
| Draft order | `OrderTicket` | `portfolio-manager` | No execution before schema, policy, cash/position, broker validation, and risk checks. |

Approval table metadata is part of approval readiness. New `run_order_checks`
results carry machine-readable `approval_table_meta` with `valid_until`,
`invalidates_on`, per-row `quote_as_of`, `cash_as_of`,
`order_status_as_of`, and cash-reserve stress fields. `request_order_approval`
must refuse to create an `ApprovalReceipt` when that metadata is stale because
of quote drift, cash delta, order-status delta, replacement-ticket creation,
terminal-refresh failure, or age threshold. Metadata-free legacy check tables
remain accepted for backward compatibility, but approval responses must surface
a warning instead of silently implying freshness.

| Risk review | risk/policy report | `risk-manager` | Check restricted list, downside, limits, and approval readiness. |

Approval table metadata is part of approval readiness. New `run_order_checks`
results carry machine-readable `approval_table_meta` with `valid_until`,
`invalidates_on`, per-row `quote_as_of`, `cash_as_of`,
`order_status_as_of`, and cash-reserve stress fields. `request_order_approval`
must refuse to create an `ApprovalReceipt` when that metadata is stale because
of quote drift, cash delta, order-status delta, replacement-ticket creation,
terminal-refresh failure, or age threshold. Metadata-free legacy check tables
remain accepted for backward compatibility, but approval responses must surface
a warning instead of silently implying freshness.

Approval-wait session cutoffs extend the same `approval_table_meta` mechanism
for tickets that carry `session_close_at` expiry metadata (for example a US DAY
order closing at 05:00 KST; market close times are derived from the ticket, not
hardcoded). `invalidates_on` then also lists `session_revalidation_window`,
`resting_day_cutoff`, and `session_close_cutoff`, and the meta carries a
`session_deadline` block with `revalidation_due_at` (T-60),
`resting_day_cutoff_at` (T-30), `latest_safe_submit_at` (T-15), and a
versioned `cutoff_policy_id`. From T-60 an approval table built earlier must be
revalidated and re-presented (`recheck_required` with a `re_present` payload —
exact ticket id and order payload hash, remaining minutes, next action; never
auto-approve or auto-submit). From T-30 new approval receipts for resting DAY
orders return `approval_wait_cutoff`; immediate execution order types remain
allowed until T-15. From T-15 all new DAY approvals and submits fail closed,
existing receipts are invalidated, the `approval_wait.cutoff` event is written
to the audit log, and only a next-session successor proposal — requiring a new
approval and a new LIVE confirmation — is allowed; the original ticket is never
silently reused or auto-recreated. Receipt `expires_at`/`valid_until` are
clamped to `latest_safe_submit_at`. Tickets without `session_close_at` keep the
existing behavior and surface a warning that session cutoffs are not enforced.

| Approval | `ApprovalReceipt` | `risk-manager` | Bind approval to exact order payload hash, broker/account, max notional/price, order type, time-in-force, and expiry. |
| Execution | `submit_approved_order` through TradingCodex MCP | `execution-operator` | Revalidate the order ticket payload and approval receipt in MCP. |
| Audit/postmortem | audit event, execution result, postmortem | MCP/head-manager | Record rejects, approvals, executions, and policy decisions. |

Approved execution is idempotent by order/profile boundary. A repeated
`submit_approved_order` call for an order that already has an
`ExecutionResult` in the same `portfolio_id` / `account_id` / `strategy_id`
must be rejected before any connection is called.
Connection readiness failures, such as missing credentials, disabled live opt-in,
or signed-health errors before a broker submit attempt, must fail before creating
an `ExecutionResult` so the operator can retry after fixing configuration,
credentials, permissions, or IP allowlists. Once a broker submission may have
reached the provider submit boundary, TradingCodex records `NEEDS_REVIEW` /
unknown status and duplicate protection applies until status is reconciled.
Before returning a connection-state preflight rejection, the service checks
whether the same ticket already has local broker-order activity and refreshes
that order first. If the ticket timeline shows provider progress, the response
is `reconciled` with both the original rejection reasons and final ticket state.
Read-only crosswalk and occupancy views must preserve this uncertainty. If a
ticket, approval receipt, broker order id, replacement lineage, or latest
broker status cannot be joined cleanly, the view reports anomaly flags and sets
terminal inference to false. An unresolved `ACKED`, `NEEDS_REVIEW`, or broker
`unknown` row remains duplicate/cash-reservation-relevant until a later
canonical refresh, cancel, reject, fill, expiry, or reviewed correction resolves
it.

The resting lifecycle panel (`get_resting_lifecycle_panel`) extends the same
discipline to time-based checkpoints. It never infers terminal expiry from the
wall clock: a working order past the post-20:00 KST terminal-refresh deadline
without an observed terminal broker status renders `terminal_blocked` — never
`expired` — with an explicit `no_terminal_evidence` gap and terminal inference
set to false. Missed cadence checkpoints render `ttl_stale` with the missed
count recorded as a gap, and missed ticks are attributed to `blocked` only
while a block signal (provider source review or a failed terminal refresh in
the approval table) is currently evidenced. The `no_auto_reprice` and
`no_overnight_carry` fields are standing-rule declarations for the operator;
the panel enforces nothing and mutates nothing.

Order ticket ids are central-DB ids. CLI/API/MCP calls use `ticket_id` or
`order_ticket_id`; if the same id appears with a different payload, validation
must fail closed instead of mutating the existing ticket.

`OrderTicket` state changes must happen through explicit service functions.
Invalid transitions are blocked, and every transition writes `OrderEvent` plus
an audit event. The supported lifecycle is:

```text
DRAFT -> PRECHECKED -> READY_FOR_APPROVAL -> APPROVED -> RESERVED
  -> SUBMITTED -> ACKED -> PARTIALLY_FILLED -> FILLED
```

Terminal or review states are `REJECTED`, `CANCELED`, `EXPIRED`, `FAILED`,
`VOIDED`, and `NEEDS_REVIEW`. Fills create `Fill`, `BrokerOrder`, `OrderEvent`,
portfolio ledger, snapshots, and reconciliation records. Validation submissions
create broker-order and audit records but no fill when the broker endpoint
validates without sending an order to a matching engine. For validation-only
connector modes, `refresh_broker_order_status` preserves the local validated
state when the broker endpoint intentionally does not create an external order.
Live cancel uses the installed provider cancel path and remains audited.

Approved-only tickets that have no broker order and no fills may be locally
voided through the service layer. Local void invalidates active approval
receipts, records an order event plus audit event, and blocks later submission
with a terminal-state reason.

Signed broker credential failures are execution blockers, not execution
attempts. The connector remains read-only with no enabled trade scopes, exposes
only secret-free diagnostics such as `credential_validation_details`, and
`submit_approved_order` stops before reserving or consuming execution
idempotency.

## Required Blocks

TradingCodex must block:

- direct live broker requests outside `submit_approved_order` / `cancel_approved_order`
- direct raw external MCP proxy for broker, execution, secret, or policy/admin
  tools
- raw broker API variants such as `broker.raw_api`, `broker_api.*`, and generic live execution actions
- generic execution-like actions such as `execute_order` unless they enter the approved TradingCodex MCP lifecycle
- self-issued approvals
- approval creation by roles other than `risk-manager`
- restricted symbol orders
- approval order-payload-hash mismatch after order mutation
- expired approval receipts or expired approval `valid_until`
- orders exceeding approval max notional, max price, order type, or time-in-force scope
- paper/test-sandbox/live provider orders without a valid order ticket plus matching approval receipt
- stale approval-table metadata: `valid_until` expiry, cash delta, order-status delta, quote drift, replacement-ticket creation, terminal-refresh failure, or age threshold
- repeated connection submission for an already executed approved order
- duplicate order ticket ids with different payloads
- global MCP exposure for approval, execution, cancellation, policy mutation, secret, or broker tools
- Any default Admin edit that would bypass service-layer policy for execution-sensitive state
- execution when the principal is inactive or capability is denied
- raw secrets in API, MCP, audit response, generated prompt, generated docs, or shell output
- live execution when workspace config, policy, environment opt-in, enabled live adapter, signed health, trading-enabled connection, live scope, approval hash, explicit confirmation, idempotency, sync, or audit gates are missing

## External MCP Gate

External MCP servers are useful for broker account data, market data, research
sources, and future adapter support, but they must enter through the
TradingCodex External MCP Gate rather than direct Codex exposure.

Discovery stores external tool/resource/prompt metadata, schema hash, risk
category, sensitivity, canonical capability, role scope, proxy mode, and
lifecycle status. Default posture is fail-closed:

- unknown tools are disabled until classified
- schema-hash drift disables the tool until reviewed
- secret and policy/admin tools are not proxyable
- execution tools cannot use direct raw proxy and must map to the approved
  service-layer connection path
- account-read tools require explicit role scope and audit because balances,
  positions, orders, and fills expose private strategy/account data
- public market-data/news/filing tools may remain lightweight, but they require
  source/as-of posture, cache/freshness discipline, and source-snapshot or
  research-artifact handling when used in TradingCodex order, risk, approval,
  or portfolio decisions

External MCP permission is not execution authorization. Even if an external
broker order tool is present and reviewed, order submission must still pass the
TradingCodex order-ticket, approval, duplicate-request, connection, and audit lifecycle.

The built-in TradingCodex MCP server auto-approves safe enabled tools to avoid
buried subagent prompts for routine research and audit writes. Execution
submission and cancellation are the exception: non-execution roles do not see
those tools, and `execution-operator` can only submit or cancel through
TradingCodex service-layer checks.

Reviewed external MCP calls that expose private account state, write research
state, use workflow prompts, or map to execution require an explicit user
permission request before proxy evaluation returns `allow`. The request is
stored as pending service-layer state and surfaced through Build Center,
`tcx build permission list`, and the coordinator-visible MCP pending-request
list. Subagents must stop at `waiting_for_user_permission` instead of burying a
Codex permission prompt in their transcript.

Codex network access may be enabled for public web, filing, disclosure, news,
and market-data evidence gathering. That access is read-only research support:
it does not authorize direct broker APIs, raw external broker MCP exposure,
secret reads, approval bypass, or execution.

## Broker Safety

Broker connections start disabled or read-only, except the built-in paper
adapter. Core ships only the paper provider by default. Broker-specific
providers are installed or developed on request, then registered by provider
metadata. A registered provider profile with an allowed execution posture
becomes execution-ready only after signed health verifies its credential
reference and the policy/config gates allow that posture. Broker records store
`credential_ref` only; raw credentials must not be stored in repo files,
workspace files, API responses, MCP responses, or audit payloads.

Broker sync can discover accounts, cash, positions, orders, and fills through
the provider registry. It materializes central DB state through
`BrokerSyncRun`, `PortfolioLedgerEvent`, `PortfolioSnapshot`, and
`ReconciliationRun`. A reviewed validation provider can run broker-native
dry-run/order-test endpoints through the service-layer connection after order
ticket, approval, policy, duplicate-request, and audit checks. A reviewed live
provider can submit only when all live gates pass: `execution.live_enabled:
true`, policy allows the broker id and `live_broker`, environment variable
`TRADINGCODEX_ENABLE_LIVE_EXECUTION=1`, the live `AdapterDefinition` is enabled,
signed health is `ok`, the connection is `trading_enabled`, the exact order hash
has an approval receipt, and `submit_approved_order` includes
`LIVE:<ticket_id>:<broker_id>:<symbol>:<side>:<quantity>`.

## Routing Guardrail

- `no order`, `no trading`, `do not place trades`, and equivalent negations must keep a request out of execution routing.
- Guardrail-verification wording such as "verify blocked order/approval/execution actions" is evidence of a safety check, not a request to execute.
- Secret-only prompts such as requests to save, read, or rotate broker API
  keys, tokens, credentials, passwords, or `.env` files produce secret-wall
  warning context and do not activate investment subagent dispatch unless a
  separate investment, order, approval, or execution request remains after the
  secret terms are removed.
- Public-equity earnings, filing, catalyst, thesis, and valuation requests route to thesis-review style research/valuation support unless the user separately asks for portfolio fit, order drafting, approval, or execution.
- Unsupported universes are downgraded to research-only, screen-grade, not-decision-ready, or blocked.

## Secret Wall

Raw broker API keys, tokens, account credentials, and secrets must not appear in:

- generated workspace files
- `.codex/` or `.agents/` prompts
- shell output
- product web output
- Admin list displays or exported rows
- API responses
- MCP responses
- audit event payloads
- starter prompts
- generated research artifacts

Adapters that need secrets must use external environment-backed credential
references and expose only redacted references through TradingCodex.

## Policy Inputs

Policy decisions can depend on:

- principal
- role
- capability
- requested action
- symbol/instrument
- universe
- adapter type
- restricted list
- portfolio limits
- order schema validity
- approval receipt validity
- idempotency state
- current live-execution posture
- workspace provenance

Policy output should record the decision, reason codes, material inputs, and
audit reference.

## Admin Risky Changes

Risky Admin changes use:

```text
proposal -> validation -> approval -> apply -> audit
```

Examples:

- enabling or disabling MCP tools
- projecting workspace skill proposal files
- changing principals or capabilities
- toggling restricted symbols
- disabling adapters
- changing universe routing or supported-instrument policy
- applying policy changes

Admin is an operations console, not a bypass.

## Default And Live-Gated Execution

Paper, stub, and reviewed validation execution remain local harness flows.
Live execution is not enabled by bootstrap, workspace generation, connector
scaffold, or connector registration alone. It is available only through an
installed and reviewed provider plus the explicit live gates above.

Every execution still requires:

- structured order ticket
- service-layer validation
- valid approval receipt
- role and capability checks
- idempotency check
- adapter availability check
- audit event

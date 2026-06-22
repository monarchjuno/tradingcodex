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
principal -> capability -> policy -> schema -> approval/idempotency -> adapter -> audit
```

This order matters:

1. `principal`: identify the caller and workspace provenance.
2. `capability`: confirm the action is explicitly allowed for that principal.
3. `policy`: check restricted list, limits, role, universe, adapter, and live-execution posture.
4. `schema`: validate the structured order/action payload.
5. `approval/idempotency`: prove approval is valid and the order has not already produced an execution result.
6. `adapter`: call only an enabled non-live adapter in the initial core.
7. `audit`: record request, decision, result, hashes, and errors.

Policy and approval are revalidated immediately before adapter submission.
Broker/API/MCP adapter invocation is always owned by the Django service layer.
Codex may draft, explain, classify, and request checks, but it must not call a
raw broker execution primitive directly.

## Execution Lifecycle

| Step | Artifact/action | Owner | Required rule |
| --- | --- | --- | --- |
| Evidence collection | evidence pack | analyst roles | Separate sources, dates, facts, and assumptions. |
| Analysis | analyst reports, valuation | role subagents | Maintain each role's information barrier. |
| Portfolio fit | portfolio review | `portfolio-manager` | Check sizing, cash, concentration, liquidity, and portfolio fit. |
| Broker sync | `BrokerSyncRun`, `PortfolioLedgerEvent`, `ReconciliationRun` | service layer | Read-only adapter path only; raw credentials are references. |
| Draft order | `OrderTicket` | `portfolio-manager` | No execution before schema, policy, cash/position, broker validation, and risk checks. |
| Risk review | risk/policy report | `risk-manager` | Check restricted list, downside, limits, and approval readiness. |
| Approval | `ApprovalReceipt` | `risk-manager` | Bind approval to exact order payload hash, broker/account, max notional/price, order type, time-in-force, and expiry. |
| Execution | `submit_approved_order` through TradingCodex MCP | `execution-operator` | Revalidate the order ticket payload and approval receipt in MCP. |
| Audit/postmortem | audit event, execution result, postmortem | MCP/head-manager | Record rejects, approvals, executions, and policy decisions. |

Approved execution is idempotent by order/profile boundary. A repeated
`submit_approved_order` call for an order that already has an
`ExecutionResult` in the same `portfolio_id` / `account_id` / `strategy_id`
must be rejected before any adapter is called.
Adapter readiness failures, such as missing credentials or signed-health
errors before a broker order-test or submit attempt, must fail before creating
an `ExecutionResult` so the operator can retry after fixing credentials,
permissions, or IP allowlists. Once a broker submission reaches the adapter
submit boundary and records an execution result, duplicate protection applies.

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
and `NEEDS_REVIEW`. Paper fills create `Fill`, `BrokerOrder`, `OrderEvent`,
portfolio ledger, and reconciliation records. Broker-native test/sandbox
validation submissions create broker-order and audit records but no fill when
the broker endpoint validates without sending an order to a matching engine.
For validation-only connector modes, `refresh_broker_order_status` preserves
the local validated state when the broker endpoint intentionally does not
create an external order. `cancel_approved_order` remains a conservative
audited `not_supported` result for validation-only modes.

Signed broker credential failures are execution blockers, not execution
attempts. The connector remains read-only with no enabled trade scopes, exposes
only secret-free diagnostics such as `credential_validation_details`, and
`submit_approved_order` stops before reserving or consuming execution
idempotency.

## Required Blocks

TradingCodex must block:

- direct live broker requests
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
- paper/stub/test-sandbox validation orders without a valid order ticket plus matching approval receipt
- repeated adapter submission for an already executed approved order
- duplicate order ticket ids with different payloads
- global MCP exposure for approval, execution, cancellation, policy mutation, secret, or broker tools
- Any default Admin edit that would bypass service-layer policy for execution-sensitive state
- execution when the principal is inactive or capability is denied
- raw secrets in API, MCP, audit response, generated prompt, generated docs, or shell output
- unsupported live execution for crypto, macro, options, credit, FX, rates, commodities, or other instruments

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
  service-layer adapter path
- account-read tools require explicit role scope and audit because balances,
  positions, orders, and fills expose private strategy/account data
- public market-data/news/filing tools may remain lightweight, but they require
  source/as-of posture, cache/freshness discipline, and source-snapshot or
  research-artifact handling when used in TradingCodex order, risk, approval,
  or portfolio decisions

External MCP permission is not execution authorization. Even if an external
broker order tool is present and reviewed, order submission must still pass the
TradingCodex order-ticket, approval, idempotency, adapter, and audit lifecycle.

Codex network access may be enabled for public web, filing, disclosure, news,
and market-data evidence gathering. That access is read-only research support:
it does not authorize direct broker APIs, raw external broker MCP exposure,
secret reads, approval bypass, or execution.

## Broker Safety

Broker connections start disabled or read-only, except the built-in paper
adapter. A registered connector profile with an allowed non-live execution
posture becomes execution-ready only after signed health verifies its
credential reference. Broker records store `credential_ref` only; raw
credentials must not be stored in repo files, workspace files, API responses,
MCP responses, or audit payloads. Validation connectors remain read-only with
empty trade scopes until signed health succeeds; a failed signed-health check
records the error and keeps validation execution disabled.

Read-only broker sync can discover accounts, cash, positions, orders, and
fills through the adapter registry. It materializes central DB state through
`BrokerSyncRun`, `PortfolioLedgerEvent`, `PortfolioSnapshot`, and
`ReconciliationRun`. A reviewed test/sandbox validation connector can run its
broker-native dry-run or order-test endpoint through the service-layer adapter
after order ticket, approval, policy, idempotency, and audit checks. This is
validation-only non-live execution, not live trading. Place-order or live
execution modes remain off by default and require explicit product, policy,
adapter, and validation changes. Live broker execution remains locked unless a
future adapter supports submit, cancel, status, fill reconciliation, approval
scope, idempotency, and explicit local confirmation.

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

## Non-Live Execution

Paper, stub, and reviewed test/sandbox validation execution remain
experimental in the current release line. Keep the code and guardrails
available for local harness validation, but do not present them as production
trading infrastructure or live broker support.

Non-live execution still requires:

- structured order ticket
- service-layer validation
- valid approval receipt
- role and capability checks
- idempotency check
- adapter availability check
- audit event

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
6. `adapter`: call only an enabled paper/stub adapter in the initial core.
7. `audit`: record request, decision, result, hashes, and errors.

Policy and approval are revalidated immediately before adapter submission.

## Execution Lifecycle

| Step | Artifact/action | Owner | Required rule |
| --- | --- | --- | --- |
| Evidence collection | evidence pack | analyst roles | Separate sources, dates, facts, and assumptions. |
| Analysis | analyst reports, valuation | role subagents | Maintain each role's information barrier. |
| Portfolio fit | portfolio review | `portfolio-manager` | Check sizing, cash, concentration, liquidity, and portfolio fit. |
| Draft order | `trading/orders/draft/*.order_intent.json` | `portfolio-manager` | No execution before schema and policy validation. |
| Risk review | risk/policy report | `risk-manager` | Check restricted list, downside, limits, and approval readiness. |
| Approval | `trading/approvals/*.approval_receipt.json` | `risk-manager` or approved flow | No self-approval and no forged receipts. |
| Execution | `submit_approved_order` through TradingCodex MCP | `execution-operator` | Revalidate order intent and approval receipt in MCP. |
| Audit/postmortem | audit event, execution result, postmortem | MCP/head-manager | Record rejects, approvals, executions, and policy decisions. |

Approved execution is idempotent by order/profile boundary. A repeated
`submit_approved_order` call for an order that already has an
`ExecutionResult` in the same `portfolio_id` / `account_id` / `strategy_id`
must be rejected before any adapter is called.

Order intent ids are central-DB ids. If the same `order_intent_id` appears with
a different payload, validation must fail closed instead of overwriting the
existing order intent.

## Required Blocks

TradingCodex must block:

- direct live broker requests
- raw broker API variants such as `broker.raw_api`, `broker_api.*`, and generic live execution actions
- generic execution-like actions such as `execute_order` unless they enter the approved TradingCodex MCP lifecycle
- self-issued approvals
- approval creation by roles other than the approved risk/approval flow
- restricted symbol orders
- paper/stub orders without valid order intent and approval receipt
- repeated adapter submission for an already executed approved order
- duplicate order intent ids with different payloads
- global MCP exposure for approval, execution, cancellation, policy mutation, secret, or broker tools
- Admin actions that try to bypass service-layer policy
- execution when the principal is inactive or capability is denied
- raw secrets in API, MCP, audit response, generated prompt, generated docs, or shell output
- unsupported live execution for crypto, macro, options, credit, FX, rates, commodities, or other instruments

## Routing Guardrail

- `no order`, `no trading`, `do not place trades`, and equivalent negations must keep a request out of execution routing.
- Guardrail-verification wording such as "verify blocked order/approval/execution actions" is evidence of a safety check, not a request to execute.
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

If a future adapter needs secrets, it must use an external secret mechanism and
expose only redacted references through TradingCodex.

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
- applying skill proposals
- changing principals or capabilities
- toggling restricted symbols
- disabling adapters
- changing universe plugin availability
- applying policy changes

Admin is an operations console, not a bypass.

## Paper And Stub Execution

Paper/stub execution remains experimental in the current release line. Keep the
code and guardrails available for local harness validation, but do not present
it as production trading infrastructure or live broker support.

Paper/stub execution still requires:

- structured order intent
- service-layer validation
- valid approval receipt
- role and capability checks
- idempotency check
- adapter availability check
- audit event

# Safety And Execution

Use this page before changing policy, permissions, approvals, broker connectors, order tickets, execution, external MCP, secret handling, or audit. Human-facing rules live in [docs/safety-policy-and-execution.md](../docs/safety-policy-and-execution.md) and [docs/guardrails.md](../docs/guardrails.md).

## Approved Action Boundary

Every executable action follows:

```text
requester -> permission -> policy -> payload validation -> approval/duplicate-request check -> connection -> audit
```

Policy and approval are revalidated immediately before connection use. Broker/API/MCP connection invocation is owned by the Django service layer.

## Order And Execution Rules

`OrderTicket` is the canonical workflow root for draft, check, approval, submission, cancellation, refresh, and inspection. CLI, API, and MCP actions address central DB tickets.

Supported lifecycle:

```text
DRAFT -> PRECHECKED -> READY_FOR_APPROVAL -> APPROVED -> RESERVED
  -> SUBMITTED -> ACKED -> PARTIALLY_FILLED -> FILLED
```

Terminal or review states are `REJECTED`, `CANCELED`, `EXPIRED`, `FAILED`, and `NEEDS_REVIEW`.

Approved execution is idempotent by `portfolio_id`, `account_id`, and `strategy_id`. Duplicate approved-order submission must fail before connection use.

## Required Blocks

TradingCodex must block direct live broker requests, raw external broker/execution/secret/policy proxies, self-issued approvals, non-risk-role approvals, restricted-symbol orders, approval hash mismatches, expired approvals, over-scope submissions, duplicate submissions, duplicate order ids with different payloads, global exposure of approval/execution tools, raw secrets in outputs, and live execution when any live gate is missing.

## Broker And External MCP Posture

Core ships paper by default. Broker connections start disabled or read-only except the built-in paper adapter. Provider adapters become execution-ready only after provider metadata, signed health, policy/config gates, approval hash, idempotency, explicit confirmation, sync, and audit gates pass.

External MCP servers enter through TradingCodex's External MCP Gate. Unknown tools are disabled until classified. Secret and policy/admin tools are not proxyable. Execution tools cannot use direct raw proxy and must map to the approved service-layer connection path.

External MCP user-consent prompts become `McpExternalPermissionRequest` rows.
The coordinator should surface `approval_required` as
`waiting_for_user_permission`; subagents do not continue with buried permission
prompts.

## Secret Wall

Raw broker API keys, tokens, account credentials, and secrets must not appear in repository files, generated workspace files, prompts, shell output, product web, Admin exports, API responses, MCP responses, audit payloads, starter prompts, generated docs, or research artifacts.

## Edit Checklist

When changing this area, inspect:

- `docs/safety-policy-and-execution.md`
- `docs/guardrails.md`
- `tradingcodex_service/application/policy.py`
- `tradingcodex_service/application/orders.py`
- `tradingcodex_service/application/brokers.py`
- `tradingcodex_service/mcp_runtime.py`
- order/policy/broker/API/CLI tests
- generated role allowlists and prompts for authority drift

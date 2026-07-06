---
name: tcx-build
description: Use TradingCodex build mode for self-update, harness/template changes, Codex config/MCP customization, and broker/API provider scaffold or implementation without submitting live orders.
---

# TCX Build

Use this skill when the user asks to update TradingCodex itself, rewrite harness/templates/skills, discover or add Codex MCP config, manage agent customization, or add a broker/API provider for a broker they named.

## Build Gate

Proceed only when both are true:

- Codex permission is full access.
- TradingCodex mode is build and not expired.

If either is false, explain the exact blocker and provide `tcx mode set build --reason <reason>` or the terminal command. Do not perform build work.

## Procedure

1. Confirm the request is product/build work, not an investment recommendation or execution request.
2. For self-update, use the command from `update_status.command` only after an explicit user request; then stop and tell the user to fully restart Codex.
3. For Codex config and MCP customization, use `tcx build status`, `tcx build codex-mcp discover`, `tcx build codex-mcp import`, `tcx build codex-mcp add`, and `tcx build permission list|approve|deny`. Prefer the Web Build Center for user review.
4. Import user-configured MCP servers into the TradingCodex External MCP Gate before use; do not expose unmanaged external MCP tools directly to subagents.
5. For broker connectors, start with `tcx connectors connect <broker> --mode read-only|validation|live-request --credential-ref env:<REF>`, then fall back to provider commands: `tcx connectors providers`, `tcx connectors scaffold <broker-id>`, `tcx connectors register --provider <provider-id> --broker-id <id> --credential-ref env:<REF> --environment <env>`, and `tcx connectors validate <broker-id>`.
6. Store only credential references, env key names, and secret schemas. Never request or persist raw credentials.
7. If an external MCP call needs user consent, stop at `waiting_for_user_permission` and surface the pending request; do not bury the prompt in a subagent transcript.
8. If the requested provider is not installed, treat the task as provider development or scaffold a provider-development-required connector; do not pretend the broker is already supported.
9. If provider files changed while the TradingCodex service is running, report `service_restart_required` and stop at validation; do not treat the provider as hot-loaded for live execution.
10. Validate with focused tests, `./tcx doctor`, and generated-workspace smoke checks when harness surfaces changed.

## Hard Stops

- Build mode may create live-capable providers, but never submits live orders.
- Do not call raw broker APIs from shell, hooks, skills, or ad hoc scripts.
- Do not bypass TradingCodex policy, approval, idempotency, connection, or audit gates.
- Live submission must use TradingCodex MCP canonical tools only; broker API, SDK, or broker-specific MCP calls stay behind reviewed service adapters.
- Do not rewrite user-owned Codex config outside TradingCodex managed blocks.

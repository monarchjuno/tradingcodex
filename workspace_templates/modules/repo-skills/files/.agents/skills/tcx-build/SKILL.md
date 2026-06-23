---
name: tcx-build
description: Use TradingCodex build mode for self-update, harness/template changes, and broker/API connector scaffold or implementation without granting live execution.
---

# TCX Build

Use this skill when the user asks to update TradingCodex itself, rewrite harness/templates/skills, or add a broker/API connector such as Binance, KIS, Upbit, Alpaca, or IBKR.

## Build Gate

Proceed only when both are true:

- Codex permission is full access.
- TradingCodex mode is build and not expired.

If either is false, explain the exact blocker and provide `tcx mode set build --reason <reason>` or the terminal command. Do not perform build work.

## Procedure

1. Confirm the request is product/build work, not an investment recommendation or execution request.
2. For self-update, use the command from `update_status.command` only after an explicit user request; then stop and tell the user to fully restart Codex.
3. For connectors, use `tcx connectors scaffold`, `tcx connectors register`, and `tcx connectors validate`; user-friendly aliases such as `binance`, `kis`, `한투`, `upbit`, and `alpaca` are valid.
4. Store only credential references and secret schemas. Never request or persist raw credentials.
5. Validate with focused tests, `./tcx doctor`, and generated-workspace smoke checks when harness surfaces changed.

## Hard Stops

- Build mode never enables `live_order`.
- Do not call raw broker APIs from shell, hooks, skills, or ad hoc scripts.
- Do not bypass TradingCodex policy, approval, idempotency, connection, or audit gates.

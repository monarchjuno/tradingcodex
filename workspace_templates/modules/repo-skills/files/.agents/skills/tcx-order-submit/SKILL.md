---
name: tcx-order-submit
description: Submit one already-approved TradingCodex order from a native Codex workspace session. Use only when the user intentionally requests the final submit action and already has the exact order ticket id and matching approval receipt id.
---

# Execute Approved Order

Submit exactly one approved order through the native TradingCodex execution gate.
This skill carries no tool authority. The `UserPromptSubmit` hook validates the
complete prompt and calls the service-owned execution boundary before any model
or subagent can act.

## Exact Invocation

Enter one of these forms as the complete user prompt:

```text
$tcx-order-submit --ticket-id <ticket-id> --approval-receipt-id <approval-receipt-id>
$tcx-order-submit --ticket-id <ticket-id> --approval-receipt-id <approval-receipt-id> --live-confirmation <token>
```

Replace every placeholder with the canonical identifier or confirmation token.
Use literal `--name value` pairs. Do not add prose, comments, another skill,
quotes, escaped values, aliases, or `--name=value` syntax.

## Hard Stops

- Invoke this action only from a root native Codex user turn.
- Do not invoke it from Plan mode or a subagent.
- Do not use it to create or approve an order.
- Do not retry a `needs_review` or uncertain result. Inspect canonical order
  status first.
- Keep the existing approval, policy, adapter, live-confirmation, idempotency,
  audit, and reconciliation gates intact.

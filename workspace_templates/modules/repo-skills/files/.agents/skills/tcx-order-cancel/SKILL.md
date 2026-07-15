---
name: tcx-order-cancel
description: Cancel one known submitted TradingCodex broker order from a native Codex workspace session. Use only when the user intentionally requests the final cancel action and has the exact ticket, broker order, and approval receipt ids.
---

# Cancel Submitted Order

Cancel exactly one submitted broker order through the native TradingCodex
execution gate. This skill carries no tool authority. The `UserPromptSubmit`
hook validates the complete prompt and calls the service-owned cancellation
boundary before any model or subagent can act.

## Exact Invocation

Enter one of these forms as the complete user prompt:

```text
$tcx-order-cancel --ticket-id <ticket-id> --broker-order-id <broker-order-id> --approval-receipt-id <approval-receipt-id>
$tcx-order-cancel --ticket-id <ticket-id> --broker-order-id <broker-order-id> --approval-receipt-id <approval-receipt-id> --live-confirmation <token>
```

Replace every placeholder with the canonical identifier or confirmation token.
Use literal `--name value` pairs. Do not add prose, comments, another skill,
quotes, escaped values, aliases, or `--name=value` syntax.

## Hard Stops

- Invoke this action only from a root native Codex user turn.
- Do not invoke it from Plan mode or a subagent.
- Do not use it to discard a local draft; draft discard remains a separate
  portfolio-manager action.
- Do not retry a `needs_review` or uncertain result. Inspect canonical order
  status first.
- Keep the existing approval, policy, broker-order identity, live-confirmation,
  idempotency, audit, and reconciliation gates intact.

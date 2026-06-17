---
name: execute-paper-order
description: "Submit an approved non-live OrderTicket through the workspace MCP execution boundary when a matching approval receipt already exists."
---

# Execute Approved Non-Live Order

Use this skill only with an approved OrderTicket and a valid approval receipt
whose exact order payload hash still matches the current ticket payload.

Universe and adapter gate:

- Reconfirm that the approved OrderTicket, approval receipt, policy decision, broker/adapter, and instrument support all match.
- Supported non-live adapters are `paper-trading`, `stub-execution`, and reviewed test/sandbox validation connectors with an allowed execution posture.
- Paper/stub execution can support only the instruments represented by the installed adapter contract.
- Test/sandbox validation connectors are validation-only unless a future policy explicitly enables a fuller non-live execution path; never switch a connector into real/place-order mode from this skill.
- If the approved artifact references an unsupported live, account, margin, options, futures, FX, commodity, or credit execution path, stop and report the mismatch.

Execution path:

1. Fetch the ticket with `get_order_ticket`.
2. Validate the order ticket payload and approval receipt.
3. Call the workspace MCP execution tool `submit_approved_order` with `ticket_id`.
4. Confirm the ticket timeline records reservation, submit, ack/fill/reject state.
5. Confirm an audit event was written.

Rules:

- Non-live execution still goes through the workspace MCP execution boundary.
- If validation fails, stop and write the rejection reason; do not attempt a workaround.
- If universe/instrument or adapter support fails, stop rather than falling back to a direct broker or shell path.
- Report execution status, adapter, ticket id, broker order/fill state, and audit trail reference.

---
name: approve-order
description: "Approve or reject a checked OrderTicket without executing it after risk review, policy review, restricted-list checks, approval-table freshness, and creator-versus-approver separation."
---

# Approve Order

Use this skill to approve or reject a checked OrderTicket after risk and policy
review.

Required inputs:

- Canonical order ticket id
- Risk review artifact or risk-check output
- Policy review result or `simulate_policy` output
- Universe/instrument support and adapter eligibility from policy review
- Latest approval table metadata from `run_order_checks`, including
  `valid_until`, `invalidates_on`, per-row `quote_as_of`, `cash_as_of`,
  `order_status_as_of`, and cash-reserve stress fields.

Approval path:

1. Fetch the ticket with `get_order_ticket`.
2. Run or inspect `run_order_checks` and require no failing checks. For new
   check output, inspect machine-readable approval table metadata:
   - `valid_until`
   - `invalidates_on`: quote drift, cash delta, order-status delta,
     replacement-ticket creation, terminal-refresh failure, age threshold
   - per-row `quote_as_of`, `cash_as_of`, and `order_status_as_of`
   - cash-reserve stress line with pre-batch orderable, total notional,
     fee/tax reserve, post-batch residual, and residual warning threshold
3. If the table is stale, missing a required new-field value, or any
   invalidation event has occurred, stop and request recheck. Metadata-free
   legacy tables may continue through the existing flow, but call out the
   legacy warning instead of implying freshness.
4. Confirm `approved_by` is not the same principal as `created_by`.
5. Confirm restricted list, enabled adapter, instrument support, notional limit, and approval readiness are all acceptable.
6. Create the approval receipt through `request_order_approval` as the configured approval principal.
7. Confirm the receipt binds `order_ticket_id`, broker/account scope, expiry, the exact order payload hash, and the latest approval table metadata.

Do not create an approval receipt when the latest approval table is stale; use
`request_order_approval` only after the recheck passes.

Reject path:

- If validation, risk, or policy fails, write or reference the rejection reason.
- Do not create an approval receipt for a revise/reject decision.

Rules:

- Do not approve a live broker order unless a user-installed adapter and policy explicitly enable it.
- Do not approve unsupported universes or instruments merely because research or screen-grade analysis exists.

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
     replacement-ticket creation, terminal-refresh failure, age threshold,
     and — when the ticket carries `session_close_at` — session cutoffs
     (`session_revalidation_window`, `resting_day_cutoff`,
     `session_close_cutoff`)
   - `session_deadline` (when present): `session_close_at`,
     `revalidation_due_at` (T-60), `resting_day_cutoff_at` (T-30),
     `latest_safe_submit_at` (T-15), and `cutoff_policy_id`
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

Session-deadline behavior for tickets with `session_close_at` (derived from the
ticket's expiry metadata, e.g. US DAY close 05:00 KST — never hardcode market
close times):

- From T-60 (`revalidation_due_at`), a table built before that point requires a
  read-only revalidation and one re-present; `request_order_approval` returns
  `recheck_required` with a `re_present` payload (exact ticket id, order payload
  hash, remaining minutes, next action). Never auto-approve or auto-submit.
- From T-30 (`resting_day_cutoff_at`), new approval receipts for resting DAY
  orders are blocked; the gate returns `approval_wait_cutoff`. Immediate
  execution order types remain allowed until T-15.
- From T-15 (`latest_safe_submit_at`), all new DAY approvals and submits are
  blocked, existing receipts are invalidated, and only a next-session successor
  proposal (with a new approval and a new LIVE confirmation) is allowed. Do not
  silently reuse or auto-recreate the ticket after cutoff.
- Receipt `expires_at`/`valid_until` are clamped to `latest_safe_submit_at`.
- Tickets without `session_close_at` keep the existing flow and surface a
  warning that session cutoffs are not enforced.
- An approved DAY ticket past session close with no broker order or fill is
  locally expired (`ticket_expired_no_resubmit`); re-entry requires a fresh
  successor ticket (new order checks, new approval, new LIVE confirmation), not
  reuse of the expired receipt.

Reject path:

- If validation, risk, or policy fails, write or reference the rejection reason.
- Do not create an approval receipt for a revise/reject decision.

Rules:

- Do not approve a live broker order unless a user-installed adapter and policy explicitly enable it.
- Do not approve unsupported universes or instruments merely because research or screen-grade analysis exists.

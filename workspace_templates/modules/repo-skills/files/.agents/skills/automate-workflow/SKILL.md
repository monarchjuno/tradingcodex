---
name: automate-workflow
description: "Prepare and register safe Codex automations for recurring TradingCodex workflows through user Q&A, preflight checks, arming status, and head-manager registration. Use when the user asks to automate, schedule, monitor, or periodically run an agentic workflow, including workflows that may draft orders or request paper/live execution gates."
---

# Automate Workflow

Use this skill to turn a recurring workflow request into a Codex automation that is armed before it runs. The goal is to remove silent blockers before registration, not to grant execution authority.

## Procedure

1. Classify the automation mode: `observe`, `review`, `draft-order`, `paper-execution`, `live-assisted`, or `live-execution`.
2. Ask only for missing fields needed to arm the automation: schedule, workspace, workflow request, allowed actions, execution scope, approval model, blocker handling, and stop conditions.
3. Build a compact mandate summary with the recurring prompt, mode, lane, allowed actions, blocked actions, symbols/universe, strategy/profile/account, broker, max order/daily limits, expiry, and kill-switch conditions.
4. Run preflight before creating an active automation. Use existing TradingCodex service checks and MCP/read-only status tools when available: service health, runtime mode, active profile, relevant broker status, policy simulation, role/capability posture, and execution-gate readiness for the selected mode.
5. Mark the mandate `armed` only when every required preflight item is known and passing. If any required item is missing or failing, do not create an active automation.
6. When armed, register the Codex automation with a prompt that includes the mandate summary, run-time preflight requirements, blocked-action policy, and explicit stop states.
7. Prefer updating a matching existing automation over creating a duplicate. If the Codex automation tool is unavailable, return a ready-to-register summary instead of writing raw automation files.

## Execution Modes

- `observe`: research, news, data, or portfolio monitoring only.
- `review`: recurring workflow artifacts and synthesis, no order ticket.
- `draft-order`: may create order-ticket drafts only when the mandate permits it.
- `paper-execution`: may use approved paper execution only through TradingCodex service gates.
- `live-assisted`: may prepare live execution artifacts, but user approval remains required.
- `live-execution`: requires a narrow pre-approved mandate plus every TradingCodex live gate.

## Required Preflight

For all modes:

- The workspace is attached and healthy enough for `./tcx doctor` or the relevant layer check.
- The recurring workflow prompt is specific enough to route through `$tcx-workflow`.
- The schedule is clear.
- The automation has a blocker policy: `pause`, `downgrade-to-draft`, or `report-only`.

For order drafting:

- The mandate names the allowed symbols or universe, strategy/profile scope, broker/account scope, and max order size.
- Order drafting is allowed explicitly; approval and execution remain blocked unless separately allowed.

For execution:

- The approval model is explicit: `per-run-approval` or `pre-approved-mandate`.
- Role/capability checks, policy posture, broker connection status, idempotency posture, and audit path are preflighted.
- Live execution also requires live policy/config opt-in, reviewed provider, signed health, `trading_enabled`, live trade scope, explicit max notional/daily/order-count limits, expiry, and kill-switch rules.

## Registration Prompt Contract

The Codex automation prompt must tell the future run to:

- Use `$tcx-workflow` or the selected TradingCodex skill instead of direct analysis.
- Re-run preflight at the start of every scheduled run.
- Stop with `BLOCKED_BEFORE_EXECUTION` when a required gate fails before broker submission.
- Stop with `NEEDS_REARM` when permissions, policy, broker, scope, schedule, or mandate limits drift.
- Never read raw secrets, change policy, self-approve, call raw broker APIs, or widen the mandate.
- Submit or cancel only through TradingCodex MCP canonical tools when execution is allowed.

## Hard Stops

- Do not create an `ACTIVE` automation unless the mandate is armed.
- Do not treat Codex automation as approval, execution, broker, policy, or secret authority.
- Do not hide blockers in the recurring prompt. Surface them before registration.
- Do not register broad live-execution automation without symbol/universe, broker/account, notional, daily, order-count, time-window, expiry, and kill-switch limits.

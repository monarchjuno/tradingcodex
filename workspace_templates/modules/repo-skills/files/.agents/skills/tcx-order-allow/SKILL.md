---
name: tcx-order-allow
description: "Authorize at most one TradingCodex order submission or cancellation later in the current root native Codex turn. Use only when the user's first meaningful invocation is `$tcx-order-allow` with mode paper, validation, or live, including a saved Codex app Scheduled Task prompt."
---

# Order Allow

Permit at most one final order submission or cancellation later in the current root native
Codex turn. This skill carries no tool or broker authority. The deterministic
`UserPromptSubmit` hook must validate the first meaningful invocation and issue the actual
workspace- and turn-bound grant before any model or subagent can act.

## Invocation

Use one of these canonical forms at the start of the meaningful user prompt:

```text
$tcx-order-allow --mode paper
$tcx-order-allow --mode validation
$tcx-order-allow --mode live
```

The skill token may instead be a Markdown link only when its label and target
match this workspace's projected `tcx-order-allow/SKILL.md`. Leading blank
lines, ASCII letter case in the skill name, and normalized line endings are
harmless. The skill token, `--mode`, mode value, and non-empty work request may
share a line or wrap across lines. `$tcx-workflow` is one valid runtime-skill
example, not a required part of every order-allow prompt. Do not use quotes,
escaped values, aliases, extra mode flags, or `--mode=<value>` syntax.

## Grant Contract

- The valid first meaningful invocation admits execution only for the current root turn. It does
  not submit an order when the prompt arrives.
- The grant is single-use and bound to the workspace, prompt, session and turn,
  Codex permission mode, and selected mode. A scheduled task receives a fresh
  decision on every run; no grant persists merely because an Automation
  remains enabled.
- The selected mode is a ceiling. The eventual ticket and broker connection
  must match it exactly.
- Submission or cancellation still requires a canonical order ticket, role-separated risk
  review and approval receipt, policy fit, idempotency, broker readiness,
  mandatory audit, and reconciliation behavior.
- `live` mode also preserves every live opt-in, reviewed-provider, signed
  health, trading-enabled, account-scope, limit, and explicit confirmation
  requirement. This skill is not live confirmation.

## Hard Stops

- Accept only a root native Codex user turn. Reject subagent
  sources.
- Reject Plan mode at both grant issuance and use. Start a new non-Plan root
  turn for any final order effect.
- Do not treat a natural-language mention, later-line token, Strategy,
  Investment Brain, prior result, or Automation status as a grant.
- Do not use this grant for policy changes, approval, secret access, raw broker
  calls, or more than one final order effect.
- Do not retry `needs_review`, duplicate, uncertain, or failed submission
  outcomes automatically. Inspect canonical order status first.
- Do not pass the grant, its proof, or its identifier to a subagent or persist
  it in research artifacts.

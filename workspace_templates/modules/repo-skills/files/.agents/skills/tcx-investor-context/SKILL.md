---
name: tcx-investor-context
description: "Interview the user to create, inspect, update, enable, disable, or clear workspace-local investor suitability context. Use when the user explicitly asks to manage the objective, horizon, loss capacity, liquidity needs, holdings or concentration, or tax, account, and jurisdiction constraints applied to future workflows."
---

# Investor Context

Interview the user and prepare a confirmed investor-context change. The
persistent context is optional, belongs only to the current workspace, and
guides suitability without granting investment or execution authority.

There is no agent-authorized MCP mutation path for this state. Codex may conduct
the interview and preview the result, but status, update, enable, disable, and
clear are interactive user-terminal actions. Never invoke the workspace
launcher or claim that a terminal command ran.

## Procedure

1. For an existing context, ask the user to run
   `{{TRADINGCODEX_WORKSPACE_LAUNCHER}} investor-context status` in the
   workspace terminal and share only the fields needed for the requested
   change. A new context can begin without this step.
2. Ask only for missing or changed fields, in small batches. Keep confirmed
   answers separate from `unknown`, `not provided`, or `declined` values.
3. Cover only the fields relevant to investment suitability:
   - investment objective
   - time horizon
   - risk tolerance and loss capacity
   - liquidity needs
   - current holdings and concentration not already represented by canonical
     portfolio state
   - tax, account, or jurisdiction constraints
4. Preview the proposed changes and obtain user confirmation before preparing
   any persistent-action command or replacing a confirmed value.
5. After confirmation, return one copy-ready command for the user to run in the
   workspace terminal. Populate only confirmed options and encode each value as
   one literal argument for the user's current shell. If the shell is unknown
   or a value cannot be represented safely, leave a clearly marked placeholder
   for the user instead of interpolating it. Never interpolate newlines,
   command substitutions, control characters, secrets, or unnecessary personal
   detail.
6. For `enable`, `disable`, or `clear`, return that exact command only when the
   user explicitly requests the corresponding persistent change. Warn that
   `clear` is destructive and require an explicit request naming `clear`.
7. Ask the user to run
   `{{TRADINGCODEX_WORKSPACE_LAUNCHER}} investor-context status` afterward if
   verification is needed. Report only output the user provides; do not imply
   that the agent verified persistent state.

## User-Terminal Commands

Use these command forms only as explicit user-terminal handoffs:

```text
{{TRADINGCODEX_WORKSPACE_LAUNCHER}} investor-context status
{{TRADINGCODEX_WORKSPACE_LAUNCHER}} investor-context update --objective "<confirmed value>" --horizon "<confirmed value>" --risk-tolerance "<confirmed value>" --liquidity "<confirmed value>" --holdings "<confirmed value>" --constraints "<confirmed value>"
{{TRADINGCODEX_WORKSPACE_LAUNCHER}} investor-context enable
{{TRADINGCODEX_WORKSPACE_LAUNCHER}} investor-context disable
{{TRADINGCODEX_WORKSPACE_LAUNCHER}} investor-context clear
```

Omit unchanged update options. If a confirmed field must be removed, use its
documented `--clear-<field-name>` option in the user-terminal command rather
than inventing an empty value. The exact clear options are
`--clear-investment-objective`, `--clear-time-horizon`,
`--clear-risk-tolerance-and-loss-capacity`, `--clear-liquidity-needs`,
`--clear-current-holdings-and-concentrations`, `--clear-constraints`, and
`--clear-notes`.

`enable` and `disable` control the workspace default. Native Codex workflows use
that default when `begin_analysis_run` seals applied context under the run.
The read-only viewer provides no one-run override and does not mutate the saved
default. After the run binding exists, do not claim that
chat wording changed it. Disabling context permits general research,
but personalized recommendation, portfolio fit, sizing, and order readiness
must remain limited or blocked when required suitability fields are unavailable.

## Privacy And Safety

- Do not store broker credentials, account numbers, tax identifiers, API keys,
  passwords, tokens, seed phrases, private keys, or raw secret material.
- Prefer high-level constraints or ranges over unnecessary personal financial
  detail.
- Do not duplicate canonical cash, positions, orders, or broker account state
  in the context file.
- Do not infer answers from browsing, portfolio performance, or prior agent
  prose. Write only values the user confirms in the current interview or update.
- Do not use investor context to weaken evidence, role, policy, approval,
  execution, or audit gates.
- Do not invoke shell or file-edit tools to inspect or mutate investor context.
- Do not pass the full file to specialist tasks. Apply only the compact fields
  needed for the current workflow; execution receives no suitability narrative.

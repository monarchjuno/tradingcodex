---
name: tcx-investor-context
description: "Interview the user to create, inspect, update, enable, disable, or clear workspace-local investor suitability context. Use when the user explicitly asks to manage the objective, horizon, loss capacity, liquidity needs, holdings or concentration, or tax, account, and jurisdiction constraints applied to future workflows."
---

# Investor Context

Interview the user and prepare a confirmed investor-context change. The
persistent context is optional, belongs only to the current workspace, and
guides suitability without granting investment or execution authority.

This is an ordinary user-owned workspace file. Read and update it with native
workspace file tools; do not add an MCP service or hand routine changes to a
terminal command.

## Procedure

1. Read `.tradingcodex/user/investor-context.md` when it exists. Treat a missing
   file as unconfigured and do not search other files for replacement values.
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
4. Preview the exact proposed changes and obtain user confirmation before the
   first write or before replacing or removing a confirmed value.
5. After confirmation, create or update only
   `.tradingcodex/user/investor-context.md` with native `apply_patch`. Preserve
   unchanged confirmed fields and notes. Pass that exact workspace-relative
   path to `apply_patch`; never prefix the workspace path or use an absolute
   path. Set `updated_at` to the current ISO-8601 time and `updated_by` to
   `user`.
6. For `enable` or `disable`, change only `enabled_by_default` after an explicit
   request. For `clear`, warn that the action removes all saved fields and notes,
   require an explicit request naming `clear`, and leave a valid empty context
   document rather than deleting unrelated files.
7. Re-read that exact workspace-relative file after a write and report its
   configured fields, default application state, updated time, and path without
   echoing unnecessary sensitive detail. A similarly suffixed file under a
   duplicated workspace path is not valid verification. If the expected file
   is missing or invalid, fix it before claiming success.

## File Contract

Keep schema-versioned YAML frontmatter with `schema_version: 1`,
`scope: workspace`, `enabled_by_default`, `updated_at`, and `updated_by`. Store
confirmed suitability fields with these exact keys:

- `investment_objective`
- `time_horizon`
- `risk_tolerance_and_loss_capacity`
- `liquidity_needs`
- `current_holdings_and_concentrations`
- `constraints`

Use `# Investor Context` as the Markdown heading and put optional confirmed
notes below it. Omit unknown, declined, cleared, or empty fields instead of
inventing values. Do not edit generated run snapshots; `begin_analysis_run`
creates those from the saved file.

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
- Do not use shell commands, the workspace launcher, or MCP for routine
  investor-context reads and writes.
- Do not pass the full file to specialist tasks. Apply only the compact fields
  needed for the current workflow; execution receives no suitability narrative.

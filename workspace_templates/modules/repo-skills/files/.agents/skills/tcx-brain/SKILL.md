---
name: tcx-brain
description: "Author and manage TradingCodex Investment Brains. Use when the user wants to create, inspect, revise, validate, install, update, activate, deactivate, roll back, remove, or delete a user-owned `investment-brain-*` source or managed plugin. Start tool-using management as an exact first-line `$tcx-brain` root turn; do not wrap it in `$tcx-build`."
---

# TCX Brain

Manage the complete Investment Brain lifecycle through one user entrypoint. A
Brain shapes Head Manager's inquiry and interpretation; it is not a Strategy,
role roster, workflow, memory store, policy package, or execution extension.

`$tcx-brain` manages Brains. It never selects one for analysis. A native
analysis still requires one exact active `$investment-brain-*` invocation.

Read [bundle-contract.md](references/bundle-contract.md) before creating,
revising, or deleting a user-owned source bundle.

## Action Model

Keep the two managed layers distinct:

- Source actions create, inspect, revise, validate, or explicitly delete a
  user-owned workspace-local bundle below `investment-brains/`.
- Plugin actions list, inspect, install, update, activate, deactivate, roll
  back, or remove registry-managed immutable versions through the canonical
  `manage_investment_brain` MCP service.

`remove` is the managed delete operation: it removes the Head Manager
projection and marks the plugin removed while retaining immutable versions for
run provenance. It does not delete a user-owned source directory. A source
deletion is a separate, explicitly named action.

## Managed Turn Admission

1. Require the original root prompt to begin with the exact physical first line
   `$tcx-brain` before using file or lifecycle tools. Put the concrete request
   on following lines. Do not combine it with `$tcx-build`, `$tcx-strategy`, or
   an order marker.
2. Use the normal `trading-research` profile. The marker creates only a
   Brain-scoped current-turn grant: it admits canonical `investment-brains/`
   source edits and the proof-protected `manage_investment_brain` MCP tool, not
   generic Build, Strategy, order, credential, publication, or global-config
   authority.
3. Plan mode, follow-ups without a fresh marker, and subagents have no managed
   authority. If reviewed confirmation arrives later, return a new root-turn
   prompt beginning with `$tcx-brain`.
4. Without managed admission, explain or draft the requested operation in the
   conversation, or return the exact
   user-terminal `{{TRADINGCODEX_WORKSPACE_LAUNCHER}} investment-brains ...`
   command. Do not attempt a mutation.
5. Read-only `list`, `inspect`, and `validate` still use this direct skill turn
   when Codex calls `manage_investment_brain`. Otherwise return the matching
   launcher command for the user to run in a terminal.

## Source Procedure

1. For a new Brain, use the canonical
   `investment-brains/<investment-brain-id>` source directory. Use a new lowercase
   hyphen-case `investment-brain-*` id and version `1.0.0` unless the user chose
   another valid initial version.
2. Revise or delete only a user-owned source directory explicitly identified by
   the user. Never edit or delete `.tradingcodex/investment-brains`, projected
   `.agents/skills/investment-brain-*`, a third-party package, an external
   source, or an upstream repository. Adapt third-party ideas only under a new
   user-owned id with a compatible license and original wording.
3. For create or revise, ask the user to select the exact Decision Memory
   episodes, forecasts, postmortems, validated lessons, and contrary cases. Do
   not sweep all memory or infer consent from relevance.
4. Require counterexamples and scope limits. Perform a privacy review that
   excludes private Investor Context, account or holding details, personal
   constraints, confidential sources, issuer-specific cases, and verbatim
   private prose.
5. Abstract the selected evidence into general hypotheses, inquiry priorities,
   interpretation principles, causal frames, scenarios, falsifiers,
   applicability limits, and abstention heuristics. Do not copy private cases,
   names, tickers, account facts, or memory text into the bundle.
6. Before writing, state the abstraction, counterexamples, limitations,
   excluded private material, id, version, publisher, license, destination, and
   requested source action. An exact create or revise request with sufficient
   inputs already authorizes that reversible source change; do not add a
   redundant confirmation. Require a new explicit request for deletion or when
   a missing privacy/evidence choice would materially change the bundle.
7. Write only the strict source bundle described in the reference. Keep its
   content platform-neutral. It must not name roles, tools, models, sandboxes,
   workflow order, artifact paths, memory operations, policy, approval, broker,
   order, or execution authority.
8. For a source deletion, first state that installed immutable versions and
   historical provenance remain. Delete only the exact confirmed user-owned
   source files; do not translate source deletion into managed plugin removal or
   vice versa.
9. After create or revise, call `manage_investment_brain` with
   `action="validate"` and `local_source=<source-directory>`. Changed content
   already represented by an installed
   version requires a version higher than every installed version.
10. Stop after any source create, revise, or delete action. Do not install,
    update, activate, remove, stage, commit, configure a remote, push, publish,
    or open a pull request in the same turn. A reviewed lifecycle action starts
    in a fresh exact `$tcx-brain` turn.

## Managed Plugin Procedure

1. Identify exactly one explicit workspace-local source directory or public,
   credential-free HTTPS Git URL and ref. Do not infer a source or search a
   marketplace. Private, authenticated, SSH, file, or external local Git
   sources remain explicit user-terminal workflows.
2. Validate before install or update and inspect the returned id, version,
   source posture, content digest, and skill digest.
3. Install inactive first, then inspect the registry result. Activate only when
   the user explicitly requested that exact validated id and version.
4. Use only the proof-protected `manage_investment_brain` MCP tool; never edit
   registry, package, projection, generated index, or Head Manager config files
   directly. Select one exact action and only its matching fields:

   ```text
   action=list [active_only]
   action=inspect brain_id=<investment-brain-id>
   action=validate local_source=<source-directory>
   action=validate git_source=<public-https-url> [ref=<ref>]
   action=install local_source=<source-directory>
   action=install git_source=<public-https-url> [ref=<ref>]
   action=update brain_id=<investment-brain-id> [local_source=<source-directory>|git_source=<public-https-url> ref=<ref>]
   action=activate|deactivate brain_id=<investment-brain-id>
   action=rollback brain_id=<investment-brain-id> [version=<major.minor.patch>]
   action=remove brain_id=<investment-brain-id>
   ```

   Install always starts inactive. The MCP process owns service access; do not
   reopen the denied TradingCodex runtime or call its Python directly.

5. Update by publishing a higher immutable version. Never rewrite installed
   bytes under an existing version. Rollback selects an already-installed
   version; remove retains all installed versions for provenance.
6. Return the action, Brain id, selected version, status, validation posture,
   source posture, digests, projection posture, and exact next step. Do not
   expose credentials or absolute external local paths.

## Hard Stops

- Do not mutate Decision Memory, Investor Context, a Strategy, or current
  evidence while managing a Brain.
- Do not use `$tcx-build` as a wrapper or use the Brain grant for generic
  workspace maintenance.
- Do not present a Brain as validated investment truth; current authenticated
  evidence can falsify it.
- Do not stage, commit, configure a remote, push, publish, or open a pull
  request unless the user separately requests that Git or publication action.
- Do not approve orders, grant broker authority, change policy, submit, cancel,
  or execute through this skill.

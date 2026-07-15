---
name: tcx-build
description: Mark one exact root Codex turn as explicit workspace-local TradingCodex Build intent for workspace refresh, managed optional-role-skill lifecycle work, managed MCP configuration, and broker/API provider development without elevating filesystem permission or granting order execution. Strategy and Investment Brain management use their own direct skills instead.
---

# TCX Build

Use this skill only when the original root user prompt begins with the exact physical first line `$tcx-build`.
The remaining lines describe the requested build work.

## Turn Contract

- Treat the marker as current-turn user intent, not filesystem permission.
- Codex's active native permission profile still decides what tools can reach.
  The marker and hook do not elevate the default `trading-research` profile.
  Ordinary user-owned files outside `trading/` can be changed in Research and
  are not Build work. Start controlled `trading/`, managed lifecycle, or
  connector work in a new root turn with `trading-build` selected; canonical DB
  calls remain separately proof-protected and service-owned.
- Do not wrap `$tcx-brain` or `$tcx-strategy` work in this marker. Those skills
  use separate capability-scoped turns in the normal `trading-research`
  profile. If the request is actually Brain or Strategy management, stop and
  return the corresponding direct first-line prompt.
- Do not issue or use a Build grant while Codex is in platform Plan mode.
  Start a new root turn in `trading-build` when the requested work must edit
  files. The grant is bound to the permission mode in which it was issued, so
  switching profiles does not carry authority forward.
- Use the grant only in the root native Codex turn. Subagents
  cannot inherit or use it.
- The grant is multi-use within this turn so editing and validation can finish.
  A follow-up turn that mutates state must begin with `$tcx-build` again.
- For recurring Automation, require the saved prompt to start with the marker
  on every run. File-mutating work also requires `trading-build`; prefer an
  isolated worktree or workspace and retain a reviewable diff.
- Keep direct edits and commands workspace-local. Use typed TradingCodex
  MCP services for connector state. External MCP lifecycle changes remain a
  separate user-terminal operator workflow.
- Keep local work in the native Build lane: use `apply_patch` for edits and
  workspace-local shell, Python, and test tools as needed. The profile permits
  ordinary workspace writes and the dedicated `$TRADINGCODEX_SCRATCH` path but denies TradingCodex
  runtime/DB state, credentials, protected ledgers, network access, and global
  Codex config. Trusted workspace-launcher lifecycle commands remain
  allowlisted and proof-gated.

If the actual Codex permission blocks a required tool, report that platform
blocker and stop. Do not create another TradingCodex permission state.

## Procedure

1. Confirm the request is product/build work, not an investment recommendation or execution request.
2. For self-update, inspect status only after an explicit user request. When `package_refresh_user_terminal_required=true`, do not run the refresh and return `interactive_user_terminal_command`. Otherwise run non-empty `update_status.command` only when it is admitted by the trusted workspace-launcher Build lane; if it is unavailable, return the reported terminal command. After an update, stop and tell the user to fully restart Codex.
3. If the request is Investment Brain management, stop with a new prompt whose
   exact first line is `$tcx-brain`. If it is Strategy management, stop with a
   new prompt whose exact first line is `$tcx-strategy`. Never issue those
   capability grants from a Build turn or combine their markers.
4. For a managed optional role skill, author the standalone body with
   `apply_patch` in a workspace-local staging file, then use the exact
   `{{TRADINGCODEX_WORKSPACE_LAUNCHER}} skills optional ...` lifecycle command
   so validation and projection remain service-owned. Do not directly repair
   generated skill folders, role TOML, or root projection blocks. Activation
   still requires the user's explicit request.
5. For Codex config and MCP customization, use `{{TRADINGCODEX_WORKSPACE_LAUNCHER}} build status`, `{{TRADINGCODEX_WORKSPACE_LAUNCHER}} build codex-mcp discover`, and workspace-scoped `{{TRADINGCODEX_WORKSPACE_LAUNCHER}} build codex-mcp add`. Importing a discovered entry into the External MCP Gate is not Build work; stop with the exact interactive user-terminal command `{{TRADINGCODEX_WORKSPACE_LAUNCHER}} mcp external import-codex --source workspace|global|any --name <server>`. Use `{{TRADINGCODEX_WORKSPACE_LAUNCHER}} mcp permission list` only to surface pending external consent requests; only the user may approve or deny them.
6. Never register, probe, discover, or review an External MCP server from Head Manager in a Build turn. Prepare workspace-local config if requested, then stop with the exact user-terminal `{{TRADINGCODEX_WORKSPACE_LAUNCHER}} mcp external ...` next step. Do not expose unmanaged external MCP tools directly to subagents.
7. For broker connectors, inspect providers with the read-only provider-list tool, then call `render_broker_connector_scaffold`. It returns target content plus content-addressed preimage existence/hash/size metadata and performs no workspace write; it never returns existing file content. Verify those preimages and create or update the returned files with `apply_patch`; never ask an MCP scaffold tool to write them. Use only the build-protected DB tools `register_broker_connector`, `validate_broker_connector_build`, and `record_broker_mapping_review` for service state. `connect` and the write-style `scaffold` command remain explicit user-terminal operator flows and are not agent MCP tools; do not invoke their CLI equivalents from the agent shell. Provider implementation files remain workspace-local edits.
8. Store only credential references, env key names, and secret schemas. Never request or persist raw credentials.
9. If an external MCP call needs user consent, stop at `waiting_for_user_permission` and surface the pending request; do not bury the prompt in a subagent transcript.
10. If the requested provider is not installed, treat the task as provider development or scaffold a provider-development-required connector; do not pretend the broker is already supported.
11. A workspace provider bundle is untrusted source until the user approves its exact bundle hash from a terminal. Stop with `{{TRADINGCODEX_WORKSPACE_LAUNCHER}} connectors inspect-provider <provider-id>` and `{{TRADINGCODEX_WORKSPACE_LAUNCHER}} connectors approve-provider <provider-id>`, and require re-approval after every bundle change; never approve provider code from the Build turn. Approval snapshots the reviewed bytes but executes no code. Report `service_restart_required` and stop at validation until the service restarts.
12. In the generated Build turn, validate with the trusted `{{TRADINGCODEX_WORKSPACE_LAUNCHER}} doctor` path and the smallest useful workspace-local syntax, unit, or smoke checks. General shell and Python are available inside the native profile, but cannot be used to cross protected paths, obtain credentials, reach local/private services, publish remotely, or create order effects. Stop after a successful self-update and tell the user to restart Codex.

## Hard Stops

- A Build turn may create live-capable providers, but never submits or cancels an order.
- A Build turn does not manage an Investment Brain or Strategy. Use the direct
  capability-scoped skill turn instead.
- Do not use Codex Plan mode as Build authority; it blocks the grant entirely.
  In the default `trading-research` profile, ordinary user-owned paths outside
  `trading/` remain writable, but do not attempt controlled `trading/` or
  managed lifecycle edits. A non-writable Automation runtime remains limited
  to rendering/inspection, temporary computation, and specifically
  proof-protected canonical DB calls.
- Do not use the grant for global Codex config, raw credential access, External MCP lifecycle or consent decisions, provider-source approval, Git push/publication, or direct edits to hooks, grants, managed `.gitignore`, credential files, runtime DB, audit, approval, policy, or execution state.
- Do not directly edit generated core harness files, hooks, workspace templates,
  fixed-role configuration, or service-owned projection blocks. Use the
  supported workspace refresh or managed lifecycle service instead.
- Do not call raw broker APIs from shell, hooks, skills, or ad hoc scripts.
- Do not bypass TradingCodex policy, approval, idempotency, connection, or audit gates.
- If a protected call reports that the operation completed but grant
  finalization failed, stop and inspect canonical state. The grant is revoked
  fail-closed; never retry the operation blindly.
- Order submission or cancellation belongs outside Build and must enter through
  the exact native execution gateway for its own current root turn. Broker API,
  SDK, or broker-specific MCP calls stay behind reviewed service adapters.
- Do not rewrite user-owned Codex config outside TradingCodex managed blocks.

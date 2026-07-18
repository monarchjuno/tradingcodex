---
name: tcx-server
description: Operate TradingCodex server, read-only workspace viewer, MCP, update readiness, and broker connector status without build-turn or execution authority.
---

# TCX Server

Use this skill for TradingCodex status checks, service recovery, viewer URL guidance, MCP setup, update status, and safe broker connector inspection.

## Runtime Sources

- Treat the TradingCodex session-start context injected by the hook as the
  initial service and update snapshot. Do not reread its backing files through
  a shell command.
- Refresh product status with the read-only `get_tradingcodex_status` MCP tool.
- Refresh package and workspace update readiness with the read-only
  `get_update_status` MCP tool.
- Inspect user-installed Codex MCP servers, standalone skills, plugins, and
  plugin-provided skills, MCP servers, apps, and hooks with the read-only
  `list_codex_capabilities` MCP tool.
- Inspect connectors only through read-only MCP tools such as
  `list_broker_connections`, `get_broker_connection_status`,
  `list_broker_adapter_providers`, `get_broker_capability_profile`,
  `get_broker_instrument_constraints`, `get_connector_build_status`, and
  `get_order_status`, when the current role permits them.
- Inspect the TradingCodex-supported optional OpenBB integration with the
  read-only `get_data_source_status` tool. Keep this narrow supported exception
  separate from user-owned Codex capability inventory.

## Procedure

1. Start with the injected session context, then call only the smallest
   read-only MCP tool needed to refresh stale or missing information.
2. Report service reachability, compatibility, readiness, package/workspace
   versions, DB mismatch, port conflict, update availability, and the viewer
   URL only when those fields are present in trusted status output.
3. If startup context reports `version_mismatch`, `db_mismatch`, or
   `port_occupied`, explain the recorded issue and next action before
   recommending the viewer.
4. If recovery, doctor, service lifecycle, or package update requires the
   launcher, stop and give an explicit interactive user-terminal handoff. Do
   not invoke the launcher from this skill.
5. For a Codex capability inventory, report only the kind, id, scope,
   enabled/availability state, and parent plugin returned by the trusted tool.
   Do not infer data-versus-execution purpose, trust, licensing, or risk, and
   do not provide install, disable, or removal commands.
6. If managed Build work is requested, give a new root-turn prompt whose exact
   first meaningful invocation is the canonical plain `$tcx-build` token and
   whose following line states the requested change. Explain that an
   equivalent matching workspace skill link is accepted interactively, but the
   plain token is the path-independent handoff. The marker grants no authority
   beyond the current Codex sandbox and TradingCodex policy.
7. For OpenBB, explain `declared_access`, credential-reference availability,
   runtime compatibility, projection/restart state, observed access, auto-use,
   secondary consent, and credential-slot hint provenance only as returned.
   Treat `provider_name_convention_unverified` as a prompt to confirm the slot
   against current provider documentation, never as an exact requirement.
   Never infer free/paid entitlement from a key. After provision, configure,
   enable, disable, or credential-reference changes, the workspace projection
   must be regenerated from the user terminal before a full Codex restart.
   These changes do not take effect by restarting Django.

## Interactive User-Terminal Handoff

Give only the command needed for the diagnosed state and label it **run by the
user in the workspace terminal**. Common commands are:

```text
{{TRADINGCODEX_WORKSPACE_LAUNCHER}} service status
{{TRADINGCODEX_WORKSPACE_LAUNCHER}} doctor --layer service
{{TRADINGCODEX_WORKSPACE_LAUNCHER}} doctor --layer mcp
{{TRADINGCODEX_WORKSPACE_LAUNCHER}} update status --json
{{TRADINGCODEX_WORKSPACE_LAUNCHER}} data-sources openbb status --json
```

For OpenBB setup, give only the minimum command required by the returned
state. Use `provision` only after explicit user consent. Store only environment
variable references, never a value:

```text
{{TRADINGCODEX_WORKSPACE_LAUNCHER}} data-sources openbb provision
{{TRADINGCODEX_WORKSPACE_LAUNCHER}} data-sources openbb configure <provider> --access keyless|free|paid|unknown --credential-ref <slot>=env:<ENV_NAME>
{{TRADINGCODEX_WORKSPACE_LAUNCHER}} data-sources openbb enable <provider> --data-kind <kind> --auto-use allow|ask|deny [--secondary-consent]
{{TRADINGCODEX_WORKSPACE_LAUNCHER}} data-sources openbb probe <provider> --data-kind <kind> [--symbol <symbol>]
{{TRADINGCODEX_WORKSPACE_LAUNCHER}} data-sources openbb disable [<provider>|--all]
{{TRADINGCODEX_WORKSPACE_LAUNCHER}} data-sources openbb clear-credential-ref <provider> --slot <slot>
{{TRADINGCODEX_WORKSPACE_LAUNCHER}} update --skip-refresh --no-doctor
```

Use `clear-credential-ref`, `disable`, or `status` only when that exact action
was requested or diagnosed. Tell the user to define the referenced variable in
the environment that starts Codex; never ask them to paste its value in chat.
After a state-changing OpenBB command, hand off the workspace update command,
then tell the user to fully quit and restart Codex and start a new task.

For a stale compatible-address service using the same DB, hand off:

```text
{{TRADINGCODEX_WORKSPACE_LAUNCHER}} service stop
```

Then tell the user to fully restart Codex so project MCP autostart can launch
the current package. Never claim a terminal command ran unless the user returns
its output.

## Hard Stops

- Do not run any launcher, shell, service, doctor, or update command from this
  skill. A separate exact `$tcx-build` root turn may use only its own trusted
  allowlisted validation and managed lifecycle commands; service lifecycle and
  package refresh remain interactive user-terminal actions.
- Do not scaffold or edit connector code without the current Build-turn grant.
- Do not read raw secrets, call raw broker APIs, approve, cancel, or execute.
- Do not install, recommend, enable, disable, remove, classify, or validate a
  user-owned Codex capability. Those capabilities are bring-your-own-risk and
  remain outside TradingCodex licensing, safety, execution, and audit
  guarantees.
- The OpenBB commands above apply only to the explicit TradingCodex-supported
  integration. They do not authorize managing another user-owned MCP server or
  claim that OpenBB or an upstream provider is free, accurate, licensed for the
  user's purpose, or commercially safe.

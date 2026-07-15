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
- Inspect connectors only through read-only MCP tools such as
  `list_broker_connections`, `get_broker_connection_status`,
  `list_broker_adapter_providers`, `get_broker_capability_profile`,
  `get_broker_instrument_constraints`, `get_connector_build_status`, and
  `get_order_status`, when the current role permits them.

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
5. If managed Build work is requested, give a new root-turn prompt whose exact
   physical first line is `$tcx-build` and whose following line states the
   requested change. Explain that the marker grants no authority beyond the
   current Codex sandbox and TradingCodex policy.

## Interactive User-Terminal Handoff

Give only the command needed for the diagnosed state and label it **run by the
user in the workspace terminal**. Common commands are:

```text
{{TRADINGCODEX_WORKSPACE_LAUNCHER}} service status
{{TRADINGCODEX_WORKSPACE_LAUNCHER}} doctor --layer service
{{TRADINGCODEX_WORKSPACE_LAUNCHER}} doctor --layer mcp
{{TRADINGCODEX_WORKSPACE_LAUNCHER}} update status --json
```

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

---
name: tcx-server
description: Operate TradingCodex server, dashboard, MCP, runtime mode, update status, and read-only broker connector status without build or execution authority.
---

# TCX Server

Use this skill for operate-plane TradingCodex status checks, service recovery, dashboard URL guidance, MCP setup, runtime mode status, update status, and safe broker connector inspection.

## Procedure

1. Read `.tradingcodex/mainagent/server-status.json` and `.tradingcodex/mainagent/session-start.json`.
2. Use `./tcx service status`, `./tcx doctor --layer service`, and `./tcx doctor --layer mcp` when status is stale or unhealthy.
3. Use `./tcx mode status` and `./tcx update status --json` for mode/update posture.
4. Use read-only connector commands and MCP tools for broker profile, capability, instrument constraints, sync state, and order status.
5. If update or build work is requested but build is not enabled, explain that Codex full access plus `tcx mode set build --reason <reason>` is required, or give the terminal command.
6. If startup context reports `service_issue=version_mismatch`, `db_mismatch`, or `port_occupied`, report the issue with service/package versions or DB paths when present, and give the recorded next action before opening or recommending the dashboard. For a stale same-DB TradingCodex service, prefer `./tcx service stop` and Codex restart so MCP autostart can launch the current package.

## Hard Stops

- Do not run `tcx update` unless `update_status.can_self_update=true` and the user explicitly asks.
- Do not scaffold or edit connector code in operate mode.
- Do not read raw secrets, call raw broker APIs, approve, cancel, or execute.

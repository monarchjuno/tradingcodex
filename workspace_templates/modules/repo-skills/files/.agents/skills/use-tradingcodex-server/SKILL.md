---
name: use-tradingcodex-server
description: Operate TradingCodex Server broker connectors and MCP setup. Use when the coordinator needs to attach, register, inspect, validate, troubleshoot, or explain native broker/API connectors across equities, ETFs, options, futures, FX/CFD, crypto, fixed income, funds, or cash without granting execution authority.
---

# Use TradingCodex Server

## Overview

Use this skill to manage TradingCodex itself: startup health, local dashboard access, project MCP setup, native broker connector registration, capability profile review, read-only account sync, order translation preview, and doctor recovery. This skill does not authorize analysis, approval, execution, cancellation, secret access, or direct broker API calls.

## Load References

- For connector/profile JSON shape, read `references/capability-profile.md`.
- For asset-class order fields and support boundaries, read `references/asset-classes.md`.
- For broker family templates, read `references/connector-templates.md`.
- For permission, secret, approval, and execution boundaries, read `references/safety-runbook.md`.
- For failed checks, stale profile, credential, rate-limit, or preview issues, read `references/troubleshooting.md`.

Load only the reference needed for the current task.

## Startup Health And Dashboard

Use this procedure at the start of a new Codex thread and whenever the user asks
to check, fix, show, or open TradingCodex Server.

1. Read `.tradingcodex/mainagent/server-status.json` if it exists.
2. Run `./tcx doctor --layer service`.
3. Run `./tcx doctor --layer mcp` if MCP config, tool availability, autostart,
   or Codex restart state is relevant.
4. If the service is not reachable, run `./tcx service ensure` to start or
   reuse the compatible local service.
5. Verify `http://127.0.0.1:48267/api/health` reports
   `service=tradingcodex`, the current package version, and the current central
   DB path.
6. Open `http://127.0.0.1:48267/` in the Codex in-app browser when browser
   control is available, and make the browser visible to the user. This is a
   required startup action for a new conversation; do not merely say that you
   can open it. If browser control is unavailable, give the dashboard URL and
   state that browser control was unavailable.
7. If MCP config was created, refreshed, missing, or changed, tell the user to
   fully quit and restart Codex, then start a new thread in this workspace
   because Codex may not hot reload project MCP config.
8. If `server-status.json` has `update_status.versions_match=false`, use the
   workspace version as the baseline and align the workspace to the installed
   `tcx` version only when `update_status.workspace_update_allowed=true`.
9. If `update_status.package_update_required_first=true`, do not run workspace
   update with the currently installed `tcx`; tell the user to update the
   TradingCodex package first, then restart Codex and open a new thread.
10. Mention update recommendations only in this new-conversation startup check.
    If the user declines update prompts, write the TradingCodex home preference
    file shown in `update_status.preference_path`, normally
    `~/.tradingcodex/preferences/update.json`, with
    `{"suppress_update_recommendation": true}` and do not recommend automatic
    workspace updates again unless the user changes that flag or explicitly asks.

Do not work around port ownership, version mismatch, or central DB mismatch.
Report those as blocked service compatibility problems.

## Safe Workflow

1. Inspect TradingCodex status and current connector state.
2. List connector templates before registering anything new.
3. Register connectors with `credential_ref` only; never request, read, echo, or store raw keys.
4. Inspect the resulting capability profile and blocked surfaces.
5. Validate profile JSON with `scripts/validate_connector_profile.py` when a profile payload is available.
6. Run health checks and read-only sync before using account, position, or order-read data.
7. Use `preview_order_translation` and `run_order_checks` for readiness; treat preview output as validation only.
8. Use `get_order_status` for local ticket/broker-order reads when asked to inspect execution state.
9. Route approval, status refresh, cancellation, and execution work to the configured approval/execution roles
   instead of naming or widening role authority inside this skill.

## Useful Commands

```bash
./tcx doctor
./tcx doctor --layer service
./tcx doctor --layer mcp
./tcx service ensure
./tcx mcp call list_broker_connector_templates --principal head-manager
./tcx mcp call register_broker_connector --principal head-manager --template <template_id> --broker-id <broker-id> --credential-ref env:<BROKER_REF> --environment <paper|sandbox|testnet>
./tcx mcp call get_broker_capability_profile --principal head-manager --broker-id <broker-id>
./tcx mcp call get_broker_instrument_constraints --principal head-manager --broker-id <broker-id> --symbol <symbol-or-instrument>
./tcx mcp call preview_order_translation --principal head-manager '{"broker_id":"<broker-id>","symbol":"<symbol>","side":"buy","quantity":1,"order_type":"limit","limit_price":100,"time_in_force":"day"}'
./tcx mcp call get_order_status --principal head-manager --ticket-id <ticket-id>
```

## Hard Stops

Stop and report blocked reasons if the user asks the coordinator to:

- add broker MCP servers directly to `.codex/config.toml` or `.codex/agents/*.toml`
- call raw broker APIs or SDKs from shell, hooks, scripts, or ad hoc code
- submit, cancel, replace, transfer, withdraw, create deposit addresses, mutate API keys, perform KYC/account-opening, or handle travel-rule actions
- read or save raw broker credentials, tokens, passwords, seed phrases, or `.env` secrets
- bypass TradingCodex policy, approval, idempotency, adapter, or audit checks

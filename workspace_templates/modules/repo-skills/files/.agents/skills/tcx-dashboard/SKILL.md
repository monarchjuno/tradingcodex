---
name: tcx-dashboard
description: Open the read-only TradingCodex workspace dashboard and summarize current attention items from trusted sources. Use the Codex in-app browser by default, and use an external browser only when the user explicitly asks for one.
---

# TCX Dashboard

Use this skill to open the read-only workspace dashboard for the current attached
workspace and give the user a compact orientation. Open it in the Codex in-app
browser by default. Use an external browser only when the user explicitly asks
for an external browser. Do not turn the dashboard into investment judgment,
workflow execution, or service recovery.

## Trusted Sources

- Start with the TradingCodex session context injected by the hook. Do not reread
  its backing files through the shell.
- Refresh only the requested or stale sections with Head Manager's read-only MCP
  tools, including `get_tradingcodex_status`, `list_research_artifacts`,
  `list_forecasts`, `get_forecast_calibration_report`, `get_portfolio_snapshot`,
  `get_positions`, `list_order_tickets`, `get_order_status`,
  `list_broker_connections`, and `get_broker_connection_status` when available
  and relevant.
- Treat missing, unavailable, or redacted data as unknown. Never convert it to a
  zero balance, empty portfolio, healthy status, or completed workflow.

## Procedure

1. Identify the current attached workspace, the trusted viewer base URL, and the
   user's requested destination. Treat an unqualified `$tcx-dashboard` invocation
   as an explicit request to open the dashboard.
2. Choose the destination from the request: use `#/library` for research and
   artifacts, `#/skills` for skills, and `#/system` for runtime, broker, MCP, or
   permission posture. Use the viewer base URL when no narrower destination is
   requested.
3. Open the selected URL in the Codex in-app browser by default. If and only if
   the user explicitly requests an external browser, use the available external
   Browser Use surface instead. Never use shell commands such as `open`,
   `xdg-open`, or `start` to launch a browser.
4. If the requested browser surface is unavailable, return the exact clickable
   viewer URL and state the surface limitation. Do not silently switch from the
   in-app browser to an external browser or vice versa.
5. Check the smallest useful set across recent research, portfolio/orders,
   forecasts, pending permissions, and system/broker posture. Surface attention
   items first. Include only explicit states such as blocked,
   waiting, stale, failed, unhealthy, pending, uncertain, or incompatible, with
   their recorded timestamps or as-of posture when present.
6. Summarize the remaining available sections compactly. Preserve canonical
   status names and distinguish recorded facts from interpretation.
7. Describe something as changed only when a trusted comparison, version, event,
   or timestamp establishes the change. Otherwise label it recent or current.
8. Report which browser surface and viewer destination were actually opened. Do
   not claim success unless the browser action succeeded.
9. If service status is missing, stale, or unhealthy, do not open a stale or
   incompatible viewer URL. Stop at that boundary and route recovery to
   `$tcx-server`. If the user wants a new
   investment judgment, route that separate task to `$tcx-workflow`.

## Response Shape

Use only sections supported by returned data:

- **Needs attention**: blockers, stale state, pending permissions, uncertain
  orders, or unhealthy connections.
- **Recent work**: accepted research, reports, forecasts, and recorded workflow
  artifacts with source/as-of posture.
- **Portfolio and orders**: current recorded snapshot, positions, and ticket
  status without recommendations.
- **System**: workspace, service, broker, MCP, and update posture.
- **Inspect next**: one or two relevant viewer destinations or a separate exact
  skill entrypoint.

## Hard Stops

- Do not call `begin_analysis_run`, dispatch a role, create an artifact, or
  perform fresh investment analysis.
- Do not draft, approve, submit, cancel, retry, or reconcile an order.
- Do not mutate workspace, skill, policy, permission, broker, connector, or
  service state.
- Do not use shell, raw database access, raw broker APIs, or secrets. Browser
  opening must use the selected Codex browser surface.
- Do not expose raw reasoning, tool payloads, credential references, or
  unsanitized workspace content.

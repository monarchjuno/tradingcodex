---
name: tcx-source-gate
description: Route external investment research data and preserve concise SourceSnapshot/Dataset provenance. Use when a fixed-role TradingCodex analyst needs external facts, documents, prices, fundamentals, or time series.
---

# Data Source Routing

Use this order for each missing fact or series:

1. Reuse a relevant SourceSnapshot or Dataset already supplied for the work.
2. Use one relevant enabled user Skill, Plugin, or MCP capability.
3. Use the optional direct OpenBB MCP when it is projected for this role.
4. Research original public records: company IR and filings for companies, exchanges for prices, central banks/statistics agencies for macro data, and regulators for regulatory facts.
5. Use another reliable web source.
6. State the remaining data gap clearly.

Name the provider when a tool supports one. Do not call the same source again with unchanged inputs. When a source partially succeeds, retain its valid result and ask only the missing field, identifier, or period from the next source. This is an operating procedure, not a service-side trust rating or a guarantee about third-party capabilities.

Use the current task's callable tool surface rather than treating a static
inventory as proof. Make the smallest relevant public, read-only call. Paid or
cost-unknown access requires user approval.

For conclusion-relevant structured or point-in-time evidence, preserve enough
context to interpret it: issuer identity and instrument/venue; unit, currency,
and timezone; price or series raw-versus-adjusted posture and adjustment policy;
filing accepted/published time, period, and amendment/restatement posture; and
historical macro first-release/vintage versus current-revision posture. State
the as-of/known-at/freshness posture and any empty, partial, stale, rate-limit,
authentication, or entitlement warning. Surface material source conflicts and
the remaining coverage gap. A narrow fact does not need this full context.

If the selected route is direct OpenBB MCP, read
[`references/openbb-mcp.md`](references/openbb-mcp.md) before the call. Do not
load it for another route.

Record every used external source with `record_source_snapshot`; preserve its provider, locator, as-of/known-at posture, payload hash, warnings, and a necessary excerpt. Create a Dataset only for reusable rows, time series, OHLCV, or financial tables; retain all used rows and bind the resulting Snapshot/Dataset IDs in the ResearchArtifact. A document, filing, news item, or qualitative source normally needs only a SourceSnapshot.

Do not install, configure, classify, proxy, approve, or audit user capabilities or OpenBB. Never handle credential values. OpenBB remains optional, and its package, provider, and data terms remain the user's responsibility.

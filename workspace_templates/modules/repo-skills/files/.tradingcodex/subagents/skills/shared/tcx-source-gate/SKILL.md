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

Complete the reusable Snapshot/Dataset check before an external network call.
For structured prices, OHLCV, fundamentals, estimates, or macro series, try one
relevant callable direct OpenBB tool before public web or direct HTTP unless the
user named another provider or a clearly relevant enabled capability. Generic
web, browser, and shell HTTP access are public-web fallbacks, not step 2 user
capabilities.

Name the provider when a tool supports one. Do not call the same source again with unchanged inputs. When a source partially succeeds, retain its valid result and ask only the missing field, identifier, or period from the next source. This is an operating procedure, not a service-side trust rating or a guarantee about third-party capabilities.

Judge evidence against the claim and intended use, not a provider label. Prefer
an original public record when exact legal, regulatory, contractual,
accounting, filing, or official-policy status is material. Otherwise use
current attributable evidence for the fields and periods it supports. Treat
OpenBB as access to its returned provider, not as a low-trust source class;
verify provider, identifiers, period, units, adjustments, and coverage.
Credible institutional data and reputable secondary reporting may support a
conclusion when they are within the source's competence and have no unresolved
material conflict. Secondary does not mean screen-only.

Corroborate in proportion to consequence. Independently check a
conclusion-driving claim when it is surprising, disputed, transformed from raw
data, or weakly attributed; do not require a fixed source count for ordinary
well-supported facts. One missing, stale, or ambiguous field invalidates only
dependent claims. Use `evidence_readiness: decision-grade` when every
conclusion-driving claim has fit-for-purpose support, the relevant market
anchor is current, and material conflicts and gaps are explicit.
Use `evidence_readiness: screen` or `insufficient` only when a material unresolved gap
prevents responsible decision support, not merely because a primary source is
absent. Record `action_readiness` and confidence separately from evidence
readiness; evidence quality alone never creates order or execution authority.

Judge freshness relative to the requested as-of, the source's publication or
observation cadence, the instrument or venue session when applicable, and the
claim being made. The latest completed period can be the current usable anchor
before a new period exists; expected absence before the next observation or
release is not a data gap. Preserve offset-free provider timestamps as
ambiguous raw values rather than adding a timezone, and convert epoch values
exactly once. A timing ambiguity limits time-sensitive claims, not unrelated
content from the same response.

Use the current task's callable tool surface rather than treating a static
inventory as proof. Make the smallest relevant public, read-only call. Paid or
cost-unknown access requires user approval.

If a named tool is deferred, use one names-only exact-name lookup, then inspect
the selected exact name's schema once. Never print full tool records, scan
descriptions, or repeat a schema lookup.

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

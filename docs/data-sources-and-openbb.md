# Data Sources And OpenBB

This page is the canonical contract for external research sources. It does not
cover broker connectivity or execution; see
[safety-policy-and-execution.md](./safety-policy-and-execution.md) for those
boundaries.

## Source routing

The shared `tcx-source-gate` skill gives an evidence-producing role this order
for each missing fact or series:

1. Reuse a relevant SourceSnapshot or Dataset already supplied for the work.
2. Use one relevant enabled user Skill, Plugin, or MCP capability.
3. Use the optional direct OpenBB MCP if it is projected for the role.
4. Research original public records: company IR and filings for companies,
   exchanges for prices, central banks or statistics agencies for macro data,
   and regulators for regulatory facts.
5. Use another reliable web source.
6. State the remaining data gap.

This is a short operating procedure, not a service-side source-rating system or
a guarantee about a third party. A role names the provider where possible, does
not repeat an unchanged request to the same source, and keeps a partial result
while asking the next source only for the missing field, identifier, or period.
TradingCodex does not install, classify, proxy, approve, or audit a user-owned
capability. The normal research safety boundaries still apply: never expose a
secret or mix research retrieval with account, order, or other mutation work.

The procedure adds point-in-time context only when structured or historical
evidence matters to a conclusion. It preserves issuer identity and
instrument/venue, unit/currency/timezone, raw-versus-adjusted price policy,
filing timing/period/amendment posture, and macro first-release or vintage
posture versus a current revision. It also makes as-of/known-at/freshness,
empty/partial/stale/authentication/entitlement/rate-limit warnings, material
conflicts, and remaining coverage gaps visible. Narrow facts do not carry a
full evidence checklist.

## Evidence records

Every used external document or response is recorded as a content-addressed
`SourceSnapshot`. Its provider, locator, as-of and known-at posture, hashes,
warnings, and necessary excerpt preserve provenance for filings, news, IR
material, HTML/PDF extracts, and other unstructured sources.

A `Dataset` is optional. Create one only when a reusable table, time series,
OHLCV set, or financial table is used. Preserve every row used in the analysis;
the immutable Dataset manifest binds its payload and source snapshots. A
`ResearchArtifact` cites the exact SourceSnapshot and Dataset IDs it consumed.
The resulting flow is:

```text
external source → SourceSnapshot → Dataset (only for reusable structured rows) → ResearchArtifact
```

There is no separate external-call receipt state machine and no document-blob
store. Existing Snapshot payload/locator/hash data and a necessary excerpt are
the current retention boundary. Add a source-specific adapter only when a real,
repeatable pipeline requires one; TradingCodex does not own adapters for SEC,
Treasury, BLS, ECB, World Bank, CFTC, Bank of Canada, or similar sources.

## Optional OpenBB MCP

OpenBB is optional and is not installed or started by `tcx attach` or `tcx
update`. When enabled, generated configuration projects the official upstream
MCP directly to fundamental, technical, news, macro, instrument, and valuation
roles only. Head Manager, portfolio, risk, and judgment roles do not receive it.

```toml
[mcp_servers.openbb]
command = "uvx"
args = ["--from", "openbb-mcp-server", "--with", "openbb", "openbb-mcp", "--transport", "stdio", "--default-categories", "admin"]
enabled = true
required = false
env_vars = ["FMP_API_KEY"]
```

`uvx` resolves the upstream packages only when Codex actually uses the MCP.
TradingCodex never wraps, provisions, supervises, validates, or proxies that
server. The configuration stores environment-variable *names* only; values are
inherited from the process that starts Codex and must not appear in workspace
files, prompts, APIs, MCP output, artifacts, or logs.

Only a role that actually selects this direct route reads the short OpenBB
procedure. It discovers the smallest needed upstream route, names and verifies
the provider when available, verifies returned identifiers/venue/time posture
and data units or adjustments, and treats access or empty-result warnings as
coverage gaps. A partial response remains usable while another source fills
only the missing coverage. Used responses become SourceSnapshots immediately;
reused structured rows also become Datasets. The procedure deliberately avoids
tool-name or version coupling.

From a workspace terminal, use only the small projection controls:

```bash
./tcx data-sources openbb enable --env-var FMP_API_KEY
./tcx data-sources openbb status
./tcx data-sources openbb env add OTHER_PROVIDER_KEY
./tcx data-sources openbb disable
./tcx update --skip-refresh --no-doctor
```

Restart Codex after changing the projection or inherited environment. OpenBB,
its providers, and their data remain third-party services: package licensing,
provider terms, entitlement, cost, availability, accuracy, and data-use duties
are the user's responsibility. In particular, optional direct use does not by
itself settle OpenBB AGPL or downstream data-license obligations. See the
[upstream OpenBB license guidance](https://docs.openbb.co/odp/python/faqs/license).

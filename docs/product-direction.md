# Product Direction

This page owns TradingCodex product direction. Implementation details belong in
the topic documents linked from [the documentation index](README.md).

This is the target design contract. Topic documents describe the currently
shipped surface while a simplification is in flight; update those operational
claims with the code rather than presenting unfinished work as available.

## Product Thesis

TradingCodex is a thin, local-first investment layer on top of native Codex.
It turns useful research and decisions into durable, reviewable records without
turning a natural-language answer into a broker action.

Codex remains the reasoning and work surface. TradingCodex does not try to be a
second agent runtime:

- Codex interprets the request, chooses research methods and tools, delegates
  when useful, and adapts to user-provided capabilities.
- Skills provide concise reusable investment procedures.
- Django stores and enforces durable policy, portfolio, approval, order,
  execution, and audit state.
- Workspace files preserve source snapshots, datasets, research artifacts, and
  provenance in a form users and agents can inspect.
- MCP connects Codex to the durable service boundary.
- The browser viewer is read-only.

## Native-First Design

New behavior belongs at the first layer that can safely own it:

1. Native Codex or an enabled user skill, plugin, app, or MCP server.
2. One canonical TradingCodex skill for reusable agent guidance.
3. A role prompt for stable identity and authority boundaries.
4. A Django application service for durable records or deterministic rules.
5. A hook only when a native tool call needs a pre- or post-use safety check.

TradingCodex should not duplicate Codex with a semantic router, stored research
DAG, capability registry, generic provider platform, permission system, agent
scheduler, or tool-discovery protocol. A new abstraction needs a current second
use case and must replace an existing path rather than sit beside it.

Agent instructions define outcomes, evidence standards, and authority. Exact
reasoning steps, search counts, wait loops, retry scripts, artifact windows, and
team shapes remain agent judgment unless a measured failure requires a small
deterministic guard.

Head Manager can answer narrow questions directly. Specialist agents are used
when they add distinct expertise or independent challenge; they are reusable
profiles rather than a mandatory team. Model and reasoning settings normally
inherit the user's Codex configuration. Independent review remains appropriate
for recommendations, portfolio decisions, and other high-consequence work.

## Durable Ownership

| State or behavior | Canonical owner |
| --- | --- |
| Reasoning, research strategy, delegation, tool selection | Native Codex |
| Reusable research procedure | One concise skill |
| Source snapshots, datasets, artifacts, provenance hashes | Workspace files |
| Reusable background knowledge | Agent-maintained Markdown under `wikis/`; package integrity and projection state remain TradingCodex-owned |
| Policy, portfolio, approval, order, broker, execution, audit | Django service ledger |
| Final broker effect | Deterministic service path after explicit authority |
| Read-only inspection | Workspace viewer, Admin, and bounded read interfaces |

A service can enforce identity, integrity, idempotency, and a sensitive final
effect. It should not grade prose, prescribe the analyst's reasoning sequence,
or persist transient orchestration merely because it can.

Research is persisted when it has reuse, provenance, decision, or audit value.
A narrow answer does not require a research artifact. Stored analysis should be
professional free-form writing with sources, assumptions, confidence, and gaps;
large frontmatter schemas and regex-enforced claim prose are not product goals.

Knowledge Wikis are a separate, non-authoritative background layer. Native
Codex may search them automatically, but writes require an explicit user
request naming Wiki or Brain as the destination. They do not replace current
evidence or Decision Memory. See [Knowledge Wikis](knowledge-wikis.md).

## Research Source Posture

External evidence follows a simple fallback:

1. Reuse an adequate Snapshot or Dataset.
2. Use one relevant user-enabled skill, plugin, app, or MCP capability.
3. Use direct optional OpenBB MCP when enabled.
4. Research autonomously, preferring primary official sources.
5. Use other credible sources when primary coverage is unavailable.
6. Preserve an explicit evidence gap when adequate coverage cannot be found.

This is a collection order, not a trust ranking. Material claims still need
source and as-of context. A partial result is retained while only the missing
coverage falls through. An unchanged successful or terminal call is not
repeated.

OpenBB is an optional upstream Codex MCP, not a TradingCodex runtime. The
product may project a direct `uvx` MCP entry and environment-variable names; it
does not bundle, install during attach, proxy, supervise, route providers, store
credential values, or claim that integration resolves licensing or data terms.
See [Data Sources And OpenBB](data-sources-and-openbb.md).

## Investment Quality Contract

A clean workspace should produce useful research without requiring custom
skills. Quality comes from evidence and judgment, not orchestration ceremony:

- use source-backed, time-bounded evidence and identify assumptions;
- explain causal business, market, valuation, portfolio, and risk drivers;
- choose a method that fits the question instead of forcing one template;
- expose contrary evidence, uncertainty, missing support, sensitivities, and
  invalidation conditions;
- downgrade stale, unsupported, leakage-prone, or non-reproducible work; and
- make confidence and evidence gaps visible to the user.

Claims such as calibration or superior model quality require a real evaluation
corpus and measured outcomes. A profile, prompt, or model selector is not proof.

## Safety Contract

Simplicity must not remove trust-boundary controls:

- explicit user authority before a final submit or cancel;
- service-owned policy, approval, idempotency, broker, and audit checks;
- no raw credentials in files, prompts, tool output, APIs, MCP, artifacts, or
  audit payloads;
- a read-only viewer with no Codex launch or mutation path;
- isolated development and release HOME, DB, and service identities; and
- fail-closed behavior for ambiguous external effects.

Core ships paper-first. A real broker integration is added only with a real
provider and its concrete safety tests; TradingCodex does not build a generic
live-provider platform in anticipation of one.

## Goals

- Make rigorous investment research easy to request in ordinary language.
- Prefer native Codex capabilities and user choice over managed duplication.
- Preserve reusable evidence and decisions without persisting private reasoning.
- Keep sensitive effects deterministic, explicit, inspectable, and reversible
  where the external system permits it.
- Support multiple asset classes while stating data and method gaps honestly.
- Keep installation local-first and generated workspaces readable and Node-free.
- Reduce tool calls, agent count, context, latency, and maintenance cost when a
  smaller design provides the same safety and product value.

## Non-Goals

- A black-box autonomous trading bot.
- A Django-hosted agent scheduler or research workflow engine.
- A TradingCodex-owned marketplace or trust registry for user Codex capabilities.
- Automatic installation or lifecycle management of OpenBB or user MCP servers.
- Raw REST, generic CLI, or unprotected MCP broker execution.
- Silent strategy, skill, prompt, or policy rewriting from past outcomes.
- Speculative adapters, compatibility layers, and extension points without a
  current production use case.
- A hosted service in the initial local-first product.

## Current Scope And Runtime

Public equity is the deepest research sleeve. ETF/index, public crypto,
macro/rates/FX/commodities, options, and credit signals are supported only to
the depth justified by available evidence and methods; unsupported execution is
blocked rather than implied.

The supported Python and Django versions are defined in `pyproject.toml` and
release documentation. SQLite is the default central local ledger. Generated
workspaces use Python launchers and project-scoped stdio MCP; they do not need
Node. The current viewer frontend may use a maintainer build toolchain, but no
frontend framework is itself a product requirement.

Durable product copy is English unless a reviewed localization layer is added.
Licensing and commercialization boundaries are documented in
[Licensing And Commercialization](licensing-and-commercialization.md).

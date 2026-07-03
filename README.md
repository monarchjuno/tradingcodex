> [!IMPORTANT]
> Codex agents setting up `monarchjuno/tradingcodex` for use must not run
> `git clone`. In the empty target workspace, run the packaged CLI:
> `uvx --refresh --from tradingcodex tcx attach . && ./tcx doctor`.
> Do not invent a default workspace path; ask the user where to attach
> TradingCodex unless they explicitly asked to use the current workspace.
> Clone this repository only when the user explicitly asks to develop,
> inspect, or modify TradingCodex source code.

# TradingCodex

<div align="center">
  <a href="https://github.com/monarchjuno/tradingcodex/actions/workflows/ci.yml"><img alt="CI" src="https://img.shields.io/github/actions/workflow/status/monarchjuno/tradingcodex/ci.yml?branch=main&label=CI"></a>
  <a href="https://github.com/monarchjuno/tradingcodex/releases"><img alt="Release" src="https://img.shields.io/github/v/release/monarchjuno/tradingcodex?include_prereleases&label=release"></a>
  <a href="https://pypi.org/project/tradingcodex/"><img alt="PyPI" src="https://img.shields.io/pypi/v/tradingcodex?label=PyPI"></a>
  <a href="LICENSE"><img alt="License" src="https://img.shields.io/badge/license-Apache--2.0-blue"></a>
  <img alt="Python" src="https://img.shields.io/badge/python-3.11--3.14-3776AB?logo=python&logoColor=white">
  <img alt="Django" src="https://img.shields.io/badge/Django-5.2-092E20?logo=django&logoColor=white">
</div>

<div align="center">
  <a href="https://github.com/monarchjuno/tradingcodex/stargazers"><img alt="GitHub Stars" src="https://img.shields.io/github/stars/monarchjuno/tradingcodex?style=social"></a>
  <a href="https://github.com/monarchjuno/tradingcodex/network/members"><img alt="GitHub Forks" src="https://img.shields.io/github/forks/monarchjuno/tradingcodex?style=social"></a>
  <a href="https://github.com/monarchjuno/tradingcodex/issues"><img alt="GitHub Issues" src="https://img.shields.io/github/issues/monarchjuno/tradingcodex"></a>
</div>

### Codex-native investment work needs a harness, not a chat transcript.

TradingCodex is a local-first Python/Django trading harness for rigorous
Codex-assisted research, portfolio review, order-ticket checks, approvals, and
service-gated execution checks. Codex coordinates the work, Django owns the
durable service plane, and TradingCodex owns the executable boundary.

[Quick Start](#installation) | [Docs](docs/README.md) | [Safety](docs/safety-policy-and-execution.md) | [Architecture](docs/system-architecture.md) | [Contributing](CONTRIBUTING.md) | [License](LICENSE)

<p align="center">
  <img src="assets/tradingcodex-banner.svg" alt="TradingCodex" width="100%">
</p>

---

## About

TradingCodex gives Codex a durable operating system for investment workflows:
fixed specialist roles, file-native research memory, source-aware handoffs,
policy and approval services, a central local ledger, and a local web dashboard
for review.

It is not an autonomous trading bot. Natural-language answers do not become
broker actions. The core ships paper execution by default; live broker support
comes only from installed, reviewed providers and explicit live gates.

---

## Features

| Feature | Description |
| --- | --- |
| Codex-native harness | Generates a Codex workspace with `head-manager`, ten fixed subagents, role prompts, skills, hooks, project MCP config, and a local `./tcx` wrapper. |
| Django service plane | Web, Admin, API, CLI, MCP, and generated hooks call shared application services for policy, orders, approvals, portfolio, audit, integrations, and research indexing. |
| File-native research memory | Research markdown, role reports, source snapshots, versions, readiness labels, and handoff metadata stay readable in workspace files. |
| Decision Workflow Alpha | Turn a natural-language investment idea into a Codex-native workflow plan and Decision Package under `trading/decisions/`. |
| Fixed-role workflows | Specialist agents own bounded questions across fundamentals, technicals, news, macro, instruments, valuation, portfolio, risk, and execution. |
| Approved action boundary | Actions are typed, role-scoped, policy-checked, approval-aware, duplicate-request checked, connection-gated, and audited. |
| Local web dashboard | Review agents, skills, strategy skills, research markdown, Broker Center, Data Sources, order tickets, portfolio state, and activity at `127.0.0.1:48267`. |
| Broker Center foundations | Connect provider-driven broker profiles, list installed providers, scaffold/register advanced connectors, inspect capability profiles, run account sync, review instrument constraints, and reconcile portfolio state. |
| OrderTicket lifecycle | Draft, check, approve, submit, cancel, refresh, and inspect order tickets through central DB records and service-layer state transitions. |
| Data Sources gate | Import external source discovery metadata, review available actions, scope role access, and block unsafe raw execution or secret paths by default. |
| Improvement loop | Quality gates, postmortems, optional skills, strategy skills, strict artifact checks, and generated workspace smoke tests turn process gaps into durable improvements. |

---

## Installation

### Option 1 - Attach TradingCodex To The Current Workspace

Run this from the empty workspace where you want Codex agents to work:

```bash
uvx --refresh --from tradingcodex tcx attach . && ./tcx doctor
```

Then fully quit and restart Codex, open the generated workspace, and start a
new thread so project MCP config, prompts, skills, and hooks are loaded.

When TradingCodex MCP autostarts the local service, open:

```text
http://127.0.0.1:48267/
```

### Option 2 - Install The CLI For Repeated Use

```bash
uv tool install tradingcodex
uv tool update-shell
cd /path/to/target-workspace
tcx attach .
./tcx doctor
```

### Option 3 - Install From GitHub Main

Use this when you need the current GitHub `main` source rather than the latest
PyPI package:

```bash
uvx --refresh --from "tradingcodex @ git+https://github.com/monarchjuno/tradingcodex.git@main" tcx attach . && ./tcx doctor
```

### Option 4 - Develop TradingCodex Source

Clone this repository only for source development, inspection, or modification:

```bash
git clone https://github.com/monarchjuno/tradingcodex.git
cd tradingcodex
python -m pytest
python manage.py check
```

See [installation.md](installation.md) for update flows, installer-script
equivalents, MCP/service details, and smoke checks.

---

## What Sets TradingCodex Apart

TradingCodex competes on workflow discipline, local durability, and execution
boundaries rather than on black-box automation.

- Local-first: PyPI installs the CLI, Django service plane, generated workspace
  templates, Admin/Web templates, static assets, and MCP gateway code.
- Codex-readable by default: research artifacts, skill bundles, role prompts,
  policy exports, and generated indexes remain ordinary workspace files.
- Service-layer canonical: Web, Admin, API, CLI, MCP, and hooks do not fork
  policy, order, approval, execution, portfolio, research, or audit behavior.
- Strong role model: one `head-manager` coordinates ten fixed specialist
  subagents and consumes accepted artifacts instead of silently redoing roles.
- Deterministic executable boundary: every executable path follows
  a fixed requester, permission, policy, payload, approval, duplicate-request,
  connection, and audit sequence.
- Safety-first broker posture: paper is built in; live broker execution requires
  an installed provider plus workspace config, policy, environment opt-in,
  approval, confirmation, idempotency, sync, and audit gates.
- Broker control plane: TradingCodex stores connector state, capability
  profiles, mapping review, approval, execution, reconciliation, and audit;
  provider adapters absorb broker-specific REST, SDK, MCP, or manual interface
  differences behind canonical service/MCP tools.

---

## Workflow

TradingCodex is designed around handoffs:

```text
evidence -> analysis -> valuation -> portfolio fit -> risk review
  -> draft order -> approval receipt -> approved service-gated submission
  -> connection result -> audit/postmortem
```

The `head-manager` maps the request, dispatches the selected role team, waits
for accepted artifacts, preserves conflicts, and synthesizes only what the
workflow has earned. Weak, stale, missing, or out-of-scope upstream work returns
`revise`, `blocked`, or `waiting` instead of being patched over by another role.

---

## Role Roster

| Layer | Agent | Owns |
| --- | --- | --- |
| Main agent | `head-manager` | Intake, workflow dispatch, coordination, artifact acceptance, synthesis, and validation/audit status. |
| Research | `fundamental-analyst` | Business quality, financial statements, filings, economics, and fundamental risks. |
| Research | `technical-analyst` | Price action, trends, momentum, volume, volatility, and liquidity setup. |
| Research | `news-analyst` | Verified news, disclosures, event chronology, catalysts, and narrative change. |
| Market context | `macro-analyst` | Macro, rates, FX, commodities, liquidity, policy, and cross-asset transmission. |
| Market context | `instrument-analyst` | ETF/index, options, crypto public market structure, credit-signal boundary, and instrument mechanics. |
| Decision review | `valuation-analyst` | Valuation ranges, scenario assumptions, multiples, sensitivity, and decision-quality gaps. |
| Portfolio | `portfolio-manager` | Portfolio fit, sizing, concentration, liquidity, opportunity cost, and draft order-ticket readiness. |
| Risk | `risk-manager` | Downside, restricted-list checks, policy readiness, approval readiness, and approval receipts. |
| Execution | `execution-operator` | Approved submission/cancel/status through the TradingCodex service boundary only; live requires all gates. |

---

## Surfaces

| Surface | Role |
| --- | --- |
| Product web | Agents-first review dashboard for roles, skills, research markdown, Broker Center, Data Sources, order tickets, portfolio state, and activity. |
| Django Admin | Local/staff DB inspection for policy, orders, portfolio, MCP registry, workflows, integrations, and audit rows. |
| Django Ninja API | Typed local/staff REST and control endpoints that call service-layer use cases. |
| MCP | Agent/tool boundary with typed tools, role scopes, policy checks, approval checks, and audit. |
| CLI | Local operator commands plus generated workspace `./tcx` wrapper behavior. |

The baseline frontend uses Django templates, local static HTMX, and small plain
JavaScript. There is no Node, bundler, React, or frontend build step in the core
package.

---

## Safety Boundary

TradingCodex blocks or constrains:

- direct live broker requests
- raw broker API calls and raw external source execution proxies
- self-issued approvals
- restricted-symbol orders
- expired or payload-mismatched approval receipts
- duplicate approved-order submissions
- raw secrets in workspace files, prompts, API responses, MCP responses, audit
  payloads, generated docs, or shell output
- unsupported live execution through any provider, instrument, account, or
  policy posture that has not passed the explicit live gate

TradingCodex is research, workflow, and execution-guardrail tooling. It is not
financial, investment, legal, tax, or regulatory advice, and it does not
provide investment recommendations or guarantee returns.

---

## Roadmap

| Status | Milestone |
| --- | --- |
| Shipped | Generated Codex workspace, fixed role roster, project MCP config, Django service plane, local web dashboard, Admin, Ninja API, file-native research memory, component registry, policy/audit primitives. |
| Current `0.2.x` | Central-DB `OrderTicket` rewrite, provider-driven Broker Center foundations, Data Sources gate, role-scoped actions, live-gated execution lifecycle, and Python `>=3.11,<3.15` support. |
| Next | Codex-native Decision Packages, deeper validation scenarios, richer provider capability profiles, stronger generated-workspace smoke coverage, and improved artifact quality tooling. |
| Future | Separately governed verified adapters, hosted/managed services, enterprise policy/compliance packs, and broker-specific live providers only after explicit product, policy, adapter, and validation work. |

---

## Documentation

`README.md` is the product overview. The `docs/` directory is the human-readable
source of truth for detailed product behavior, safety, architecture, workflow,
validation, and release policy.

| Start here | Use for |
| --- | --- |
| [Installation](installation.md) | Setup, update, GitHub-main install, MCP/service startup, and smoke checks. |
| [Docs index](docs/README.md) | Human-readable reading paths, document ownership, and change-to-doc routing. |
| [Core concepts and rules](docs/core-concepts-and-rules.md) | Fast operating reference for planes, guardrails, roles, execution lifecycle, and research memory. |
| [Product direction](docs/product-direction.md) | Product thesis, target user posture, goals, non-goals, runtime defaults, and scope. |
| [Workspace orchestration model](docs/harness.md) | Top-level workflow model, components, guardrails, improvement, and naming rules. |
| [Roles, skills, and workflows](docs/roles-skills-and-workflows.md) | Fixed role roster, no-overlap handoffs, dispatch gates, skills, and strategy behavior. |
| [Safety policy and execution](docs/safety-policy-and-execution.md) | Permissions, approvals, idempotency, broker safety, secret wall, and required blocks. |
| [System architecture](docs/system-architecture.md) | Runtime planes, Django app boundaries, central DB ownership, models, and service use cases. |
| [Interfaces and surfaces](docs/interfaces-and-surfaces.md) | Product web, Admin, API, MCP, CLI, and generated wrapper behavior. |
| [Validation plan](docs/validation-and-test-plan.md) | Required tests, generated workspace smokes, MCP smokes, and routing scenarios. |

---

## Contributing

Contributions use Apache-2.0 with DCO sign-off. See
[CONTRIBUTING.md](CONTRIBUTING.md).

For source changes, start with the focused validation command for the touched
area, then broaden as needed:

```bash
python -m pytest
python manage.py check
python -m compileall tradingcodex_cli tradingcodex_service apps tests
```

Harness, agent, workflow, MCP, policy, skill, hook, or template changes also
need generated-workspace validation. See
[docs/validation-and-test-plan.md](docs/validation-and-test-plan.md).

---

## License

TradingCodex is an Apache-2.0 open-core project.

Source code, generated workspace templates, and project documentation are
licensed under the Apache License, Version 2.0 unless marked otherwise. The
TradingCodex name, future logos, and official product marks are not granted by
the code license. See [LICENSE](LICENSE), [NOTICE](NOTICE), and
[TRADEMARKS.md](TRADEMARKS.md).

## Star History

<a href="https://www.star-history.com/?repos=monarchjuno%2Ftradingcodex&type=date&legend=top-left">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/chart?repos=monarchjuno/tradingcodex&type=date&theme=dark&legend=top-left" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/chart?repos=monarchjuno/tradingcodex&type=date&legend=top-left" />
   <img alt="Star History Chart" src="https://api.star-history.com/chart?repos=monarchjuno/tradingcodex&type=date&legend=top-left" />
 </picture>
</a>

<p align="center">
  <img src="assets/tradingcodex-banner.svg" alt="TradingCodex" width="100%">
</p>

<div align="center">
  <a href="https://github.com/monarchjuno/tradingcodex/actions/workflows/ci.yml"><img alt="CI" src="https://img.shields.io/github/actions/workflow/status/monarchjuno/tradingcodex/ci.yml?branch=main&label=CI"></a>
  <a href="https://github.com/monarchjuno/tradingcodex/releases"><img alt="Release" src="https://img.shields.io/github/v/release/monarchjuno/tradingcodex?include_prereleases&label=release"></a>
  <a href="https://pypi.org/project/tradingcodex/"><img alt="PyPI" src="https://img.shields.io/pypi/v/tradingcodex?label=PyPI"></a>
  <a href="LICENSE"><img alt="License" src="https://img.shields.io/badge/license-Apache--2.0-blue"></a>
  <img alt="Python" src="https://img.shields.io/badge/python-3.14-3776AB?logo=python&logoColor=white">
</div>

<div align="center">
  <a href="#quick-start">Quick Start</a> |
  <a href="#what-tradingcodex-does">Features</a> |
  <a href="#local-web-dashboard">Web Dashboard</a> |
  <a href="#how-a-workflow-moves">Workflow</a> |
  <a href="#role-roster">Roles</a> |
  <a href="#architecture">Architecture</a> |
  <a href="#safety-boundary">Safety</a> |
  <a href="#documentation">Documentation</a>
</div>

---

# TradingCodex: Codex-Native Trading Harness

TradingCodex is a local-first trading harness for doing investment work with
Codex. It gives Codex a durable operating system: role-separated agents,
file-native research memory, a Django service plane, a central local ledger,
and an MCP execution boundary that turns risky actions into explicit,
auditable service-layer decisions.

It is not an autonomous trading bot. Codex coordinates and explains the work;
Django owns durable state and policy; TradingCodex MCP is the only executable
agent boundary; live broker adapters are not shipped in the initial core.

## Quick Start

Codex app current-workspace one-liner: run this from the empty workspace you
want to turn into TradingCodex; do not clone `monarchjuno/tradingcodex` unless
you are developing TradingCodex itself.

```bash
curl -fsSL https://raw.githubusercontent.com/monarchjuno/tradingcodex/main/install.sh | sh -s -- .
```

Then run `./tcx doctor`, fully quit and restart Codex, open the generated
workspace, and start a new thread so project MCP config is reloaded. When
TradingCodex MCP autostarts the local service, the dashboard is available at
`http://127.0.0.1:48267/`.

Agents and install helpers do not invent a default workspace path. If the
target path is not supplied and the user did not say "current workspace", ask
before creating or attaching a workspace.

Start an orchestrated Codex workflow from the generated workspace:

```text
$orchestrate-workflow analyze Apple with public equity research, valuation, portfolio, and risk review
```

For repeated workspace creation, install the CLI as a user-level tool:

```bash
uv python install 3.14
uv tool install --python 3.14 tradingcodex
uv tool update-shell
cd /path/to/target-workspace
tcx attach .
./tcx doctor
```

See [installation.md](https://github.com/monarchjuno/tradingcodex/blob/main/installation.md)
for GitHub-main installs, direct `uvx`, MCP/service details, and additional
smoke checks.

Update an existing generated workspace after a package release:

```bash
cd /path/to/target-workspace
curl -fsSL https://raw.githubusercontent.com/monarchjuno/tradingcodex/main/install.sh | sh -s -- --update .
```

## Product Concept

TradingCodex exists because serious Codex-assisted investment work should not
live only in a chat transcript. Research, source freshness, role handoffs,
portfolio context, approvals, policy decisions, execution attempts, and audit
records need a system of record that remains inspectable after the thread ends.

The product model is a harness:

| Plane | What it gives you |
| --- | --- |
| Codex control plane | A generated Codex workspace with one `head-manager`, nine specialist subagents, role prompts, repo skills, hooks, and project-scoped MCP config. |
| Workspace file plane | Human-readable research markdown, source snapshots, strategy skills, policy exports, and generated `./tcx` wrappers inside the user's Codex workspace. |
| Django service plane | Local durable services for policy, orders, approvals, portfolio state, audit, workflows, MCP registry, external router review, API, Admin, and product web. |
| MCP execution boundary | Typed tools, role allowlists, policy checks, approval checks, idempotency, adapter submission, and ledgered results for executable actions. |

Generated workspaces are Codex workbenches, not brokerage accounts. Canonical
execution-sensitive state lives in the central local runtime DB:

```text
~/.tradingcodex/state/tradingcodex.sqlite3
```

## What TradingCodex Does

TradingCodex `0.2.0` provides:

- A generated Codex workspace with fixed role topology, project-scoped MCP,
  local wrappers, workspace manifest, generated policy/config files, and
  `./tcx doctor` validation.
- An explicit workspace update path that refreshes generated files, applies
  central DB migrations, preserves workspace identity/profile state, and
  re-runs doctor checks.
- A role workflow model where `head-manager` dispatches work and specialists
  return bounded artifacts instead of every agent redoing every part of the
  investment question.
- File-native research memory: markdown artifacts, source snapshots, versioned
  handoffs, export/search flows, and source/as-of posture that agents and
  humans can both read.
- Strategy and skill management through workspace files, including
  `strategy-*` skills and role-local optional skills without weakening core
  role boundaries.
- Policy, restricted-symbol, approval, order, execution, portfolio, MCP, and
  audit services behind a shared Django application layer.
- Broker Center foundations: broker connections, broker accounts, read-only
  paper sync, external MCP broker discovery import, and reconciliation
  summaries behind the service layer.
- Canonical Order Ticket foundations: natural-language or structured draft
  tickets, schema/policy/cash-position/broker validation checks, state-machine
  events, exact approval-scope binding, broker order timeline, and paper fills.
- Experimental paper/stub order lifecycle support: check order tickets, request
  approval receipts, submit approved orders, cancel approved orders, and record
  outcomes.
- A local product web surface for browsing agents, skills, research markdown,
  external MCP Gate metadata, starter prompts, and operational state.
- Django Admin, Django Ninja API, CLI, and MCP surfaces that call the same
  service-layer logic instead of creating parallel execution paths.
- A managed External MCP Gate that imports discovery metadata,
  classifies tool risk, scopes role access, and blocks unsafe direct proxy
  paths by default.

## Local Web Dashboard

TradingCodex includes a local Django web surface so users do not have to inspect
everything through chat or CLI output. When Codex trusts a generated workspace,
project MCP startup also starts the local service and exposes the dashboard at:

```text
http://127.0.0.1:48267/
```

The dashboard is a review and control surface for the agent roster, required
and optional skills, strategy skills, research markdown, Broker Center,
External MCP Gate metadata, portfolio sync/reconciliation, order-ticket
drafts/checks, starter prompts, policy/order/portfolio/activity status, and
local workspace state. It does not spawn Codex agents, approve orders, submit
executions, or provide investment recommendations.

For CLI-only sessions, start it manually:

```bash
./tcx service runserver
```

Django Admin is available separately at `http://127.0.0.1:48267/admin/` for
local/staff DB inspection.

## How A Workflow Moves

TradingCodex is designed around handoffs rather than one giant answer:

1. The user asks Codex for an investment workflow.
2. `head-manager` classifies the request, maps the investment universe and
   workflow lane, and dispatches bounded work to specialist roles.
3. Analysts create evidence-backed artifacts with source/as-of posture.
4. Downstream roles consume accepted upstream artifacts instead of silently
   filling missing work outside their role.
5. Portfolio and risk roles review fit, sizing, broker sync/reconciliation,
   limits, restricted symbols, order-ticket checks, and approval readiness.
6. If an executable paper/stub action is requested, `risk-manager` creates an
   approval receipt bound to the exact order payload hash and
   `execution-operator` submits only through TradingCodex MCP.
7. Policy decisions, MCP calls, approvals, execution results, and audit events
   remain inspectable through local service surfaces.

## Role Roster

| Layer | Agent | Role summary |
| --- | --- | --- |
| Main agent | `head-manager` | Dispatches specialist roles, preserves constraints, and synthesizes completed artifacts. |
| Analysis subagent | `fundamental-analyst` | Reviews business quality, financial evidence, company fundamentals, and source claims. |
| Analysis subagent | `technical-analyst` | Reviews price action, trend structure, levels, and market behavior. |
| Analysis subagent | `news-analyst` | Tracks news, catalysts, events, and freshness-sensitive context. |
| Market-context subagent | `macro-analyst` | Covers macro, rates, FX, commodities, policy, and cross-asset context. |
| Market-context subagent | `instrument-analyst` | Supports ETF/index, options, crypto market structure, and instrument-level work. |
| Decision-review subagent | `valuation-analyst` | Reviews valuation assumptions, sensitivity, and decision-quality gaps. |
| Portfolio subagent | `portfolio-manager` | Reviews portfolio fit, sizing, exposure, and draft order readiness. |
| Risk subagent | `risk-manager` | Reviews downside, policy constraints, restricted lists, and approval readiness. |
| Execution subagent | `execution-operator` | Handles approved paper/stub execution through TradingCodex MCP only. |

The default generated workspace includes this one-plus-nine roster. The main
agent coordinates and synthesizes; specialist agents own the actual role work.

## Architecture

TradingCodex is a Python/Django modular monolith packaged as a local-first
tool. The important implementation rule is that every interface calls shared
application services:

| Surface | Role |
| --- | --- |
| Product web | Agents-first review dashboard for roles, skills, research markdown, External MCP Gate lifecycle/review, starter prompts, and local status. |
| Django Admin | Local/staff DB inspection for policy, orders, portfolio, MCP registry, workflows, integrations, and audit rows. |
| Django Ninja API | Typed local/staff REST and control endpoints. |
| MCP | Agent/tool boundary with typed tools, role scopes, policy checks, and audit. |
| CLI | Local operator commands and generated workspace wrapper behavior. |

Canonical implementation lives in:

```txt
tradingcodex_service/application/
tradingcodex_cli/commands/
apps/
workspace_templates/modules/
```

The baseline frontend uses Django templates, local static HTMX, and local
static Alpine. There is no Node, bundler, React, or frontend build step in the
core package.

## Safety Boundary

TradingCodex treats executable actions as a deterministic service-layer
lifecycle:

```text
principal -> capability -> policy -> schema -> approval/idempotency -> adapter -> audit
```

Important boundaries:

- Product web routes do not spawn agents, generate investment analysis, create
  approvals, or submit executions.
- REST/Admin/CLI/MCP call shared Django service functions.
- Role MCP allowlists are narrow: `head-manager` cannot submit orders,
  `risk-manager` owns approvals, and `execution-operator` owns execution calls.
- Paper/stub execution remains experimental.
- Live broker adapters are not shipped in the initial core.
- Raw broker secrets must not be stored in this repository or generated
  workspaces.

TradingCodex is research, workflow, and execution-guardrail tooling. It is not
financial, investment, legal, tax, or regulatory advice, and it does not provide
investment recommendations or guarantee returns.

## Supported Workflow Scope

Public equity is the first deeply specified sleeve. The harness keeps explicit
paths for ETF/index, public crypto market, macro/rates/FX/commodities, options,
credit-signal, and cross-asset workflows when the required data source, role
workflow, and policy boundary exist.

Unsupported or weakly sourced workflows should receive conservative readiness
labels such as `research-only`, `screen-grade`, `not-decision-ready`, or
`blocked`.

## Release Status

`0.2.0` is the OrderTicket rewrite release for the generated workspace, Python
CLI, Django service plane, product web, MCP boundary, and documentation set.
The package still uses an alpha development classifier because live broker
adapters and hosted service modes are intentionally outside the initial core.

## Documentation

- [Installation](installation.md)
- [Docs index](docs/README.md)
- [Product direction](docs/product-direction.md)
- [Core concepts and rules](docs/core-concepts-and-rules.md)
- [Harness model](docs/harness.md)
- [Roles, skills, and workflows](docs/roles-skills-and-workflows.md)
- [Safety policy and execution](docs/safety-policy-and-execution.md)
- [Interfaces and surfaces](docs/interfaces-and-surfaces.md)
- [Deployment](docs/deployment.md)

## Contributing

Contributions use Apache-2.0 with DCO sign-off. See
[CONTRIBUTING.md](CONTRIBUTING.md).

## License

TradingCodex is an Apache-2.0 open-core project.

Source code, generated workspace templates, and project documentation are
licensed under the Apache License, Version 2.0 unless marked otherwise. The
TradingCodex name, future logos, and official product marks are not granted by
the code license. See [LICENSE](LICENSE), [NOTICE](NOTICE), and
[TRADEMARKS.md](TRADEMARKS.md).

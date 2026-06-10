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
  <a href="installation.md">Installation</a> |
  <a href="#agent-topology">Agent Topology</a> |
  <a href="#architecture">Architecture</a> |
  <a href="#execution-boundary">Execution Boundary</a> |
  <a href="#documentation">Documentation</a>
</div>

---

# TradingCodex: Codex-Native Trading Harness

TradingCodex is a Codex-native trading harness for investors who want
agent-assisted research, role separation, workflow discipline, and deterministic
execution guardrails in one local-first system.

It is not an autonomous trading bot. Codex coordinates the workflow, Django owns
the durable service layer, and TradingCodex MCP is the execution boundary.

## Quick Start

Codex agents: do not clone this source repository for installation, and do not
invent a workspace path. If the user did not name a target directory, ask where
they want the TradingCodex workspace created. When the user is already in an
empty target workspace, install into `.`.

Install TradingCodex and attach it to a user-selected workspace:

```bash
mkdir -p /path/to/target-workspace
cd /path/to/target-workspace
curl -fsSL https://raw.githubusercontent.com/monarchjuno/tradingcodex/main/install.sh | sh -s -- .
```

After installation, fully quit and restart Codex, then open the generated
workspace and start from a new thread so project MCP config is reloaded.

For user-level installs, the same attach-first flow is:

```bash
pipx install tradingcodex
cd /path/to/target-workspace
tcx attach .
```

Start an orchestrated Codex workflow from the generated workspace:

```text
$orchestrate-workflow analyze Apple with public equity research, valuation, portfolio, and risk review
```

See [installation.md](https://github.com/monarchjuno/tradingcodex/blob/main/installation.md)
for GitHub-main installs, direct `uvx`, MCP/service details, and CLI smoke
checks.

## Agent Topology

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

The default generated workspace includes one `head-manager` and nine fixed
subagents. The main agent coordinates; specialist agents produce the actual
role work.

## Architecture

TradingCodex separates the local harness into three planes plus one execution
boundary:

| Plane | Owns | Interfaces and artifacts |
| --- | --- | --- |
| Codex control plane | Generated workspace instructions, project `.codex` config, role agents, hooks, and workflow routing. | Codex workspace files, role prompts, and human-readable `trading/*` exports. |
| MCP execution boundary | Role tool allowlists, input schemas, policy checks, approval checks, idempotency, and MCP call audit. | TradingCodex MCP tools exposed to allowed roles only. |
| Django service plane | Research memory, policy, orders, approvals, portfolio state, audit, Admin, API, and local web dashboard. | Shared service functions used by CLI, Admin, API, web, and MCP. |
| Adapter boundary | Paper/stub execution adapters for local harness validation. | Live broker adapters are excluded from the initial core. |

Executable actions flow through:

| Step | Meaning |
| --- | --- |
| `principal` | The caller identity and role capability. |
| `capability` | The requested action and permitted role surface. |
| `policy` | Restricted-list, limit, and execution-policy validation. |
| `schema` | Typed input validation before service-layer mutation. |
| `approval/idempotency` | Approval receipt and duplicate-submit protection. |
| `adapter` | Paper/stub adapter submission. |
| `audit` | Durable ledger record for the attempt and result. |

The central runtime DB defaults to:

```text
~/.tradingcodex/state/tradingcodex.sqlite3
```

Generated workspaces are clients and provenance sources. They do not own
canonical investment state.

## Execution Boundary

TradingCodex treats executable actions as a service-layer lifecycle:

```text
principal -> capability -> policy -> schema -> approval/idempotency -> adapter -> audit
```

Important boundaries:

- Product web routes do not spawn agents, create approvals, or submit
  executions.
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

Public equity is the first deeply specified sleeve. The harness is designed to
extend across ETF/index, public crypto market, macro/rates/FX/commodities,
options, credit-signal, and cross-asset workflows when the required data source,
role workflow, and policy boundary exist.

## Documentation

- [Docs index](docs/README.md)
- [TradingCodex PRD](docs/tradingcodex-prd.md)
- [Core concepts and rules](docs/core-concepts-and-rules.md)
- [Harness model](docs/harness.md)
- [Guardrails](docs/guardrails.md)
- [Improvement loop](docs/improvement-loop.md)
- [Deployment](docs/deployment.md)
- [Licensing and commercialization](docs/licensing-and-commercialization.md)

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

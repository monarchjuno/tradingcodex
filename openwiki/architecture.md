# Architecture

Use this page to orient source edits. For the human-readable architecture narrative, see [docs/system-architecture.md](../docs/system-architecture.md).

## Investment OS Layers

TradingCodex is the top-level investment OS. The harness is its orchestration
and runtime subsystem, not a synonym for the whole product.

| Layer | Owns |
| --- | --- |
| Core kernel | Non-replaceable evidence, method-fit, quality-gate, policy, approval, execution, audit, and provenance invariants. |
| Bundled investment capability pack | Fixed roles, built-in investment skills, method profiles, evaluation profiles, and the pristine research, analysis, and forecast baseline. |
| Managed user overlays | Additional instructions, optional role skills, and strategies that extend the baseline while remaining subject to the kernel. |
| Harness subsystem | Routing, dispatch, handoffs, projection, service boundaries, persistence, and validation that coordinate the three layers. |

## Runtime Planes

| Plane | Owns | Primary source |
| --- | --- | --- |
| Codex control plane | `head-manager`, nine fixed subagent TOMLs, skills, prompts, hooks, project MCP config, and exact root-native action interception | `workspace_templates/modules/*/files` |
| Django service plane | policy, orders, approvals, portfolio, audit, broker/integration state, API, MCP, read-only React asset serving, Admin | `tradingcodex_service/application/`, `apps/` |
| Workspace system plane | generated config, research markdown, source snapshots, indexes, `tcx`/`tcx.cmd` launchers | generated files from `workspace_templates/` |

Control-plane files request or guide work. Service-plane code decides and records durable outcomes. Workspace files make Codex-native state reviewable.

## Source Ownership

| Source | Ownership rule |
| --- | --- |
| `tradingcodex_service/application/*` | Canonical service use cases. Put durable behavior here before wiring surfaces. |
| `tradingcodex_service/application/execution_gateway.py` | Exact root-native submit/cancel parser, workspace-bound mandate, `native-user` authorization, redacted projection, and dispatch into the order kernel. |
| `tradingcodex_service/application/skill_invocations.py` | Shared lexical parser for managed, selection, and order skill invocations, including first-meaningful-line and matching projected-link validation. |
| `tradingcodex_service/application/build_gateway.py` | DB-canonical capability-scoped Build/Brain/Strategy current-turn grant/proof lifecycle, revocation, and audit without sandbox elevation or execution authority. |
| `apps/*/models.py` | Central DB records for policy, orders, portfolio, audit, MCP, workflows, integrations, and harness provenance. |
| `tradingcodex_cli/commands/*` | CLI interface only. It should call shared services rather than fork behavior. |
| `tradingcodex_service/api.py` | Typed local/staff REST/control API. |
| `frontend/` | React 19, TypeScript, and Vite 8 workspace viewer source; Node 22 build-time only. |
| `tradingcodex_service/static/tradingcodex_web/` | Committed Vite output served by Django and WhiteNoise. |
| `tradingcodex_service/web.py` | GET-only root SPA shell. |
| `tradingcodex_service/application/viewer.py`, `workspaces.py` | Read-only snapshot/detail assembly and registered selected-workspace binding. |
| `tradingcodex_service/mcp_runtime.py` | Codex MCP boundary, role visibility, schema validation, and MCP ledger behavior; final submit/cancel/refresh mutations are not public tools. |
| `workspace_templates/modules/*/files` | Generated workspace contract. Human/Codex-readable generated content should stay here as files. |
| `tradingcodex_service/application/components.py` | Component maintenance map exported to generated workspaces. |
| `tradingcodex_service/application/agents.py` | Role/skill registry and projection source. |
| `tradingcodex_service/application/analysis_runs.py` | Lightweight analysis run identity, request hash, and sealed Brain/strategy/context provenance. |
| `tradingcodex_service/application/investment_brains.py` | Strict Investment Brain registry, immutable versions, activation/rollback, and Head Manager-only projection. |
| `tradingcodex_service/application/decision_packages.py`, `postmortems.py` | Sealed Decision Packages, outcome-separated reviews, and lesson lifecycle records. |
| `tradingcodex_service/application/workspace_git.py` | Generated-workspace Git membership, privacy-first ignore contract, and diagnostics without automatic repository actions. |
| `tradingcodex_service/application/investor_context.py` | Optional workspace-local suitability context, saved application default, and strict file validation. |
| `tradingcodex_service/application/research_specs.py`, `forecasting.py` | Frozen point-in-time research, method profiles, experiment validation, and forecast lifecycles. |
| `tradingcodex_service/application/investment_analysis.py`, `evaluation_lab.py` | Method-bound causal valuation plus pristine and corpus-declared model-evaluation profiles. |

## State Model

The canonical home is macOS `~/Library/Application Support/TradingCodex`,
Windows `%LOCALAPPDATA%\TradingCodex`, or Linux
`${XDG_DATA_HOME:-~/.local/share}/tradingcodex`; the DB is
`state/tradingcodex.sqlite3` below it. `TRADINGCODEX_HOME` and
`TRADINGCODEX_DB_NAME` remain explicit overrides. v1 selects only that native
home or the explicit override and does not probe or migrate alternate homes.
Versioned managed Python environments under `runtime/python/` keep generated
launchers and project MCP independent of removable uv caches.
`TRADINGCODEX_WORKSPACE_ROOT` is provenance, not a separate canonical
investment ledger.

Central DB state includes policy decisions, order tickets, approvals, execution results, portfolio snapshots, broker connections, non-research MCP call ledgers, and audit rows. Analysis runs and research artifacts stay file-native. A common storage location is not a common active account: workspace provenance and workspace-derived internal account scopes remain explicit.

Workspace-file state includes `.codex/`, `.agents/skills/*`,
`.tradingcodex/subagents/skills/*`, `.tradingcodex/generated/*.json`, lightweight
analysis run records, Codex/subagent events, `trading/research/*.md`, source snapshots,
ResearchSpecs, replay manifests, experiment runs, causal analyses, forecast
events, and model-evaluation artifacts. Immutable hashes and replay bindings
make these research/control files reviewable; they do not become execution
authority.

Decision Memory composes these file-native records with frozen decision
packages, outcome-separated postmortems, strategy/context hashes, and reviewed
lesson states. Wiki and graph outputs are rebuildable views, not another
canonical store. See [docs/decision-memory.md](../docs/decision-memory.md).

The viewer adds no run metadata and launches no Codex process. It reads
canonical native activity and accepted artifacts through the shared service
layer. Raw reasoning, tool inputs/outputs, stderr, and raw final output are not
persisted or exposed. A reader-facing Head Manager report is available only
when its sealed run lineage, authenticated artifact receipt, accepted handoff,
producer, body hash, complete accepted-input hash set, and applicable quality
gate all match.

## Django Model Families

Observed model families:

- policy: principals, capabilities, restricted symbols, policy decisions
- orders: order tickets, checks, approval receipts, execution results, broker order timeline, fills, order events
- portfolio: snapshots, positions, cash, versioned paper state, ledger events, sync runs, reconciliation runs
- integrations: adapter definitions, broker connections, accounts, instrument maps
- MCP: TradingCodex tool definitions and non-research tool-call ledger
- harness: workspace provenance
- audit: append-only audit events

Research artifacts are intentionally file-native and do not have a Django research model surface.

## Design Constraints

- One Node build root is allowed under `frontend/`; do not add a package
  workspace, production Node server, Node MCP runtime, or Node requirement to
  the wheel/generated workspace. Attach and update never run npm.
- No per-interface policy/order/approval/execution forks.
- No workspace-local canonical investment DB by default.
- No hidden prompt, skill, policy, hook, or generated contract text inside Python string constants when it should be reviewed by humans or Codex.
- No managed overlay may remove or weaken core quality, evidence, policy, approval, execution, audit, or provenance requirements.
- No globally installed or plugin-provided host skill belongs to the pristine baseline without explicit user opt-in for the current workflow or managed activation.
- Do not describe workspace projection as hard runtime skill isolation until clean-host, populated-host, name-collision, and invocation tests attest it.
- Keep method profiles distinct: general evidence, event research, quant-signal validation, and listed-equity FCFF DCF do not share one universal artifact contract.

# TradingCodex Agent Quickstart

OpenWiki is the working map for coding agents in this repository. It is intentionally shorter than `docs/`: use it to decide what to read, where to edit, and how to validate. For product-facing explanations and durable rules, follow [docs/README.md](../docs/README.md).

## Documentation Contract

| Layer | Audience | Purpose |
| --- | --- | --- |
| `README.md` | New users | Product overview, install path, and links into detailed docs. |
| `docs/` | Humans and maintainers | Durable source of truth for product behavior, safety, architecture, workflows, validation, release policy, and commercialization. |
| `guidebook/` | End users | Concise task-first GitHub Pages guide for setup, skills, concepts, provider-to-order onboarding, and recovery. |
| `openwiki/` | Coding agents | Fast source map, edit routing, and validation routing. |
| `AGENTS.md` | Coding agents | Hard repository rules and required validation expectations. |

When source behavior changes, update the relevant `docs/` page. Update `guidebook/` in the same change when the user-visible setup, task, skill, viewer, recovery, provider, or order journey changes. When the agent working map becomes misleading, update `openwiki/`. Keep the same concept canonical in `docs/` and link to it elsewhere.

## Fast Path

| Task | Start |
| --- | --- |
| Understand the repository shape | [Architecture](architecture.md) |
| Change head-manager, subagents, skills, hooks, routing, or handoff behavior | [Workflows And Agents](workflows-and-agents.md) |
| Change `tcx attach/update`, templates, generated files, or projection | [Generated Workspaces](generated-workspaces.md) |
| Change React viewer/web/API/MCP/CLI behavior, models, research/decision memory, or investor context | [Interfaces And Data](interfaces-and-data.md) |
| Change the public task-first guide or its navigation | `guidebook/`, the owning `docs/` page, and `docs/deployment.md` |
| Change policy, approval, broker, execution, BYOR Codex capabilities, or secrets | [Safety And Execution](safety-and-execution.md) |
| Choose validation before handoff | [Development And Validation](development-and-validation.md) |

## Setup Guard

If a user asks to set up, install, attach, or use TradingCodex in a workspace, do not clone this source repository into that workspace. From the target workspace, run:

```bash
uvx --refresh --from tradingcodex tcx attach . && ./tcx doctor
```

On native Windows PowerShell, attach with the same installed command and run
`.\tcx.cmd doctor`. Generated workspaces always contain both `tcx` and
`tcx.cmd`; use only the native launcher for the current platform.

Clone this repository only for source development, inspection, or modification. Source: `README.md`, `AGENTS.md`, `docs/generated-workspaces.md`.

## Core Mental Model

TradingCodex is a local-first investment OS built on Codex. Head Manager owns dynamic research orchestration; Django owns durable persistence, policy, approval, order, broker, execution, and audit boundaries. Natural-language answers do not become broker actions.

Keep three product layers separate when editing:

- Core kernel: non-replaceable quality, evidence, policy, approval, execution, audit, and provenance contracts.
- Bundled investment capability pack: the fixed team, built-in investment skills, method profiles, and evaluation profiles that must make a pristine workspace useful without customization.
- Managed user overlays: additional instructions, optional role skills,
  strategies, and explicit Investment Brain plugins that extend the baseline
  without weakening the kernel.

All 32 bundled skill ids use the reserved compact `tcx-` namespace with one
suffix word when possible and no more than two. User-owned `strategy-*`,
`investment-brain-*`, and optional role skills remain separate namespaces.

Codex may discover globally installed or plugin-provided skill metadata. Those capabilities are outside the pristine TradingCodex baseline and require explicit user opt-in for the current workflow or managed activation. Current workspace projection is not proof of hard runtime isolation; do not claim that property without clean-host, populated-host, name-collision, and invocation evidence.

The system has three runtime planes:

- Codex control plane: generated prompts, role TOML, skills, hooks, and project MCP config.
- Django service plane: policy, orders, approvals, portfolio, audit, integrations, MCP, API, Admin, read-only React viewing, and research indexing.
- Workspace system plane: generated files, research markdown, source snapshots, policies, audit files, and the `tcx`/`tcx.cmd` launchers.

The service plane decides and records execution-sensitive outcomes. Workspace
files keep agent and skill configuration, lightweight run provenance, and
research state readable; they do not materialize a server-selected team or DAG.
There is no execution subagent. A root native user can request an immediate
final effect with one exact action-only submit/cancel prompt, or admit at most one
later effect in the current turn with an exact first meaningful line
`$tcx-order-allow --mode paper|validation|live`. The generated hooks bind that
grant to workspace/session/turn/prompt/mode and inject proof only into Head
Manager's protected `use_order_turn_grant` call. The browser viewer has no
mutation route; fixed roles, public REST/generic CLI, and direct MCP callers
expose no usable final mutation. Codex
app Scheduled Tasks submit the saved prompt through the same root-turn path;
TradingCodex does not detect or trust an Automation origin.

Normal Head Manager and fixed-role analysis inherits the project-wide
`trading-research` permission profile. General shell/Python, credential-free
public retrieval, user-owned file changes outside `trading/`, and the dedicated
`$TRADINGCODEX_SCRATCH` path are available. The `trading/` tree, generated
control files, TradingCodex runtime/DB, protected artifacts, credentials, and
local/private services remain protected. Authenticated service/MCP tools own
durable TradingCodex writes. Controlled `trading/`, product, or connector
editing requires the separate `trading-build` profile and a root
native prompt whose first meaningful invocation is `$tcx-build`; the hook issues
a DB-canonical current-turn grant, but the active Codex profile remains
authoritative. Build edits use `apply_patch`; its shell is a narrow review lane
for public GET/HEAD, read-only HTTPS Git, limited workspace reads, inert provider
hash/diff/Git inspection, isolated `py_compile`, and allowlisted launcher
commands. General interpreters, helper scripts, test runners, build systems,
shell composition, and model-authored POST remain blocked. Credential-free
public sources are staged in the dedicated scratch tree, while authenticated
access, local/private targets, package installation, remote mutation, and
broker calls remain denied. Brain and Strategy management stay in `trading-research` and
start directly with matching first-meaningful-line `$tcx-brain` or
`$tcx-strategy`; their current-turn
grant admits only the matching native source/staging path and proof-protected
`manage_investment_brain` or `manage_strategy` lifecycle tool. Research does
not expose the model to the generated launcher or attached runtime. Every managed
follow-up and every Automation run needs a fresh marker. The browser viewer
cannot request one, subagents cannot inherit one, Plan mode blocks all of them,
and Build, Brain, Strategy, and order markers must never be combined. Persistent
`tcx mode` is retired and old `mode.json` state is ignored.

## High-Signal Source Files

| File or directory | Why it matters |
| --- | --- |
| `tradingcodex_cli/__main__.py` | CLI command dispatcher and command surface. |
| `tradingcodex_cli/generator.py` | Workspace module graph, template rendering, generated indexes. |
| `tradingcodex_service/application/components.py` | Harness component registry and maintenance ownership. |
| `tradingcodex_service/application/agents.py` | Fixed roles, built-in skills, MCP allowlists, projection. |
| `tradingcodex_service/application/analysis_runs.py` | Lightweight request-hash and sealed run-provenance bindings with no semantic plan or DAG. |
| `tradingcodex_service/application/skill_invocations.py` | Shared lexical parsing for managed, selection, and order skill invocations, including first-meaningful-line and matching projected-link validation. |
| `tradingcodex_service/application/build_gateway.py` | DB-canonical Build/Brain/Strategy current-turn grant reservation, protected-call proof consumption, revocation, and audit. |
| `tradingcodex_service/application/investment_brains.py` | Strict Brain bundle registry, immutable local/Git versions, activation, and Head Manager-only projection. |
| `tradingcodex_service/application/decision_packages.py`, `postmortems.py` | Sealed decisions, outcome-separated review, and lesson validation/promotion. |
| `tradingcodex_service/application/workspace_git.py` | Generated-workspace Git and privacy-ignore contract without automatic repository actions. |
| `tradingcodex_service/application/investor_context.py` | Optional workspace-local suitability context and its saved application default. |
| `tradingcodex_service/application/research_specs.py`, `forecasting.py` | Point-in-time research plans, method profiles, experiment runs, and forecast lifecycle. |
| `tradingcodex_service/application/investment_analysis.py`, `evaluation_lab.py` | Method-bound causal valuation plus pristine and corpus-declared paired model-evaluation profiles. |
| `tradingcodex_service/api.py` | Local/staff API surface. |
| `tradingcodex_service/viewer_api.py`, `application/viewer.py` | Read-only selected-workspace snapshot and skill/artifact detail API. |
| `frontend/` | React 19/TypeScript/Vite 8 source for Library (`#/library`), Skills (`#/skills`), System (`#/system`), and registered workspace selection. |
| `tradingcodex_service/static/tradingcodex_web/` | Committed frontend output served by Django/WhiteNoise; do not hand-edit. |
| `tradingcodex_service/web.py` | GET-only root SPA shell. |
| `tradingcodex_service/mcp_runtime.py` | MCP tool registry, input validation, role visibility, call ledger behavior. |
| `workspace_templates/modules/*/files` | Source for generated Codex prompts, skills, hooks, policies, and wrappers. |
| `docs/README.md` | Human documentation hub and source-of-truth map. |

## Change Rules

- Do not infer harness behavior from Python alone. Read docs, prompts, skills, hooks, policies, generated templates, services, and tests as one contract.
- All durable service behavior belongs under `tradingcodex_service/application/` and should be reused by Web, Admin, API, MCP, CLI, and generated hooks.
- The workspace viewer is read-only and starts no Codex process. Native Codex
  owns Head Manager dispatch; browser origin never widens MCP, policy, approval,
  or execution authority.
- Node 22 is a maintainer frontend-build dependency only. The wheel and
  generated workspaces remain Node-free; attach/update never run npm.
- Research artifacts and source snapshots are workspace-file-native, not Django research DB models.
- Final submit/cancel begins with either an exact complete immediate root action
  or an exact first-meaningful-line `$tcx-order-allow` grant plus current
  `PreToolUse` proof;
  both then pass native-user permission, policy, payload validation, exact
  approval/idempotency, mandatory intent audit, connection, and result audit.
- Generated prompt, skill, hook, policy, and workspace-contract content should remain ordinary template files under `workspace_templates/modules/*/files`.

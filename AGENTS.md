# TradingCodex Repository Guide

## Read Order And Documentation Ownership

Read in this order:

1. This file for repository-wide non-negotiable rules and validation routing.
2. [Architecture](docs/architecture.md) for the codebase map, ownership, and
   runtime boundaries.
3. [Product documentation](docs/README.md) for durable behavior and rationale.
4. [User guide](guidebook/index.html) only when setup or an everyday user
   journey changes.

Keep each fact in one owning layer and link to it elsewhere:

| Layer | Owns |
| --- | --- |
| `docs/` | Durable product behavior, architecture, safety, workflow, and release intent. |
| `guidebook/` | Concise, task-first user instructions. |
| `README.md` / `installation.md` | Product entrypoint and installation path. |
| `AGENTS.md` | Repository-wide development constraints. |

Update only the owning layer unless another audience's route actually changed.
Do not mirror a rule across several documents merely to keep them textually
similar. When layers disagree, correct the owning `docs/` page first.

## Product Direction

TradingCodex is a thin, local-first investment layer on top of native Codex.
Codex owns reasoning, research strategy, tool selection, delegation, and use of
the user's available skills, plugins, apps, and MCP servers. TradingCodex owns
durable investment records and deterministic boundaries for provenance,
policy, approval, execution, secrets, and audit.

Do not replace native Codex capabilities with a parallel router, workflow
engine, capability registry, permission system, tool-discovery protocol, agent
scheduler, or provider platform. Add TradingCodex machinery only when a durable
cross-session invariant or sensitive final effect cannot be handled safely by
native Codex, a canonical skill bundle with a concise entrypoint, or an
existing application service.

Put behavior at the first layer that can own it:

1. Native Codex and user-provided capabilities.
2. One canonical skill bundle with a concise `SKILL.md` entrypoint for reusable
   agent guidance.
3. A prompt for stable role identity or a safety boundary.
4. An application service for durable records or deterministic enforcement.
5. A hook only when enforcement must surround a native tool call.

Keep one canonical owner for each rule. Do not duplicate the same procedure in
prompts, skills, hooks, services, CLI, MCP, and documentation.

Treat the skill directory, not one Markdown file or a prescribed resource
taxonomy, as the canonical bundle. Keep default-loaded metadata and
`SKILL.md` focused; put optional detail or reusable resources in clearly named
bundle paths and route when to load or execute them. Do not impose a fixed byte
limit without a measured compatibility, safety, or latency failure.

## Simplicity And Agent Autonomy

- Specify goals, evidence standards, authority, and safety boundaries. Do not
  prescribe exact reasoning steps, tool order, search counts, wait loops,
  artifact windows, or retry scripts without a demonstrated failure that needs
  deterministic enforcement.
- Head Manager may answer narrow factual, status, and explanation requests
  directly. Use subagents only when distinct expertise, independent evidence,
  or high-consequence review materially improves the result.
- Reuse an existing agent for corrections when practical. Fixed roles are
  optional expert profiles, not the only valid path. Inherit the user's Codex
  model and reasoning settings unless a documented compatibility or safety
  requirement proves otherwise.
- Persist research when it has reuse, provenance, decision, or audit value. Do
  not require an artifact for every narrow answer or intermediate thought.
- Prefer professional free-form analysis with sources, assumptions,
  confidence, and gaps over large schemas or regex-enforced prose.
- Use native Codex permissions for ordinary workspace, shell, Git, web, skill,
  plugin, and MCP activity. Reserve TradingCodex gates for secrets, brokers,
  orders, approvals, execution, and other sensitive effects.
- Prefer deletion and direct composition over registries, compatibility
  layers, state machines, projection indexes, generic provider frameworks, and
  speculative extension points.
- Do not build an abstraction for a second implementation before a real second
  use case exists. A new abstraction must name the current failure it fixes,
  explain why a smaller native solution is insufficient, and replace rather
  than duplicate the old path.

## Model-Aware Consequence Tracing

When the implementing Codex agent and TradingCodex runtime agents use the same
GPT/Codex model family, use your own likely interpretation and behavior as a
concrete design signal. Do not assume identical behavior: prompts, context,
skills, tools, permissions, model versions, and reasoning settings may differ.

Before changing prompts, skills, tool exposure, delegation, hooks, or services,
trace the consequential path from the user request through model
interpretation, guidance and context, tool or subagent selection, canonical
service boundaries, generated output or external action, and finally durable,
safety, latency, context, and user-visible effects. Improve the earliest layer
that owns the cause. Validate consequential model-facing changes in the actual
generated TradingCodex harness rather than inferring behavior only from unit
tests or assumptions about the model.

## Research Source Direction

External research uses a guidance-based fallback, not a TradingCodex-owned
provider state machine:

1. Reuse an adequate existing Snapshot or Dataset.
2. Use one relevant user-enabled skill, plugin, app, or MCP capability.
3. Use the optional direct OpenBB MCP when enabled.
4. Research autonomously, preferring primary official sources.
5. Use other credible sources when primary coverage is unavailable.
6. Report an explicit evidence gap when adequate data cannot be obtained.

This is a collection preference, not a trust ranking. Preserve source and
as-of context, verify material claims, avoid unchanged repeated calls, and
fetch only missing coverage after partial success. OpenBB remains optional and
direct: do not build an OpenBB proxy, runtime supervisor, provider router,
compatibility state machine, or package manager. Store environment-variable
names only, never credential values.

## Safety And Product Boundaries

- Django application services are canonical for durable policy, portfolio,
  order, approval, broker, execution, and audit behavior. Interfaces call those
  services rather than fork them.
- The workspace viewer is read-only. It must not start Codex or mutate
  workspace, skill, order, broker, or execution state.
- Research artifacts, source snapshots, immutable datasets, and provenance
  hashes remain workspace-file-native. Execution-sensitive state belongs to
  the central service ledger.
- Sensitive final actions remain service-gated and idempotent. Never expose
  secrets in repository or workspace files, prompts, shell output, APIs, MCP
  responses, artifacts, or audit output.
- Generated workspaces remain Node-free. Node is a maintainer dependency only.
- Preserve development/release HOME, DB, and service isolation. A version or
  DB mismatch remains fail-closed.

## Setup And Development Isolation

For an end-user workspace, do not clone this source repository. From the target
directory run:

```bash
uvx --refresh --from tradingcodex tcx attach . && ./tcx doctor
```

On native Windows PowerShell, run the same attach command and then
`.\tcx.cmd doctor`. Clone this repository only for source development.

From this checkout create or refresh a development workspace with:

```bash
./install.sh --dev /path/to/empty-workspace
./install.sh --dev --update /path/to/existing-dev-workspace
```

Do not combine `--dev` with `--from` or convert a release workspace in place.
Development workspaces from this checkout share its derived
`TRADINGCODEX_HOME`, DB, and deterministic `20000`-`29999` loopback service;
release workspaces remain isolated at their own recorded identity. Use the
generated `./tcx service status|ensure|stop` commands and never hard-code or
free another runtime's port.

## Change Routing

| Change area | Read first | Minimum validation |
| --- | --- | --- |
| CLI, attach/update, templates, hooks, generated files | `docs/generated-workspaces.md` | Focused tests and disposable workspace smoke. |
| Service, model, API, MCP, viewer | `docs/architecture.md` and `docs/interfaces-and-surfaces.md` | Focused tests; `python manage.py check` when Django wiring changes. |
| Roles, skills, workflow, research harness | `docs/roles-skills-and-workflows.md` | Generated workspace and Codex-native smoke when behavior changes. |
| Policy, broker, approval, execution, secrets | `docs/safety-policy-and-execution.md` | Focused safety tests and canonical service-path checks. |
| Documentation only | `docs/README.md` | Link/file checks and review of changed Markdown. |
| Package or release | `docs/deployment.md` | The release-readiness checks documented there. |

## Implementation And Delivery

- Target Python `>=3.11,<3.15` and Django `5.2.x`. Keep durable service behavior
  under `tradingcodex_service/application/` and use direct canonical imports.
- Frontend source lives under `frontend/`; compiled assets under
  `tradingcodex_service/static/tradingcodex_web/` are generated, not hand-edited.
- Keep prompts, skills, hooks, policies, and workspace contracts as ordinary
  files under `workspace_templates/modules/*/files`.
- Use the repository's `skill-creator` workflow before adding or materially
  changing a generic skill; keep bundled TradingCodex skills in the reserved
  `tcx-` namespace.
- Use the smallest meaningful validation while iterating. Harness changes need
  observed Codex behavior, not unit tests alone; run one integrated E2E after
  coupled changes stabilize.
- For a non-trivial architecture or harness change, state whether native Codex
  already covers it, what duplication is removed, and the expected effect on
  tool calls, spawned agents, context size, and latency.
- Keep durable product copy in English unless a reviewed localization layer is
  explicitly introduced.
- Preserve unrelated worktree changes. Commit only when requested, using small
  imperative commits that identify the validation actually run.

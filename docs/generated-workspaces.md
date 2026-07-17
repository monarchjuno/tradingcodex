# Generated Workspaces

This document owns `tcx attach`, `tcx update`, generated workspace
structure, template behavior, project-scoped MCP config, hook behavior,
workspace provenance, and smoke checks.

## Workspace Contract

`tcx attach` renders `workspace_templates/modules/*/files` into a new Codex
workspace. `tcx update` re-renders those files only for an existing v1
workspace. After rendering, both commands set the Django settings module,
apply the current central runtime schema, and record workspace provenance in
the central local Django DB. Pre-v1 workspace and database state is rejected
before mutation rather than converted.

Generated invocation, permission, hook, and skill changes are not retroactive
inside an already attached workspace. Apply them with `tcx update`, then fully
restart Codex so the refreshed project config, prompts, skills, hooks, and MCP
binding load together. Start a new task after restart rather than resuming an
already-open task, which can retain the prior generated hook snapshot.

The template source tree may be refactored for maintainability, but generated
output paths are the v1 release contract. Module ids, module dependency
resolution, and rendered paths such as `.codex/config.toml`, `.agents/skills/*`,
`.tradingcodex/*`, `trading/*`, and `./tcx` must remain stable unless docs and
tests intentionally change the generated workspace contract. The generated
`tcx-calc` and `tcx-calc.cmd` names are likewise stable; the cache key behind
them is content-addressed and may change when the locked runtime changes.

Template bodies should remain ordinary source files under
`workspace_templates/modules/*/files` whenever the generated artifact is meant
to be read or edited by humans or Codex, such as Markdown, TOML, YAML, JSON,
Python hook scripts, schemas, and wrappers. Python code may own module
registry loading, dependency resolution, rendering, validation, and generated
index writing, but it should not hide durable prompt, skill, policy, hook, or
workspace-contract content inside Python string constants merely for
organization. If a template is generated from structured Python data, the
generated file path and reviewable source-of-truth data must be documented and
covered by contract tests.

The generated workspace is ready for:

- `./tcx doctor`
- `./tcx workspace status`
- `./tcx investor-context status`
- MCP ledger inspection
- research-memory commands
- prepared and exploratory `tcx-calc` workflows
- local React viewer/Admin service access
- Codex-native role prompts and skills

`./tcx doctor` prints a compact layer summary plus every warning or failure by
default. Use `./tcx doctor --verbose` when reviewing every individual PASS
check. `--layer <layer>` computes only that layer plus required global service
preflight instead of probing unrelated layers.

The generated workspace does not create a workspace-local canonical investment
DB by default.

A generated workspace projects three distinct TradingCodex product layers:

| Layer | Generated-workspace contract |
| --- | --- |
| Core kernel | Non-replaceable workflow-quality, evidence, policy, approval, execution, audit, and provenance contracts. |
| Bundled investment capability pack | The fixed investment team, built-in research and judgment skills, method profiles, and evaluation profiles that define the pristine baseline. |
| Managed user overlays | Additional instructions, optional role skills, and `strategy-*` skills that extend the baseline without replacing core quality or safety requirements. |

The harness is the orchestration and runtime subsystem that projects and
coordinates these layers. It is not the top-level product definition;
TradingCodex is the investment OS.

## Valid Targets

The target may be:

- a new or empty standalone directory, which `tcx attach` initializes as a
  local Git worktree;
- an empty directory already contained by a parent Git worktree; or
- a directly git-initialized directory containing only `.git` plus optional
  Git metadata files.

Attach preserves an existing direct or parent repository and never creates a
nested repository inside it. It does not stage files, create a commit or branch
reference, configure a remote, push, or associate the workspace with a GitHub
account. Git must be installed when a new standalone workspace is attached.
`tcx update` requires the existing workspace to remain inside a Git worktree;
it does not silently retrofit a missing repository boundary.

Source checkouts of this repository are development projects, not generated
TradingCodex workspaces.

For a disposable workspace generated from the checkout currently running the
CLI, developers may use `uv run python -m tradingcodex_cli attach
/path/to/empty-workspace --dev`. On POSIX, `./install.sh --dev
/path/to/empty-workspace` is the equivalent bootstrap and sets both the outer
package runner and inner executable-source declaration to the checkout. The
same paths support update through `tcx update ... --dev` from the checkout or
`./install.sh --dev --update ...`. `--dev` is mutually exclusive with
`--from`; it is never an inference performed by an installed wheel. With no
explicit runtime override, attach selects a home below the platform default at
`development/source-<checkout-hash>` and a deterministic loopback service port
in the `20000`-`29999` range. Workspaces from one checkout intentionally share
that development ledger/service; different checkouts and release workspaces do
not. `TRADINGCODEX_HOME`, `TRADINGCODEX_DB_NAME`, and
`TRADINGCODEX_SERVICE_ADDR` remain authoritative overrides.

Codex agents must not run `git clone` when a user asks to install, set up,
attach, or use TradingCodex in a workspace. Run the packaged CLI
from the target workspace instead:

```bash
uvx --refresh --from tradingcodex tcx attach . && ./tcx doctor
```

Agents must also not silently create a default target when a user only asks to
install TradingCodex. The agent rule is: do not invent a
workspace path such as `tradingcodex-workspace`. If no target path is supplied
and the user did not ask to use the current workspace, ask for the target
directory before running setup. If the user is already in an empty target
workspace, install into `.`.

## Workspace Ownership Boundaries

Do not infer ownership from a top-level directory alone. `.agents/skills/`,
`.tradingcodex/`, and `trading/` intentionally contain files with different
lifecycles. The exact `generated_files` entries in
`.tradingcodex/generated/module-lock.json`, protocol-owned identity/status
paths, reserved namespaces, and managed block markers define the boundary:

| Class | Representative paths | Update contract |
| --- | --- | --- |
| Release-managed generated files | `AGENTS.md`, `pyproject.toml`, `tcx`, `tcx.cmd`, `.codex/config.toml`, `.codex/agents/*.toml`, `.codex/hooks/*`, `.agents/skills/tcx-*`, bundled `.tradingcodex/subagents/skills/**/tcx-*`, schemas, policies, launchers, generated indexes, and protocol-owned workspace identity/status files | Template and projection files are listed and hashed in the module lock. The lock cannot inventory itself; the immutable workspace manifest and rebuildable bootstrap/status files are separately protocol-owned. Update re-renders or re-projects these files from the current package and canonical state. Direct edits are unsupported and may be replaced. |
| User-selected managed overlays | `.tradingcodex/agent-instructions/*.md`, `.tradingcodex/user/*`, `.agents/skills/strategy-*`, optional role skills, `investment-brains/*` authoring sources, and the installed Brain registry/packages/projections | The owning lifecycle service validates and writes these paths. Update preserves their canonical state and rebuilds only generated projection blocks or indexes from it. |
| Workflow and research artifacts | `trading/research/`, `trading/reports/`, `trading/forecasts/`, `trading/decisions/`, `trading/evaluations/`, postmortems, lesson state, and per-run provenance | These are workspace state, not release payload. Update preserves them; template-owned `.gitkeep` files in the same directories do not transfer ownership of sibling artifacts. |
| Local/private runtime state | The external `TRADINGCODEX_HOME`, central DB, ignored session/status/cache/audit files, secrets, credentials, and private Investor Context | These remain outside versionable product state or inside the managed privacy-ignore block. Rebuildable status/cache files may be refreshed; durable authority never moves into the workspace. |
| Ordinary user files | Any non-reserved path not listed in the generated-file inventory | Update leaves them untouched. A file placed in a reserved managed namespace may be rejected as a collision rather than adopted or deleted. |

In particular, `.tradingcodex/config.yaml` is a generated control input despite
its name; use the documented customization and lifecycle surfaces instead of
editing it directly. `.codex/config.toml` is release-managed, but update
preserves non-reserved user MCP, plugin, and skill configuration while
reconstructing the reserved `tradingcodex` MCP entry and Strategy, Investment
Brain, role-skill, and additional-instruction projections. TradingCodex never
rewrites user capability directories or non-`tcx-*` skill sources.

Attach also creates or updates a user-visible `.gitignore`. It is not a
template-owned generated file: TradingCodex manages only the clearly delimited
`TradingCodex local/private state` block and preserves all user-authored rules
outside that block.

## Workspace Git Contract

A generated workspace is a versioned user environment. Generated Codex
configuration, installed Brain registry/projection state, local Brain overlays,
user-owned `strategy-*` skills, research, Decision Memory, analysis-run
provenance, and human-readable guidance remain eligible for version control.
TradingCodex does not stage or commit any of them automatically.

The managed `.gitignore` block excludes local/private state by default:

- runtime SQLite databases and journal files;
- process, session, and service-status state, including
  `.tradingcodex/mainagent/session-start.json`;
- transient audit streams;
- Python/tool caches, rebuildable research and artifact-catalog indexes,
  content-addressed Dataset payloads under
  `trading/research/datasets/objects/`, and native lock files;
- raw secrets, credentials, local environment files, keys, and certificates;
  and
- the private workspace Investor Context plus its per-run sealed snapshot.

Investor Context hash bindings remain eligible for version control, so a run
can prove which private context state was bound without publishing its body.
The block intentionally does not ignore Brain skills, Strategy skills,
`trading/research/`, `trading/decisions/`, postmortems, validated lesson state,
or the non-private portion of an analysis-run record.

Versionable Brain registry and run provenance never store an absolute local
source path. Workspace-contained sources use canonical workspace-relative POSIX
locators. External local sources retain public kind, revision, declared source,
and digest metadata without the machine-specific locator; updating one requires
the user to provide the explicit local source again.

`TRADINGCODEX_HOME` must resolve outside the generated workspace. Attach and
update reject an internal home before Git or database mutation, runtime home
resolution fails closed when the workspace boundary is known, and
`./tcx doctor` reports the same invariant. Use the platform home, a sibling, or
an OS temporary home for isolated validation. Hiding an internal runtime home
with `.gitignore` is not supported because receipt-signing keys and other
private runtime authority must remain physically outside versionable files.

`./tcx workspace status` exposes `git_root`, workspace-scoped `git_dirty`,
branch, and origin metadata. `./tcx doctor` verifies Git membership and the
privacy block and prints the repository root and workspace dirty state. A dirty
workspace is diagnostic, not a doctor failure: publication and history remain
under explicit user control.

A clean generated workspace must not contain:

- `package.json`
- Node MCP runtime files
- workspace-local canonical investment DB
- file-canonical `trading/orders/` or `trading/approvals/` state; order and
  approval records live in the central DB
- broker credentials or raw secrets
- pre-v1 `.tradingcodex/mainagent/head-manager.yaml` or
  `.tradingcodex/mainagent/subagent-registry.yaml` role/skill registry copies
- retired `.tradingcodex/capabilities.yaml`,
  `.tradingcodex/policies/roles.yaml`, or
  `.tradingcodex/policies/policy-bindings.yaml` capability copies
- duplicate `.tradingcodex/policies/principals.yaml`,
  `.tradingcodex/policies/information-barriers.yaml`, or
  `.tradingcodex/policies/access-policies.yaml` authority copies; principal and
  role boundaries come from the service registries, MCP registry, project-wide
  analysis sandbox, hooks, and role TOML

The source repository's React/TypeScript/Vite build does not change this
contract. Compiled viewer assets ship inside the Python package; attach and
update never copy `frontend/`, create `node_modules`, or invoke npm.

## Baseline Generated Contents

Generated workspaces contain a usable pristine investment baseline before the
operator adds a strategy or optional skill. The baseline is expected to support
source-aware research, causal analysis, explicit uncertainty, scoreable
forecast and calibration records, and method-appropriate evaluation. Those are
quality contracts to test, not a claim that every fresh workspace has already
produced enough resolved forecasts or blind reviews to demonstrate calibration.

Generated workspaces contain:

- one root `head-manager`
- nine fixed analytical and decision-support subagents; no execution subagent
- an immutable workspace manifest at `.tradingcodex/workspace.json`
- root `head-manager` identity loaded from `.codex/prompts/base_instructions/head-manager.md` through `.codex/config.toml` `model_instructions_file`
- sectioned Markdown base-instruction format for `head-manager`, including `# How you work`, TradingCodex guardrails, and tool guidelines
- Codex-style operating style in the root `head-manager` prompt: scoped
  `AGENTS.md` handling, concise preambles, selective planning, exact safe
  workspace reads, native `apply_patch` edits, dirty-worktree respect, concise
  maintenance handoffs, and brief chat replies that point to saved head-manager
  synthesis reports once accepted artifacts exist without making the saved
  research report shallow
- instruction/skill separation: root `head-manager` instructions own identity, durable safety boundaries, fail-closed dispatch, role boundaries, skill routing, optional-skill management, and approved action boundaries; fixed subagent TOML files own standing role identity, MCP/tool config, artifact walls, and always-on prohibitions; repo skills are dependency-light capability procedures for workflow maps, compact assignment-envelope templates, optional skill file management, quality gates, synthesis, and postmortems, without declaring role ownership or direct inter-skill call chains
- no-overlap handoff contract: each role owns its specialist question, downstream roles consume accepted artifacts, and missing/stale/weak upstream work returns `revise`, `blocked`, or `waiting` instead of being silently redone by another role
- dynamic Head Manager coordination: `$tcx-workflow` interprets the current
  mandate, begins a lightweight provenance run, chooses the smallest useful
  exact role, and revises or expands the team only after inspecting accepted
  artifacts; no Django classifier, selected-team record, staged plan, or server
  DAG decides the research sequence
- explicit scope preservation: prohibitions such as no valuation, order,
  approval, trading, or execution remain binding throughout the run. Head
  Manager reasons over those constraints directly; generated hooks do not use a
  natural-language keyword or negation router
- no default ticker team: a broad company prompt does not expand to a fixed
  analyst roster. Head Manager derives current questions, dispatches only roles
  justified by them, and reassesses from authenticated evidence
- compact hook context contains transport and run-binding facts only; Head
  Manager owns decision-quality, forecast, Investor Context, and method choices
- exact fixed-role V2 spawn: generated config exposes `agent_type` through the
  `agents` tool namespace; every task uses a fresh custom role, a compact
  assignment envelope, and `fork_turns="none"`; `followup_task` and generic
  role emulation are forbidden
- role-scoped web search: root `.codex/config.toml` sets `web_search="live"`
  so Head Manager can perform narrow planning reconnaissance before choosing or
  revising the team. Those results are untrusted planning leads, never accepted
  investment evidence, and every material fact must be reacquired by a producing
  role through an authenticated run-local artifact. The six evidence-producing
  custom agents also use live search; portfolio, risk, and judgment-review TOML
  files explicitly set `web_search="disabled"`
- fail-closed spawn hook: `PreToolUse` permits `spawn_agent` only for an exact
  registered `agent_type`, `fork_turns="none"`, an underscore-only task name,
  and a valid lightweight analysis run id in the compact message; no semantic
  plan or server task is consulted
- native Research permission runtime: `head-manager` and every fixed role
  inherit `trading-research`; ordinary shell and credential-free public HTTP
  are available, user-owned paths outside `trading/` are readable and writable,
  and disposable intermediates stay under `$TRADINGCODEX_SCRATCH`. Fixed roles
  stage one direct scratch-local `.py` file with native `apply_patch` and run it
  only through `./tcx-calc <filename.py>` or native Windows
  `.\tcx-calc.cmd <filename.py>`; system Python, heredocs, `-c`, `-m`, paths,
  and extra arguments are outside that contract.
  The `trading/` tree, control files, TradingCodex runtime/DB, protected
  artifacts, credentials, local/private destinations, and Unix sockets remain
  protected. Durable workflow, role-report, and synthesis writes go through
  authenticated service/MCP tools
- subagent hook isolation: `UserPromptSubmit` run-binding context is ignored for
  fixed subagent contexts so child briefs cannot replace the parent run binding
  or create recursive dispatch pressure
- deterministic root-native action interception: before allocating an analysis
  run, `UserPromptSubmit` reserves immediate `$tcx-order-submit` and
  `$tcx-order-cancel`, accepts only their complete exact `--name value`
  grammar from a root native user turn, creates a workspace-bound `native-user`
  mandate, and calls the canonical Django execution gateway in-process. It also
  reserves a first-meaningful-line `$tcx-order-allow --mode
  paper|validation|live`, issues one workspace/session/turn/prompt/mode-bound
  `OrderTurnGrant`, and then continues the normal workflow. Malformed reserved
  actions, subagent actions, and retired
  `$execute-paper-order` actions fail closed
- deterministic Build-turn admission: a root native prompt whose first
  meaningful invocation is a plain or matching projected-link `$tcx-build` and
  whose same-line or following body is non-empty issues a
  DB-canonical workspace/session/turn/cwd/prompt-bound `BuildTurnGrant` with
  `authority_scope=build`;
  `PreToolUse` requires it for direct writes and injects one-time proof into
  protected build MCP calls, while later turns and subagents cannot
  inherit it; Codex Plan mode cannot issue or use it, and the grant is bound to
  its issue-time permission mode
- deterministic managed-skill admission: a plain or matching projected-link
  `$tcx-brain` or `$tcx-strategy` on the first meaningful line of a root prompt
  issues the same DB-canonical grant shape with a
  distinct `brain` or `strategy` scope. The normal Research profile remains
  active, source/launcher use is allowlisted to that capability, cross-scope
  calls fail, and Build or order markers cannot be combined
- native tool containment: project config disables unified execution and
  interactive app/browser/computer features; both `PreToolUse` and
  `PermissionRequest` cover `Bash`, `exec_command`, and `write_stdin`. General
  calculation and public retrieval pass to the active native profile, while
  the hook still rejects interactive sessions, credentials, order effects,
  runtime/CLI mutations, global config, and remote publication
- main-to-subagent briefs are assignment envelopes, not role manuals: they carry
  the derived specialist question, explicit constraints, artifact language,
  artifact target, compact context summary, request-specific out-of-scope items,
  and return contract without repeating long method/source/guardrail checklists,
  forwarding the raw request, or pasting full artifacts
- role report storage is service-native: producing roles call authenticated
  `create_research_artifact`; the service derives producer identity, schema,
  content hash, and hashes for exact run-local `input_artifact_ids`. Roles do
  not hand-author run lineage or fall back to shell/edit writes
- Head Manager owns continuation: after reading exact returned artifacts it
  dynamically revises, adds a distinct role, requests independent judgment,
  stops, or synthesizes; there is no server supervisor-loop tool
- service-gated artifact reads: children and Head Manager retrieve exact
  returned bodies by artifact id through authenticated `get_research_artifact`;
  synthesis must name verified run-local inputs. Shell/glob report discovery is
  not part of the generated analysis contract
- narrow research-only briefs use an Evidence Quality Floor instead of thesis
  or decision-quality fields
- action-only native skills stay service-boundary focused on exact ticket,
  approval, broker-order, live-confirmation, and audit references instead of
  research source snapshots or thesis-quality fields
- verification-budget copy is task-specific: native actions, connector, and
  strategy work verifies its own service or validation evidence instead of
  research source-freshness fields
- context-efficient research handoffs: stored markdown frontmatter includes
  `context_summary` so downstream roles can consume artifact paths and summaries
  before opening full markdown; `reader_summary` and `next_action` keep the
  first-read experience clear for non-expert users
- context-budget audit: `./tcx subagents context-audit --strict` inspects
  subagent session state and research artifacts after long multi-agent runs; it
  fails strict mode when artifacts lack compact context summaries
- compact subagent session state: `.tradingcodex/mainagent/subagent-session-state.json`
  keeps total counters plus recent active/completed/event records for Codex
  context; the full event stream remains in
  `trading/audit/subagent-session-events.jsonl`
- lightweight analysis run state:
  `.tradingcodex/mainagent/runs/<analysis-run-id>/run.json` stores only run
  identity, request hash/size, timestamps, and sealed strategy/Investor Context
  provenance. Codex and subagent event streams plus authenticated artifacts are
  the observable workflow; there is no materialized team/DAG/terminal state
- the workspace viewer reads canonical artifacts and projection state only. It
  creates no browser-owned run state, launches no Codex process, and persists no
  reasoning, tool input/output, stderr, or final output. A reader-facing
  head-manager synthesis additionally requires accepted
  handoff, producer/body-hash binding, and verified run-local input hashes
- chained lesson events under `.tradingcodex/mainagent/improve.jsonl`, with
  authenticated latest-event heads in `lesson-chain-heads.json`, fed by
  reviewed postmortem lessons; these records
  are reusable investment-judgment context and never apply prompt, skill,
  policy, MCP, broker, approval, or execution changes
- Codex session/thread run-binding map:
  `.tradingcodex/mainagent/session-workflow-runs.json` maps a Codex app session
  key to the active `workflow_run_id`, so two app threads in one attached
  workspace can continue independent analysis runs without clobbering each other
- selected Investment Brain reference gate: a native read-only `cat` bundle may
  read only Markdown below the exact session-bound Brain projection's
  `references/` directory after the run has sealed the complete projected
  `skill_digest`; the bundle may contain multiple validated `cat` commands and
  literal `printf` headings joined only by `&&`, while unbound, stale, changed,
  unselected, executable or redirecting compounds, registry/package/source,
  generated-index, and role-config reads remain blocked
- current-request run binding: hook context supplies an optional run id and
  Head Manager calls the head-manager-only `begin_analysis_run` tool without
  storing the raw request
- a registry-projected v1 role policy: `gpt-5.6-sol`/xhigh for root
  `head-manager` and Terra/high for all nine fixed subagents, with no runtime
  fallback or rollback; final provider effects run through deterministic
  service code rather than an execution model
- `.tradingcodex/generated/model-policy-manifest.json` with policy revision,
  primary/resolved model, reasoning effort, required capabilities,
  prompt/tool-profile revisions, and `verified` or `unverified` support posture
- fixed subagent `nickname_candidates` set to a single item matching the exact role `name`
- fixed subagent identities kept in `.codex/agents/*.toml` `developer_instructions`, as required by Codex custom agent files
- project-local additional agent instructions under `.tradingcodex/agent-instructions/<role>.md`; projection appends them after generated default instructions for `head-manager` and fixed subagents as a managed overlay, without permitting them to replace core role, quality, policy, approval, or execution boundaries
- an immutable core/extension footer projected after project-local additional
  instructions for both `head-manager` and fixed roles
- planning-only root live web search in `.codex/config.toml`, with durable Head
  Manager and `tcx-workflow` instructions that prohibit using reconnaissance to
  answer the mandate or support synthesis claims. Live evidence search remains
  enabled for the six producing roles, while portfolio, risk, and judgment
  review explicitly disable it
- workspace customization preferences under `.tradingcodex/user/customization.json`, merged over `preferences/customization.json` in the canonical platform home; these files store UX/config metadata and never raw credentials
- thirty-three bundled repo skills across project-scope mainagent skills and
  subagent skill directories, each with `SKILL.md` frontmatter for document
  metadata and UI metadata when projected
- one compact bundled namespace: every core skill id is `tcx-` plus one suffix
  word when possible and no more than two. The namespace is reserved for
  bundled skills; user `strategy-*`, `investment-brain-*`, and optional role
  skills remain separate
- root `tcx-order-allow`, `tcx-order-submit`, and `tcx-order-cancel`
  native-only skill bundles with `allow_implicit_invocation: false`; the
  immediate bundles document exact action-only prompts, while `tcx-order-allow`
  documents current-turn admission and itself carries no proof or broker
  authority
- `tcx-automate` as Codex app Scheduled Task authoring across research,
  monitoring, recurring analysis, portfolio/status review, order drafting,
  assisted execution, optional turn-authorized execution, and explicitly
  delegated turn-authorized Build work. Saved runtime prompts invoke the
  selected work skill rather than recursively invoking `tcx-automate`; every
  Build run needs a fresh marker and file-mutating runs need the
  `trading-build` profile, while recurring Brain or Strategy management uses
  its own exact marker in `trading-research`
- `tcx-dashboard` as the read-only viewer entrypoint and user overview for
  current attention items, recent research, forecasts, portfolio/order posture,
  pending permissions, and broker state; it opens the Codex in-app browser by
  default, uses an external browser only on an explicit request, starts no
  analysis run, and mutates no TradingCodex workspace or service state
- decision-quality skill bundles for forecasting discipline, thesis scenario
  trees, numeric data QC, and anti-overfit validation, plus role-owned
  `tcx-judgment` for the independent `judgment-reviewer` gate
- standalone `strategy-*` skills under `.agents/skills/strategy-*` for user-approved agent-readable investment strategies, created from native Codex only in a matching first-meaningful-line `$tcx-strategy` root `trading-research` turn through the managed lifecycle service, or through an explicitly authenticated user/operator API surface, then exposed to the root `head-manager` through the strategy marker block in `.codex/config.toml`
- built-in `tcx-brain` projected only to Head Manager as the single source and
  managed-plugin entrypoint; source create/inspect/revise/validate/delete and
  managed list/inspect/install/update/activate/deactivate/rollback/remove stay
  distinct, tool-using management needs a matching first-meaningful-line `$tcx-brain` root
  `trading-research` turn, source
  authoring stops before lifecycle work, installation begins inactive, and no
  path implies Git/publication actions
- community `investment-brain-*` plugins installed from one explicit local or
  Git source into `.tradingcodex/investment-brains/`, retained as immutable
  versioned package copies, and projected only while active to
  `.agents/skills/investment-brain-*`; their metadata disables implicit
  invocation and only Head Manager's root config receives the projected path
- file-native agent/skill projection: head-manager and strategy skills live
  under `.agents/skills/*`, role-owned subagent skills live under
  `.tradingcodex/subagents/skills/*`, and role TOML embeds the allowed role
  skill source list. A child may read only its exact enabled `SKILL.md` files
  and Markdown references, including a strictly read-only batched `cat` form;
  other-role skills, role TOML, generated indexes, and runtime state remain
  blocked. State is expressed in `.codex/agents/*.toml`, `.codex/config.toml`,
  `.tradingcodex/mainagent/skill-change-proposals/*.yaml`, and
  `.tradingcodex/generated/*.json`, not Django skill DB tables
- optional subagent skills are created, updated, activated, archived, deleted, and validated through the shared application service used by `head-manager`, CLI, and authenticated API; the viewer only inspects them
- one project-wide native Research permission profile, a separately selected
  Build profile, and role-scoped MCP allowlists
- order/approval schemas
- restricted-list policy
- the sole workspace execution-policy input under
  `.tradingcodex/config.yaml` (`max_single_order_base`, enabled broker ids,
  enabled execution postures, and the live gate)
- built-in paper provider plus provider-driven validation/live gates
- audit directories
- central local SQLite service access through `state/tradingcodex.sqlite3` in the canonical platform home
- workspace identity through `.tradingcodex/workspace.json`
- workspace provenance through `TRADINGCODEX_WORKSPACE_ROOT`
- an internal active paper-account reference used as the selected portfolio/account/strategy scope
- optional workspace investor suitability context under
  `.tradingcodex/user/investor-context.md`, created only after a confirmed user
  update rather than during attach
- Python hook scripts callable from Codex hook commands
- generated indexes under `.tradingcodex/generated/`, including
  `module-lock.json`, `capability-index.json`, `component-index.json`,
  `agent-index.json`, `skill-index.json`, `model-policy-manifest.json`, and
  `projection-manifest.json`
- skill and projection indexes that identify each managed skill by id, layer,
  trust scope, implicit-invocation posture, and workspace-relative resolved
  source file; the same
  indexes declare `inventory_scope=tradingcodex_managed_workspace` and
  `runtime_discovery_complete=false` rather than pretending to inventory the
  whole host Codex runtime
- generated indexes are diagnostic projections, not an authorization source.
  The service runtime registries are the sole truth for capabilities, fixed
  roles, and MCP allowlists; the retired YAML capability and policy-binding
  copies are not generated
- append-only forecast ledger directory at `trading/forecasts/`
- immutable point-in-time research directories for specs, replay manifests,
  experiments, causal analyses, blind judgment priors/reviews, Dataset
  manifests/payloads/withdrawals, Calculation specs/runs, and the rebuildable
  SQLite+FTS v3 research-object catalog plus temporary legacy JSON exports
  under `trading/research/`
- research-only model-evaluation directories under `trading/evaluations/` for
  frozen corpora, control/candidate runs, blind reviews, and comparisons

## Skill Discovery Boundary

The managed workspace baseline consists of the projected core kernel, bundled
investment capability pack, and explicitly activated TradingCodex overlays.
Codex may also discover metadata for skills installed globally or supplied by
host plugins. Those host capabilities are outside the TradingCodex pristine
baseline and must enter a workflow only through explicit user opt-in for that
current workflow or a managed activation path that preserves role, quality,
policy, approval, and execution boundaries.

That explicit overlay rule applies to external skill procedures, not to
read-only app, connector, MCP, or data tools used as evidence. A role whose
assignment needs external data or preserves a user-named provider first checks
the current task's callable tool surface and uses the runtime's available
deferred-tool discovery surface when needed. It attempts the narrowest
relevant read-only call before a public web fallback. Sanitized capability
inventory is configuration evidence only:
installed/enabled state neither proves nor disproves that a tool was loaded
into the current task. A capability added or changed after task start may
require a new task or full Codex restart. Generated workspace configuration
does not override Codex's `features.apps` setting; user, organization, and
host policy continue to decide whether installed apps are exposed.

Workspace projection and role-local skill lists reduce accidental mixing, but
they are not by themselves proof of hard runtime isolation from every
host-discoverable skill. Documentation and release claims must not promise hard
isolation until clean-host, populated-host, name-collision, and invocation
smokes attest it. `doctor --layer improvement` verifies exact enabled managed
skill paths for root and every fixed role and fails on a same-name host-global
collision; a differently named host skill remains outside that finite managed
inventory and must be covered by invocation smokes.

## Method And Evaluation Profiles

The bundled capability pack declares method profiles so one analysis template
does not become a universal answer:

- `general_evidence_v1` for source-aware evidence synthesis
- `event_research_v1` for event chronology and causal impact analysis
- `quant_signal_v1` for signals, validation, costs, leakage, and overfitting
  controls
- `listed_equity_fcff_dcf_v1` for listed-equity FCFF valuation with explicit
  revenue, margin, reinvestment, risk, and sensitivity assumptions

`core_investment_v1` is the bundled pristine evaluation profile. Frozen corpora
may declare additional profiles with their own required tags and dimensions.
Profile declarations make method fit and comparison reproducible; they do not
prove forecast or analysis quality without populated frozen inputs, paired
runs, hard-failure checks, blind review, and resolved outcomes.

Generated `.codex/config.toml` enables MultiAgent V2 with visible spawn
metadata and the `agents` tool namespace. It keeps every
`.codex/agents/*.toml` role discoverable while setting
`features.multi_agent_v2.enabled = true`,
`max_concurrent_threads_per_session = 7`, and `agents.max_depth = 1`. The V2
session cap counts the root, leaving six child slots; TradingCodex reserves one
of those slots and plans at most five parallel fixed-role children. The
V1-only `agents.max_threads` key is absent because Codex rejects it when V2 is
enabled. Every dispatch names the exact custom `agent_type`, uses
`fork_turns="none"`, and avoids `followup_task`. Roster size is not a scheduler
concurrency promise, and no subagent may recursively dispatch another role.
Project thread policy bounds concurrency; Head Manager decides whether an
explicitly chosen roster needs one or more dispatch batches.

`TRADINGCODEX_MODEL_ROLLOUT=rollback` is rejected; generated workspaces require
the exact v1 role policy. Operators may provide
`TRADINGCODEX_CODEX_SUPPORTED_MODELS` as a comma-separated capability input; a
missing required selector fails generation rather than selecting a fallback.
Without that input the generated policy is intentionally reported as
runtime-unverified, so `doctor` checks projection consistency but does not claim
that a real Codex session has loaded the model.

Workspace template modules are deployment projections. Harness component
ownership comes from the Python component registry and is exported into
`component-index.json` for Codex-readable inspection.
Agent and skill ownership comes from the Python agent registry and is projected
into Codex-readable agent TOML plus generated agent/skill indexes.

User-configured Codex MCP servers, skills, and plugins remain native Codex
configuration. TradingCodex preserves their non-reserved project entries and
directories during update, and Codex exposes them to root and fixed-role agents
through normal configuration inheritance. TradingCodex owns only its reserved
entries and does not install, review, classify, or recommend user capabilities.
The read-only inventory covers canonical repository and user `.agents/skills`
locations plus compatible direct skills below `CODEX_HOME/skills`; plugin
component records use only top-level app, MCP-server, and hook identifiers and
never return component bodies or launch settings.

## Attach-First UX

TradingCodex is installed globally once, then attached to the workspace where
the operator wants to ask Codex agents to work.

Recommended agent-facing flow:

```bash
uv tool install tradingcodex
uv tool update-shell
cd <user-selected-workspace>
tcx attach .
codex .
```

`tcx attach .` is the user-facing CTA for adding the TradingCodex harness to a
new empty workspace. It refuses an existing TradingCodex workspace; use
`tcx update .` for an existing v1 workspace.

## Update UX

`tcx update .` is the explicit release-update command for an existing generated
v1 workspace. It requires a valid v1 `.tradingcodex/workspace.json` and
`.tradingcodex/generated/module-lock.json` with matching workspace identity and
a generated-file inventory. The module lock is an exact schema: it contains
only `format`, `schema_version`, `generated_at`, `workspace_id`,
`tradingcodex_version`, `tradingcodex_package_spec`, `tradingcodex_home`,
`home_source`, `tradingcodex_db_path`, `db_source`, `modules`, and
`generated_files`. Versions use canonical PEP 440 syntax and stay within the
running package's major version; status inspection alone may read a newer
same-major lock so it can direct the user to refresh the package. Module entries
contain exactly `id`, `description`, and `capabilities` with unique ids.
Generated-file records contain exactly a lowercase SHA-256 and an owner of
`template` or `projection`. Timestamps must be timezone-aware, workspace ids use
the v1 `tcxw_` identity format, home and DB paths are absolute, and path-source
values are closed enums. The writer applies this same validator before writing
the lock as the generation completion marker. A safe persistent source occupies
`tradingcodex_package_spec`; a local source uses the non-executable
`local-explicit` provenance marker instead of its machine path. The update
installs or refreshes both native launchers.

Update behavior:

- preserve immutable `workspace_id`
- preserve internal paper-account selection and investor-context state
- preserve per-run provenance and accepted artifacts
- preserve user-selected managed overlays and ordinary files that are not exact
  module-lock generated-file entries
- re-render generated template paths from the currently running package
- rebuild projection files and marked blocks from canonical Strategy, optional
  skill, Investment Brain, additional-instruction, and managed MCP state
- remove retired generated files only when their content still matches the
  recorded generated hash; modified retired files require manual resolution
- reject a symlink at any current generated destination or below any of its
  workspace-relative ancestors before Git, template, manifest, projection, or
  user-file writes begin
- refresh generated indexes under `.tradingcodex/generated/`
- refresh only the delimited TradingCodex privacy block in `.gitignore` while
  preserving user-authored ignore rules
- apply only the current v1/forward central DB migrations through the runtime
  path; pre-v1 databases fail before mutation
- persist the workspace context in the central DB
- run `./tcx doctor` unless `--no-doctor` is passed

Update consumes the frontend build already included in the Python package. It
does not install Node dependencies or run the Vite build.

Package-release updates should prefer a refreshing package invocation so the
latest package is fetched before rendering:

```bash
uvx --refresh --from tradingcodex tcx update . --from tradingcodex
```

Generated workspaces contain one common Python launcher at
`.tradingcodex/cli.py`, a POSIX `./tcx` shim, and a native Windows `tcx.cmd`
shim. The Python launcher owns package resolution, hook dispatch, home/service
environment, and update refresh behavior. Hook dispatch runs the generated
Python hook in the launcher process so redirected stdin/stdout remains intact
through the native Windows batch shim. On update it prefers
`uvx --refresh --from <recorded-package-spec>` when available. Windows users
run `.\tcx.cmd` in PowerShell; native Windows validation never treats the Bash
shim as executable evidence.

Calculation does not reuse that launcher or its package-bearing interpreter.
Attach/update provisions one `finance-*` calculation runtime v2 in a separate,
content-addressed platform cache and renders `tcx-calc` plus `tcx-calc.cmd`.
The wheel-only, per-artifact hash resource pins the complete 12-package direct
and transitive runtime around
`numpy==2.3.5`, `pandas==2.3.3`, `scipy==1.16.3`,
`statsmodels==0.14.6`, `numpy-financial==1.0.0`, and
`pyarrow==25.0.0`; source builds and agent-initiated `pip`/`uv` installation
are forbidden. A platform without a locked Python 3.11–3.14 wheel fails attach
clearly instead of compiling a dependency.

The launcher accepts one basename-only script already staged in the exact
workspace scratch, sanitizes environment first, starts Python with `-I -B -S`,
and then adds only the verified runtime site-packages path. It clears
service/DB/credential environment variables, maps home and temp to scratch,
bounds CPU, wall time, file count, file size, and normal text output, and
rejects links and oversized source. It never imports or starts Django, MCP, the
service, or the ledger. The runner also denies process creation and replacement,
shell/system execution, sockets and network access, package bootstrap, and
dangerous dynamic-library loading. This is a constrained calculation surface
inside the existing Codex OS sandbox, not a new Python security sandbox or a
Django-owned Python environment.

Without a service-authored sidecar, `tcx-calc` is exploratory compatibility
mode and cannot support an accepted research conclusion. Prepared mode binds
the script, declared input/output basenames, runtime lock, typed result schema,
cutoff, and CalculationSpec fingerprint. The runner permits only declared
scratch input/output plus runtime, stdlib, and required system-library reads,
and writes a bounded success or failure envelope for
`record_calculation_run`. `tcx doctor` verifies the launcher hash, runtime
manifest and lock hashes, exact package versions, and imports.

The POSIX shim resolves its canonical workspace root but does not `cd` back to
the same absolute path when it is already running there. This keeps the normal
workspace-root contract while avoiding a redundant path re-entry that native
Codex can reject for a disposable workspace below an otherwise denied temp
root.

Generated launchers and every project MCP entry persist one absolute, working
Python 3.11 through 3.14 interpreter. Generation prefers an explicit
`TRADINGCODEX_PYTHON` and validates that it exists, runs, and is not a
disposable uv cache environment. When canonical `uvx` attach/update runs from
`archive-v0` or editable `builds-v0`, TradingCodex provisions a versioned
private virtual environment under
`<TRADINGCODEX_HOME>/runtime/python/tradingcodex-*`, installs the selected
package there with copied files, verifies the actual MCP imports, and persists
that environment instead. For a local source directory, provisioning first
copies only runtime-relevant source into a clean temporary snapshot; ignored
build, distribution, cache, bytecode, local state, and database products cannot
reintroduce a file that was deleted from the checkout. Local directories and
archives are content-digested,
so an explicit update after changing installed source bytes selects a new
managed environment even when the package version did not change. A direct
remote source also binds the bytes of the package executing the refresh, so a
moving ref cannot silently reuse an older same-version runtime. `uv cache clean`
can then remove every builder/cache environment without breaking launchers or
MCP.

Before a generated launcher refreshes through a package runner, it carries the
previous durable interpreter through a private bootstrap-only variable. The
refreshed generator never treats that prior interpreter as
`TRADINGCODEX_PYTHON`: it provisions the new version/source-keyed runtime and
persists only that result. A caller-supplied `TRADINGCODEX_PYTHON` remains an
explicit override and fails closed when it is missing, cache-bound,
dependency-incomplete, or contains a different TradingCodex version. The POSIX
installer keeps cache enabled only for efficient provisioning; correctness does
not depend on that cache.

Executable package sources are validated before logging, rendering, status
generation, or package-runner execution. Credentialed URLs, inline secrets,
plain HTTP, URL queries or signed parameters, fragments, and control characters
are rejected, as are option-like values, unsupported schemes, remote `file:`
URLs, and SCP-style locators. Safe index or remote specs may be recorded for
refresh. A bare valid requirement such as `tradingcodex` remains a package name
regardless of same-named files in the current directory. Prefix a relative
local directory with `./`, or use an absolute path. A local directory, wheel,
archive, or `file:` source is different: its machine locator
and `PYTHONPATH` are never written to version-control-eligible workspace files.
The module lock records only `local-explicit`, and a later refresh must declare
the source again:

```bash
./tcx update --from /path/to/tradingcodex
```

The direct-checkout `--dev` and POSIX installer `--dev --update` forms satisfy
that repeated declaration without storing the checkout locator. They still
build a new source-content-keyed durable runtime when relevant source bytes
change; the outer development command imports the live checkout through an
editable package-runner environment, while the generated workspace never
executes through a source-tree `PYTHONPATH`. Development update preserves the existing local-explicit
workspace's recorded home and explicit DB override, then derives its service
address from that home. It rejects an in-place conversion of an index/release
workspace; use a separate development workspace instead.

The CLI uses PEP 610 installation metadata only to detect an otherwise
undeclared direct-source runtime and require `--from`; it never copies the
discovered locator into provenance. Ordinary PyPI
`uvx --from tradingcodex tcx attach .` retains the one-copy default UX.

Inside a generated workspace, normal `head-manager` and fixed-role analysis
threads inherit the `trading-research` permission profile. They can use normal
shell, data tools, and credential-free public HTTP, read ordinary
workspace inputs, and write user-owned files outside `trading/`; disposable
intermediates belong under `$TRADINGCODEX_SCRATCH`. Fixed-role Python
calculations use only the generated scratch-local `tcx-calc` contract. They
cannot modify
`trading/`, generated control files, or the TradingCodex home, DB, attached
runtime, protected artifact paths, credential files, local/private
destinations, or Unix sockets. Authenticated service/MCP tools own durable
TradingCodex writes. Harness update is either an explicit user-terminal
operation or a root native Codex turn whose first meaningful invocation is
`$tcx-build`,
because it rewrites the generated `.codex` prompt/config/hook surfaces that
define the current agent. The marker records current-turn intent but cannot
widen the active native profile. For already-installed packages, the wrapper
supports a user-terminal workspace-only path:

```bash
./tcx update --skip-refresh
```

`--skip-refresh` uses a Python environment where the package is already
importable or an installed `tcx` command, and avoids the package refresh step.
`./tcx update status` and update help are read-only and never invoke a package
runner. A mutating refresh fails clearly when neither `uv` nor `uvx` is
available; it never falls through to a successful-looking rewrite by the old
runtime. Durable runtime provisioning and validation finish before target Git,
`.gitignore`, or generated workspace files are created or changed.
If startup health reports `update_status.workspace_update_allowed=true`,
`head-manager` should tell the user to run
`update_status.workspace_update_command` from their terminal. If startup health
reports `update_status.package_update_required_first=true`, including when the
generated workspace and installed wrapper both match an older release, package
refresh is also a user-terminal action, normally:

```bash
uvx --refresh --from tradingcodex tcx update . --from tradingcodex
```

## Native Permission Profiles

Generated root config sets `default_permissions = "trading-research"` and
defines two custom profiles. It deliberately omits legacy `sandbox_mode` from
the root and every fixed-role TOML because any loaded `sandbox_mode` overrides
custom permission profiles in Codex. This contract requires Codex CLI 0.144.4
or later on a locally supported platform. Version 0.144.4 is the current
release-validation reference for permission profiles, hooks, required MCP,
deferred MCP calls, and the explicit V2 feature table. Older versions fail the
Codex runtime doctor check. These remain version-sensitive
surfaces, so release validation includes strict config/feature inspection and
a real native smoke.
See the [Codex permissions reference](https://learn.chatgpt.com/docs/permissions).

`trading-research` extends Codex's built-in `:workspace` profile, then
overrides `trading/`, generated
control files, Git metadata, launchers, and sensitive paths with more-specific
read or deny rules. User-owned paths outside `trading/` can therefore be used
as inputs or outputs without a Build turn. One generated, workspace-id-scoped,
cache-backed path is writable as `$TRADINGCODEX_SCRATCH` and is projected as
`TMPDIR`/`TEMP`/`TMP`; the broader temp roots are denied. The scratch root lives
under the platform cache tree (`~/Library/Caches/TradingCodex` on macOS,
`$XDG_CACHE_HOME/tradingcodex` or `~/.cache/tradingcodex` on Linux, and
`%LOCALAPPDATA%\TradingCodexScratch` on Windows), not inside the workspace and not in
the broad OS temporary tree.
The calculation runtime uses a separate sibling cache
(`TradingCodexCalculation`/`tradingcodex-calculation`) and is explicitly
read-only in Research and denied in Build. Neither cache overlaps the service
home, DB, workspace, Codex home, or the other cache.
Credential-bearing home paths such as Codex auth state, SSH/cloud/CLI config,
keyrings, credential files, and shell histories are explicitly denied. The
narrow read-only `~/.codex/packages/standalone` exception permits the installed
Codex runtime to execute native file tools without exposing Codex auth, config,
session, or memory state. The
narrow `.codex/proxy` read exception contains only the
Codex-generated network-proxy material required by command-line HTTPS clients.
More-specific rules deny the rest of `.codex`, canonical
TradingCodex home/DB/runtime paths, `.env` patterns, protected TradingCodex
workspace state, and durable research/report/forecast/decision paths. Research
does not reopen `.tradingcodex/cli.py` or the attached runtime for managed
lifecycle work. Matching `$tcx-brain` and `$tcx-strategy` invocations on the
first meaningful line create
separate current-turn scopes; the hook injects proof only into
`manage_investment_brain` or `manage_strategy`, and neither scope requires or
accepts `$tcx-build`.
The
parent `trading/` path is read-only, so connector and build-input changes remain
Build work even where a child path is not fully denied. Network
mode is limited with the native network proxy enabled, public domains allowed,
local/private destinations blocked, upstream proxying disabled, and no Unix
sockets. Public reachability does not grant credentials or broker authority.

`trading-build` uses the same credential and protected-state denials, writes the ordinary
workspace through admitted edit/service surfaces and the dedicated scratch path, but keeps `.git`,
`.agents`, and `AGENTS.md` read-only, denies `.codex`, TradingCodex home/DB/
runtime state, `.env` files, and audit/approval/order state. The exact managed
launcher runtime is reopened read-only so hook-admitted `./tcx` validation and
inspection commands can run. Across every active Build turn/profile, the hook
admits only a narrow shell review lane: limited workspace `pwd`/`cat`/`ls`,
inert provider-source reads/hash/diff/Git inspection, exact isolated
`python -I -S -m py_compile`, allowlisted workspace-launcher commands, and the
public retrieval forms below. General interpreters, helper scripts, test
runners, build systems, shell composition, and direct runtime commands remain
blocked. Its limited-public
network permits credential-free HTTP(S) GET/HEAD and public HTTPS Git retrieval
while blocking authenticated requests, bodies/uploads, local/private targets,
non-HTTP(S) transports, package installation, fetch-to-execute pipelines,
remote mutation, and broker access. Root `web_search="live"` supports only
Research workflow planning; the generated `PreToolUse` matcher covers every
tool name, and an active Build grant rejects native web search, browser, web,
HTTP, fetch, and navigation tools, so retrieval cannot reuse authenticated
browser state. Policy otherwise remains a no-op for ordinary Research tools;
Research browser behavior is unchanged.
The Build profile uses the proxy's full HTTP transport mode because Git Smart
HTTP needs a small POST exchange even for read-only clone/fetch/ls-remote.
That does not open general POST access: the Build hook admits only public
GET/HEAD curl or wget commands and the enumerated read-only HTTPS Git commands,
while rejecting general interpreters, shell composition, and other network
clients.
Durable research, report, forecast, and decision paths are denied in Build as
well because those writes remain authenticated service operations.
The root `$tcx-build` marker and hook remain required for controlled
`trading/` edits, generic Build launcher lifecycle commands, and protected MCP
mutations. Ordinary `apply_patch` edits outside `trading/` do not require the
marker; generic direct Write/Edit tools remain blocked so changes stay
reviewable. Brain source edits below `investment-brains/` require a Brain turn;
Brain/Strategy registry and projection lifecycle uses its matching
proof-protected MCP tool in Research and cannot cross into Build or each other.

The shell environment inherits only core process setup and then applies an
explicit allowlist for path, home, temp, locale, terminal, and native-Windows
runtime variables; API-key, secret, token, and broker patterns remain excluded.
The project MCP process is separate: its config supplies the canonical home,
DB selection, principal, service address, and workspace root explicitly to the
required `tradingcodex` stdio server. Those values are not forwarded to model
shell commands.

## Project-Scoped MCP Config

Generated Codex workspaces render a project-scoped
`[mcp_servers.tradingcodex]` entry in `.codex/config.toml`.

The config follows the OpenAI Codex MCP shape:

- stdio `command`
- `args`
- `enabled`
- `required`
- `env`
- `enabled_tools`
- `default_tools_approval_mode`
- `startup_timeout_sec`
- `tool_timeout_sec`

The built-in `tradingcodex` MCP server defaults safe enabled tools to
`approve` so routine research, audit, status, and reviewed service calls do not
bury Codex permission prompts inside subagent transcripts. No config exposes
raw approved-order submit, submitted-order cancel, or broker-order status
refresh mutations. Root Head Manager alone lists `use_order_turn_grant`.
The root and every fixed-role server are also `required = true`: a trusted
TradingCodex session or child fails at startup when its canonical MCP cannot
initialize instead of silently running without persistence, policy, or
execution services. Native Codex owns every root and fixed-role runtime.
Without a matching `OrderTurnGrant`, reservation by `PreToolUse`, and the
internal proof injected into rewritten tool input, its service handler rejects
the call. Fixed roles and direct callers receive no usable execution authority.
The service still revalidates permission, policy, ticket, receipt, requested
mode, duplicate-request state, connection, live confirmation, and audit before
any adapter call.

Each root or fixed-role MCP instance binds its immutable transport identity in
`TRADINGCODEX_MCP_PRINCIPAL`. A caller-supplied `principal_id` must match that
binding and cannot elevate the role. Direct CLI calls that intentionally act as
a role establish the same transport binding before entering stdio dispatch.
The environment value identifies a role; it is not a secret or a substitute for
the separate API/session authentication required by HTTP mutations.

Project-scoped Codex config applies only when the generated workspace is
trusted by Codex.

The generated TradingCodex MCP command uses the validated stable Python
selected during attach or update:

```text
<attached-python> -m tradingcodex_cli mcp stdio
```

Codex starts this required stdio bridge from its explicit command and MCP-only
environment without exposing that runtime binding to model shell commands. A
safe persistent package spec remains recorded as
provenance and is propagated to service/update checks; `tcx update` refreshes
the package and rewrites the interpreter binding. Local explicit sources instead
propagate only the non-secret `local-explicit` kind marker and run entirely from
the copied managed environment.

Codex may resolve a relative MCP `cwd` from the caller process rather than the
project selected with `-C`. Root and fixed-role MCP entries therefore record the
attached workspace's absolute path in both `cwd` and
`TRADINGCODEX_WORKSPACE_ROOT`, so run, artifact, receipt, and audit paths cannot
fall through to the source checkout or another caller directory. After moving
an attached workspace, run `tcx update .` before reopening it in Codex so these
generated bindings are refreshed.

TradingCodex owns only the `tradingcodex` MCP entry in project config. Other
MCP, plugin, and skill entries are user-owned Codex configuration and survive
update. Root live web search is limited by durable instructions to
planning-only reconnaissance; raw results cannot support accepted evidence or
synthesis claims. The six evidence-producing custom agents may use their
role-local live web setting and available native Codex capabilities to gather
public filings, disclosures, news, web sources, and market-data references
while the filesystem remains read-only. TradingCodex does not classify or
block those user-installed capabilities. Codex sandbox, approval, and organization
requirements still apply, while TradingCodex service policy continues to protect
its own principals, grants, order proofs, protected state, and execution path.
Current-task callable tools, not the System inventory or the TradingCodex MCP
allowlist alone, determine whether an external data call can be made. Agents
must try the narrow read-only tool before reporting a capability or data field
unavailable and must state separately whether configuration was discovered and
whether the current task exposed or successfully called the tool.
Broker APIs are attached through provider-driven TradingCodex connector profiles
using canonical MCP tools such as `list_broker_adapter_providers`,
`render_broker_connector_scaffold`, `register_broker_connector`,
`validate_broker_connector_build`, `get_broker_capability_profile`,
`get_broker_instrument_constraints`, and `preview_order_translation`.

Generated Codex config does not grant writable filesystem roots to analysis
threads. The TradingCodex service process owns the central DB, migration lock,
service status, and preference files; authenticated MCP calls own durable
workspace artifacts. Run service recovery, workspace update, or product changes
from the user terminal or an explicit `$tcx-build` root native turn whose actual
Codex sandbox permits the required writes. Do not add writable roots to analysis.

Codex-native capability management is deliberately absent from `tcx`. Users
install, enable, disable, and remove MCP servers, skills, and plugins with Codex
itself. Codex makes those capabilities available to root and fixed-role agents
according to its normal inheritance and policy. TradingCodex does not classify
or recommend them; its read-only inventory reports only sanitized component
metadata.

## MCP Autostart

The generated TradingCodex MCP config sets:

```text
TRADINGCODEX_MCP_AUTOSTART_SERVICE=1
```

This lets Codex MCP startup idempotently start the local Django viewer/service
process at the generated `TRADINGCODEX_SERVICE_ADDR` while keeping MCP stdio
stdout clean. Release workspaces default to `127.0.0.1:48267`. Unless
explicitly overridden, development bootstrap chooses a checkout-isolated
loopback port in the `20000`-`29999` range together with its isolated home and
DB.

If the port is already open, MCP startup verifies that the existing process is
a TradingCodex service with the same package version and central DB path before
using it.
When the existing process is an older TradingCodex service backed by the same
central DB, MCP autostart may stop it and launch the current package instead.
After starting a new process, explicit service ensure and MCP autostart allow
up to 30 seconds for the readiness endpoint on slower native hosts before
failing closed. `TRADINGCODEX_MCP_AUTOSTART_TIMEOUT` can explicitly override
the MCP startup wait. Local health and viewer probes bypass host HTTP proxy
settings so a system proxy cannot intercept the loopback compatibility check.
Service identity/readiness uses a direct loopback HTTP connection with a
two-second response allowance per probe so slower native runners are not
misclassified as an unrelated process. Port detection also rejects a macOS
ephemeral same-source-port self-connection instead of treating it as a
listening service. Detached startup preserves a separate redacted startup log;
an early exit or timeout reports its bounded tail together with the child
process state instead of discarding the only startup evidence. A child that
still has not become compatible at the timeout is stack-dumped where supported
and terminated so a failed ensure does not leave an orphan service process.
The local Django server records the literal bind address as its WSGI server
name instead of performing a reverse-DNS lookup during bind; loopback startup
therefore does not depend on host resolver latency.

The autostart path must be:

- idempotent
- silent on MCP stdout except for MCP protocol messages
- not required for direct `./tcx mcp stdio` smoke checks

Generated workspaces also support startup context for Codex sessions.
Bootstrap writes an initial compact diagnostic cache at
`.tradingcodex/mainagent/server-status.json`, and the `SessionStart` hook
refreshes it; neither path starts services, updates workspaces, opens browsers,
or performs package refresh on its own. The emitted context uses marker
`tradingcodex-session-context` and keeps only compact fields for
`build_authorization`, `permission_status`, `update_status`, `server_status`,
`allowed_next_actions`, and `routing_status`.

Build authorization retains `exact_first_line` and managed-skill authorization
retains `exact_first_lines` as canonical plain-token examples for copy/paste
compatibility. Both also report `invocation_position=first_meaningful_line`,
`accepted_forms`, and `same_line_request_allowed`.
These fields describe lexical admission only; the grant remains bound to the
original unnormalized prompt hash and current workspace/session/turn/profile.

`head-manager` uses `$tcx-dashboard` to open the read-only workspace viewer and
provide a compact orientation, and `$tcx-server` for diagnostics and recovery.
Dashboard uses hook startup context plus the smallest relevant read-only
workspace queries. Its default surface is the Codex in-app browser; it uses an
external browser only when explicitly requested and never launches either
surface through the shell. Server uses
hook startup context and the read-only status/update MCP tools, and returns
`./tcx service status`, `./tcx service
stop`, `./tcx service ensure`, and focused `doctor` commands only as explicit
user-terminal recovery steps; the general model shell does not run them. It tells the user that the
local workspace viewer is available at the URL reported by service status. The service commands default to the
generated `TRADINGCODEX_SERVICE_ADDR`, so a development workspace does not
silently fall back to the release port. If project MCP config was created or changed, the user
must fully quit and restart Codex and start a new thread because Codex may not
hot reload project MCP config.

Startup context preserves incompatible service detail from `./tcx service
status`, including `service_issue`, service/package versions, DB paths, and the
recorded next action. If the issue is `version_mismatch`, `db_mismatch`, or
`port_occupied`, `head-manager` must mention the startup notice in its first
user-facing response and avoid presenting the viewer as ready until the
recovery path is handled.

Startup health may compare the generated workspace version in
`.tradingcodex/generated/module-lock.json` with the installed/running `tcx`
package version and the latest known TradingCodex release. For a workspace-only
refresh, `head-manager` explains two paths: start a new `trading-build` root
native turn whose first meaningful invocation is `$tcx-build` and run the non-empty
`update_status.command`, or run that workspace command from a terminal.
Self-update is allowed only in that explicit current turn and only when the user
asks for it. When `package_refresh_user_terminal_required=true`,
`update_status.command` is empty and only
`interactive_user_terminal_command` may be run from an interactive user
terminal; `$tcx-build` never runs package refresh. After either update path,
fully restart Codex. If a
same-DB service is already running with a newer TradingCodex version than the
current wrapper, startup health treats that service version as an update hint
and should recommend package/workspace refresh before service stop.

## Build Turn Authorization

Mutating Codex build work uses an exact prompt contract, not a persistent
workspace mode. The first meaningful line must invoke:

```text
$tcx-build
```

A non-empty, concrete build request may share that line or follow it. The skill
may be a plain token or a Markdown link whose label and target match the
projected workspace `tcx-build/SKILL.md`. One UTF-8 BOM, normalized line
endings, and leading/trailing blank lines do not change admission; mixed or
duplicate managed markers still fail. `UserPromptSubmit` parses the complete
root native prompt deterministically and issues a DB-canonical grant bound to
the workspace, session, turn, cwd, and original prompt hash.
`PreToolUse` enforces that grant for controlled `trading/` edits and injects a
one-time internal proof into protected build MCP calls. Ordinary `apply_patch`
edits outside `trading/` do not need the grant; generic Write/Edit tools remain
blocked. The grant may support multiple Build edits and validations within that
turn, but every Build follow-up must start with `$tcx-build` again. The browser
viewer has no Build path, and subagents cannot create, inherit, or use the
grant.

The marker and hook never elevate Codex permissions. The active Codex profile
remains the filesystem and network authority. Use `trading-build` for
controlled `trading/` and managed lifecycle Build work; it opens connector and
build paths but keeps TradingCodex runtime/DB, credentials, and ledgers denied.
Only credential-free limited-public retrieval is available; local/private or
authenticated network access, uploads, package installs, fetch-to-execute
pipelines, remote mutation, and broker calls remain denied.
Codex Plan mode cannot issue or use a Build grant, and the grant is bound
to its issue-time permission mode; changing modes requires a new root turn and
fresh marker. Full access is not implied by the skill. Build work defaults to
workspace-local targets and cannot use the grant for global Codex config, raw
credentials, provider-source
approval, Git publication, direct edits to hooks, managed `.gitignore`,
credential files, policy or approval state, or order execution.
User capability management belongs to Codex; provider-source approval uses a
separate interactive user-terminal command and is not a synthetic Build turn.

Within `trading-build`, native `apply_patch` is the reviewable edit tool. Shell
access is intentionally non-general: Codex may use public HTTP(S) GET/HEAD,
enumerated read-only HTTPS Git retrieval, limited workspace `pwd`/`cat`/`ls`,
inert provider-source reads/hash/diff/Git inspection, exact isolated
`python -I -S -m py_compile`, and allowlisted `./tcx`/`tcx.cmd` commands.
Provider source retrieval is confined to
`$TRADINGCODEX_SCRATCH/provider-sources/<provider-id>/` and remains inert there.
The Build-turn context advertises an absolute command-proof form for native
Codex transports that do not include their actual shell workdir in hook input:
that form requires the recorded absolute executable, absolute staged operands,
and an absolute Git `-C` staging/repository root, so authorization never relies
on an opaque cwd, repository-local configuration, or cwd executable lookup.
Relative workspace commands remain valid only with the exact generated
workspace workdir; Git clone uses `--no-checkout`, and curl uses `--globoff`.
General interpreters, helper scripts, test runners, build systems, shell
composition, and model-authored POST are blocked throughout the active Build
turn/profile. Broader unit, smoke, or build validation belongs to an explicit
user-terminal or maintainer flow. The hook routes every direct edit and trusted
workspace-launcher lifecycle command through the current-turn grant and retains
hard stops for protected paths, credentials, global config, Git publication,
and order effects. The native profile supplies
the lower-level filesystem and network boundary.

Persistent `tcx mode` is retired. `./tcx mode status` remains only as an inert
compatibility diagnostic, `tcx mode set ...` cannot enable Build, and any old
`.tradingcodex/runtime/mode.json` file is ignored and grants no authority.

An explicit Build turn may update TradingCodex, templates, and broker/API
provider scaffolds, including live-capable provider code. It never submits live
orders; live submission remains behind the service gates. Update recommendations are scoped
to the new-conversation health pass, not every user turn. If the user declines
update prompts, `head-manager` records `preferences/update.json` below the
canonical TradingCodex home with
`suppress_update_recommendation=true`; future new conversations should not
recommend automatic workspace updates unless the user removes or changes that
flag, or explicitly asks for an update.

Codex app Scheduled Tasks use the same prompt path without a trusted Automation
origin. Recurring Build work is allowed only when the user deliberately saves a
runtime prompt beginning with the exact `$tcx-build` line. Every scheduled run
receives a fresh current-turn grant decision and remains subject to that run's
actual Codex profile. Controlled `trading/` or managed lifecycle changes
for optional role skills require `trading-build`. Recurring Brain or Strategy
management begins directly with its matching exact skill marker in
`trading-research`; a `trading-research` run may read and write user-owned
paths outside `trading/`, use temporary computation and credential-free public
retrieval, perform rendering/inspection, and call specifically proof-protected
canonical DB services. Plan mode blocks every managed workspace grant.
Prefer an isolated worktree or workspace and retain a reviewable diff for every
recurring run. Never combine Build, Brain, Strategy, or order markers.

In a Build turn, Head Manager first inspects available providers. When the
requested provider is missing, it develops that provider before rendering or
registering a connector. A `provider_development_required` connector is created
first only when the user explicitly requests scaffold-only output; an
implementation or connection request does not leave behind a dead-end scaffold
as apparent progress.

Credential-free public HTTP(S) and HTTPS Git sources may be retrieved only into
`$TRADINGCODEX_SCRATCH/provider-sources/<provider-id>/`. The staging tree is for
read, hash, diff, and static validation only; fetched code is not executed or
installed. For a new provider sourced by direct HTTP(S) rather than Git, the
hook admits one canonical directory-creating fetch: one curl command with
`--create-dirs`, one URL, and one explicit
`--output <provider-id>/<file>` relative to the provider-sources staging root
(or the equivalent absolute staged output when hook input omits workdir). It
may create only that one fresh direct provider-id directory. It does not admit
general directory creation, a nested output path,
`--remote-name`/`--output-dir` forms, a repeated `--create-dirs`, or an
already-existing destination directory. Later HTTP(S) files require an
existing real direct provider parent and omit `--create-dirs`. Final provider
files are authored with `apply_patch`, never by downloading, redirecting,
copying, or moving content directly into `trading/`.
Externally informed bundles include `source-provenance.json` with
`schema_version: 1` and per-source `kind` (`https` or `git`), a public
credential-free HTTPS `url` without userinfo/query/fragment, optional
`requested_ref`, and exactly one resolved identifier: HTTPS uses
`resolved_ref`, while Git uses `resolved_ref` or `resolved_commit`. Each entry
also includes `fetched_content_sha256` and RFC 3339 `retrieved_at`. Existing manually
authored providers remain compatible without that optional file. Provider
bundles reject `.git`, `.hg`, `.svn`, credential/key/`.env` material, and
symlinks. AST/syntax, `py_compile`, and static contract checks do not import the
provider.

After the provider is approved, the service is restarted, and a fresh Build
turn begins, Head Manager uses the read-only
`render_broker_connector_scaffold` MCP tool. Its content-addressed result
contains each workspace target, rendered content/hash, and exact preimage
existence/hash/size metadata but never existing file content. It performs no
workspace write. Head Manager verifies the preimages and applies the files with
`apply_patch`, so Codex's native workspace permission remains the filesystem
authority. Only the DB-backed `register_broker_connector` and
`validate_broker_connector_build` calls consume protected Build proof. Direct connector `connect` and write-style
`scaffold` remain explicit user-terminal operator flows and are absent from the
agent MCP surface.

Broker provider build work is separate from the running operate server. A
generated workspace may already have TradingCodex MCP autostarting the Django
service; mutable workspace `provider.py` files are untrusted and are never
loaded merely by listing providers. `inspect-provider` reports the secret-free
source provenance summary and final bundle hash. When Build's central-ledger
filesystem denial prevents an approval-state read, it falls back to an inert
bundle-only result marked `service_check_required`; the later interactive
operator inspection and approval still resolve canonical approval state from
the service ledger. The user must inspect the bundle and approve
its exact bundle hash from an interactive terminal with `./tcx connectors
approve-provider <provider-id>`; piped stdin and agent, MCP, API, Admin,
browser viewer, or Automation calls cannot approve or revoke source. Approval copies
the reviewed `provider.py`, optional provenance, and its symlink-free supporting source bundle to an
immutable managed snapshot but executes no code, and any bundle or workspace
path, provenance, or helper change invalidates it. Runtime imports only the rehashed snapshot after a
restart, never the mutable workspace copy. Connector profiles record provider
hashes, status calls report `service_restart_required`, and live execution
remains blocked until the service is restarted and connector render,
registration, and validation complete in a fresh Build turn.

## Hooks

Generated hooks are Python scripts. Hook behavior is guidance, not final
enforcement.

For native Codex runs, project command rules and `PreToolUse` also reject agent
shell commands that select a role/principal or mutate workflow, research,
forecast, evaluation, or order state. Those mutations use the authenticated
project MCP transport. A Build turn has only the exact safe read, trusted
workspace-launcher, and isolated provider-syntax command lane described above;
operator workflows and full validation remain usable by a human in a terminal
because hook policy is an agent-runtime boundary.

`UserPromptSubmit` handles:

- deterministic interception of the two literal immediate root-native action
  tokens before analysis-run allocation; an exact full-prompt grammar
  dispatches the service-owned gateway as `native-user`, while malformed,
  subagent, and retired action forms fail closed
- deterministic parsing of first-meaningful-line `$tcx-order-allow --mode
  paper|validation|live`; it requires current root `session_id` and `turn_id`,
  revokes an older session grant, issues a workspace/session/turn/full-prompt/
  mode-bound single-use `OrderTurnGrant`, and continues normal analysis
- deterministic parsing of matching first-meaningful-line `$tcx-build` with a
  non-empty body; it requires the current root session/turn/cwd, revokes an
  older session Build grant, and issues a DB-canonical current-turn grant that
  never elevates the Codex sandbox; Plan mode cannot issue the grant and tool
  use must match its bound permission mode
- deterministic parsing of matching first-meaningful-line `$tcx-brain` and
  `$tcx-strategy` prompts with non-empty bodies; each issues only its own
  managed capability scope in `trading-research`, rejects marker combinations,
  and bypasses investment-analysis run allocation
- identical handling for interactive and Codex app Scheduled Task turns; the
  saved prompt is submitted each scheduled turn and no Automation-origin
  detector participates
- transport/run binding context only
- duplicate marker management
- run audit metadata with prompt hash and byte count, without raw
  prompt text in the audit ledger
- compact hook `additionalContext` instructing Head Manager to interpret the
  request directly and call `begin_analysis_run` for investment analysis
- no natural-language prompt classification, semantic lane, selected team,
  DAG, or supervisor state; literal reserved execution-token recognition is a
  fixed action protocol, not an intent classifier
- native strategy application requires exactly one explicit `$strategy-*`
  invocation; Head Manager seals the selected strategy and saved
  Investor Context in the lightweight analysis run
- startup diagnostics: `SessionStart` records compact build-authorization,
  permission, update, service, and routing status for `head-manager`
- update recommendation diagnostics: `SessionStart` records package/workspace
  drift and respects the TradingCodex home update preference file

`PreToolUse` handles fixed-role dispatch separately from prompt transport. It
requires exact registered `agent_type`, `fork_turns="none"`, an underscore-only
task name, and a valid lightweight analysis run id in the compact message. The
decision is written as a redacted hook audit before allow/block is returned;
audit failure blocks dispatch. The hook never chooses the role or next step.

For `use_order_turn_grant`, `PreToolUse` additionally rejects subagents,
missing session/turn/tool-use ids, caller-supplied proof, expired or mismatched
grants, and replays. It reserves the grant for the tool-use id and rewrites the
MCP input with internal proof. The service consumes that proof before one
submit or cancel and runs the canonical gates. The grant expires after one hour
and is revoked on `Stop`, the next user turn, or consumption. Neither hook nor
service claims to convert free-form prompt scope into policy; deterministic
scope is limited to canonical policy, ticket, receipt, action, broker posture,
and mode state.

Once consumed, a grant whose result remains `authorizing` represents an
in-flight canonical effect. Stop and new-turn cleanup never reset it, and the
same session blocks any new Build or order-sensitive prompt until the result is
terminal. Ordinary research and canonical status inspection may continue.

For controlled `trading/` edits and protected build MCP calls, `PreToolUse` rejects
missing or mismatched Build grants, subagent callers, caller-supplied proof,
off-workspace targets, global config, raw credential access, protected runtime
or financial state. `UserPromptSubmit` rejects marker combinations with
`$tcx-order-allow`. Protected MCP calls receive a one-time internal proof; the
parent Build grant remains usable for further in-turn Build editing and validation.
An unstarted reservation lease expires after two minutes; an already-started
service call instead finishes before a pending `Stop` or new-turn revocation
takes effect, so the same grant cannot be reissued into an in-flight call. If
post-effect grant finalization fails, idempotent recovery records the
`finished_unfinalized` state, revokes the grant, and never reruns the protected
operation.

`PostToolUse` audit is always metadata-only for native runs. It
stores the event, current run id, tool name, and `redacted=true`; it never
persists tool input, tool response, command output, or artifact bodies.

Generated project config pins `features.hooks=true`. Codex still loads the
project config, MCP server, and hooks only for a trusted workspace, and a
managed policy may force hooks off. In either case native execution is
unavailable and must fail closed; trust the attached workspace and review its
hooks instead of using a shell or public-surface fallback.

## Workspace Provenance

Generated workspace wrappers derive `TRADINGCODEX_WORKSPACE_ROOT` from their
own location. Generated hook commands use that relocatable wrapper, and Codex
skill paths are relative to their declaring project config. MCP servers are the
exception: their generated `cwd` and `TRADINGCODEX_WORKSPACE_ROOT` record the
absolute attached workspace so a caller's cwd cannot redirect durable workflow
state. Run `tcx update .` after moving the workspace to refresh that binding.
The generated contract persists only the validated external-home managed or
explicit stable Python binding, never a uv cache/package-runner interpreter. A local package spec explicitly
supplied through `--from` remains recorded as intentional MCP/update provenance
and uses editable execution during source development; the generated launcher
passes its prior stable binding across that refresh.

The wrapper and project MCP config retain the canonical `TRADINGCODEX_HOME`,
its `TRADINGCODEX_HOME_SOURCE`, and `TRADINGCODEX_SERVICE_ADDR` selected at
attach/update time. `.tradingcodex/generated/module-lock.json` records
`tradingcodex_home`, `home_source`, the rendered DB path, and `db_source`. An
explicit `TRADINGCODEX_DB_NAME` override is retained in the common Python
launcher and every generated MCP environment instead of falling back to the
home-default ledger. The generated
`.tradingcodex/config.yaml` uses that canonical DB path rather than a tilde
literal. Service status, ensure, stop, and default runserver use the recorded
service address projected by the launcher. Explicit process environment values still override recorded values;
doctor then validates both home and DB projection and requires update when the
generated ledger contract is stale.

An index/package-runner update such as `uvx --refresh --from tradingcodex tcx
update . --from tradingcodex` does not pass through the generated wrapper.
Before regeneration, update therefore restores a validated workspace's
recorded explicit home and DB override plus the service address from its
generated project MCP configuration. It does not pin a recorded
`platform_default` absolute path, so a copied workspace can resolve the native
default on its destination platform. A new explicit process value remains the
only implicit-free way to select a different runtime identity.

TOML, YAML, JSON, POSIX shell, CMD, and Python values are serialized with
format-specific literals. This is required for macOS paths with spaces and
Windows drive-letter/backslash paths. Generated code/config uses LF line
endings; executable bits are applied only on POSIX. Hooks select `./tcx` on
POSIX and `tcx.cmd` on native Windows. If a workspace is copied between
platforms, run `tcx update` from the installed package on the destination before
opening it in Codex so launcher, hook, writable-root, and MCP projections match.

The workspace-root value identifies which Codex project called the service. It
is provenance, not a DB selector or paper-account identifier; immutable
workspace identity and the selected internal account scope remain explicit.

`.tradingcodex/workspace.json` stores immutable workspace identity:

- `workspace_id`
- project name
- internal active paper-account reference
- MCP scope
- execution mode

`path_hash` remains path provenance. It is not the durable workspace identity.

## Internal Paper Account Scope And Investor Context

The attached Codex workspace is not an investment ledger. Paper portfolio
state remains scoped internally by:

- `profile_id`
- `portfolio_id`
- `account_id`
- `strategy_id`

Newly attached workspaces receive an isolated paper account scope derived from their
immutable workspace id. This prevents a fresh workspace from silently opening
another workspace's draft orders or paper portfolio as its default context.

The profile CLI creates and selects explicit workspace-local paper account
scopes; it is not the user-facing investor-context model:

```text
./tcx profile create <profile-id>
./tcx profile select <profile-id>
./tcx profile update --base-currency EUR
```

Order and portfolio commands use the selected internal scope when an order does not
provide explicit portfolio/account/strategy ids. Each profile also carries a
validated three-letter base-currency code; native-currency orders require a
point-in-time FX snapshot before policy compares their converted notional with
the profile's base-currency limit. New profiles start with `USD` as an explicit,
changeable bootstrap default rather than a market-specific policy constraint.

Investor suitability context is separate and workspace-local:

```text
.tradingcodex/user/investor-context.md
./tcx investor-context status
./tcx investor-context update --objective "long-term capital growth" --horizon "5+ years"
./tcx investor-context enable
./tcx investor-context disable
./tcx investor-context clear
```

The file is created on the first confirmed update, uses schema-versioned YAML
frontmatter plus optional Markdown notes, and records its default application
state and content hash. Native Codex run binding always follows the saved default
and seals any applied context into the run. The viewer exposes no one-run
override and does not change the saved default. The workspace file
is the only Investor Context source. See
[decision-memory.md](./decision-memory.md) for privacy, application, and
strategy/memory boundaries.

The v1 workspace manifest uses `format = tradingcodex.workspace` and schema 1,
records the complete account scope and base currency explicitly, and rejects
unsupported formats. Paper state likewise requires its current money schema;
TradingCodex never guesses or relabels an ambiguous balance.

## Optional Global Home MCP

Project-scoped MCP remains the approved action boundary. An optional global Codex MCP
server can be installed with:

```text
./tcx mcp install-global --safe
```

The global server name is `tradingcodex-home`. It is read-only/safe-scope only
and must not expose approval, execution, cancellation, policy mutation, secret,
or broker tools.

## Bootstrap Verification

Codex-native bootstrap verification:

- `./tcx doctor` checks generated project MCP server shape and role allowlists.
- `./tcx doctor` also checks the calculation launcher hash, `finance-*` runtime
  v2 manifest and lock hashes, all 12 exact package versions/imports, and emits
  a warning rather than disabling structured catalog search when SQLite lacks
  FTS5.
- `./tcx update --no-doctor` verifies the generated update path without running
  the full doctor twice in installer smoke tests.
- `./tcx mcp stdio` `tools/list` verifies the TradingCodex MCP bridge and tool annotations.
- MCP `tools/list` contains no raw submit, cancel, or broker-status-refresh
  mutation. It contains the proof-protected `use_order_turn_grant` only for
  Head Manager, and generated fixed-role configs contain neither that tool nor
  a retired `execution-operator` role.
- `list_codex_capabilities` verifies the sanitized read-only native Codex
  capability inventory and partial-warning behavior.
- Generated Codex MCP config starts the stdio bridge through the attached
  Python interpreter and starts the local viewer/service process when autostart is
  enabled.
- The installed wheel serves the committed read-only SPA shell and assets
  without Node; native Codex uses the attached workspace's generated
  `head-manager` contract.
- Direct `./tcx mcp stdio` remains service-free unless `TRADINGCODEX_MCP_AUTOSTART_SERVICE=1` is set.
- `codex exec -C <workspace> --skip-git-repo-check ...` launched without a
  command-line sandbox override from another directory can verify that Codex
  loads the generated `trading-research` profile and binds MCP state to that
  workspace. The canonical workspace must
  either be interactively trusted or receive the one-run `projects={...}` trust
  override shown in `AGENTS.md`. A full fixed-role lifecycle smoke must use
  persisted trust for all eight generated project hooks. Codex 0.144.4 does not
  carry the one-run `--dangerously-bypass-hook-trust` flag through the role
  config reload used by an exact V2 child, so that flag may be used only for
  root/config diagnostics and is not lifecycle acceptance. It also does not
  activate an untrusted project config.

The management command `codex mcp list/get` may show only user/global MCP
servers, even when a session uses project-scoped MCP config after workspace
trust.

## Template Change Rule

Hand-editing a generated workspace in an OS temporary directory is only a
smoke/debug step.
Durable behavior changes belong in `workspace_templates/modules/*`, docs, and
tests. After changing bootstrap behavior, regenerate a clean workspace for
verification.

Template contract tests must cover:

- every `module.json` id matches its directory name
- every declared module dependency exists
- the default module graph resolves
- generated workspaces keep the public output paths and avoid Node runtime
  files, Python bytecode caches, broker secrets, and workspace-local canonical
  investment DBs

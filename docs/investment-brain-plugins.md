# Investment Brain Plugins

Status: the Phase 0, Phase 1, Phase 1A, and Phase 1B v1 contracts below are
implemented in the working tree. Phase 2 and later describe optional future
distribution and community work; they are not prerequisites for local v1 use.

## Product Decision

TradingCodex is the investment operating system. An Investment Brain is a
community-distributed, high-freedom TradingCodex plugin that changes how Head
Manager frames investment questions and interprets evidence. TradingCodex owns
its installation, validation, activation, local registry, projection, and run
provenance. Codex skill format is the execution representation projected from
the plugin, not the plugin ownership or distribution boundary. An Investment
Brain is not a TradingCodex workflow, role registry, strategy, memory store,
policy package, or execution extension.

Users install Investment Brain plugins through TradingCodex from an explicit
local or Git source. TradingCodex does not operate a public marketplace, mutate
the upstream source, or copy Brain instructions into fixed-agent
configuration. After validation, TradingCodex projects the plugin's Brain skill
into the workspace. A user opts into that projected Brain for a native Codex
task with one exact `$investment-brain-*` skill invocation.

The built-in `$tcx-brain` skill is the single user-facing manager for local
source create/inspect/revise/validate/delete and installed plugin
list/inspect/install/update/activate/deactivate/rollback/remove. It delegates
managed state to the canonical application service and never turns source file
editing into a parallel registry or projection path.

Decision Memory remains workspace-file-native and user-owned. Installing a
Brain never uploads, rewrites, or packages that memory.

This plan deliberately keeps the product Codex-native. TradingCodex supplies
the durable safety, provenance, plugin, and artifact boundaries; Head Manager
owns live interpretation and workflow judgment. No server-side semantic router,
precompiled investment DAG, or Django workflow state machine decides which
analysts a question requires.

## Architectural Guardrails

- Do not add a Django app, database model, or migration for Investment Brains.
  The registry, immutable package copies, projections, and provenance remain
  workspace-file-native.
- Do not restore lane classifiers, keyword routing, default analyst teams,
  staged workflow plans, or a server-generated DAG. The hook may bind compact
  run context, but it must not choose roles or task order.
- Do not let a Brain name agents, tools, models, sandboxes, artifact paths, or
  workflow steps. Head Manager translates its questions into a dynamic plan.
- Do not merge Brain, Strategy, Investor Context, and Decision Memory into one
  generic preference layer. Their authority and lifecycle differ.
- Do not make community publication a prerequisite for local use. A user can
  install a private local or Git-hosted Brain without a marketplace.
- Do not add v0 migration, compatibility, or fallback behavior to this clean
  v1 contract. Future schema versions must be explicit and backward-readable.

## Product Layers

| Layer | Owns | Does not own |
| --- | --- | --- |
| TradingCodex Core | evidence provenance, point-in-time discipline, roles, tool boundaries, policy, approval, execution, audit, run integrity | investment style or community doctrine |
| Current user mandate | the task, explicit prohibitions, selected Brain/Strategy, one-run context choices | safety or execution bypass |
| Investor Context | suitability, horizon, liquidity, concentration, and user constraints | investment doctrine or factual claims |
| Strategy | an explicitly selected decision policy, eligibility, entry/exit, sizing, and risk rules | role dispatch, factual authority, or safety overrides |
| Investment Brain | hypotheses, inquiry priorities, interpretation principles, causal frames, scenarios, falsifiers, and abstention heuristics | TradingCodex roles, tools, workflow, persistence, policy, or execution |
| Method skills | bounded analytical procedures such as event research, DCF, technical analysis, or anti-overfit validation | portfolio mandate or user suitability |
| Current-run evidence | authenticated facts, source/as-of posture, conflicts, and uncertainty | authority to approve or execute |
| Decision Memory | prior decisions, forecasts, outcomes, postmortems, and validated lessons as evidence | automatic override, automatic Brain mutation, or external publication |

This is a typed authority model, not one flat prompt-priority list. Core safety
always wins. The current mandate selects scope. Investor Context limits
suitability. Strategy governs decision policy. A Brain supplies reasoning
heuristics. Current evidence can falsify a Brain or Strategy assumption.
Decision Memory can inform a judgment but never wins by authority.

## Investment Brain Contract

An Investment Brain is a TradingCodex plugin bundle with a manifest and one
projectable Codex skill payload:

```text
investment-brain-example/
├── .tradingcodex/
│   └── plugin.json
└── skill/
    ├── SKILL.md
    ├── agents/openai.yaml
    └── references/
        ├── philosophy.md
        ├── inquiry.md
        ├── interpretation.md
        └── falsifiers.md
```

The v1 manifest is deliberately small and strict:

```json
{
  "format": "tradingcodex.investment-brain",
  "schema_version": 1,
  "type": "investment-brain",
  "id": "investment-brain-quality-growth",
  "version": "1.0.0",
  "skill": "skill",
  "source": {
    "publisher": "Example Research Collective",
    "repository": "https://example.com/example/quality-growth-brain",
    "license": "MIT"
  }
}
```

The manifest identifies the plugin type, stable `investment-brain-*` id,
stable `major.minor.patch` version, fixed skill payload, and declared source
metadata. The skill bundle may contain `SKILL.md`, `agents/openai.yaml`, and
Markdown references only; scripts, assets, symlinks, and arbitrary executable
payloads are outside the v1 Brain contract. TradingCodex validates the
bundle, computes the installed content digest, records its local activation
state, and projects only the skill payload into the Codex workspace. The
projected `agents/openai.yaml` must set
`policy.allow_implicit_invocation: false` so Brain selection remains explicit.
The optional declared `repository` is publication metadata, not an install
locator. It must be a credential-free public HTTPS repository URL with a
repository path; local/file paths, SSH locators, private hosts, custom ports,
queries, and fragments are rejected before registry or run provenance changes.
Numeric address aliases and special-use DNS names such as loopback shorthand,
`.localdomain`, `home.arpa`, `.invalid`, and `.test` are not public hosts and
are rejected as well.

Validation snapshots the exact accepted bytes before the immutable package is
written, so a local source changing between validation and installation cannot
change the installed payload. v1 limits the manifest and metadata to 64 KiB
each, `SKILL.md` to 256 KiB, each Markdown reference to 512 KiB, and the whole
bundle to 4 MiB. A bundle may contain at most 64 reference files, 32 reference
directories, 96 reference entries, and four reference path levels. Directory
enumeration, Git tree output, file reads, subprocess output, and package copying
are bounded; non-regular files, invalid UTF-8, YAML aliases/anchors/tags, extra
bundle-root entries, and validation-to-copy content changes fail closed. Git
sources use authenticated HTTPS or SSH, or an explicit local/file source;
unauthenticated `git://` and executable remote-helper transports are rejected.

The skill should remain platform-neutral and high freedom. It may describe:

- what constitutes an attractive opportunity;
- which hypotheses and questions deserve priority;
- how to distinguish facts, expectations, causality, and narrative;
- which evidence changes the view;
- how to form scenarios and falsifiers;
- when the framework is inapplicable; and
- when to abstain.

It must not name or select TradingCodex roles, call TradingCodex MCP tools,
define task order or parallelism, set model or sandbox configuration, prescribe
artifact paths, read secrets, modify Decision Memory, or grant policy,
approval, broker, order, or execution authority. Head Manager translates the
Brain's platform-neutral inquiry into the current fixed-role team.

Validation enforces the machine-verifiable part of this boundary: the bundle
has no executable payload, implicit invocation is disabled, and reserved
TradingCodex paths, role ids, skill tokens, and runtime/tool identifiers are
rejected. TradingCodex does not pretend an English keyword denylist can prove
the meaning of arbitrary multilingual prose. Core Head Manager instructions,
fixed-role projection, the native `trading-research` permission profile, MCP allowlists, and service policy
remain the authoritative boundary even when a Brain's prose is adversarial.

The reserved compatibility namespace is `investment-brain-*`. One native
analysis may select at most one Primary Brain. Knowledge or method skills may
still be used separately, but another Primary Brain requires a new independent
run. TradingCodex does not infer a Brain from natural-language resemblance.

### Trust And Resource Boundary

Installation treats every bundle as untrusted input. Validation must happen on
a materialized snapshot before projection and must enforce a bounded number of
regular UTF-8 files, bounded file and aggregate sizes, bounded reference depth,
strict manifest/frontmatter/metadata schemas, and no symlinks, devices, YAML
object tags, anchors, aliases, scripts, or executable assets. Digests are
computed from the exact validated snapshot that is copied into the immutable
package store, closing validation-to-copy races.

Git installation accepts one explicit repository and ref, resolves an exact
commit, and never executes repository hooks, filters, submodules, or external
protocol helpers. TradingCodex-owned Git subprocesses ignore inherited
repository/index/worktree overrides and unsafe global/system configuration.
Clone and checkout remain non-interactive and resource-bounded. Source URLs
shown in diagnostics or provenance must remove credentials, query strings, and
fragments.

Activation and run binding fail closed when a host-global Codex skill or another
known skill root exposes the same `investment-brain-*` id. A workspace-local
projection must never silently compete with a same-name global skill whose
precedence may vary by Codex version.

## Override And Conflict Rules

| Conflict | Required outcome |
| --- | --- |
| Brain or Strategy vs Core | Core blocks the conflicting instruction |
| User mandate vs Core safety | Core blocks the conflicting instruction |
| Strategy vs Investor Context | suitability or policy remains blocking until explicitly resolved |
| Brain vs Strategy | Strategy governs the decision rule; Brain may explain or challenge it |
| Brain or Strategy vs current evidence | evidence controls factual claims; preserve and disclose the conflict |
| Decision Memory vs current evidence | compare chronology and regime fit; preserve both; do not overwrite |
| Brain vs Decision Memory | memory is a counterexample or supporting case, not an automatic Brain update |
| Multiple explicit Brains | stop and ask the user to select exactly one |
| Mid-run Brain or Strategy change | start a new run; do not mutate sealed provenance |

## Codex-Native Experience

Source authoring and installed-plugin lifecycle management enter through
`$tcx-brain`; the underlying validation, registry, immutable version, rollback,
and local discovery operations remain owned by TradingCodex. Invocation belongs
to the Codex task composer through the exact projected skill id. The primary
analysis experience remains Codex-native rather than a Django orchestration
form.

```bash
./tcx investment-brains validate --local ../quality-growth-brain
./tcx investment-brains install --local ../quality-growth-brain
./tcx investment-brains install --git https://example.com/example/quality-growth-brain.git --ref v1.0.0
./tcx investment-brains list
./tcx investment-brains inspect investment-brain-quality-growth
./tcx investment-brains update investment-brain-quality-growth
./tcx investment-brains rollback investment-brain-quality-growth --version 1.0.0
./tcx investment-brains deactivate investment-brain-quality-growth
./tcx investment-brains activate investment-brain-quality-growth
./tcx investment-brains remove investment-brain-quality-growth
```

Validate checks exactly one explicit local or Git source without changing the
registry or projection. Install accepts exactly one explicit local or Git
source. Update reuses a recorded remote Git source or a workspace-relative
local source unless the user supplies another explicit source. Absolute local
paths are never written to the tracked registry or analysis-run provenance.
A manifest's optional declared `repository` follows the stricter public-HTTPS
metadata contract and can never be used to preserve or disguise a local source
path.
A source contained by the workspace is recorded as a canonical
workspace-relative POSIX locator, so the locator remains valid after cloning or
moving the workspace. An external local bundle is recorded with an empty public
locator and a later update must supply `--local` again. A file-based or external
path-based Git source follows the same privacy rule and must be supplied again
with `--git`. Published
version content is immutable: changed content requires a higher version, while
each update must exceed every version already installed even when an older
version is currently selected. Rollback without a version selects the highest
installed version strictly below the current selection. Explicit
`rollback --version` reselects any already-installed immutable version in
either direction without republishing it. Remove deletes the
Head Manager projection and marks the plugin removed but retains its installed
versions so prior run provenance stays inspectable. None of these commands
commits, pushes, opens a pull request, or edits the source repository.

The workspace-managed registry lives at
`.tradingcodex/investment-brains/registry.json`. Immutable package copies live
under `.tradingcodex/investment-brains/packages/`; only active validated skill
payloads are projected to `.agents/skills/investment-brain-*`. Projection is
registered only in the Head Manager Codex config and never in a fixed-role
TOML file. The registry and non-private run records remain versionable, but
their source metadata contains only the public source kind, portable or remote
locator when available, resolved revision, declared publisher metadata, and
content digests.

```text
$investment-brain-quality-growth
$strategy-long-term-compounder

Analyze ACME. Form an independent current view before consulting Decision
Memory. No order, approval, or execution.
```

At run start, TradingCodex resolves the selected projected skill through its
local plugin registry and records the stable Brain id, plugin version, source
metadata, installed content digest, projected skill-tree digest, sealed Strategy snapshot, sealed Investor
Context snapshot, and request hash. A prompt token that does not resolve to one
active, validated Investment Brain plugin fails closed before analysis begins.

`SKILL.md` is loaded through native explicit skill invocation. Optional
Markdown references remain lazy. After the analysis run exists, Head Manager
may read an exact linked file below the selected projection's `references/`
directory with a read-only `cat` command. Multiple permitted references may be
read in one bundle containing only validated `cat` commands and optional
literal `printf` headings joined by `&&`. The PreToolUse gate resolves the
current Codex session to exactly one analysis run, verifies the run record,
requires the selected Brain id and projected path to match, and recomputes the
whole projected skill tree against the run-sealed `skill_digest` before it
allows the read. Missing session binding, baseline runs, changed projections,
unselected Brains, non-Markdown paths, symlinks, redirects, pipelines,
substitutions, executable compounds, and registry, immutable-package, source,
generated-index, or TOML discovery all fail closed. A caller-supplied run id
without the matching session binding is not sufficient authority.

Brain lineage is service-derived, never trusted from caller-authored Markdown
or JSON. A run-bound artifact is accepted only when the service that performed
the authenticated role-scoped write also issued a receipt binding the artifact
id, path, version, file/body hashes, producer, input artifacts, exact
source-snapshot id/hash pairs, sealed run record, Brain, Strategy, and Investor
Context. Synthesis, forecasts, and
Decision Memory snapshots reverify those receipts, so copied frontmatter or a
matching Brain id cannot enter a trusted evidence chain.

Head Manager then:

1. preserves the current mandate and Core boundaries;
2. applies the Brain only as an inquiry and interpretation overlay;
3. translates domain questions into the smallest useful fixed-role team;
4. forms a current evidence view before Decision Memory can anchor it;
5. retrieves memory only when relevant or explicitly requested;
6. exposes Brain/Strategy/evidence/memory conflicts;
7. stores synthesis with run-local lineage and binding provenance; and
8. starts a new run for a different Brain rather than blending doctrines.

The plan is a live Head Manager judgment, not a persisted server workflow. Head
Manager may add, remove, reorder, or parallelize eligible fixed roles as
evidence changes. Head Manager remains on `gpt-5.6-sol` with `xhigh` reasoning;
all nine fixed roles remain on the Terra family with `high` reasoning. Final
execution is not a role and runs no model. A Brain cannot override those model
assignments or obtain execution authority. Every MultiAgent V2 dispatch uses
the exact native `agent_type`,
a fresh child with `fork_turns="none"`, compact run-bound context, and the
role's projected model and inherited `trading-research` profile. When an exact role cannot be
dispatched, Head Manager returns `waiting` or `blocked`; it does not imitate
that role, inspect TradingCodex source or role TOML as a fallback, or perform
the specialist work itself.

The browser viewer never discovers, activates, selects, or invokes an
Investment Brain. Brain analysis runs only from the native Codex task surface
through the workspace-projected skill; the viewer remains read-only and has no
Codex subprocess.

## Workspace Git Contract

A TradingCodex-generated workspace is a versioned user environment, not a
disposable runtime directory. Brain selection, local Brain overlays, Strategy,
Decision Memory, research, and user-authored guidance can change independently,
so the workspace must live inside a Git worktree.

`tcx attach` should use these rules:

- if the target already belongs to a Git worktree, preserve that repository;
- if a new standalone target is outside Git, initialize a local repository;
- never create a commit, branch, remote, push, pull request, or GitHub account
  association automatically;
- never stage pre-existing user files;
- preserve an existing `.gitignore` and add only a clearly delimited
  TradingCodex local-state block; and
- make the repository boundary and workspace-scoped dirty state visible through
  doctor/status without exposing remote credentials.

Repository inspection and initialization must ignore inherited `GIT_DIR`,
`GIT_WORK_TREE`, index/object overrides, and unsafe helper/protocol settings so
an environment variable or user Git rewrite cannot redirect TradingCodex writes
outside the target workspace. Diagnostics may show a sanitized remote identity,
but never credentials, query parameters, or fragments.

Versionable workspace state should include generated configuration, user-owned
Strategy skills, local Brain overlays, Decision Memory and research the user
chooses to retain, and human-readable guidance. Runtime databases, locks,
process/session files, service status, transient audit streams, caches, raw
secrets, and private Investor Context should be ignored by default. Git is local
history; publishing to a remote remains a separate user decision because
research and memory may be sensitive.

`TRADINGCODEX_HOME` must remain outside the generated workspace. Attach,
runtime resolution, and doctor fail closed if it is internal; a platform home,
sibling, or OS temporary home keeps receipt-signing keys and other private
runtime authority physically separate from versionable Brain packages,
projections, research, and decisions.

Codex-native Git UX should remain conversational:

- "show what changed in my TradingCodex workspace";
- "commit this Strategy and local Brain revision";
- "compare the current Brain overlay with the last decision"; and
- "publish this plugin" only as a separate explicit GitHub workflow.

Changing an installed community Brain version leaves inspectable registry,
projection, digest, and run-provenance changes in the workspace, but
TradingCodex must not edit the plugin's upstream repository or create a pull
request on behalf of the user.

## Decision Memory

Decision Memory keeps its current file-native contract. It stores episodes,
forecasts, outcomes, postmortems, and lesson state independently of any Brain.
Every decision-quality episode should retain the Brain identity used by its
analysis run so users can compare how different frameworks behaved without
coupling memory storage to plugin installation.

The blind-first sequence remains:

```text
independent current view
  -> freeze the view
  -> retrieve relevant memory
  -> compare chronology, regime fit, support, and conflict
  -> keep or revise with an explicit delta
```

Memory-to-Brain improvement is a separate, user-controlled curation activity.
TradingCodex may propose a local lesson or Brain change, but it never edits a
community TradingCodex plugin, publishes memory, commits, pushes, or opens a
pull request on the user's behalf without a separate explicit user request.

The built-in explicit-only `$tcx-brain` skill supports this user-owned curation
path and the installed-plugin lifecycle. Writing or changing managed state
requires an explicit request in a root native turn whose exact physical first
line is `$tcx-brain`. The normal `trading-research` profile supports canonical
source writes and public credential-free Git validation. The Brain-scoped
current-turn grant cannot elevate the sandbox, authorize Build or Strategy, or
carry into a follow-up or subagent. The browser viewer has no management path. The user selects
the exact Decision Memory lessons and counterexamples. The skill abstracts
general doctrine without copying private cases, performs a privacy review, and
writes a local source under `investment-brains/<investment-brain-id>` by
default.

Source authoring never edits the managed package store, registry, projection, a
third-party package, or Decision Memory. It ends before install, update,
activation, managed removal, staging, commit, remote configuration, push,
publication, or pull request. Managed lifecycle work starts in a fresh exact
`$tcx-brain` turn, uses the canonical service, installs inactive first, and activates
only on an explicit request. If third-party ideas are adapted, the result uses
a new user-owned id, compatible license, and original wording.

## Delivery Map

### Phase 0 — Contract alignment

- remove remaining documentation and CLI language that promises a staged DAG,
  selected/default team, keyword/negation router, or server workflow loop;
- describe `tcx subagents plan` only as an explicit roster/batch preview, not an
  investment workflow planner, and remove nonexistent loop commands;
- align README, installation, generated-workspace, skill, OpenWiki, and
  validation guidance with dynamic Head Manager coordination; and
- document the Git executable as an attach prerequisite while retaining
  automatic local repository init for a new standalone workspace.

### Phase 1 — Core contract

- reserve exact `$investment-brain-*` invocation;
- define and validate the TradingCodex Investment Brain plugin manifest;
- install from an explicit local or Git source into the workspace-managed
  plugin registry;
- require explicit-only Codex skill projection;
- record immutable Brain id, version, source metadata, and installed content
  digest in each analysis run;
- derive Brain provenance into run-local research artifacts and authenticate it
  with service-issued artifact receipts;
- teach Head Manager the typed layer and conflict rules;
- keep role selection, tools, workflow, and memory out of Brain skills; and
- reject multiple Brains and non-native pseudo-invocation.

### Phase 1A — Trust boundary

- validate and digest one bounded materialized snapshot before immutable copy;
- reject executable/symlink/device payloads, unsafe YAML graphs, path escapes,
  unbounded bundles, Git option injection, and unsafe Git helpers/protocols;
- make registry, package, projection, and generic Codex projection writes
  symlink-safe and workspace-contained;
- fail activation on known same-id host-global skill collisions; and
- prove that handwritten or tampered artifact lineage cannot enter synthesis,
  forecasting, or Decision Memory.

### Phase 1B — Versioned workspace

- require every generated workspace to belong to a Git worktree;
- initialize Git only for a new standalone non-Git target;
- merge privacy-first TradingCodex ignore rules without replacing user rules;
- expose repository root, workspace-scoped dirty state, and sanitized remote
  identity in workspace diagnostics;
- isolate Git operations from inherited repository/config poisoning; and
- leave commit, remote, publication, and pull-request actions entirely under
  explicit user control.

### Phase 2 — Distribution provenance

- preserve immutable Git commit or package-source revision metadata when the
  selected source provides it;
- add publisher and signature metadata when a trustworthy verification channel
  is available;
- introduce future manifest/schema versions without rewriting sealed v1 run
  bindings; and
- show Brain provenance in the native run/artifact inspection experience.

### Phase 3 — User-controlled learning

- compare episodes by Brain identity and version;
- propose privacy-reviewed, counterexample-tested improvements;
- maintain the explicit `$tcx-brain` source path for reviewed local proposal
  bundles without mutating installed third-party plugins; and
- let users decide whether to keep improvements private or publish their own
  community TradingCodex Brain plugin.

### Phase 4 — Community quality

- define compatibility and safety checks for community Brain bundles;
- support paired, blind evaluation against the pristine Head Manager baseline;
- report inquiry quality, contrary-evidence coverage, calibration, abstention,
  and regime robustness rather than a naive return leaderboard; and
- keep install, update, rollback, trust, and publisher UX owned by
  TradingCodex while keeping analysis invocation Codex-native.

## Acceptance Criteria

- A pristine workspace remains useful with no Investment Brain installed.
- One exact explicit Brain changes inquiry and interpretation but cannot select
  roles or widen tools, policy, approval, or execution authority.
- Head Manager chooses and revises the useful role set from the live task and
  accepted evidence; no hook, keyword router, Django model, or stored DAG fixes
  the team or sequence in advance.
- The same request with no Brain remains the TradingCodex baseline.
- Multiple Brains fail closed before analysis begins.
- An unresolved, inactive, tampered, or same-id globally colliding Brain fails
  before dispatch rather than falling back to another skill or baseline.
- Oversized, deeply nested, non-regular, executable, path-escaping, or
  validation-to-copy-mutated bundles cannot reach the managed package store or
  Head Manager projection.
- A run seals the validated TradingCodex Brain plugin id, version, source
  metadata, content digest, Strategy, and Investor Context without storing a
  server-generated plan or raw request.
- Accepted artifacts retain service-derived Brain/run lineage, and synthesis,
  forecasts, and Decision Memory reject copied metadata, missing receipts, and
  body/file tampering.
- Decision Memory remains private, file-native, and non-authoritative.
- `$tcx-brain` writes only after an explicit source-authoring request in a root
  native `trading-research` turn whose exact first line is `$tcx-brain` and whose sandbox
  permits the writes, uses exact user-selected evidence and counterexamples,
  abstracts rather than copies private cases, and leaves a user-owned local
  source without installing, activating, or performing Git/publication actions
  in the same turn; managed lifecycle work uses a fresh explicit `$tcx-brain` turn.
- Removing or not invoking a Brain restores baseline behavior without a
  migration, Django state transition, or generated-workspace repair.
- A new standalone generated workspace is Git-managed without an automatic
  commit or remote.
- Attaching inside an existing Git worktree preserves its history, branch,
  remote, index, and user `.gitignore` content.
- Poisoned Git environment/config cannot redirect workspace operations, and
  status/provenance never emits remote credentials.
- Runtime DB/session/secret state is ignored by default while user-owned Brain,
  Strategy, research, and memory files remain eligible for deliberate version
  control.
- A real Codex-native smoke proves Head Manager uses the selected Brain while
  fixed subagents retain their own Terra model, exact role identity,
  `fork_turns="none"`, and Research profile with no source/TOML emulation.

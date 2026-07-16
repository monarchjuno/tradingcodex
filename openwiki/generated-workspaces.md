# Generated Workspaces

Use this page before changing `tcx attach`, `tcx update`, template modules, generated files, hooks, project MCP config, projection indexes, or the generated launchers. Human-facing rules live in [docs/generated-workspaces.md](../docs/generated-workspaces.md).

## Attach Model

TradingCodex is installed globally or invoked through `uvx`, then attached to the workspace where Codex should work:

```bash
uvx --refresh --from tradingcodex tcx attach . && ./tcx doctor
```

Generation persists one validated absolute Python for both launchers and all
project MCP configs. Canonical `uvx` cache/build interpreters are never
persisted: attach/update provisions a versioned environment below the external
`TRADINGCODEX_HOME/runtime/python/`, copies installed package files out of uv
cache, and verifies MCP imports. Local-directory provisioning uses a clean
runtime-source snapshot and excludes `build`, `dist`, caches, bytecode, local
state, and databases, so a deleted checkout file cannot return from a stale
build tree. A local-source update carries that managed
Python across its editable `uv` reexec through a private bootstrap-only
variable; it never reclassifies the old runtime as the public
`TRADINGCODEX_PYTHON` override. The refreshed generator selects a new
version/source-content-keyed durable runtime. Missing, dependency-incomplete,
and cache-bound explicit interpreters fail before generated files change.

`tcx attach/update --from <spec>` is the explicit executable-source boundary.
Credentialed, signed/query-bearing, fragment-bearing, HTTP, control-character,
inline-secret, option-like, unsupported-scheme, remote-file, and SCP-style specs
fail before rendering, runner execution, or status output. Bare package names
stay package names regardless of cwd contents; relative local directories need
an explicit `./` prefix. Local source
locators and `PYTHONPATH` never enter versionable generated files: projection
stores only `local-explicit`, installs copied bytes, and requires `--from` again
for refresh. Ordinary index-installed `tradingcodex` retains the no-duplicate-
flag attach command. Current generated destinations and every workspace-relative
ancestor are symlink-preflighted before any workspace write.

For maintainers running the CLI directly from this checkout, `tcx
attach/update --dev` selects that executing checkout and is mutually exclusive
with `--from`. POSIX `./install.sh --dev [--update] <workspace>` also binds the
outer editable package runner to this checkout. Both remain local-explicit flows: they
copy installed bytes into a source-content-keyed durable runtime, never render
the checkout path, and require the developer to select dev/local source again
for a later refresh. With no explicit override, dev attach derives a
checkout-scoped home below the platform default and a deterministic loopback
port in `20000`-`29999`; dev update preserves that workspace's home/DB and
rejects converting a release workspace in place. Generated service commands
honor the projected address, so dev MCP bootstrap does not contend with the
release default `127.0.0.1:48267`.

`tcx doctor` runs the global service preflight plus the requested layer only.
Its default view prints per-layer totals and expands warnings or failures;
`tcx doctor --verbose` prints every individual check.

Read-only `update status` and help never refresh. Mutating update fails when no
package runner is available, and runtime provisioning completes before Git,
`.gitignore`, or generated target mutation. Direct remote refreshes bind the
executing package bytes so a moving same-version ref cannot reuse stale code.

Valid targets are new/empty directories or directly Git-initialized directories
containing only `.git` plus optional Git metadata files. Attach initializes Git
for a standalone non-Git target. If the target already belongs to a direct or
parent worktree, it preserves that repository and never creates a nested one. A
source checkout of this repository is not a generated workspace. Update
requires the attached workspace to remain in a Git worktree and does not
initialize a missing repository boundary.

## Generator Flow

`tradingcodex_cli/generator.py`:

1. loads `workspace_templates/modules/*/module.json`
2. resolves module dependencies and conflicts
3. restores a validated release workspace's recorded explicit home, explicit
   DB override, and projected service address when an external package runner
   did not inherit its generated wrapper environment, then resolves the
   platform-native v1 home or explicit override
4. validates the v1 workspace and generated-file inventory before an update
5. preserves an existing Git worktree or initializes a standalone local one,
   then merges only the delimited privacy-first `.gitignore` block
6. takes the native bootstrap lock and renders typed template values
7. ensures the exact-format `.tradingcodex/workspace.json`
8. calls `project_agent_configuration()`
9. writes generated indexes and owned-file hashes, removing unchanged retired files
10. writes `module-lock.json` as the completion marker and records startup status

Default modules include `codex-base`, `fixed-subagents`, `repo-skills`, guardrails, information barriers, audit, MCP, and paper execution. Postmortems are file-native Decision Memory records, not generated workflow metadata.

## Generated Contract And Ownership

Do not use a parent directory as an ownership boundary. `.agents/skills/`,
`.tradingcodex/`, and `trading/` are mixed-lifecycle roots. Use the exact
`generated_files` inventory in `.tradingcodex/generated/module-lock.json`,
protocol-owned identity/status paths, reserved namespaces, and marked blocks:

- release-managed exact files include `AGENTS.md`, `pyproject.toml`, `tcx`,
  `tcx.cmd`, `.codex/config.toml`, prompts, hooks, role TOML, bundled `tcx-*`
  skills, schemas, policies, launchers, and generated indexes; template and
  projection files are inventory-hashed, while the lock itself, immutable
  workspace manifest, and rebuildable bootstrap/status files are separately
  protocol-owned; update may replace direct edits
- managed overlay state includes `.tradingcodex/agent-instructions/*.md`,
  `.tradingcodex/user/*`, `.agents/skills/strategy-*`, optional role skills,
  `investment-brains/*` authoring sources, and installed Brain state; lifecycle
  services validate it and update preserves it while rebuilding projections
- research, report, forecast, decision, evaluation, lesson, and run-provenance
  files are workspace artifacts; generated `.gitkeep` siblings do not make
  their parent directories release-owned
- runtime DB, authority, secrets, credentials, and private local state stay
  external or ignored; ordinary non-reserved user files remain untouched

`.tradingcodex/config.yaml` and `.codex/config.toml` are release-managed. Update
rebuilds TradingCodex-owned entries and projections while preserving
non-reserved user MCP, plugin, and skill configuration plus user capability
directories. `.gitignore` is the inverse special case: only its delimited
TradingCodex privacy block is managed.

Generated workspaces should contain:

- the release-managed files described above
- nine fixed role TOMLs, no `execution-operator`, and 32 bundled skills,
  including root explicit-only `tcx-order-allow`, `tcx-order-submit`, and
  `tcx-order-cancel`
- a `.gitignore` whose TradingCodex local/private-state block is managed without
  replacing user rules
- `.tradingcodex/user/customization.json` when the user saves workspace-local customization preferences

Clean generated workspaces must not contain `package.json`, Node MCP runtime files, workspace-local canonical investment DBs, file-canonical `trading/orders/` or `trading/approvals/` state, broker credentials, raw secrets, pre-v1 `.tradingcodex/mainagent/*.yaml` role registries, retired `.tradingcodex/capabilities.yaml`, `.tradingcodex/policies/roles.yaml`, `.tradingcodex/policies/policy-bindings.yaml`, or policy-local `role_owned_skills` roster copies. Order and approval records live in the central DB, while role skill sources are projected from `tradingcodex_service/application/agents.py` into `.codex/agents/*.toml`.

Git is local history, not a publication action. Attach/update never stage,
commit, create a branch reference or remote, push, or open a pull request.
Runtime DB/session/status/audit/cache/secret files and private Investor Context
are ignored by default. Brain and Strategy skills, research, decisions,
postmortems, lesson state, and non-private run provenance remain eligible.
`TRADINGCODEX_HOME` must be outside the workspace. Attach, runtime resolution,
and doctor fail closed for an internal home; use a platform, sibling, or OS
temporary home rather than a Git ignore workaround.
`tcx workspace status` and doctor expose the repository root and the dirty state
for the attached workspace path.

`head-manager` and every fixed role inherit `trading-research` during analysis.
Ordinary shell/Python, credential-free public HTTP, and the dedicated
`$TRADINGCODEX_SCRATCH` path are
available. The profile extends the built-in native `:workspace` profile, then
uses more-specific rules to keep `trading/`, Git metadata, launchers, and
generated control files read-only or denied. User-owned paths outside
`trading/` are therefore available for workflow inputs and outputs without a
Build turn. Broad temp roots are denied and the exact generated scratch child
is reopened as the shell temp directory. Credential-bearing home paths,
TradingCodex runtime/DB state,
credentials, and local/private services are denied, while `.codex/proxy` is
reopened only for HTTPS proxy material inside the otherwise denied project
`.codex`; the installed standalone Codex runtime is separately reopened
read-only beneath the denied user Codex home. Final role reports and Head Manager synthesis are
written only through authenticated research-artifact MCP writers; other
role-authorized mutations remain bounded by the exact role MCP allowlist.
Native command rules plus `PreToolUse` reject agent-shell `tcx` commands that
select a role/principal or mutate workflow, research, forecast, evaluation, or
order state; read-only inspection and server/build operator commands stay
available.

One narrow generated-path exception exists for a selected Investment Brain's
optional Markdown references. A strictly read-only bundle of validated `cat`
commands and optional literal headings is allowed only when the hook can map
the current Codex session to a verified analysis run and the exact selected
projection still matches the run-sealed skill digest. It never opens unselected
Brains, registry/package/source state, generated indexes, or TOML, and it
rejects redirects, pipelines, substitutions, and executable compounds.

The same `PreToolUse` hook gates `spawn_agent`: allow requires one exact
registered `agent_type`, `fork_turns="none"`, an underscore-only task name, and
the current analysis run id in the compact message. Missing or malformed state
and audit-write failure block dispatch. Role choice, parallel waves, revision,
challenge, and stopping remain Head Manager judgments based on returned
artifacts; the service does not issue ready tasks or unlock later roles.

Before normal analysis handling, `UserPromptSubmit` reserves two immediate
root-native action tokens. It accepts only the complete exact `--name value`
grammar from a root native user turn, creates a workspace-bound `native-user`
mandate, and calls `application/execution_gateway.py` in-process. The third
explicit token, a first-meaningful-line `$tcx-order-allow --mode
paper|validation|live`, instead issues one `OrderTurnGrant` bound to workspace,
session, turn, original complete prompt hash, Codex permission mode, and execution mode,
then continues the normal workflow. Plan mode, malformed, subagent,
and retired `$execute-paper-order` forms fail closed.

The separate `$tcx-build` invocation on the first meaningful line, with a
non-empty same-line or following request, issues a DB-canonical
workspace/session/turn/cwd/original-prompt-bound Build
grant. `PreToolUse` requires it for direct writes and injects one-time proof
into protected DB-backed Build MCP calls. Connector scaffolds are rendered by a
read-only content-addressed MCP tool that never returns existing file content,
then written through native `apply_patch`;
agent MCP has no connector `connect` or write-style `scaffold` operation. It
never elevates the Codex sandbox. Codex Plan mode cannot issue or use the grant,
and the permission mode at tool use must match the issue-time mode. It cannot
be inherited by subagents, follow-ups, or later scheduled runs.
Use `trading-build` for ordinary file-editing Build work. Native `apply_patch`
is its edit surface. Its shell is restricted to public GET/HEAD, enumerated
read-only HTTPS Git, limited workspace `pwd`/`cat`/`ls`, inert provider
reads/hash/diff/Git inspection, exact isolated `py_compile`, and allowlisted
workspace-launcher commands. General interpreters, helper scripts, test runners,
build systems, shell composition, and model-authored POST are blocked while
credential-free public HTTP(S)/HTTPS Git retrieval remains available. Protected
runtime/DB, credential, audit, approval, order, authenticated/local-private
network, package-install, publication, and broker state remain denied. Provider source is
staged inertly under `$TRADINGCODEX_SCRATCH/provider-sources/<provider-id>/`.
When native Codex omits shell workdir from hook input, follow the Build
context's absolute command-proof contract: recorded absolute executables and
operands plus absolute Git `-C` roots. Do not infer the hidden cwd; relative
workspace commands require the exact generated-root workdir.
The generated `PreToolUse` matcher is catch-all: it remains a no-op for
ordinary Research tools but evaluates every browser/web extension and external
MCP-backed browser call while a Build grant is active.
Trusted Build-only `./tcx`/`tcx.cmd` commands and protected MCP
calls remain grant/proof-gated; Plan mode blocks Build entirely. Unstarted protected-call
reservations expire after two minutes; calls that entered the service finish
before deferred revocation becomes terminal. User capability lifecycle belongs
to Codex, while workspace-provider source approval stays an interactive
user-terminal action.
Broader unit, smoke, and build validation also runs from an explicit user or
maintainer terminal rather than the active Build turn.

Provider work is provider-first: a missing provider is fetched into scratch,
recorded in required `source-provenance.json`, written with `apply_patch`, and
statically checked before exact-hash operator approval. Only legacy or wholly
manual providers with no externally fetched source may omit provenance. After
service restart, a fresh Build turn renders, registers, and validates the
connector. Create a `provider_development_required` connector before that only
for an explicit scaffold-only request. Provider inspection reports the
secret-free provenance summary and bundle hash; provenance/helper changes
invalidate approval.

Brain and Strategy management do not use Build. A new root
`trading-research` turn starts directly with a matching first-meaningful-line `$tcx-brain` or
`$tcx-strategy`; the DB grant records only that capability. Source authoring or
body staging remains native workspace-file work, while the hook injects a
one-time proof only into `manage_investment_brain` or `manage_strategy` for
registry/projection lifecycle. Research keeps `.tradingcodex/cli.py`, the
attached runtime, registry, projection, and credential state denied. Model-side
lifecycle launcher calls are blocked with an MCP/user-terminal handoff;
cross-scope, combined-marker, Plan, and subagent use fails closed.

Codex app Automations submit their complete saved prompt as a fresh root turn
on every scheduled run.
TradingCodex handles that prompt exactly like an interactive root turn and does
not detect an Automation origin. `tcx-automate` is the authoring skill for
research, monitoring, recurring analysis, portfolio/status review, draft,
assisted, optional execution, explicitly delegated recurring Build tasks, and
capability-scoped Brain/Strategy management;
the saved prompt invokes the actual runtime work skill rather than
`tcx-automate` recursively. Only a task that may execute begins with
`$tcx-order-allow`; recurring Build begins with `$tcx-build`, while Brain or
Strategy management begins with its matching exact marker. Markers are never
combined. Every managed run needs a fresh scoped grant; file-mutating Build
runs need `trading-build`, and Brain/Strategy management uses
`trading-research`.
Prefer an isolated worktree or workspace and retain a reviewable diff for
scheduled changes.

All 32 bundled skill ids use the reserved compact `tcx-` namespace with one
suffix word when possible and never more than two. Generated folder,
frontmatter, registry, UI metadata, and projection ids must match. User-owned
`strategy-*`, `investment-brain-*`, and optional role skills are separate
namespaces and receive no bundled alias.

The grant expires after one hour and is revoked on one submit or cancel,
`Stop`, or the next user turn. Root Head Manager alone can select
`use_order_turn_grant`; `PreToolUse` reserves the grant against the tool-use id
and injects internal proof into rewritten MCP input. Subagents and
direct MCP callers cannot supply the proof. The service enforces canonical
policy, ticket, receipt, action, broker posture, and mode. It does not claim to
compile natural-language symbol, notional, schedule, or strategy scope into
deterministic policy.

A consumed grant that remains `authorizing` is never reset by Stop or a new
turn. The session blocks a new Build or order-sensitive prompt until its
canonical result is terminal; ordinary research may continue.

Native config disables unified execution and interactive action features.
Pre-use and permission hooks match legacy and current shell tool names, reject
`write_stdin`, unsupported fields, protected state, credentials, publication,
and order effects, while passing general computation and public retrieval to
the active native profile. Workdirs stay in the workspace or its dedicated scratch path.

Root config sets `web_search="disabled"`. Only the fundamental, technical,
news, macro, instrument, and valuation custom-agent TOML files override it with
`web_search="live"`; Codex applies that role config to the spawned child. Other
roles and Head Manager do not perform live web research.

The source repository's React/TypeScript/Vite tooling does not alter this rule.
Compiled viewer assets ship in the Python package; attach/update never copy
`frontend/`, create `node_modules`, or invoke npm. The viewer reads canonical
workspace state and never starts analysis or stores reasoning/tool output.

Project/root user MCP servers, skills, and plugins are configured through
Codex, not `tcx`. Codex exposes them natively to root and fixed-role agents;
role TOML overrides only the TradingCodex MCP entry and projected `tcx-*`
skills. TradingCodex neither recommends nor classifies user capabilities and
offers only a sanitized read-only inventory.
The built-in TradingCodex MCP defaults safe enabled tools to Codex `approve`;
raw submit, cancel, and broker-status-refresh mutations are absent from every
config and from public `tools/list`. Root Head Manager alone lists
`use_order_turn_grant`, which is inert without the hook-injected current-turn
proof. Immediate exact actions and the proof-protected grant path both retain
all canonical service gates.
Every root and fixed-role MCP entry is `required=true`, so a trusted session
fails closed instead of continuing without its canonical service.
Root and fixed-role MCP entries render the attached workspace's absolute path
into both `cwd` and `TRADINGCODEX_WORKSPACE_ROOT`. Relative MCP cwd resolution
can otherwise follow the caller process instead of Codex `-C`, sending
file-native workflow state to the wrong project. Run `tcx update .` after moving
an attached workspace so the generated bindings are refreshed.

## Projection Outputs

Generated indexes under `.tradingcodex/generated/` include module, capability,
component, agent, skill, and projection metadata. They are diagnostic
projections, not runtime authority. The service agent and MCP tool registries
are the sole capability truth; the retired YAML capability/role/policy-binding
copies are not generated. Component data comes from
`tradingcodex_service/application/components.py`. Agent and skill projection
comes from `tradingcodex_service/application/agents.py`.

Skill/projection indexes cover only the TradingCodex-managed workspace. They
record each skill's layer, trust scope, implicit-invocation posture, and exact
workspace-relative resolved source file. Codex TOML entries are relative to
their declaring config and TradingCodex resolves them for exact-path checks.
The indexes set `runtime_discovery_complete=false` and report
same-name host-global collisions without importing host skill bodies.
`doctor --layer improvement` compares exact root/role paths. This is drift and
collision detection, not proof that the host Codex runtime cannot discover a
differently named global or plugin skill.

Generated project config keeps root web search disabled while the six evidence
custom agents provide the pristine public-research baseline. Project-local additional instructions and managed skills are
overlays; projection places an immutable core/extension footer after additional
instructions so they cannot redefine the documented kernel contract.

Native `PostToolUse` audit is always redacted: it records only the
event, run id, and tool name, never tool input, response, command output, or
artifact bodies. A permitted native spawn additionally records only structural
evidence (`agent_type`, compact task name, `fork_turns`, message byte count, and
message hash); the child brief itself is never persisted in the hook audit.

When generated agent behavior changes, inspect generated output, not just template source.

## Update Rules

`tcx attach .` creates a new workspace and rejects an already attached
workspace. `tcx update .` accepts only a current v1 workspace, refreshes
exact module-lock generated paths while preserving immutable `workspace_id`,
internal active paper-account scope, managed overlay state, workflow artifacts,
and ordinary non-reserved user files. Current generated files may be replaced;
retired generated files are removed only when they still match their recorded
hash, so modified retired files require manual resolution. The optional
`.tradingcodex/user/investor-context.md` file is user-owned workspace state and
is preserved; attach does not create it before a confirmed investor-context
update.
`.tradingcodex/cli.py` is the common Python
launcher behind POSIX `./tcx` and Windows `tcx.cmd`; hooks select the native
shim. The POSIX shim skips redundant absolute-path re-entry when already at its
canonical root so a temp-root disposable workspace remains usable through the
more-specific native workspace permission. Generator values use format-specific TOML/YAML/JSON/shell/CMD literals.
Module lock records canonical `tradingcodex_home`, `home_source`, DB path, and
`db_source`; Codex writable roots and MCP env use the same resolved path. An
explicit DB override is also projected through the common launcher and every
MCP environment so the home-default DB cannot be selected accidentally. A destination-OS
update is required after moving a workspace across platforms. Safe persistent
package specs remain intentional provenance; local sources record only
`local-explicit` and must be supplied again. Update refreshes through the package
unless the caller passes `--skip-refresh`. A current `$tcx-build`
`trading-build` turn may run the proof-gated workspace-local
`./tcx update --skip-refresh` command. Package refresh stays interactive
user-terminal-only and is never executed by Head Manager.
The v1 module lock rejects unknown or missing fields. Its module and generated-
file entries are exact typed records, generated-file owners are `template` or
`projection`, versions are canonical same-major PEP 440 values, and timestamps,
workspace identity, absolute home/DB paths, hashes, and source enums are
validated by the same function before write and after read. Read-only status
inspection may accept a newer same-major version only to report that the
installed package must be refreshed first.
Per-run provenance and accepted artifacts are workspace state and remain
preserved. Projection files and marked blocks are rebuilt from canonical
Strategy, optional-skill, Investment Brain, additional-instruction, and managed
MCP state. Update consumes the frontend build already
inside the package and does not run npm.

## Edit Checklist

When changing this area:

- update `docs/generated-workspaces.md` for durable contract changes
- keep human-readable generated content under `workspace_templates/modules/*/files`
- update generator tests for module graph, rendered paths, and generated indexes
- run a clean generated workspace smoke
- inspect `.tradingcodex/generated/*.json`

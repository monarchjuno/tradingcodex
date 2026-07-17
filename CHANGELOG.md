# Changelog

## Unreleased

## 1.1.2 - 2026-07-17

- Add a cross-platform fixed-role calculation surface with generated
  `tcx-calc`/`tcx-calc.cmd` launchers and a content-addressed runtime v2 outside
  Django, MCP, the service home, DB, workspace, and scratch. Its wheel-only
  per-artifact hash resource pins the complete 12-package direct/transitive
  set around NumPy, pandas, SciPy, statsmodels, numpy-financial, and PyArrow.
  The exact basename-only contract replaces host-dependent system
  Python and heredocs, sanitizes environment and temp state before exposing the
  verified runtime, rejects links and compound forms, applies resource bounds,
  blocks child-process and network escape paths in prepared mode, verifies the
  generated launcher hashes in doctor, and remains inside the native Codex OS
  sandbox. Windows scratch now uses `TradingCodexScratch` so it cannot overlap
  the default service home. Native Windows release smoke invokes both generated
  batch launchers through tokenized `cmd /c call` commands so workspace paths
  containing spaces retain their argument boundaries. The explicit manual
  release workflow gates the exact wheel through an x86-64 Linux/macOS/Windows
  and Python 3.11-3.14 runtime matrix.
- Separate ordinary GitHub uploads from deployment work. Normal source CI keeps
  source, framework, and deterministic frontend checks but does not build or
  upload distributions, run release-upgrade or platform matrices, or publish.
  Documentation-only pushes and pull requests skip source CI, GitHub Pages
  becomes manual-only, and package build/publication remains confined to
  explicit Manual Release dispatches.
- Add immutable Dataset memory with Source Snapshot lineage, explicit typed
  manifests, content-addressed canonical Parquet payloads, append-only license
  withdrawals, bounded card/manifest/profile/slice retrieval, and a managed
  Git ignore rule for payload objects. A rebuildable SQLite+FTS v3 catalog now
  unifies existing research artifacts with Dataset and Calculation objects,
  reprojects only changed files, rebuilds after corruption, and continues the
  legacy JSON exports for one compatibility release. Lineage timestamps cannot
  predate their Source Snapshot or parent Dataset, withdrawn shared payloads
  cannot be profiled, malformed manifests fail closed per object, and L0 cards
  are size-bounded. Internally generated `Path` objects are normalized to the
  portable forward-slash workspace form on native Windows while caller-supplied
  backslash paths remain rejected.
- Add prepared CalculationSpec/Run memory around `tcx-calc`, including declared
  scratch inputs/outputs, typed finite result envelopes, exact full-fingerprint
  reuse with a current-workflow reuse Run, comparison by requested metric, and
  private-ledger snapshot hash binding without durable input values. The new
  shared `tcx-calculation` skill gives the six financial calculation roles the
  bounded procedure; Head Manager receives card-only planning discovery and
  the viewer remains read-only. Calculation cards also project bounded symbols
  and immutable Dataset relations so instrument-scoped role searches do not
  miss an existing exact calculation. Prepared recording independently
  revalidates script/input/sidecar/runtime/output hashes and schemas; Dataset
  materializations and private central-ledger snapshots are service-bound
  rather than caller-asserted.
- Route legacy Source Snapshot, ResearchSpec, ReplayManifest, and ExperimentRun
  path construction, regular-file reads, hash verification, and immutable
  writes through the shared Research Object primitives while preserving their
  released v1 hash encoding and IDs without migration.
- Enable Head Manager live web search for narrow workflow-planning
  reconnaissance. Durable root instructions and `tcx-workflow` keep raw search
  results untrusted and prohibit using them as accepted evidence or synthesis
  support; material facts must be reacquired by producing roles. Portfolio,
  risk, and judgment-review roles explicitly keep web search disabled, and the
  existing Build hook continues to block native web and browser tools.
- Fix BYOR Codex capability use after the 1.1.1 gate removal. Head Manager and
  fixed roles now distinguish explicit external skill overlays from read-only
  app, connector, MCP, and data tools; inspect the current task's callable
  surface before falling back or declaring a provider unavailable; and treat
  the sanitized inventory as configuration evidence rather than runtime
  callability proof. Capability inventory now names plugin app/MCP/hook
  components from secret-free top-level identifiers and includes compatible
  user skills discovered below `CODEX_HOME/skills`. Generated workspaces no
  longer disable Codex apps, leaving app exposure to user, organization, and
  host policy.
- Align observed E2E behavior with the workspace contract: fixed roles may edit
  files in their dedicated private scratch root while arbitrary external paths
  remain blocked; viewer write-shaped requests consistently return GET-only
  `405` responses even with CSRF enforcement; managed Brain and Strategy proof
  errors name their own entrypoints; and E2E hooks execute through the generated
  launcher runtime.

## 1.1.1 - 2026-07-16

- Remove the legacy user-capability gate, its CLI and broker-import paths,
  and its active database tables. User-installed MCP servers, skills, plugins,
  apps, and hooks now remain BYOR native Codex capabilities for root and fixed
  agents. Add a secret-free read-only capability inventory to MCP and System,
  preserve non-reserved user Codex configuration across update, and keep all
  TradingCodex principals, grants, protected proofs, and order effects behind
  the existing service boundary.

## 1.1.0 - 2026-07-16

- Make managed skill invocation resilient without weakening authority: Build,
  Brain, Strategy, and native order entrypoints now share one lexical parser
  that accepts the first meaningful line, matching projected-skill Markdown
  links, same-line requests, BOMs, and cross-platform newline forms. Prompt
  proofs remain bound to the original bytes, and order flags, approvals,
  confirmations, one-effect limits, and replay protection remain strict.
- Give `trading-build` credential-free, limited public HTTP(S) and HTTPS Git
  retrieval while keeping root native web search disabled. Public source files
  are staged only below the workspace-specific cache-backed scratch provider
  directory, remain inert, and may be hashed, diffed, inspected, or
  syntax-checked but not installed or executed. The proxy uses full HTTP
  transport only for Git Smart HTTP's read-only protocol POST; ordinary HTTP
  remains GET/HEAD-only and general POST stays blocked. Authentication,
  model-authored request bodies, private/local targets, shell pipelines,
  dependency installation, direct `trading/` downloads, Git publication, and
  broker effects remain blocked.
- Make the active Build-turn shell a narrow, non-extensible review lane. Edits
  use `apply_patch`; admitted commands are public GET/HEAD, enumerated read-only
  HTTPS Git, limited workspace reads, inert provider hash/diff/Git inspection,
  exact isolated `py_compile`, and allowlisted workspace-launcher operations.
  General interpreters, helper scripts, test runners, build systems, shell
  composition, model-authored POST, and native browser/web/network tools now
  fail closed during Build while Research browser behavior remains unchanged.
- Add provider source provenance, full bundle hashing, secret/VCS/symlink
  rejection, immutable approval snapshots, and provider-first connector
  onboarding. Build-side inspection can return an inert bundle-only hash and
  `service_check_required` posture without reading the central ledger; only the
  interactive operator path resolves canonical approval state. Missing-provider
  connection requests no longer leave dead-end connector scaffolds; explicit
  scaffold-only requests remain available.
- Preserve the v1 update contract for existing workspaces. `tcx update`
  refreshes the generated invocation, permission, hook, and skill surfaces
  without a database migration, keeps workspace identity and user-owned
  artifacts, and retains the existing explicit home, database, and service
  address. Fully restart Codex and open a new task after updating so an older
  task cannot retain its prior hook snapshot. Existing manual provider bundles
  may omit provenance, while changed or newly unsafe bundles must be reviewed
  and approved again.

## 1.0.2 - 2026-07-15

- Preserve an attached release workspace's explicit `TRADINGCODEX_HOME`,
  explicit database override, and projected service address when an update is
  launched through `uvx` instead of the generated wrapper. This prevents a
  custom or isolated v1.0.0 workspace from silently switching to the platform
  default ledger during a package refresh.
- Fix the public guide's full-width desktop grid so viewport gutters no longer
  collapse the reading column, and align the provider-to-order header with the
  primary Guide/Reference/GitHub navigation. Add route, fragment, mobile-menu,
  and desktop-layout contract tests.
- Reduce hosted automation usage: normal CI, GitHub Pages deployment, and
  release verification now use one job each; PyPI publication adds only its
  protected upload job. Guide-only pushes skip the source CI workflow.

## 1.0.1 - 2026-07-15

- Add the shared `tcx-artifact` skill to all nine producing fixed roles so
  deferred MCP calls receive the exact research-artifact, thesis-lifecycle,
  retry, and forecast persistence procedure.
- Expand `create_research_artifact` and `issue_forecast` MCP schemas to expose
  state-specific lifecycle fields, one-range probability rules, RFC 3339
  timestamps, and the complete base-rate contract before a call is attempted.
  Artifact validation errors now name the supported alternative fields, and
  MCP resource-template discovery returns an empty supported list. Omitted
  forecast `issued_at` and revision `revised_at` values now reuse the same
  service receipt timestamp as `recorded_at`, eliminating a microsecond race
  that rejected otherwise valid agent calls.
- Require Codex CLI 0.144.4, the validated reference for generated permission
  profiles, V2 fixed-role dispatch, hooks, and deferred TradingCodex MCP calls.
  Older clients now fail the runtime doctor check with upgrade guidance.

## 1.0.0 - 2026-07-15

- Raise the validated Codex CLI reference to 0.144.4 while retaining the
  proven 0.144.1 compatibility floor, explicitly enable
  MultiAgent V2, replace the incompatible V1 `agents.max_threads` setting with
  the V2 session-wide thread ceiling, add CLI version diagnostics, and add a
  strict config/feature/MCP maintainer preflight. Native lifecycle acceptance
  now requires persisted project-hook trust, because the one-run hook-trust
  bypass is not inherited when a V2 child reloads an exact role config.
- Add explicit development bootstrap shortcuts: source checkouts can run `tcx
  attach/update --dev`, while the POSIX installer supports `--dev` and
  `--dev --update` with the checkout bound as both package runner and declared
  executable source. Generated workspaces continue to store only
  `local-explicit`, never the private checkout path. Default development
  bootstrap now isolates each checkout with its own runtime home, ledger, and
  deterministic loopback service port; generated service commands honor that
  address, and development update refuses to convert a release workspace in
  place. Development commands import the live checkout through an editable
  package-runner environment, while durable local runtimes are built from a
  clean runtime-source snapshot that excludes stale build, distribution,
  cache, bytecode, state, and database products.
- Remove the web Work execution runner, preview/start/follow-up APIs, and
  workbench-only hook mode; replace the product web with a read-only
  Library/Skills/System viewer whose left rail selects only registered,
  validated attached workspaces. Optimize half-width desktop windows with a
  compact workspace selector and full-width list-to-detail transitions, keep
  long status labels inside every supported viewport, move keyboard focus into
  narrow Library/Skills details and back to their indexes, and restore
  main-content focus after workspace switching.
- Harden native workspace startup by keeping hook stdin/stdout in the launcher
  process, using direct proxy-free loopback readiness checks with a native-host
  response allowance, rejecting macOS ephemeral-port self-connections, and
  retaining redacted detached-startup diagnostics while terminating timed-out
  children instead of leaving orphans. Remove reverse-DNS lookup from the local
  Django bind path, and validate these paths from the same clean wheel on macOS
  and Windows.

- Make `tcx doctor` concise by default: run only the selected layer plus global
  service preflight, summarize layer totals, expand warnings and failures, and
  retain full per-check evidence behind `--verbose`.
- Strengthen point-in-time research guidance for filing/accession identity and
  first-release or vintage macro data. Record all tried variants, effective
  trial counts, frozen selection rules, and single-use holdout posture, while
  keeping PBO, reality-check, and deflated-Sharpe diagnostics conditional on
  supported assumptions and inputs.
- Let exact fixed-role children read only their projected role-owned/shared
  skill documents and Markdown references, including the read-only batched
  `cat` form Codex naturally emits. Redirects, pipelines, substitutions,
  executable compounds, other-role skills, configs, generated indexes, and
  runtime state remain fail-closed.

- Move all 31 bundled skills into the reserved compact `tcx-` namespace, with
  one suffix word preferred and two allowed only for clarity. User-owned
  `strategy-*`, `investment-brain-*`, and optional role skill namespaces stay
  separate, and unchanged retired generated files migrate on `tcx update`.
- Remove the `execution-operator` fixed role and retired
  `execute-paper-order` skill from generated workspaces.
- Add explicit-only root-native `tcx-order-submit` and
  `tcx-order-cancel` action bundles. Their exact full-prompt grammar is
  parsed by `UserPromptSubmit` and dispatched in-process to the canonical
  service gateway as a workspace-bound `native-user` mandate before any model
  runs.
- Expand `tcx-automate` into the authoring path for all Codex app
  Scheduled Tasks, including simple research, monitoring, recurring analysis,
  portfolio/status review, draft orders, assisted execution, and optional
  turn-authorized execution. Saved prompts run on every scheduled turn and
  invoke the actual work skill rather than `tcx-automate` recursively.
- Retire persistent Build mode. Only an exact root first line `$tcx-build`
  creates a current-turn, workspace/session/turn-bound Build grant; Plan mode
  is rejected and the marker never elevates Codex's native permission profile.
  Generated workspaces default to `trading-research`, which permits general
  shell/Python, credential-free public retrieval, and user-owned file changes
  outside `trading/` while denying runtime/DB, credentials, protected control
  files, local/private network targets, and direct durable TradingCodex writes.
  Explicit `trading-build` turns open controlled `trading/` connector work with
  network and sensitive state still denied; hook-owned proofs continue to gate
  trusted lifecycle and canonical connector DB changes.
- Give Strategy and Investment Brain management their own exact root markers,
  `$tcx-strategy` and `$tcx-brain`, in the normal `trading-research` profile.
  Their DB-canonical current-turn grants are capability-scoped and cannot cross
  into Build, one another, Plan mode, subagents, orders, credentials, global
  config, or publication. Codex-native source authoring and ordinary workspace
  computation stay in the normal permission profile, while registry and
  projection lifecycle uses the proof-protected `manage_strategy` and
  `manage_investment_brain` MCP tools. Research no longer exposes the generated
  CLI or attached runtime for those actions, and model-side lifecycle launcher
  calls now return a precise MCP/user-terminal handoff. Reversible source and
  draft creation no longer asks for redundant confirmation; activation and
  destructive lifecycle actions remain explicit. General server, Investor Context, and
  unsupported Decision Memory lifecycle commands now return explicit
  user-terminal handoffs instead of attempting a blocked model shell.
- Replace agent-side connector `connect`/write-style scaffold MCP operations
  with read-only, content-addressed scaffold rendering plus native patching.
  Provider-source approval remains an interactive operator action protected by
  a one-use service capability.
- Add the explicit-only `tcx-order-allow` bundle and `OrderTurnGrant`: only a
  physical first line `$tcx-order-allow --mode paper|validation|live` can admit one
  later submit or cancel in that root turn. Grants bind workspace, session,
  turn, full prompt hash, and mode; expire after one hour; and are revoked on
  `Stop`, the next turn, or consumption.
- Add Head Manager-only `use_order_turn_grant`. `PreToolUse` reserves the grant
  and injects internal proof, so Workbench, subagents, direct MCP, REST, and CLI
  callers gain no execution authority. The service still enforces canonical
  ticket, receipt, policy, mode, live-confirmation, idempotency, adapter, audit,
  reconciliation, and uncertainty gates; free-form prompt scope is not claimed
  as deterministic policy.
- Keep a consumed `authorizing` order effect immutable while its broker result
  is in flight. Stop/new-turn cleanup never resets it, and the same session
  blocks new Build/order-sensitive prompts until terminal while ordinary
  research remains available.
- Increase the bundled core skill count from 29 to 31 without adding an
  execution subagent.
- Remove final submit, cancel, and broker-status-refresh mutations from public
  MCP, REST, generic CLI guidance, and Workbench while preserving the existing
  policy, approval, live-confirmation, idempotency, adapter, audit,
  reconciliation, and uncertain-result gates.
- Reduce the fixed subagent roster to nine and keep Workbench preview, start,
  and follow-up strictly analysis-only by rejecting reserved native action
  tokens before launch.
- Require the canonical TradingCodex MCP for Head Manager and every fixed role,
  make Workbench apply an isolated canonical project-trust override, and fail
  launch instead of silently continuing without service authority. Native
  dispatch audit now records exact role/fork/task plus child-brief hash and
  size, never the brief body.

- Establish the first supported TradingCodex public contract across the CLI,
  Django service, MCP gateway, React workbench, and generated workspaces.
- Start from clean v1 workspace, runtime-home, database, migration, policy,
  approval, audit, research, forecast, and execution boundaries.
- Make Codex Head Manager the dynamic research orchestrator, with no semantic
  hook router, server-generated DAG, default analyst team, or Django workflow
  state machine. MultiAgent V2 projects Sol/xhigh for Head Manager and
  Terra/high for analytical roles, with Terra/low for execution.
- Add explicit, workspace-file-native Investment Brain plugins with strict
  local/Git installation, immutable versions, Head Manager-only projection,
  lazy sealed references, rollback, collision checks, and run provenance.
- Add the user-owned `tcx-brain` source and managed-plugin lifecycle entrypoint
  while keeping Strategy, Investor Context, Decision Memory, current evidence,
  and Core safety as distinct authority layers.
- Authenticate run-bound research artifacts and synthesis inputs with
  service-issued receipts, external signing-key custody, source snapshots, and
  exact Brain/Strategy/Context lineage.
- Validate accepted run-bound artifacts against the strict quality contract
  before receipt or stable publication, expose structured follow-up and
  improvement MCP schemas, and exclude non-accepted handoffs from synthesis.
- Enforce V2 spawn-field allowlists, source-snapshot knowledge cutoffs,
  no-future artifact time bounds, and strict claim tagging for Head Manager
  synthesis.
- Initialize generated workspaces as local Git worktrees without staging,
  commits, remotes, or publication, and require the private runtime home to
  remain outside the versionable workspace.
- Serve the committed React workbench through content-hashed deterministic
  assets so package updates cannot leave a browser on a stale bundle.
- Ship manual, tag-bound PyPI publishing with one verified wheel and source
  distribution reused across Ubuntu, macOS, Windows, and publication jobs.
- Support forward package, generated-workspace, and Django schema updates within
  the v1 line while rejecting prerelease compatibility and downgrade paths.

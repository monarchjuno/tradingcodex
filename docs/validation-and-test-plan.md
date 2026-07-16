# Validation And Test Plan

This document owns required validation commands, unit/API/generator/smoke test
coverage, and release-sensitive verification expectations.

## Default Validation

Run after source or test changes:

```bash
pytest
```

Run after Django settings, model, admin, API, MCP, or service changes:

```bash
python manage.py check
```

Run after broad Python migration, package, or import-structure changes:

```bash
python -m compileall tradingcodex_cli tradingcodex_service apps tests
```

Run after frontend or viewer UI changes:

```bash
npm ci --prefix frontend
npm test --prefix frontend
npm run build --prefix frontend
git diff --exit-code -- tradingcodex_service/static/tradingcodex_web
```

Node 22 is a maintainer build dependency only. The committed build must remain
usable from a Python wheel without Node installed.

## Unit Test Expectations

Unit tests should cover:

- policy decisions, restricted list, limits, capability checks
- order ticket checks, JSON order input validation, approval validation, execution preconditions
- order ticket creation, state transitions, check pass/warn/fail recording,
  approval readiness, exact approval-scope hash validation, broker order
  events, and fill deduplication
- approved-order idempotency and duplicate execution blocking
- exact `$tcx-order-allow` first-meaningful-line parsing across plain and
  matching projected-link forms, original-prompt workspace/session/turn/mode
  binding, one-hour expiry, next-turn and `Stop` revocation, proof
  reservation/injection, atomic single consumption, and replay blocking
- immediate submit/cancel accepts only the allowed BOM/line-ending/blank-line
  and matching projected-link variants; literal `--name value`, known unique
  flags, action-only content, Plan/root/subagent checks, idempotency, and the
  single-effect boundary remain strict
- `tcx-automate` prompt-shape coverage across research, monitoring,
  analysis, portfolio/status, draft, assisted, and optional execution tasks:
  non-execution prompts begin with the selected runtime skill, execution-capable
  prompts begin with exact `$tcx-order-allow`, and no saved prompt recursively
  invokes `$tcx-automate`
- broker connection defaults, broker account discovery, read-only sync runs,
  portfolio ledger event creation, snapshot materialization, and
  reconciliation summaries
- principal/capability checks before MCP handler dispatch and policy decisions
- Head Manager dynamic exact-role selection and readiness labels without a
  server-side intent, lane, or team classifier
- adapter registry and disabled live adapter behavior
- audit append behavior and request/result hash generation
- file-native research artifact creation, versioning, search, source snapshot recording, and markdown export
- v2 artifact catalog lazy projection across current and legacy Markdown/JSON/
  forecast records, immutable-source preservation, cutoff fail-closed search,
  invalid-record quarantine, and incremental change/removal refresh
- central DB path resolution through `TRADINGCODEX_HOME` and `TRADINGCODEX_DB_NAME`
- workspace identity/provenance recording without workspace-local DB partitioning
- duplicate research ids fail closed within a workspace unless an explicit append/version path is used
- duplicate order ids fail closed through the central runtime ledger unless an explicit idempotent path is used
- harness component registry uniqueness, dependency validity, taxonomy tag coverage, and tag filtering
- method-profile-specific ResearchSpec validation, including proof that general
  and event research do not require quant or FCFF-only fields
- evaluation-profile isolation, extension-profile pair invariants, and hard
  failure on unregistered extension use
- managed skill-layer metadata, non-implicit strategy/optional metadata,
  exact-path projection checks, immutable post-overlay instruction footers, and
  host-global same-name collision reporting
- Decision Memory and Investor Context root-skill projection, explicit-only
  invocation metadata, fixed-role non-projection, and no duplicate Postmortem
  root entrypoint
- investor-context file validation, on-demand creation, file-only reads,
  enable/disable, native saved-default application, content hashing, privacy limits, and compact
  task-appropriate application
- decision-memory evidence-origin and lesson-state validation, strategy/context
  snapshot binding, point-in-time replay cutoff enforcement, and separate
  historical-holdout versus live-forward reporting
- viewer snapshots accept only registered, current attached workspaces; invalid
  or stale ids fail without fallback
- viewer routes are GET-only, old workbench routes are absent, and no browser
  endpoint launches, resumes, or supervises Codex
- viewer skill and artifact detail use sanitized previews and never return raw
  reasoning, tool payloads, stderr, or secret-bearing state
- research allowed roots reject symlinks; MCP artifact identity is transport-
  principal-bound; run-local input dependencies cannot be skipped; authenticated
  receipts must match the current artifact bytes and sealed run lineage
- DB and JSONL audit writes recursively redact secret fields and secret-bearing
  error text at the shared audit boundary
- concurrent analysis-run creation leaves one immutable request-hash and sealed
  Brain, Strategy, and Investor Context binding; conflicting reuse fails closed

## API And Admin Test Expectations

API/Admin tests should cover:

- Ninja endpoints return typed schemas and reject unauthorized calls
- staff-session Ninja mutations reject missing CSRF, while valid header API-key
  calls remain independent of CSRF
- role-authored Ninja mutations reuse MCP role/capability/principal binding;
  cross-role synthesis-artifact or run-lineage forgeries leave no accepted file
- accepted run-bound artifact writes validate the exact intended Markdown bytes
  before receipt or stable publication; malformed structured follow-ups or
  missing strict-quality fields leave neither file nor receipt, while synthesis
  rejects authenticated `revise`, `blocked`, and `waiting` inputs
- a CSRF-valid staff session whose username collides with an agent principal id
  still cannot call role-authored Ninja mutations
- harness component endpoints expose the static component registry and return 404 for unknown component ids
- Django Admin uses default templates with no custom TradingCodex actions/CSS/dashboard,
  while order tickets, order-turn grants, execution-ledger models, and external
  MCP router launch configuration are fully read-only
- service-layer MCP registry, policy, and adapter helpers create audit events when called directly by supported service/API/CLI paths
- agent/skill file projection tests cover proposal files, generated manifest, and blocked risky assignments without writing skill DB or AuditEvent state
- `tcx mcp stdio` handles JSON-RPC `initialize`, `tools/list`, and `tools/call`
- MCP research tools store and retrieve workspace markdown/source-snapshot JSON through the service layer without writing research DB rows, audit rows, or tool-call ledger rows
- non-research MCP tool calls create DB ledger entries with request/result hashes
- generated `./tcx mcp ledger` can inspect the central DB tool-call ledger
- stdio bridge returns valid MCP messages and writes no non-MCP stdout
- Broker Center and order-ticket API endpoints expose read/status/draft/check
  behavior without bypassing approval or approved action gates
- viewer snapshot/detail GETs remain read-only and POST returns method-not-allowed
- old `/api/workbench/` routes return 404 and every anonymous loopback mutation
  remains denied
- Django Admin continues to use default registration and templates after the
  React cutover

## Generated Workspace Smoke Tests

Run after template/bootstrap behavior changes:

```bash
python -m pytest tests/test_dev_bootstrap.py -q
```

These focused tests cover `--dev` source provenance, checkout-scoped home and
service-address derivation, preservation during dev update, release-workspace
conversion rejection, generated service-address defaults, and POSIX installer
forwarding.

Then run the full disposable-workspace contract:

```bash
SOURCE_ROOT="$(pwd)"
SOURCE_PYTHON="$(uvx --refresh --from "$SOURCE_ROOT" python -c 'import sys; print(sys.executable)')"
export PYTHONPATH="$SOURCE_ROOT${PYTHONPATH:+:$PYTHONPATH}"
export TRADINGCODEX_MCP_PACKAGE_SPEC="$SOURCE_ROOT"
unset TRADINGCODEX_PYTHON
SMOKE_ROOT="$(python -c 'import tempfile; print(tempfile.mkdtemp(prefix="tradingcodex-smoke-"))')"
export TRADINGCODEX_HOME="$SMOKE_ROOT/home"
"$SOURCE_PYTHON" -m tradingcodex_cli attach "$SMOKE_ROOT/workspace"
cd "$SMOKE_ROOT/workspace"
./tcx doctor
./tcx workspace status
./tcx investor-context status
./tcx skills list --all
cd "$SOURCE_ROOT"
python tests/codex_cli_contract.py --workspace "$SMOKE_ROOT/workspace" --require-reference
```

Smoke coverage should verify:

- `tcx attach` creates a new workspace contract and rejects an already attached
  workspace
- `tcx update` refreshes an existing generated workspace while preserving
  `workspace_id` and internal paper-account scope
- generated workspace contains `.tradingcodex/workspace.json`
- generated workspace contains `.tradingcodex/generated/component-index.json`
- generated workspace contains no `package.json` or Node MCP/runtime files
- generated workspace contains nine fixed subagents and 31 protected bundled
  repo skills, including all three explicit-only native execution bundles and
  no retired execution role or skill
- generated `.codex/config.toml` enables live web search for pristine public
  research without treating a host finance skill as a dependency
- generated `.codex/config.toml` explicitly enables MultiAgent V2, reports it
  enabled through `codex features list`, uses seven session threads, and omits
  the incompatible V1 `agents.max_threads` key
- skill/projection manifests identify the finite managed inventory, declare
  runtime discovery incomplete, and resolve exact root/role skill paths
- two generated workspaces have different workspace ids
- two generated workspaces keep separate research markdown/source-snapshot
  files while central MCP ledger rows retain the correct workspace provenance
  without collapsing account scopes
- internal paper account selection controls portfolio separation while
  investor context remains a separate optional workspace file
- all fixed-role MCP allowlists match `AGENT_SPECS` and runtime tool annotations
- root and fixed-role MCP `cwd` plus `TRADINGCODEX_WORKSPACE_ROOT` are absolute,
  identical, and remain bound to the attached workspace when Codex is launched
  from another caller directory
- generated hooks are callable, auto-route plain investment prompts, ignore non-investment prompts, and classify secret-warning cases
- component index matches the Python component registry

## Platform Runtime And Wheel Matrix

Focused source tests are split across `tests/test_runtime_paths.py` and
`tests/test_platform_runtime.py`. They cover macOS/Linux/Windows default-path
selection, explicit override validation, strict v1 workspace/module-lock
rejection, symlink/case identity, DB override, spaces/backslashes/drive paths,
typed config rendering, both launchers, native lock/atomic behavior, process
flags and fixed read-only Codex inventory subprocess handling.

After building a wheel, run:

```bash
python tests/platform_wheel_smoke.py --wheel-dir dist
python tests/release_upgrade_smoke.py --wheel-dir dist --from-version 1.0.2
```

GitHub Actions keeps the complete Python/Django suite on Ubuntu and runs that
same clean-wheel helper on native macOS and Windows. The helper uses
`tempfile`, a space-containing wheel path and workspace, parses root plus all
role TOML and generated YAML/JSON, runs `tcx` on POSIX or `tcx.cmd` on Windows,
executes doctor/DB/hook/MCP/capability-inventory smokes, and proves local service
ensure/status/stop on the release-default loopback port, or the next available
non-ephemeral product-range port. It also loads `/` and the packaged
content-hashed JavaScript and CSS under
`/static/tradingcodex_web/assets/` from the clean wheel without installing or
invoking Node. A feature is not described as native-Windows verified until
that runner is green. The release-upgrade helper first attaches a workspace
with the published prior release, then updates it with the built wheel and
verifies that workspace and runtime identity, user-owned files, and explicit
home, DB, and service-address configuration survive while generated contracts
advance. Real Codex CLI E2E remains a final macOS-host check after
all non-Codex validation; the Windows matrix does not claim a real Codex client
session.

## Research Memory Smoke Tests

Run after research-memory changes:

```bash
mkdir -p trading/research/.drafts
printf '%s\n' '---' 'artifact_id: research-smoke' '---' '# Research Smoke' '' '[factual] Initial evidence.' \
  > trading/research/.drafts/research-smoke-v1.md
./tcx research create --markdown-file trading/research/.drafts/research-smoke-v1.md \
  --artifact-id research-smoke --type evidence_pack --universe public_equity \
  --title "Research smoke"
printf '%s\n' '---' 'artifact_id: research-smoke' '---' '# Research Smoke' '' '[factual] Updated evidence.' \
  > trading/research/.drafts/research-smoke-v2.md
./tcx research append research-smoke --markdown-file trading/research/.drafts/research-smoke-v2.md
./tcx research search "Updated evidence"
./tcx research catalog list
./tcx research catalog search "Updated evidence"
./tcx research catalog rebuild
./tcx research export research-smoke
./tcx research run-card trading/research/research-smoke.evidence.md
./tcx research validation-card trading/research/research-smoke.evidence.md
```

The smoke flow should confirm:

- workspace markdown artifact creation
- source/as-of metadata preservation
- version and content hash updates
- duplicate create with changed content is rejected within the same workspace
- markdown export path generation
- parallel catalog projection reports `full`, `legacy_partial`, and `invalid`
  coverage without modifying source artifacts
- point-in-time catalog search excludes missing, malformed, and later cutoffs
- authenticated supervisor or postmortem flows record investment-judgment
  lesson events
- doctor verifies the append-only `.tradingcodex/mainagent/improve.jsonl`
  event chain and `lesson-chain-heads.json` without a separate rebuild command
- workspace provenance recording
- no raw secrets in exported output

## MCP Smoke Tests

Run after MCP registry, handler, bridge, or role allowlist changes:

```bash
./tcx mcp stdio
./tcx mcp install-global --safe --print
```

Verify at least:

- `tools/list`
- tool annotations include category, risk, role allowlist, approval requirement, and audit requirement
- research/status tools are visible to `head-manager`
- approval creation is not visible to `head-manager`
- approval creation is visible only to the approved risk role path
- raw submit, cancel, and broker-status-refresh mutations are absent from MCP
  `tools/list` and every root/fixed-role config
- root Head Manager alone lists `use_order_turn_grant`; fixed roles omit it,
  and direct calls without the exact current-turn hook proof fail closed
- `tradingcodex-home` safe scope exposes only read-only/status/search tools
- `tradingcodex-home` safe scope may expose broker/order read-status tools
  such as `list_broker_connections`, `get_broker_connection_status`,
  `list_order_tickets`, `get_order_ticket`, and `list_reconciliation_runs`,
  but not sync, approval, submit, cancel, mapping mutation, or order-ticket
  mutation tools
- stdio emits no non-MCP logs to stdout
- `list_codex_capabilities` merges MCP, standalone skill, and installed plugin
  metadata by scope while preserving disabled state and duplicate names
- inventory handles malformed plugin manifests, missing Codex CLI, invalid
  JSON, and timeouts as bounded `partial` or `unavailable` results
- inventory output contains no commands, arguments, URL details, environment
  values, headers, tokens, credential paths, raw config, skill bodies, or hook code
- the final MCP schema and Admin contain only TradingCodex-owned
  `McpToolDefinition` and `McpToolCall` state

## Broker Provider Smoke

Run after connector, broker adapter, order-ticket, approval, execution, or
policy changes. Use a disposable workspace and a disposable runtime DB; keep
credentials in process environment only. Core ships paper only, so a real broker
smoke starts by installing or developing a provider for the requested broker.
Keep the missing-provider and approved-provider cases separate. First prove
that an implementation or connection request for a missing provider does not
create connector files or report a connector as progress:

```bash
python -m pytest \
  tests/test_broker_center_prd.py::test_provider_registry_is_request_driven_and_unknown_broker_scaffolds_development \
  -q
```

In the generated-workspace Codex smoke, make the same assertion after the
missing-provider Build turn: provider implementation may create the reviewed
bundle under `trading/connectors/requested-provider/`, but
`trading/connectors/requested-broker/` must remain absent until the provider's
exact bundle hash is approved and the service is restarted. A scaffold may
appear at this stage only when the prompt explicitly requests scaffold-only
output.

Then test connection separately with an already reviewed provider. For
repository validation, use the fake provider integration tests unless a
reviewed provider bundle has been added. For a real reviewed bundle, inspect
and approve it from an interactive operator terminal, restart the service, and
only then connect and validate:

```bash
SMOKE_ROOT="$(python -c 'import tempfile; print(tempfile.mkdtemp(prefix="tradingcodex-provider-"))')"
TRADINGCODEX_HOME="$SMOKE_ROOT/home" python -m tradingcodex_cli attach "$SMOKE_ROOT/workspace"
cd "$SMOKE_ROOT/workspace"
export TRADINGCODEX_HOME="$SMOKE_ROOT/home"
./tcx doctor
./tcx connectors providers
./tcx connectors inspect-provider requested-provider
./tcx connectors approve-provider requested-provider
./tcx service stop
./tcx service ensure
./tcx connectors connect requested-broker --provider-id requested-provider --credential-ref env:REQUESTED_BROKER --environment live --mode read-only
./tcx connectors validate requested-broker
python -m pytest tests/test_broker_center_prd.py -q
```

Do not run the approval command in CI or pipe its confirmation. It is an
interactive hash decision. In a Build-managed connector flow, the fresh
post-restart turn renders connector content, writes it with `apply_patch`, and
uses the protected register/validate services instead of the user-terminal
`connect` command shown in this operator smoke.

`register_broker_connector` should not by itself make a live-capable connector
execution-ready. A validation or live connector starts locked/read-only with
`credential_validation_status: not_checked`; `get_broker_connection_status` or
a successful account sync must prove signed health before validation scopes are
enabled, and live scopes require the separate live gate. If signed health fails,
the connection must remain/read back as locked with no enabled trade scopes, and
order checks or submit preflight must stop before consuming execution
idempotency. Authentication or permission failures must expose a secret-free
diagnostic in `health.details` and `metadata.credential_validation_details`.

Also verify the generated agent contract for broker-validation workflows:

```bash
./tcx doctor --layer codex-native
./tcx doctor --layer improvement
./tcx subagents status
./tcx subagents inspect risk-manager
./tcx skills list --all
printf '{"prompt":"Configure a reviewed test/sandbox broker connector, validate an approved order path, do not read secrets, and do not call broker APIs directly."}\n' \
  | ./tcx __hook user-prompt-submit
printf '{"prompt":"Configure a reviewed test or sandbox broker connector only. No order, no approval, no execution, do not read secrets."}\n' \
  | ./tcx __hook user-prompt-submit
./tcx subagents prompt "Configure a reviewed test or sandbox broker connector only. No order, no approval, no execution, do not read secrets."
```

Treat the smoke as failed if generated agent instructions hard-code one broker
as the only supported path, if any fixed role or Head Manager without current
hook proof can submit or cancel, if raw public MCP/API surfaces expose
submit/cancel/refresh mutations, if a retired execution role or skill remains
projected, if raw broker APIs appear in Codex MCP config, if connector-only work
attempts an execution dispatch, or if the hook routes a secret-only prompt into
execution. Also fail if a malformed or subagent native-action
prompt reaches the service; if a valid exact immediate action begins an
analysis run or spawns a role; or if an exact `$tcx-order-allow` turn can perform
more than one protected effect, survive the turn, or bypass canonical gates.

## Harness And Codex-Native Coordination Tests

Run targeted scenario tests after harness, Head Manager, hook, or fixed-role
coordination changes. Inspect logs/results rather than relying only on static
checks.

Scenarios should include:

- a broad investment request begins a lightweight provenance run and Head
  Manager chooses the first smallest useful exact role without a server plan
- explicit `$tcx-workflow` invokes Head Manager's dynamic coordination rather
  than a preselected team or materialized DAG
- `tcx-automate` authors Codex app Scheduled Tasks for simple research,
  monitoring, analysis, portfolio/status review, draft, assisted, and optional
  execution work; a saved runtime prompt invokes the selected work skill and
  never recursively invokes `$tcx-automate`
- non-execution Scheduled Task prompts start with the selected runtime skill and
  omit `$tcx-order-allow`; only an execution-capable saved prompt starts with one
  exact `$tcx-order-allow --mode paper|validation|live` line and places the runtime
  skill below it
- scheduled and interactive prompts use the same root-turn hook path with no
  Automation-origin branch; each scheduled execution turn receives a fresh
  grant decision rather than inheriting authority from an enabled task
- the shared invocation matrix covers UTF-8 BOM, CRLF/CR/NEL/Unicode
  line/paragraph separators, leading blank lines, horizontal whitespace,
  same-line Build/Brain/Strategy requests, POSIX and Windows projected skill
  links, and original-prompt hash preservation; mismatched link targets,
  case/confusable/zero-width changes, empty requests, and mixed or distinct
  managed markers fail
- connector build prompts put a plain or matching projected-link `$tcx-build`
  invocation on the first meaningful line,
  remain in the root native turn, and do not dispatch investment subagents
- missing, malformed, later-meaningful-line, mismatched-link, or subagent
  `$tcx-build` markers cannot mint or use a Build grant; every mutating
  follow-up needs a fresh invocation
- Build grants are bound to workspace/session/turn/cwd/prompt, protected MCP
  proofs are one-time, an unstarted reservation lease releases after two
  minutes, service-started revocation is deferred until finish, and the grant
  never widens the actual Codex sandbox
- matching first-meaningful-line `$tcx-brain` and `$tcx-strategy` prompts issue separate
  `brain` and `strategy` scopes in `trading-research`; each admits only its own
  canonical native source/staging path and injects proof only into
  `manage_investment_brain` or `manage_strategy`; Research runtime/launcher
  access remains denied, Build cannot substitute for either, mixed markers and
  cross-scope commands fail, and Plan/subagent contexts remain blocked
- user-installed Codex capabilities remain callable through native Codex and
  cannot mint TradingCodex principals, grants, reserved namespace entries, or
  order proof; an unapproved or changed
  workspace provider is never imported by provider listing or connector status
- throughout every active Build turn/profile, native `apply_patch` is the edit
  surface and the hook admits only public GET/HEAD, enumerated read-only HTTPS
  Git, limited workspace `pwd`/`cat`/`ls`, inert provider-source
  reads/hash/diff/Git inspection, exact isolated
  `python -I -S -m py_compile`, and allowlisted workspace-launcher commands;
  general interpreters, helper scripts, test runners, build systems, shell
  composition, and model-authored POST fail closed
- Build public fetch permits credential-free HTTP(S) GET/HEAD and HTTPS Git
  clone/fetch/ls-remote into
  `$TRADINGCODEX_SCRATCH/provider-sources/<provider-id>/`, while URL userinfo,
  auth/cookie/API-key headers, request bodies/uploads, non-GET/HEAD methods,
  SSH/file/git transports, local/private targets, package installs,
  fetch-to-shell, direct downloads/copies/moves into `trading/`, Git push, and
  remote changes fail. The Build proxy's full HTTP transport admits Git Smart
  HTTP's read-only protocol POST only through those enumerated Git commands;
  model-authored general POST remains blocked, and Build-native browser, web,
  HTTP, fetch, and navigation tools cannot bypass the shell allowlist while
  Research browser behavior remains available. Generated hook tests must prove
  native user MCP calls are not blanket-blocked while TradingCodex protected
  paths, principals, grants, and order proof remain closed
- direct HTTP(S)-only provider staging admits `curl --create-dirs` only for one
  URL and one explicit direct `<provider-id>/<file>` output when that provider
  directory does not exist. The hook only validates the command; curl creates
  the one directory during the admitted fetch. Missing `--create-dirs` for that
  fresh parent, nested output, `--remote-name`/`--output-dir` forms, duplicate
  flags, multiple URLs or outputs, an existing provider directory, Research use,
  and any attempt to treat the exception as general directory creation fail
  closed. Subsequent HTTP(S) files use an existing real direct provider parent
  without `--create-dirs`
- when Codex omits the shell workdir from hook input, Build tests use the exact
  advertised absolute executables, absolute workspace/staging operands, and
  absolute Git `-C` roots. Relative tools and operands, nested-workdir launcher
  shadows, curl glob/template expansion, checkout-on-clone, secret-like or
  nonregular source reads, and Git object/worktree indirection must fail closed
- provider bundle validation rejects VCS metadata, credential/key/`.env`
  material, and symlinks; optional `source-provenance.json` participates in the
  bundle/snapshot hash, requires exactly one resolved source identifier, and
  changing provenance or a helper makes approval stale. Static Build checks do
  not import the provider, and an implementation/connection request does not
  create a dead-end connector before the provider is approved and restarted.
  Build-side `inspect-provider` returns the inert bundle hash and provenance as
  `inspection_scope=bundle_only` and `approval_status=service_check_required`
  when the central ledger is denied; an interactive operator inspection still
  resolves canonical approval state before approval
- recurring Build Automation works only when its saved prompt deliberately
  starts with `$tcx-build`; each run is re-evaluated and the marker is never
  combined with another managed or order marker; recurring Brain/Strategy
  management starts directly with its matching exact skill marker in
  `trading-research`
- retired `tcx mode` state and old `.tradingcodex/runtime/mode.json` files are
  inert, including malformed or symlinked legacy files
- explicit prohibitions such as "no order" remain binding without a
  language-specific keyword router
- guardrail-verification wording does not trigger execution
- secret-only credential, token, broker-key, password, or `.env` prompts create
  secret-wall warning context without subagent dispatch
- earnings, catalyst, valuation, fact-only, and technical-only requests cause
  Head Manager to derive request-specific questions and justify each chosen role
- vague public-equity prompts do not expand to a default analyst team; Head
  Manager narrows or asks for clarification when scope materially changes the
  result
- narrow requests do not add unrelated roles or independent judgment review
  unless accepted evidence or the user's mandate justifies it
- backtest, signal, and model-performance prompts require anti-overfit
  validation without implying strategy authoring or execution
- strategy authoring prompts route to `tcx-strategy`/strategy CRUD instead
  of investment subagent auto-dispatch
- native strategy binding accepts one exact explicit `$strategy-*` invocation,
  rejects ambiguous multiple invocations, never infers from a plain-language
  strategy name, and records `no_strategy` when no token is present
- native strategy selection seals the validated active strategy's
  exact bytes under the run directory and bind its snapshot path and hash
- explicit Decision Memory prompts retrieve, replay, review, or validate without
  creating a server workflow plan; current-decision use records an independent
  initial view before prior-case retrieval
- historical replay rejects post-cutoff evidence and reports replay, holdout,
  and live-forward evidence separately
- postmortem review separates an outcome-blind process assessment from outcome
  and calibration assessment, then emits a lesson candidate rather than a rule
- when both valuation and portfolio fit are material, Head Manager obtains the
  owning role artifacts in an evidence-dependent order and preserves their
  distinct boundaries
- native Codex starts from natural language or an exact built-in analysis skill
  and produces source-bound artifacts and accepted synthesis
- the viewer displays Library, Skills, and System for the selected registered
  workspace and exposes no prompt or process controls
- starter-prompt next allowed actions distinguish unanswered, partially
  answered, disabled, and complete workspace investor context
- explicit investor-context updates persist to the workspace Markdown file;
  enable/disable changes the default, native run binding follows that default,
  while the viewer offers no one-run override
- Codex `UserPromptSubmit` generated hooks keep transport/run hints under budget
  and never classify meaning or choose roles; `begin_analysis_run` seals enabled
  workspace Investor Context and an exact explicitly invoked `$strategy-*`
  before Head Manager dispatches any role
- an initial non-interactive prompt with no `UserPromptSubmit` event creates a
  fresh lightweight run through the head-manager-only MCP tool and never reuses
  a stale session binding
- unavailable or unverified subagent routing fails closed without a generic
  spawn, role-TOML/source-code emulation, or an empty wait
- unavailable or unauthenticated native Codex does not affect viewer readability
  or corrupt workflow state
- completed role artifacts are reused when quality gates pass
- Head Manager and every fixed role inherit the actual `trading-research`
  profile, general shell/Python, credential-free public retrieval, and ordinary
  user-owned file writes outside `trading/` work, protected
  `trading/`/control/runtime/DB/credential/local-network access fails, final role
  reports use authenticated MCP only, and the service derives
  producer identity, run/Brain lineage, schema, dependency, content, and receipt
  hash fields
- generated root config selects `trading-research`, defines both native
  profiles, forwards only the shell environment allowlist, and contains no
  legacy `sandbox_mode`; fixed-role TOML also contains no sandbox override
- generated `$TRADINGCODEX_SCRATCH` resolves to the workspace-id-scoped
  platform cache tree, projects the same path through `TMPDIR`/`TEMP`/`TMP`,
  and creates a real non-symlink `provider-sources` staging directory while the
  broad OS temporary roots remain denied
- Research-profile native smoke proves ordinary user-owned workspace writes,
  dedicated scratch writes, and a credential-free public fetch work while
  `trading/` writes, generated control-file writes, `.env`, TradingCodex
  home/DB/runtime, local loopback, and Unix-socket access fail; Build-profile
  smoke proves controlled connector edits through `apply_patch`, limited
  workspace `pwd`/`cat`/`ls`, inert provider read/hash/diff/Git inspection,
  isolated `py_compile`, allowlisted `./tcx`/`tcx.cmd`, and credential-free
  public HTTP(S)/HTTPS Git retrieval work while general interpreters, helper
  scripts, test runners, build systems, shell composition, model-authored POST,
  authenticated requests, local/private targets, direct fetches into
  `trading/`, fetched-code execution/installation, publication, and the same
  protected paths fail
- head-manager does not inspect schemas, generated indexes, role TOML, source,
  or CLI runtime paths to reconstruct bindings and does not file-edit workflow
  state or role artifacts; synthesis id/path/input hashes are service-derived
- `tcx subagents plan <agent...>|--all` previews only an explicitly supplied
  fixed roster and thread-capacity batches; it does not infer roles or record
  workflow state. Generated workspaces omit the retired capability/role/policy-
  binding YAML copies
- downstream roles return `revise`, `blocked`, or `waiting` instead of filling missing upstream role work
- hook `additionalContext` stays compact and points to the current run-binding
  contract instead of injecting a full starter prompt or semantic plan
- starter prompts and generated guidance expose the no-overlap handoff contract
- starter prompts and generated guidance tell subagents to write reader-facing
  research artifacts in the user's language unless explicitly overridden
- starter prompts and generated guidance tell `head-manager` to keep final chat
  brief while saving full accepted-artifact synthesis as a Markdown report under
  `trading/reports/head-manager/`
- `tcx quality-check <artifact> --strict` fails research markdown that lacks
  source/as-of posture, `context_summary`, material claim tags, handoff state,
  confidence, missing-evidence fields, next-recipient routing, blocked actions,
  or source snapshot metadata
- accepted run-bound writes apply that same strict contract before publication,
  including structured `follow_up_requests` and `improvements`; tests assert a
  rejected write cannot create a stable artifact or service receipt
- `tcx quality-check <artifact> --strict` validates
  `trading/forecasts/*.jsonl` forecast records and fails malformed probability
  ranges, missing resolution fields, or invalid open/closed status
- `tcx quality-check <artifact>.run-card.json --strict` validates Evidence Run
  Card config hash, input/source refs, artifact hashes, metrics or validation
  summary, warnings, limitations, timestamp, and evidence-only authority
- `tcx quality-check <artifact>.validation-card.json --strict` validates
  Validation Card evidence-quality labels, anti-overfit evidence metadata,
  artifact hashes, metrics or validation summary, warnings, limitations,
  timestamp, and evidence-only authority
- `tcx quality-check trading/research/source-snapshots/<id>.json` surfaces
  data-boundary warnings for OHLC invariants, non-positive prices, duplicate or
  sparse bars, timezone/as-of ambiguity, adjustment ambiguity, stale sources,
  invalid JSON constants, and missing fallback policy
- generated starter prompts and subagent-management skills include a context
  budget so agents pass artifact paths, context summaries, source snapshot IDs,
  and short deltas instead of full artifacts or repeated role manuals
- multi-round subagent smokes run `tcx subagents context-audit --strict` after
  several independent analysis runs, subagent start/stop events, and large
  research artifacts across research-only, thesis, portfolio/risk, order-draft,
  approval/execution, crypto, ETF/index, and options/instrument requests; the
  audit must show compact hook context, compact run-binding history, compact session
  state with total counters plus retained recent events, no pasted markdown artifacts in
  run-binding history or session state, no research artifacts missing `context_summary`, and
  warnings for artifacts missing reader-first `reader_summary` or `next_action`
- repo skill boundary tests fail when role identifiers leak into generic skills
  outside necessary command principal examples or policy/artifact contracts
- MCP `tools/list` exposes both TradingCodex custom annotations and standard
  MCP hints such as `readOnlyHint`, `destructiveHint`, `idempotentHint`, and
  `openWorldHint`
- authenticated API additional-agent-instruction edits are saved as-is, projected
  after generated defaults but before the immutable core/extension footer, and
  removable without leaving stale marker blocks
- clean-host and populated-host Codex smokes compare the same pristine request;
  a host-global sentinel skill must not appear without explicit opt-in, and a
  same-name managed/global collision must fail doctor before quality claims are
  made
- a separate managed-activation smoke proves that a user-approved overlay is
  projected, attributed, non-implicit by default, and applied only when selected
- `tcx doctor --layer task-harness` is rejected; `improvement` is the canonical
  layer name in the v1 contract

Harness taxonomy checks should confirm:

- product web opens on Library and keeps Skills and System available under the
  stable hash routes with readable, sanitized artifact previews
- Guardrails are split into Guidance, Enforcement, and Information barriers
- Improvement is separate from Guardrails
- `tcx doctor --layer improvement` runs the quality/workflow checks

## Browser And Viewer Verification

After the frontend build and focused GET API tests pass, start the generated
workspace with `./tcx service ensure`, read its URL from
`./tcx service status --json`, and use a real browser against that exact URL.
The release default is `127.0.0.1:48267`; development workspaces normally use
their checkout-scoped projected address. Verify:

- desktop and narrow responsive layouts without hidden primary actions or
  horizontal content loss
- Library and Skills use a list-or-detail transition at narrow widths with
  a visible Back action and no stale detail body during selection changes
- keyboard-only section navigation, visible focus, labeled controls, and
  logical focus after section changes, detail transitions, dialogs/errors, and
  workspace and section changes
- empty library, loading, invalid-workspace, missing-data, and partial-section
  failure states
- registered-workspace switching at desktop and narrow widths, with no arbitrary
  path input or silent fallback
- Skills and extensions remain inspect-only and native Codex guidance is clear
- no browser console errors, unsanitized workspace HTML, raw reasoning, raw tool
  payloads, stderr, or secrets
- Django Admin still renders and behaves as the default Admin surface

When Codex CLI and authentication are available, run one real native analysis
smoke in a disposable generated workspace. It must load the generated
`head-manager`, preserve explicit prohibitions, dispatch only roles dynamically
justified by the mandate and accepted evidence, write accepted artifacts, and
stop without an order, approval, execution, cancellation, broker mutation, or
secret action. Record an unavailable Codex/auth blocker rather than replacing this with a
claim based only on the fake subprocess test.

Reference acceptance uses Codex CLI 0.144.4. Run
`python tests/codex_cli_contract.py --workspace <workspace>
--require-reference --require-hook-trust` first, after opening the disposable
workspace in interactive Codex and persistently trusting all eight generated
hooks in a dedicated maintainer `CODEX_HOME`. The preflight requires the exact
reference version, strict Codex config loading, locally consistent MCP
configuration, readable sandbox settings, the expected enabled/disabled
feature states, and trusted lifecycle hooks. Native `codex exec` smokes must
also pass `--strict-config`, so a newly unknown or removed project key fails
before model behavior is accepted. Do not use `--ignore-user-config` or
`--dangerously-bypass-hook-trust` for lifecycle acceptance: in 0.144.4 the
one-run bypass is not inherited when a V2 child reloads an exact role config.
On platforms where a temporary directory has a symlinked alias, resolve the
workspace once with `realpath` or `Path.resolve()` and use that same physical
path for interactive hook review, the contract preflight, and every native
run. Hook trust is bound to the hook source path and current hash; mixing, for
example, `/var/...` and `/private/var/...` makes a correctly reviewed hook
appear untrusted.

Run a second native CLI dispatch smoke that inspects the actual JSONL tool call
and child hook audit. The parent must select its chosen role through exact
`agent_type`, pass compact context without a full-history fork, and the child
must load the role's projected model, reasoning effort, principal, and the same
actual project-wide `trading-research` profile. Require `fork_turns="none"`, no model or
reasoning override, and no `followup_task`. Run a sequential two-spawn smoke as
well as one full artifact-to-synthesis workflow because V2 lifecycle failures
can appear only after the first child finishes. Also run the same request with
multi-agent dispatch disabled: it must stop at
`waiting_for_subagent_dispatch` without spawning a default agent or reading
TradingCodex source/role files to imitate one.

## Release-Sensitive Validation

Before release or packaging changes, run:

```bash
npm ci --prefix frontend
npm test --prefix frontend
npm run build --prefix frontend
git diff --exit-code -- tradingcodex_service/static/tradingcodex_web
python -m pytest
python manage.py check
python manage.py makemigrations --check --dry-run
python -m compileall tradingcodex_cli tradingcodex_service apps tests
python -m build
python -m twine check dist/*
```

Also install the built wheel in a clean environment and run:

```bash
python tests/platform_wheel_smoke.py --wheel-dir dist
python tests/release_upgrade_smoke.py --wheel-dir dist --from-version 1.0.2
```

Detailed release workflow lives in [deployment.md](./deployment.md).

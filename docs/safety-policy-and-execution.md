# Safety, Policy, And Execution

This document owns executable safety: permission checks, approval rules,
execution lifecycle, workspace viewer read/auth boundaries, adapter
boundary, blocked actions, and secret handling.
Use [guardrails.md](./guardrails.md) for the broader guardrail taxonomy.

## Safety Model

TradingCodex safety is part of the investment OS core kernel and is coordinated
through the harness subsystem. Guidance reduces risky behavior early, but only
deterministic enforcement on the final action path can block execution.
Information barriers limit what roles can see or do, while improvement loops
raise quality without becoming executable authorization.

## Executable Action Rule

Every final submit or cancel action follows one of two native-user entries:

```text
exact immediate root action -> deterministic hook parse -> native-user permission
  -> policy -> payload validation -> approval/duplicate-request check
  -> mandatory intent audit -> connection -> finalized/uncertain audit

exact first-meaningful-line $tcx-order-allow -> workspace/session/turn/prompt/mode grant
  -> canonical workflow state -> protected tool + PreToolUse proof
  -> consume once -> the same policy/approval/idempotency/live/audit gates
```

This order matters:

1. Exact user admission: require either one action-only immediate submit/cancel
   prompt or an exact first meaningful line `$tcx-order-allow --mode
   paper|validation|live` on the current root turn. A matching projected
   Markdown skill link may replace only the skill token; all arguments stay
   literal.
2. Parse: create a workspace-bound immediate mandate, or a workspace-,
   session-, turn-, full-prompt-, and mode-bound single-use turn grant before
   any model or subagent runs.
3. Permission: authorize only the narrow `native-user` submit or cancel action.
4. Policy: check restricted list, limits, account, connection, and
   live-execution posture.
5. Payload validation: resolve and validate canonical structured state.
6. Approval and duplicate-request check: prove approval is valid and the order
   has not already produced an execution result.
7. Audit and connection: commit the mandatory intent audit before invoking only
   an enabled connection, then record a finalized or uncertain result.

Policy and approval are revalidated immediately before connection use.
Broker connection invocation is always owned by the Django service layer.
Codex may draft, explain, classify, and request checks, but it must not call a
raw broker execution primitive directly.

The requester is transport-bound, not trusted from a request body. A generated
role MCP instance supplies `TRADINGCODEX_MCP_PRINCIPAL`; a payload principal is
accepted only when it matches that immutable binding. HTTP mutations require a
bound API principal/key or authenticated staff session. Unsafe Ninja requests
authenticated by a staff session also require CSRF; an API key remains a
non-cookie transport and does not. Role-authored workflow, research, order,
broker, forecast, and evaluation mutations pass through the canonical MCP tool
registry so role allowlists, active-principal state, capabilities, and payload
identity binding are the same over HTTP and stdio. Staff status alone does not
grant an agent role, and a staff username collision with an active principal id
does not authorize role-owned tools. Role-authored HTTP mutations require an
API-key-bound principal. Staff may perform documented administrative overlay
operations, while API-key use of those administrative mutations requires an
active canonical `head-manager`. The workspace viewer is GET-only and creates
no local-loopback mutation exception. Remote mutation use always requires
staff/API-key authentication. Loopback is not generic mutation authority.

Immediate final submit and cancel do not use an agent MCP or REST identity. The
`UserPromptSubmit` hook accepts only an exact root native action prompt, binds a
short-lived mandate to the original prompt hash and workspace provenance, and
calls the canonical execution gateway in-process as `native-user`.

For a workflow that creates or selects canonical identifiers later in the
turn, the same hook accepts only an exact first-meaningful-line
`$tcx-order-allow` mode, requires current Codex `session_id` and `turn_id`, and issues a grant
bound to workspace, session, turn, original full prompt hash, Codex permission mode, and
execution mode. Plan mode rejects both immediate execution and turn-grant
issuance or use; the user must start a new non-Plan root turn. The grant expires
after one hour and is revoked on consumption, `Stop`, or the next user turn.
Only root Head Manager can select one `submit` or `cancel` through
`use_order_turn_grant`. `PreToolUse` reserves that grant for the tool-use id and
injects an internal proof into rewritten input; a direct MCP caller or model
cannot provide the proof. The service consumes it before entering the same
canonical execution kernel. Passing a principal id or reproducing action text
grants nothing.

After proof consumption, `result_status=authorizing` means the canonical broker
effect is still in flight even though the grant cannot be reused. `Stop` and a
new turn never reset or retry it. A new Build or order-sensitive prompt in the
same session fails closed until the result becomes terminal; ordinary research
may continue. Inspect canonical order status and never retry an uncertain or
still-authorizing effect blindly.

Codex app Scheduled Tasks run their saved prompt on each scheduled turn and can
explicitly invoke a skill. TradingCodex treats that prompt exactly like any
other root turn and does not detect or trust an Automation origin. See the
[Codex Scheduled Tasks](https://developers.openai.com/codex/app/automations)
and [hooks](https://learn.chatgpt.com/docs/hooks) documentation.
The mandate signature protects in-process field integrity; it is not OS-level
attestation against arbitrary same-user Python. The generated native analysis
profile explicitly enables lifecycle hooks and disables unified execution and
interactive app/browser/computer features. Its default `trading-research`
permission profile allows ordinary shell, Python, command-line data tools, and
credential-free public HTTP plus read/write access to user-owned workspace
paths outside `trading/`. Disposable intermediates use the generated path
exposed as `$TRADINGCODEX_SCRATCH`. The profile extends the native read-only
baseline through the built-in `:workspace` permission profile, then applies
more-specific read or deny rules for `trading/`, control files, Git metadata,
launchers, temp roots, and sensitive paths. The generated scratch child is
reopened and projected as `TMPDIR`/`TEMP`/`TMP`. It explicitly denies
credential-bearing home paths and
the canonical TradingCodex home, database, attached runtime interpreter,
protected workspace state, credential files, local/private destinations, and
Unix sockets. A narrow read-only exception for the installed standalone Codex
runtime supports native file tools without exposing Codex auth, config,
session, or memory state. The enabled native network proxy enforces the public-only command
network rules. The shell receives only a small allowlist of non-secret process
environment variables. This OS-level authority boundary, not a ban on useful
interpreters, prevents model-launched code from reading grants or reaching the
service ledger. `PreToolUse` and `PermissionRequest` retain deterministic
blocks for interactive `write_stdin`, raw credentials, order effects, runtime
mutation, global config, and remote publication.
Codex loads that project config, its MCP server, and its hooks only after the
workspace is trusted. An untrusted workspace, or a managed policy that disables
hooks, therefore has no native execution gateway; it must fail closed instead
of falling back to shell, public MCP, REST, generic CLI, or a model-selected
path. Trust the attached workspace and review its hooks before using native
execution.

## Workspace Viewer Boundary

The product web reads a canonical snapshot plus sanitized skill and artifact
detail for one registered attached workspace. It exposes no prompt preview,
run start, follow-up, cancellation, skill mutation, or `codex exec` path. Native
Codex is the only agent runtime. Invalid or stale workspace ids fail rather than
falling back, and the SPA remains loadable so the JSON error is visible.

## Workspace Turn Boundaries

### Build turns

Build authorization is current-turn intent, not a persistent workspace mode or
a permission elevation. A valid root native Codex prompt invokes `$tcx-build`
on the first meaningful line and has a non-empty concrete request on that line
or later. The plain token and a Markdown link are equivalent only when the link
label and target match the current workspace's projected skill. The
deterministic `UserPromptSubmit` hook issues a DB-canonical `BuildTurnGrant`
bound to the workspace, session, turn, cwd, and full prompt. `PreToolUse`
requires that grant for controlled `trading/` edits and injects one-time proof
into each protected build MCP call. Ordinary `apply_patch` edits outside
`trading/` need no Build grant; generic Write/Edit tools remain blocked so file
changes stay reviewable. Multiple Build edits and validations may use the same
grant within that turn, but every Build follow-up must start with `$tcx-build`
again. Subagents cannot mint, inherit, or consume a Build grant, and the browser
viewer has no Build path.
Codex platform Plan mode cannot issue or use one. The grant records its
issue-time permission mode, and a later mode change does not carry the grant
forward.

Codex's active permission profile remains the filesystem and network authority.
The marker, skill, and hook cannot promote the default `trading-research`
profile. Ordinary user-owned file work outside `trading/` remains available in
Research. Controlled `trading/`, optional-role-skill lifecycle, and generic
Build work starts in a new root turn with the `trading-build` profile selected
and the exact marker present.
That profile can write connector/build paths and the dedicated scratch path,
and permits credential-free public HTTP(S) and HTTPS Git retrieval, but still denies the
TradingCodex home, DB and mutable runtime state, credential files, global config, audit,
approval, order, and durable artifact paths. Authenticated requests, uploads,
local/private destinations, non-HTTP(S) transports, network package installs,
fetch-to-execute pipelines, remote mutation, and broker access remain blocked.
Its proxy transport mode is full only so read-only Git Smart HTTP can complete
its protocol POST; the hook still limits model-issued network commands to
public GET/HEAD retrieval and enumerated HTTPS Git clone/fetch/ls-remote forms.
The generated launcher's immutable runtime tree is read-only in Build solely
for hook-admitted `./tcx` inspection and validation commands; direct runtime
execution and general interpreters remain blocked.
Plan mode blocks
grant issuance and use entirely. Start a new root Build turn in the required
profile rather than treating the grant as elevation.
### Brain and Strategy management

Investment Brain and Strategy management are separate capability-scoped
operate-plane actions. They start directly with `$tcx-brain` or
`$tcx-strategy` on the first meaningful line in `trading-research`; the plain
token or matching projected link is accepted and the concrete request may
share the line or follow it. The shared DB grant records `brain` or
`strategy` scope and the hook permits only the matching source/lifecycle
operation. Brain source editing and Strategy body staging remain native
workspace-file work; registry and generated projection changes use only
`manage_investment_brain` or `manage_strategy`, with a hook-owned one-time
proof. Build, Brain, Strategy, and order markers cannot be combined. A scope
cannot authorize another scope, Plan mode blocks issuance and use, and
subagents cannot inherit a grant. The Research profile keeps the generated
launcher, attached runtime, DB, registry, and projections denied. A model-side
`tcx strategies` or `tcx investment-brains` command is blocked with a precise
MCP/user-terminal handoff instead of reopening that runtime.
Build cannot authorize global Codex config changes,
raw credential access, Git push/publication, direct edits
to hooks, grants, the managed `.gitignore`, credential files,
audit/runtime DB/policy/approval/order state, or financial execution.
User-installed Codex MCP servers, skills, plugins, apps, and hooks remain owned
by Codex and the user. TradingCodex does not install, enable, disable, remove,
classify, approve, or proxy them. They remain subject to Codex sandbox,
approval, and organization requirements, but are outside TradingCodex's
license, trust, audit, data-terms, cost, and execution guarantees.
Workspace provider approve/revoke still mints one opaque, process-local,
single-use service capability only after the CLI's TTY and exact-text
confirmation, bound to the exact workspace, provider, and reviewed bundle.

The generated Build shell is intentionally narrow throughout every active
Build turn/profile. Native `apply_patch` is the reviewable edit surface; shell
commands are limited to public HTTP(S) GET/HEAD, enumerated read-only HTTPS Git,
workspace `pwd`/`cat`/`ls` in their admitted forms, inert provider-source
reads/hash/diff/Git inspection, exact isolated
`python -I -S -m py_compile`, and allowlisted workspace-launcher commands.
General interpreters, helper scripts, test runners, build systems, shell
composition, and model-authored POST are blocked. Public provider source is
fetched only into `$TRADINGCODEX_SCRATCH/provider-sources/<provider-id>/`, where
it may be reviewed but never executed or installed. Curl `--create-dirs` is a
single-purpose HTTP(S)-only staging exception: one URL and one explicit direct
`<provider-id>/<file>` output may create one fresh provider-id directory. It is
not general directory-creation authority; nested paths,
`--remote-name`/`--output-dir` forms, repeated use, and existing provider
directories remain blocked. Later files require the existing real direct
parent and omit the flag. The generated `PreToolUse`
matcher covers every tool name. It leaves ordinary Research browser behavior
unchanged, while an active Build grant blocks native browser, web, HTTP, fetch,
and navigation tools so retrieval cannot reuse browser sessions. The hook separately
supports a fail-closed absolute command proof when native Codex omits shell
workdir from its hook payload: exact recorded executables, absolute file
operands, and absolute Git `-C` roots replace any trust in the unseen cwd.
Relative reads, validation, and launcher commands require the exact generated
root workdir. Curl globbing, checkout-on-clone, secret-like/nonregular provider
reads, and Git object/worktree indirection remain blocked. The hook separately
proof-gates controlled `trading/` edits and protected workspace MCP mutations,
keeps generic Write/Edit tools blocked, and blocks credentials, global Codex
configuration, remote publication, and order effects.
Broader unit, smoke, or build validation and any action that needs protected
runtime access remain explicit user-terminal/operator or maintainer work.

Scaffold rendering is a read-only, content-addressed MCP operation: it returns
target content/hash and preimage existence/hash/size without returning existing
file content, performs no workspace write, and leaves file creation or
replacement to native `apply_patch` under Codex's sandbox. Agent MCP exposes
no connector `connect` or write-style
`scaffold` operation. Only the DB-backed connector registration, validation,
and mapping-review calls are Build-protected.

Provider onboarding is provider-first. If a provider is missing, Build fetches
and implements it before rendering or registering a connector; a
`provider_development_required` connector is created first only for an explicit
scaffold-only request. Final provider files must be authored with
`apply_patch`, not downloaded, redirected, copied, or moved into `trading/`.
Externally informed bundles include `source-provenance.json` with
`schema_version: 1` and per-source `kind` (`https` or `git`), a public
credential-free HTTPS `url` without userinfo/query/fragment, optional
`requested_ref`, and exactly one resolved identifier: HTTPS uses
`resolved_ref`, while Git uses `resolved_ref` or `resolved_commit`. Each entry
also includes `fetched_content_sha256` and RFC 3339 `retrieved_at`. VCS metadata,
credential/key/`.env` material, and symlinks are rejected. Legacy manually
authored providers may omit provenance. Static syntax and contract checks do
not import the provider.

Those protected connector MCP calls use one hook-owned reservation at a time. A
reservation that never reaches the service is released after two minutes and
its proof becomes invalid. A call that already entered the service is never
revoked mid-flight: `Stop` or a new turn marks revocation pending, and the grant
becomes terminal when that call finishes. Schema/auth failures after proof
consumption finalize the call as an error and release the grant instead of
leaving it reserved. There is no caller-controlled switch to disable this proof
check. If the protected operation completes but normal grant finalization
fails, a separate idempotent recovery path never reruns the operation; it clears
the reservation, counts the use, records `finished_unfinalized`, and revokes the
grant fail-closed with a do-not-retry-blindly error.

Persistent `tcx mode` is retired. Compatibility status is always inert,
`tcx mode set ...` cannot enable Build, and any old
`.tradingcodex/runtime/mode.json` file is ignored. An ordinary analysis thread
cannot promote itself into a Build turn, and the browser viewer cannot create one.

Codex app Scheduled Tasks use this same root-turn hook path. A recurring Build
task must be explicitly saved with the canonical plain `$tcx-build` invocation
as its first meaningful line; each
run receives a fresh grant decision and remains constrained by that run's
sandbox. Controlled `trading/` or optional-role-skill lifecycle scheduled work
therefore also requires a `trading-build` Automation runtime. Recurring Brain
or Strategy management starts directly with its exact managed skill marker and
uses `trading-research`. A `trading-research` run may
read and write ordinary user-owned paths outside `trading/`, use temporary
computation, credential-free public retrieval, rendering/inspection, and
specifically proof-protected canonical DB calls; Plan mode blocks Build
entirely. Use an isolated worktree or workspace and retain a reviewable diff
before accepting recurring changes. `$tcx-build` and
`$tcx-order-allow` must never be combined.

Only normalized, redacted, allowlisted events and public analysis-run
projections may be stored or returned. Run, hook, research-root, and verified
generated-runtime paths reject symlinks. MCP artifact writes bind `created_by`,
`role`, and `producer_role` to the authenticated fixed role and record a receipt
bound to the sealed run lineage and current artifact bytes, with synthesis
reports reserved for Head Manager. Raw reasoning, tool inputs/outputs, stderr,
and raw final output are discarded, not written into the workspace. Final output
additionally requires authenticated receipts, accepted handoff readiness, the
applicable quality gate, Head Manager producer binding, body hash, and the
complete accepted-input hash set. An accepted analysis artifact remains evidence, not
an order, approval, or execution authorization. All role, MCP, policy, approval,
idempotency, connection, and audit boundaries remain unchanged.

## Execution Lifecycle

| Step | Artifact/action | Owner | Required rule |
| --- | --- | --- | --- |
| Evidence collection | evidence pack | analyst roles | Separate sources, dates, facts, and assumptions. |
| Analysis | analyst reports, valuation | role subagents | Maintain each role's information barrier. |
| Portfolio fit | portfolio review | `portfolio-manager` | Check sizing, cash, concentration, liquidity, and portfolio fit. |
| Broker sync | `BrokerSyncRun`, `PortfolioLedgerEvent`, `ReconciliationRun` | service layer | Read-only connection path only; raw credentials are references. |
| Draft order | `OrderTicket` | `portfolio-manager` | No execution before schema, policy, cash/position, broker validation, and risk checks. |
| Risk review | risk/policy report | `risk-manager` | Check restricted list, downside, limits, and approval readiness. |
| Approval | `ApprovalReceipt` | `risk-manager` | Bind approval to exact order payload hash, broker/account, max notional/price, order type, time-in-force, and expiry. |
| Execution | exact immediate `$tcx-order-submit`, or one protected `use_order_turn_grant` call in an exact `$tcx-order-allow` turn | native user intent plus Django service | Immediate prompts parse before the model. Turn grants require hook proof and are consumed once. Both resolve DB-canonical ticket/receipt state, revalidate mode and policy, reserve idempotency and mandatory audit before provider invocation, then finalize or mark `NEEDS_REVIEW`. |
| Cancellation | exact immediate `$tcx-order-cancel`, or the same one-effect protected turn grant | native user intent plus Django service | Require the canonical ticket, broker order, and approval ids, then preserve mode, policy, live-confirmation, idempotency, audit, and uncertain-result gates. |
| Audit/postmortem | audit event, execution result, postmortem | service/head-manager | Record mandates, rejects, approvals, executions, and policy decisions; Head Manager may explain recorded state but has no action authority. |

Audit writes use one validated event envelope with `type`, object `payload`, and
optional explicit `resource` and `decision`. Event-name aliases and implicit
resource or decision extraction from payloads are not accepted in v1.

Inline receipt dictionaries and workspace receipt paths are not submission
authority. Submission resolves a central DB `ApprovalReceipt` linked to its
ticket and requires the exact order hash, broker/account scope, order type,
time-in-force, limits, expiry, and unconsumed state to match. Receipt validation,
ticket locking, execution reservation, and the mandatory pre-provider audit are
committed together. An unavailable mandatory audit sink prevents adapter
invocation.

Approved execution is idempotent by order/profile boundary. A repeated
`submit_approved_order` call for an order that already has an
`ExecutionResult` in the same `portfolio_id` / `account_id` / `strategy_id`
must be rejected before any connection is called.
Connection readiness failures, such as missing credentials, disabled live opt-in,
or signed-health errors before a broker submit attempt, must fail before creating
an `ExecutionResult` so the operator can retry after fixing configuration,
credentials, permissions, or IP allowlists. Once a broker submission may have
reached the provider submit boundary, TradingCodex records `NEEDS_REVIEW` /
unknown status and duplicate protection applies until status is reconciled.
Provider correlation data stays on the durable execution record so recovery
does not rely on retrying an uncertain submit. The correlated ticket payload
remains immutable while it is in `NEEDS_REVIEW`.

Order ticket ids are central-DB ids. Read and preparation surfaces use
`ticket_id`; approval and provider boundaries use `approval_receipt_id` and
`broker_order_id` respectively. Public CLI, API, and MCP surfaces do not expose
submit, cancel, or broker-status-refresh mutations. If the same ticket id
appears with a different payload, validation must fail closed instead of
mutating the existing ticket.
Repeating the same payload is idempotent and never rewrites ticket state or
approved/executed data.

`OrderTicket` state changes must happen through explicit service functions.
Invalid transitions are blocked, and every transition writes `OrderEvent` plus
an audit event in the same database transaction. Provider submit, cancel, and
status responses require canonical v1 statuses, string identifiers, and
timezone-aware timestamps. A provider-boundary or post-fill account-sync
failure durably moves the execution and ticket to `NEEDS_REVIEW` with its
reason, event, and required audit instead of returning a false success. Django
Admin is read-only for `OrderTicket`, `ApprovalReceipt`,
`ExecutionResult`, `BrokerOrder`, `Fill`, `OrderCheckRun`, and `OrderEvent`; it
cannot rewrite payload, state, approval, submission, fill, or `NEEDS_REVIEW`
records outside those services. The supported lifecycle is:

```text
DRAFT -> PRECHECKED -> READY_FOR_APPROVAL -> APPROVED -> RESERVED
  -> SUBMITTED -> ACKED -> PARTIALLY_FILLED -> FILLED
```

Terminal or review states are `REJECTED`, `CANCELED`, `EXPIRED`, `FAILED`,
and `NEEDS_REVIEW`. Fills create `Fill`, `BrokerOrder`, `OrderEvent`, portfolio
ledger, snapshots, and reconciliation records. Validation submissions create
broker-order and audit records but no fill when the broker endpoint validates
without sending an order to a matching engine. For validation-only connector
modes, `_refresh_broker_order_status_for_reconciliation` preserves the local validated state when
the broker endpoint intentionally does not create an external order. Live cancel
uses the installed provider cancel path and remains audited.

Signed broker credential failures are execution blockers, not execution
attempts. The connector remains read-only with no enabled trade scopes, exposes
only service-owned health status, message, and detail projections, and
`submit_approved_order` stops before reserving or consuming execution
idempotency.

Provider health text, health detail dictionaries, exception messages, and
account display metadata are untrusted. TradingCodex persists only fixed health
codes, validated exception classes, allowlisted account types, service-derived
account labels/masked identifiers, and the required canonical broker account
identifier. Adapter-supplied account metadata is discarded.

Workspace broker provider source is untrusted code. Provider discovery and
inspection hash `provider.py`, optional `source-provenance.json`, and the full
symlink-free supporting bundle without importing it. Inspection reports a
secret-free provenance summary plus the final bundle hash. The only approval
surface is the interactive user-terminal
`tcx connectors approve-provider <provider-id>` command; piped stdin, Codex
agent shell calls, MCP, API, and Admin cannot grant approval. The confirmation
binds the canonical workspace id and path hash, provider id, relative path, and
exact source and bundle SHA-256 values in the central database and required
audit log.

Approval copies those reviewed bytes into a digest-addressed, read-only
snapshot below the canonical TradingCodex home without executing them. After a
service restart, the provider loader rechecks both the mutable workspace bundle
and every immutable snapshot byte against the database binding, then imports
only the snapshot. A workspace move/copy, source or helper change, symlink,
path escape, snapshot mutation, revocation, or cross-workspace cache lookup
fails closed. A provenance or helper-file change changes the bundle hash and
invalidates approval. Revocation uses the same interactive-only boundary and
evicts the loaded workspace/provider cache. Any newly approved bundle requires
another service restart; connector rendering, registration, and validation
resume in a fresh Build turn before execution can be enabled.

Draft discard and broker cancellation are distinct operations.
`discard_draft_order` is portfolio-manager-only and applies only to local
`DRAFT` or `PRECHECKED` tickets with no broker order. `cancel_submitted_order`
requires a parser-issued native-user mandate, a known submitted broker order,
canonical approval, current policy/connection checks, idempotency and audit,
and explicit live confirmation when the provider is live. It does not discard
drafts.

## Money And Paper Portfolio Safety

Order notionals use a typed money contract: native currency and notional,
paper-account base currency/notional, FX rate, source snapshot id, and FX
as-of time. The base currency is a validated three-letter code and defaults to
the selected paper account scope. Values remain `Decimal` through order, policy,
serialization, and paper-portfolio paths. Cross-currency orders fail before
approval when the FX source snapshot is
missing, after the order time, stale, invalid, or currency-mismatched. Policy
limits compare the base notional, while the native amount remains visible.
The internal money ledger keeps six decimal places for every validated
three-letter code instead of guessing a currency's display minor units. Broker
adapters remain responsible for venue tick, lot, and settlement-precision
validation at their boundary. Ambiguous currency symbols and external accounts
without an explicit base currency fail closed.

Paper state is serialized per `(portfolio_id, account_id, strategy_id)` through
a versioned `PaperPortfolioState` row and compare-and-swap update. Cash is held
by currency, position currency must match the order currency, and state,
snapshot, positions, and cash child rows are written transactionally. A
concurrent conflicting update retries or fails explicitly instead of silently
overwriting cash or positions.

The v1 schema stores native and base money fields explicitly. Current order,
approval, and paper-state schemas are required exactly; missing currency, FX,
account scope, or money fields fail validation instead of being inferred.

## Required Blocks

TradingCodex must block:

- direct live broker requests outside `submit_approved_order` /
  `cancel_submitted_order`
- any claim that a user-installed capability was blocked, reviewed, licensed,
  audited, or made execution-safe by TradingCodex
- raw broker API variants such as `broker.raw_api`, `broker_api.*`, and generic live execution actions
- generic execution-like actions such as `execute_order`; final effects enter
  only through an exact immediate native action or a current hook-proven
  `OrderTurnGrant`, followed by the canonical service gateway
- self-issued approvals
- approval creation by roles other than `risk-manager`
- restricted symbol orders
- approval order-payload-hash mismatch after order mutation
- expired approval receipts or expired approval `valid_until`
- orders exceeding approval max notional, max price, order type, or time-in-force scope
- paper/test-sandbox/live provider orders without a valid order ticket plus matching approval receipt
- repeated connection submission for an already executed approved order
- duplicate order ticket ids with different payloads
- public MCP/API/CLI exposure for raw submission, cancellation, broker-status
  refresh, policy mutation, secret, or raw broker tools; the listed
  `use_order_turn_grant` tool must remain inert without current hook proof
- Any default Admin edit that would bypass service-layer policy for execution-sensitive state
- execution without the active `native-user` principal, narrow service
  capability, and either a valid parser-issued immediate mandate or consumed
  current-turn grant proof
- raw secrets in API, MCP, audit response, generated prompt, generated docs, or shell output
- inline or path-based approval receipts, payload principals that differ from
  the transport identity, and draft discard through an execution cancellation
  operation
- cross-currency order approval without a valid point-in-time FX conversion
- live execution when workspace config, policy, environment opt-in, enabled live adapter, signed health, trading-enabled connection, live scope, approval hash, explicit confirmation, idempotency, sync, or audit gates are missing

## User-Installed Codex Capabilities (BYOR)

Codex exposes user-installed standalone MCP servers and skills, plus installed
plugin skills, MCP servers, apps, and hooks, to the root agent and fixed
subagents through its native configuration inheritance. TradingCodex does not
classify data versus execution tools and applies no blanket tool-name block.
Codex's sandbox, approval mode, plugin policy, and organization requirements
remain authoritative.

These capabilities are bring-your-own-risk. Their licenses, data terms,
credentials, costs, availability, side effects, and outputs are the user's and
provider's responsibility. TradingCodex neither recommends nor verifies them,
and its audit and safety guarantees cover only TradingCodex-owned capabilities,
state, and service actions.

The read-only `list_codex_capabilities` tool and System page show only kind,
identifier, label, scope, origin, enabled/availability state, and plugin
ownership. Inventory collection uses local Codex list commands and installed
plugin manifests without refreshing a marketplace, using the network, reading
skill bodies or hook code, or executing plugin content. It never returns MCP
commands, arguments, URLs, environment values, headers, tokens, credential
paths, or raw configuration. Partial local metadata produces warnings instead
of failing the whole request.

User-installed capabilities cannot mint a TradingCodex principal, write the
reserved `tcx-*` namespace, create Build/Brain/Strategy/order grants, inject
protected proof, or bypass protected workspace and service-ledger boundaries.
TradingCodex prompts continue to route order approval and execution through the
canonical TradingCodex MCP and service path. An effect performed independently
by a user-installed capability is not a TradingCodex action and must not be
described as TradingCodex-blocked, TradingCodex-audited, or TradingCodex-safe.

## Broker Safety

Broker connections start disabled or read-only, except the built-in paper
adapter. Core ships only the paper provider by default. Broker-specific
providers are installed or developed on request, then registered by provider
metadata. A registered provider profile with an allowed execution posture
becomes execution-ready only after signed health verifies its credential
reference and the policy/config gates allow that posture. Broker records store
`credential_ref` only; raw credentials must not be stored in repo files,
workspace files, API responses, MCP responses, or audit payloads.

The v1 policy vocabulary is exact: `paper_only`, `broker_validation_only`, and
`live_broker`. Unknown or pre-v1 posture names make policy configuration invalid
instead of being translated. An unknown provider still fails safely as
`provider_development_required` and cannot enable execution.

`get_broker_connection_status` is a pure read. It may calculate and return
health but does not persist status, credential validation, drift, or trading
scopes. Execution enablement belongs to an explicit reviewed mutation, never a
GET or read-hinted MCP call.

Broker sync can discover accounts, cash, positions, orders, and fills through
the provider registry. It materializes central DB state through
`BrokerSyncRun`, `PortfolioLedgerEvent`, `PortfolioSnapshot`, and
`ReconciliationRun`. A reviewed validation provider can run broker-native
dry-run/order-test endpoints through the service-layer connection after order
ticket, approval, policy, duplicate-request, and audit checks. A reviewed live
provider can submit only when all live gates pass: `execution.live_enabled:
true`, policy allows the broker id and `live_broker`, environment variable
`TRADINGCODEX_ENABLE_LIVE_EXECUTION=1`, the live `AdapterDefinition` is enabled,
signed health is `ok`, the connection is `trading_enabled`, the exact order hash
has an approval receipt, and `submit_approved_order` includes
`LIVE:<ticket_id>:<broker_id>:<symbol>:<side>:<quantity>`.

## Mandate And Execution Guardrail

- Immediate final submission is available only when the complete trimmed root
  native user prompt is one of:

  ```text
  $tcx-order-submit --ticket-id <ticket-id> --approval-receipt-id <approval-receipt-id>
  $tcx-order-submit --ticket-id <ticket-id> --approval-receipt-id <approval-receipt-id> --live-confirmation <token>
  ```

  Immediate final cancellation likewise accepts only:

  ```text
  $tcx-order-cancel --ticket-id <ticket-id> --broker-order-id <broker-order-id> --approval-receipt-id <approval-receipt-id>
  $tcx-order-cancel --ticket-id <ticket-id> --broker-order-id <broker-order-id> --approval-receipt-id <approval-receipt-id> --live-confirmation <token>
  ```

- The deterministic parser accepts only literal `--name value` pairs. It rejects
  prose, aliases, quotes, escapes, comments, duplicate or unknown flags,
  `--name=value`, multiple actions, subagent turns, and the
  retired `$execute-paper-order` invocation. A malformed prompt beginning with a
  reserved token is blocked rather than falling through to analysis. One UTF-8
  BOM, normalized line endings, leading/trailing blank lines, and a Markdown
  link whose label and target match the projected action skill do not relax
  that action-only grammar.
- The two root skill bundles document this protocol but carry no tool authority.
  The hook creates the in-memory mandate from the original user prompt, records
  a redacted prompt hash and normalized identifiers, and calls the Django
  gateway synchronously before normal Head Manager orchestration.
- A workflow may instead put one exact invocation on its first meaningful line:

  ```text
  $tcx-order-allow --mode paper
  $tcx-order-allow --mode validation
  $tcx-order-allow --mode live
  ```

  The skill token may be its matching projected Markdown link. The remainder
  must contain a non-empty normal interactive or Scheduled Task prompt. The line
  admits at most one later submit or cancel and does not itself perform an
  effect. A free-form mention, later-line token, malformed mode, or subagent
  turn grants nothing. The browser viewer exposes no prompt entrypoint. Scheduled and interactive prompts use the
  same deterministic check; no Automation-origin signal is consulted.
- The turn grant is bound to workspace, session, turn, original complete prompt hash,
  and mode; it expires after one hour and is revoked by `Stop`, the next user
  turn, or consumption. `PreToolUse` injects its proof only into the protected
  `use_order_turn_grant` call. The proof is not model-visible and direct MCP
  input cannot supply it.
- Head Manager interprets the complete request and preserves explicit scope and
  prohibitions throughout the run. Hooks and Django services do not classify
  natural language with a keyword or negation router. The service
  deterministically enforces canonical policy, receipt, ticket, action, and
  mode state; it does not claim that free-form symbol, notional, schedule, or
  strategy scope was compiled into policy.
- Descriptive evidence never grants action authority merely because it mentions
  recommendation, approval, an order, or execution. If high-impact intent is
  materially ambiguous, Head Manager asks the user or stops at `waiting` or
  `blocked`.
- Order drafting and approval retain their role-owned structured tools. Final
  cancellation and execution additionally require either the exact immediate
  native grammar and parser-issued mandate or the exact first-meaningful-line turn grant
  plus protected proof, together with every deterministic principal,
  capability, policy, approval, idempotency, connection, and audit gate.
  Analytical prose cannot activate those paths.
- A user prohibition cannot remove a mandatory portfolio, risk, policy, or
  approval gate while retaining the downstream action.
- Guardrail-verification requests inspect blocked actions; they do not perform
  them.
- Secret handling remains outside investment dispatch. Use reviewed credential
  references and service-owned connection flows; never read, echo, or route raw
  keys, tokens, passwords, or `.env` contents through an analysis task.
- A ticker, universe name, or research phrase never maps to a default role team.
  Head Manager chooses the smallest useful role from the current questions and
  can return research-only, screen-grade, not-decision-ready, or blocked when
  available capabilities or evidence do not support a stronger result.

## Secret Wall

Raw broker API keys, tokens, account credentials, and secrets must not appear in:

- generated workspace files
- `.codex/` or `.agents/` prompts
- shell output
- product web output
- Admin list displays or exported rows
- API responses
- MCP responses
- audit event payloads
- starter prompts
- generated research artifacts
- workspace viewer responses

Adapters that need secrets must use external environment-backed credential
references and expose only redacted references through TradingCodex.

## HTTP Runtime Boundary

The default `local` service profile is loopback-only. Anonymous loopback
requests may read product, viewer, and health state. Viewer routes are GET-only.
Every API/web mutation requires its existing authenticated principal or staff
session; no browser-local analysis mutation exception exists.

Non-loopback binding is fail-closed and requires the explicit `remote` profile,
`DEBUG=False`, a non-default Django secret, configured API mutation credentials,
non-wildcard allowed hosts, matching HTTPS CSRF origins, and TLS termination by
a trusted reverse proxy. The proxy must strip untrusted forwarded-protocol
headers before setting `X-Forwarded-Proto: https`; the backend must not be
directly exposed. Raw Django/API credentials remain subject to the Secret Wall
and must not enter repository, workspace, prompt, audit, API, MCP, or log data.
Cookie-authenticated staff mutations are CSRF-protected in both local and remote
profiles. Header API keys do not require CSRF, but their bound principal must be
active and pass the same role and capability checks as the corresponding MCP
tool before a role-authored mutation reaches its service handler.

## Policy Inputs

Policy decisions can depend on:

- principal
- role
- capability
- requested action
- symbol/instrument
- universe
- exact broker `provider_id` and transport
- restricted list
- portfolio limits
- order schema validity
- approval receipt validity
- idempotency state
- current live-execution posture
- workspace provenance

The service loads workspace execution policy only from the exact v1
`execution` mapping in `.tradingcodex/config.yaml`. Unknown or missing fields,
wrong types, unsupported execution postures, a missing config, or a missing or
malformed restricted-list file fail closed. Principal/capability truth remains
in the service registries and central DB; generated policy-shaped mirrors are
not runtime inputs.

Policy output should record the decision, reason codes, material inputs, and
audit reference.

## Admin Risky Changes

Risky Admin changes use:

```text
proposal -> validation -> approval -> apply -> audit
```

Examples:

- enabling or disabling MCP tools
- projecting workspace skill proposal files
- changing principals or capabilities
- toggling restricted symbols
- disabling adapters
- changing universe routing or supported-instrument policy
- applying policy changes

Admin is an operations console, not a bypass.

## Default And Live-Gated Execution

Paper and reviewed validation execution remain local harness flows.
Live execution is not enabled by bootstrap, workspace generation, connector
scaffold, or connector registration alone. It is available only through an
installed and reviewed provider plus the explicit live gates above.

Every execution still requires:

- structured order ticket
- service-layer validation
- valid approval receipt
- parser-issued immediate native mandate or consumed turn-grant proof, plus
  `native-user` capability checks
- idempotency check
- adapter availability check
- audit event

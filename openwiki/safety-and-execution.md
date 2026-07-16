# Safety And Execution

Use this page before changing policy, permissions, approvals, broker connectors, order tickets, execution, viewer read/auth boundaries, BYOR Codex capabilities, secret handling, or audit. Human-facing rules live in [docs/safety-policy-and-execution.md](../docs/safety-policy-and-execution.md) and [docs/guardrails.md](../docs/guardrails.md).

## Approved Action Boundary

Every final submit or cancel follows one of two entries:

```text
exact immediate root action -> deterministic hook parse -> native-user permission
  -> policy -> payload validation -> canonical approval
  -> idempotency/effect reservation -> mandatory intent audit
  -> connection -> mandatory finalized/uncertain audit

exact first-meaningful-line $tcx-order-allow -> workspace/session/turn/prompt/mode grant
  -> protected use_order_turn_grant + PreToolUse proof -> consume once
  -> the same canonical policy/approval/idempotency/live/audit gates
```

Policy and approval are revalidated immediately before connection use.
Submission and live cancellation reserve the external effect before provider
invocation. A provider exception or local-finalization failure moves the ticket
to `NEEDS_REVIEW` with correlation metadata and blocks blind retry. Broker/API/MCP
connection invocation is owned by the Django service layer. After admission,
the provider effect runs through deterministic service code; no execution
subagent or execution model sits on that effect path.

Provider health strings/details, exception messages, and account display
metadata are untrusted. The service retains only fixed health codes, validated
exception classes, allowlisted account types, service-derived display fields,
and the required canonical broker account identifier; provider account metadata
is discarded.

## Native Action Gateway

Only a complete root native user prompt matching an exact
`$tcx-order-submit` or `$tcx-order-cancel` `--name value` grammar
can request an immediate final effect. The root skill bundles are explicit-only and
carry no tools. `UserPromptSubmit` intercepts the literal reserved token before
analysis allocation, creates a prompt-hash- and workspace-bound `native-user`
mandate, writes redacted audit metadata, and calls
`tradingcodex_service/application/execution_gateway.py` in-process.

A workflow that creates or selects identifiers later in the turn uses a
separate exact first meaningful line: `$tcx-order-allow --mode paper`,
`validation`, or `live`. The skill token may be its matching projected link,
but the mode grammar stays literal. `UserPromptSubmit` requires the current root `session_id` and
`turn_id`, issues one `OrderTurnGrant` bound to workspace, session, turn,
original complete prompt hash, Codex permission mode, and execution mode, and continues
normal orchestration. Plan mode rejects immediate effects plus grant issuance
and use. The grant expires after one hour and is revoked by one submit or
cancel, `Stop`, or the next user turn. Root Head Manager alone may call
`use_order_turn_grant`; `PreToolUse`
reserves the grant for the tool-use id and injects internal proof into rewritten
MCP input. The proof is not model-visible and cannot be supplied directly.

If proof has been consumed while the canonical result remains `authorizing`,
Stop/new-turn cleanup never resets or retries the effect. The session blocks a
new managed workspace or order-sensitive prompt until terminal, while ordinary research
may continue and inspect canonical status.

Codex app Scheduled Tasks submit their saved prompt on each scheduled turn.
TradingCodex does not detect an Automation origin: a scheduled prompt and an
interactive root prompt pass through the same parser. `tcx-automate`
authors research, monitoring, analysis, portfolio/status, draft, assisted, and
optional execution tasks; only the last category includes the canonical plain
first-meaningful-line invocation,
and the saved runtime prompt never invokes `tcx-automate` recursively.

The in-memory mandate signature is field-integrity defense, not same-user OS
attestation. Native project config explicitly enables hooks, disables unified
execution and interactive action features, and defaults to the custom
`trading-research` profile. General shell/Python and credential-free public
HTTP plus user-owned file changes outside `trading/` are available, while the
profile keeps `trading/` read-only and denies generated control files, the
TradingCodex home/DB/runtime, protected workspace state, credentials,
local/private destinations, and Unix sockets. It extends the built-in native
`:workspace` profile, applies more-specific read/deny
overrides, denies the broad temp roots, reopens an exact scratch child as the
shell temp directory, and enables Codex's network proxy for
the public-only command network policy. Only the installed standalone Codex
runtime is reopened read-only beneath the otherwise denied Codex home so native
file tools work without exposing auth/config/session state. The shell environment excludes
secret, token, and broker variables. The hook matches legacy `Bash` and current
`exec_command`/`write_stdin` events and retains semantic blocks for interactive
sessions, credentials, state mutation, publication, and order effects. This
OS-level authority separation, rather than an interpreter ban, prevents
model-launched Python from reading or bypassing a mandate.

Codex loads the project config, TradingCodex MCP server, and hooks only after
the attached workspace is trusted. If it is untrusted or managed policy forces
hooks off, native execution is unavailable and must not fall back to shell,
public MCP, REST, generic CLI, or a model-selected path.

Malformed reserved prompts, subagent turns, and retired
`$execute-paper-order` invocations fail closed. Public REST and
generic CLI expose no submit, cancel, or status-refresh mutation. Fixed roles
have no execution tool. Root Head Manager lists only the protected grant
consumer, which direct MCP callers cannot use without current hook proof.
Read-only order/execution status remains available.

The exact first meaningful line and requested mode provide deterministic admission, not
deterministic interpretation of the remaining prose. The service enforces
canonical policy, ticket, receipt, action, broker posture, and mode. It does
not claim to enforce a natural-language symbol, notional, schedule, or strategy
limit unless canonical state represents it.

## Order And Execution Rules

`OrderTicket` is the canonical workflow root for draft, check, approval,
submission, cancellation, refresh, and inspection. Public preparation and read
surfaces address central DB tickets; final mutations stay internal behind the
native action gateway.

Supported lifecycle:

```text
DRAFT -> PRECHECKED -> READY_FOR_APPROVAL -> APPROVED -> RESERVED
  -> SUBMITTED -> ACKED -> PARTIALLY_FILLED -> FILLED
```

Terminal or review states are `REJECTED`, `CANCELED`, `EXPIRED`, `FAILED`, and `NEEDS_REVIEW`.

Approved execution is idempotent by `portfolio_id`, `account_id`, and
`strategy_id`. Native/base currency, point-in-time FX snapshot identity, and
versioned portfolio compare-and-swap state are part of the money contract. The
internal paper account scope selects a validated three-letter base currency;
policy compares
only converted base notional and requires FX evidence for any different native
currency. Internal service money uses fixed six-decimal precision and requires
explicit currency codes at ambiguous natural-language or external-connector
boundaries. The v1 order, approval, and paper-state schemas require their
current money fields exactly and never infer an absent currency or account
scope.
Duplicate approved-order submission or uncertain cancellation retry must fail
before connection use.

## Required Blocks

TradingCodex must block direct live broker requests, raw external
broker/execution/secret/policy proxies, self-issued approvals, non-risk-role
approvals, restricted-symbol orders, approval hash mismatches, expired
approvals, over-scope submissions, duplicate submissions, duplicate order ids
with different payloads, raw public or fixed-role submit/cancel/refresh
mutations, direct protected-tool calls without hook proof, raw secrets in
outputs, malformed or non-root native mandates/grants, and live execution when
any live gate is missing.

The workspace viewer has no prompt or mutation input and therefore cannot carry
reserved native execution tokens or start Codex.

## Broker And BYOR Capability Posture

Core ships paper by default. Broker connections start disabled or read-only except the built-in paper adapter. Provider adapters become execution-ready only after provider metadata, signed health, policy/config gates, approval hash, idempotency, explicit confirmation, sync, and audit gates pass. Workspace `provider.py` bundles remain inert until an exact workspace/path/source hash is approved from an interactive operator terminal; approval copies a symlink-free immutable snapshot below TradingCodex home without executing it, runtime loads only that rehashed snapshot after restart, and MCP/API/Admin expose no approval mutation. The CLI mints a process-local, single-use service capability only after its TTY and exact confirmation checks, bound to the workspace, provider, and reviewed bundle.

Missing providers are implemented before connector rendering unless the user
explicitly asks for scaffold-only output. Externally informed bundles include
required `source-provenance.json`; only legacy or wholly manual providers that
use no externally fetched source may omit it. Provenance, provider helpers, and
`provider.py` all participate in the approval hash. VCS metadata,
secrets/key/`.env` material, and symlinks fail validation. Inspection exposes
only a secret-free provenance summary and the bundle hash. Approval never
imports code; runtime import waits for the immutable post-restart snapshot,
then connector render/register/validate resumes in a fresh Build turn.

User-installed MCP servers, skills, plugins, apps, and hooks remain BYOR native
Codex capabilities. Codex exposes them to root and fixed-role agents under its
own sandbox, approval, plugin, and organization policies. TradingCodex does not
install, classify, approve, proxy, recommend, or audit them. Licenses, data
terms, cost, credentials, and side effects remain the user/provider's
responsibility. The sanitized inventory is read-only and never returns launch
details, secrets, raw config, skill bodies, or hook code.

External capabilities cannot mint TradingCodex principals or grants, write the
reserved namespace, inject service proof, or bypass protected workspace and
ledger state. Order and execution guarantees apply only to effects that enter
the canonical TradingCodex service path.

## Secret Wall

Raw broker API keys, tokens, account credentials, and secrets must not appear in repository files, generated workspace files, prompts, shell output, product web, Admin exports, API responses, MCP responses, audit payloads, starter prompts, generated docs, or research artifacts.

## Workspace Viewer Boundary

The product web is GET-only and read-only. It selects a registered attached
workspace, displays sanitized workspace, skill, artifact, and system state, and
does not start a Codex process or mutate service/workspace state. There is no
loopback mutation exception. Native Codex, MCP identity checks, policy,
approval, idempotency, broker, and audit remain the only action path.
Artifact writes are authenticated-role-bound, stage gates are ordered, research
roots reject symlinks, and terminal state must match append-only event replay.
Workspace authorization is a DB-canonical, capability-scoped current-turn
intent grant. A root native prompt puts matching `$tcx-build`, `$tcx-brain`, or
`$tcx-strategy` on its first meaningful line; the plain token or matching
projected link is accepted and Build/Brain/Strategy requests may share the line
or follow it. The hook binds the grant to
workspace/session/turn/cwd/prompt/scope and supplies one-time proof
to protected DB-backed Build calls and scoped Brain/Strategy lifecycle MCP
calls. Connector scaffold rendering is
read-only and content-addressed; it returns target content/hash and only
preimage existence/hash/size metadata, while actual workspace edits use native
`apply_patch`. Agent MCP exposes no
connector `connect` or write-style `scaffold` tool. It never elevates the actual
Codex sandbox. Codex Plan mode cannot issue or use the grant, and its
issue-time permission mode must still match when a tool is used. The browser
viewer cannot request it, subagents cannot inherit it, and every mutating follow-up or
Automation run needs a fresh marker. Never combine Build, Brain, Strategy, or
order markers. Persistent `tcx mode` is retired, old `mode.json` state is
ignored, and direct operator CLI mutation remains a separate authority. User
capability lifecycle belongs to Codex and is outside the TradingCodex Build
grant. Normal Build edits require a fresh root turn in
`trading-build`. That profile uses `apply_patch` for edits and limits shell to
public GET/HEAD, enumerated read-only HTTPS Git, limited workspace
`pwd`/`cat`/`ls`, inert provider reads/hash/diff/Git inspection, exact isolated
`py_compile`, and allowlisted workspace-launcher commands. General
interpreters, helper scripts, test runners, build systems, shell composition,
and model-authored POST are blocked, while credential-free public
HTTP(S)/HTTPS Git retrieval remains available. Native browser/web/network
tools are blocked during Build so retrieval cannot reuse browser credentials;
Research browser behavior is unchanged. The profile also denies the
TradingCodex home/DB/mutable runtime state, credentials, audit, approval,
order state, authenticated/local-private network access, package installation,
publication, and broker calls. Public provider source remains inert under
`$TRADINGCODEX_SCRATCH/provider-sources/<provider-id>/`.
The Build proxy uses full HTTP transport only for Git Smart HTTP's read-only
protocol POST; the hook still admits only public GET/HEAD and enumerated HTTPS
Git retrieval commands.
The exact generated launcher runtime is read-only only for hook-admitted
`./tcx` validation and inspection; direct runtime and general interpreter
commands remain blocked.
Broader unit, smoke, and build validation belongs to an explicit user/operator
or maintainer terminal flow.
Plan mode blocks all managed workspace grants. Trusted Build-only
workspace-launcher commands and protected MCP
calls retain their exact grant/proof gates. Hook/runtime state,
credential paths, and the managed `.gitignore` remain protected from direct
Build edits. For recurring Build
Automation, use an isolated worktree or workspace and retain a reviewable diff.
Brain and Strategy management remain in `trading-research`; their direct exact
markers permit only the matching native source/staging path plus
`manage_investment_brain` or `manage_strategy`. Research keeps the lifecycle
launcher and attached runtime denied. These scopes cannot authorize Build,
each other, credentials, global config, publication, or orders. Plan
mode and subagents cannot issue or use these grants.
Unstarted protected-call reservations expire after
two minutes, while a service-started call completes before deferred Stop or
new-turn revocation becomes terminal. Protected MCP proof enforcement is
unconditional for every transport and direct caller. If post-effect
finalization fails, idempotent recovery never reruns the operation and instead
marks the use `finished_unfinalized`, clears the reservation, and revokes the
grant fail-closed.

Workspace execution policy has one file input: the exact v1 `execution`
mapping in `.tradingcodex/config.yaml`. The service rejects missing, unknown,
wrongly typed, or unsupported fields. The restricted-list YAML augments the
central DB list; role, principal, capability, and information-barrier authority
comes from service registries, MCP allowlists, hooks, and role TOML rather than
duplicate policy YAML.

## Edit Checklist

When changing this area, inspect:

- `docs/safety-policy-and-execution.md`
- `docs/guardrails.md`
- `tradingcodex_service/application/policy.py`
- `tradingcodex_service/application/orders.py`
- `tradingcodex_service/application/execution_gateway.py`
- `tradingcodex_service/application/skill_invocations.py`
- `tradingcodex_service/application/build_gateway.py`
- `tradingcodex_service/application/brokers.py`
- `tradingcodex_service/mcp_runtime.py`
- order/policy/broker/API/CLI tests
- viewer read-auth, registered-workspace, sanitization, and no-mutation tests
- generated role allowlists and prompts for authority drift

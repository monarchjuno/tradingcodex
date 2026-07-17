# Execution Without An Execution Subagent

Status: immediate native actions verified on 2026-07-13; Codex app Scheduled
Task turn grants implemented on 2026-07-14. This page records the accepted
design, migration, and validation evidence.

> Historical note: references below to a Workbench runner describe the retired
> pre-v1 design. The current product web is a read-only workspace viewer and has
> no preview, start, follow-up, or Codex-process path.

## Implementation Checklist

- [x] Add the exact parser, immutable workspace-bound mandate, `native-user`
  authorization, redacted projection, and direct service gateway.
- [x] Intercept reserved actions in root-native `UserPromptSubmit` before
  analysis-run allocation; reject malformed, subagent, Workbench, and retired
  forms.
- [x] Require a parser-issued mandate in the canonical submit/cancel services
  while preserving policy, approval, live, idempotency, adapter, audit,
  reconciliation, and `NEEDS_REVIEW` behavior.
- [x] Remove `execution-operator`, its TOML/model projection, and the retired
  `execute-paper-order` role skill.
- [x] Add explicit-only root `tcx-order-submit` and
  `tcx-order-cancel` skill bundles with no tool authority.
- [x] Add explicit-only root `tcx-order-allow` and extend `tcx-automate` to
  author every class of Codex app Scheduled Task, from simple research and
  monitoring through optional turn-authorized execution.
- [x] Issue a workspace-, session-, turn-, prompt-, and mode-bound single-use
  grant only for an exact first meaningful line `$tcx-order-allow --mode
  paper|validation|live`; revoke it after one submit or cancel, after one hour,
  on `Stop`, or when the next user turn starts.
- [x] Protect `use_order_turn_grant` so only root Head Manager can select the
  canonical ticket/action fields and `PreToolUse` injects the internal proof;
  callers cannot supply or replay that proof directly.
- [x] Treat a consumed `authorizing` grant as an in-flight canonical effect:
  Stop/new-turn cleanup never resets it, and the same session blocks new Build
  or order-sensitive prompts until terminal while ordinary research may
  continue.
- [x] Remove submit, cancel, and broker-status-refresh mutation exposure from
  public MCP, REST, generated role configs, and generic CLI guidance.
- [x] Keep Workbench preview, start, and follow-up analysis-only and reject the
  reserved native action tokens before launch.
- [x] Close model-code bypasses by disabling unified/interactive action
  features and applying one fail-closed managed-read shell allowlist to legacy
  and current Codex tool names in pre-use and permission hooks.
- [x] Canonicalize provider results, health, account display metadata, and
  errors before persistence or public projection, and make local cancellation
  intent/state/final audit atomic.
- [x] Update durable product docs and the OpenWiki agent map for the nine-role
  roster and native execution boundary, including the breaking-change entry in
  `CHANGELOG.md`.
- [x] Complete the full Python suite, Django/compile checks, disposable
  generated-workspace smoke, real Codex-native hook smoke, wheel/platform smoke,
  and remaining evidence required by the acceptance criteria.

## Validation Evidence

- The full Python suite, focused native-gateway/platform tests, Django system
  check, compileall, skill validation, and diff hygiene checks pass.
- Provider canary regressions prove that submit/cancel results, health details,
  account-sync exceptions, and successful account display metadata do not
  reappear in DB rows, API/MCP projections, execution results, or audit events.
- A disposable generated workspace reports nine fixed roles and 33 skills,
  contains no retired execution role, and exposes no ungated submit, cancel, or
  broker status-refresh mutation. Its one execution-risk MCP tool is
  `use_order_turn_grant`, which has no authority without hook-injected proof.
- A trusted-workspace Codex CLI smoke loads the project hooks and MCP, blocks a
  real canonical `Bash` command outside the managed-read allowlist, begins one
  analysis run, dispatches `fundamental-analyst` with `fork_turns="none"`, and
  receives `ROLE_READY` from the read-only Terra child without synthesis.
- A real exact root-native paper action submits an approved order directly
  through `UserPromptSubmit`, records mandate/result audit events, reaches
  canonical `FILLED` state, and creates no analysis run or execution subagent.
- A clean-source wheel contains no `execution-operator` or
  `execute-paper-order` path, passes `twine check`, and passes the macOS
  wheel/platform smoke including generated launchers, hooks, MCP, service, and
  packaged SPA assets.

## Summary

Remove `execution-operator` from the fixed-role roster. Native Codex keeps the
exact immediate submit/cancel path for already-known canonical identifiers and
adds a separate single-turn path for workflows that create the ticket or
approval during the turn. The immediate path executes in `UserPromptSubmit`
before Head Manager runs. The turn path begins only when the first meaningful
line is exactly `$tcx-order-allow --mode paper|validation|live`, then continues
the normal workflow with a single-use service grant.

`tcx-automate` authors Codex app Scheduled Tasks; it is not a scheduler or
the skill invoked recursively by each run. Research, monitoring, recurring
analysis, portfolio/status review, draft-order, and assisted-execution tasks
need no `$tcx-order-allow`. Codex injects the saved task prompt on each scheduled
turn, and TradingCodex processes that prompt exactly like an interactive root
turn. It does not attempt to detect an Automation origin.

The change preserves the existing order ticket, risk approval, policy,
restricted-list, money, adapter, live-confirmation, idempotency, mandatory
audit, broker-result, reconciliation, and `NEEDS_REVIEW` invariants. It changes
only who requests the final action and how explicit user intent reaches the
service.

The target flow is:

```text
portfolio-manager -> OrderTicket and checks
risk-manager      -> ApprovalReceipt
native user       -> exact execution or cancellation skill invocation
UserPromptSubmit  -> deterministic parse and native-user mandate
Django service    -> authorize, reserve, audit, invoke adapter, finalize
Head Manager      -> summarize the already-recorded result; no action authority

native/scheduled user -> exact first-meaningful-line $tcx-order-allow mode
UserPromptSubmit      -> bind one grant to workspace + session + turn + prompt + mode
roles/services        -> create and approve canonical ticket state
Head Manager          -> call protected use_order_turn_grant once
PreToolUse             -> reserve grant and inject internal proof
Django service        -> consume proof, revalidate canonical gates, perform one effect
```

## Product Decisions

- Remove `execution-operator`; the fixed roster becomes Head Manager plus nine
  fixed subagents.
- Support execution invocations in native Codex workspace sessions only.
  Workbench remains analysis-only.
- Preserve submit and cancel behavior for paper, broker-validation, and reviewed
  live providers.
- Do not give raw submit or cancel tools to Head Manager, `risk-manager`, or any
  subagent. Head Manager alone may see `use_order_turn_grant`; it is unusable
  without the current hook-injected proof and permits only one submit or cancel.
- Do not create an `ExecutionMandate` database model. The hook performs the
  action immediately and passes an immutable in-memory mandate to the service;
  its normalized fields and prompt hash are persisted in existing execution
  and audit records.
- Retire direct execution mutation through public MCP, REST, and generic CLI
  surfaces. Read-only order and execution status interfaces remain available.
- Keep `risk-manager` as the only approval issuer. User execution consent does
  not create or replace an `ApprovalReceipt`.
- Treat `$tcx-order-allow` as boolean turn admission plus a mode ceiling, not a
  deterministic parser for the natural-language scope below it. The service
  enforces canonical ticket, receipt, action, broker posture, and mode; it does
  not claim that arbitrary prose scope was compiled into executable policy.

## Exact Native Invocation Contract

Add two root skill bundles:

- `$tcx-order-submit`
- `$tcx-order-cancel`

Each bundle follows the normal TradingCodex skill shape, including `SKILL.md`
and `agents/openai.yaml`, but contains no tool authority. The skill documents an
action syntax intercepted by the hook before the model runs.

The complete meaningful user prompt must have one of these canonical plain
forms:

```text
$tcx-order-submit --ticket-id <ticket-id> --approval-receipt-id <receipt-id>
$tcx-order-submit --ticket-id <ticket-id> --approval-receipt-id <receipt-id> --live-confirmation <token>

$tcx-order-cancel --ticket-id <ticket-id> --broker-order-id <broker-order-id> --approval-receipt-id <receipt-id>
$tcx-order-cancel --ticket-id <ticket-id> --broker-order-id <broker-order-id> --approval-receipt-id <receipt-id> --live-confirmation <token>
```

Parsing rules are intentionally strict. One UTF-8 BOM, normalized CRLF/CR/NEL/
Unicode line separators, leading/trailing blank lines, and a Markdown link whose
label and target match the projected workspace action skill are presentation
variants only:

- Parse with `shlex.split`; never use a shell or evaluate prompt content.
- Require the skill invocation to be first and the entire meaningful prompt to be the
  action. Free-form prefixes, suffixes, comments, quotations, or a second skill
  invocation are invalid.
- Accept only the documented `--name value` form. Reject `--name=value`, unknown
  flags, positional values, duplicate flags, missing values, and duplicate
  action invocations.
- Require `ticket-id` and `approval-receipt-id` for submit. Also require
  `broker-order-id` for cancel.
- Apply the existing identifier length limits. Reject control characters,
  leading/trailing whitespace inside values, and values that cannot be safely
  represented in audit output.
- Treat `live-confirmation` as an opaque string. The existing order service must
  continue to compare it with the exact live submit or cancel confirmation
  derived from canonical DB state.
- Do not accept aliases for the retired `$execute-paper-order` skill. A stale
  invocation fails with migration guidance and performs no action.
- Ignore apparent action tokens in subagent turns. Only a root native user turn
  may create a native execution mandate.

Any prompt that does not exactly match this grammar follows the ordinary
analysis path and has no execution authority. A malformed prompt that starts
with either reserved action token is blocked with a safe validation error; it
must not fall through to Head Manager as ordinary analysis.

## Turn-Scoped Order Allow Contract

For a workflow that may create its final identifiers during the turn, the first
meaningful line must be exactly one of:

```text
$tcx-order-allow --mode paper
$tcx-order-allow --mode validation
$tcx-order-allow --mode live
```

The skill token may be its matching projected Markdown link. The rest of the
prompt is a non-empty ordinary interactive or Codex app Scheduled Task request.
The line accepts no aliases, comments, quotes, extra flags, embedded prose, or
`--mode=value` form. A free-form mention or later-meaningful-line occurrence
does not grant authority. Scheduled and interactive turns use the same parser;
there is no Automation-origin branch.

The accepted line issues one grant bound to the attached workspace, Codex
`session_id`, `turn_id`, complete prompt hash, and requested mode. It authorizes
at most one `submit` or `cancel` selection and expires after one hour. It is
also revoked by `Stop`, consumption, or the next user turn. The model never
receives the proof. On the one protected `use_order_turn_grant` call,
`PreToolUse` reserves the grant against the tool-use id and injects the proof
into rewritten MCP input. The service consumes it and then runs the unchanged
policy, ticket, receipt, approval, idempotency, live-confirmation, connection,
audit, and uncertainty gates.

The grant does not approve an order, turn prose into policy, or prove that a
symbol, notional, or other natural-language restriction was understood. Those
constraints are enforceable only when represented in canonical ticket,
approval receipt, execution mode, and policy state. A missing or uncertain
canonical constraint must stop the effect.

## Hook And Service Design

### Shared parser and mandate

Create a canonical execution gateway in the Django application layer. It owns:

- exact invocation parsing;
- a frozen `NativeExecutionMandate` value containing action, normalized ids,
  optional live confirmation, source `native_codex_user_prompt`, prompt SHA-256,
  invocation timestamp, and workspace provenance;
- safe public result projection for hook output; and
- dispatch to the existing submit or cancel service function.

The parser is shared by the native hook and Workbench validation so the two
surfaces cannot disagree. The mandate factory accepts the original user prompt,
not model-generated text or injected hook context. Raw prompt text is never
stored in the DB, audit ledger, or returned result.

### Native `UserPromptSubmit`

Handle reserved execution tokens before allocating an analysis run or emitting
normal analysis context:

1. Reject the invocation when `TRADINGCODEX_WORKBENCH_RUN=1` or when the hook
   payload identifies a subagent.
2. Parse the exact prompt into `NativeExecutionMandate`.
3. Append a redacted hook audit containing action, prompt hash, workspace, and
   canonical ids. If this audit is unavailable, fail closed before service
   invocation.
4. Call the execution gateway synchronously and in-process. Do not launch a
   shell, subprocess, user-installed capability, or Head Manager tool call.
5. Emit only an allowlisted result projection as `UserPromptSubmit` additional
   context with marker `tradingcodex-native-execution-result`.
6. Instruct Head Manager to report the recorded result without spawning roles,
   beginning an analysis run, retrying the action, or calling another mutation.
7. Audit the normalized accepted, rejected, duplicate, or `needs_review`
   outcome. A hook-output failure after provider invocation must not trigger a
   retry; canonical status is recovered from the DB.

The existing provider-call idempotency remains the replay defense if the hook
event is delivered more than once.

For `$tcx-order-allow`, `UserPromptSubmit` instead validates the exact first meaningful line,
requires root native `session_id` and `turn_id`, revokes the session's older
grant, issues the new bounded grant, and continues normal analysis allocation.
The additional context contains only mode, expiry, single-use posture, and the
protected tool name. It contains no proof. `PreToolUse` later matches only
`use_order_turn_grant`, rejects subagents and caller-supplied proof fields,
reserves the current grant, and rewrites the MCP input with internal proof.

### Native user principal

Replace the agent principal with an explicit service principal:

- principal id: `native-user`;
- role: `user`;
- allowed actions: native mandated submit and native mandated cancel only;
- no policy-write, approval-create, broker-raw, secret, cash-transfer, or
  general MCP capability.

Register these as service capabilities rather than deriving them from an MCP
tool allowlist. Policy records and execution audit events use `native-user` as
`requested_by`; approval records continue to identify `risk-manager`; provider
effects are recorded as service-owned execution. The service rejects a submit
or cancel request that does not carry a valid `NativeExecutionMandate` produced
by the exact parser.

### Workbench boundary

Workbench preview, run start, and follow-up validation must detect all three
reserved native execution tokens with the shared parser and reject them before
starting Codex. The skills are not startable from Workbench and are labeled
native-only wherever skill metadata is shown. Workbench continues to expose no
order, approval, submit, cancel, broker-mutation, policy-mutation, or secret
authority.

## Execution Kernel That Remains Unchanged

The gateway must enter the existing canonical service path. It must not copy or
replace these checks:

- central-DB `OrderTicket` lookup and canonical payload resolution;
- `APPROVED` state requirement;
- DB-canonical `ApprovalReceipt` lookup by id;
- receipt validity, expiry, creator/approver separation, exact order hash,
  broker/account, notional, price, order type, and time-in-force scope;
- active principal and narrow service capability;
- exact v1 workspace policy, restricted list, money/FX contract, adapter, and
  execution-posture checks;
- live environment opt-in, enabled live adapter, health, trading-enabled
  connection, required trade scope, and exact live confirmation;
- transactionally locked execution reservation, receipt consumption,
  idempotency key, state transition, order event, and mandatory pre-provider
  audit;
- canonical provider result validation, broker order/fill/portfolio updates,
  and mandatory final audit;
- `NEEDS_REVIEW` plus blind-retry blocking after uncertain provider invocation
  or local finalization failure; and
- separate semantics for draft discard and submitted broker cancellation.

Refactor duplicated principal checks around this kernel into one
`authorize_native_execution` entrypoint, but do not weaken or reorder the
external-effect invariants.

## Retired Surfaces And Generated Workspace Migration

Remove all projections and registry entries owned only by the agent:

- the `execution-operator` `AgentSpec`, role descriptions, handoff contract,
  model policy, TOML template, optional-skill target, and generated index row;
- the role-owned `execute-paper-order` skill and its projection;
- Head Manager dispatch guidance that treats execution as a fixed-role task;
  and
- doctor checks that expect an execution role or its MCP allowlist.

Replace them with checks that prove:

- the fixed roster has nine subagents;
- no generated root or role MCP config exposes submit or cancel mutations;
- `use_order_turn_grant` is projected only to Head Manager and cannot act
  without a current hook proof;
- all three native execution skills and their exact parsers are installed;
- Workbench rejects immediate actions and `$tcx-order-allow`; and
- the retired role and skill files are absent.

Remove `submit_approved_order` and `cancel_submitted_order` from public MCP tool
definitions and generated MCP allowlists. Remove the corresponding REST
mutation routes and generic `tcx mcp call` help/examples; do not add aliases or
compatibility shims. Keep internal application service functions and read-only
ticket, broker-order, and execution-status interfaces.

`tcx update` already removes retired, unmodified managed files recorded in the
previous module lock. Preserve that behavior for the retired role TOML and
skill directory. If a retired generated file was locally modified, update must
continue to fail closed and instruct the user to resolve or remove it manually.

No database migration is required. Existing `OrderTicket`, `ApprovalReceipt`,
`ExecutionResult`, broker order, fill, order event, policy decision, and audit
rows remain canonical and compatible. Add mandate metadata only inside existing
JSON payloads and audit envelopes.

## Documentation And Release Changes

Update the durable product contract in the same implementation change:

- describe nine fixed subagents and remove execution-role routing;
- define the two immediate native actions plus the explicit-only
  `tcx-order-allow` turn-admission skill;
- define `tcx-automate` as Codex app Scheduled Task authoring for research,
  monitoring, analysis, portfolio/status, draft, assisted, and optional
  turn-authorized execution tasks;
- state that executable effects are user-mandated and service-owned rather than
  performed by an agent;
- document removal of direct MCP/API/CLI execution mutation surfaces;
- preserve the existing approval, adapter, live, idempotency, audit, and
  uncertainty rules; and
- add the breaking generated-workspace migration to the changelog/release
  notes.

Update OpenWiki only where the role roster, interface map, generated workspace
map, or safety routing would otherwise become misleading.

## Test Plan

### Parser and hook tests

- Accept each exact submit and cancel form, including the live-confirmation
  variant.
- Reject missing, duplicate, unknown, positional, `--name=value`, malformed,
  control-character, mixed-prose, quoted-example, multiple-action, and retired
  skill forms.
- Prove that a root native prompt creates one mandate and does not begin an
  analysis run or dispatch a role.
- Prove that subagent prompts and Workbench preview/start/follow-up cannot
  create a mandate or invoke the service.
- Prove that unavailable hook audit blocks before reservation/provider use.
- Prove that hook result projection contains no raw prompt, reasoning, secrets,
  credentials, raw provider payload, or unallowlisted error text.
- Accept only the three exact first-meaningful-line `$tcx-order-allow` modes;
  reject later-meaningful-line, malformed, subagent, Workbench,
  missing-session, and missing-turn
  forms without issuing a grant.
- Prove grant binding to workspace, session, turn, prompt hash, mode, and
  tool-use id; prove one-hour, `Stop`, next-turn, and consumption revocation.
- Prove `PreToolUse` injects proof only into `use_order_turn_grant`, rejects a
  model-supplied proof, and never exposes proof in context, audit, or artifacts.

### Authorization and execution tests

- `native-user` can request only mandated submit/cancel and cannot approve,
  write policy, access secrets, use raw broker actions, or call generic MCP
  mutations.
- No fixed role or direct MCP/API/CLI caller can submit or cancel. Head Manager
  can use only the protected grant tool during the exact authorized turn.
- A valid paper invocation submits once; a replay never calls the adapter
  again.
- Approval mismatch, expiry, supersession, consumption, ticket mutation,
  restricted symbol, notional/FX failure, invalid state, disabled adapter, and
  failed health all stop before provider use.
- Validation and live providers retain their existing gates and exact
  confirmation behavior.
- Cancel retains known-broker-order, state, approval, scope, idempotency, audit,
  and uncertain-result behavior.
- Mandatory intent-audit failure rolls back reservation; uncertain provider or
  finalization failure records `NEEDS_REVIEW` and blocks blind retry.

### Generated workspace and compatibility tests

- A clean attach contains nine fixed role TOMLs, all three native execution skill
  bundles, no retired execution role/skill, and no raw or ungated execution
  mutation MCP exposure. The protected `use_order_turn_grant` tool is projected
  only to Head Manager and is inert without current hook-injected proof.
- Updating an old clean generated workspace removes the retired managed files.
- Updating an old workspace with a modified retired file fails without deleting
  user content.
- Doctor, skill indexes, agent indexes, component indexes, projection manifests,
  wheel smoke, and native Windows launcher checks reflect the new roster and
  action path.
- Focused security, broker, order, API, MCP, workbench, generator, and end-to-end
  tests pass, followed by the full Python suite, Django check, compileall, and
  the required disposable generated-workspace/Codex-native smoke.

## Acceptance Criteria

The change is complete only when all of the following are true:

1. No source registry, generated workspace, prompt, skill projection, doctor
   check, or public documentation presents `execution-operator` as an active
   role.
2. No subagent can obtain an execution mutation tool. Head Manager's sole
   protected grant tool is inert without the exact current-turn hook proof.
3. Direct MCP, REST, generic CLI, and Workbench callers cannot submit or cancel
   an order; calling the protected tool without hook proof is not authority.
4. A valid native exact invocation can submit or cancel through the existing
   service kernel without starting analysis or dispatching a role.
5. Any missing or malformed invocation, approval failure, policy failure,
   duplicate request, connection failure, or audit failure stops at the same or
   an earlier boundary than today.
6. Provider uncertainty still produces durable `NEEDS_REVIEW` state with no
   automatic retry.
7. Existing central DB execution records remain readable without migration.
8. Generated workspace update safely retires unmodified agent files and
   preserves modified-file conflict protection.
9. A saved Scheduled Task prompt is processed as an ordinary root turn on each
   run; only an exact `$tcx-order-allow` first meaningful line can create one bounded
   effect grant, and no Automation-origin detector is involved.

## Explicit Non-Goals

- No Workbench trading button or remote execution UX.
- No natural-language intent classifier, keyword synonym list, or negation
  router.
- No model-minted execution authority, model-issued approval, or
  model-generated live confirmation. Within a valid turn grant, Head Manager
  may select one submit or cancel using canonical identifiers; service gates
  remain decisive.
- No claim that natural-language symbol, notional, timing, or strategy scope is
  deterministically enforced unless it is represented in canonical policy,
  ticket, receipt, or execution-mode state.
- No new broker adapter, order type, instrument, margin, or live-execution
  capability.
- No weakening of approval separation, policy, money, idempotency, audit,
  secret, adapter, or uncertainty boundaries.
- No automatic deletion of user-modified retired generated files.

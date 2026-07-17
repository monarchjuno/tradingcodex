# User-Facing Skills

TradingCodex exposes many skills internally, but users usually interact with a
small user-facing set. Primary skills start the main plane. Supporting skills
shape, automate, or review workflows without granting extra authority.

## Naming Contract

All 33 bundled TradingCodex skills use the `tcx-` namespace. Prefer one word
after the prefix and use at most two words when clarity or safety requires it,
as in `tcx-order-submit` or `tcx-investor-context`. Folder names, frontmatter
names, registry ids, projected paths, UI metadata, and explicit `$` invocations
must match exactly. Legacy built-in names are not projected as aliases.

The namespace is reserved for bundled product skills. User-owned
`strategy-*`, `investment-brain-*`, and optional role skills remain separate;
an optional skill cannot claim a `tcx-*` id.

The shared internal `tcx-artifact` skill is projected to every producing fixed
role. It teaches the exact research-artifact, thesis-lifecycle, and forecast
ledger contracts; users do not invoke it as a Head Manager entrypoint.
The shared internal `tcx-calculation` skill is projected only to the six roles
that perform bounded financial calculations. It teaches Dataset retrieval,
prepared execution, diagnostics, exact reuse, and private-input separation; it
is not a Head Manager entrypoint.

## Primary Entrypoints

| Skill | Primary use | Main output |
| --- | --- | --- |
| `tcx-workflow` | Investment research, thesis review, Decision Packages, portfolio fit, risk review, or order-readiness coordination. | Dynamic exact-role evidence gathering, authenticated artifact synthesis, waiting/revise/blocked state, or Decision Package. |
| `tcx-memory` | Retrieve prior decisions, replay an historical decision with point-in-time evidence, compare outcomes, or validate a lesson. | Source-bound episodes, replay/review artifacts, lesson status, evidence tier, and next validation needed. |
| `tcx-strategy` | Design and manage reusable user strategy skills in a direct first-meaningful-line `$tcx-strategy` Research turn. | Validated strategy skill with required sections, status, projection metadata, and user approval posture. |
| `tcx-brain` | Author and manage user-owned Brain sources plus installed Brain validation, discovery, version, activation, rollback, and removal in a direct first-meaningful-line `$tcx-brain` Research turn. | Privacy-reviewed source action or canonical managed lifecycle result with id, version, status, digests, projection posture, and next step. |
| `tcx-dashboard` | Open the read-only workspace dashboard and review current attention items, recent research, forecasts, portfolio/order posture, pending permissions, and broker state. | Dashboard opened in the Codex in-app browser by default, or in an external browser only when explicitly requested, plus a compact recorded-state orientation. |
| `tcx-server` | Viewer/service health, `doctor`, update status, MCP readiness, DB path checks, and startup recovery. | Runtime status, recovery command, viewer URL, update guidance, or blocker reason. |
| `tcx-build` | Explicit current-turn workspace refresh, managed optional-role-skill lifecycle work, workspace MCP configuration, and connector/provider development. | Validated managed state or connector files, provider metadata, focused validation, reviewable diff, or an exact operator-terminal next step. |
| `tcx-order-allow` | Explicitly admit at most one approved submit or cancel later in the current root native Codex turn, including a Codex app Scheduled Task turn. | A mode-bound, single-use `OrderTurnGrant`; no immediate broker effect. |
| `tcx-order-submit` | Explicitly submit one already-approved order from a root native Codex workspace turn. | Redacted accepted, rejected, duplicate, or needs-review service result. |
| `tcx-order-cancel` | Explicitly cancel one known submitted broker order from a root native Codex workspace turn. | Redacted canceled, rejected, duplicate, or needs-review service result. |

## Supporting User Skills

| Skill | Primary use | Main output |
| --- | --- | --- |
| `tcx-plan` | Clarify scope, constraints, action boundaries, and stop conditions before an immediate or recurring task. | Compact user mandate and missing-field or blocked posture; never a server dispatch plan or selected team. |
| `tcx-automate` | Create or update Codex app Scheduled Tasks for simple research, monitoring, recurring analysis, portfolio/status review, order drafting, assisted execution, optional turn-authorized execution, or explicitly delegated turn-authorized Build work. | Schedule plus a durable runtime prompt that invokes the actual work skill, not `tcx-automate` recursively. |
| `tcx-investor-context` | Interview and preview workspace suitability context; persistent status/update/enable/disable/clear is handed to an explicit user-terminal command. | User-confirmed proposed values, exact terminal action, default application state, and remaining gaps. |

## Entrypoint Rules

`tcx-workflow` is the default for investment-facing natural-language prompts.
For ordinary workflow requests, the hook supplies transport/run context only.
`head-manager` interprets the request directly, begins a lightweight run,
dynamically selects/revises the smallest useful fixed-role team, and synthesizes
authenticated artifacts. The separate literal native-action protocol is
described below. Head Manager should not produce substantive investment analysis
before role artifacts exist.

`tcx-strategy` handles strategy authoring as durable user rules, not live
market analysis. A strategy can guide future workflows, but it does not approve
orders, grant broker authority, mutate policy, or execute trades. Durable
create, update, activate, archive, and delete requests start a new root native
`trading-research` turn with `$tcx-strategy` as its first meaningful invocation;
the scoped turn stages the standalone
body and calls the proof-protected `manage_strategy` service. It does not expose
the launcher/runtime or directly
repair generated skill folders, fixed-role TOML, or root projection blocks.

`tcx-brain` is the single user entrypoint for Brain source and installed-plugin
management. Source actions create, inspect, revise, validate, or explicitly
delete a user-owned workspace-local bundle; managed actions list, inspect,
install, update, activate, deactivate, rollback, or remove through the
proof-protected `manage_investment_brain` application service. Tool-using management starts in a root
native `trading-research` turn whose first meaningful invocation is
`$tcx-brain`, and the actual Codex sandbox must
permit workspace-local source writes. Authoring uses only exact user-selected
memory evidence and counterexamples, performs privacy review, and abstracts
general heuristics without copying private cases. Source create and revise run
non-mutating local validation, then create, revise, and delete all stop before
managed lifecycle work. Installation begins in a fresh explicit `$tcx-brain` turn and
starts inactive, activation requires an explicit request, and remove retains immutable
versions for provenance. The skill never directly edits managed packages,
registry, projection, or third-party sources and performs no implicit Git or
publication action.

Native workflow strategy selection requires exactly one explicit
`$strategy-*` invocation. Plain-language mentions never select a strategy, and
absence records `no_strategy`; the read-only viewer never selects a strategy.
Native binding validates and seals the active strategy into the protected run.

`tcx-memory` is an explicit retrieval, replay, review, and lesson-validation
entrypoint. For a current judgment it records the independent initial view
before introducing past cases. Wiki and graph outputs are rebuildable views;
canonical evidence remains in source snapshots, decision packages, forecast
events, and review artifacts. Use structured MCP for supported research,
replay, forecast, and authenticated lesson-review state. A decision snapshot or
postmortem lifecycle action without a projected structured tool is returned as
an exact explicit maintainer/user-terminal command; it is never smuggled
through the general model shell.

`tcx-investor-context` interviews and previews only the optional
workspace-local suitability file in the Codex turn. Persistent status, update,
enable, disable, or clear is performed by the user with the exact interactive
terminal command returned after confirmation; the skill does not bypass the
Build shell gate. Its persistent enable/disable state is separate from skill availability,
strategy rules, and internal paper account scope. It does not run investment
analysis or grant authority. Native run binding follows the saved workspace default;
the read-only viewer offers no one-run override and never rewrites the file.

`tcx-dashboard` opens the read-only viewer and handles user orientation. An
unqualified invocation opens the viewer in the Codex in-app browser; an external
browser is used only when the user explicitly requests it. The skill does not
silently switch surfaces and does not launch a browser through the shell. It
reads only the smallest relevant set of canonical status, research, forecast,
portfolio, order, permission, and broker surfaces, puts explicit attention
states first, and chooses the relevant Library, Skills, or System destination.
It does not begin an analysis run, dispatch a role, create an artifact, infer a
change without comparison evidence, or mutate any TradingCodex state. Missing
or redacted data remains unknown rather than becoming an empty or healthy state.

`tcx-server` handles operations and recovery. It can explain service state, local viewer
readiness, update posture, MCP configuration, and recovery steps. It should not
be used to perform investment judgment or connector implementation. It reads
hook startup context and the read-only status/update MCP tools. Service,
`doctor`, update, or recovery commands that require the launcher are returned
as explicit user-terminal steps instead of being run through the model shell.
The default doctor view is a compact layer summary with warning/failure detail;
`doctor --verbose` exposes every individual check for maintainer review.

`tcx-build` handles product/build-plane work. The root native prompt must have
`$tcx-build` as its first meaningful invocation, followed on the same or a
later line by a non-empty concrete request. A plain token and a Markdown link
are equivalent only when the link label and target match this workspace's
projected skill. The deterministic hook issues a DB-canonical grant bound to that
workspace, session, turn, cwd, and complete prompt. The grant is multi-use only
within that turn; every mutating follow-up needs the marker again, and
subagents cannot create or inherit it. The browser viewer has no Build path. The marker is intent, not permission:
the active Codex permission profile remains authoritative. The default
`trading-research` profile supports general calculation and credential-free
public retrieval plus user-owned file changes outside `trading/`. Controlled
`trading/` or optional-role-skill lifecycle Build work requires a fresh root turn with
`trading-build` selected. Codex Plan mode cannot
issue or use the grant, and a permission-mode change requires a fresh root
turn. Generated Build work uses native `apply_patch` for edits and a narrow
shell review lane: credential-free public GET/HEAD, enumerated read-only HTTPS
Git, limited workspace `pwd`/`cat`/`ls`, inert provider reads/hash/diff/Git
inspection, exact isolated `py_compile`, and allowlisted workspace-launcher
commands. General interpreters, helper scripts, test runners, build systems,
shell composition, and model-authored POST are blocked; the Build profile still
denies protected runtime/DB state,
credentials, ledgers, local/private or authenticated network access, remote
mutation, and global config. Public provider source is staged inertly under
`$TRADINGCODEX_SCRATCH/provider-sources/<provider-id>/`, never installed or
executed, and final workspace files are written with `apply_patch`. Broader
unit, smoke, and build validation runs from an explicit user or maintainer
terminal rather than the active Build turn. Generated
core harness files, hooks,
templates, fixed-role configuration, and service-owned projection blocks are
not direct Build edit targets. Brain and Strategy management instead begin
with their own first-meaningful-line invocation in `trading-research`; each current-turn
grant is limited to its matching native source/staging path and protected MCP
tool. The generated lifecycle launcher and attached runtime remain denied in
Research. Brain management always uses an explicit
workspace-local source or public credential-free HTTPS Git source and
never implies global config, raw credential access, user capability management,
source-repository or Git-publication actions. It may create live-capable
provider code. A missing provider is implemented and statically validated
before connector rendering; a `provider_development_required` scaffold is
created first only on an explicit scaffold-only request. Externally informed
provider bundles include `source-provenance.json`, and the user must approve the
exact provider bundle hash in an interactive terminal before the service may
load its immutable snapshot after restart. Connector registration and
validation resume in a fresh Build turn. Live execution still
remains behind service-layer approval, policy, connection, confirmation,
idempotency, sync, and audit gates.

Persistent `tcx mode` is retired. Its compatibility status is inert, old
`.tradingcodex/runtime/mode.json` state is ignored, and `tcx mode set ...`
cannot enable Build. User capability management belongs to Codex, while
provider-source approval is a separate interactive user-terminal operator
action; user-terminal CLI mutations remain separate operator authority.

`tcx-order-submit` and `tcx-order-cancel` are native-only exact
action protocols, not model procedures. Their bundles disable implicit
invocation and carry no MCP authority. The meaningful content of the root user
prompt must match the documented `--name value` grammar; a matching projected
Markdown skill link may replace only the leading skill token. The deterministic `UserPromptSubmit`
hook parses it and invokes the canonical service gateway before a model runs.
They are unavailable from Plan mode and subagents; the browser viewer exposes
no action entrypoint. Public/raw MCP,
REST, and generic CLI surfaces cannot perform these final mutations; the
separately protected grant consumer is inert without current hook proof.

`tcx-order-allow` is the explicit-only current-turn alternative for workflows that
do not have final ticket identifiers when the prompt begins. The first
meaningful line must contain exactly one of:

```text
$tcx-order-allow --mode paper
$tcx-order-allow --mode validation
$tcx-order-allow --mode live
```

The leading skill token may be its matching projected Markdown link, but the
mode syntax stays exact and a non-empty workflow request follows on later
lines. The hook issues an `OrderTurnGrant` bound to workspace, session, turn,
the original complete prompt hash, Codex permission mode, and execution mode. Plan mode rejects grant
issuance and use. The grant is usable for one submit or cancel, expires after
one hour, and is revoked on `Stop`, the next user turn, or consumption. It
grants no approval and performs no immediate action. Only root Head Manager can
call `use_order_turn_grant`; `PreToolUse` injects the internal proof, so
subagents and direct MCP callers have no authority. The browser viewer cannot
request or consume a grant.
If a consumed grant is still `authorizing`, Stop and new-turn cleanup do not
reset it. The same session blocks a new Build or order-sensitive prompt until
the canonical result is terminal; status inspection and ordinary research may
continue without retrying the effect.

`tcx-automate` authors the Codex app Scheduled Task. The app submits the
saved prompt on each scheduled turn, and TradingCodex treats it like any other
root prompt rather than detecting an Automation origin. Ordinary research,
monitoring, analysis, portfolio/status, draft, and assisted-execution prompts
must omit both authority markers. Only optional final execution uses the exact
plain `$tcx-order-allow` first-meaningful-line form above. Only deliberately delegated recurring
workspace-local Build work uses plain `$tcx-build` as the first meaningful line,
with the concrete build request below it. Each scheduled run receives a fresh
current-turn grant decision and remains subject to that run's actual Codex
sandbox. Controlled `trading/` or managed lifecycle recurring Build work needs
a `trading-build` runtime; a `trading-research` run may read and write ordinary
user-owned paths outside `trading/`, use temporary computation,
credential-free public retrieval, rendering/inspection, and specifically
proof-protected canonical DB calls, while Plan mode blocks Build entirely.
Prefer an isolated worktree or workspace and retain a reviewable diff for
scheduled changes. Never combine `$tcx-build` with `$tcx-order-allow`. The saved prompt
invokes `$tcx-workflow` or the selected work skill; it must not invoke
`$tcx-automate` again.

The exact mode is a ceiling, not deterministic interpretation of the remaining
prose. Canonical policy, ticket, receipt, action, broker state, and mode are
service-enforced. Natural-language symbol, notional, schedule, or strategy
limits are enforceable only after they exist in canonical state.

`tcx-plan`, `tcx-automate`, and `tcx-investor-context` are user-facing
support skills. A `tcx-plan` mandate preserves scope but does not choose
roles; Head Manager still decides and revises the smallest useful team from the
live task and accepted evidence. Decision Memory owns postmortem and lesson-review
requests. None replaces `tcx-workflow` as the normal investment-dispatch
entrypoint.

## Role-Owned Skills

Role-owned subagent skills such as `tcx-judgment`,
`tcx-fundamental`, or `tcx-portfolio` belong to fixed-role dispatch.
Users normally reach them through `tcx-workflow`, not by calling the role skill
directly. The retired `execute-paper-order` role skill has no compatibility
alias; use the exact root-native action above.

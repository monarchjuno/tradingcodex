# Roles, Skills, And Workflows

This document owns current role identity, skill responsibility, dynamic investment
orchestration, handoffs, overlays, and execution separation.

## Responsibility Split

| Surface | Owns | Does not own |
| --- | --- | --- |
| Head Manager prompt | concise coordinator identity, plane routing, stable authority, and hard stops | reusable workflow mechanics, role methods, execution authorization |
| `tcx-workflow` skill | request interpretation, smallest-team judgment, parallel waves, artifact-driven revision, synthesis procedure | durable role eligibility, MCP capability, approval |
| Investment Brain plugin | platform-neutral hypotheses, inquiry priorities, causal frames, interpretation, falsifiers, and abstention heuristics | role selection, tools, workflow, memory, policy, approval, execution |
| Knowledge Wiki | reusable untrusted factual background and explicit agent-maintained Markdown links | current evidence, reasoning instructions, automatic writes, or investment conclusions |
| Fixed-role base prompt | shared child safety, evidence/handoff invariants, compact artifact reads, and gap handling | provider procedure, specialist identity, cross-role scheduling |
| Fixed-role TOML | concise specialist identity, unique boundary, web posture, tools, and MCP principal | shared evidence procedure, cross-role scheduling, or model policy |
| Role skills | domain procedure and output quality | role identity or authority |
| Hooks | compact session health and current-turn authority context, reserved action parsing, proof injection, and TradingCodex-owned safety-gate audit | natural-language routing, run/session ownership, child lifecycle, role selection, generic shell/network policy, or lane/team/DAG selection |
| Django services/MCP | run provenance, principal/tool checks, artifact lineage, policy/order/approval/broker/execution/audit state; one protected turn-grant consumer plus no raw final mutation | investment research orchestration or model-granted execution authority |

The current Codex authoring contract follows the official
[skills](https://learn.chatgpt.com/docs/build-skills),
[subagents](https://learn.chatgpt.com/docs/agent-configuration/subagents),
[hooks](https://learn.chatgpt.com/docs/hooks), and
[prompting](https://learn.chatgpt.com/docs/prompting) guidance:

- skill metadata stays concise enough for discovery, while `SKILL.md` keeps the
  core procedure and task-routed bundle resources load only when needed;
- independent children receive compact task-local briefs with no full-history
  fork, and write-heavy coordination remains centralized;
- every matching hook can run concurrently, so service authorization never
  depends on one hook suppressing another; exact project-hook trust is part of
  release acceptance; and
- durable instructions describe outcome, context, boundaries, and completion
  evidence without scripting every judgment step.

The Head Manager prompt and fixed-role TOMLs intentionally do not repeat spawn,
wait, source-fallback, artifact-field, or retry procedures. Those procedures
live in `tcx-workflow`, `tcx-source-gate`, `tcx-artifact`, and other owning
skills. Generated role source blocks list permitted skills and the canonical
artifact boundary without embedding their manuals in every role.

Fixed roles load `tcx-calculation` only for reproducible derived calculations
that can change a conclusion. Quoting or comparing source-reported figures and
trivial arithmetic do not trigger that skill.

These are product-shape inputs, not claims that prose creates authority. Native
permissions, exact role config, authenticated MCP/service checks, and artifact
receipts remain authoritative.

## Canonical Skill Bundles

Canonical ownership applies to the whole skill directory for Head Manager and
fixed-role skills. `SKILL.md` is the concise discovery and core-procedure
entrypoint; optional detail, examples, schemas, templates, executable helpers,
or other reusable resources may live at clearly named bundle paths. The
entrypoint states when each resource is relevant. Directory names such as
`references`, `scripts`, or `assets` are conventions, not a required taxonomy.

Compactness is a default-context objective, not a fixed byte target. Numeric
limits require a measured Codex compatibility, safety, or latency failure and
must name that failure. Validation instead checks clear triggering metadata,
one canonical owner, task-routed resource links, absence of duplicated
procedures, and recursive projection of every managed bundle file. Untrusted
external package safety limits remain separate from this authoring rule.

## Skill Invocation Contract

TradingCodex uses one lexical contract for managed, selection, and order
skills. It removes one UTF-8 BOM, normalizes CRLF, CR, NEL, and Unicode
line/paragraph separators to LF, tolerates leading and trailing blank lines,
and trims horizontal whitespace for recognizing an invocation. It does not
case-fold skill ids, normalize confusable characters, or ignore zero-width
characters.

A plain `$skill-id` and a Markdown link are equivalent only when the link label
is that exact id and its target resolves to the matching projected
`SKILL.md` in the current workspace. Build and state-changing Brain, Wiki, and
Strategy lifecycle management requires the invocation on the first meaningful
line; the concrete non-empty request may share that line or follow it. Mixed or
distinct managed markers remain invalid. Brain and Strategy selection accepts a plain token or matching
projected link, deduplicates repeated references to the same id, and rejects
distinct multiple ids.

Order syntax is intentionally narrower. `$tcx-order-allow --mode
paper|validation|live` occupies the complete first meaningful line and requires
a non-empty request below it. Immediate submit/cancel remains an action-only
prompt with exact `--name value` pairs. A matching projected Markdown skill
link may replace only the skill token; it does not relax flags, values, or the
single-effect rule. Grant, mandate, and audit bindings continue to hash the
original unnormalized prompt.

## Fixed Team

Head Manager coordinates nine fixed analytical and decision-support subagents.
There is no execution subagent.

| Role | Purpose | Key prohibition |
| --- | --- | --- |
| `head-manager` | interpret, coordinate, reassess, synthesize | performing analyst research itself |
| `fundamental-analyst` | business, financials, filings, economics | orders/approval/execution |
| `technical-analyst` | price, trend, momentum, volume, volatility, liquidity | standalone final recommendation |
| `news-analyst` | current disclosures, news, chronology, narrative change | rumor-as-fact |
| `macro-analyst` | rates, FX, commodities, policy, macro transmission | orders/execution |
| `instrument-analyst` | ETF/index/options/crypto mechanics and market structure | unsupported execution claims |
| `valuation-analyst` | ranges, scenarios, sensitivities, gaps | approval/execution |
| `portfolio-manager` | fit, sizing, concentration, draft readiness | self-approval/execution |
| `risk-manager` | downside, restrictions, policy/approval readiness | drafting or executing orders |
| `judgment-reviewer` | independent challenge, source trust, conflicts, forecast judgment | producing the original analysis |

Generated root Codex TOML omits `model` and `model_reasoning_effort`, so
`head-manager` inherits the user's current Codex model and reasoning settings.
All nine fixed roles remain pinned directly to `gpt-5.6-terra` with `high`
reasoning to keep specialist cost and latency balanced. These child TOML
settings are not a model-policy registry, manifest, rollout control, or doctor
availability state. All use the shared
`trading-research` profile: ordinary
user-owned paths outside `trading/` may be used as workflow inputs or outputs,
while `trading/`, generated controls, credentials, and runtime state remain
protected. Head Manager receives live web search for planning-only
reconnaissance, and evidence-producing roles receive it for role-owned research.
Portfolio, risk, and judgment review explicitly disable it. Final
provider effects are not a role and run through the deterministic service
gateway rather than an execution model.

## Dynamic Workflow

Head Manager reads the user's original language directly. It does not ask a
hook or Django classifier to translate the request into a lane.

1. Answer a narrow trusted fact or simple recorded status directly, without a
   run, child, or artifact.
2. For fresh research or decision work, call `begin_analysis_run` once to seal
   request and overlay provenance.
3. If one exact Investment Brain id is selected by its plain token or matching
   projected skill link, apply it as an inquiry and
   interpretation overlay, then translate its domain questions into role-owned
   work. Use the pristine baseline when none is selected.
4. Frame the request as a provisional causal map and identify the causal cruxes
   that could materially change the answer or its readiness.
5. Choose the smallest useful set of role profiles by distinct expertise.
6. Dispatch independent questions in parallel when useful. Reuse a live child
   with `followup_task` for its own correction or clarification.
7. A generic child may cover only an unavailable evidence-producing role with
   the same bounded research-only brief and prohibitions. If an independent
   `risk-manager` or `judgment-reviewer` review is needed but unavailable,
   return the explicit profile gap as `blocked` or `waiting`.
8. Reassess useful evidence. An accepted result may trigger a correction from
   its owner or a causal missing-evidence question for another specialty; give
   that role the exact artifact ID and bounded question, then reassess again.
   Otherwise request independent judgment, stop, answer directly, or synthesize.
9. Save a run-local artifact only when the result supports a decision, reuse,
   audit, or downstream handoff.

Head Manager updates when workflow state materially changes. A timeout alone is
not progress. Updates state only observable progress, evidence gaps, and the next action;
private model reasoning and unaccepted findings are never surfaced.

For final synthesis, forecasts, recommendations, valuations, portfolio/risk
results, and high-consequence judgments, the canonical `tcx-workflow` skill
uses a natural evidence structure plus only the applicable base rate or
comparison, scenarios, assumptions/falsifier, contrary evidence, and update or
readiness limits. It does not impose sentence tags or a template on narrow
answers or intermediate role work.

Broad analysis is not a fixed template. A factual company profile may need one
fundamental role. For a horizon-sensitive directional forecast, Head Manager
first resolves the relevant market session and separates instrument-specific
from market-wide drivers. At one market session or less, market-wide regime or
cross-asset transmission is presumed material unless the request isolates an
idiosyncratic event; `macro-analyst` owns that question when it could change
direction, range, or scenario weights. This is a causal-coverage rule, not a
fixed macro/technical/news roster. A recommendation or portfolio/risk decision
usually needs independent judgment. Evidence can change the next role.

The shared workflow treats inside-out economics, outside-in peers and base
rates, upstream/downstream value-chain position, time and expectations, and
competing explanations as optional question-generating lenses rather than
mandatory coverage. A clear narrow scope and explicit exclusions remain
binding. When coverage is underspecified, Head Manager may inspect causally
adjacent factors that could materially affect the answer, including important
points the user may not know to ask about, without widening the outcome or
action authority.

Head Manager routes unresolved causal cruxes rather than a preset role
checklist. It distinguishes structural from cyclical drivers, leading evidence
from lagging outcomes, and temporary states from durable transitions. Evidence
is judged by independence, diagnostic value, and position in the causal chain,
not source count; dependent repetition is not corroboration. Plausible competing
explanations remain live until evidence distinguishes them. Each material
uncertainty becomes an observable update that names the affected causal link
and direction, or an explicit unknowable gap.

Research quality and decision relevance take priority over resource economy.
Head Manager continues while a material uncertainty remains and relevant
evidence is obtainable within the user's scope and authority. Tool-call count,
context size, and latency alone never justify stopping decision-relevant
research; deduplicated calls, compact artifact handoffs, and parallel
independent work manage those operational concerns. It stops only when another
in-scope question is unlikely to change the answer or readiness, the needed
evidence is unavailable, or an explicit user scope or deadline requires it.

Explicit negations and constraints are binding. Ambiguity is resolved by Head
Manager only when it materially changes the requested outcome or sensitive
authority. No server candidate-role ceiling or mandatory analytical DAG exists.

## Source Routing

Before refetching a data family with material reuse value, Head Manager checks
only current-workflow artifact candidates and Dataset cards/manifests. It
assigns one producing owner and gives relevant children briefs that name that
owner, exact reusable IDs, and the needed or missing slice; roles do not browse
a whole catalog. A stale, incomplete, or mismatched record still contributes
its valid portion, while only the owner retrieves missing coverage. Head Manager
then gives the evidence producer the smallest missing-data question and other
roles compact SourceSnapshot, Dataset, and ResearchArtifact IDs. The producer
follows the canonical `tcx-source-gate` procedure: reuse supplied evidence, use
one relevant enabled user capability, use optional direct OpenBB, prefer
original public records, then reliable web sources, and state a remaining gap.

This is agent guidance, not a Django routing state machine. The role names the
provider where possible, does not repeat unchanged calls to the same source,
and preserves a partial result while seeking only the missing field, identifier,
or period. User capabilities remain BYOR; TradingCodex does not install,
classify, proxy, approve, or audit them.

Use `record_source_snapshot` for every external source. Create a Dataset only
for reusable structured rows, and bind the resulting Snapshot and Dataset IDs to
the ResearchArtifact. The six evidence roles alone may receive the direct
optional OpenBB MCP; Head Manager, portfolio, risk, and judgment roles do not.
The canonical details are in
[data-sources-and-openbb.md](./data-sources-and-openbb.md).

## Delegation And Follow-Up

Prefer an exact registered profile when its specialty is available. Give a child
only the run identity, bounded question, user constraints, relevant source lead,
and exact upstream IDs it needs. Do not copy the full root history, role manual,
source output, or unrelated artifacts into the brief.

Spawn an exact profile with its exact `agent_type`, compact `message`,
`task_name`, and `fork_turns="none"`; its role TOML supplies the fixed model
and reasoning settings. Continue only after the tool returns a live target.
Correct a rejected spawn only when the error identifies the change.

Use `followup_task` when a live child still owns the correction or clarification.
Start a fresh child for a new specialty, an unavailable session, or independent
review. The Dynamic Workflow fallback boundary applies here. A generic child
retains the same research-only scope, evidence standard, no-secret boundary,
and no-order boundary; it cannot approve, execute, access a broker, or act as
Head Manager. Whether to delegate a distinct child-owned subtask further is a
native Codex decision, not a workflow requirement. Each descendant remains
subject to its selected projected role and the shared evidence, authority, and
safety boundaries. Wait only while a live child has useful work.
`begin_analysis_run` creates no child. Before any wait, `spawn_agent` or
`followup_task` in the current run must have returned a live target; without
one, Head Manager dispatches the chosen role or stops.
Native
wait-any may serialize without explicit targets; verify lifecycle through the
native tool result and child session events rather than treating that as failure.

A same-owner follow-up distinguishes the target owner's Artifact ID to append
or revise from triggering cross-role Artifact IDs consumed as inputs. If an
expected target ID is absent, the child returns `waiting`; a brief says to
create a new artifact only when that is intended. A new specialty receives no
target ID, only the triggering IDs and an explicit new-artifact instruction.

## Handoffs And Artifacts

Persist a role result through `create_research_artifact` when external evidence
changes a conclusion; it supports a forecast, recommendation, valuation,
portfolio, or risk judgment; a downstream role consumes it; it records a
material source conflict or decision-relevant gap; or it informs
order/execution readiness. A narrow bounded answer and discarded intermediate
thought need no artifact. Saved work includes source/as-of posture, readiness,
confidence, missing evidence, next action, blocked actions, consumed IDs, and an
explicit handoff state; use the service-returned identity rather than inventing
one.

Head Manager reads only the exact accepted artifact needed and retains its ID
and content hash. Fixed children receive exact upstream IDs rather than broad
artifact discovery. Parallel assignments separate genuinely independent
questions or source classes, while judgment review remains independently free
to challenge shared evidence.

An `evidence_pack` under `trading/research/` is separate only when source intake
has independent reuse, cross-role handoff, conflict, or gap value. A role
conclusion is a `role_report` under `trading/reports/<role>/`; when the evidence
only supports that report, the role creates no duplicate evidence pack. When
both are justified, the report consumes the pack's authenticated Artifact ID
through `input_artifact_ids`, retains the exact Snapshot/Dataset IDs it uses,
and does not copy the same body into both artifacts.

Every producing fixed role receives the shared `tcx-artifact` skill. It maps
the service's state-specific thesis lifecycle, single-range probability,
follow-up request, RFC 3339, and complete forecast base-rate requirements into
a compact persistence procedure. MCP schemas expose the same nested fields so
deferred tool discovery supplies the contract before the call. One artifact
write permits at most the initial submission plus one targeted corrected retry;
if that retry fails, the role stops in `waiting` even when a further correction
seems possible.

The same bounded rule applies across role MCP use: a documented terminal
outcome such as stored, updated, existing, reused, or prepared ends that call;
an unchanged deterministic failure is never blindly resubmitted. Under the
Codex 0.145.0 deferred-tool contract, unknown-provider resolution starts with
the canonical names-only query
`text(ALL_TOOLS.filter(x => x.name.includes("<provider-or-keyword>")).slice(0, 12).map(x => x.name))`.
One names-only query may combine at most four literal `name.includes`
predicates with only `||`/`&&` while retaining the same 12-name slice and
name-only projection. Only an exact name present in that prior result may
be selected for at most one schema lookup, using the anchored canonical form
`const t = ALL_TOOLS.find(x => x.name === "<exact-tool-name>"); text(t ? t.description : "missing")`.
Each step emits exactly one standard data envelope. Codex transport may prepend
a status prelude; that prelude is not a second data envelope. Description
mapping, searching, filtering, or regex; full `ALL_TOOLS` records or catalogs;
unselected-name inspection; and repeat schema lookups are invalid.

For run-bound artifacts, provide `workflow_run_id` and exact consumed
`input_artifact_ids`. The service derives producer identity, schema version,
content hash, and input hashes. `plan_hash`, `stage_id`, and `task_id` are not v1
artifact fields.

`accepted` means the producer considers the artifact ready for Head Manager
review. It is not a server workflow terminal action. Head Manager may still
revise, challenge, or add another role. Before publication, the service applies
the strict artifact-quality contract to the intended rendered bytes of every
accepted run-bound artifact. Invalid output receives no stable file or receipt
and returns to its producer for correction. Head Manager synthesis accepts only
authenticated current-run inputs whose handoff state is `accepted`;
`revise`, `blocked`, and `waiting` artifacts remain explicit workflow evidence,
not synthesis inputs.

## Judgment And Method Selection

Use `judgment-reviewer` for recommendations, portfolio/risk decisions,
material conflicts, and high-consequence uncertainty. Do not force it into a
narrow factual request.

Select a method profile that matches the task:

- `general_evidence_v1`
- `event_research_v1`
- `quant_signal_v1`
- `listed_equity_fcff_dcf_v1`

Do not force quant or FCFF contracts onto incompatible questions. Source
snapshots, point-in-time cutoffs, forecasts, calibration, concise decision-
quality checks, and anti-overfit validation remain available as appropriate.
In particular, a decision-usable forward per-share DCF requires attributable,
current support for the cash-flow base, reinvestment/CAPEX, working capital,
net debt or cash, diluted shares, and forecast bridge. Prefer audited filings
or issuer disclosures for historical accounting facts, while allowing
provider-normalized fundamentals, reputable consensus estimates, and other
credible secondary evidence when their provider, period, units, adjustments,
and material conflicts are checked. A missing source-of-record item lowers
confidence or widens sensitivity unless that item is conclusion-driving and
unresolved. When the overall foundation is materially insufficient, use a
reverse-DCF expectation threshold, a labeled scenario screen, or abstain
instead of manufacturing a precise intrinsic-value target.

## Dataset And Calculation Discipline

Reusable numerical evidence follows search-before-fetch and search-before-
compute. Head Manager may search Dataset and Calculation cards and inspect
Dataset manifest metadata to identify available evidence, stale cutoffs,
missing inputs, and the smallest useful role. It cannot register a Dataset,
materialize rows, prepare or record a calculation, or treat a historical card
as evidence for the current conclusion. Current synthesis requires an exact
current-run Dataset and Calculation binding returned by an eligible producer.

The shared `tcx-calculation` skill is projected to `fundamental-analyst`,
`technical-analyst`, `macro-analyst`, `valuation-analyst`,
`portfolio-manager`, and `risk-manager`. It owns the cross-role procedure for:

- card → manifest/profile → typed slice progressive disclosure;
- deciding between Decimal and NumPy numerical semantics;
- preparing the script, exact parameters, cutoff, input hashes, and output
  schema before running `tcx-calc`;
- calling the injected `tcx_emit_result` global exactly once with one positional
  result object whose metrics use the exact typed six-field shape;
- DCF/IRR sign and timing, regression sample/confidence/residual diagnostics,
  optimizer convergence, deterministic seed, and numerical warning posture;
- recording only conclusion-relevant successful or failed prepared runs and
  making exact reuse visible;
- diagnosing a failed Run from its safe code/message, then preparing one
  corrected immutable script/spec retry instead of overwriting or blindly
  repeating the failed attempt; and
- keeping private ledger materializations out of Dataset, script, artifact,
  stdout/stderr, and Calculation metadata.

Role-specific analytical methods remain in `tcx-fundamental`,
`tcx-technical`, `tcx-macro`, `tcx-valuation`, `tcx-portfolio`, and
`tcx-risk`; the shared skill does not replace those methods or widen the role's
question. News, instrument, and judgment-review roles do not receive the
calculation execution bundle. Build receives none of the Dataset mutation,
materialization, or Calculation execution tools.

MCP permission is composed from named tool groups instead of repeated
role-local lists. Head Manager receives only `DATASET_CARD_DISCOVERY` and
`CALCULATION_CARD_DISCOVERY`; the six calculation roles receive the applicable
full `DATASET_DISCOVERY`, `DATASET_WRITE`, `CALCULATION_DISCOVERY`, and
`CALCULATION_EXECUTION` groups. The role registry remains the authority for
which exact groups a role receives. The browser viewer has
read-only cards, schema/profile, lineage, result, and payload-availability
surfaces only; it cannot register, materialize, prepare, run, reuse, or record.

## Typed Context And Overlays

TradingCodex uses a typed authority model rather than one flat prompt-priority
list:

| Layer | Scope |
| --- | --- |
| TradingCodex Core | Evidence provenance, roles/tools, point-in-time discipline, policy, approval, execution, audit, and run integrity. |
| Current user mandate | Requested outcome, scope, prohibitions, and explicit one-run overlay choices, subject to Core. |
| Investor Context | Suitability constraints such as horizon, liquidity, loss capacity, and concentration. |
| Strategy | Explicit decision policy, eligibility, entry/exit, sizing, and risk rules. |
| Investment Brain | High-freedom inquiry and interpretation heuristics. |
| Method skills | Bounded analytical procedures. |
| Current-run evidence | Authenticated facts, source/as-of posture, conflicts, and uncertainty. |
| Decision Memory | Prior cases and validated lessons as non-authoritative evidence. |

TradingCodex includes Head Manager skills, bundled role skills, optional role
skills, `strategy-*` skills, Investment Brain plugins, and project additional
instructions.

All bundled Head Manager and role skill ids use the reserved `tcx-` namespace:
one suffix word is preferred and two is the maximum. The exact id is shared by
the folder, frontmatter, registry, projection, UI metadata, and invocation.
User-owned strategies, Investment Brains, and optional role skills retain their
separate namespaces and do not receive legacy bundled aliases.

- Skills are procedures, not evidence or authority.
- Head Manager may use narrow live-web reconnaissance only to resolve the
  subject or event, identify likely source availability and material unknowns,
  and choose or revise the smallest useful fixed-role team. Raw reconnaissance
  is untrusted planning input: it cannot answer the mandate, support a material
  claim, or enter synthesis as accepted evidence. Any conclusion-relevant fact
  must be reacquired by a producing role and returned through an authenticated
  run-local artifact. It batches at most two discovery queries in a short
  response, then opens one selected primary source per call, at most two total,
  also in short responses; medium, long, or batched source opens are outside
  the contract.
- Host-global/plugin skills require explicit user selection or managed
  activation; pristine Strategy authoring does not auto-load one. This skill
  overlay rule does not apply to read-only external apps, connectors, MCP
  servers, or data tools used as evidence sources. When the task needs external
  evidence or names a provider, the owning agent inspects the current task's
  callable surface, including the runtime's available deferred-tool discovery
  surface, and attempts
  the smallest relevant read-only call before public-web fallback.
- Evidence-producing roles retain the exact SourceSnapshot/Dataset IDs they use
  and keep handoff context compact; the source-routing procedure itself lives
  only in `tcx-source-gate`.
- A native run selects at most one exact `$strategy-*` id through a plain token
  or matching projected skill link.
- A native run selects at most one exact `$investment-brain-*` id through the
  same forms. Selection is explicit-only; plain-language resemblance never
  activates one, repeated same-id references deduplicate, and distinct multiple
  or unresolved selections fail before analysis.
- Investment Brains remain Head Manager-level, platform-neutral, high-freedom
  inquiry/interpretation overlays. They may not name roles, dispatch agents,
  call tools, prescribe workflow, retrieve or modify memory, or widen policy,
  approval, broker, order, or execution authority.
- Head Manager translates Brain questions into the smallest useful dynamic
  fixed-role team. It sends derived questions rather than the Brain body to
  children.
- Brain, Strategy, and Investor Context bindings are sealed into the analysis
  run and cannot be replaced mid-run. The Brain binding includes the validated
  projected skill-tree digest; native Codex loads only the selected skill and
  its required linked references. A different Brain or Strategy starts a new
  run.
- Optional skills must stay within their owner role and cannot widen MCP, approval, broker, or execution permissions.

Conflict resolution follows authority type. Core safety remains blocking.
Investor Context blocks an unsuitable Strategy until the user resolves the
gap. Strategy governs an explicit decision rule when it conflicts with a
Brain. Authenticated evidence controls factual claims when it conflicts with a
Brain or Strategy. Decision Memory contributes chronology- and regime-aware
support or counterexamples but never automatically overrides evidence or
updates a Brain.

When memory may influence a new judgment, form and preserve an independent
current-evidence view before retrieving similar prior cases. Synthesis states
the Brain's material influence, overlay/evidence/memory conflicts, and any
post-memory decision delta.

Use `$tcx-brain` as the user-facing manager over TradingCodex's canonical Brain
plugin CLI/registry for source authoring, discovery, validation, installation,
activation, update, rollback, removal, and explicit-only projection. Use the
CLI/API/strategy creator and optional-skill services for their respective
overlays so `SKILL.md`, `agents/openai.yaml`, TradingCodex metadata, validation,
activation, and projection remain aligned. See
[Investment Brain Plugins](investment-brain-plugins.md).

`tcx-wiki` is implicit-capable because Head Manager may search relevant active
Wikis without a marker. Query and lint are read-only. Local ingest and shared
source authoring require an explicit user request naming Wiki as the
destination; research completion never writes automatically. Community
lifecycle mutations require a fresh exact `$tcx-wiki` root turn, while list,
inspect, and validation are proof-free. Head Manager owns this work directly;
there is no librarian role. See [Knowledge Wikis](knowledge-wikis.md).

Natural-language requests may author a user-owned Brain source and use
proof-free list, inspect, and validation actions. Installed-state mutations
run in a fresh root `trading-research` prompt whose first meaningful invocation
is `$tcx-brain`. The hook-issued Brain-scoped grant admits only the matching
lifecycle mutation; it never elevates the sandbox, crosses into Build, Wiki, or
Strategy, or carries into a follow-up or subagent. Source edits remain native;
installed registry and projection lifecycle uses only the proof-protected
`manage_investment_brain` MCP tool, with the Research runtime and launcher
still denied. The browser viewer has no management path. Source authoring
curates exact user-selected Decision
Memory evidence and counterexamples into a privacy-reviewed abstraction under
`investment-brains/<investment-brain-id>` by default. It never copies private
cases or edits installed or third-party packages. Source create and revise run
the non-mutating local validation before stopping; source create, revise, and
delete all stop before install or activation. A fresh explicit `$tcx-brain` turn
installs inactive through the shared service, then activates only on the user's
exact request. Managed remove drops the projection but retains immutable
versions, while source deletion is separate. Neither path implies Git or
publication actions.

## Execution Workflow

Dynamic research does not make execution nondeterministic. Natural language is
never an order. `portfolio-manager` may draft through its allowed tool and
`risk-manager` may request approval through its allowed tool. Head Manager may
coordinate evidence and explain status. No fixed role has a submit or cancel
mutation tool. Head Manager has only the protected `use_order_turn_grant`
consumer; without proof injected by `PreToolUse` for the current exact
`$tcx-order-allow` turn, it has no execution authority.

For already-known canonical identifiers, the final external effect remains
available from a root native Codex user turn whose meaningful content matches
one exact action-only skill invocation. The canonical plain forms are:

```text
$tcx-order-submit --ticket-id <ticket-id> --approval-receipt-id <approval-receipt-id>
$tcx-order-submit --ticket-id <ticket-id> --approval-receipt-id <approval-receipt-id> --live-confirmation <token>
$tcx-order-cancel --ticket-id <ticket-id> --broker-order-id <broker-order-id> --approval-receipt-id <approval-receipt-id>
$tcx-order-cancel --ticket-id <ticket-id> --broker-order-id <broker-order-id> --approval-receipt-id <approval-receipt-id> --live-confirmation <token>
```

The leading skill token may be its matching projected Markdown link; every
other part of the grammar remains literal. These two root skill bundles
describe a protocol and disable implicit invocation; they carry no MCP or
broker authority. `UserPromptSubmit` recognizes the reserved invocation,
rejects malformed or subagent forms,
parses the complete prompt deterministically, creates a workspace-bound
`native-user` mandate, and calls the canonical service gateway in-process before
an analysis run begins.

For a workflow that creates or selects identifiers during the turn, the first
meaningful line must instead contain exactly one canonical mode invocation:

```text
$tcx-order-allow --mode paper
$tcx-order-allow --mode validation
$tcx-order-allow --mode live
```

The skill token may be its matching projected Markdown link, but no prose or
extra flag may share the line and a non-empty workflow request must follow. The
hook requires a root native Codex `session_id` and `turn_id`, issues one grant
bound to workspace, session, turn, original complete prompt hash, Codex permission
mode, and execution mode, then continues ordinary orchestration. Plan mode
rejects immediate order effects plus grant issuance and use. The grant expires
after one hour and is revoked by one submit or cancel, `Stop`, or the next user
turn. The grant is never passed to a child. When Head Manager later calls `use_order_turn_grant`, `PreToolUse`
reserves it against the tool-use id and rewrites the input with internal proof;
the model and a direct MCP caller cannot supply that proof.

The browser viewer has no mutation route. Public REST, generic CLI, subagents,
and direct MCP calls expose no usable submit, cancel, or broker-status-refresh authority. Order tickets, approval
receipts, payload hashes, idempotency, account scope, broker capability, live
confirmation, submission, cancellation, reconciliation, and audit remain
explicit Django service state.

`$tcx-order-allow` is a deterministic syntax and mode gate, not a natural-language
policy compiler. The service enforces the canonical ticket, receipt, action,
broker posture, policy, and requested mode. Free-form symbol, notional,
schedule, or strategy scope is binding only when represented in those canonical
records; ambiguity or mismatch must stop before the effect.

## Codex App Scheduled Tasks

`tcx-automate` authors Codex app Scheduled Tasks across the full recurring
surface: simple research, monitoring, analysis, portfolio and status review,
draft-order preparation, assisted execution, and optional turn-authorized
execution. It is not a second scheduler and must not be copied into the saved
prompt as a recursive runtime invocation. The saved prompt invokes the actual
workflow skill on every scheduled turn.

TradingCodex handles scheduled and interactive root prompts identically; it
does not inspect an Automation-origin marker. Research, review, draft, and
assisted tasks contain no `$tcx-order-allow`. Only a task whose every run is
explicitly allowed to perform one final effect begins with the canonical plain
first-meaningful-line mode above, and every scheduled turn receives a fresh
grant decision. Automation stores plain tokens rather than workspace-dependent
skill links.

## Workspace Viewer

The web viewer inspects projected skills, strategies, optional role skills,
artifacts, and system posture for one registered workspace. It never invokes a
skill or selects an Investment Brain. All workflow dispatch, follow-up, and
explicit `$investment-brain-*` selection remain native Codex behavior.

For a healthy compatible service, the `SessionStart` hook exposes the read-only
Viewer and Wiki links in a Codex system message. This replaces a dedicated
dashboard skill: native Codex handles ordinary viewing and workspace questions,
while operational diagnosis and recovery remain `tcx-server` responsibilities.

## Validation

Validate the nine-role fixed roster and projections, 33 skill bundles, absence
of raw public execution-mutation tools, protected grant-tool proof behavior,
deterministic native-action and `$tcx-order-allow` hook behavior,
native role-profile dispatch, bounded evidence fallback, exact independent risk/judgment review,
multilingual analysis requests,
principal-bound artifacts, lineage, dynamic revision, viewer read boundaries,
exact explicit Brain selection/failure behavior, typed conflict
handling, blind-first memory use, and unchanged execution gates. See
[Codex-Native Orchestration](codex-native-orchestration.md) and
[Harness](harness.md).

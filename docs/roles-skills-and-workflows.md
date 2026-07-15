# Roles, Skills, And Workflows

This document owns v1 role identity, skill responsibility, dynamic investment
orchestration, handoffs, overlays, and execution separation.

## Responsibility Split

| Surface | Owns | Does not own |
| --- | --- | --- |
| Head Manager prompt | coordinator identity, plane routing, hard stops, exact V2 dispatch discipline | role methods, execution authorization |
| `tcx-workflow` skill | request interpretation, smallest-team judgment, parallel waves, artifact-driven revision, synthesis procedure | durable role eligibility, MCP capability, approval |
| Investment Brain plugin | platform-neutral hypotheses, inquiry priorities, causal frames, interpretation, falsifiers, and abstention heuristics | role selection, tools, workflow, memory, policy, approval, execution |
| Fixed-role TOML | role identity, Sol/Terra policy, reasoning, sandbox, web posture, role instructions, MCP principal | cross-role scheduling |
| Role skills | domain procedure and output quality | role identity or authority |
| Hooks | health/run context, exact-role spawn checks, audit, tool policy, immediate native actions, and `$tcx-order-allow` turn-grant issue/revocation/proof injection | natural-language routing, lane/team/DAG selection, or prose-scope enforcement |
| Django services/MCP | run provenance, principal/tool checks, artifact lineage, policy/order/approval/broker/execution/audit state; one protected turn-grant consumer plus no raw final mutation | investment research orchestration or model-granted execution authority |

The current Codex authoring contract follows the official
[skills](https://learn.chatgpt.com/docs/build-skills),
[subagents](https://learn.chatgpt.com/docs/agent-configuration/subagents),
[hooks](https://learn.chatgpt.com/docs/hooks), and
[prompting](https://learn.chatgpt.com/docs/prompting) guidance:

- skill metadata stays concise enough for discovery, while detailed procedures
  and references load only after invocation;
- independent children receive compact task-local briefs with no full-history
  fork, and write-heavy coordination remains centralized;
- every matching hook can run concurrently, so service authorization never
  depends on one hook suppressing another; exact project-hook trust is part of
  release acceptance; and
- durable instructions describe outcome, context, boundaries, and completion
  evidence without scripting every judgment step.

These are product-shape inputs, not claims that prose creates authority. Native
permissions, exact role config, authenticated MCP/service checks, and artifact
receipts remain authoritative.

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

Head Manager uses `gpt-5.6-sol`/`xhigh`. Analytical children use
`gpt-5.6-terra`/`high`. All use the shared `trading-research` profile: ordinary
user-owned paths outside `trading/` may be used as workflow inputs or outputs,
while `trading/`, generated controls, credentials, and runtime state remain
protected. Only evidence-producing roles receive live web search. Final
provider effects are not a role and run through the deterministic service
gateway rather than an execution model.

## Dynamic Workflow

Head Manager reads the user's original language directly. It does not ask a
hook or Django classifier to translate the request into a lane.

1. Call `begin_analysis_run` once to seal request and overlay provenance.
2. If one exact Investment Brain is selected, apply it as an inquiry and
   interpretation overlay, then translate its domain questions into role-owned
   work. Use the pristine baseline when none is selected.
3. Choose the smallest useful first wave by distinct role expertise.
4. Spawn independent roles in parallel with exact V2 identity and no history fork.
5. Wait for authenticated artifacts.
6. Read exact returned artifact IDs and reassess the next useful question.
7. Revise the owning role, add a distinct role, request independent judgment, stop, or synthesize.
8. Save a run-local synthesis with exact consumed input artifact IDs and
   service-derived overlay lineage.

Head synthesis is itself a strict research artifact. Every material markdown
claim carries a `[factual]`, `[inference]`, or `[assumption]` tag rather than
leaving claim type implicit in headings or prose structure.

Broad analysis is not a fixed template. A factual company profile may need one
fundamental role. A near-term market forecast may begin with macro, technical,
and news roles. A recommendation or portfolio/risk decision usually needs
independent judgment. Evidence can change the next role.

Explicit negations and constraints are binding. Ambiguity is resolved by Head
Manager only when it materially changes the requested outcome or sensitive
authority. No server candidate-role ceiling or mandatory analytical DAG exists.

## Spawn And Wait

Every assignment must include:

- exact registered `agent_type`;
- compact underscore-only `task_name`;
- compact message containing the analysis run id, original outcome, role-owned question, constraints, and exact upstream artifact IDs;
- `fork_turns="none"`.

Each follow-up is a fresh child. Do not use `followup_task`, generic/default
agents, full-history forks, role-config or generated-state discovery, or
model/reasoning overrides. A child may read the exact role-owned and shared
skill documents already enabled in its projected role config, but cannot read
the config itself, another role's skill, or use that exception for general
shell execution. Role instructions use one `cat path ...` call for one or more
permitted documents so loops, redirects, pipelines, substitutions, and
executable compounds remain unnecessary and fail closed. If exact role
selection is unavailable, stop in
`waiting_for_subagent_dispatch` with briefs.

## Handoffs And Artifacts

Each producing role writes its own report through `create_research_artifact`.
Required quality includes source/as-of posture, non-empty readiness label,
context and reader summaries, confidence, missing evidence, next action,
blocked actions, and explicit handoff state.

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
snapshots, point-in-time cutoffs, forecasts, calibration, Decision Quality
Spine fields, and anti-overfit validation remain available as appropriate.

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
- Host-global/plugin skills require explicit user selection or managed
  activation; pristine Strategy authoring does not auto-load one.
- A native run selects at most one exact `$strategy-*` skill.
- A native run selects at most one exact `$investment-brain-*` skill. Selection
  is explicit-only; plain-language resemblance never activates one, and
  multiple or unresolved selections fail before analysis.
- Investment Brains remain Head Manager-level, platform-neutral, high-freedom
  inquiry/interpretation overlays. They may not name roles, dispatch agents,
  call tools, prescribe workflow, retrieve or modify memory, or widen policy,
  approval, broker, order, or execution authority.
- Head Manager translates Brain questions into the smallest useful dynamic
  fixed-role team. It sends derived questions rather than the Brain body to
  children.
- Brain, Strategy, and Investor Context bindings are sealed into the analysis
  run and cannot be replaced mid-run. The Brain binding includes the projected
  skill-tree digest; optional Markdown references are readable only from that
  exact session-bound projection. A different Brain or Strategy starts a new
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

Every tool-using `$tcx-brain` operation runs in a root native
`trading-research` prompt whose exact physical first line is `$tcx-brain`.
The hook-issued Brain-scoped grant admits only the canonical source and
lifecycle paths; it never elevates the sandbox, crosses into Build or Strategy,
or carries into a follow-up or subagent. Source edits remain native; installed
registry and projection lifecycle uses only the proof-protected
`manage_investment_brain` MCP tool, with the Research runtime and launcher
still denied. The browser viewer has no management
path. Source authoring curates exact user-selected Decision
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
available from a root native Codex user turn whose complete prompt matches one
exact action-only skill invocation:

```text
$tcx-order-submit --ticket-id <ticket-id> --approval-receipt-id <approval-receipt-id>
$tcx-order-submit --ticket-id <ticket-id> --approval-receipt-id <approval-receipt-id> --live-confirmation <token>
$tcx-order-cancel --ticket-id <ticket-id> --broker-order-id <broker-order-id> --approval-receipt-id <approval-receipt-id>
$tcx-order-cancel --ticket-id <ticket-id> --broker-order-id <broker-order-id> --approval-receipt-id <approval-receipt-id> --live-confirmation <token>
```

These two root skill bundles describe a protocol and disable implicit
invocation; they carry no MCP or broker authority. `UserPromptSubmit` recognizes
the reserved leading token, rejects malformed or subagent forms,
parses the complete prompt deterministically, creates a workspace-bound
`native-user` mandate, and calls the canonical service gateway in-process before
an analysis run begins.

For a workflow that creates or selects identifiers during the turn, the
physical first line must instead be exact:

```text
$tcx-order-allow --mode paper
$tcx-order-allow --mode validation
$tcx-order-allow --mode live
```

The hook requires a root native Codex `session_id` and `turn_id`, issues one
grant bound to workspace, session, turn, complete prompt hash, Codex permission
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
explicitly allowed to perform one final effect begins with the exact first-line
mode above, and every scheduled turn receives a fresh grant decision.

## Workspace Viewer

The web viewer inspects projected skills, strategies, optional role skills,
artifacts, and system posture for one registered workspace. It never invokes a
skill or selects an Investment Brain. All workflow dispatch, follow-up, and
exact `$investment-brain-*` invocation remain native Codex behavior.

`tcx-dashboard` is the native read-only viewer entrypoint and user overview of
the same canonical workspace domains. It opens the viewer in the Codex in-app
browser by default and uses an external browser only when the user explicitly
requests one. It uses Head Manager's existing read-only MCP tools, reports only
recorded state, and routes detail to the relevant viewer destination. It does not call
`begin_analysis_run`, dispatch roles, create artifacts, perform investment
judgment, or mutate state. Operational diagnosis and recovery remain
`tcx-server` responsibilities.

## Validation

Validate the nine-role fixed roster and projections, 31 skill bundles, absence
of raw public execution-mutation tools, protected grant-tool proof behavior,
deterministic native-action and `$tcx-order-allow` hook behavior,
exact V2 dispatch, multilingual analysis requests,
principal-bound artifacts, lineage, dynamic revision, viewer read boundaries,
exact explicit Brain selection/failure behavior, typed conflict
handling, blind-first memory use, and unchanged execution gates. See
[Codex-Native Orchestration](codex-native-orchestration.md) and
[Harness](harness.md).

# Artifact Supervisor Loop PRD

Status: draft  
Owner surface: Harness Improvement, fixed-role dispatch, workflow quality,
research memory, generated workspace templates  
Related docs: `harness.md`, `roles-skills-and-workflows.md`,
`research-memory-and-artifacts.md`, `improvement-loop.md`,
`validation-and-test-plan.md`, `generated-workspaces.md`

## Problem

TradingCodex already routes investment work through a `head-manager`, fixed
subagents, source-aware artifacts, handoff states, and approval/execution gates.
The current workflow is still too close to a one-pass sequence:

```text
intake -> selected subagents -> role artifacts -> head-manager synthesis
```

That misses an important property of real research: one role's artifact can
change the right next question for another role. A news artifact can reveal a
material driver. A technical artifact can expose a regime shift. A valuation
artifact can depend on stale or contradictory inputs. A risk artifact can block
decision support because the upstream evidence is not decision-ready.

TradingCodex needs a first-class loop, but not an unconstrained agent chat
loop. The system must turn role artifacts into bounded, auditable follow-up
work while preserving selected-team binding, no-overlap role ownership,
context discipline, approval gates, execution boundaries, and user scope.

## Goals

- Replace one-pass dispatch with a bounded, artifact-driven supervisor loop.
- Let role artifacts propose follow-up work without letting subagents directly
  command other subagents.
- Let `head-manager` verify artifacts, queue delta follow-ups, request same-role
  revisions, preserve conflicts, propose lane escalation, or stop.
- Add lane-aware loop policy to routing output and generated hook context.
- Split role eligibility into initial dispatch, allowed follow-up, and
  escalation-only sets.
- Extend research artifact metadata with explicit follow-up requests, trigger
  types, materiality, suggested consent posture, and loop provenance.
- Store loop state as file-native workspace state for Codex inspection,
  context-budget audit, product web preview, concurrent Codex app threads, and
  postmortems.
- Keep all follow-up briefs compact: artifact path, context summary,
  trigger, delta question, constraints, and blocked actions.
- Preserve Decision Quality Spine expectations inside the loop rather than
  keeping them as a separate monolithic PRD.
- Add tests and smoke checks that prove loops improve quality without widening
  authority.

## Non-Goals

- No free-form multi-agent chat mesh.
- No subagent-to-subagent direct dispatch.
- No head-manager direct investment analysis before accepted role artifacts.
- No automatic lane expansion outside the routed lane without explicit policy
  permission or user-visible escalation.
- No weakening of `research_only`, negated scope, selected-team binding,
  information barriers, MCP allowlists, policy gates, approval gates, execution
  gates, or secret boundaries.
- No loop-driven order approval, execution, broker access, or raw secret access.
- No new frontend framework, Node runtime, or unrelated dependency.
- No hidden prompt drift; durable behavior must live in docs, services,
  templates, skills, schemas, hooks, and tests.
- No language-specific durable routing aliases outside a reviewed localization
  layer.

## Related Agent Loop Patterns

The design takes the useful part of modern agent-loop practice and rejects the
unsafe part for investment workflows.

- ReAct shows the value of interleaving reasoning and actions against external
  evidence sources: <https://arxiv.org/abs/2210.03629>.
- Self-Refine shows that iterative feedback and refinement can improve outputs
  at test time: <https://arxiv.org/abs/2303.17651>.
- Reflexion shows the value of feedback records and episodic learning for
  agents: <https://openreview.net/forum?id=vAElhFcKW6>.

TradingCodex should not copy these patterns as open-ended self-reflection. It
should implement a product-specific loop where artifacts create explicit
triggers, `head-manager` acts as the supervisor, and service-layer policy
decides what can be queued, escalated, blocked, or synthesized.

## Product Model

The loop is a supervised artifact state machine:

```text
user request
  -> intake / lane routing
  -> initial dispatch queue
  -> role artifacts
  -> head-manager verification
  -> loop planner
       -> same-role revise
       -> selected-team follow-up
       -> downstream handoff
       -> conflict challenge
       -> lane escalation proposal
       -> synthesize / waiting / blocked
```

The loop does not replace the existing handoff states. It makes them runtime
inputs. Artifact handoff states are not workflow terminal states:

| Handoff state | Loop meaning |
| --- | --- |
| `accepted` | Artifact can be consumed, synthesized, or used to trigger a bounded follow-up. |
| `revise` | Owning role must repair missing evidence, stale source posture, scope mismatch, or artifact quality. |
| `blocked` | The workflow must preserve the block reason and avoid downstream synthesis that depends on the blocked claim. |
| `waiting` | Required role output does not exist yet; head-manager may provide task briefs but no substantive synthesis. |

Workflow terminal actions are separate from artifact states:

| Terminal workflow action | Meaning |
| --- | --- |
| `synthesize` | Exit criteria are met and synthesis can use accepted artifacts only. |
| `blocked` | The workflow cannot proceed without violating policy, role boundaries, or evidence quality. |
| `waiting` | Required artifacts, user consent, or role outputs are absent. |
| `lane_escalation_proposal` | Useful next work is outside the current lane or consent posture. |

## Role-Team Model

Current routing exposes one selected team. Multi-round workflows need three
sets:

```yaml
selected_team:
  - roles spawned during initial dispatch
allowed_followup_team:
  - roles that may receive loop follow-up inside the current lane
escalation_team:
  - roles that may be proposed but not spawned without lane escalation or user consent
```

Rules:

- `selected_team` remains the initial dispatch contract.
- `allowed_followup_team` may include selected roles and lane-authorized
  conditional roles, but it is computed only during request classification.
- `escalation_team` is advisory only; it can produce a lane escalation proposal,
  not automatic dispatch.
- Loop iterations must not add new roles to `allowed_followup_team`.
- Explicit negations remove roles and actions from `selected_team`,
  `allowed_followup_team`, and `escalation_team`.
- Strategy context may explain an escalation proposal, but it must not directly
  expand `allowed_followup_team`.
- `research_only` must not silently add valuation, portfolio, risk, approval,
  or execution roles.
- Negated scope always wins before follow-up policy is applied.
- Execution-sensitive lanes use loop policy for verification only; they do not
  use loops to discover new trade ideas or expand analysis scope.

## Loop Triggers

Role artifacts may expose follow-up triggers. The initial required trigger set
is intentionally small:

| Trigger | Meaning |
| --- | --- |
| `coverage_gap` | Required evidence, method coverage, or role-owned work is missing. |
| `freshness_gap` | Source date, retrieved-at posture, market data, or filing/news freshness is inadequate. |
| `contradiction` | Artifacts conflict, or one artifact contradicts a key assumption used downstream. |
| `material_driver` | A new driver may affect thesis, valuation, portfolio fit, or risk posture. |
| `assumption_change` | A scenario, model input, or base/bear/bull case may need revision. |
| `method_gap` | The requested method needs anti-overfit, numeric QC, valuation, scenario, or source validation not yet present. |
| `scope_boundary` | The useful next role is outside the current lane or selected/allowed team. |
| `forecast_gap` | Prediction scope lacks horizon, base rate, target, probability posture, or resolution source. |
| `profile_gap` | Portfolio/risk/sizing support lacks investor profile, constraints, account state, or strategy context. |

Triggers do not automatically create work. They are inputs to the loop planner.

## Artifact Contract

Research artifacts should keep the existing handoff metadata and add structured
follow-up requests when a role believes another pass would materially improve
the workflow:

```yaml
follow_up_requests:
  - trigger: material_driver
    suggested_role: valuation-analyst
    question: "Assess whether the material driver changes existing scenario assumptions."
    reason: "The artifact identified a driver not yet reflected in accepted valuation work."
    materiality: high
    requested_by_role: news-analyst
    created_at: "2026-06-30T00:00:00Z"
    source_artifact_id: example-news-20260630
    source_artifact_path: trading/research/example.news.md
    source_artifact_version: 2
    source_artifact_content_hash: "sha256:..."
    trigger_evidence_refs:
      - source_snapshot_id: news-source-001
      - claim_ref: "body:material-driver-1"
    required_inputs:
      - accepted news artifact
      - prior valuation artifact path if present
    suggested_consent_posture: no_consent_expected
    blocked_actions:
      - order_execution
```

The artifact request is not authoritative about lane scope or consent. The
planner must recalculate those fields from routing policy:

```json
{
  "planner_decision": "follow_up_existing_team",
  "policy_within_current_lane": true,
  "policy_requires_user_consent": false,
  "policy_reason": "valuation-analyst is in allowed_followup_team for thesis_review"
}
```

Contract rules:

- `question` must be delta-only and role-owned.
- `suggested_role` must be a fixed TradingCodex role.
- `reason` must cite the artifact finding that created the trigger.
- `materiality` is one of `low`, `medium`, or `high`.
- `suggested_consent_posture` is advisory only; the planner computes
  authoritative consent and lane scope.
- Follow-up requests must not request approval, execution, broker secrets, raw
  broker API access, or policy changes.
- `next_recipient` remains supported for a single obvious handoff; use
  `follow_up_requests` for multiple or conditional follow-ups.

## Loop Planner

`head-manager` runs artifact evaluation before loop planning. Artifact
evaluation uses this closed result set:

| Artifact evaluation | Use when |
| --- | --- |
| `accept_artifact` | Artifact is role-owned, source-aware, and ready for downstream use or trigger evaluation. |
| `revise_artifact` | Owning role stayed in bounds but must repair quality gaps. |
| `block_artifact` | The artifact identifies a block that prevents dependent workflow progress. |
| `wait_for_artifact` | Required role output does not exist yet. |

The planner then chooses from this closed action set:

| Planner action | Use when |
| --- | --- |
| `revise_same_role` | Owning role stayed in bounds but must repair quality gaps. |
| `follow_up_existing_team` | A delta question can be sent to selected or allowed follow-up roles. |
| `challenge_conflict` | Accepted artifacts materially conflict and need a targeted challenge pass. |
| `downstream_handoff` | Existing lane order requires a later role to consume accepted artifacts. |
| `lane_escalation_proposal` | Useful next role or action is outside the current lane or needs consent. |
| `blocked` | Policy, scope, stale evidence, unsupported instrument, or missing data blocks progress. |
| `waiting` | Required artifacts or role outputs are absent. |
| `synthesize` | Exit criteria are met and synthesis can use accepted artifacts only. |

The planner must not create new analysis content. It only decides how the
workflow should move based on artifacts, route policy, and blocked actions.

`challenge_conflict` can be sent only to the roles that own the conflicting
artifacts and the downstream role that must consume them. It must not add an
unrelated role under the cover of conflict review.

`queue` has phase-specific meaning:

- Phase 2 queue means an inspectable pending task record plus a generated
  delta brief for `head-manager`; hooks and services do not automatically spawn
  subagents.
- Phase 3 queue may compute controlled planner actions and delta briefs, but
  it still must use the explicit Codex fixed-role spawn path and must not create
  recursive hook dispatch.

The loop planner may flag `forecast_gap` and route a role-owned follow-up. It
must not create an empty forecast ledger task without an eligible role artifact
or explicit user forecast request.

## Loop Policy

Routing should produce lane-specific loop policy. Initial defaults:

```yaml
loop_policy:
  max_iterations: 3
  max_followups_per_iteration: 2
  max_same_role_revisions: 1
  max_total_subagent_tasks: 8  # includes initial dispatch, revisions, challenge passes, and follow-ups
  max_loop_subagent_tasks: 4   # excludes initial dispatch
  require_user_consent_for_lane_escalation: true
  synthesize_on_budget_exhaustion: false
  terminal_workflow_actions:
    - synthesize
    - blocked
    - waiting
    - lane_escalation_proposal
  artifact_handoff_states:
    - accepted
    - revise
    - blocked
    - waiting
```

Lane defaults may tighten these values:

| Lane family | Default loop posture |
| --- | --- |
| `research_only` | Up to 2 iterations; follow-up inside research roles only; valuation/portfolio/risk become escalation proposals. |
| `thesis_review` | Up to 3 iterations; challenge pass allowed for material conflicts and scenario assumptions. |
| `portfolio_risk_review` | Up to 3 iterations; profile gaps remain visible; no order approval or execution. |
| `order_ticket_draft_gate` | Up to 2 verification iterations; no execution and no new thesis expansion. |
| `order_ticket_approval_execution_gate` | Verification-only; all service-layer approval, duplicate, connection, policy, and audit gates remain authoritative. |
| `connector_build` | Investment subagents are not dispatched; build-plane rules apply instead. |

Budget exhaustion is not success. If the loop runs out of allowed iterations or
task budget without meeting exit criteria, the result is `waiting`, `blocked`,
or a lane escalation proposal, not a forced conclusion.

## Context Contract

Every loop follow-up brief must be compact:

- original request
- current lane and constraints
- source artifact path
- source artifact `context_summary`
- exact trigger and reason
- delta question
- allowed inputs
- expected handoff state
- blocked actions

Follow-up briefs must not paste full prior artifacts, full source dumps, long
chat history, role manuals, or unrelated evidence. Downstream roles should
open full artifacts only when the summary and path are insufficient.

## Workspace State

Add file-native loop state records:

```text
.tradingcodex/mainagent/workflow-loop-state.json                    # latest compact summary / pointer
.tradingcodex/mainagent/workflows/<workflow_run_id>/loop-state.json # canonical run state
.tradingcodex/mainagent/workflows/<workflow_run_id>/prompt-gate.json
.tradingcodex/mainagent/session-workflow-runs.json                  # Codex session/thread -> workflow_run_id map
```

The latest file is intentionally compact so Codex can inspect the current loop
without loading every historical run. The canonical run file is the durable
state for a specific workflow. This prevents two Codex app threads in the same
workspace from clobbering each other's loop state when prompts are routed at
nearly the same time.

Canonical run-state shape:

```json
{
  "workflow_run_id": "workflow-20260630T000000Z",
  "lane": "thesis_review",
  "state_path": ".tradingcodex/mainagent/workflows/workflow-20260630T000000Z/loop-state.json",
  "latest_state_path": ".tradingcodex/mainagent/workflow-loop-state.json",
  "session_key": "session_id:codex-thread-1",
  "iteration": 1,
  "loop_policy": {},
  "selected_team": [],
  "allowed_followup_team": [],
  "escalation_team": [],
  "pending_tasks": [],
  "completed_artifacts": [],
  "loop_decisions": [],
  "escalation_proposals": [],
  "blocked_actions": [],
  "stop_reason": ""
}
```

The compact latest summary mirrors the current run id, canonical `state_path`,
pending tasks, completed artifact summaries, planner decisions, escalation
proposals, blocked actions, and stop reason. Full event history remains
append-only audit JSONL.

Subagent continuation is tracked by run id, role, and subagent session id. A
subagent start can therefore mean either a new role-local session or a
continuation/reuse of an active role session, without merging unrelated Codex
threads that happen to use the same role.

## Service And Template Requirements

Update these surfaces together:

- `tradingcodex_service/application/harness.py`
  - routing output includes `loop_policy`, `allowed_followup_team`,
    `escalation_team`, and exit criteria.
  - starter prompt and hook context expose compact loop metadata.
- `tradingcodex_service/application/research.py`
  - artifact export/import preserves `follow_up_requests`.
- `tradingcodex_service/application/artifact_quality.py`
  - strict quality checks validate follow-up request shape.
- `workspace_templates/modules/repo-skills/files/.agents/skills/tcx-workflow/SKILL.md`
  - procedure includes artifact intake, loop planner, delta follow-up, lane
    escalation proposal, and stop conditions.
- Generated workspace hooks
  - maintain run-specific loop state plus the compact latest pointer.
  - map Codex session/thread ids to workflow run ids for concurrent app
    threads in the same workspace.
  - preserve subagent hook isolation and prevent recursive dispatch.
- CLI
  - `./tcx subagents plan` should show initial dispatch, allowed follow-up,
    escalation-only roles, pending tasks, and stop reason.
  - `./tcx doctor --layer improvement` should check loop state and artifact
    follow-up contract.
- Product web
  - preview loop state, follow-up requests, escalation proposals, and blocked
    actions without spawning agents, approving orders, or executing orders.
  - first release escalation proposals are read-only; escalation approval stays
    Codex-prompt or CLI driven with explicit user wording captured in audit.

## Safety Requirements

- Subagents may propose follow-ups but cannot dispatch them.
- `head-manager` may queue follow-ups only inside routing policy.
- Lane escalation must be user-visible when it widens the team, scope, or
  decision posture.
- Research-only lanes never become valuation, portfolio, risk, approval, or
  execution lanes by loop drift.
- Natural language never creates approval or execution authority.
- Blocked actions from routing remain blocked after every loop iteration unless
  an explicit service-layer gate changes them.
- Conflict handling must preserve unresolved disagreement instead of averaging
  artifacts into false consensus.
- Weak upstream artifacts return `revise`, `blocked`, or `waiting`; downstream
  roles must not fill missing upstream analysis outside their owned question.
- Follow-up requests that touch secrets, raw broker APIs, policy mutation,
  approval mutation, or execution are invalid.

## Implementation Plan

### Phase 1: Contracts And Routing Metadata

- Add loop policy, allowed follow-up team, escalation team, and exit criteria
  to routing summaries.
- Add `follow_up_requests` to artifact schema and quality checks.
- Update `tcx-workflow` and Decision Quality Spine reference text.
- Add docs and tests for trigger validation and selected-team preservation.
- Do not add runtime enqueue or automatic subagent spawn.

### Phase 2: Inspectable Assisted Loop

- Write canonical run state under
  `.tradingcodex/mainagent/workflows/<workflow_run_id>/loop-state.json` and a
  compact latest pointer at `.tradingcodex/mainagent/workflow-loop-state.json`.
- Track pending tasks, completed artifacts, planner actions, escalation
  proposals, and stop reason.
- Track session/thread to workflow-run mapping so multiple Codex app threads can
  run in one attached workspace without overwriting each other's active loop.
- Extend `./tcx subagents plan`, product web read-only preview, and improvement
  doctor checks.
- Generate pending task records and delta briefs for `head-manager`.
- Do not let hooks or services automatically spawn subagents.

### Phase 3: Controlled Runtime Planner

- Implement loop planner helpers that read artifacts and return one of the
  closed planner actions.
- Compute policy-owned lane scope and consent fields.
- Enqueue delta follow-up briefs inside selected/allowed team through the
  explicit fixed-role spawn path.
- Generate lane escalation proposals outside allowed team.
- Enforce task and iteration budgets.
- Preserve recursive-dispatch prevention in hooks.

### Phase 4: Product Web And Postmortems

- Show loop graph, trigger list, follow-up status, escalation proposals, and
  blocked actions in read-only web views.
- Feed blocked/revised/escalated loops into postmortem and skill proposal
  workflows.

## Validation

Minimum tests:

- Artifact with `follow_up_requests` to a role inside `allowed_followup_team`
  records a policy-approved delta follow-up.
- Artifact with `follow_up_requests` to a role outside allowed team creates
  `lane_escalation_proposal` only.
- `research_only` cannot auto-add valuation, portfolio, risk, approval, or
  execution through loop triggers.
- `handoff_state=revise` sends only the owning role a revision brief.
- `handoff_state=blocked` preserves block reason and prevents dependent
  synthesis.
- Material conflict creates a challenge pass for owning roles without adding
  unrelated roles.
- Loop budget exhaustion returns `waiting` or `blocked`, not unsupported final
  synthesis.
- `blocked_actions` survive every loop state transition.
- `accepted` is validated as an artifact handoff state, not a terminal workflow
  state.
- Artifact-provided consent posture is advisory; planner-computed policy fields
  are authoritative.
- Invalid follow-up requests fail strict artifact quality checks.
- Generated workspace smoke proves hook context, latest loop summary, canonical
  run loop state, role artifacts, and `tcx-workflow` instructions load together.
- Two routed prompts with different Codex session/thread ids produce different
  canonical loop-state paths, and a subagent event for one session does not
  mutate the other session's pending tasks.

Required smoke after implementation:

```bash
rm -rf /tmp/tradingcodex-loop-smoke
python -m tradingcodex_cli attach /tmp/tradingcodex-loop-smoke
cd /tmp/tradingcodex-loop-smoke
./tcx doctor
./tcx doctor --layer improvement
./tcx subagents plan "Analyze NVDA. No order, no trading."
printf '{"prompt":"Analyze NVDA. No order, no trading.","session_id":"thread-a"}\n' | python .codex/hooks/tradingcodex_hook.py user-prompt-submit
printf '{"prompt":"Analyze AAPL. No order, no trading.","session_id":"thread-b"}\n' | python .codex/hooks/tradingcodex_hook.py user-prompt-submit
./tcx skills list --all
```

Codex CLI smoke should verify that `head-manager` reports dispatch or waiting
status, does not produce direct investment analysis before artifacts, preserves
selected-team limits, and exposes loop state or escalation proposal when
follow-up is needed.

## Acceptance Criteria

- Investment workflows are no longer limited to one artifact pass.
- Follow-up work is generated from artifacts, not hidden head-manager analysis.
- Subagents can propose but not dispatch cross-role work.
- `head-manager` can queue, revise, challenge, escalate, block, wait, or
  synthesize using a closed action set.
- Loop state is visible in workspace files and audit trails.
- Context stays compact across iterations.
- Decision Quality Spine fields still apply inside selected lanes.
- Approval, execution, broker, secret, and policy boundaries are unchanged.
- Generated workspace validation catches loop drift, unauthorized expansion,
  malformed follow-up requests, and unsupported synthesis.

## Open Questions

- Should `allowed_followup_team` be computed entirely from lane metadata, or
  can future reviewed strategy policy add non-execution conditional follow-up
  roles before classification completes?
- Should older canonical run files be retained forever, capped by age/count, or
  exported through a workspace cleanup command?
- Should follow-up materiality affect task priority only, or also loop budget?

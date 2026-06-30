# Harness

Harness is the top-level TradingCodex concept. It is the operating system for
Codex-native investment workflows: roles, skills, service-layer state, policy,
MCP tools, research memory, artifacts, approvals, execution adapters, audit,
and feedback loops all live inside the harness.

TradingCodex is therefore not just a set of guardrails. Guardrails are one
subsystem of the harness. Improvement is another subsystem.

TradingCodex is implemented and maintained through harness components.
Guardrails and Improvement are taxonomy views over those components, not
implementation buckets. A component can carry multiple taxonomy tags when it
spans guidance, enforcement, information barriers, workflow quality, research
memory, or validation feedback.

## Top-Level Model

```text
TradingCodex Harness
  -> Components
       -> investment-request-routing
       -> fixed-role-dispatch
       -> context-efficiency-contract
       -> responsibility-boundary-contract
       -> approval-gate
       -> execution-boundary
       -> research-memory
       -> ...
  -> Guardrails
       -> Guidance guardrails
       -> Enforcement guardrails
       -> Information barriers
  -> Improvement
       -> Workflow quality
       -> Research memory and source freshness
       -> Skill proposals
       -> Postmortems
       -> Validation/test feedback
```

## What The Harness Owns

| Area | Harness responsibility |
| --- | --- |
| Roles | Keep one `head-manager` and nine fixed subagents as the default coordination model. |
| Skills | Keep core role-owned skills locked and file-native, expose direct user entrypoints, support `strategy-*` strategy skills, and let `head-manager` manage role-local optional skills through workspace files while Django shows status only. |
| State | Keep execution-sensitive runtime state in the central Django DB, while Codex-native agent, skill, and research handoff state is workspace-file state. |
| Interfaces | Expose Web, Admin, REST, CLI, and MCP as service-layer callers. |
| Guardrails | Reduce, restrict, or block risky actions through guidance, enforcement, and information barriers. |
| Improvement | Raise workflow quality through no-overlap handoff contracts, quality gates, artifact readiness, research memory, postmortems, and test feedback. |
| Approved action boundary | Keep executable actions behind policy, approval, duplicate-request, connection, and audit checks. |
| Decision packages | Keep investment ideas Codex-native by packaging workflow plans, artifact paths, profile gaps, blocked actions, and next allowed actions as workspace markdown. |
| Provenance | Record which workspace and role produced or requested work without making workspaces separate ledgers. |
| Profiles | Separate paper portfolio/account/strategy state from workspace identity. |
| Components | Provide the developer-facing maintenance map for implementation surfaces, dependencies, capabilities, tags, and validation. |
| Context efficiency | Keep subagent briefs compact, pass artifact paths and context summaries before full artifacts, audit long runs with `subagents context-audit` over prompt-gate history, and avoid repeated role manuals or source dumps. |
| Responsibility boundaries | Keep role identity, MCP allowlists, permission profiles, hooks, policies, skills, schemas, and service behavior in their own authoritative surfaces. |

## Components As Maintenance Units

The canonical component registry lives in
`tradingcodex_service.application.components`. Each component declares:

- stable id, label, summary, and status
- descriptive taxonomy tags such as `guardrail.guidance` or
  `improvement.workflow_quality`
- implementation surfaces such as instructions, skills, hooks, services,
  templates, models, MCP tools, and tests
- dependencies, owned capabilities, and validation expectations

Generated workspace modules remain deployment projections. They are not the
source of conceptual ownership. Generated workspaces receive
`.tradingcodex/generated/component-index.json` from the Python registry.

When a change crosses surfaces, update the component rather than duplicating
logic. For example, role identity belongs in role TOML and service registries;
skill bodies describe procedures; hooks classify and write guidance context;
information-barrier policy files describe file/tool walls; services enforce
durable behavior.

## Guardrails Under Harness

Guardrails answer: "What should be prevented, reduced, isolated, or blocked?"
They are tags and review lenses applied to components.

- Guidance guardrails shape agent behavior before risky action.
- Enforcement guardrails deterministically block final risky action paths.
- Information barriers limit role knowledge, file access, secrets, and tool surfaces.

Guardrails never replace the need for improvement. A blocked action can still
leave behind a useful postmortem, skill proposal, or validation scenario.

## Improvement Under Harness

Improvement answers: "How does the next workflow become higher quality?"
It is a tag and review lens applied to components.

- Workflow maps route work to the right role team.
- Quality gates define evidence, source/as-of posture, claim discipline, handoff acceptance, and readiness.
- Handoff contracts keep downstream roles from filling missing upstream work outside their owned question.
- Research memory preserves workspace markdown artifacts, versions, source posture, and source snapshots.
- Skill proposals let the harness evolve without hidden prompt drift.
- Postmortems turn rejected orders, failed checks, and thesis changes into process improvements.
- Validation tests convert recurring mistakes into regression coverage.
- Context efficiency keeps those quality gates usable by passing summaries and
  artifact references first, then opening full evidence only when needed.
- Context-budget audits make long multi-subagent runs inspectable by checking
  compact hook context, prompt-gate history, starter prompt size, bounded
  subagent session state, and `context_summary` coverage across research
  artifacts. Full subagent event history stays in append-only audit JSONL.

Improvement does not authorize execution. A high-quality report still needs the
guardrail path before any draft, approval, or non-live connection use.

The Artifact Supervisor Loop is part of Improvement, not a new monolithic
workflow. It turns accepted, revised, blocked, and waiting artifacts into
bounded follow-up, challenge, escalation, or synthesis decisions. The Decision
Quality Spine remains the cross-lane quality contract inside that loop. Neither
the loop nor the spine widens role authority, MCP access, approval, execution,
broker, or secret boundaries.

Runtime loop inspection is file-native and read-first. Hooks write canonical
per-run state under
`.tradingcodex/mainagent/workflows/<workflow_run_id>/loop-state.json` and a
compact latest summary at `.tradingcodex/mainagent/workflow-loop-state.json`;
`tcx subagents plan` shows the current selected team, allowed follow-up team,
escalation-only roles, pending tasks, stop reason, and canonical state path.
Codex session/thread ids are mapped through
`.tradingcodex/mainagent/session-workflow-runs.json`, so multiple Codex app
threads in the same workspace can continue different loops without overwriting
each other. `tcx subagents loop --artifact <path>` previews closed planner
actions from artifact handoff state and `follow_up_requests`. `--record` may
append the computed pending tasks and escalation proposals to the loop state and
`trading/audit/workflow-loop-events.jsonl`, but it still does not spawn
subagents, approve orders, or execute.

## Interface Implications

The product web app should make the harness usable through a workflow-planner
first screen, then an agents/skills browser for inspection: users can start from
a plain-language investment request, while head-manager and fixed subagents,
required and optional skills, and markdown bodies remain inspectable without
hand-rolled parsing. Django Admin stays on default model registration for
local/staff DB inspection; richer operations belong in product web, CLI, API, or
MCP service-layer paths. CLI checks should keep separate layers for guidance,
enforcement, information barriers, improvement, MCP, and service status.

Long workspace paths, projection hashes, component maintenance details, and
file internals belong in collapsed diagnostics unless the user opens them.

## Naming Rule

Use "harness" for the top-level operating model. Use "guardrail" only for
safety/restriction systems. Use "improvement" for quality, learning, skill,
postmortem, and validation loops.

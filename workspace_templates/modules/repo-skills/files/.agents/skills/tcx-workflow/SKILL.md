---
name: tcx-workflow
description: Coordinate TradingCodex operate-plane investment workflows by turning hook intake hints into validated staged workflow plans, dispatching fixed-role subagents from the recorded plan, evaluating artifacts, and synthesizing only after accepted role outputs.
---

# TCX Workflow

Use this skill when a user asks for investment analysis, decision support, portfolio/risk review, order drafting, approval review, or non-live execution status.

## Procedure

1. Read hook intake from `.tradingcodex/mainagent/latest-workflow-intake.json` or the hook `intake_path`. Treat `heuristic_lane`, `heuristic_roles`, and `deterministic_hint` as suggestions only.
2. Draft a staged workflow plan before dispatch. Include `workflow_run_id`, `lane`, `stages`, `blocked_actions`, `user_constraints`, `decision_quality_flags`, `profile_gaps`, `artifact_requirements`, `stop_condition`, and `planner_rationale`.
3. Each stage must include `stage_id`, `roles`, `depends_on`, `dispatch_mode`, `purpose`, and `exit_criteria`. Use only fixed TradingCodex roles.
4. Validate the plan with `./tcx workflow validate --plan <path|->`. Fix validation errors instead of dispatching around them.
5. Record the validated plan with `./tcx workflow record --plan <path|->`. The recorded plan is the run contract; hooks do not choose the final team.
6. Dispatch or reuse only roles in the next ready recorded stage. Pass compact assignment envelopes: original request, constraints, stage purpose, source artifact path, `context_summary`, expected handoff state, and blocked actions.
7. Use the Artifact Supervisor Loop after artifact intake: evaluate artifacts, then choose `revise_same_role`, `follow_up_existing_team`, `challenge_conflict`, `downstream_handoff`, `lane_escalation_proposal`, `blocked`, `waiting`, or `synthesize`.
8. Use `./tcx subagents loop --artifact <path>` to preview closed planner actions from artifacts when helpful. Queue means a compact pending task and delta brief; hooks do not recursively spawn subagents.
9. Require the Decision Quality Spine fields described in `references/decision-quality-spine.md` when they are in scope.
10. Synthesize only accepted artifacts; preserve disagreements and stop with `waiting`, `revise`, `blocked`, or `lane_escalation_proposal` when quality gates fail.

## Hard Stops

- Do not produce substantive investment analysis before required role outputs exist.
- Do not dispatch before a validated workflow plan is recorded.
- Do not treat hook hints as binding final workflow decisions.
- Do not widen the recorded staged plan without a new user request or validated plan revision.
- Do not treat artifact-proposed lane scope or consent as authoritative; recompute those from routing policy.
- Do not create approval or execution artifacts from natural language alone.
- Do not change TradingCodex build mode, policy, MCP allowlists, or broker execution posture while producing investment judgment.

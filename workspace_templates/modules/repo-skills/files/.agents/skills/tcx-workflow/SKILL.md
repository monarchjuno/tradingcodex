---
name: tcx-workflow
description: Coordinate TradingCodex operate-plane investment workflows from compact hook context without duplicating role manuals or execution policy.
---

# TCX Workflow

Use this skill when a user asks for investment analysis, decision support, portfolio/risk review, order drafting, approval review, or non-live execution status.

## Procedure

1. Read the latest hook context from `.tradingcodex/mainagent/latest-user-prompt-gate.json` when compact context is insufficient.
2. Treat `routing_status.lane`, `selected_team`, and `blocked_actions` as binding for the current turn.
3. Respect explicit constraints and negations before applying defaults.
4. Dispatch or reuse only the selected fixed-role subagents when initial role output is required.
5. Use the Artifact Supervisor Loop after artifact intake: evaluate artifacts, then choose `revise_same_role`, `follow_up_existing_team`, `challenge_conflict`, `downstream_handoff`, `lane_escalation_proposal`, `blocked`, `waiting`, or `synthesize`.
6. Treat `allowed_followup_team`, `escalation_team`, `loop_policy`, and the hook-provided canonical loop `state_path` as the assisted-loop contract. `.tradingcodex/mainagent/workflow-loop-state.json` is only the latest compact summary/pointer; per-run state lives under `.tradingcodex/mainagent/workflows/<workflow_run_id>/loop-state.json`. Use `./tcx subagents loop --artifact <path>` to preview closed planner actions from artifacts when helpful. Queue means a compact pending task and delta brief; hooks do not recursively spawn subagents.
7. Pass compact assignment envelopes: original request, constraints, lane, source artifact path, `context_summary`, trigger, delta question, expected handoff state, and blocked actions.
8. Require the Decision Quality Spine fields described in `references/decision-quality-spine.md` when they are in scope.
9. Synthesize only accepted artifacts; preserve disagreements and stop with `waiting`, `revise`, `blocked`, or `lane_escalation_proposal` when quality gates fail.

## Hard Stops

- Do not produce substantive investment analysis before required role outputs exist.
- Do not widen the selected team without a new user request.
- Do not treat artifact-proposed lane scope or consent as authoritative; recompute those from routing policy.
- Do not create approval or execution artifacts from natural language alone.
- Do not change TradingCodex build mode, policy, MCP allowlists, or broker execution posture while producing investment judgment.

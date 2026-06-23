---
name: tcx-workflow
description: Coordinate TradingCodex operate-plane investment workflows from compact hook context without duplicating role manuals or execution policy.
---

# TCX Workflow

Use this skill when a user asks for investment analysis, decision support, portfolio/risk review, order drafting, approval review, or non-live execution status.

## Procedure

1. Read the latest hook context from `.tradingcodex/mainagent/latest-user-prompt-gate.json` when compact context is insufficient.
2. Treat `routing_status.lane`, `selected_team`, and `blocked_actions` as binding for the current turn.
3. Dispatch or reuse only the selected fixed-role subagents when role output is required.
4. Pass compact assignment envelopes: original request, constraints, lane, artifact target, expected handoff state, and blocked actions.
5. Synthesize only after required artifacts exist or dispatch is unavailable and the state is `waiting`.

## Hard Stops

- Do not produce substantive investment analysis before required role outputs exist.
- Do not widen the selected team without a new user request.
- Do not create approval or execution artifacts from natural language alone.
- Do not change TradingCodex build mode, policy, MCP allowlists, or broker execution posture while producing investment judgment.

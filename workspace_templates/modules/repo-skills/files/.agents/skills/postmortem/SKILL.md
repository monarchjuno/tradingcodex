---
name: postmortem
description: "Create a TradingCodex postmortem after executed orders, rejected orders, thesis changes, or process failures. Use for audit review and workflow improvement proposals."
---

# Postmortem

Use this skill after thesis changes, rejected orders, executed paper orders, or process failures.

Expected output:

- A structured JSON postmortem report named `trading/reports/postmortem/<id>.postmortem_report.json`
- `id`, `created_by`, `created_at`, and `trigger`
- `findings` entries covering what was intended, what happened, artifacts used, guardrails fired, changed assumptions, root cause, and process improvement
- `next_actions`, including a policy or skill change proposal when needed
- Universe/instrument support gap if the process failed because the requested asset class, instrument, adapter, source, or workflow was not installed

Quality floor:

- Apply the risk, uncertainty, and anti-hallucination floor from `scenario-quality-gates`.
- Tag material narrative claims as `[factual]`, `[inference]`, or `[assumption]`.
- Use a short timeline.
- Separate root cause, contributing factors, and symptoms.
- State whether the failure was user-input, analysis, policy, approval, execution, or harness related.
- State whether the failure was universe-support, source-readiness, hero/support artifact, or readiness-label related.
- Do not fabricate audit events, artifacts, command output, approvals, executions, or timestamps.
- End with one or more concrete harness, guardrail, policy, skill, artifact, or validation improvements.

Write outputs under `trading/reports/postmortem/` using the `*.postmortem_report.json` artifact shape.

Postmortem is a skill, not a subagent role.

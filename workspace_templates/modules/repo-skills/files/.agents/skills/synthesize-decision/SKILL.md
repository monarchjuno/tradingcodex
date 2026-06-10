---
name: synthesize-decision
description: "Synthesize collected subagent artifacts into a user-facing decision state. Use by head-manager after research, valuation, portfolio, risk, policy, order, approval, execution, or postmortem artifacts exist."
---

# Synthesize Decision

Use this skill when `head-manager` has collected the required subagent artifacts and needs to produce a user-facing decision state or next-step recommendation.

Boundary:

- This skill owns user-facing synthesis after required subagent artifacts or outputs exist.
- It does not create new investment research, valuation, technical analysis, news analysis, portfolio sizing, risk approval, order intents, approvals, or execution.
- If required artifacts are missing, return a waiting state and the exact next role/artifact needed.
- Use `scenario-quality-gates` for the synthesis gate and readiness language.

Before writing the synthesis, apply `scenario-quality-gates` for the scenario's synthesis gate.

Inputs:

- Relevant research, valuation, portfolio, risk, policy, order, approval, execution, or postmortem artifact paths
- The user's stated objective, time horizon, constraints, and requested action
- Any unresolved disagreements between subagents

Output:

- Universe and workflow type when relevant
- Workflow lane
- Scenario archetype
- Artifacts reviewed
- Role-by-role signal summary
- Confidence and evidence quality
- Disagreements or missing evidence
- Source/as-of posture, support gaps, and readiness label
- Decision state: `research-only`, `ready-for-portfolio-risk`, `ready-for-draft`, `ready-for-approval`, `approved`, `executed`, `blocked`, or `revise`
- Next allowed action

Rules:

- Apply the risk, uncertainty, and anti-hallucination floor from `scenario-quality-gates`.
- Preserve `[factual]`, `[inference]`, and `[assumption]` distinctions for material claims, especially when they affect confidence or the next action.
- Lower confidence when data quality, source coverage, sample size, regime coverage, parameter sensitivity, or validation setup is weak.
- Do not turn suggestive evidence into a conclusive recommendation.
- Do not create new market research inside this skill.
- Do not hide conflicting subagent outputs behind a vague summary.
- Do not present a single conclusion when role outputs conflict; state the conflict and the blocking uncertainty.
- Do not omit source dates, stale-data warnings, or missing-evidence warnings when they materially affect quality.
- Do not convert natural language directly into an order.
- Do not approve or submit orders.
- If order drafting is next, hand off to `portfolio-manager`.
- If approval is next, hand off to `risk-manager`.
- If execution is next, require an approved order intent and approval receipt before assigning `execution-operator`.

---
name: agent-judgment-review
description: "Challenge accepted investment artifacts before synthesis, portfolio, risk, order, approval, or execution gates. It makes conclusions reviewable, source-aware, and revisitable without granting research, portfolio, approval, execution, or model-training authority."
---

# Agent Judgment Review

Use this procedure after upstream artifacts are accepted or when a
decision-oriented artifact needs independent challenge.

Inputs:

- original user request and explicit constraints
- accepted artifact paths and compact summaries
- source/as-of metadata, source trust notes, and forecast fields
- stated missing evidence, blocked actions, and downstream recipient

Required output fields:

- strongest supporting evidence
- strongest contrary evidence
- weak, stale, missing, or discounted source posture
- overconfidence risk
- assumptions that would change the conclusion
- source trust notes
- update triggers
- invalidation conditions
- owning role for any required revision
- review outcome: `accepted`, `revise`, `blocked`, or `waiting`

Evidence weighting:

- Treat official primary sources as strongest for factual issuer, regulator,
  exchange, and policy claims.
- Treat management claims as source claims until independently supported.
- Treat market-derived evidence as useful but timestamp-sensitive.
- Treat secondary news as event or narrative evidence, not final proof.
- Discount stale evidence, unsupported assumptions, and sources with missing
  as-of or retrieved-at posture.

Outcome rules:

- Use `accepted` only when contrary evidence, source trust, update triggers,
  and invalidation conditions are explicit enough for downstream use.
- Use `revise` when an owning role can fix weak evidence, missing source
  posture, unsupported assumptions, or unclear forecast/update fields.
- Use `blocked` when the conclusion depends on unavailable evidence, policy
  conflicts, missing profile context, or unsupported downstream authority.
- Use `waiting` when required upstream artifacts or accepted handoff state are
  missing.

Quality floor:

- Challenge the artifact; do not produce replacement analyst work.
- Separate `[factual]`, `[inference]`, and `[assumption]` claims when the
  distinction affects downstream use.
- Name the best objection instead of averaging conflict into false consensus.
- Lower confidence when source trust, freshness, coverage, or contradiction is
  weak.
- Do not create order tickets, approvals, broker actions, execution requests,
  strategy changes, policy changes, or forecast ledger records from this review
  alone.

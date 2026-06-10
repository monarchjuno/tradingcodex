# Improvement Loop

Improvement is the quality subsystem under the top-level TradingCodex harness.
It makes future workflows better through routing discipline, artifact quality,
research memory, skill proposals, postmortems, and validation feedback.

Improvement is separate from Guardrails. Improvement can raise confidence and
reduce repeated mistakes, but it never authorizes execution by itself.

## Improvement Taxonomy

| Improvement area | Purpose | Examples |
| --- | --- | --- |
| Workflow quality | Route work to the right role team and require useful handoffs. | workflow maps, role briefs, artifact paths, readiness gates |
| Research memory | Preserve source-aware work products for reuse and review. | `ResearchArtifact`, versions, source snapshots, markdown exports |
| Skill evolution | Improve role behavior without hidden prompt drift. | skill proposals, Admin review, CLI apply flow, audit trail |
| Postmortems | Learn from rejected orders, failed checks, thesis changes, and executions. | root cause, guardrail fired, changed assumptions, process improvement |
| Validation feedback | Convert recurring issues into tests and smoke checks. | routing scenarios, MCP smoke tests, generated workspace doctor checks |

## Workflow Quality

Workflow quality starts before analysis. `head-manager` classifies the universe
and workflow type, chooses the role team, sets artifact paths, and waits for
role outputs before synthesis.

Quality gates should preserve:

- source/as-of posture
- claim tags
- role boundaries
- missing evidence
- uncertainty
- readiness labels
- hero/support artifact split

## Research Memory

Research memory keeps mutable research in the central DB. Markdown exports are
useful for Codex and humans, but the canonical object is the DB artifact.

Good research memory improves later work by preserving:

- source date and retrieved-at time
- version history
- content hashes
- stale-data warnings
- role/user provenance
- workspace provenance
- readiness labels

## Skill Evolution

Skill proposals let TradingCodex change role-owned behavior deliberately.

Expected flow:

```text
proposal -> validation -> approval -> apply -> audit
```

This keeps improvements visible through Admin, CLI, tests, and docs instead of
letting hidden prompt changes become durable product rules.

## Postmortems

Postmortems are not only for executed orders. They also apply to:

- rejected orders
- blocked approval attempts
- failed policy checks
- stale or weak evidence
- thesis changes
- routing failures
- process gaps

A useful postmortem should end with concrete harness, guardrail, policy, skill,
artifact, or validation improvements.

## Validation Feedback

Validation feedback turns lessons into regression coverage:

- unit tests for policy and execution preconditions
- API tests for Admin/Ninja/MCP boundaries
- generated workspace smoke checks
- research-memory smoke checks
- routing scenario tests
- UI checks for review-only product web behavior

The validation plan is part of improvement because it prevents old mistakes
from returning.

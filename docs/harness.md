# Harness

Harness is the top-level TradingCodex concept. It is the operating system for
Codex-native investment workflows: roles, skills, service-layer state, policy,
MCP tools, research memory, artifacts, approvals, execution adapters, audit,
and feedback loops all live inside the harness.

TradingCodex is therefore not just a set of guardrails. Guardrails are one
subsystem of the harness. Improvement is another subsystem.

## Top-Level Model

```text
TradingCodex Harness
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
| Skills | Assign role-owned skills, expose direct user entrypoints, and manage skill proposals. |
| State | Keep canonical runtime state in the central Django DB. |
| Interfaces | Expose Web, Admin, REST, CLI, and MCP as service-layer callers. |
| Guardrails | Reduce, restrict, or block risky actions through guidance, enforcement, and information barriers. |
| Improvement | Raise workflow quality through quality gates, artifact readiness, research memory, postmortems, and test feedback. |
| Execution boundary | Keep executable actions behind policy, approval, idempotency, adapter, and audit checks. |
| Provenance | Record which workspace and role produced or requested work without making workspaces separate ledgers. |
| Profiles | Separate paper portfolio/account/strategy state from workspace identity. |

## Guardrails Under Harness

Guardrails answer: "What should be prevented, reduced, isolated, or blocked?"

- Guidance guardrails shape agent behavior before risky action.
- Enforcement guardrails deterministically block final risky action paths.
- Information barriers limit role knowledge, file access, secrets, and tool surfaces.

Guardrails never replace the need for improvement. A blocked action can still
leave behind a useful postmortem, skill proposal, or validation scenario.

## Improvement Under Harness

Improvement answers: "How does the next workflow become higher quality?"

- Workflow maps route work to the right role team.
- Quality gates define evidence, source/as-of posture, claim discipline, and readiness.
- Research memory preserves versioned artifacts and source snapshots.
- Skill proposals let the harness evolve without hidden prompt drift.
- Postmortems turn rejected orders, failed checks, and thesis changes into process improvements.
- Validation tests convert recurring mistakes into regression coverage.

Improvement does not authorize execution. A high-quality report still needs the
guardrail path before any draft, approval, or adapter submission.

## Interface Implications

The product web app should show the harness as the first-level concept, with
Guardrails and Improvement visible as child systems. Django Admin should expose
both safety operations and improvement operations. CLI checks should keep
separate layers for guidance, enforcement, information barriers, improvement,
MCP, and service status.

## Naming Rule

Use "harness" for the top-level operating model. Use "guardrail" only for
safety/restriction systems. Use "improvement" for quality, learning, skill,
postmortem, and validation loops.

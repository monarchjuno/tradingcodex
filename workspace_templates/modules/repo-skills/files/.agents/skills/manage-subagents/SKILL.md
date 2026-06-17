---
name: manage-subagents
description: "Handle fixed-role runtime state checks, compact assignment envelopes, artifact review, reuse, and conflict handling after a workflow lane has been selected."
---

# Manage Subagents

Use this skill after a workflow lane and selected team already exist.

## Boundary

- Covers subagent mechanics: state/reuse checks, dispatch shape,
  compact brief construction, artifact review, and conflict handling.
- It does not own scenario selection, role identity, model settings, permission
  profiles, MCP allowlists, policy decisions, or standing prohibitions.
- Briefs are assignment envelopes, not role manuals.

## Runtime Checks

1. Inspect available fixed-role agent files and use their exact configured
   runtime names.
2. Check active/completed state before creating another worker for the same
   role and workflow.
3. Reuse a completed artifact when it answers the assigned question and passes
   review.
4. If exact role routing is unavailable, return `routing-unverified` and provide
   briefs only.

## Spawn Shape

Use the active runtime schema. When fixed-role `agent_type` selection is
available, prefer:

```text
spawn_agent(agent_type="<configured-role-name>", message="<compact assignment envelope>", fork_context=false)
```

Do not pass model, reasoning, service-tier, or internal run-id overrides in the
subagent-visible message.

## Assignment Envelope

```text
TASK: <one concrete outcome>.
DELIVERABLE: <artifact path or DB artifact reference>.
CONTEXT: Original user request: "<verbatim>". Explicit constraints: <user-stated constraints or none>. Workflow consent: <activation source>. Lane: <workflow lane>. Asset/context: <minimal, non-binding if inferred>.
CONTEXT BUDGET: Use artifact paths, context summaries, source snapshot IDs, and short deltas; do not paste full artifacts, long source dumps, reusable role instructions, or unrelated chat.
RESPONSE LANGUAGE: <user requested language or artifact default>.
OUT OF SCOPE: <request-specific exclusions>.
INSTRUCTIONS: Use your role config and assigned skills. Treat inferred coordinator context as non-binding. If external data is used, record provider, as-of/retrieved-at time, warnings, missing coverage, and source snapshot IDs when available.
RETURN: Artifact path, artifact state, concise findings, confidence, source/as-of posture, missing evidence, readiness gaps, next eligible recipient, blocked actions, source snapshot IDs, and role-boundary conflicts.
```

## Review

- The artifact exists at the expected path or DB reference.
- It answers the assigned question without silently doing another role's work.
- Material claims distinguish facts, inferences, assumptions, and missing
  evidence.
- Source/as-of posture is visible when it affects downstream use.
- Confidence, support gaps, next eligible recipient, blocked actions, and source
  snapshot IDs are explicit when available.
- Stored research markdown includes context summary, handoff state, confidence,
  missing-evidence, next-recipient, blocked-action, and source-snapshot metadata.
- Artifact state is `accepted`, `revise`, `blocked`, or `waiting`.
- Downstream context starts from artifact path plus context summary; open full
  markdown only when summary coverage is insufficient.

## Conflict Handling

- Preserve disagreements instead of averaging them into false consensus.
- Ask for targeted follow-up from the role that owns the unresolved question.
- Carry unresolved conflict forward as a risk or open question.

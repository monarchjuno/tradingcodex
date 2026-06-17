---
name: orchestrate-workflow
description: "Sequence an investment workflow across intake, workflow mapping, scenario gates, subagent coordination, synthesis, and postmortem without duplicating role or policy rules."
---

# Orchestrate Workflow

Use this skill when a request needs more than a direct procedural answer and
must move through the configured investment workflow.

## Boundary

- Covers sequencing only.
- Base instructions own the dispatch boundary, standing role boundary, and
  execution safety invariants.
- The workflow-map step covers universe, source posture, hero/support artifacts,
  support gaps, and readiness labels.
- The scenario-gate step covers lane, selected team, blocked actions, and
  synthesis gates.
- The subagent-management step covers runtime state checks, spawn mechanics,
  compact briefs, artifact review, reuse, and conflict handling.
- The synthesis step covers the user-facing decision state after required
  artifacts or explicit waiting state exists.

## Operating Loop

1. Preserve the user's original request, explicit constraints, and requested
   output language.
2. Run the workflow map for investment-universe and source-posture context.
3. Run scenario quality gates for lane, selected team, expected artifacts,
   blocked actions, and quality gates.
4. Use subagent management for dispatch or reuse when the selected lane requires
   role outputs.
5. Review returned artifacts against the gate outputs and keep conflicts visible;
   prefer artifact paths and context summaries before reopening full artifacts.
6. Stop with a waiting state when required artifacts are missing or dispatch is
   unavailable.
7. Use synthesis only after the required artifacts or waiting state exists.
8. Route rejected checks, executions, thesis changes, and process failures into
   postmortem when requested or materially useful.

## Output Shape

```text
Workflow: <lane>
Artifacts reviewed: <paths or none>
Artifact state: accepted | revise | blocked | waiting
Decision state: research-only | ready-for-risk-review | ready-for-draft | blocked | approved | executed | waiting
Open questions: <missing evidence, conflicts, or gates>
Next allowed action: <one or two actions>
```

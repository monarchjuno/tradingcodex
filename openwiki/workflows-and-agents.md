# Workflows And Agents Source Map

Canonical behavior: [Roles, Skills, And Workflows](../docs/roles-skills-and-workflows.md),
[Codex-Native Orchestration](../docs/codex-native-orchestration.md), and
[Product Direction](../docs/product-direction.md).
Knowledge lookup, explicit ingest, and Brain promotion boundaries are owned by
[Knowledge Wikis](../docs/knowledge-wikis.md).

## Primary Sources

| Concern | Source |
| --- | --- |
| Head Manager identity and boundaries | `workspace_templates/modules/codex-base/files/.codex/prompts/base_instructions/head-manager.md` |
| Shared child boundary | `workspace_templates/modules/codex-base/files/.codex/prompts/base_instructions/fixed-role.md` |
| Role profiles | `workspace_templates/modules/fixed-subagents/files/.codex/agents/*.toml` |
| User and role skills | `workspace_templates/modules/repo-skills/files/` |
| Wiki manager skill | `workspace_templates/modules/repo-skills/files/.agents/skills/tcx-wiki/` |
| Agent/skill projection metadata | `tradingcodex_service/application/agents.py` |
| Run identity | `tradingcodex_service/application/analysis_runs.py` |
| Research artifact persistence | `tradingcodex_service/application/research.py` |
| Hook enforcement | `workspace_templates/modules/codex-base/files/.codex/hooks/tradingcodex_hook.py` |
| MCP visibility | `tradingcodex_service/mcp_runtime.py` |

## Desired Runtime Shape

Head Manager interprets the request and takes the smallest useful path. It can
answer narrow questions directly. Specialist agents are optional profiles used
for distinct expertise or independent review, not a mandatory workflow. Reuse
an existing child for correction when practical. The fixed model settings and
fallback boundary are owned by
[Roles, Skills, And Workflows](../docs/roles-skills-and-workflows.md); generated
TOML projects them without a model-policy layer.

An exact profile spawn uses its exact `agent_type`, compact task context, and
`fork_turns="none"`. Waiting, follow-up, and lifecycle claims require the live
target returned by a completed native spawn.
Native wait-any may serialize with no explicit target list while a child is
live. Hooks do not record, alter, route, schedule, or choose native child calls.

Prompts state role identity, authority, evidence standards, and safety. Shared
procedures live once in a skill. Tool syntax, exact search counts, wait loops,
artifact pagination, and retry mechanics should not be copied through every
prompt and role.

Keep Head Manager routing and stable authority in the root prompt, shared child
invariants in `fixed-role.md`, and only role-specific purpose and prohibitions
in each role TOML. Projection-generated source blocks are an index, not an
embedded skill manual.

Persist work when it has reuse, provenance, decision, or audit value. Stored
artifacts retain source/dataset references, confidence, gaps, and a content
hash; narrow replies need not create one.

Codex owns thread, child, wait, and follow-up lifecycle. TradingCodex creates a
run only through `begin_analysis_run` when durable provenance is needed and does
not keep a parallel session-to-run map. Follow-ups in the same Codex task reuse
the run ID already present in task context.

## External Evidence

The shared source-routing skill owns this guidance:

```text
adequate Snapshot/Dataset
  -> one relevant user capability
  -> optional direct OpenBB MCP
  -> official-source-first native research
  -> other credible sources
  -> explicit gap
```

Carry partial results forward, retrieve only missing coverage, and do not repeat
an unchanged completed or terminal call. This is agent guidance, not a service
lease, semantic dedupe engine, provider registry, or trust ranking.

## Edit Checklist

- Search for the rule in Head Manager, fixed-role base, role TOML, skills, hook,
  service, and tests; keep one owner and delete copies.
- Confirm a restriction protects a real safety invariant rather than merely
  prescribing one reasoning style.
- Prefer native Codex delegation, follow-up, permissions, and capability
  discovery over TradingCodex wrappers.
- Preserve independent review for recommendations and high-consequence work.
- Inspect generated output after changing templates or projection.

## Validation

Run focused prompt/skill/projection tests, generate a disposable development
workspace, and inspect effective role instructions and MCP exposure. When
behavior changes, run one native Codex smoke that measures agent count, tool
calls, visible progress cadence, context size, and resulting artifacts.

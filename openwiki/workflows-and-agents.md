# Workflows And Agents Source Map

Canonical behavior: [Roles, Skills, And Workflows](../docs/roles-skills-and-workflows.md),
[Codex-Native Orchestration](../docs/codex-native-orchestration.md), and
[Product Direction](../docs/product-direction.md).

## Primary Sources

| Concern | Source |
| --- | --- |
| Head Manager identity and boundaries | `workspace_templates/modules/codex-base/files/.codex/prompts/base_instructions/head-manager.md` |
| Shared child boundary | `workspace_templates/modules/codex-base/files/.codex/prompts/base_instructions/fixed-role.md` |
| Role profiles | `workspace_templates/modules/fixed-subagents/files/.codex/agents/*.toml` |
| User and role skills | `workspace_templates/modules/repo-skills/files/` |
| Agent/skill projection metadata | `tradingcodex_service/application/agents.py` |
| Run identity | `tradingcodex_service/application/analysis_runs.py` |
| Research artifact persistence | `tradingcodex_service/application/research.py` |
| Hook enforcement | `workspace_templates/modules/codex-base/files/.codex/hooks/tradingcodex_hook.py` |
| MCP visibility | `tradingcodex_service/mcp_runtime.py` |

## Desired Runtime Shape

Head Manager interprets the request and takes the smallest useful path. It can
answer narrow questions directly. Specialist agents are optional profiles used
for distinct expertise or independent review, not a mandatory workflow. Reuse
an existing child for correction when practical; a generic child can receive a
bounded role brief when a profile is unavailable. Head Manager and children
inherit the user's native Codex model and reasoning defaults.

An exact profile spawn uses its exact `agent_type`, compact task context, and
`fork_turns="none"` without model overrides. Waiting, follow-up, and lifecycle
claims require the live target returned by a completed native spawn.
Native wait-any may serialize with no explicit target list while a child is
live. The hook records redacted spawn and follow-up metadata but does not alter,
route, schedule, or choose either call.

Prompts state role identity, authority, evidence standards, and safety. Shared
procedures live once in a skill. Tool syntax, exact search counts, wait loops,
artifact pagination, and retry mechanics should not be copied through every
prompt and role.

Persist work when it has reuse, provenance, decision, or audit value. Stored
artifacts retain source/dataset references, confidence, gaps, and a content
hash; narrow replies need not create one.

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

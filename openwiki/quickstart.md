# TradingCodex Agent Quickstart

OpenWiki is the repository map for coding agents. It points to source owners and
validation; durable product behavior belongs in [`docs/`](../docs/README.md).

## Read Order

1. Read repository rules in [`AGENTS.md`](../AGENTS.md).
2. Use the table below to find the relevant source map.
3. Read the linked canonical `docs/` page before changing behavior.
4. Inspect only the owning source and its callers/tests.
5. Run the smallest relevant validation from
   [Development And Validation](development-and-validation.md).

## Route A Change

| Work | Source map | Canonical product docs |
| --- | --- | --- |
| Runtime planes, service ownership, state placement | [Architecture](architecture.md) | [System Architecture](../docs/system-architecture.md) |
| Head Manager, roles, skills, prompts, research flow | [Workflows And Agents](workflows-and-agents.md) | [Roles, Skills, And Workflows](../docs/roles-skills-and-workflows.md) |
| Viewer, Admin, API, MCP, CLI, research objects | [Interfaces And Data](interfaces-and-data.md) | [Interfaces And Surfaces](../docs/interfaces-and-surfaces.md) |
| Knowledge Wiki pages, packages, lifecycle, and Viewer | [Interfaces And Data](interfaces-and-data.md), [Workflows And Agents](workflows-and-agents.md), [Generated Workspaces](generated-workspaces.md) | [Knowledge Wikis](../docs/knowledge-wikis.md) |
| Attach/update, templates, hooks, projected files | [Generated Workspaces](generated-workspaces.md) | [Generated Workspaces](../docs/generated-workspaces.md) |
| Policy, approvals, brokers, orders, secrets, audit | [Safety And Execution](safety-and-execution.md) | [Safety, Policy, And Execution](../docs/safety-policy-and-execution.md) |
| Tests, smoke, docs checks, release validation | [Development And Validation](development-and-validation.md) | [Validation And Test Plan](../docs/validation-and-test-plan.md) |

## Product Mental Model

TradingCodex is a thin local-first investment layer on native Codex:

```text
native Codex
  reasoning, research, tools, delegation, user capabilities
        |
        v
TradingCodex skills and MCP
  concise procedures + durable service calls
        |
        v
Django service ledger          workspace research files
  policy/order/audit            snapshots/datasets/artifacts
```

Native Codex owns agent behavior. Django owns durable and sensitive invariants;
it is not an agent scheduler. The viewer is read-only. Final broker effects use
the canonical service path and never follow from analysis prose alone.

External evidence falls through: adequate existing data, one relevant user
capability, optional direct OpenBB MCP, official-source-first native research,
other credible sources, then an explicit gap. This is guidance in the research
skill, not a provider state machine.

## Repository Landmarks

| Path | Purpose |
| --- | --- |
| `tradingcodex_service/application/` | Canonical durable use cases and safety rules. |
| `apps/` | Django persistence models, admin, and migrations. |
| `tradingcodex_service/mcp_runtime.py` | TradingCodex MCP exposure and dispatch. |
| `tradingcodex_service/api.py`, `viewer_api.py`, `web.py` | Local interfaces and read-only viewer boundary. |
| `tradingcodex_cli/` | Operator CLI and generated workspace lifecycle. |
| `workspace_templates/modules/*/files/` | Prompts, skills, role profiles, hooks, policies, and launchers projected into workspaces. |
| `frontend/` | Maintainer-only viewer source; generated workspaces stay Node-free. |
| `tradingcodex_service/application/knowledge_wikis.py`, `wiki_viewer.py` | Knowledge Wiki package lifecycle and bounded read-only discovery. |
| `tests/` | Unit, contract, generated-workspace, and native Codex validation. |
| `docs/` | Canonical product documentation. |
| `guidebook/` | Task-first public user guide. |

## Working Rules

- Trace the real path before editing, then prefer deletion and direct reuse.
- Do not duplicate native Codex routing, permissions, capability discovery, or
  scheduling in TradingCodex.
- Put durable behavior in one application service and have interfaces call it.
- Keep prompts and skills about goals and boundaries, not exact reasoning
  scripts.
- Preserve unrelated dirty changes and the generated/source distinction.
- Update only the owning documentation layer.

## Setup Guard

End-user workspaces attach the published package; they do not clone this repo:

```bash
uvx --refresh --from tradingcodex tcx attach . && ./tcx doctor
```

Development workspaces use `./install.sh --dev ...` from this checkout. See
[Generated Workspaces](generated-workspaces.md) before changing bootstrap or
runtime identity.

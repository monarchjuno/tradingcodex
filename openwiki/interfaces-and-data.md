# Interfaces And Data Source Map

Canonical behavior: [Interfaces And Surfaces](../docs/interfaces-and-surfaces.md),
[System Architecture](../docs/system-architecture.md), and
[Research Memory And Artifacts](../docs/research-memory-and-artifacts.md).

## Interface Owners

| Surface | Source | Boundary |
| --- | --- | --- |
| Viewer shell and read API | `tradingcodex_service/web.py`, `viewer_api.py`, `application/viewer.py`, `application/wiki_viewer.py` | Read-only workspace inspection, including bounded Wiki search/detail/backlinks. |
| Frontend source | `frontend/` | Maintainer build only; no generated-workspace Node runtime. |
| Django Admin | `apps/*/admin.py` | Local/staff inspection through canonical models. |
| Local API | `tradingcodex_service/api.py` | Thin caller of application services. |
| MCP | `tradingcodex_service/mcp_runtime.py` | Role-visible tools and bounded service calls. |
| CLI | `tradingcodex_cli/commands/` | Operator and workspace lifecycle entrypoints. |

No interface owns a second policy, portfolio, approval, order, broker,
execution, research, or audit implementation.

## Data Owners

| Data | Owner |
| --- | --- |
| Policy, portfolio, approval, order, broker, execution, audit | Central Django ledger. |
| Source snapshots, datasets, calculations, artifacts | Workspace files written through application services. |
| Local Wiki and shareable sources | User-owned Markdown under `wikis/local` and `wiki-packages`; explicit destination requests only. |
| Installed Wiki versions and projections | Type-specific `application/knowledge_wikis.py` service under `.tradingcodex/knowledge-wikis` and `wikis/knowledge-wiki-*`. |
| Search indexes and viewer snapshots | Rebuildable projections. |
| Credentials | External environment/secret store; never persisted or returned. |

Important source modules include `application/research.py`, `datasets.py`,
`calculations.py`, `research_object_catalog.py`, and the relevant `apps/*`
models. Check actual callers before changing a schema.

Knowledge Wikis add no model or migration. Lifecycle is in
`application/knowledge_wikis.py`, shared safe source materialization is in
`application/managed_package_sources.py`, CLI is `commands/wikis.py`, MCP is
`manage_knowledge_wiki`, and Viewer routes are under `/api/viewer/wiki-pages/`.

## Interface Rules

- Keep MCP and API registries small and purpose-specific; do not expose every
  internal service method automatically.
- Keep every supported MCP field and validator, but express repeated schema
  shapes once with standard JSON Schema references and expose only standard MCP
  annotations. Enforcement metadata stays in the service registry.
- Prefer one application function shared by MCP, API, CLI, Admin actions, and
  hooks.
- Return stable IDs and compact cards by default. Retrieve large payloads only
  when needed.
- Keep the viewer GET-only and sanitized. It does not launch Codex, mutate
  files, or perform final broker effects.
- Record provenance and content hashes for durable evidence without storing raw
  reasoning or secrets.
- Remove an interface when it has no supported consumer; speculative parity is
  not a product requirement.

## Validation

- Django wiring: focused pytest and `python manage.py check`.
- MCP: registry/list plus handler and role-visibility smoke.
- Viewer: focused read API tests; frontend test/build and browser checks only
  when viewer behavior changes.
- Research data: create/read/export or replay the affected object and verify
  provenance, bounds, and secret redaction.

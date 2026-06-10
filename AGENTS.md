# TradingCodex Source Repository Guide

This file is the durable working guide for the root source repository at `/Users/junhoyoon/codex_pjt/tradingcodex`. It is not the generated workspace guide; generated `AGENTS.md` content comes from `workspace_templates/modules/codex-base/files/AGENTS.md`.

## Current Direction

- TradingCodex is now a Python/Django-native local-first trading harness, not a Node package workspace.
- Harness is the top-level product model. Guardrails and Improvement sit under it: guidance guardrails, enforcement guardrails, and information barriers reduce or block risk; Improvement covers workflow quality, research memory, skill proposals, postmortems, and validation feedback.
- The service target follows the product docs: latest LTS-oriented Python/Django stack, currently Django 5.2.x and the current supported Python line defined in `pyproject.toml` and `docs/tradingcodex-prd.md`.
- Django is the durable service plane. The product web app at `/` is the visual harness dashboard. Django Admin is the advanced harness operations console. Django Ninja is the typed local/staff control API. Django-hosted MCP is the agent/tool execution boundary.
- Public equity remains the deepest first investing sleeve, but the product must not be limited to public equities. Preserve extensibility for ETF/index, public crypto, macro/rates/FX/commodities, options, credit-signal, and cross-asset workflows.
- Runtime state and research memory are central-DB-first. Codex projects are clients/provenance; markdown and JSON files are Codex-readable exports, caches, or artifacts unless the docs explicitly say otherwise.
- OpenAI SDK embedding, semantic-search, AI-review, and SDK-backed agent orchestration surfaces are intentionally not part of the core harness right now. Do not add them back unless the user explicitly reopens that decision.

## Documentation Source Of Truth

- Treat `docs/` as the source of truth for durable product direction, core concepts, safety rules, role boundaries, and execution policy.
- Read [docs/README.md](./docs/README.md) before changing product rules, generated workspace behavior, guardrails, subagent roles, artifact contracts, module capabilities, MCP tools, or Admin operations.
- Use [docs/tradingcodex-prd.md](./docs/tradingcodex-prd.md) as the source of truth for product definition, goals, non-goals, architecture, initial scope, and test expectations.
- Use [docs/core-concepts-and-rules.md](./docs/core-concepts-and-rules.md) as the source of truth for role responsibilities, guardrails, information barriers, execution lifecycle, artifact paths, and MCP role boundaries.
- Use [docs/harness.md](./docs/harness.md), [docs/guardrails.md](./docs/guardrails.md), and [docs/improvement-loop.md](./docs/improvement-loop.md) when changing the top-level harness taxonomy, safety taxonomy, or quality/improvement loops.
- Update the relevant `docs/` files in the same change whenever product direction, rules, permissions, workflows, templates, policy behavior, or MCP/Admin behavior changes.
- If implementation and docs disagree, resolve the mismatch in the same change. Do not let hidden product rules live only in code, tests, templates, or prompts.

## Source Layout

- Keep Python CLI code under `tradingcodex_cli/`.
- Keep CLI command implementations under `tradingcodex_cli/commands/`; keep `tradingcodex_cli/workspace.py` as the public compatibility facade for generated wrappers and imports.
- Keep Django project code under `tradingcodex_service/`.
- Keep shared Django service implementation under `tradingcodex_service/application/`; keep `tradingcodex_service/domain.py` as the public compatibility facade.
- Keep modular Django apps under `apps/`.
- Keep generated workspace templates under `workspace_templates/modules/*`.
- Keep TradingCodex product documentation under `docs/`.
- Keep tests under `tests/`.
- Do not reintroduce Node runtime surfaces such as `package.json`, `packages/*`, old `templates/*`, or Node MCP scripts unless the user explicitly reverses the migration direction.

## Service Layer Rules

- Admin, Django Ninja, MCP, generated hooks, and CLI must call shared application service functions for durable behavior.
- Do not duplicate policy, order, approval, execution, portfolio, research, audit, or harness logic per interface.
- Executable actions must flow through `principal -> capability -> policy -> schema -> approval/idempotency -> adapter -> audit`.
- Revalidate policy and approval immediately before adapter submission.
- Live broker adapters remain disabled and unimplemented in the initial core. Paper/stub adapters are the only executable adapters unless the docs and user request explicitly change that.
- Do not store broker API keys, tokens, or secrets in this repository.
- Do not add direct live broker paths outside the TradingCodex MCP/service-layer boundary.

## DB-First Runtime

- The default runtime DB is the central local SQLite ledger at `~/.tradingcodex/state/tradingcodex.sqlite3`, unless `TRADINGCODEX_HOME` or `TRADINGCODEX_DB_NAME` overrides it.
- `TRADINGCODEX_WORKSPACE_ROOT` is provenance only. Do not use it to partition canonical investment state.
- Prefer Django models/services for durable runtime state: research artifacts and versions, source snapshots, workflow runs, skill proposals, role assignments, order intents, approval receipts, execution results, portfolio snapshots, policy decisions, MCP tool definitions, MCP call ledgers, and audit events.
- Treat generated workspace `trading/*` markdown/json files as readable exports or cache artifacts. Do not make file scraping the canonical runtime path when the Django DB can own the state.
- Research workflows should prioritize source/as-of/retrieved-at metadata, stale-data warnings, versioning, invalidation, and content hashes over long-lived embedding memory. Live-data freshness matters more than recalling old notes.

## Product Web, Admin, API, And MCP

- The product web app at `/` is a user-facing read/review surface for the visual harness dashboard, role topology, research memory, paper portfolio state, orders, policy, activity, and starter prompt generation.
- Product web routes must not spawn Codex subagents, generate investment analysis, create approvals, submit executions, or mutate execution-sensitive state.
- The visual harness canvas should show `head-manager`, fixed subagents, role skill ownership, MCP tool exposure, policy gates, the MCP execution boundary, and the Guardrails/Improvement split in English.
- Django Admin is an operations console, not a bypass. Admin actions for risky changes must call service functions and create audit events.
- Useful Admin surfaces include role roster, skill assignments, skill proposals, policy, restricted list, limits, adapter definitions, universe plugins, MCP tool registry, MCP call ledger, workflow runs, artifacts, approvals, executions, portfolio state, and audit logs.
- Django Ninja is for local/staff status, validation, and control APIs. It must not bypass MCP or service-layer execution policy.
- MCP tools are intentionally selected service-layer use cases, not automatic REST endpoint mirrors.
- MCP definitions should include stable name, description, input schema, category, risk level, role allowlist, approval requirement, and audit requirement.
- Role-specific MCP allowlists are part of the guardrail. Analyst roles may use research-memory tools, while drafting, approval, execution, cancellation, policy mutation, and secret access remain restricted.

## Subagents And Skills

- Preserve the generated baseline of one `head-manager`, nine fixed subagents, and twenty-one repo skills unless the docs and tests are updated in the same change.
- Keep role-owned skills as specialist role skill bundles. The root/head-manager may inspect and assign them, but should not silently perform role work that belongs to a specialist workflow.
- When changing role instructions, skill behavior, skill assignments, or workflow routing, update the relevant templates, docs, and tests together.
- Public Equity Investing plugin skills can inform research quality, evidence handling, and role workflows, but TradingCodex must keep a broader multi-asset universe and its own harness, guardrail, and improvement model.

## Generated Workspace Contract

- A clean generated workspace must not contain `package.json` or Node MCP/runtime files.
- Generated workspaces do not own a canonical investment DB. They call the central local TradingCodex service/DB and pass workspace provenance through `TRADINGCODEX_WORKSPACE_ROOT`.
- The generated `./tcx` wrapper calls the Python CLI.
- Generated workspace behavior is controlled by `workspace_templates/modules/*`; hand-editing a workspace under `~/tmp/*` is only a smoke/debug step, not a durable source change.
- When changing bootstrap behavior, update the source templates and then regenerate a clean workspace for verification.

## Validation

- Run `pytest` after source or test changes.
- Run `python manage.py check` after Django settings, model, admin, API, or service changes.
- Run `python -m compileall tradingcodex_cli tradingcodex_service apps tests` after broad Python migration changes.
- Run generated workspace smoke checks after template/bootstrap behavior changes: remove the tmp workspace, run `python -m tradingcodex_cli init <tmp-workspace>`, then `./tcx doctor`.
- For research-memory changes, verify at least `./tcx research create`, `./tcx research search`, and `./tcx research export` in a generated workspace.
- For MCP surface changes, verify `tcx mcp stdio` or generated `./tcx mcp stdio` `tools/list`.
- For harness or routing changes, run targeted scenario tests and inspect logs/results rather than relying only on static checks.

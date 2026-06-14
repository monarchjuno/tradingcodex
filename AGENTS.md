# Repository Guidelines

## Setup Request Guard

If a user asks to set up, install, attach, or use `monarchjuno/tradingcodex` in
their current workspace, do not run `git clone` and do not turn the workspace
into a source checkout. Run the packaged CLI from that target workspace
instead:

```bash
uvx --refresh --from tradingcodex tcx attach . && ./tcx doctor
```

Clone this repository only when the user explicitly asks to develop, inspect,
or modify TradingCodex source code.

## Project Structure & Module Organization

TradingCodex is a Python/Django local-first trading harness. CLI code lives in `tradingcodex_cli/`, with command implementations in `tradingcodex_cli/commands/`. Django service code lives in `tradingcodex_service/`; shared durable behavior belongs in canonical modules under `tradingcodex_service/application/`. Modular apps live under `apps/`. Product docs live in `README.md`, `installation.md`, and `docs/`; generated workspace templates live in `workspace_templates/modules/*`; tests live in `tests/`. Do not reintroduce Node roots such as `package.json`, `packages/*`, Node MCP runtime files, pre-release compatibility facades, or a Django `apps/universes` app unless product direction changes.

## Build, Test, and Development Commands

- `python -m pytest`: run the repository test suite configured in `pyproject.toml`.
- `python manage.py check`: validate Django settings, models, apps, admin, API, and service wiring.
- `python -m compileall tradingcodex_cli tradingcodex_service apps tests`: catch broad Python syntax/import issues.
- `python manage.py runserver 127.0.0.1:48267`: run the local web, admin, and API service.
- `python -m tradingcodex_cli init /tmp/tcx-smoke` then `/tmp/tcx-smoke/tcx doctor`: smoke-test generated workspace behavior.

## Coding Style & Naming Conventions

Target Python `>=3.11,<3.15` and Django `5.2.x`. Use four-space indentation, clear module-level service functions, and type hints where they clarify contracts. Admin, Django Ninja, MCP, generated hooks, and CLI code should call shared application services rather than duplicating policy, approval, research, order, portfolio, audit, or harness logic. Research artifacts and source snapshots are workspace-file-native, not Django DB models. Prefer direct canonical imports over pre-release compatibility facades. Keep generated workspace template bodies as ordinary files under `workspace_templates/modules/*/files`; use Python for registry loading, dependency resolution, rendering, validation, and generated indexes, not to hide durable prompts, skills, policies, hooks, or workspace-contract content inside string constants.

## Agent Context & Harness Review

When changing or reviewing agent, workflow, MCP, policy, template, or harness behavior, do not infer behavior from Python code alone. Read the relevant harness flow and instruction surfaces first: `docs/harness.md`, `docs/roles-skills-and-workflows.md`, `tradingcodex_service/application/components.py`, and generated workspace files under `workspace_templates/modules/*/files`, especially `.agents/skills/*/SKILL.md`, `.codex/agents/*.toml`, `.codex/prompts/*`, `.codex/hooks/*`, `.tradingcodex/policies/*`, and `.tradingcodex/workflows/*`. Treat skill bodies, role TOML, hooks, policies, docs, and service-layer code as one product contract; keep them aligned when routing, permissions, role boundaries, quality gates, or workflow behavior changes.

## Testing Guidelines

Use pytest; test files and functions should follow `test_*.py` and `test_*`. Run focused tests while iterating, then `python -m pytest` before handoff. Template or bootstrap changes must regenerate a clean workspace and run `./tcx doctor`. Research-memory changes should verify file-native create, search, source-snapshot, and export flows. MCP changes should verify `tools/list`.

## Commit & Pull Request Guidelines

Recent history uses short imperative subjects such as `Make agent skill config file-native` and `Tighten TradingCodex handoff routing`. Keep commits focused and avoid trailing periods in subject lines. PRs should summarize behavior changes, list validation commands, link related issues, and call out docs, template, migration, or UI changes. Include screenshots only for visible web UI updates.

## Security & Agent-Specific Instructions

Do not store broker API keys, tokens, or secrets in this repository. The default runtime DB is `~/.tradingcodex/state/tradingcodex.sqlite3`; `TRADINGCODEX_WORKSPACE_ROOT` is provenance only. Live broker adapters remain disabled. Execution-sensitive actions must flow through service-layer policy, approval/idempotency, adapter, and audit paths. Treat `README.md`, `installation.md`, and `docs/` as durable product docs, with `docs/` as the source of truth; update them when product rules, workflows, templates, policy behavior, MCP tools, Admin behavior, or release-facing messaging changes.

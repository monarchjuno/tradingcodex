# Repository Guidelines

## Project Structure & Module Organization

TradingCodex is a Python/Django local-first trading harness. CLI code lives in `tradingcodex_cli/`, with commands in `tradingcodex_cli/commands/` and compatibility exports in `tradingcodex_cli/workspace.py`. Django service code lives in `tradingcodex_service/`; shared behavior belongs in `tradingcodex_service/application/`, with `tradingcodex_service/domain.py` as a compatibility facade. Modular apps live under `apps/`. Product docs are in `docs/`, generated workspace templates in `workspace_templates/modules/*`, and tests in `tests/`. Do not reintroduce Node roots such as `package.json`, `packages/*`, or Node MCP runtime files unless product direction changes.

## Build, Test, and Development Commands

- `python -m pytest`: run the repository test suite configured in `pyproject.toml`.
- `python manage.py check`: validate Django settings, models, apps, admin, API, and service wiring.
- `python -m compileall tradingcodex_cli tradingcodex_service apps tests`: catch broad Python syntax/import issues.
- `python manage.py runserver 127.0.0.1:8000`: run the local web, admin, and API service.
- `python -m tradingcodex_cli init /tmp/tcx-smoke` then `/tmp/tcx-smoke/tcx doctor`: smoke-test generated workspace behavior.

## Coding Style & Naming Conventions

Target Python `>=3.14,<3.15` and Django `5.2.x`. Use four-space indentation, clear module-level service functions, and type hints where they clarify contracts. Admin, Django Ninja, MCP, generated hooks, and CLI code should call shared application services rather than duplicating policy, approval, research, order, portfolio, audit, or harness logic. Research artifacts and source snapshots are workspace-file-native, not Django DB models. Keep compatibility facades stable.

## Testing Guidelines

Use pytest; test files and functions should follow `test_*.py` and `test_*`. Run focused tests while iterating, then `python -m pytest` before handoff. Template or bootstrap changes must regenerate a clean workspace and run `./tcx doctor`. Research-memory changes should verify file-native create, search, source-snapshot, and export flows. MCP changes should verify `tools/list`.

## Commit & Pull Request Guidelines

Recent history uses short imperative subjects such as `Make agent skill config file-native` and `Tighten TradingCodex handoff routing`. Keep commits focused and avoid trailing periods in subject lines. PRs should summarize behavior changes, list validation commands, link related issues, and call out docs, template, migration, or UI changes. Include screenshots only for visible web UI updates.

## Security & Agent-Specific Instructions

Do not store broker API keys, tokens, or secrets in this repository. The default runtime DB is `~/.tradingcodex/state/tradingcodex.sqlite3`; `TRADINGCODEX_WORKSPACE_ROOT` is provenance only. Live broker adapters remain disabled. Execution-sensitive actions must flow through service-layer policy, approval/idempotency, adapter, and audit paths. Treat `docs/` as the source of truth and update it when product rules, workflows, templates, policy behavior, MCP tools, or Admin behavior change.

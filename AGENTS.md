# Repository Guidelines

## Documentation Layers

Read documentation in this order:

1. [OpenWiki quickstart](openwiki/quickstart.md) for the fastest agent-facing orientation.
2. This `AGENTS.md` for non-negotiable repository rules and validation expectations.
3. [docs/README.md](docs/README.md) for the human-readable source-of-truth map linked from the public README.

Use `openwiki/` as the working map for coding agents. Use `docs/` as durable product documentation for users, maintainers, and reviewers. If behavior, policy, workflow, generated workspace output, release-facing language, or safety posture changes, update the relevant `docs/` page in the same change.

## Setup Request Guard

If a user asks to set up, install, attach, or use `monarchjuno/tradingcodex` in a workspace, do not run `git clone` and do not turn that workspace into this source checkout. From the target workspace, run:

```bash
uvx --refresh --from tradingcodex tcx attach . && ./tcx doctor
```

Ask for the target directory if the user did not provide one and did not explicitly ask to use the current workspace. Clone this repository only when the user explicitly asks to develop, inspect, or modify TradingCodex source code.

## Source Map

TradingCodex is a Python/Django local-first trading harness.

| Path | Owns |
| --- | --- |
| `tradingcodex_cli/` | Packaged `tcx` CLI. Command implementations live in `tradingcodex_cli/commands/`. |
| `tradingcodex_cli/__main__.py` | CLI command dispatch and top-level command list. |
| `tradingcodex_cli/generator.py` | Generated workspace module graph, rendering, and generated indexes. |
| `tradingcodex_service/` | Django project, web/API/MCP surfaces, templates, static assets, and service entrypoints. |
| `tradingcodex_service/application/` | Canonical durable service behavior shared by CLI, Web, Admin, API, MCP, and generated hooks. |
| `tradingcodex_service/application/components.py` | Harness component registry and cross-surface maintenance map. |
| `tradingcodex_service/application/agents.py` | Fixed role registry, built-in skills, permission profiles, MCP allowlists, and projection behavior. |
| `apps/` | Django model/admin apps for policy, orders, portfolio, audit, MCP, integrations, workflows, and harness provenance. |
| `workspace_templates/modules/*/files` | Generated Codex prompts, agents, hooks, skills, policies, wrappers, and workspace contracts. |
| `docs/` | Human-readable product source of truth. |
| `openwiki/` | Agent-facing repository working map. |
| `tests/` | Pytest coverage and scenario contracts. |

Do not reintroduce Node roots such as `package.json`, `packages/*`, Node MCP runtime files, pre-release compatibility facades, or a Django `apps/universes` app unless product direction changes in `docs/`.

## Change Routing

| Change area | Read first | Usually validate |
| --- | --- | --- |
| CLI or wrapper behavior | `openwiki/quickstart.md`, `tradingcodex_cli/__main__.py`, relevant `tradingcodex_cli/commands/*` | focused pytest, generated workspace smoke |
| Django service behavior | `openwiki/architecture.md`, relevant `tradingcodex_service/application/*`, `docs/system-architecture.md` | `python -m pytest`, `python manage.py check` |
| Web, API, or MCP surface | `openwiki/interfaces-and-data.md`, `tradingcodex_service/web.py`, `tradingcodex_service/api.py`, `tradingcodex_service/mcp_runtime.py` | focused pytest, `python manage.py check`, MCP smoke when touched |
| Agent, workflow, skill, hook, or template behavior | `openwiki/workflows-and-agents.md`, `docs/harness.md`, `docs/roles-skills-and-workflows.md`, `workspace_templates/modules/*/files` | generated workspace smoke, Codex-native smoke when behavior changes |
| Policy, approval, broker, secret, or execution boundary | `openwiki/safety-and-execution.md`, `docs/safety-policy-and-execution.md`, policy/order/broker/MCP services | focused pytest, `python manage.py check`, order/MCP smoke as applicable |
| Research memory or artifact quality | `openwiki/interfaces-and-data.md`, `docs/research-memory-and-artifacts.md`, `tradingcodex_service/application/research.py`, `tradingcodex_service/application/artifact_quality.py` | research create/search/export, strict quality check |
| Package, release, or install flow | `docs/deployment.md`, `installation.md`, `pyproject.toml`, `MANIFEST.in` | packaging/release checks from `docs/deployment.md` |

## Development Commands

- `python -m pytest`: run the repository test suite configured in `pyproject.toml`.
- `python manage.py check`: validate Django settings, models, apps, admin, API, and service wiring.
- `python -m compileall tradingcodex_cli tradingcodex_service apps tests`: catch broad Python syntax/import issues.
- `python manage.py runserver 127.0.0.1:48267`: run the local web, admin, and API service.
- `python -m tradingcodex_cli attach /tmp/tcx-smoke && /tmp/tcx-smoke/tcx doctor`: smoke-test generated workspace behavior.

## Coding Rules

Target Python `>=3.11,<3.15` and Django `5.2.x`. Use four-space indentation, clear module-level service functions, and type hints where they clarify contracts.

Web, Admin, Django Ninja, MCP, CLI, and generated hooks must call shared application services instead of duplicating policy, approval, research, order, portfolio, audit, harness, or broker logic. Prefer direct canonical imports over pre-release compatibility facades.

Research artifacts and source snapshots are workspace-file-native, not Django DB models. Generated workspace template bodies should remain ordinary files under `workspace_templates/modules/*/files`; use Python for registry loading, dependency resolution, rendering, validation, and generated indexes, not to hide durable prompts, skills, policies, hooks, or workspace-contract content inside string constants.

TradingCodex targets global users. Keep repository code, durable docs, generated workspace guidance, prompts, tests, CLI help, UI copy, and examples in the project's default product language and language-neutral. Do not add language-specific literals, keyword lists, escape-hidden localized strings, or examples tied to one natural language unless the change explicitly builds a reviewed localization layer.

## Harness And Agent Changes

Do not infer agent, workflow, MCP, policy, template, or harness behavior from Python code alone. Treat docs, skill bodies, role TOML, hooks, policies, generated workspace files, service-layer code, and tests as one product contract.

Before changing those surfaces, read the relevant OpenWiki page and source docs, especially:

- `docs/harness.md`
- `docs/roles-skills-and-workflows.md`
- `docs/generated-workspaces.md`
- `docs/safety-policy-and-execution.md`
- `tradingcodex_service/application/components.py`
- `workspace_templates/modules/*/files/.agents/skills/*/SKILL.md`
- `workspace_templates/modules/*/files/.codex/agents/*.toml`
- `workspace_templates/modules/*/files/.codex/prompts/*`
- `workspace_templates/modules/*/files/.codex/hooks/*`
- `workspace_templates/modules/*/files/.tradingcodex/policies/*`
- `workspace_templates/modules/*/files/.tradingcodex/workflows/*`

For agent skill authoring, use `$skill-creator` for generic skill discipline before applying TradingCodex-specific projection rules. Treat every skill as a folder bundle: `SKILL.md` is required, `agents/openai.yaml` is required for TradingCodex UI/projection, and `scripts/`, `references/`, or `assets/` should be included when they make the skill more reliable.

Keep durable role identity, role eligibility, MCP allowlists, permission profiles, approval authority, execution authority, and policy boundaries out of skill bodies. Those belong to base instructions, `.codex/agents/*.toml`, `ROLE_SKILL_MAP`, service-layer policy, and generated projection indexes.

Do not hand-roll optional or strategy skill state around shared services. For optional subagent skills, use `./tcx skills optional create|update ...` so frontmatter, `agents/openai.yaml`, `agents/tradingcodex.json`, validation, status, and TOML projection stay aligned. For user strategy skills, route authoring through `strategy-creator`, CLI, API, or the shared service so `strategy-*` naming, required frontmatter, required strategy sections, `agents/openai.yaml`, active/archive status, and root projection are validated.

After agent-skill changes, validate generated shape with `./tcx doctor --layer improvement`, `./tcx skills list --all`, the affected `./tcx subagents skills <role>` or `./tcx strategies inspect <name>`, and generated `.tradingcodex/generated/skill-index.json` plus `.tradingcodex/generated/projection-manifest.json` in a disposable workspace.

## Validation Expectations

Use the smallest meaningful validation while iterating, then broaden when scope justifies it.

Run focused pytest for source changes. Run `python manage.py check` after Django settings, model, admin, API, MCP, or service wiring changes. Run `python -m compileall tradingcodex_cli tradingcodex_service apps tests` after broad import, packaging, or migration changes.

Harness, agent, workflow, MCP, policy, skill, hook, or template changes need Codex-native validation, not just repository tests:

```bash
rm -rf /tmp/tradingcodex-harness-smoke
python -m tradingcodex_cli attach /tmp/tradingcodex-harness-smoke
cd /tmp/tradingcodex-harness-smoke
./tcx doctor
./tcx doctor --layer codex-native
./tcx doctor --layer improvement
./tcx subagents status
./tcx skills list --all
./tcx subagents prompt "Analyze NVDA. No order, no trading, no valuation."
printf '{"prompt":"Analyze NVDA. No order, no trading, no valuation."}\n' | python .codex/hooks/tradingcodex_hook.py user-prompt-submit
```

When skill text, role TOML, head-manager instructions, hooks, routing, or handoff behavior changes, also run a real Codex CLI smoke from the disposable workspace when available:

```bash
codex exec -C /tmp/tradingcodex-harness-smoke --skip-git-repo-check --dangerously-bypass-hook-trust --output-last-message /tmp/tradingcodex-codex-smoke.txt \
  'Harness smoke only. Do not produce investment analysis. Confirm the TradingCodex head-manager instructions loaded, identify the selected team for "Analyze NVDA. No order, no trading, no valuation.", and stop at dispatch/waiting status.'
```

Inspect `/tmp/tradingcodex-codex-smoke.txt`, `.tradingcodex/mainagent/latest-workflow-intake.json`, `.tradingcodex/mainagent/latest-workflow-plan.json` when present, `.tradingcodex/mainagent/subagent-session-state.json` when present, and `trading/audit/codex-hooks.jsonl`. If Codex CLI or authentication is unavailable, record that blocker and still run generated workspace, hook, and starter-prompt checks.

Treat a smoke as failed if `head-manager` gives substantive investment analysis before accepted subagent artifacts, expands beyond the selected team, ignores negated scope such as `no order` or `no valuation`, bypasses role/tool boundaries, or cannot state `waiting`, `revise`, `blocked`, or accepted handoff status.

Research-memory changes should verify file-native create, search, source-snapshot, and export flows. MCP changes should verify `tools/list`, role allowlists, and audit behavior when touched:

```bash
printf '{"jsonrpc":"2.0","id":1,"method":"tools/list"}\n' | ./tcx mcp stdio
```

Template or bootstrap changes must regenerate a clean workspace. Hand edits in `/tmp` smoke workspaces are debugging only, not durable fixes.

## Git, Security, And PRs

Recent history uses short imperative subjects such as `Make agent skill config file-native` and `Tighten TradingCodex handoff routing`. Keep commits focused and avoid trailing periods in subject lines. PRs should summarize behavior changes, list validation commands, link related issues, and call out docs, template, migration, or UI changes. Include screenshots only for visible web UI updates.

Do not store broker API keys, tokens, or secrets in this repository. The default runtime DB is `~/.tradingcodex/state/tradingcodex.sqlite3`; `TRADINGCODEX_WORKSPACE_ROOT` is provenance only. Live broker adapters remain disabled by default. Execution-sensitive actions must flow through service-layer policy, approval/idempotency, adapter, and audit paths.

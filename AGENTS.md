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

## Agent Skill Authoring

When creating or substantially updating agent skills, use `$skill-creator` for
generic skill authoring discipline before applying TradingCodex-specific
projection rules. Treat every skill as a folder bundle, not a lone `SKILL.md`:
`SKILL.md` is required, `agents/openai.yaml` is required for TradingCodex UI and
projection, and `scripts/`, `references/`, or `assets/` should be included when
they make the skill more reliable or avoid bloating the main skill body. Skills
must be concise, dependency-light capability procedures with `SKILL.md`
frontmatter and a matching folder/name. Keep durable role identity, role
eligibility, MCP allowlists, permission profiles, approval authority, execution
authority, and policy boundaries out of skill bodies; those belong to base
instructions, `.codex/agents/*.toml`, `ROLE_SKILL_MAP`, service-layer policy,
and generated projection indexes.

Do not hand-roll optional or strategy skill state around the shared services.
For role-local optional subagent skills, author the procedure with
`$skill-creator` expectations, then create/update it through the shared
TradingCodex path such as `./tcx skills optional create|update ...` so
frontmatter, `agents/openai.yaml`, `agents/tradingcodex.json`, validation,
status, and TOML projection stay aligned. Optional skills may add work style,
checklists, evidence-quality rules, or output shapes inside an existing role
boundary only; they must not change locked core skills or widen tool,
permission, approval, execution, secret, or broker surfaces.

For user strategy skills, route authoring through `strategy-creator`, CLI, API,
or the shared service so `strategy-*` naming, required frontmatter, required
strategy sections, `agents/openai.yaml`, active/archive status, and root
projection are validated. Strategy bodies must remain standalone judgment
procedures and must not mention TradingCodex roles, MCP, approval gates,
execution gates, policy overrides, or handoff mechanics.

After agent-skill changes, validate the exact generated shape: run
`./tcx doctor --layer improvement`, inspect `./tcx skills list --all`, inspect
the affected role with `./tcx subagents skills <role>` or the affected strategy
with `./tcx strategies inspect <name>`, and review
`.tradingcodex/generated/skill-index.json` plus
`.tradingcodex/generated/projection-manifest.json` in a disposable generated
workspace.

## Testing Guidelines

Use pytest; test files and functions should follow `test_*.py` and `test_*`.
Run focused tests while iterating, then `python -m pytest` before handoff when
scope justifies it. Run `python manage.py check` after Django settings, model,
admin, API, MCP, or service wiring changes, and run `python -m compileall
tradingcodex_cli tradingcodex_service apps tests` after broad import,
packaging, or migration changes.

Harness, agent, workflow, MCP, policy, skill, hook, or template changes need
Codex-native validation, not just repository tests. Bootstrap a disposable
workspace from the current checkout and inspect the generated contract:

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

When skill text, role TOML, head-manager instructions, hooks, routing, or
handoff behavior changes, also run a real Codex CLI smoke from the disposable
workspace so the generated `AGENTS.md`, `.codex/config.toml`, hook context, and
skill discovery load together:

```bash
codex exec -C /tmp/tradingcodex-harness-smoke --skip-git-repo-check --dangerously-bypass-hook-trust --output-last-message /tmp/tradingcodex-codex-smoke.txt \
  'Harness smoke only. Do not produce investment analysis. Confirm the TradingCodex head-manager instructions loaded, identify the selected team for "Analyze NVDA. No order, no trading, no valuation.", and stop at dispatch/waiting status.'
```

Inspect `/tmp/tradingcodex-codex-smoke.txt`,
`.tradingcodex/mainagent/latest-user-prompt-gate.json`,
`.tradingcodex/mainagent/subagent-session-state.json` when present, and
`trading/audit/codex-hooks.jsonl`. Passing pytest alone is insufficient when
the user-visible behavior depends on prompt text, skill bodies, role allowlists,
or generated workspace projection. If Codex CLI or authentication is
unavailable, record that blocker and still run the generated workspace, hook,
and starter-prompt checks above.

Judge agent behavior and artifacts as product quality, not just command
success. Treat a smoke as failed if `head-manager` gives substantive investment
analysis before accepted subagent artifacts, expands beyond the selected team,
ignores negated scope such as "no order" or "no valuation", bypasses role/tool
boundaries, or cannot state `waiting`, `revise`, `blocked`, or accepted
handoff status. Treat generated role artifacts as failed if they miss the
artifact path, source/as-of or retrieved-at posture, claim discipline
(`[factual]`, `[inference]`, `[assumption]` where material), confidence,
missing evidence, readiness/support gaps, role-boundary conflicts, next
eligible recipient, or blocked actions. Downstream roles should return
`revise`, `blocked`, or `waiting` for weak upstream work instead of filling
another role's missing analysis. For skill or prompt changes that affect a
specific role, run at least one Codex or generated-workspace scenario that
exercises that path and inspect the final message plus any written artifacts;
convert reproducible quality regressions into tests or template/doc fixes.

Research-memory changes should verify file-native create, search,
source-snapshot, and export flows. MCP changes should verify `tools/list`, role
allowlists, and audit behavior when touched:

```bash
printf '{"jsonrpc":"2.0","id":1,"method":"tools/list"}\n' | ./tcx mcp stdio
```

Template or bootstrap changes must regenerate a clean workspace; hand edits in
`/tmp` smoke workspaces are debugging only, not durable fixes.

## Commit & Pull Request Guidelines

Recent history uses short imperative subjects such as `Make agent skill config file-native` and `Tighten TradingCodex handoff routing`. Keep commits focused and avoid trailing periods in subject lines. PRs should summarize behavior changes, list validation commands, link related issues, and call out docs, template, migration, or UI changes. Include screenshots only for visible web UI updates.

## Security & Agent-Specific Instructions

Do not store broker API keys, tokens, or secrets in this repository. The default runtime DB is `~/.tradingcodex/state/tradingcodex.sqlite3`; `TRADINGCODEX_WORKSPACE_ROOT` is provenance only. Live broker adapters remain disabled. Execution-sensitive actions must flow through service-layer policy, approval/idempotency, adapter, and audit paths. Treat `README.md`, `installation.md`, and `docs/` as durable product docs, with `docs/` as the source of truth; update them when product rules, workflows, templates, policy behavior, MCP tools, Admin behavior, or release-facing messaging changes.

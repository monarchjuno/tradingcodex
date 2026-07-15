# TradingCodex Repository Guide

## Canonical Documentation

Read in this order:

1. [OpenWiki quickstart](openwiki/quickstart.md) for source and validation routing.
2. This file for repository-wide non-negotiable rules.
3. [docs/README.md](docs/README.md) for canonical product documentation.
4. [guidebook/index.html](guidebook/index.html) when a change affects user setup or everyday use.

`docs/` is the durable product source of truth. `guidebook/` is the concise,
task-first public guide. `openwiki/` is the agent working map. Keep one concept
canonical in `docs/` and link to it from the other layers.

For every non-trivial change, update the affected documentation in the same
change:

| Layer | Update when |
| --- | --- |
| `docs/` | Behavior, policy, architecture, safety, workflow, installation, validation, or release intent changes. |
| `guidebook/` | User setup, skill use, output, viewer, recovery, customization, provider/order, or everyday safety flow changes. |
| `README.md` | Product promise, primary install path, or top-level user route changes. |
| `openwiki/` | Agent source ownership or validation routing changes. |
| `AGENTS.md` | Repository-wide development or validation rules change. |

When these layers disagree, fix the mismatch from `docs/`. In the handoff,
state which layers changed and why a relevant user-facing layer did not change.

## Setup Guard

For a user workspace, do not clone this source repository. From the target
directory, run:

```bash
uvx --refresh --from tradingcodex tcx attach . && ./tcx doctor
```

On native Windows PowerShell, run
`uvx --refresh --from tradingcodex tcx attach .`, then `.\tcx.cmd doctor`.
Generated workspaces provide both launchers; use the platform-native one. Clone
this repository only to develop, inspect, or modify TradingCodex source.

## Development Bootstrap Isolation

Keep source development and release workspaces on separate runtime identities.
From this checkout, create or refresh a development workspace with:

```bash
./install.sh --dev /path/to/empty-workspace
./install.sh --dev --update /path/to/existing-dev-workspace
```

The direct CLI equivalents are `tcx attach/update ... --dev` when the command
is running from the checkout. Do not combine `--dev` with `--from`, and do not
convert an index/release workspace in place; attach a separate development
workspace.

Without explicit overrides, development attach derives a checkout-scoped
`TRADINGCODEX_HOME`, central DB, and deterministic loopback service port in the
`20000`-`29999` range. Workspaces from the same checkout intentionally share
that development ledger/service. Different checkouts and release workspaces
must remain isolated; the release default service address is
`127.0.0.1:48267`. Development update preserves the workspace's recorded home
and explicit DB override.

Never hard-code `48267` for generated-workspace diagnostics or stop a service
belonging to another home/DB merely to free the port. Run the generated wrapper
commands `./tcx service status --json`, `./tcx service ensure`, and
`./tcx service stop`; they default to the projected
`TRADINGCODEX_SERVICE_ADDR`. A version/DB mismatch must remain fail-closed.
After bootstrap, MCP, or service-address changes, regenerate a disposable dev
workspace, run `./tcx doctor`, and perform the documented Codex CLI smoke.

## Product Boundaries

- Django application services are canonical. Web, Admin, API, MCP, CLI, and
  generated hooks must reuse them rather than create a parallel policy, order,
  research, portfolio, broker, approval, or audit path.
- The workspace viewer is read-only. Native Codex owns analysis and dispatch;
  browser routes must not start Codex or mutate workspace, skill, order, broker,
  or execution state.
- Research artifacts and source snapshots are workspace-file-native. Portfolio,
  account, order, approval, execution, and audit state belong to the central
  service ledger.
- The harness is a product contract, not only Python code. Evaluate agent
  behavior together with service code, skill bundles, prompts, role TOML,
  hooks, policies, generated workspace files, artifacts, and tests.
- Keep orchestration Codex-native. Native Codex and Head Manager interpret the
  mandate and dynamically dispatch exact fixed roles from accepted evidence;
  Django does not replace that with a semantic router, preset team, stored DAG,
  or generic-agent fallback.
- Generated workspaces remain Node-free. Node is only a maintainer dependency
  under `frontend/`; do not add a production Node server or run npm from
  `tcx attach` or `tcx update`.
- Sensitive final actions remain service-gated. Never put secrets in the
  repository, workspace files, prompts, shell output, APIs, MCP responses, or
  audit output. Live adapters remain disabled unless the documented gates allow
  them.

## Change Routing

| Change area | Read first | Validate |
| --- | --- | --- |
| CLI, attach/update, templates, hooks, or generated files | `openwiki/generated-workspaces.md`, `docs/generated-workspaces.md` | Focused tests and generated-workspace smoke. |
| Service, model, API, MCP, or viewer | `openwiki/interfaces-and-data.md`, `docs/interfaces-and-surfaces.md` | Focused tests; `python manage.py check`; frontend/MCP checks when touched. |
| Roles, skills, workflows, policy, brokers, or execution | `openwiki/workflows-and-agents.md`, `openwiki/safety-and-execution.md`, corresponding `docs/` topics | Required generated-workspace and Codex-native smoke. |
| Research, memory, artifacts, forecasts, or readiness | `docs/research-memory-and-artifacts.md`, `docs/decision-memory.md` | Relevant create/search/export and quality checks. |
| Public user guide | `guidebook/`, the owning `docs/` page, `docs/deployment.md` | Link/fragment check, local static preview, and `git diff --check -- guidebook`. |
| Package, install, or release | `installation.md`, `docs/deployment.md`, `docs/release-readiness.md` | Release checks documented there. |

## Implementation Rules

- Target Python `>=3.11,<3.15` and Django `5.2.x`. Keep service behavior in
  `tradingcodex_service/application/` and use direct canonical imports.
- Frontend source lives only in `frontend/`; commit its deterministic build in
  `tradingcodex_service/static/tradingcodex_web/`. Never hand-edit compiled
  assets.
- Keep durable prompts, skills, hooks, policies, and workspace contracts as
  ordinary files under `workspace_templates/modules/*/files`.
- Do not infer harness behavior from Python alone. Treat docs, templates, skill
  bundles, role TOML, hooks, policies, services, and tests as one contract.
- For generic skill authoring, use `$skill-creator` before the TradingCodex
  projection rules in `docs/roles-skills-and-workflows.md`. Bundled skills use
  the reserved `tcx-` namespace; user-owned Strategy, Investment Brain, and
  optional skills use their documented namespaces and managed lifecycle.
- Keep durable product copy, prompts, CLI help, docs, and examples in English
  unless a reviewed localization layer explicitly changes that rule.

## Validation And Delivery

Use the smallest meaningful validation while iterating, then apply the routing
table above. The complete command matrix and required harness smoke are in
[docs/validation-and-test-plan.md](docs/validation-and-test-plan.md) and
[openwiki/development-and-validation.md](openwiki/development-and-validation.md).

- Run focused pytest for source changes and `python manage.py check` after
  Django settings, model, admin, API, MCP, or service wiring changes.
- For frontend changes, run the frontend test/build, verify committed assets,
  and use the repository’s documented UI checks.
- When a change affects harness behavior, prompts, roles, skills, hooks,
  routing, handoffs, MCP boundaries, or generated workspace behavior, include
  the documented disposable-workspace Codex CLI E2E smoke whenever available.
  Verify observed native behavior and artifacts, not just code-level outcomes.
  For several coupled changes, run focused checks while iterating and one
  integrated E2E pass after the combined change set has stabilized.
- For guidebook changes, serve it locally with
  `python -m http.server 4173 --directory guidebook`; do not deploy, commit,
  push, or alter the GitHub Pages workflow unless explicitly requested.
- Template, bootstrap, harness, skill, role, hook, policy, or MCP changes need
  the relevant generated-workspace and Codex-native validation, not only unit
  tests.

Keep unrelated dirty worktree changes intact. Use focused, imperative commits
only when requested. PRs identify behavior, documentation, template, migration,
and UI changes and list the validation actually run.

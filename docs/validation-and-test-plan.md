# Validation And Test Plan

This document owns required validation commands, unit/API/generator/smoke test
coverage, and release-sensitive verification expectations.

## Default Validation

Run after source or test changes:

```bash
pytest
```

Run after Django settings, model, admin, API, MCP, or service changes:

```bash
python manage.py check
```

Run after broad Python migration, package, or import-structure changes:

```bash
python -m compileall tradingcodex_cli tradingcodex_service apps tests
```

## Unit Test Expectations

Unit tests should cover:

- policy decisions, restricted list, limits, capability checks
- order intent validation, approval validation, execution preconditions
- approved-order idempotency and duplicate execution blocking
- principal/capability checks before MCP handler dispatch and policy decisions
- universe routing and readiness labels
- adapter registry and disabled live adapter behavior
- audit append behavior and request/result hash generation
- DB-backed research artifact creation, versioning, search, source snapshot recording, and markdown export
- central DB path resolution through `TRADINGCODEX_HOME` and `TRADINGCODEX_DB_NAME`
- workspace identity/provenance recording without workspace-local DB partitioning
- duplicate research/order ids fail closed unless an explicit append/version path is used

## API And Admin Test Expectations

API/Admin tests should cover:

- Ninja endpoints return typed schemas and reject unauthorized calls
- Admin actions call service layer and create audit events
- Admin MCP registry, policy, skill, and adapter actions call service-layer helpers and create audit events
- `/mcp` handles JSON-RPC `initialize`, `tools/list`, and `tools/call`
- `/mcp` handles JSON-RPC batch requests and returns role/risk tool metadata
- MCP research tools store and retrieve markdown from Django DB
- MCP tool calls create DB ledger entries with request/result hashes
- generated `./tcx mcp ledger` can inspect the central DB tool-call ledger
- stdio bridge returns valid MCP messages and writes no non-MCP stdout

## Generated Workspace Smoke Tests

Run after template/bootstrap behavior changes:

```bash
rm -rf /tmp/tradingcodex-smoke
python -m tradingcodex_cli attach /tmp/tradingcodex-smoke
cd /tmp/tradingcodex-smoke
./tcx doctor
./tcx workspace status
./tcx profile status
```

Smoke coverage should verify:

- `tcx attach` and `tcx init` create the workspace contract
- generated workspace contains `.tradingcodex/workspace.json`
- generated workspace contains no `package.json` or Node MCP/runtime files
- generated workspace contains nine fixed subagents and twenty-one repo skills
- two generated workspaces have different workspace ids
- two generated workspaces share research memory and MCP ledger through the central DB
- profile selection controls paper portfolio separation
- root, `risk-manager`, and `execution-operator` MCP allowlists match role boundaries
- generated hooks are callable and classify routing/secret-warning cases

## Research Memory Smoke Tests

Run after research-memory changes:

```bash
./tcx research create
./tcx research append
./tcx research search
./tcx research export
```

The smoke flow should confirm:

- DB artifact creation
- source/as-of metadata preservation
- version and content hash updates
- duplicate create with changed content is rejected
- markdown export path generation
- workspace provenance recording
- no raw secrets in exported output

## MCP Smoke Tests

Run after MCP registry, handler, bridge, or role allowlist changes:

```bash
./tcx mcp stdio
./tcx mcp install-global --safe --print
```

Verify at least:

- `tools/list`
- tool annotations include category, risk, role allowlist, approval requirement, and audit requirement
- research/status tools are visible to `head-manager`
- approval creation is not visible to `head-manager`
- approval creation is visible only to the approved risk role path
- experimental execution tools are visible only to `execution-operator`
- `tradingcodex-home` safe scope exposes only read-only/status/search tools
- stdio emits no non-MCP logs to stdout

## Harness And Routing Tests

Run targeted scenario tests after harness or workflow routing changes. Inspect
logs/results rather than relying only on static checks.

Scenarios should include:

- broad investment request asks for workflow confirmation or starter prompt
- explicit `$orchestrate-workflow` routes to selected role team
- negated execution wording such as "no order" stays out of execution routing
- guardrail-verification wording does not trigger execution
- earnings/catalyst/valuation requests route to thesis-review style research
- unavailable or unverified subagent routing fails closed
- completed role artifacts are reused when quality gates pass

Harness taxonomy checks should confirm:

- product web presents Harness as the top-level concept
- Guardrails are split into Guidance, Enforcement, and Information barriers
- Improvement is separate from Guardrails
- `tcx doctor --layer improvement` runs the quality/workflow checks
- legacy `tcx doctor --layer task-harness` remains compatible if kept as an alias

## Release-Sensitive Validation

Before release or packaging changes, run:

```bash
python -m pytest
python manage.py check
python manage.py makemigrations --check --dry-run
python -m compileall tradingcodex_cli tradingcodex_service apps tests
python -m build
python -m twine check dist/*
```

Also install the built wheel in a clean environment and run:

```bash
tcx init .
./tcx doctor
```

Detailed release workflow lives in [deployment.md](./deployment.md).

# Deployment

TradingCodex is distributed as a Python package on PyPI. The package name is
`tradingcodex`; the installed command is `tcx`.

TradingCodex is local-first software. A PyPI release ships the CLI, Django
service plane, generated workspace templates, Admin/Web templates, static
assets, and MCP gateway code. It does not deploy a hosted service. Core ships
paper execution by default; broker-specific live execution requires installed,
reviewed providers and explicit live gates.

## Release Policy

The `0.2.x` release line is the OrderTicket rewrite contract for the local-first
Python/Django harness. Order flows use central DB `OrderTicket` records
directly; pre-release compatibility shims do not remain in the runtime package.
The documented Web, API, CLI, MCP, generated workspace, and
application-service surfaces are the supported contract.

Execution status for this release line:

- paper execution is built in
- validation and live-capable provider code must be installed/reviewed before use
- live submission is disabled by default and requires config, policy, environment, adapter, health, approval, confirmation, idempotency, sync, and audit gates
- execution MCP tools must stay behind policy, approval, duplicate-request,
  connection, and audit checks

## Maintainer Prerequisites

Use Python 3.11 for release build verification and keep CI green across the
supported range. The package metadata requires `>=3.11,<3.15`, and CI runs on
Python 3.11, 3.12, 3.13, and 3.14.

Create separate PyPI and TestPyPI accounts. TestPyPI is a separate service and
does not share login state with PyPI.

Configure Trusted Publishing before the first upload. Do not store long-lived
PyPI API tokens in GitHub repository secrets unless Trusted Publishing is not
available.

Trusted Publisher settings:

| Index | Project | Owner | Repository | Workflow | Environment |
| --- | --- | --- | --- | --- | --- |
| PyPI | `tradingcodex` | repository owner/org | repository name | `release.yml` | `pypi` |
| TestPyPI | `tradingcodex` | repository owner/org | repository name | `release.yml` | `testpypi` |

On GitHub, create both environments:

- `testpypi`: no manual approval required by default
- `pypi`: require manual approval before deployment

## Local Build Verification

Run the regular validation suite first:

```bash
python3.11 -m pytest
python3.11 manage.py check
python3.11 manage.py makemigrations --check --dry-run
python3.11 -m compileall tradingcodex_cli tradingcodex_service apps tests
```

Build and check the source distribution and wheel:

```bash
python3.11 -m pip install --upgrade build twine
rm -rf dist build
find . -maxdepth 1 -name '*.egg-info' -type d -exec rm -rf {} +
python3.11 -m build
python3.11 -m twine check dist/*
```

Install the built wheel in a clean environment:

```bash
python3.11 -m venv /tmp/tcx-install-test
/tmp/tcx-install-test/bin/pip install dist/*.whl
rm -rf /tmp/tcx-smoke
mkdir -p /tmp/tcx-smoke
cd /tmp/tcx-smoke
/tmp/tcx-install-test/bin/tcx attach .
./tcx doctor
```

## CI/CD

CI is defined in `.github/workflows/ci.yml`.

It runs on pull requests and pushes to `main` or `develop`:

- installs `tradingcodex` with development extras
- runs `pytest`
- runs `python manage.py check`
- checks that migrations are current
- compiles Python sources
- builds the package
- validates distribution metadata with `twine check`

Release automation is defined in `.github/workflows/release.yml`.

The release workflow is manual-only. Branch pushes and tag pushes must not
publish package artifacts to TestPyPI or PyPI.

The release workflow has additional guardrails:

- publication requires `workflow_dispatch`
- PyPI publication is allowed only from the `main` branch
- TestPyPI and PyPI publication must be run as separate manual workflow runs
- concurrent release runs on the same ref are serialized instead of cancelled

Manual `workflow_dispatch` can publish to TestPyPI when
`publish_testpypi=true`, and to PyPI when `publish_pypi=true`.

Keep both publish inputs set to `false` when the run should only build and
verify distributions.

The workflow uses PyPI Trusted Publishing. The publish jobs request only an
OIDC token through `id-token: write`; they do not require API-token secrets.

The workflow uses current GitHub artifact actions so release artifact upload
and download do not depend on the deprecated Node.js 20 action runtime.

## Existing Installation Update Notes

`tcx update` applies central DB migrations before the updated workspace is used.
Product flows create, check, approve, and submit `OrderTicket` records directly.

## TestPyPI Release

Use TestPyPI before the first public PyPI release and after packaging changes.

1. Confirm the local build verification steps pass.
2. Run the `Release` workflow manually with `publish_testpypi=true`.
3. Install from TestPyPI in a clean environment.

Example:

```bash
python3.11 -m venv /tmp/tcx-testpypi
/tmp/tcx-testpypi/bin/pip install \
  --index-url https://test.pypi.org/simple/ \
  --extra-index-url https://pypi.org/simple/ \
  tradingcodex==0.2.8
rm -rf /tmp/tcx-testpypi-smoke
mkdir -p /tmp/tcx-testpypi-smoke
cd /tmp/tcx-testpypi-smoke
/tmp/tcx-testpypi/bin/tcx attach .
./tcx doctor
```

`--extra-index-url` is used because dependencies such as Django may not be
available on TestPyPI.

## PyPI Release

Before pushing the release tag:

- verify `pyproject.toml` version is the intended release version
- verify `README.md` describes execution as service-gated
- verify docs mention that live broker execution requires installed providers and explicit gates
- run local build verification
- run a TestPyPI release when packaging changed

Then create or update the GitHub release/tag as needed, and run the `Release`
workflow manually with `publish_pypi=true`. Do not rely on tag push for
publication; tag pushes are intentionally non-publishing.

After the PyPI workflow completes:

```bash
python3.11 -m venv /tmp/tcx-pypi
/tmp/tcx-pypi/bin/pip install tradingcodex==0.2.8
rm -rf /tmp/tcx-pypi-smoke
mkdir -p /tmp/tcx-pypi-smoke
cd /tmp/tcx-pypi-smoke
/tmp/tcx-pypi/bin/tcx attach .
./tcx doctor
```

Also verify the user-facing installer path:

```bash
rm -rf /tmp/tcx-install-sh-smoke
sh ./install.sh --no-doctor /tmp/tcx-install-sh-smoke
cd /tmp/tcx-install-sh-smoke
./tcx doctor
```

Verify the user-facing update path against the same workspace:

```bash
cd /tmp/tcx-install-sh-smoke
sh /path/to/tradingcodex/install.sh --update --no-doctor .
./tcx doctor
```

## Update Policy

TradingCodex has two update layers:

- package update: install or run the desired PyPI/GitHub package with
  `uvx --refresh`, `uv tool install --upgrade`, or `install.sh --update`
- workspace update: run `tcx update <workspace>` from that package to refresh
  generated files, generated indexes, project MCP config, hook scripts, and
  central DB schema

Generated workspace `./tcx update` normally refreshes through `uvx` first so
stale recorded Python paths do not rewrite templates. In restricted Codex
permissions, `head-manager` should not run the update itself because it rewrites
protected `.codex` prompt/config/hook surfaces. When the package is already
installed and Codex startup health reports `workspace_update_allowed=true`,
`head-manager` should tell the user to switch to full access plus TradingCodex
build mode, or run the workspace-only path from a terminal:

```bash
./tcx update --skip-refresh
```

`head-manager` may run the update directly only when
`update_status.can_self_update=true`, which requires Codex full access,
unexpired TradingCodex build mode, and an explicit user request. After a
self-update it must stop and tell the user to fully restart Codex. The terminal
path avoids package-cache or user-tool writes and keeps self-modifying `.codex`
prompt/config/hook updates outside a restricted active Codex agent sandbox.
Generated Codex config declares the bounded `~/.tradingcodex` writable root so
central DB migrations, lock files, and update preferences can still work
without disabling the sandbox when the active Codex surface honors
project-scoped sandbox roots. If a package update is required first, the user
should run the package-refresh command from a terminal instead.

`tcx update` must preserve `.tradingcodex/workspace.json` identity fields,
including `workspace_id` and active profile. It may overwrite generated paths
owned by `workspace_templates/modules/*/files`, and it must not overwrite
workspace-native user artifacts such as `trading/research/*`,
`trading/reports/*`, `.agents/skills/strategy-*`, or optional role skills
except through their documented service-layer workflows.

After a workspace update, users must fully quit and restart Codex, then start
from a new thread in the updated workspace so project MCP config, prompts,
skills, and hooks are reloaded.

## Versioning

Use PEP 440 versions:

- `0.2.0` for the OrderTicket rewrite contract after install, docs, DB
  migration, generated workspace smoke checks, and release e2e checks are stable
- `0.2.1` for Python `>=3.11,<3.15` support and clone-free setup guidance
- `0.2.2` for dashboard startup behavior fixes after `0.2.1`
- `0.2.3` for workflow-planner UX, fixed strategy authoring, profile-scoped
  ticket isolation, workspace-scoped transition audit, and startup/status fixes
- `0.2.4` for the operate/build/execution plane rewrite, compact startup
  context, build-mode updates, and connector scaffold workflow
- `0.2.5` for packaged web static assets and startup service mismatch notices
  reaching head-manager compact context
- `0.2.6` for provider-driven Broker Center foundations, live-gated provider
  execution paths, runtime surface simplification, and stricter subagent skill
  boundaries after `0.2.5`
- `0.2.7` for Codex-native decision packages and investment decision quality
  spine improvements after `0.2.6`
- `0.2.8` for artifact-supervisor loop concurrency and run-specific workflow
  loop state after `0.2.7`
- later patch releases for compatible fixes after `0.2.8`
- pre-releases such as `0.3.0a1`, `0.3.0b1`, or `0.3.0rc1` when preparing
  the next minor contract

PyPI files are immutable. If a release has a packaging defect, publish the next
version instead of trying to replace the broken artifact.

## What Is Not Deployed

This PyPI release does not deploy:

- a hosted web service
- live broker adapters
- raw broker credential storage
- production execution infrastructure
- official commercial/verified adapter packs

Those surfaces require separate product decisions, separate documentation, and
the same service-layer policy, approval, duplicate-request, connection, and audit
boundary.

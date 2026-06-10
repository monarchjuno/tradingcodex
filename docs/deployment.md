# Deployment

TradingCodex is distributed as a Python package on PyPI. The package name is
`tradingcodex`; the installed command is `tcx`.

TradingCodex is local-first software. A PyPI release ships the CLI, Django
service plane, generated workspace templates, Admin/Web templates, static
assets, and MCP gateway code. It does not deploy a hosted service and it does
not ship live broker execution.

## Release Policy

The first public releases should use alpha versions, for example `0.1.0a1`.
This is intentional: TradingCodex contains investment workflow and execution
guardrail surfaces, so public release language should be conservative until
the package has install feedback from fresh environments.

Execution status for this release line:

- live broker execution is excluded
- paper/stub execution code remains in the package for local harness tests
- paper/stub execution is experimental and not production trading
- execution MCP tools must stay behind policy, approval, idempotency, adapter,
  and audit checks

## Maintainer Prerequisites

Use Python 3.14 for release verification. The package metadata requires
`>=3.14,<3.15`, and CI is pinned to Python 3.14.

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
python3.14 -m pytest
python3.14 manage.py check
python3.14 manage.py makemigrations --check --dry-run
python3.14 -m compileall tradingcodex_cli tradingcodex_service apps tests
```

Build and check the source distribution and wheel:

```bash
python3.14 -m pip install --upgrade build twine
rm -rf dist build
find . -maxdepth 1 -name '*.egg-info' -type d -exec rm -rf {} +
python3.14 -m build
python3.14 -m twine check dist/*
```

Install the built wheel in a clean environment:

```bash
python3.14 -m venv /tmp/tcx-install-test
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

## TestPyPI Release

Use TestPyPI before the first public PyPI release and after packaging changes.

1. Confirm the local build verification steps pass.
2. Run the `Release` workflow manually with `publish_testpypi=true`.
3. Install from TestPyPI in a clean environment.

Example:

```bash
python3.14 -m venv /tmp/tcx-testpypi
/tmp/tcx-testpypi/bin/pip install \
  --index-url https://test.pypi.org/simple/ \
  --extra-index-url https://pypi.org/simple/ \
  tradingcodex==0.1.0a8
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
- verify `README.md` describes execution as experimental
- verify docs mention that live broker execution is excluded
- run local build verification
- run a TestPyPI release when packaging changed

Then create or update the GitHub release/tag as needed, and run the `Release`
workflow manually with `publish_pypi=true`. Do not rely on tag push for
publication; tag pushes are intentionally non-publishing.

After the PyPI workflow completes:

```bash
python3.14 -m venv /tmp/tcx-pypi
/tmp/tcx-pypi/bin/pip install tradingcodex==0.1.0a8
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

## Versioning

Use PEP 440 versions:

- `0.1.0a1`, `0.1.0a2`, `0.1.0a3`, `0.1.0a4`, `0.1.0a5`, `0.1.0a6`, `0.1.0a7`, `0.1.0a8` for alpha releases
- `0.1.0b1` for beta releases
- `0.1.0rc1` for release candidates
- `0.1.0` only after install, docs, DB migration, and generated workspace
  smoke checks are stable

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
the same service-layer policy, approval, adapter, idempotency, and audit
boundary.

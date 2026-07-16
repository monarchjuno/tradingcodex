# Deployment

TradingCodex is distributed as the `tradingcodex` Python package on PyPI. The
installed command is `tcx`.

The package contains the CLI, Django service plane, generated workspace
templates, Django Admin, MCP gateway, and the committed React viewer build.
Django and WhiteNoise serve the viewer. End users and generated workspaces
do not need Node or npm.

## v1 Release Contract

`1.0.0` is the first supported public contract for the current Web, Admin, API,
CLI, MCP, generated workspace, application-service, policy, approval, audit,
and execution boundaries.

The v1 baseline is intentionally clean:

- new installations attach an empty workspace with `tcx attach`
- v1 workspaces update through the documented v1 package and workspace flow
- generated files, runtime state, and database schema use the v1 contract
- historical prerelease compatibility surfaces are not part of the v1 runtime
- paper execution is built in; live execution remains disabled by default and
  requires an installed provider plus every policy, approval, idempotency,
  connection, confirmation, sync, and audit gate

If the selected runtime database contains prerelease migrations or project
tables without clean v1 migration history, startup and bootstrap fail closed.
The error reports the selected `TRADINGCODEX_HOME` and database path.
TradingCodex does not migrate, delete, archive, or back up incompatible
prerelease/non-v1 state.

The operator must choose one explicit recovery path:

1. Select a new empty `TRADINGCODEX_HOME` outside the generated workspace. If
   `TRADINGCODEX_DB_NAME` is set, unset it or point it to a new empty database
   outside the workspace.
2. Stop every TradingCodex process, then archive or remove the old selected
   home/database manually before retrying.

There is no automatic prerelease migration or fallback path in v1.

TradingCodex releases software; they do not promote a model policy or establish
investment-performance claims. Those claims require the independent evidence
and review gates in [release-readiness.md](./release-readiness.md).

## Runtime Profiles

`local` is the default desktop profile. It may use the packaged development
secret and `DEBUG=True` only because `tcx service` refuses to bind it outside
loopback (`127.0.0.1`, `::1`, or `localhost`). Local anonymous HTTP viewer
access is read-only. Mutations require a bound API principal/key or an
authenticated staff session on their canonical non-viewer surfaces.

`remote` is an explicit operator-managed hardening profile. Before a
non-loopback bind, all of these environment-backed settings are mandatory:

| Setting | Required contract |
| --- | --- |
| `TRADINGCODEX_SERVICE_PROFILE` | `remote` |
| `TRADINGCODEX_DEBUG` | `0` |
| `TRADINGCODEX_SECRET_KEY` | Non-default value of at least 32 characters |
| `TRADINGCODEX_API_KEY` | API key of at least 32 characters |
| `TRADINGCODEX_API_PRINCIPAL` | Explicit mutation principal distinct from the key |
| `TRADINGCODEX_ALLOWED_HOSTS` | Explicit hosts; wildcard `*` is refused |
| `TRADINGCODEX_CSRF_TRUSTED_ORIGINS` | Explicit matching `https://` origins |
| `TRADINGCODEX_TRANSPORT_SECURITY` | `reverse-proxy` |

The remote profile enables HTTPS redirect, secure session and CSRF cookies,
HSTS, and trusted-proxy `X-Forwarded-Proto` handling. The reverse proxy must
remove client-supplied forwarded-protocol headers, set its own value, and keep
the backend listener private. Invalid remote settings fail before binding.

## Health, Processes, And Logs

- `GET /api/health/live` reports process liveness.
- `GET /api/health/ready` checks the central database, pending migrations, and
  mandatory state-directory writeability.
- service autostart, `tcx service status`, compatibility checks, and `doctor`
  consume readiness rather than treating a reachable process as ready.
- service logs rotate at 5 MiB with three backups by default.
- persisted logs redact known secrets, authorization values,
  credential-shaped fields, and URL user-info.

The browser is a read-only workspace viewer. It has no Codex subprocess,
preview, run, follow-up, or mutation route and never widens role, MCP, policy,
approval, broker, or execution authority.

## Versioning

`tradingcodex_service/version.py` is the only package version source.
`pyproject.toml` reads it dynamically, and `tcx --version` exposes the same
value. Public release tags use `v<version>`, for example `v1.0.0`.
`CHANGELOG.md` keeps durable release notes with an `Unreleased` section and is
included in the source distribution.

Use canonical PEP 440 versions:

- increment `MAJOR` for a deliberately incompatible public contract
- increment `MINOR` for backward-compatible capability additions within the
  current major line
- increment `PATCH` for backward-compatible fixes
- use `aN`, `bN`, or `rcN` only for an explicitly planned prerelease
- do not publish development or local versions to PyPI

PyPI files are immutable. A packaging defect requires a new version; published
files are never replaced.

## Maintainer Prerequisites

Release verification uses Python 3.11 and Node 22. The hosted CI budget runs
the complete source, frontend, and package gates once on Python 3.11 instead
of multiplying every push across the supported Python range and native
platforms. Maintainers run additional supported-version or native-platform
checks locally or through an explicit temporary/manual workflow when a change
touches compatibility-sensitive code. Node is a maintainer-only build
dependency.

Configure PyPI Trusted Publishing for:

| Index | Project | Workflow | Environment |
| --- | --- | --- | --- |
| PyPI | `tradingcodex` | `.github/workflows/release.yml` | `pypi` |

The GitHub `pypi` environment should require manual approval. The publish job
requests only `id-token: write`; no long-lived PyPI token is required.

## Local Release Verification

Run product and source checks first:

```bash
npm ci --prefix frontend
npm test --prefix frontend
npm run build --prefix frontend
git diff --exit-code -- tradingcodex_service/static/tradingcodex_web
python3.11 -m pytest
python3.11 manage.py check
python3.11 manage.py makemigrations --check --dry-run
python3.11 -m compileall tradingcodex_cli tradingcodex_service apps tests
```

Build from a clean distribution directory and verify the exact artifacts:

```bash
python3.11 -m pip install --upgrade build twine
rm -rf dist build
find . -maxdepth 1 -name '*.egg-info' -type d -exec rm -rf {} +
python3.11 -m build
python3.11 -m twine check dist/*
python3.11 tests/platform_wheel_smoke.py --wheel-dir dist
python3.11 tests/release_upgrade_smoke.py --wheel-dir dist --from-version 1.0.2
```

The wheel smoke installs only the built wheel into a clean virtual environment,
checks distribution metadata against the runtime version, attaches a workspace,
validates native launchers and generated configuration, runs hooks and MCP
stdio, exercises the local service, and loads the packaged SPA and assets.
The release-upgrade smoke starts from the published `1.0.2` package, updates
that attached workspace with the built candidate wheel, and verifies preserved
workspace and runtime identity, user-owned state, and explicit home, DB, and
service-address settings alongside the refreshed generated contract. It also
preserves the exact provider approval identity and timestamp, compares every
byte in the immutable provider snapshot, and proves that the candidate runtime
loads that approved snapshot after the update.

Harness, role, prompt, skill, hook, policy, MCP, or generated-template changes
also require the disposable-workspace and Codex-native checks documented in
[validation-and-test-plan.md](./validation-and-test-plan.md).

## CI And Release Automation

`.github/workflows/ci.yml` is test-only. One Ubuntu/Python 3.11 job runs the
frontend build, complete Python suite and framework gates, clean package
construction, the wheel smoke, and the prior-release update smoke. Guide-only
pushes skip this workflow because
the Pages workflow validates and deploys only static guide files. CI never
publishes.

`.github/workflows/release.yml` is manual-only. Every run requires an explicit
`release_version`. A dry run may build from any selected ref with
`publish_pypi=false`. Publication additionally requires all of these conditions:

- the input is a canonical public PEP 440 version
- the input equals `TRADINGCODEX_VERSION`
- editable package metadata equals the same version
- the workflow ref is exactly `refs/tags/v<release_version>`
- the tagged commit is on `origin/main`
- the wheel and source-distribution filenames carry the same version
- `dist/` contains exactly that one wheel and one source distribution
- the built wheel passes the Ubuntu wheel and prior-release update smokes
- the protected `pypi` environment approves Trusted Publishing

The release workflow has one build/verification job. When publication is
requested, one protected PyPI job downloads that exact artifact and uploads it;
no job rebuilds the distribution after verification. Native macOS and Windows
release smokes are local or explicitly scheduled maintainer gates rather than
automatic jobs on every release.

## User Guide Pages

The public user guide is a static documentation site under `guidebook/`. It
uses a documentation-first layout with section navigation, a reading column,
and a local table of contents. Its task-first content covers setup, copy-ready
prompts, workspace viewing, reusable skills, and everyday recovery. The detailed
product rules remain canonical in `README.md`, `installation.md`, and `docs/`;
the guide links back to those sources rather than replacing them.

`.github/workflows/deploy-user-guide.yml` uses one job to configure, upload,
and deploy only `guidebook/` to GitHub Pages after a push to `main` that changes
the guide or its workflow. It can also be run manually. It does not build or
deploy the Python package, the Django service, or a production Node runtime.

GitHub Pages is configured to use **GitHub Actions** as its publishing source.
The guide for this repository is published at
`https://monarchjuno.github.io/tradingcodex/`.

## Publishing A Release

1. Set `TRADINGCODEX_VERSION`, move the release notes from `Unreleased` into
   the matching `CHANGELOG.md` version heading, and update
   `docs/release-readiness.md`.
2. Run the local release verification and every applicable release gate.
3. Commit the release, merge it to `main`, push, and wait for CI to pass.
4. Create and push the matching tag:

   ```bash
   RELEASE_VERSION="$(python3.11 -m tradingcodex_cli --version)"
   git tag -a "v$RELEASE_VERSION" -m "Release $RELEASE_VERSION"
   git push origin "v$RELEASE_VERSION"
   ```

5. In GitHub Actions, run `Manual Release` from that tag with the exact
   `release_version`. Use `publish_pypi=true` for an approved publication; use
   the optional `publish_pypi=false` rehearsal when release risk warrants an
   additional hosted build.

Pushing a branch or tag does not publish by itself.

## Post-Publish Verification

Pin the released version so verification cannot accidentally select a later
package. From the source checkout on POSIX:

```bash
SOURCE_ROOT="$(pwd)"
RELEASE_VERSION="$(python3.11 -m tradingcodex_cli --version)"
SMOKE_ROOT="$(python3.11 -c 'import tempfile; print(tempfile.mkdtemp(prefix="tcx-pypi-"))')"

python3.11 -m venv "$SMOKE_ROOT/venv"
"$SMOKE_ROOT/venv/bin/pip" install "tradingcodex==$RELEASE_VERSION"
"$SMOKE_ROOT/venv/bin/tcx" --version
mkdir "$SMOKE_ROOT/workspace"
(
  cd "$SMOKE_ROOT/workspace"
  "$SMOKE_ROOT/venv/bin/tcx" attach . --from "tradingcodex==$RELEASE_VERSION"
  ./tcx doctor
)

sh "$SOURCE_ROOT/install.sh" \
  --from "tradingcodex==$RELEASE_VERSION" \
  --no-doctor \
  "$SMOKE_ROOT/installer-workspace"
(
  cd "$SMOKE_ROOT/installer-workspace"
  ./tcx doctor
)
```

On native Windows PowerShell:

```powershell
$ReleaseVersion = "1.1.1"
$Workspace = Join-Path $env:TEMP "tcx-pypi-$ReleaseVersion"
New-Item -ItemType Directory -Force $Workspace | Out-Null
Set-Location $Workspace
uvx --refresh --from "tradingcodex==$ReleaseVersion" tcx attach . --from "tradingcodex==$ReleaseVersion"
.\tcx.cmd doctor
```

Verify the PyPI project page, filenames, metadata, and release notes after the
smokes pass.

## v1 Update Policy

TradingCodex has two update layers within the v1 line:

- package update: run the desired package with `uvx --refresh`, upgrade an
  installed `uv` tool, or use `install.sh --update`
- workspace update: run `tcx update <workspace>` from that package to refresh
  generated files, indexes, project MCP configuration, hooks, and the v1 schema

`tcx update` preserves workspace identity and workspace-native user artifacts.
It may replace only generated paths owned by
`workspace_templates/modules/*/files`. After an update, fully quit and restart
Codex and open a new task so project configuration, prompts, skills, hooks, and
MCP state reload together.

A future major-version migration requires its own explicit product contract and
validation. v1 release automation does not infer one.

## What Is Not Deployed

The PyPI release does not deploy:

- a hosted web service
- a production Node server or Node runtime requirement
- live broker adapters
- raw broker credential storage
- production execution infrastructure
- official commercial or verified adapter packs

Those surfaces require separate product, security, deployment, and validation
decisions.

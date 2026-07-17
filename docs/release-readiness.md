# TradingCodex 1.1.2 Release Status

Status: local source, generated-workspace, native Codex, and exact-artifact
gates complete; candidate CI, tag, publication, and post-publish verification
remain pending
Updated: 2026-07-17

This page records the current `1.1.2` candidate state. It does not treat a
version bump or source changes as proof that the exact distribution artifacts
or public release have passed their gates. TradingCodex `1.1.1` remains the
published release until `1.1.2` completes every gate below. Public `1.0.2`
remains the oldest supported upgrade-smoke baseline.

## v1.1.2 Contract

- `tradingcodex_service/version.py` remains the single version source for
  package metadata, `tcx --version`, generated workspaces, release input, and
  the `v1.1.2` tag.
- Managed Build, Brain, Strategy, and order invocations share one lexical
  parser. The parser accepts the documented plain-token and exact workspace
  skill-link forms from the first meaningful line while preserving the
  existing proof, profile, scope, approval, idempotency, and single-effect
  boundaries.
- The Build profile permits credential-free public HTTP(S) reads and public
  HTTPS Git fetches into an inert scratch source tree. Authentication,
  uploads, non-public destinations, dependency execution, publication,
  direct writes into managed trading paths, and broker effects remain blocked.
- Build edits use `apply_patch`; its shell is limited to public GET/HEAD,
  enumerated read-only HTTPS Git, limited workspace `pwd`/`cat`/`ls`, inert
  provider-source reads/hash/diff/Git inspection, exact isolated `py_compile`,
  and allowlisted workspace-launcher commands. General interpreters, helper
  scripts, test runners, build systems, shell composition, and model-authored
  POST are blocked throughout every active Build turn/profile.
- Provider source provenance is included in bundle and immutable snapshot
  hashes when present. Provenance remains optional for safe existing manual
  providers, while secret-bearing, VCS-bearing, or symlinked bundles fail
  closed and require operator correction and review.
- Provider onboarding is provider-first: implementation and static inspection,
  operator hash approval, service restart, and only then connector registration
  and validation. A missing provider no longer produces a connector scaffold
  unless the user explicitly requests scaffold-only work.
- The retired External MCP Gate models, CLI, broker-import path, and blanket
  hook block are removed. A forward migration deletes only Gate-derived broker
  connections and drops the Gate tables; ordinary broker, order, and append-only
  audit state remains intact.
- User-installed MCP servers, skills, plugins, apps, and hooks remain native
  Codex-owned BYOR capabilities for root and fixed agents. TradingCodex does not
  recommend, classify, or manage them and exposes only a secret-free read-only
  inventory. Its own principals, grants, order proofs, protected state, and
  execution path remain service-gated.
- Django application services remain canonical, the viewer remains read-only,
  and live broker actions remain disabled unless every existing execution gate
  succeeds.
- Reusable tabular evidence is stored as immutable Dataset manifests and
  content-addressed canonical Parquet payloads. Conclusion-relevant numerical
  work is stored as immutable CalculationSpec/Run objects and only an exact
  full-fingerprint success can create a current-workflow reuse Run.
- Normal GitHub pushes never construct release distributions or start the
  platform runtime matrix. User-guide deployment and package publication both
  require an explicit manual workflow dispatch.
- PyPI publication remains manual, tag-bound, and protected by the `pypi`
  environment. The release workflow must build once, verify the exact uploaded
  artifacts, and publish those same artifacts without rebuilding.

## Upgrade Contract From 1.0.2

Operators should pin both the package runner and workspace update to the exact
candidate version:

```bash
uvx --refresh --from "tradingcodex==1.1.2" \
  tcx update . --from "tradingcodex==1.1.2"
./tcx doctor
```

The update must preserve the workspace id, paper scope, workspace-native
research and source snapshots, user-owned Investment Brains and Strategies,
connector state, recorded runtime home, explicit DB override, and custom
service address. Generated paths owned by TradingCodex are refreshed in place.
After updating, the operator must fully quit and restart Codex, open a new task,
and review and trust the changed projected hooks again.

The transient `$TRADINGCODEX_SCRATCH` path is regenerated under the platform
cache tree in `1.1.2`. The updater does not migrate the prior OS-temporary
scratch contents because they are disposable intermediates rather than
workspace-owned state.

Safe existing manual providers keep their approved bundle and immutable
snapshot hashes because provenance is optional and the legacy bundle hash
contract is unchanged. The upgrade smoke also preserves the approval row
identity and timestamp, compares the complete immutable snapshot byte for byte,
and requires the candidate runtime to load the approved provider from that
snapshot. A provider that newly fails the secret, VCS metadata, or symlink
checks is intentionally not grandfathered or silently re-approved; it remains
unavailable until corrected, inspected, approved, and restarted.

The exact cross-version gate starts from the public `1.0.2` package, attaches a
workspace, installs the built `1.1.2` wheel, runs `tcx update`, and proves that
the preserved identity and user-owned state coexist with the new generated
contract:

```bash
python3.11 tests/release_upgrade_smoke.py \
  --wheel-dir dist \
  --from-version 1.0.2
```

## Current Readiness

| Area | Current state | Required evidence or remaining gate |
| --- | --- | --- |
| Version identity | Verified locally | Runtime, dynamic metadata, release input, and artifact filenames agree on `1.1.2`; the tag identity remains a post-CI gate. |
| Schema compatibility | Verified locally | Django check, migration dry-run, compileall, the full suite, and the cross-version smoke cover the forward removal of retired Gate tables. |
| Workspace update | Exact cross-version smoke passed | Public `1.0.2` attach through built-wheel `1.1.2` update preserved workspace/runtime identity, explicit paths, user-owned state, and provider approval/snapshot state while refreshing the Dataset/Calculation contract. |
| Invocation and order safety | Focused and full suites passed | Parser matrices and gateway, proof, order grammar, approval, idempotency, Plan, subagent, profile, and cross-scope checks passed together. |
| Build shell boundary | Generated and native smokes passed | The narrow review lane succeeded and general interpreters, helpers, tests, build systems, composition, direct runtime commands, and unified exec remained closed. |
| Build public fetch | Generated and native smokes passed | Public HTTP(S) and read-only HTTPS Git succeeded; private, credentialed, mutating, executable, direct-managed-path, missing-parent, nested, and pathname-expanding requests failed closed. |
| Provider supply chain | Verified locally | Provenance hashing, stale-approval invalidation, legacy compatibility, AST and bundle checks, pre-approval import denial, and bundle-only inspection fallback passed. |
| Frontend and guidebook | Verified locally | Ten viewer tests, typecheck/build, deterministic asset comparison, guide link/fragment checks, local route preview, and diff checks passed. |
| Distribution artifacts | Exact local candidate passed | Fresh sdist/wheel, `twine check`, packaged-wheel smoke, and public `1.0.2` to candidate `1.1.2` upgrade smoke passed on macOS. |
| Native Codex acceptance | Verified on the installed CLI | The disposable workspace passed doctor, strict config, fixed-role dispatch, provider inspection, and positive/negative capability probes; doctor records that the installed client is newer than the validated reference. |
| Candidate CI | Pending | The final commit on `main` must pass all required GitHub Actions checks before tagging. |
| Git tag and PyPI | Not performed | The annotated `v1.1.2` tag and protected tag-bound publication follow green candidate CI. |
| Post-publish verification | Blocked on publication | Exact-version POSIX and native Windows attach, update, doctor, artifact metadata, and public `1.0.2` to public `1.1.2` checks run only after PyPI contains the immutable artifacts. |

The public `1.0.2` release and its PyPI artifacts are the immutable comparison
baseline for this upgrade. Historical validation remains available in Git
history and the `v1.0.2` release; it does not substitute for `1.1.2` validation.

The final local candidate evidence recorded on 2026-07-17 includes 870 collected
Python tests with one expected platform skip and no failures; Django, migration,
compile, Ruff, frontend, guide, and diff checks; a disposable updated workspace
on the exact reference Codex CLI; and fresh macOS distribution and cross-version
smokes.
Hosted Ubuntu source CI, the explicitly dispatched tag-bound build/runtime
matrix, protected publication, and public-package post-publish checks remain
deliberately separate gates.

## Final-Commit Validation

Run the source, frontend, schema, guide, and distribution gates documented in
[deployment.md](./deployment.md) and
[validation-and-test-plan.md](./validation-and-test-plan.md). The final
candidate artifact gates include:

```bash
python3.11 -m build
python3.11 -m twine check dist/*
python3.11 tests/platform_wheel_smoke.py --wheel-dir dist
python3.11 tests/release_upgrade_smoke.py \
  --wheel-dir dist \
  --from-version 1.0.2
```

For the Dataset/Calculation release, the explicitly dispatched release workflow must record
the Python 3.11–3.14 x86-64 Linux/Intel-macOS/native-Windows hash-locked wheel
matrix before publication. Preserve its runtime doctor, prepared/exploratory
and exact-reuse smoke results alongside v3 catalog rebuild/fallback evidence,
Dataset/Calculation native E2E, exact reuse/cache-miss behavior, and private-
ledger non-persistence. A missing wheel or source-build fallback is a release
blocker.

Harness, role, prompt, skill, hook, policy, MCP, or generated-template changes
also require a disposable development workspace and real Codex CLI acceptance.
That acceptance must exercise the supported invocation forms, public HTTP(S)
GET/HEAD, the read-only Git Smart HTTP transport exception, rejection of
model-authored/general POST, the narrow Build read/hash/diff/inspection,
isolated-`py_compile`, and allowlisted-launcher lane, plus rejection of general
interpreters, helper scripts, test runners, build systems, and shell
composition. Bundle-only provider inspection must not be confused with
canonical approval state. It must prove that hook trust and every negative
safety probe remain fail-closed. A missing Codex CLI login is recorded as a blocker; it does not
waive the generated-workspace, hook, configuration, or provider-safety gates.

## Tag And Publication State

This document does not assert that the final commit is on `main`, CI is green,
the tag exists, or PyPI contains `1.1.2`. After all final-commit and artifact
gates pass:

1. Push the release commit to `main` and wait for required CI to pass.
2. Create and push the annotated tag `v1.1.2` at that exact commit.
3. When release risk warrants an extra hosted rehearsal, run Manual Release
   from the tag with `publish_pypi=false` and `release_version=1.1.2`.
4. With protected-environment approval, run the tag with
   `publish_pypi=true` and `release_version=1.1.2`.
5. Verify the immutable PyPI files and metadata, create or verify the GitHub
   release notes, then run exact-version fresh-install and cross-version update
   smokes on the published package.

## Claim Boundary

Software release readiness does not establish model superiority, investment
performance, return improvement, or financial safety. Any such claim requires
separated replay, holdout, live-forward, and postmortem evidence; trusted corpus
provenance; zero permitted hard-safety failures; and blind human review.

## Explicit Non-Goals

- A hosted service or production Node runtime.
- Built-in live broker providers or relaxed execution gates.
- A second agent orchestration stack beneath Codex subagents.
- A frontend state framework, universal outbox, graph database, or speculative
  interface facade.
- Investment-performance or model-superiority claims without the required
  evidence and blind review.

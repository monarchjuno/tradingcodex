# TradingCodex 1.0.1 Release Status

Status: local source, frontend, disposable native workflow, and exact
distribution gates complete; candidate CI, tag-bound rehearsal, tag, and
publication remain pending
Updated: 2026-07-15

This page records the current `1.0.1` release state. It is not an implementation
roadmap and does not treat source changes as proof that an exact distribution
artifact or public release has passed its gates.

## v1.0.1 Contract

- `tradingcodex_service/version.py` is the single version source for package
  metadata, `tcx --version`, generated workspaces, release input, and tags.
- New installations attach v1 to an empty workspace. Workspace and runtime
  state use one canonical v1 shape.
- Each TradingCodex Django app starts from one `0001_v1_initial` migration.
  Ordinary forward migrations carry later v1 schema changes.
- Django application services own durable behavior shared by Web, Admin, API,
  MCP, CLI, and generated hooks.
- The workspace viewer is read-only and starts no Codex process. Policy,
  approval, idempotency, broker, execution, redaction, and audit boundaries
  stay service-owned and fail closed.
- Paper execution is built in. Live submission remains disabled by default and
  requires an installed provider plus every documented safety gate.
- The React viewer is a committed static build served by Django and
  WhiteNoise; Node remains a maintainer-only build dependency.
- PyPI publication is manual, tag-bound, and reuses one verified sdist and
  wheel across Ubuntu, macOS, Windows, and publication jobs.

## Current Readiness

| Area | Current state | Evidence or remaining gate |
| --- | --- | --- |
| Version identity | Verified in the working tree | `TRADINGCODEX_VERSION` is `1.0.1`; `pyproject.toml` reads it dynamically; `tcx --version` uses the same source. |
| Schema baseline | Verified in the working tree | Project apps contain only `0001_v1_initial`; migration-graph and model-state checks live in `tests/test_v1_migrations.py`. |
| Workspace baseline | Exact candidate native acceptance passes locally | The v1.0.1 candidate projects the shared artifact skill to all producing roles, requires the current reference Codex client, and exposes the nested MCP contracts. A disposable generated workspace passed strict config/hook preflight plus artifact, forecast, and proof-protected Brain MCP smokes. |
| Interfaces and safety | Full local candidate suite passes | MCP schema, lifecycle-error, skill-projection, resource-template discovery, Codex-version, forecast receipt-time, Django, migration, compile, and full test gates pass. |
| Frontend | Viewer source, build, and browser acceptance pass | Ten focused tests, typecheck/build, deterministic committed assets, three-section routing, read-only source checks, 1440px/900px/600px layouts, keyboard focus, workspace switching, long-label containment, and invalid-selection failure rendering pass. |
| Release automation | Structurally verified | The release contract suite verifies tag and artifact gating; a manual `publish_pypi=false` rehearsal remains required. |
| Distribution artifacts | Exact local candidate passes | Fresh `1.0.1` sdist/wheel build, `twine check`, and the isolated macOS packaged-wheel smoke pass. Native CI reuse on Ubuntu, macOS, and Windows remains required. |
| Git tag and PyPI | Not performed by this status | Merge/CI, annotated `v1.0.1` tag, protected-environment approval, and manual publication remain release-operator actions. |
| Post-publish verification | Blocked on publication | Exact-version POSIX and native Windows attach/doctor smokes run only after PyPI contains the immutable artifacts. |

Release-branch status describes candidate source shape, not release sign-off.
The merged commit and exact uploaded artifacts remain authoritative.

The local v1.0.1 candidate evidence recorded on 2026-07-15 is:

- `python3.11 -m pytest`: **617 passed** with three dependency-level Pydantic
  deprecation warnings and no failures.
- `python3.11 manage.py check`, migration dry-run, compile pass, skill
  validation, JSON-schema validation, and diff checks: passed.
- `npm ci --prefix frontend`, the ten-test frontend suite, typecheck/build, and
  deterministic committed-asset comparison: passed.
- A fresh `tradingcodex-1.0.1` sdist and wheel built in isolation; both passed
  `twine check`, and the wheel passed the macOS isolated-install platform smoke
  with runtime version `1.0.1`.
- A fresh generated development workspace passed `tcx doctor` and the strict
  current-reference Codex CLI contract, including all eight trusted hooks.
- Native artifact persistence completed with one exact
  `fundamental-analyst` child after it loaded `tcx-artifact`; the accepted
  artifact id was
  `research_report-ACME-synthetic-fixture-fundamental-artifact-persistence-smoke-76d0866c5fa1`.
- Native forecast persistence completed in one exact child without an MCP
  retry: artifact
  `fundamental_report-receipt-race-fixed-synthetic-recurring-revenue-smoke-e3e03d1566da`
  produced forecast `forecast-261e59a11d6d4bb39fe0e8c80c95042d`.
  The omitted caller timestamp resolved to one service receipt time, so
  `issued_at` equaled `recorded_at`.
- A fresh exact `$tcx-brain` management turn received the hook-owned build-turn
  proof and completed the protected list call, confirming the current-reference
  deferred MCP proof path.

The v1.0.0 baseline evidence recorded on 2026-07-15 is listed below. It does
not substitute for v1.0.1 validation, CI on the candidate commit, or the
tag-bound rehearsal of the immutable patch artifacts:

- GitHub Actions [CI run 29403737708](https://github.com/monarchjuno/tradingcodex/actions/runs/29403737708)
  passed on release-branch commit `12649b0`: the safety and financial invariant
  gate, ten-test frontend build verification, clean wheel build and Ubuntu
  smoke, native macOS and Windows smokes of the same uploaded wheel, and the
  full Python 3.11-3.14 matrix all completed successfully. Each Python job
  reported **614 passed**, no Django issues, no migration changes, and a clean
  compile pass.

- `python -m pytest`: **604 passed** after development-bootstrap isolation,
  current-reference V2 dispatch, prepublication artifact-quality enforcement,
  hook-trust preflight, and release-checklist hardening; the only output was
  three existing Pydantic deprecation warnings.
- `python manage.py check`: no issues.
- `python manage.py makemigrations --check --dry-run`: no changes detected.
- `python -m compileall -q tradingcodex_cli tradingcodex_service apps tests`:
  passed.
- `npm ci --prefix frontend`, `npm test --prefix frontend`, and
  `npm run build --prefix frontend`: passed; the frontend suite reported ten
  passing tests, typecheck/build passed, and rebuilt assets matched the
  committed output.
- A fresh `1.0.0` sdist/wheel built from a detached clean release-branch
  worktree, `twine check`, and
  `python tests/platform_wheel_smoke.py --wheel-dir <fresh-dist>` passed on
  macOS.

### Native Codex acceptance evidence

Codex CLI compatibility was rebaselined on 2026-07-15 from the previous patch
reference to the current reference recorded in [installation.md](../installation.md).
The command and flag surfaces used by TradingCodex did not change across those
patch releases, and the upstream patch range did not introduce a project config
schema migration. The audit did expose a pre-existing TradingCodex projection
defect: the generated V2 table omitted `enabled = true` and mixed in the V1-only
`agents.max_threads` key. The corrected contract explicitly enables V2, sets a
seven-thread session cap, omits the V1 key, and fails strict/version preflight
when that shape is not loaded.

The accepted current-reference compatibility run used root task
`019f61bc-ece9-7de3-8c72-6d90fe65ae2a`, child task
`019f61bd-32ef-7f63-86a6-76ca6927ab5b`, and run id
`analysis-2a9ab229746349b0bcae09ae384394ee`. Evidence includes:

- exact configured CLI reference match and `--strict-config` success;
- `config.load`, `mcp.config`, and `sandbox.helpers` all `ok`;
- effective `multi_agent=true`, `multi_agent_v2=true`, hooks and network proxy
  enabled, with computer use and unified exec disabled;
- all eight generated project hooks persistently trusted in an isolated
  maintainer `CODEX_HOME`;
- an allowed `agents.spawn_agent` call using exact
  `agent_type="fundamental-analyst"`, compact task name, and
  `fork_turns="none"`;
- the Terra/high child returning `ROLE_READY`, followed by root
  `V2_HOOKS_OK`; and
- matching `SubagentStart` and `SubagentStop` records for the child, with no
  active child left in session state.

The one-run `--dangerously-bypass-hook-trust` mode was tested only as a
diagnostic and is not accepted as lifecycle evidence. In the current reference
it is not inherited when an exact V2 child reloads its role config; persisted
hook trust is therefore a release-smoke prerequisite.

The previous server-planned DAG/supervisor-loop evidence is retired because it
does not represent the v1 Codex-native architecture. The final working-tree
native run used root task `019f62c2-5e0b-7622-a7e0-372b4bfa61ff`, run id
`analysis-6178baef749c453ca1ef072e8dad369c`, and synthesis artifact
`synthesis_report-NVDA-cb883bbc880f`. A fresh generated workspace provides
evidence for:

- `gpt-5.6-sol`/xhigh Head Manager and exact `gpt-5.6-terra`/high
  `fundamental-analyst` and `news-analyst` children;
- an NVDA company-facts/catalyst request interpreted without hook/server
  semantic classification, with the excluded valuation, portfolio, order,
  approval, trading, and execution scope preserved and no execution role
  present;
- a fresh `begin_analysis_run` request hash and sealed explicit Investment
  Brain id, version, content digest, Strategy, and Investor Context posture;
- exactly two sequential exact-role spawns (`fundamental-analyst`, then
  `news-analyst`) with compact task names, `fork_turns="none"`, no
  model/reasoning/sandbox override, and real read-only child sandboxes;
- two authenticated role artifacts and one Head Manager synthesis whose
  receipt binds the exact two run-local input ids and hashes;
- timezone-aware knowledge cutoffs bounded by service-returned source
  `known_at` and service-owned artifact `recorded_at`, with no date-only,
  future-cutoff, or MCP retry error;
- strict quality and compact-context passes for all three artifacts, including
  material `[factual]`, `[inference]`, and `[assumption]` tags in the synthesis;
- artifact-driven synthesis without a Django plan, lane, DAG, task id, or
  supervisor tool;
- no inferred Investment Brain, Strategy, or Investor Context when none was
  explicitly selected, with the pristine baseline stated in synthesis; and
- zero order tickets, approval receipts, execution results, and broker orders.

All three final-run Markdown artifacts passed `tcx quality-check --strict` with
no missing fields or warnings. The synthesis receipt
`f67eb4e469108ebfa557d87f7a5e51842bfbbc75531d1e19857b068f8cdd5e7b`
binds `fundamental_report-NVDA-e81b056d4024` and
`news_event_report-NVDA-288b8587de0c` to their complete content hashes. Both
child lifecycle records ended with no active session. A database check scoped
to the disposable workspace found zero order tickets, approvals, broker
orders, fills, order events, or execution results.

An earlier candidate run exposed that a malformed string-valued
`follow_up_requests` list could be receipted before the standalone strict check
ran. The service now evaluates the exact intended Markdown bytes before
publishing any accepted run-bound artifact, the MCP schema exposes structured
follow-up and improvement objects, and synthesis refuses authenticated inputs
whose handoff is not `accepted`. The final run above exercised the corrected
path; its fundamental artifact now passes the same strict check that exposed
the defect.

The multi-agent-disabled run used root task
`019f62cb-0552-7490-96c5-ebe28e765d02` and run id
`analysis-5ceee8b9af3c4683802f6ceb5c384f40`. It returned
`waiting_for_subagent_dispatch` with one compact `fundamental-analyst` brief,
and created no child event, role artifact, or synthesis.

This is release-branch evidence. The candidate is ready to merge, but the
release remains not ready to tag until the merged commit passes GitHub CI. The
tagged immutable artifacts must then pass the manual `publish_pypi=false`
rehearsal before publication.

### Workspace viewer browser acceptance

The generated development service passed real-browser acceptance at 1440px
desktop, 900px half-width desktop, and 600px phone widths. Library, Skills, and
System remained free of horizontal overflow; half-width Library/Skills used
full-width list-to-detail transitions; all 27 visible research status labels
stayed inside their rows; and long system status labels wrapped. Library/Skills
detail views moved focus into the selected detail and back to their indexes,
and a workspace switch returned focus to `main` after refreshed content loaded.
Desktop and narrow workspace switching both updated the selected registered
workspace. An invalid workspace query retained the SPA shell and rendered the
API error without mutation controls. The default Django Admin login remained
unchanged, and the browser console reported no errors or warnings.

## Final-Commit Validation

Run the source, frontend, and schema gates from
[deployment.md](./deployment.md):

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

Harness, role, skill, hook, policy, MCP, and generated-template changes also
require the disposable-workspace and real Codex smokes in
[validation-and-test-plan.md](./validation-and-test-plan.md). Those smokes must
prove exact-role dispatch, compact context, accepted artifact binding,
head-manager synthesis, negated-scope handling, and the fail-closed
`waiting_for_subagent_dispatch` path.

The final candidate artifacts then require:

```bash
python3.11 -m build
python3.11 -m twine check dist/*
python3.11 tests/platform_wheel_smoke.py --wheel-dir dist
```

CI and the manual release rehearsal must run the exact uploaded artifact on
Ubuntu, native macOS, and native Windows. The protected `pypi` environment and
Trusted Publisher configuration must be reviewed before publication.

## Tag And Publication State

This document does not assert that the final commit is on `main`, CI is green,
the tag exists, or PyPI contains `1.0.1`. After every final-commit and artifact
gate passes:

1. Merge the release commit to `main` and wait for CI.
2. Create and push the annotated tag `v1.0.1` at that commit.
3. Rehearse the manual workflow with `publish_pypi=false` and
   `release_version=1.0.1`.
4. With protected-environment approval, run the same tag with
   `publish_pypi=true`.
5. Verify the immutable PyPI files, release notes, and exact-version POSIX and
   native Windows attach/doctor flows.

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

# Workflows And Agents

Use this page before changing Head Manager, fixed roles, skills, hooks, handoffs,
artifact lineage, or native dispatch progress. Durable product rules live in
[`docs/roles-skills-and-workflows.md`](../docs/roles-skills-and-workflows.md) and
[`docs/codex-native-orchestration.md`](../docs/codex-native-orchestration.md).

## Runtime Model

Investment orchestration is Codex-native:

```text
user
  -> Head Manager interprets the original language
  -> begin_analysis_run (hash + sealed Brain/Strategy/Context provenance only)
  -> optional one exact explicit Investment Brain inquiry overlay
  -> exact V2 role children, parallel where independent
  -> authenticated role artifacts
  -> Head Manager revises/adds/challenges/stops dynamically
  -> run-local synthesis artifact
```

Django does not classify investment meaning, select a lane or team, compile a
DAG, issue dispatch tasks, or run an artifact supervisor state machine. For
analysis, the hook supplies service health, transport/run binding, exact-role
checks, audit, and tool policy. Separately, it reserves the two immediate
root-native action skills and parses their complete fixed grammar.
It also recognizes only an exact first-meaningful-line `$tcx-order-allow --mode
paper|validation|live` to issue a bounded `OrderTurnGrant`; the plain skill
token or matching projected link is accepted, but arguments remain literal. None of these
checks is natural-language routing or prose-scope enforcement.
Separately, matching first-meaningful-line `$tcx-build` issues a DB-canonical
workspace/session/turn/cwd/original-prompt-bound Build grant and activates deterministic
write/protected-MCP hook gates for that root native turn only. It never elevates
the actual Codex sandbox, and subagents cannot inherit it. The browser viewer
has no Build path.
Matching first-meaningful-line `$tcx-brain` and `$tcx-strategy` use the same
DB-canonical grant lifecycle with separate `brain` or `strategy` scope in
`trading-research`. The hook admits only the matching canonical source or
lifecycle path, and injects proof only into `manage_investment_brain` or
`manage_strategy` for managed registry/projection writes. Markers cannot be
combined, cross-scope use fails, and Plan
mode cannot issue or use any managed workspace grant.

An Investment Brain is a TradingCodex-managed, Head Manager-level,
platform-neutral inquiry and interpretation overlay. Native analysis selects at
most one exact `$investment-brain-*` id through a plain token or matching
projected skill link. Repeated same-id references deduplicate and distinct
multiple ids fail; `$strategy-*` selection follows the same rule. Head Manager translates
its hypotheses and questions into dynamic role-owned work; the Brain never owns
roles, tools, workflow, memory, policy, approval, or execution. No Brain is the
pristine baseline, while multiple or unresolved Brains fail closed.

The run also seals the complete projected Brain skill digest. Optional Markdown
references are lazy: the native hook allows only validated `cat` reads below
the selected projection's `references/` directory when the current Codex
session maps to that exact run and the whole projection still matches the
sealed digest. Several permitted reads may share one bundle with literal
headings, but redirects, pipelines, substitutions, executable compounds, and
unbound, stale, changed, unselected, registry/package/source, index, or
role-config reads fail closed.

`$tcx-brain` is the root management entrypoint for source
create/inspect/revise/validate/delete and installed plugin
list/inspect/install/update/activate/deactivate/rollback/remove. It starts
directly as a matching invocation on the first meaningful line of a new
`trading-research` root turn; the plain token or matching projected link is
accepted and the request may share the line or follow it;
it must not be wrapped in `$tcx-build`. Reversible source creation or revision
may proceed without redundant confirmation when the request is complete.
Installation starts inactive, while activation and deletion remain explicit.
The skill never edits managed/third-party packages directly or
implies Git/publication actions.

`$tcx-strategy` follows the same direct root-turn contract with independent
`strategy` scope. It may create a reversible draft from a concrete request,
while activation, archive, replacement, and deletion stay explicit. Native
Codex stages the body as an ordinary root file; `manage_strategy` exclusively
writes the managed skill and projection without exposing the Research runtime.
Brain and Strategy grants cannot authorize one another.

## Fixed Team

Head Manager coordinates nine fixed roles: fundamental, technical, news, macro,
instrument, valuation, portfolio, risk, and judgment review. There is no
execution subagent. Head Manager may use narrow live search to construct or
revise the workflow, but those results are untrusted planning leads rather than
accepted evidence. The first six evidence roles have live search for evidence
production; portfolio, risk, and judgment review explicitly disable it.

Every spawn must use exact `agent_type`, compact underscore-only `task_name`,
compact message, and `fork_turns="none"`. Each revision or follow-up is a fresh
child. Generic-role fallback, `followup_task`, full-history fork, role-TOML
emulation, and source-code routing are invalid.

Each child may read only the exact role-owned and shared `SKILL.md` files and
Markdown references enabled by its projected role config. The hook accepts a
strictly read-only batch of those paths for normal Codex efficiency, but does
not expose the config, generated indexes or state, another role's skills, or a
general compound-shell escape. Projected role instructions tell the child to
use one `cat path ...` call for all needed documents, avoiding rejected loops,
redirects, pipelines, substitutions, and executable compounds.

The generated root config must explicitly enable `features.multi_agent_v2`,
set `max_concurrent_threads_per_session = 7`, keep visible spawn metadata and
the `agents` namespace, and omit the incompatible V1 `agents.max_threads` key.
`agents.max_depth = 1` still prevents recursive role dispatch.

## Skill Namespace

The 33 bundled skills all use `tcx-` plus one suffix word when possible and at
most two words. Folder, frontmatter, registry, projection, UI metadata, and `$`
invocation ids are identical; legacy core aliases are not projected. `tcx-` is
reserved for bundled skills. User-owned `strategy-*`, `investment-brain-*`,
and optional role skills keep separate namespaces.

`tcx-artifact` is the shared persistence contract for all nine producing fixed
roles. Edit its source under `workspace_templates/modules/repo-skills/` together
with MCP schemas and artifact/forecast validators when nested tool contracts
change.
`tcx-calculation` is the shared reproducible-computation procedure for
fundamental, technical, macro, valuation, portfolio, and risk. It requires
Dataset/Calculation search before compute, progressive card → manifest/profile
→ slice retrieval, explicit Decimal-versus-NumPy semantics, point-in-time
input lineage, prepared sidecars, typed finite outputs, diagnostics, and exact
reuse. Role-specific methods stay in each role skill. Head Manager sees only
Dataset card/manifest and Calculation-card discovery; it cannot profile rows,
materialize, register, prepare, or record. Build and the viewer have no
mutation or execution path.
Pristine Strategy authoring uses the bundled required-section contract and
does not auto-load a host-global authoring skill; external skills remain
explicit user-selected extensions.
That rule does not block read-only app, connector, MCP, or data tools used as
evidence. Preserve a user-named provider in the role brief, inspect
current-task callable tools through the runtime's available deferred-tool
discovery surface when needed, and
attempt the smallest relevant call before public-web fallback. Treat the
sanitized capability inventory as discovered configuration only.

Before selecting or revising a role wave, Head Manager may search the live web
only to resolve the subject or event, likely source landscape, material
unknowns, and smallest useful team. It passes derived role-owned questions and
source leads, not raw web claims. Every fact that could affect the investment
conclusion must be reacquired and authenticated by the producing role before
synthesis.

`tcx-dashboard` is the read-only viewer entrypoint projected only to Head
Manager. It opens the viewer in the Codex in-app browser by default and uses an
external browser only on an explicit user request, then summarizes canonical
workspace state without starting an analysis run or mutating TradingCodex
state. `tcx-server` remains the separate diagnostic and recovery entrypoint.

## Durable Boundary

`begin_analysis_run` writes only request hash/size, run id, timestamps, and
sealed optional Investment Brain, Strategy, and Investor Context provenance under
`.tradingcodex/mainagent/runs/<run-id>/run.json`. It stores no raw request,
semantic lane, selected team, plan, task queue, or terminal action.

Producing roles write their own reports through `create_research_artifact`.
The service binds authenticated principal/producer identity, verifies the run,
validates exact run-local `input_artifact_ids`, and derives content/input hashes.
For Brain-bound runs it also derives `investment_brain_id`,
`investment_brain_version`, and `investment_brain_content_digest`. Head Manager
may synthesize only with at least one verified input artifact.

Context authority is typed: Core owns safety and provenance, the mandate owns
scope, Investor Context limits suitability, Strategy owns explicit decision
rules, Brain guides inquiry, methods own bounded procedure, current evidence
controls facts, and Decision Memory remains non-authoritative evidence. New
judgment is blind-first with respect to similar memory, and synthesis states
the Brain influence, conflicts, and post-memory delta.

Execution remains service-owned. Root `tcx-order-submit` and
`tcx-order-cancel` bundles document an explicit-only protocol but contain
no tools. For an exact complete root native user prompt, `UserPromptSubmit`
creates a workspace-bound `native-user` mandate and calls
`application/execution_gateway.py` in-process before analysis begins.

The explicit-only `tcx-order-allow` bundle is the in-workflow alternative. A
valid first meaningful line binds one grant to workspace, session, turn,
original complete prompt hash,
Codex permission mode, and execution mode, then normal role orchestration
continues. Plan mode rejects immediate order effects plus grant issuance and
use. The grant expires after one hour and is revoked after one submit or cancel,
on `Stop`, or on the next user turn. Only root Head Manager can call
`use_order_turn_grant`; `PreToolUse`
reserves the grant for the tool-use id and injects the internal proof. The model,
fixed roles, and direct MCP callers cannot supply it; the browser viewer has no
grant entrypoint.

`tcx-automate` authors Codex app Scheduled Tasks for simple research,
monitoring, analysis, portfolio/status review, draft, assisted, optional
turn-authorized execution, explicitly delegated Build work, and capability-
scoped Brain or Strategy management.
The saved prompt is submitted each scheduled turn and invokes the actual work
skill, never `tcx-automate` recursively. TradingCodex does not detect an
Automation origin; scheduled and interactive root turns use the same hook path.
Only an execution-capable task includes the canonical plain
`$tcx-order-allow` first-meaningful-line invocation.
Recurring Build uses `$tcx-build`; recurring Brain or Strategy management uses
its matching marker in `trading-research`. Every run earns a fresh scoped grant,
and markers are never combined.

Public REST and generic CLI cannot perform submit/cancel, and a direct MCP call
to the protected tool has no authority. The service deterministically enforces
canonical policy, ticket, receipt, action, broker posture, and mode, not
free-form natural-language scope.
Policy, order tickets, approval receipts, idempotency, account/broker state,
submission, cancellation, reconciliation, and audit remain in the service
kernel.

## Key Sources

- `tradingcodex_service/application/analysis_runs.py`
- `tradingcodex_service/application/research.py`
- `tradingcodex_service/mcp_runtime.py`
- `tradingcodex_service/application/viewer.py`
- `tradingcodex_service/application/execution_gateway.py`
- `tradingcodex_service/application/skill_invocations.py`
- `tradingcodex_service/application/build_gateway.py`
- `workspace_templates/modules/codex-base/files/.codex/hooks/tradingcodex_hook.py`
- `workspace_templates/modules/codex-base/files/.codex/prompts/base_instructions/head-manager.md`
- `workspace_templates/modules/repo-skills/files/.agents/skills/tcx-workflow/SKILL.md`
- `workspace_templates/modules/repo-skills/files/.agents/skills/tcx-workflow/references/context-and-override.md`
- `workspace_templates/modules/fixed-subagents/files/.codex/agents/*.toml`
- `docs/investment-brain-plugins.md`

## Validation

Regenerate a clean workspace. Verify nine fixed roles and all 33 skills,
including the three native execution bundles, with no retired execution
role/skill. MCP `tools/list` must omit raw submit/cancel/refresh mutations,
expose `use_order_turn_grant` only to Head Manager, and omit obsolete
workflow-control tools;
`begin_analysis_run` is Head Manager-only. Hooks must accept only exact root
native actions or first-meaningful-line order grants, bind/revoke/inject proof correctly,
reject malformed/subagent/direct-MCP forms, and otherwise avoid
language classification or plan/state reads. Also verify exact V2 role dispatch,
artifact lineage, native role progress, Brain selection/failure,
typed conflicts, blind-first memory, and unchanged service execution gates.

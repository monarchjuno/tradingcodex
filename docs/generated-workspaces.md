# Generated Workspaces

This document owns `tcx attach`, `tcx init`, `tcx update`, generated workspace
structure, template behavior, project-scoped MCP config, hook behavior,
workspace provenance, and smoke checks.

## Workspace Contract

`tcx attach`, `tcx init`, and `tcx update` render
`workspace_templates/modules/*/files` into a Codex workspace. After rendering,
they set the Django settings module, apply the central runtime schema, and
record workspace provenance in the central local Django DB.

The template source tree may be refactored for maintainability, but generated
output paths are the `0.2.0` release contract. Module ids, module dependency
resolution, and rendered paths such as `.codex/config.toml`, `.agents/skills/*`,
`.tradingcodex/*`, `trading/*`, and `./tcx` must remain stable unless docs and
tests intentionally change the generated workspace contract.

Template bodies should remain ordinary source files under
`workspace_templates/modules/*/files` whenever the generated artifact is meant
to be read or edited by humans or Codex, such as Markdown, TOML, YAML, JSON,
Python hook scripts, schemas, and wrappers. Python code may own module
registry loading, dependency resolution, rendering, validation, and generated
index writing, but it should not hide durable prompt, skill, policy, hook, or
workspace-contract content inside Python string constants merely for
organization. If a template is generated from structured Python data, the
generated file path and reviewable source-of-truth data must be documented and
covered by contract tests.

The generated workspace is ready for:

- `./tcx doctor`
- `./tcx workspace status`
- `./tcx profile status`
- MCP ledger inspection
- research-memory commands
- local web/Admin service access
- Codex-native role prompts and skills

The generated workspace does not create a workspace-local canonical investment
DB by default.

## Valid Targets

The target may be:

- an empty directory
- a git-initialized directory containing only `.git` plus optional git metadata files

Source checkouts of this repository are development projects, not generated
TradingCodex workspaces.

Codex agents must not run `git clone` when a user asks to install, set up,
attach, or use `monarchjuno/tradingcodex` in a workspace. Run the packaged CLI
from the target workspace instead:

```bash
uvx --refresh --from tradingcodex tcx attach . && ./tcx doctor
```

Agents must also not silently create a default target when a user only asks to
install `monarchjuno/tradingcodex`. The agent rule is: do not invent a
workspace path such as `tradingcodex-workspace`. If no target path is supplied
and the user did not ask to use the current workspace, ask for the target
directory before running setup. If the user is already in an empty target
workspace, install into `.`.

## Generated Files

Generated workspace contract:

- `AGENTS.md`
- `.codex/config.toml`
- `.codex/prompts/base_instructions/head-manager.md`
- `.codex/agents/*.toml`
- `.codex/hooks/tradingcodex_hook.py`
- `.agents/skills/*`
- `.tradingcodex/*`
- `trading/*`
- `./tcx` wrapper

A clean generated workspace must not contain:

- `package.json`
- Node MCP runtime files
- workspace-local canonical investment DB
- broker credentials or raw secrets

## Baseline Generated Contents

Generated workspaces contain:

- one root `head-manager`
- nine fixed subagents
- an immutable workspace manifest at `.tradingcodex/workspace.json`
- root `head-manager` identity loaded from `.codex/prompts/base_instructions/head-manager.md` through `.codex/config.toml` `model_instructions_file`
- sectioned Markdown base-instruction format for `head-manager`, including `# How you work`, TradingCodex guardrails, and tool guidelines
- Codex-style operating style in the root `head-manager` prompt: scoped `AGENTS.md` handling, concise preambles, selective planning, `rg`-first search, `apply_patch` edits, focused validation, dirty-worktree respect, and concise final handoffs
- instruction/skill separation: root `head-manager` instructions own identity, durable safety boundaries, fail-closed dispatch, role boundaries, skill routing, optional-skill management, and approved action boundaries; fixed subagent TOML files own standing role identity, MCP/tool config, artifact walls, and always-on prohibitions; repo skills are dependency-light capability procedures for workflow maps, compact assignment-envelope templates, optional skill file management, quality gates, synthesis, and postmortems, without declaring role ownership or direct inter-skill call chains
- no-overlap handoff contract: each role owns its specialist question, downstream roles consume accepted artifacts, and missing/stale/weak upstream work returns `revise`, `blocked`, or `waiting` instead of being silently redone by another role
- closed selected team: hook/starter-prompt selected roles are binding for the current lane; research-only prompts do not add portfolio, risk, approval, or execution roles for precautionary coverage
- negated scope routing: phrases such as "no valuation", "no order", and "no trading" remove those actions or roles from dispatch selection
- no full-history fixed-role spawn: fixed `agent_type` subagents receive compact assignment envelopes without full-history forking on the first attempt
- subagent hook isolation: `UserPromptSubmit` auto-routing is ignored for fixed subagent contexts so subagent briefs cannot overwrite main-agent routing state or create recursive dispatch pressure
- main-to-subagent briefs are assignment envelopes, not role manuals: they carry the current task, original request, explicit constraints, workflow consent posture, research artifact language, lane, artifact target, compact context summary, request-specific out-of-scope items, and return contract without repeating long method/source/guardrail checklists or pasting full artifacts
- context-efficient research handoffs: stored markdown frontmatter includes
  `context_summary` so downstream roles can consume artifact paths and summaries
  before opening full markdown; `reader_summary` and `next_action` keep the
  first-read experience clear for non-expert users
- context-budget audit: `./tcx subagents context-audit --strict` inspects the
  latest prompt gate, prompt-gate history, compact hook context, subagent
  session state, and research artifacts after long multi-subagent runs; it
  fails strict mode when handoff artifacts lack `context_summary`, compact gate
  history grows beyond budget, or gate/state/history payloads look like pasted
  markdown artifacts, and warns when reader-first fields are missing
- compact subagent session state: `.tradingcodex/mainagent/subagent-session-state.json`
  keeps total counters plus recent active/completed/event records for Codex
  context; the full event stream remains in
  `trading/audit/subagent-session-events.jsonl`
- fixed subagents configured for `model = "gpt-5.5"` and `model_reasoning_effort = "high"`
- fixed subagent `nickname_candidates` set to a single item matching the exact role `name`
- fixed subagent identities kept in `.codex/agents/*.toml` `developer_instructions`, as required by Codex custom agent files
- project-local additional agent instructions under `.tradingcodex/agent-instructions/<role>.md`; projection appends them after generated default instructions for `head-manager` and fixed subagents
- twenty-four core repo skills across project-scope mainagent skills and subagent skill directories, each with `SKILL.md` frontmatter for document metadata and `agents/openai.yaml` UI metadata
- standalone `strategy-*` skills under `.agents/skills/strategy-*` for user-approved agent-readable investment strategies, created through `strategy-creator`, CLI, API, or service-layer flows and exposed to the root `head-manager` through the strategy marker block in `.codex/config.toml`; Django web lists and previews them read-only
- file-native agent/skill projection: role skill state is expressed in `.codex/agents/*.toml`, `.agents/skills/*`, `.tradingcodex/subagents/skills/*`, `.codex/config.toml`, `.tradingcodex/mainagent/skill-change-proposals/*.yaml`, and `.tradingcodex/generated/*.json`, not Django skill DB tables
- optional subagent skills are created, updated, activated, archived, deleted, and validated through the shared application service used by `head-manager`, CLI, API, and Django web
- information-barrier policies
- order/approval schemas
- restricted-list policy
- paper and validation-only non-live adapters
- audit directories
- central local SQLite service access through `~/.tradingcodex/state/tradingcodex.sqlite3`
- workspace identity through `.tradingcodex/workspace.json`
- workspace provenance through `TRADINGCODEX_WORKSPACE_ROOT`
- an active paper profile reference used as the default portfolio/account/strategy scope
- Python hook scripts callable from Codex hook commands
- generated indexes under `.tradingcodex/generated/`, including
  `module-lock.json`, `capability-index.json`, `component-index.json`,
  `agent-index.json`, `skill-index.json`, and `projection-manifest.json`

Workspace template modules are deployment projections. Harness component
ownership comes from the Python component registry and is exported into
`component-index.json` for Codex-readable inspection.
Agent and skill ownership comes from the Python agent registry and is projected
into Codex-readable agent TOML plus generated agent/skill indexes.

## Attach-First UX

TradingCodex is installed globally once, then attached to the workspace where
the operator wants to ask Codex agents to work.

Recommended agent-facing flow:

```bash
uv tool install tradingcodex
uv tool update-shell
cd <user-selected-workspace>
tcx attach .
codex .
```

`tcx attach .` is the default user-facing CTA for adding the TradingCodex
harness to the current workspace. `tcx init <path>` remains the empty-directory
creation command. Attach preserves an existing TradingCodex `workspace_id` and
active profile when refreshing an existing generated workspace.

## Update UX

`tcx update .` is the explicit release-update command for an existing generated
workspace. It requires `.tradingcodex/workspace.json`,
`.tradingcodex/generated/module-lock.json`, and `./tcx` to exist before it will
overwrite generated paths.

Update behavior:

- preserve immutable `workspace_id`
- preserve active profile selection
- re-render generated template paths from the currently running package
- refresh generated indexes under `.tradingcodex/generated/`
- apply central DB migrations through the shared runtime path
- persist the workspace context in the central DB
- run `./tcx doctor` unless `--no-doctor` is passed

Package-release updates should prefer the installer path so the latest package
is fetched before rendering:

```bash
curl -fsSL https://raw.githubusercontent.com/monarchjuno/tradingcodex/main/install.sh | sh -s -- --update .
```

The generated `./tcx` wrapper special-cases `update` to prefer
`uvx --refresh --from <recorded-package-spec>` when `uvx` is available. This
prevents an old recorded Python path from refreshing the workspace with stale
template code.

Inside a Codex-generated workspace, `head-manager` runs under a workspace
permission profile. It can write workspace files and TradingCodex home state,
but it should not update the generated harness itself: workspace update rewrites
protected `.codex` prompt/config/hook surfaces and generated files that define
the current agent. For already-installed packages, the wrapper supports a
user-terminal workspace-only path:

```bash
./tcx update --skip-refresh
```

`--skip-refresh` uses the recorded Python/package and avoids the `uvx` refresh
step. If startup health reports `update_status.workspace_update_allowed=true`,
`head-manager` should tell the user to run
`update_status.workspace_update_command` from their terminal. If startup health
reports `update_status.package_update_required_first=true`, package refresh is
also a user-terminal action, normally:

```bash
uvx --refresh --from tradingcodex tcx update .
```

## Project-Scoped MCP Config

Generated Codex workspaces render a project-scoped
`[mcp_servers.tradingcodex]` entry in `.codex/config.toml`.

The config follows the OpenAI Codex MCP shape:

- stdio `command`
- `args`
- `enabled`
- `env`
- `enabled_tools`
- `default_tools_approval_mode`
- `startup_timeout_sec`
- `tool_timeout_sec`

Project-scoped Codex config applies only when the generated workspace is
trusted by Codex.

The generated TradingCodex MCP command uses:

```text
uvx --refresh --from <package-spec> python -m tradingcodex_cli mcp stdio
```

The package spec is recorded during bootstrap so PyPI and GitHub-source
installs keep the same MCP source without stale source-cache reuse.

Codex project config should register only the `tradingcodex` MCP server.
Generated permission profiles allow network access for public evidence
gathering, such as filings, disclosures, news, web sources, and market-data
references. They still deny workspace secret paths and do not authorize direct
broker APIs, broker-specific Codex MCP servers, approval bypass, or execution.
Broker APIs are attached through native TradingCodex connector profiles using
canonical MCP tools such as `list_broker_connector_templates`,
`register_broker_connector`, `get_broker_capability_profile`,
`get_broker_instrument_constraints`, and `preview_order_translation`.

Generated Codex config declares the TradingCodex home directory, normally
`~/.tradingcodex`, in `sandbox_workspace_write.writable_roots`. This bounded
writable root is required for the central local DB, migration lock, service
status, and update preference files when the active Codex surface honors
project-scoped sandbox roots. It is narrower than disabling the sandbox, and
generated permission rules continue to deny `.env`, secret, and
broker-credential-shaped paths under both the workspace and TradingCodex home.
If a Codex CLI or app run still reports `~/.tradingcodex` outside writable
roots, the user should add it through user-level Codex config or CLI `--add-dir`
before running service recovery or update-adjacent commands.

Broker/data MCP servers, when explicitly needed for reviewed read-only
discovery, are registered inside TradingCodex External MCP Gate with
`./tcx mcp external ...`, not directly in `.codex/config.toml` or
`.codex/agents/*.toml`.

## MCP Autostart

The generated TradingCodex MCP config sets:

```text
TRADINGCODEX_MCP_AUTOSTART_SERVICE=1
```

This lets Codex MCP startup idempotently start the local Django dashboard
service at `127.0.0.1:48267` while keeping MCP stdio stdout clean.

If the port is already open, MCP startup verifies that the existing process is
a TradingCodex service with the same package version and central DB path before
using it.

The autostart path must be:

- idempotent
- silent on MCP stdout except for MCP protocol messages
- not required for direct `./tcx mcp stdio` smoke checks

Generated workspaces also support startup context for Codex sessions.
Bootstrap writes an initial compact diagnostic cache at
`.tradingcodex/mainagent/server-status.json`, and the `SessionStart` hook
refreshes it; neither path starts services, updates workspaces, opens browsers,
or performs package refresh on its own. The emitted context uses marker
`tradingcodex-session-context` and keeps only compact fields for
`mode_status`, `permission_status`, `update_status`, `server_status`,
`allowed_next_actions`, and `routing_status`.

`head-manager` uses `$tcx-server` for service/MCP doctor checks,
`./tcx service status`, and `./tcx service ensure`. It tells the user that the
local dashboard is available at `http://127.0.0.1:48267/` and opens it only
when explicitly asked. If project MCP config was created or changed, the user
must fully quit and restart Codex and start a new thread because Codex may not
hot reload project MCP config.

Startup health may compare the generated workspace version in
`.tradingcodex/generated/module-lock.json` with the installed/running `tcx`
package version and the latest known TradingCodex release. If update is needed
while Codex is running under restricted TradingCodex permissions, `head-manager`
must explain the two supported paths: switch Codex to full access and enable
TradingCodex build mode, or run the recommended `update_status.command` from a
terminal. Self-update is allowed only when Codex full access and explicit
workspace build mode are both active and the user asks for the update. After
self-update, `head-manager` stops and tells the user to restart Codex.

Build mode is per workspace and explicit:

- `./tcx mode status`
- `./tcx mode set build --reason "<reason>"`
- `./tcx mode set operate`

Build mode may update TradingCodex, templates, and broker/API adapter
scaffolds. It never enables live execution. Update recommendations are scoped
to the new-conversation health pass, not every user turn. If the user declines
update prompts, `head-manager` records the TradingCodex home preference file,
normally `~/.tradingcodex/preferences/update.json`, with
`suppress_update_recommendation=true`; future new conversations should not
recommend automatic workspace updates unless the user removes or changes that
flag, or explicitly asks for an update.

Connector scaffold commands accept short aliases such as `binance`, `kis`,
`한투`, `upbit`, and `alpaca`; the generated connector profile still records
the canonical template ID.

## Hooks

Generated hooks are Python scripts. Hook behavior is guidance, not final
enforcement.

`UserPromptSubmit` handles:

- prompt classification
- secret warnings
- natural-language investment workflow auto-routing context
- direct-answer prevention context
- duplicate marker management
- prompt-gate audit metadata with prompt hash and workflow lane, without raw
  prompt text in the audit ledger
- compact hook `additionalContext`; the full generated starter prompt remains
  in `.tradingcodex/mainagent/latest-user-prompt-gate.json` and is loaded only
  when the compact gate is insufficient
- execution negation routing such as "no order" and "no trading"
- strategy authoring prompts remain in `strategy-creator`/strategy CRUD scope
  instead of auto-dispatching fixed investment subagents
- connector implementation prompts such as "binance 붙여줘" or "connect KIS"
  route to the `connector_build` lane and `$tcx-build`, not investment
  dispatch
- secret-only routing: credential, token, password, broker-key, or `.env`
  storage/read/rotation prompts create warning context without activating
  investment subagent dispatch unless a separate investment or execution
  request remains
- startup diagnostics: `SessionStart` records compact mode, permission,
  update, service, and routing status for `head-manager`
- update recommendation diagnostics: `SessionStart` records package/workspace
  drift and respects the TradingCodex home update preference file

Hooks load only in trusted projects and may be disabled when
`features.hooks=false`.

## Workspace Provenance

Generated workspace wrappers set `TRADINGCODEX_WORKSPACE_ROOT` for provenance.
The value helps TradingCodex answer which Codex project called the service.
It must not be used as the primary partition for canonical investment state.

`.tradingcodex/workspace.json` stores immutable workspace identity:

- `workspace_id`
- project name
- active profile reference
- MCP scope
- execution mode

`path_hash` remains path provenance. It is not the durable workspace identity.

## Profile Scope

The workspace is a Codex workbench, not an investment ledger. Paper portfolio
state is scoped by active profile:

- `profile_id`
- `portfolio_id`
- `account_id`
- `strategy_id`

The default profile is the shared central paper profile:

```text
default-paper / local-paper / default-strategy
```

Operators can create and select isolated paper profiles with:

```text
./tcx profile create <profile-id>
./tcx profile select <profile-id>
./tcx profile update --objective "medium-term thesis" --horizon "3 to 5 years" --risk-tolerance "moderate drawdown tolerance"
```

Order and portfolio commands use the selected profile when an order does not
provide explicit portfolio/account/strategy ids. Starter-prompt intake and the
Codex `UserPromptSubmit` workflow gate also read the active profile's investor
context, so answered suitability/profile fields are reused and only missing
fields are shown as questions.

## Optional Global Home MCP

Project-scoped MCP remains the approved action boundary. An optional global Codex MCP
server can be installed with:

```text
./tcx mcp install-global --safe
```

The global server name is `tradingcodex-home`. It is read-only/safe-scope only
and must not expose approval, execution, cancellation, policy mutation, secret,
or broker tools.

## Bootstrap Verification

Codex-native bootstrap verification:

- `./tcx doctor` checks generated project MCP server shape and role allowlists.
- `./tcx update --no-doctor` verifies the generated update path without running
  the full doctor twice in installer smoke tests.
- `./tcx mcp stdio` `tools/list` verifies the TradingCodex MCP bridge and tool annotations.
- `./tcx mcp external list` verifies the External MCP Gate CLI path.
- Generated Codex MCP config starts the stdio MCP bridge through `uvx` and starts the local dashboard service when autostart is enabled.
- Direct `./tcx mcp stdio` remains service-free unless `TRADINGCODEX_MCP_AUTOSTART_SERVICE=1` is set.
- `codex exec -C <workspace> --skip-git-repo-check ...` can verify that Codex CLI loads generated project context.

The management command `codex mcp list/get` may show only user/global MCP
servers, even when a session uses project-scoped MCP config after workspace
trust.

## Template Change Rule

Hand-editing a generated workspace under `~/tmp/*` is only a smoke/debug step.
Durable behavior changes belong in `workspace_templates/modules/*`, docs, and
tests. After changing bootstrap behavior, regenerate a clean workspace for
verification.

Template contract tests must cover:

- every `module.json` id matches its directory name
- every declared module dependency exists
- the default module graph resolves
- generated workspaces keep the public output paths and avoid Node runtime
  files, Python bytecode caches, broker secrets, and workspace-local canonical
  investment DBs

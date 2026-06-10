# Generated Workspaces

This document owns `tcx attach`, `tcx init`, generated workspace structure, template
behavior, project-scoped MCP config, hook behavior, workspace provenance, and
smoke checks.

## Workspace Contract

`tcx attach` and `tcx init` render `workspace_templates/modules/*/files` into a Codex
workspace. After rendering, it sets the Django settings module, applies the
central runtime schema, and records workspace provenance in the central local
Django DB.

The template source tree may be refactored for maintainability, but generated
output paths are a compatibility contract. Module ids, module dependency
resolution, and rendered paths such as `.codex/config.toml`, `.agents/skills/*`,
`.tradingcodex/*`, `trading/*`, and `./tcx` must remain stable unless docs and
tests intentionally change the generated workspace contract.

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

Codex agents must not silently create a default target when a user only asks to
install `monarchjuno/tradingcodex`. The agent rule is: do not invent a
workspace path such as `tradingcodex-workspace`. If no target path is supplied,
ask for the target directory before running the installer. If the user is
already in an empty target workspace, install into `.`.

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
- instruction/skill separation: root `head-manager` instructions own identity, durable safety boundaries, fail-closed dispatch, role boundaries, and MCP execution boundaries; fixed subagent TOML files own standing role identity, MCP/tool config, artifact walls, and always-on prohibitions; repo skills own repeatable workflow procedures, scenario maps, compact assignment-envelope templates, quality gates, synthesis, and postmortems
- main-to-subagent briefs are assignment envelopes, not role manuals: they carry the current task, original request, explicit constraints, workflow consent posture, lane, artifact target, material context, request-specific out-of-scope items, and return contract without repeating long method/source/guardrail checklists
- fixed subagents configured for `model = "gpt-5.5"` and `model_reasoning_effort = "high"`
- fixed subagent identities kept in `.codex/agents/*.toml` `developer_instructions`, as required by Codex custom agent files
- twenty-one repo skills
- information-barrier policies
- order/approval schemas
- restricted-list policy
- stub and paper adapters
- audit directories
- central local SQLite service access through `~/.tradingcodex/state/tradingcodex.sqlite3`
- workspace identity through `.tradingcodex/workspace.json`
- workspace provenance through `TRADINGCODEX_WORKSPACE_ROOT`
- an active paper profile reference used as the default portfolio/account/strategy scope
- Python hook scripts callable from Codex hook commands

## Attach-First UX

TradingCodex is installed globally once, then attached to the workspace where
the operator wants to ask Codex agents to work.

Recommended agent-facing flow:

```text
pipx install tradingcodex
cd <user-selected-workspace>
tcx attach .
codex .
```

`tcx attach .` is the default user-facing CTA for adding the TradingCodex
harness to the current workspace. `tcx init <path>` remains the empty-directory
creation command. Attach preserves an existing TradingCodex `workspace_id` and
active profile when refreshing an existing generated workspace.

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

## MCP Autostart

The generated TradingCodex MCP config sets:

```text
TRADINGCODEX_MCP_AUTOSTART_SERVICE=1
```

This lets Codex MCP startup idempotently start the local Django dashboard
service at `127.0.0.1:8000` while keeping MCP stdio stdout clean.

If the port is already open, MCP startup verifies that the existing process is
a TradingCodex service with the same package version and central DB path before
using it.

The autostart path must be:

- idempotent
- silent on MCP stdout except for MCP protocol messages
- not required for direct `./tcx mcp stdio` smoke checks

## Hooks

Generated hooks are Python scripts. Hook behavior is guidance, not final
enforcement.

`UserPromptSubmit` handles:

- prompt classification
- secret warnings
- direct-answer prevention context
- duplicate marker management
- execution negation routing such as "no order" and "no trading"

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
```

Order and portfolio commands use the selected profile when an order does not
provide explicit portfolio/account/strategy ids.

## Optional Global Home MCP

Project-scoped MCP remains the execution boundary. An optional global Codex MCP
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
- `./tcx mcp stdio` `tools/list` verifies the TradingCodex MCP bridge and tool annotations.
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

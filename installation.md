# TradingCodex Installation

Codex agents setting up `monarchjuno/tradingcodex` for use must not run
`git clone`. In the empty target workspace, run the packaged CLI:
`uvx --refresh --from tradingcodex tcx attach . && ./tcx doctor`.
Clone this repository only when the user explicitly asks to develop
TradingCodex source code.

This guide covers install variants and smoke checks. For the shortest path, use
the Quick Start in `README.md`.

## Agent Setup

Codex agents setting up `monarchjuno/tradingcodex` for a user should run the
installer from the user-selected workspace, not clone the source repository.
They should also follow this rule: do not invent a default workspace path such
as `tradingcodex-workspace`. If the user did not name a target directory, ask
where to create or configure the TradingCodex workspace, unless they explicitly
asked to use the current workspace. Use the user's target workspace, then run
the installer there.

```bash
uvx --refresh --from tradingcodex tcx attach . && ./tcx doctor
```

The target workspace should be empty. A directory with only `.git` already
initialized is fine.

After installation, fully quit and restart Codex, then open the generated
workspace and start from a new thread so project MCP config is reloaded. When
TradingCodex MCP autostarts the local service, the dashboard is available at
`http://127.0.0.1:48267/`.

## Install From GitHub Main

Use this when you need the current GitHub `main` source instead of the PyPI
package:

```bash
uvx --refresh --from "tradingcodex @ git+https://github.com/monarchjuno/tradingcodex.git@main" tcx attach . && ./tcx doctor
```

Source checkouts of this repository are for development. Generated
TradingCodex workspaces are separate Codex projects.

## Installer Script Equivalent

The hosted installer wraps the same `uvx` flow and can bootstrap `uv` when it
is missing:

```bash
curl -fsSL https://raw.githubusercontent.com/monarchjuno/tradingcodex/main/install.sh | sh -s -- .
```

The update equivalent for an existing generated workspace is:

```bash
uvx --refresh --from tradingcodex tcx update .
```

For repeated workspace creation, installing `tcx` as a user-level tool is also
available:

```bash
uv tool install tradingcodex
uv tool update-shell
cd /path/to/target-workspace
tcx attach .
```

## Updating Existing Workspaces

Use update when TradingCodex has already been attached to a workspace and a new
package release should refresh generated files and service schema:

```bash
uvx --refresh --from tradingcodex tcx update .
```

`tcx update .` preserves `.tradingcodex/workspace.json`, including
`workspace_id` and active profile, then re-renders generated template files,
refreshes generated indexes, applies central DB migrations, records workspace
provenance, and runs `./tcx doctor` unless `--no-doctor` is passed.

After update, runtime order flows use central DB `OrderTicket` records directly.

After update, fully quit and restart Codex, then start from a new thread in the
updated workspace so project MCP config and generated prompts are reloaded.

## Codex MCP And Local Service

Generated `.codex/config.toml` starts TradingCodex MCP with `uvx`, using the
same package spec recorded at bootstrap time. MCP startup also autostarts the
local Django dashboard service.

Open the generated workspace in Codex and trust the project. After Codex
connects, these local service surfaces are available:

- `http://127.0.0.1:48267/` for the local harness dashboard
- `http://127.0.0.1:48267/admin/` for the Django operations console

For CLI-only use outside Codex, the dashboard service can still be started
manually:

```bash
./tcx service runserver
```

## Smoke Checks

Inspect the local MCP surface:

```bash
printf '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}\n' | ./tcx mcp stdio
```

Inspect workspace/profile status:

```bash
./tcx workspace status
./tcx profile status
```

Create and search DB-backed research memory:

```bash
./tcx research create --markdown-file note.md --id note-1 --title "Research Note"
./tcx research search "gross margin"
./tcx research export note-1
```

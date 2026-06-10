# TradingCodex Installation

This guide covers install variants and smoke checks. For the shortest path, use
the Quick Start in `README.md`.

## Agent Setup

Codex agents setting up `monarchjuno/tradingcodex` for a user should not clone
this source repository for installation. They should also follow this rule: do
not invent a default workspace path such as `tradingcodex-workspace`. If the
user did not name a target directory, ask where to create or configure the
TradingCodex workspace. Use the user's target workspace, then run the installer
there.

```bash
mkdir -p /path/to/target-workspace
cd /path/to/target-workspace
curl -fsSL https://raw.githubusercontent.com/monarchjuno/tradingcodex/main/install.sh | sh -s -- .
```

The target workspace should be empty. A directory with only `.git` already
initialized is fine.

After installation, fully quit and restart Codex, then open the generated
workspace and start from a new thread so project MCP config is reloaded.

## Install From GitHub Main

Use this when you need the current GitHub `main` source instead of the PyPI
package:

```bash
curl -fsSL https://raw.githubusercontent.com/monarchjuno/tradingcodex/main/install.sh | sh -s -- --from-github .
```

Source checkouts of this repository are for development. Generated
TradingCodex workspaces are separate Codex projects.

## Direct uvx Equivalent

The installer wraps this `uvx` flow:

```bash
UV_NO_CACHE=1 uvx --isolated --refresh --python 3.14 --from tradingcodex python -m tradingcodex_cli attach . && ./tcx doctor
```

For repeated workspace creation, installing `tcx` as a user-level tool is also
available:

```bash
uv python install 3.14
uv tool install --python 3.14 tradingcodex
uv tool update-shell
cd /path/to/target-workspace
tcx attach .
```

## Codex MCP And Local Service

Generated `.codex/config.toml` starts TradingCodex MCP with `uvx`, using the
same package spec recorded at bootstrap time. MCP startup also autostarts the
experimental local Django dashboard service.

Open the generated workspace in Codex and trust the project. After Codex
connects, these experimental local service surfaces are available:

- `http://127.0.0.1:8000/` for the work-in-progress visual harness dashboard
- `http://127.0.0.1:8000/admin/` for the work-in-progress Django operations console

For CLI-only use outside Codex, the experimental dashboard service can still be
started manually:

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

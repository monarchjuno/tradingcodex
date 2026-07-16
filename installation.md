# TradingCodex Installation

Codex agents setting up TradingCodex for use must not run `git clone`. In an
empty target workspace, run the packaged CLI. On macOS/Linux:

```bash
uvx --refresh --from tradingcodex tcx attach . && ./tcx doctor
```

On native Windows PowerShell:

```powershell
uvx --refresh --from tradingcodex tcx attach .
.\tcx.cmd doctor
```

Clone this repository only when the user explicitly asks to develop
TradingCodex source code.

This guide covers install variants and smoke checks. For the shortest path, use
the Quick Start in `README.md`.

## Prerequisites

- Git on `PATH`; every generated workspace must belong to a Git worktree.
- `uvx` for the packaged install flow.
- An installed and authenticated `codex` CLI for native analysis.

When a standalone target is outside Git, `tcx attach` runs a local `git init`.
It never stages files, creates a commit or branch reference, configures a remote,
pushes, or associates the workspace with a GitHub account. If the target is
already inside a direct or parent worktree, attach preserves that repository.

## Agent Setup

Codex agents setting up TradingCodex for a user should run the
installer from the user-selected workspace, not clone the source repository.
They should also follow this rule: do not invent a default workspace path such
as `tradingcodex-workspace`. If the user did not name a target directory, ask
where to create or configure the TradingCodex workspace, unless they explicitly
asked to use the current workspace. Use the user's target workspace, then run
the installer there.

Use the platform command above. In the rest of this guide, `./tcx` means the
POSIX launcher; substitute `.\tcx.cmd` on native Windows.

The target workspace should be empty. A directory with only `.git` already
initialized is fine, as is an empty directory inside an existing parent
worktree.

Use Codex CLI 0.144.4 or later on macOS, Linux, WSL, or native Windows. Version
0.144.4 is the current TradingCodex reference for custom permission profiles,
hooks, required MCP startup, and explicit MultiAgent V2 configuration. Verify
with `codex --version`; `./tcx doctor --layer guidance` fails below the
compatibility floor and warns when the installed CLI is older or newer than the
validated reference. Release acceptance uses the exact reference version.
The default doctor output is concise: it reports layer totals and expands only
warnings or failures. Run `./tcx doctor --verbose` (or
`.\tcx.cmd doctor --verbose`) to inspect every individual check.

After installation, fully quit and restart Codex, then open and trust the
generated workspace and start from a new thread so project MCP config and hooks
are loaded. When Codex presents the generated project hooks, review and trust
all eight TradingCodex handlers; exact-role child lifecycle hooks require that
persisted trust. `--dangerously-bypass-hook-trust` is an automation escape
hatch, not a replacement for normal workspace setup or lifecycle validation.
Native execution is unavailable until that project layer is trusted; do not
substitute a shell or public-surface path. When
TradingCodex MCP autostarts the local service, the read-only workspace viewer is available at
the generated workspace's recorded service URL. Release workspaces use
`http://127.0.0.1:48267/` by default; development workspaces may use a different
loopback port. Run `./tcx service status --json` to print the selected address.

The PyPI package includes the compiled React viewer. End users and generated
workspaces do not need Node or npm. Analysis runs in native Codex; the Django
service only reads selected workspace state.

## Install From A Source Reference

Use `--from` when you need a local checkout, archive URL, or PEP 508 source
reference instead of the PyPI package. For example, from a source checkout:

```bash
uvx --refresh --from /path/to/tradingcodex tcx attach . --from /path/to/tradingcodex && ./tcx doctor
```

When the command itself is running directly from the checkout, `--dev` is the
shorthand for selecting that checkout as the explicit source. Keep the
generated workspace separate from the source repository:

```bash
uv run python -m tradingcodex_cli attach /path/to/empty-workspace --dev
cd /path/to/empty-workspace
./tcx doctor
```

`--dev` and `--from` are mutually exclusive. An installed wheel cannot infer a
private development checkout; run the command directly from the checkout as
above, or use the explicit outer and inner `--from` form.

Unless explicitly overridden, a new `--dev` attach isolates the runtime by
checkout. It selects a home below the platform default at
`development/source-<checkout-hash>` and a deterministic loopback port in the
`20000`-`29999` range. Development workspaces from the same checkout therefore
share one development ledger and service, while another checkout and ordinary
release workspaces remain separate. `TRADINGCODEX_HOME`,
`TRADINGCODEX_DB_NAME`, and `TRADINGCODEX_SERVICE_ADDR` remain explicit
overrides.

Source checkouts of this repository are for development. Generated
TradingCodex workspaces are separate Codex projects. The inner `--from` is the
explicit executable-source provenance declaration; `uvx` does not expose its
outer `--from` value to `tcx`. Local source paths are used only to build the
copied durable runtime. Generated files record `local-explicit`, never the local
path or a source-tree `PYTHONPATH`. Development commands import the live
checkout in an editable package-runner environment, but durable runtime
provisioning uses a clean source snapshot that excludes local `build/`,
`dist/`, caches, bytecode, state, and databases. Removing a source file is
therefore reflected on the next development attach or update even when an old
build tree still exists.

A bare value that is a valid package requirement remains a package name even
when the current directory contains a same-named folder. Use `./relative-dir`
or an absolute path for a local directory. TradingCodex rejects option-like
values, unsupported schemes, remote `file:` URLs, SCP-style locators,
credentials, and signed/query-bearing URLs before invoking its package runner.

Source developers changing the viewer use Node 22 only as a build tool:

```bash
npm ci --prefix frontend
npm test --prefix frontend
npm run build --prefix frontend
git diff --exit-code -- tradingcodex_service/static/tradingcodex_web
```

The Vite output is committed and served by Django and WhiteNoise. Do not run a
Node server as part of an installed TradingCodex service.

## Installer Script Equivalent (POSIX Only)

`install.sh` supports macOS/Linux POSIX shells only. It wraps the same `uvx`
flow and can bootstrap `uv` when missing. TradingCodex never persists the uvx
cache interpreter: attach provisions a versioned private runtime below the
external TradingCodex home and verifies MCP imports before rendering. The
installer keeps uv cache enabled only to make that provisioning efficient:

```bash
./install.sh .
```

Source developers can select the checkout containing `install.sh` without
repeating its path:

```bash
./install.sh --dev /path/to/empty-workspace
./install.sh --dev --update /path/to/existing-workspace
```

The script rejects `--dev` combined with `--from` and uses the checkout for
both the package-runner invocation and executable-source declaration. It also
applies the checkout-scoped development home and service address described
above.

Do not run `install.sh` on native Windows. Use the PowerShell `uvx` commands
above or install the console tool with `uv tool install tradingcodex`, then use
`tcx attach .` and `.\tcx.cmd doctor`.

## Global Runtime Home

Clean installs use these homes:

| Platform | Default home |
| --- | --- |
| macOS | `~/Library/Application Support/TradingCodex` |
| Windows | `%LOCALAPPDATA%\TradingCodex` |
| Linux | `${XDG_DATA_HOME:-~/.local/share}/tradingcodex` |

Run `tcx home status --json` to see the selected path/source and
`tcx home check` before changing runtime paths. `TRADINGCODEX_HOME` remains an
explicit override; `TRADINGCODEX_DB_NAME` remains an independent DB override.
TradingCodex v1 does not probe alternative home locations or move runtime data
between homes.

If attach, update, doctor, or service startup reports prerelease migrations or
project tables outside the clean v1 history, read the selected home and
database paths in the error before acting. TradingCodex intentionally leaves
that incompatible state untouched: it does not migrate, delete, archive, or
back it up. Either select a new empty `TRADINGCODEX_HOME` outside the workspace
(and unset or replace `TRADINGCODEX_DB_NAME` when configured), or stop all
TradingCodex processes and explicitly archive/remove the old selected state
yourself. Retry only after choosing one of those paths; v1 has no prerelease
fallback.

The native Windows CI smoke covers the wheel, launcher, generated config,
hooks, MCP pipes, doctor, packaged viewer assets, and local service
lifecycle. It does not claim a real Windows Codex CLI session; that limitation
remains explicit until separately exercised.

The update equivalent for an existing generated workspace is:

```bash
uvx --refresh --from tradingcodex tcx update . --from tradingcodex
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
uvx --refresh --from tradingcodex tcx update . --from tradingcodex
```

### Updating from 1.0.2 or 1.1.0 to 1.1.1

Run the new package as the updater; do not run the old generated launcher and
expect it to discover the new release automatically:

```bash
cd /path/to/existing-workspace
uvx --refresh --from "tradingcodex==1.1.1" \
  tcx update . --from "tradingcodex==1.1.1"
./tcx doctor
```

On native Windows PowerShell, run the same pinned updater and then use the
regenerated Windows launcher:

```powershell
cd C:\path\to\existing-workspace
uvx --refresh --from "tradingcodex==1.1.1" tcx update . --from "tradingcodex==1.1.1"
.\tcx.cmd doctor
```

The update keeps the workspace id, paper-account scope, user-owned research,
Brain and Strategy sources, connector sources, explicit runtime home, explicit
database override, and projected loopback service address. Version 1.1.1
applies the forward migration that removes the retired External MCP Gate
tables and Gate-derived broker connections while preserving ordinary broker,
order, and append-only audit history. The generated module lock advances to
1.1.1.

Version 1.1.1 replaces generated permission, hook, prompt, and skill files so
the first-meaningful-line invocation, limited-public Build fetch, and narrow
Build shell contracts load together. Build edits now use `apply_patch`; the
model-side shell is limited to public reads, read-only HTTPS Git, inert provider
review, isolated `py_compile`, limited workspace reads, and allowlisted
workspace-launcher commands. General interpreters, helper scripts, test runners,
and build systems that an older generated workspace may have admitted are now
blocked; run broader validation explicitly from a user or maintainer terminal.
Fully quit Codex after update, reopen the workspace, review and trust the
changed project hooks when prompted, and start a new task.
Persisted trust is content-bound, so a renewed prompt after this update is
expected and must not be bypassed.

User-installed MCP servers, skills, plugins, apps, and hooks remain native
Codex-owned BYOR capabilities and survive update. TradingCodex refreshes only
its reserved MCP and `tcx-*` projections, does not recommend or classify user
capabilities, and reports only a sanitized read-only inventory in System and
through `$tcx-server`.

The generated `$TRADINGCODEX_SCRATCH` path also moves from the 1.0.2 OS
temporary location into a workspace-id-scoped platform cache tree. Scratch
contents are disposable intermediates, not user-owned state, so the updater
does not migrate or preserve the old scratch tree. Provider files intended to
survive the update must already be in their reviewed workspace bundle; fetched
source still belongs only in the newly projected scratch staging directory.

Existing manually authored providers do not need provenance added solely for
the update. An unchanged, previously approved safe bundle keeps the same
content hash and immutable snapshot. New VCS metadata, symlinks, or
secret/credential-like files make a provider fail closed under the strengthened
1.1.1 supply-chain checks; TradingCodex neither deletes those files nor silently
re-approves the bundle. Inspect the reported bundle, remove unsafe material,
and perform a fresh interactive hash approval only when the resulting source is
the provider you intend to run.

`tcx update .` preserves `.tradingcodex/workspace.json`, including
`workspace_id` and internal paper-account scope, then re-renders generated template files,
refreshes generated indexes, applies central DB migrations, records workspace
provenance, and runs `./tcx doctor` unless `--no-doctor` is passed.

When the package refresh is launched directly through `uvx`, the updater also
restores an existing explicit `TRADINGCODEX_HOME`, explicit
`TRADINGCODEX_DB_NAME`, and projected loopback service address from the
validated workspace before regeneration. Explicit environment values on the
new update command still win, so a deliberate runtime move remains possible.
Platform-default homes are resolved again on the destination platform rather
than pinning an absolute path from a copied workspace.

Inside a generated Codex workspace, the default `trading-research` profile and
Plan mode cannot run workspace updates because update rewrites protected `.codex`
prompt/config/hook surfaces. Research may still create and edit ordinary
user-owned files outside `trading/`; that authority does not include generated
control files or managed TradingCodex state. If TradingCodex is already installed and startup
health says the workspace can be aligned to that installed version,
`head-manager` will ask you either to select `trading-build` and start a root native turn
whose first meaningful line invokes `$tcx-build`, or run this workspace-only
update from your terminal:

```bash
./tcx update --skip-refresh
```

In that valid `$tcx-build` turn, `head-manager` may run only the reported
workspace-local `update_status.command` (`./tcx update --skip-refresh`), then it
stops and tells you to fully restart Codex. The marker is current-turn intent
and does not elevate the default Research profile; Plan mode cannot issue the grant. If
a package update is required first, `update_status.command` is deliberately
empty. Run the reported `uvx --refresh ... tcx update .` or installer-script
update command from an interactive user terminal, then fully restart Codex.

For a workspace attached from a local checkout or archive, declare that source
again on every package refresh because the private machine locator is not
stored:

```bash
./tcx update --from /path/to/tradingcodex
```

From the source checkout, the development shorthands are:

```bash
uv run python -m tradingcodex_cli update /path/to/workspace --dev
./install.sh --dev --update /path/to/workspace
```

A development update preserves the workspace's recorded home and explicit DB
override, then regenerates its checkout-isolated service address. It refuses
to convert a release/index workspace in place; attach a separate development
workspace instead. This prevents a development MCP bootstrap from connecting
to a release ledger merely because both processes would otherwise choose the
default port.

TradingCodex rejects credentialed URLs, inline secrets, HTTP sources, signed or
query-bearing URLs, and fragments before it logs, renders, or records package
provenance. Use a credential manager or a pre-downloaded local artifact instead
of embedding authentication in `--from`.

After update, runtime order flows use central DB `OrderTicket` records directly.
The retired `execution-operator` role and `execute-paper-order` skill are removed
from clean generated workspaces. Final submit/cancel uses either the
explicit-only complete root-native `tcx-order-submit` or
`tcx-order-cancel` action grammar, or a first-meaningful-line
`$tcx-order-allow` current-turn grant consumed once through Head Manager's proof-protected
`use_order_turn_grant`. Public REST, generic CLI, fixed roles, and direct MCP
callers expose no usable execution mutation; the protected tool is inert
without current hook proof. A locally modified retired generated file still
causes update to fail closed rather than delete user content.

After update, fully quit and restart Codex, then start from a new thread in the
updated workspace so project MCP config and generated prompts are reloaded.

Generated workspaces project `gpt-5.6-sol`/xhigh for root `head-manager`,
and Terra/high for all nine fixed subagents. Final execution is service-owned
and runs no model.
MultiAgent V2 exposes exact custom-role routing through the `agents` namespace;
each task uses `fork_turns="none"` and a fresh role-bound child. The generated
V2 table explicitly sets `enabled = true` and a seven-thread session ceiling
(one root plus six child slots); it does not mix the V1-only
`agents.max_threads` setting into the V2 contract.
Inspect `.tradingcodex/generated/model-policy-manifest.json` and
`./tcx doctor --layer guidance` for registry/projection status. There is no
runtime model fallback or rollback mode. A manifest support status of
`unverified` means no installed-client capability input was supplied; it is not
evidence that a real Codex session accepted the model.
`TRADINGCODEX_CODEX_SUPPORTED_MODELS` may be provided during attach or update;
generation fails when a required selector is absent rather than silently
changing models. Update Codex or restore model access, then rerun `tcx update`.

## Codex MCP And Local Service

Generated `.codex/config.toml` starts TradingCodex MCP with either an explicit
validated stable Python or the versioned managed environment under
`TRADINGCODEX_HOME/runtime/python/`. It never records uvx `archive-v0` or
editable `builds-v0`; local-source update preserves the managed interpreter
across its temporary runner. This avoids package-manager writes inside
Research-profile analysis sessions and survives `uv cache clean`. MCP startup also autostarts the
local Django service that hosts the viewer, Admin, and API, and propagates the recorded package spec into
the detached service. `tcx update` refreshes the package and regenerates the
interpreter binding.

New workspaces start with an isolated paper profile derived from the immutable
workspace id. Additional paper profiles created with `./tcx profile create`
remain explicit workspace account scopes; investor suitability stays in the
separate Investor Context file.

Open the generated workspace in Codex and trust the project. After Codex
connects, these local service surfaces are available at the service URL
reported by `./tcx service status --json`:

- `/` for the read-only React workspace viewer
- `/admin/` for the Django operations console
- `/api/health/live` for process liveness
- `/api/health/ready` for DB, migration, and state-path readiness

The release default is `http://127.0.0.1:48267/`; development bootstrap uses
its recorded checkout-isolated loopback port unless explicitly overridden.

For CLI-only use outside Codex, the local service can still be started
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
./tcx investor-context status
./tcx profile update --base-currency EUR
```

The internal paper account scope's validated three-letter base currency controls paper cash
defaults and order-policy notional comparison. Orders in another currency need
a point-in-time FX snapshot. New profiles use `USD` only as the package
bootstrap default; set `--base-currency` to the portfolio's actual reporting
currency before creating orders.

Create and search workspace-file-native research memory:

```bash
mkdir -p trading/research/.drafts
printf '%s\n' '---' 'artifact_id: note-1' '---' '# Research Note' '' '[factual] Gross margin example.' > trading/research/.drafts/note.md
./tcx research create --markdown-file trading/research/.drafts/note.md --universe public_equity --artifact-id note-1 --title "Research Note"
./tcx research search "gross margin"
./tcx research export note-1
./tcx research spec list
./tcx forecast list
```

ResearchSpec/replay/ExperimentRun and forecast issue/revise/resolve operations
accept JSON payload files or `-` for stdin. Forecast authorship and resolution
are separate: generated MCP/API workflows use an evidence role to issue or
revise and `judgment-reviewer` to resolve from a reviewed source snapshot.
These commands remain evidence-only and never authorize an order.

---
name: tcx-build
description: Mark one exact root Codex turn as explicit workspace-local TradingCodex Build intent for workspace refresh, managed optional-role-skill lifecycle work, managed MCP configuration, and broker/API provider development without elevating filesystem permission or granting order execution. Strategy and Investment Brain management use their own direct skills instead.
---

# TCX Build

Use this skill only when the original root user prompt places `$tcx-build` on
its first meaningful line. Accept the plain token or a Markdown skill link only
when its label and target match this workspace's projected
`tcx-build/SKILL.md`. Ignore leading blank lines and normalized line-ending
variants. The concrete non-empty request may share the invocation line or
follow it.

## Turn Contract

- Treat the marker as current-turn user intent, not filesystem permission.
- Codex's active native permission profile still decides what tools can reach.
  The marker and hook do not elevate the default `trading-research` profile.
  Ordinary user-owned files outside `trading/` can be changed in Research and
  are not Build work. Start controlled `trading/`, managed lifecycle, or
  connector work in a new root turn with `trading-build` selected; canonical DB
  calls remain separately proof-protected and service-owned.
- Do not wrap `$tcx-brain` or `$tcx-strategy` work in this marker. Those skills
  use separate capability-scoped turns in the normal `trading-research`
  profile. If the request is actually Brain or Strategy management, stop and
  return the corresponding direct first-meaningful-line prompt.
- Do not issue or use a Build grant while Codex is in platform Plan mode.
  Start a new root turn in `trading-build` when the requested work must edit
  files. The grant is bound to the permission mode in which it was issued, so
  switching profiles does not carry authority forward.
- Use the grant only in the root native Codex turn. Subagents
  cannot inherit or use it.
- The grant is multi-use within this turn so editing and validation can finish.
  A follow-up turn that mutates state must invoke `$tcx-build` first again.
- For recurring Automation, require the saved prompt to start with the marker
  on every run. File-mutating work also requires `trading-build`; prefer an
  isolated worktree or workspace and retain a reviewable diff.
- Keep direct edits and commands workspace-local. Use typed TradingCodex
  MCP services for connector state. User-installed Codex capabilities remain
  outside Build ownership.
- Keep local work in the native Build lane: use `apply_patch` for every edit.
  Shell access is an intentionally narrow review lane: credential-free public
  HTTP(S) GET/HEAD, enumerated read-only HTTPS Git retrieval, limited
  workspace `pwd`/`cat`/`ls`, inert provider-source reads/hash/diff/Git
  inspection, exact isolated `python -I -S -m py_compile`, and allowlisted
  workspace-launcher commands. General interpreters, helper scripts, test
  runners, build systems, shell composition, and model-authored POST are
  blocked throughout an active Build turn/profile. The profile permits
  ordinary workspace writes through admitted edit/service surfaces, the
  dedicated `$TRADINGCODEX_SCRATCH` path, and credential-free public HTTP(S)
  and HTTPS Git retrieval, but denies
  TradingCodex runtime/DB state, credentials, protected ledgers, local/private
  destinations, authenticated requests, uploads, network package installation,
  remote mutation, and global Codex config. Native `web_search` remains
  disabled. The native proxy uses full HTTP transport only for the protocol
  POST required by read-only Git Smart HTTP; model-authored HTTP remains
  GET/HEAD-only and general POST is blocked. Trusted workspace-launcher
  lifecycle commands remain allowlisted and proof-gated.

If the actual Codex permission blocks a required tool, report that platform
blocker and stop. Do not create another TradingCodex permission state.

## Procedure

1. Confirm the request is product/build work, not an investment recommendation or execution request.
2. For self-update, inspect status only after an explicit user request. When `package_refresh_user_terminal_required=true`, do not run the refresh and return `interactive_user_terminal_command`. Otherwise run a non-empty `update_status.command` only when it is the exact allowlisted workspace-launcher command admitted by the Build lane; never substitute a package runner, interpreter, helper script, or composed shell command. If the exact command is unavailable, return the reported terminal command. After an update, stop and tell the user to fully restart Codex.
3. If the request is Investment Brain management, stop with a new prompt whose
   first meaningful invocation is `$tcx-brain`. If it is Strategy management,
   stop with a new prompt whose first meaningful invocation is
   `$tcx-strategy`. Never issue those
   capability grants from a Build turn or combine their markers.
4. For a managed optional role skill, author the standalone body with
   `apply_patch` in a workspace-local staging file, then use the exact
   `{{TRADINGCODEX_WORKSPACE_LAUNCHER}} skills optional ...` lifecycle command
   so validation and projection remain service-owned. Do not directly repair
   generated skill folders, role TOML, or root projection blocks. Activation
   still requires the user's explicit request. Do not run a helper, interpreter,
   test runner, or build system around that lifecycle command; if broader
   validation is needed, return an explicit user-terminal or maintainer step.
5. Do not install, remove, enable, disable, recommend, or classify user-owned
   Codex skills, plugins, apps, hooks, or MCP servers. `$tcx-server` may inspect
   their secret-free native inventory; configuration remains owned by Codex.
6. For broker connectors, inspect providers with the read-only provider-list
   tool. If the provider is already installed and available after any required
   restart, call `render_broker_connector_scaffold`. It returns target content
   plus content-addressed preimage existence/hash/size metadata and performs no
   workspace write; it never returns existing file content. Verify those
   preimages and create or update the returned files with `apply_patch`; never
   ask an MCP scaffold tool to write them. Use only the build-protected DB tools
   `register_broker_connector` and `validate_broker_connector_build` for
   service state. `connect` and the
   write-style `scaffold` command remain explicit user-terminal operator flows
   and are not agent MCP tools; do not invoke their CLI equivalents from the
   agent shell.
7. If the requested provider is missing, develop and approve the provider
   before rendering or registering a connector. Create a
   `provider_development_required` connector first only when the user explicitly
   asks for scaffold-only output; do not leave one behind as apparent progress
   for an implementation or connection request.
8. Fetch provider source only from credential-free public HTTP(S) or HTTPS Git
   endpoints and only into
   `$TRADINGCODEX_SCRATCH/provider-sources/<provider-id>/`. GET/HEAD and public
   Git clone/fetch/ls-remote are retrieval; URL userinfo, auth/cookie/API-key
   headers, model-authored request bodies, uploads, non-GET/HEAD HTTP methods,
   SSH/file/git transports, package installation, fetch-to-shell pipelines,
   push, and remote changes are forbidden. Git Smart HTTP's internal protocol
   POST is available only through those enumerated read-only Git commands.
   Follow the Build-turn context's command proof exactly: when the hook does
   not receive the shell workdir, use its advertised absolute executable and
   absolute staged operands, and use absolute `git -C` roots. Curl retrieval
   includes `--globoff` (or `-g`), and Git clone includes `--no-checkout` so
   fetched material remains inert. Relative workspace commands are valid only
   with the exact generated workspace root as the tool workdir. For a new
   provider sourced directly by HTTP(S) rather than Git, one curl command may
   use `--create-dirs` with exactly one URL and one explicit
   `--output <provider-id>/<file>` beneath the provider-sources staging root.
   That exception may create only the one fresh direct provider-id directory;
   it is not general directory-creation authority. Nested output paths,
   `--remote-name`/`--output-dir` forms, repeated `--create-dirs`, and use
   after that provider directory exists remain blocked. Fetch later files into
   the existing real provider directory without `--create-dirs`.
   Inspect, hash, diff, and statically validate staged source; never execute or
   install it.
9. When external material informed a provider, include
    `source-provenance.json` in the final bundle. Record `schema_version: 1` and a
    `sources` entry for each source with `kind` (`https` or `git`), a public
    credential-free HTTPS `url` without userinfo/query/fragment, an optional
    `requested_ref`, and exactly one resolved identifier. HTTPS sources use
    `resolved_ref`; Git sources use `resolved_ref` or `resolved_commit`. Each
    entry also includes `fetched_content_sha256` and an RFC 3339
    `retrieved_at`. Legacy providers
    authored without external source remain compatible without this optional
    file. Never copy `.git`, `.hg`, `.svn`, credential/key/`.env` material, or
    symlinks into a provider bundle. Write every final provider file with
    `apply_patch`, not a downloader, redirect, copy, or move into `trading/`.
10. Store only credential references, env key names, and secret schemas. Never
    request or persist raw credentials. Review source inertly, use only the exact
    isolated `python -I -S -m py_compile <provider-python-files...>` form for
    syntax checking, and use allowlisted provider inspection for static contract
    validation before approval; do not import provider code.
11. After final provider files are ready, run the trusted read-only
    `{{TRADINGCODEX_WORKSPACE_LAUNCHER}} connectors inspect-provider <provider-id>`
    command. In Build it may return `inspection_scope=bundle_only` with
    `approval_status=service_check_required` because the central ledger remains
    denied. Use that result only to report the inert bundle, secret-free
    provenance summary, and exact hash; never treat it as canonical approval
    state. The workspace provider remains untrusted until the user repeats the
    inspection and approves its exact bundle hash from an interactive terminal.
    Stop with
    `{{TRADINGCODEX_WORKSPACE_LAUNCHER}} connectors inspect-provider <provider-id>`
    and
    `{{TRADINGCODEX_WORKSPACE_LAUNCHER}} connectors approve-provider <provider-id>`,
    and require re-approval after every bundle, provenance, or helper change;
    never approve provider code from the Build turn. Approval snapshots the
    reviewed bytes but executes no code. Report `service_restart_required` and
    stop until the service restarts. Connector render, registration, and
    validation then resume in a fresh Build turn.
12. In the generated Build turn, validate only through the trusted allowlisted
    `{{TRADINGCODEX_WORKSPACE_LAUNCHER}} doctor`/inspection paths, limited
    workspace `pwd`/`cat`/`ls`, inert provider reads/hash/diff/Git inspection,
    and exact isolated `python -I -S -m py_compile` for admitted provider or
    connector Python files. General interpreters, helper scripts, unit or smoke
    test runners, and build systems are not available in an active Build turn;
    return broader validation as an explicit user-terminal or maintainer step.
    Stop after a successful self-update and tell the user to restart Codex.

## Hard Stops

- A Build turn may create live-capable providers, but never submits or cancels an order.
- A Build turn does not manage an Investment Brain or Strategy. Use the direct
  capability-scoped skill turn instead.
- Do not use Codex Plan mode as Build authority; it blocks the grant entirely.
  In the default `trading-research` profile, ordinary user-owned paths outside
  `trading/` remain writable, but do not attempt controlled `trading/` or
  managed lifecycle edits. A non-writable Automation runtime remains limited
  to rendering/inspection, temporary computation, and specifically
  proof-protected canonical DB calls.
- Do not use the grant for global Codex config, user-owned Codex capability management, raw credential access, provider-source approval, Git push/publication, or direct edits to hooks, grants, managed `.gitignore`, credential files, runtime DB, audit, approval, policy, or execution state.
- Do not directly edit generated core harness files, hooks, workspace templates,
  fixed-role configuration, or service-owned projection blocks. Use the
  supported workspace refresh or managed lifecycle service instead.
- Do not call raw broker APIs from shell, hooks, skills, or ad hoc scripts.
- Do not execute, import, or install fetched provider source before the exact
  bundle is approved and loaded from its immutable post-restart snapshot.
- Do not bypass TradingCodex policy, approval, idempotency, connection, or audit gates.
- If a protected call reports that the operation completed but grant
  finalization failed, stop and inspect canonical state. The grant is revoked
  fail-closed; never retry the operation blindly.
- Order submission or cancellation belongs outside Build and must enter through
  the exact native execution gateway for its own current root turn. Broker API,
  SDK, or broker-specific MCP calls stay behind reviewed service adapters.
- Do not rewrite user-owned Codex config outside TradingCodex managed blocks.

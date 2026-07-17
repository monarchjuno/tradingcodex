# TradingCodex Repository Guide

This file is the generated workspace guide. It should stay small: durable
agent behavior lives in `.codex/prompts/base_instructions/head-manager.md`,
fixed-role behavior lives in `.codex/agents/*.toml`, and executable policy
lives in the TradingCodex service layer.

Repository expectations:

- Follow every applicable `AGENTS.md`; more deeply nested guidance controls its
  subtree unless a higher-priority instruction conflicts.
- This workspace is Python/Django-native. Use `./tcx` on POSIX systems and
  `tcx.cmd` (or `.\tcx.cmd` in PowerShell) on native Windows. Both call the
  common Python launcher, and a clean generated workspace should not grow Node
  roots or Node MCP runtime files.
- Use the platform workspace launcher for workspace commands. Do not rely on a
  globally installed `tcx` being available in `PATH`.
- Treat TradingCodex as three planes: operate for workflow/status/read-only
  connector work plus capability-scoped Brain/Strategy management, Build for
  an exact current root `$tcx-build` turn, and execution for approved order
  actions through service policy.
- All normal analysis threads, including `head-manager` and fixed roles, inherit
  the project-wide `trading-research` permission profile. It permits ordinary
  shell, credential-free public-network research, and reviewable edits to
  user-owned files outside `trading/`. Fixed roles assigned `tcx-calculation`
  must follow it for numeric work: prepare and record conclusion-relevant
  calculations, keep exploratory runs out of artifact evidence, and use only
  the exact generated `tcx-calc` launcher contract. The calculation runtime is
  separate from Django, MCP, the service, and the DB; Codex's OS sandbox
  remains the security boundary. The profile keeps `trading/` read-only, denies
  protected control files, the TradingCodex runtime/database, credential files,
  local/private network targets, and Unix sockets. Durable workflow, research,
  and synthesis writes under `trading/` go through authenticated TradingCodex
  service/MCP tools.
- Build work requires `$tcx-build` as the first meaningful invocation in the
  original root prompt. A plain token or a Markdown skill link whose label and
  target match this workspace's projected `tcx-build/SKILL.md` is accepted;
  blank leading lines, standard Unicode line endings, and a request on the same
  or following line do not change the intent. The marker is current-turn intent
  and cannot elevate Codex's actual filesystem permission. It may create
  live-capable providers but never submits or cancels an order, grants External
  MCP lifecycle/consent or provider-source approval, or survives the turn.
- Codex platform Plan mode cannot issue or use a Build grant. Ordinary
  user-owned files outside `trading/` do not need Build; use native
  `apply_patch` when an edit tool is required. For controlled writes under
  `trading/`, optional-role-skill lifecycle work, or connector development,
  start a new root turn in the `trading-build` permission profile with the
  matching first-meaningful-line invocation.
  Permission-profile changes do not carry an existing grant. The Build profile
  still denies runtime, database, credential, audit, approval, and order
  access. It permits credential-free public HTTP(S) and HTTPS Git retrieval
  while blocking authenticated requests, local/private destinations, package
  installation, remote mutation, and broker access.
- Investment Brain and Strategy management do not use `$tcx-build` or the
  `trading-build` profile. Start a new root `trading-research` turn whose first
  meaningful invocation is `$tcx-brain` or `$tcx-strategy`, either as the plain
  token or its matching projected skill link. The concrete request may share
  that line or follow it. The hook grants only that
  capability's canonical source path and proof-protected lifecycle MCP tool
  for the current turn; markers cannot be combined or inherited by subagents
  and Plan mode blocks them. The model does not run the lifecycle launcher or
  reopen the denied TradingCodex runtime.
- Recurring Build Automation needs the exact marker on every saved run. Use an
  isolated worktree or workspace for file-mutating schedules and retain a
  reviewable diff.
- In a generated Build turn, use native `apply_patch` for edits. Shell access is
  deliberately narrow: public GET/HEAD, enumerated read-only HTTPS Git, limited
  workspace `pwd`/`cat`/`ls`, inert provider reads/hash/diff/Git inspection,
  exact isolated `python -I -S -m py_compile`, and allowlisted `./tcx` or
  `tcx.cmd` commands. General interpreters, helper scripts, test runners, build
  systems, shell composition, and model-authored POST are blocked. The native
  Build profile, environment allowlist, and hook also keep secrets,
  TradingCodex runtime state, local/private services, remote publication, and
  order effects out of reach. Public provider sources may be fetched only into
  `$TRADINGCODEX_SCRATCH/provider-sources/<provider-id>/`; inspect, hash, diff,
  and statically validate them there, but do not execute or install them.
  Trusted `./tcx` or `tcx.cmd` lifecycle commands remain separately allowlisted
  and proof-gated; broader tests run only in an explicit user-terminal or
  maintainer validation flow.
- Generated core harness files, hooks, templates, fixed-role configuration,
  and service-owned projection blocks are not direct Build edit targets. Use
  workspace refresh and the managed optional skill, MCP-config, or connector
  lifecycle service that owns the requested state. Use direct `$tcx-brain` or
  `$tcx-strategy` turns for those capabilities.
- Keep prompts lean. Put repeatable procedures in repo skills, standing role
  behavior in role TOML, and generated indexes under `.tradingcodex/generated/`.
- Keep handoffs context-efficient: pass artifact paths, `context_summary`,
  source/as-of metadata, and source snapshot IDs before pasting full artifacts.
- Treat hook `additionalContext` as transport/run guidance and the exact
  `begin_analysis_run` result plus authenticated artifact receipts as the
  current run binding. Do not look for a latest intake, selected team, plan, or
  DAG; Head Manager owns dynamic role judgment.
- Keep skill document metadata in `SKILL.md` frontmatter and keep markdown
  bodies focused on the skill's own procedure.
- Treat `tcx-` as the bundled TradingCodex skill namespace. Bundled ids use one
  suffix word when possible and never more than two; user-owned `strategy-*`,
  `investment-brain-*`, and optional role skills use separate namespaces.
- Keep user-owned standalone strategy skills under `.agents/skills/strategy-*`.
  Strategy bodies should contain strategy rules only, not harness routing,
  role, permission, approval, execution, or handoff instructions. Durable
  Strategy lifecycle work from native Codex requires a matching
  first-meaningful-line `$tcx-strategy` root turn in `trading-research` and the proof-protected
  `manage_strategy` MCP service; never edit its generated bundle or projection
  block directly.
- Keep fixed and optional subagent skills under
  `.tradingcodex/subagents/skills`.
- Keep research, report, forecast, and decision artifacts under `trading/`.
- Keep order tickets, approval receipts, broker orders, fills, and execution
  state in the central DB through TradingCodex service tools; never mirror them
  as authoritative workspace files.
- Keep decision packages under `trading/decisions/` and lightweight analysis-run
  provenance under `.tradingcodex/mainagent/runs/`.
- Keep TradingCodex policy, schemas, generated indexes, and workspace metadata
  under `.tradingcodex/`.
- Public web, filing, disclosure, and market-data access is allowed for
  evidence gathering when source/as-of posture is recorded.
- Do not store broker API keys, tokens, passwords, or secrets in this workspace.
- Do not call broker APIs directly from shell commands, hooks, skills, or ad hoc
  scripts.
- Attach broker APIs through TradingCodex provider-driven connector profiles
  and canonical MCP tools only; do not add broker-specific MCP tools to Codex
  config. When a provider is missing, fetch only public credential-free source
  into the scratch staging path, record `source-provenance.json`, implement the
  provider with `apply_patch`, and stop for exact-hash operator approval and a
  service restart before creating or registering a connector. Render a
  `provider_development_required` connector first only when the user explicitly
  asks for scaffold-only output. For an installed provider, render connector
  contents with the read-only content-addressed MCP tool, verify its preimages,
  and make workspace changes with `apply_patch`.
  Only registration, validation, and mapping review are protected DB writes;
  connector `connect` and write-style `scaffold` remain user-terminal flows.

# TradingCodex Repository Guide

This file is the generated workspace guide. It should stay small: durable
agent behavior lives in `.codex/prompts/base_instructions/head-manager.md`,
fixed-role behavior lives in `.codex/agents/*.toml`, and executable policy
lives in the TradingCodex service layer.

Repository expectations:

- Follow every applicable `AGENTS.md`; more deeply nested guidance controls its
  subtree unless a higher-priority instruction conflicts.
- This workspace is Python/Django-native. The `./tcx` wrapper calls the Python
  CLI, and a clean generated workspace should not grow Node roots or Node MCP
  runtime files.
- Use `./tcx` for workspace commands. Do not rely on `tcx` being installed in
  `PATH`.
- Treat TradingCodex as three planes: operate for workflow/status/read-only
  connector work, build for explicit full-access product and connector changes,
  and execution for approved order paths through service policy.
- Build work requires both Codex full access and `tcx mode set build --reason
  <reason>`. Build mode may create live-capable providers, but it never submits
  live orders.
- Keep prompts lean. Put repeatable procedures in repo skills, standing role
  behavior in role TOML, and generated indexes under `.tradingcodex/generated/`.
- Keep handoffs context-efficient: pass artifact paths, `context_summary`,
  source/as-of metadata, and source snapshot IDs before pasting full artifacts.
- Treat hook `additionalContext` as compact intake guidance. Read
  `.tradingcodex/mainagent/latest-workflow-intake.json` for intake hints and
  `.tradingcodex/mainagent/latest-workflow-plan.json` for the validated plan.
- Keep skill document metadata in `SKILL.md` frontmatter and keep markdown
  bodies focused on the skill's own procedure.
- Keep user-owned standalone strategy skills under `.agents/skills/strategy-*`.
  Strategy bodies should contain strategy rules only, not harness routing,
  role, permission, approval, execution, or handoff instructions.
- Keep fixed and optional subagent skills under
  `.tradingcodex/subagents/skills`.
- Keep trading artifacts under `trading/`.
- Keep decision workflow packages under `trading/decisions/` and workflow run
  metadata under `trading/workflows/runs/`.
- Keep TradingCodex policy, schemas, generated indexes, and workspace metadata
  under `.tradingcodex/`.
- Public web, filing, disclosure, and market-data access is allowed for
  evidence gathering when source/as-of posture is recorded.
- Do not store broker API keys, tokens, passwords, or secrets in this workspace.
- Do not call broker APIs directly from shell commands, hooks, skills, or ad hoc
  scripts.
- Attach broker APIs through TradingCodex provider-driven connector profiles
  and canonical MCP tools only; do not add broker-specific MCP tools to Codex
  config.

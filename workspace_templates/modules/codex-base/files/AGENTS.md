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
- Keep prompts lean. Put repeatable procedures in repo skills, standing role
  behavior in role TOML, and generated indexes under `.tradingcodex/generated/`.
- Keep handoffs context-efficient: pass artifact paths, `context_summary`,
  source/as-of metadata, and source snapshot IDs before pasting full artifacts.
- Treat hook `additionalContext` as compact dispatch guidance. Read
  `.tradingcodex/mainagent/latest-user-prompt-gate.json` only when the full
  starter prompt is needed.
- Keep skill document metadata in `SKILL.md` frontmatter and keep markdown
  bodies focused on the skill's own procedure.
- Keep user-owned standalone strategy skills under `.agents/skills/strategy-*`.
  Strategy bodies should contain strategy rules only, not harness routing,
  role, permission, approval, execution, or handoff instructions.
- Keep fixed and optional subagent skills under
  `.tradingcodex/subagents/skills`.
- Keep trading artifacts under `trading/`.
- Keep TradingCodex policy, schemas, generated indexes, and workspace metadata
  under `.tradingcodex/`.
- Do not store broker API keys, tokens, passwords, or secrets in this workspace.
- Do not call broker APIs directly from shell commands, hooks, skills, or ad hoc
  scripts.
- Attach broker APIs through TradingCodex native connector profiles and
  canonical MCP tools only; do not add broker-specific MCP tools to Codex
  config.

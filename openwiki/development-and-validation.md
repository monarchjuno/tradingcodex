# Development And Validation

Use this page to pick the smallest meaningful validation set before handoff. Human-facing validation detail lives in [docs/validation-and-test-plan.md](../docs/validation-and-test-plan.md).

## Baseline Commands

```bash
python -m pytest
python manage.py check
python -m compileall tradingcodex_cli tradingcodex_service apps tests
python manage.py runserver 127.0.0.1:48267
```

Use `python -m pytest` after ordinary source/test changes. Use `python manage.py check` after Django settings, model, admin, API, MCP, or service wiring changes. Use compileall after broad import, packaging, or migration changes.

## Validation Router

| Change area | Minimum useful validation |
| --- | --- |
| Docs/OpenWiki/AGENTS only | link/file existence checks, quick read of changed Markdown |
| CLI command | focused tests for command behavior, generated wrapper smoke if workspace-facing |
| Django model/service/API/web | focused pytest plus `python manage.py check` |
| MCP registry/handler/allowlist | `tools/list` smoke plus focused MCP tests |
| Research memory/artifact quality | create/search/export/source snapshot flow and `tcx quality-check --strict` |
| Generated templates/hooks/prompts/skills | disposable workspace smoke and generated contract inspection |
| Routing/head-manager/subagents | generated workspace smoke plus Codex-native smoke when available |
| Safety/order/approval/execution/broker | focused pytest, `python manage.py check`, MCP/order smoke, policy/idempotency checks |

## Generated Workspace Smoke

```bash
rm -rf /tmp/tradingcodex-harness-smoke
python -m tradingcodex_cli attach /tmp/tradingcodex-harness-smoke
cd /tmp/tradingcodex-harness-smoke
./tcx doctor
./tcx doctor --layer codex-native
./tcx doctor --layer improvement
./tcx subagents status
./tcx skills list --all
./tcx subagents prompt "Analyze NVDA. No order, no trading, no valuation."
printf '{"prompt":"Analyze NVDA. No order, no trading, no valuation."}\n' | python .codex/hooks/tradingcodex_hook.py user-prompt-submit
```

Inspect generated `AGENTS.md`, `.codex/config.toml`, role TOML, hook output, generated indexes, `.tradingcodex/mainagent/latest-workflow-intake.json`, `.tradingcodex/mainagent/latest-workflow-plan.json` when present, `.tradingcodex/mainagent/subagent-session-state.json` when present, and `trading/audit/codex-hooks.jsonl`.

## Codex CLI Smoke

When skill text, role TOML, head-manager instructions, hooks, routing, or handoff behavior changes, run this when Codex CLI/auth is available:

```bash
codex exec -C /tmp/tradingcodex-harness-smoke --skip-git-repo-check --dangerously-bypass-hook-trust --output-last-message /tmp/tradingcodex-codex-smoke.txt \
  'Harness smoke only. Do not produce investment analysis. Confirm the TradingCodex head-manager instructions loaded, identify the selected team for "Analyze NVDA. No order, no trading, no valuation.", and stop at dispatch/waiting status.'
```

If Codex CLI or authentication is unavailable, record the blocker and still run generated workspace, hook, and starter-prompt checks.

## MCP Smoke

```bash
printf '{"jsonrpc":"2.0","id":1,"method":"tools/list"}\n' | ./tcx mcp stdio
```

Confirm valid MCP output, expected tool annotations, role allowlists, approval/audit requirements, and no non-MCP stdout.

## Quality Failure Signals

Treat generated behavior as failed if `head-manager` performs substantive investment analysis before accepted subagent artifacts, expands beyond the selected team, ignores negated scope, bypasses role/tool boundaries, or cannot state `waiting`, `revise`, `blocked`, or accepted handoff status.

Generated role artifacts should include artifact path, source/as-of or retrieved-at posture, claim discipline, confidence, missing evidence, readiness/support gaps, role-boundary conflicts, next eligible recipient, and blocked actions.

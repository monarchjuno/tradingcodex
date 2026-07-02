# TradingCodex Tests

Primary validation:

```bash
pytest
python manage.py check
python -m compileall tradingcodex_cli tradingcodex_service apps tests
```

The Python migration smoke suite covers:

- workspace generation contract
- attach/init-time central Django DB setup without workspace-local DB creation
- immutable workspace identity and active profile metadata
- nine fixed subagents and twenty-four core repo skills
- default user-facing skill listing separated from full internal skill inventory
- starter prompt routing for negated execution requests
- starter prompt routing for guardrail-verification wording and earnings/catalyst thesis review
- order ticket validation, approval, and paper execution
- approved-order idempotency so repeated submission is rejected before portfolio mutation
- DB-backed Principal/Capability enforcement before MCP handler dispatch and policy decisions
- restricted symbol and disabled live adapter blocking
- MCP initialize/tools/list/tools/call surfaces
- MCP registry metadata, External MCP Gate lifecycle, role-gated tool calls, JSON-RPC batch handling, and non-research DB tool-call ledger
- service-layer MCP registry helpers creating audit events outside custom Admin actions
- generated `mcp ledger` inspection of central DB tool-call history for non-research tools
- two generated workspaces keeping separate research markdown/source-snapshot files while sharing central non-research runtime state
- profile selection controlling paper portfolio separation
- Django Ninja health, harness, subagent, and policy endpoints
- file-native research artifact create/get/search/export through MCP, Ninja, and generated workspace CLI
- Django project checks

For template/bootstrap changes, also create a throwaway workspace and run:

```bash
rm -rf /tmp/tradingcodex-smoke
mkdir -p /tmp/tradingcodex-smoke
cd /tmp/tradingcodex-smoke
tcx attach .
tcx workspace status
tcx profile status
./tcx doctor
```

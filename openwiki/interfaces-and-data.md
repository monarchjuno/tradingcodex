# Interfaces And Data

Use this page before changing Web, Admin, API, MCP, CLI command behavior, model ownership, research memory, or data flow. Human-facing detail lives in [docs/interfaces-and-surfaces.md](../docs/interfaces-and-surfaces.md), [docs/system-architecture.md](../docs/system-architecture.md), and [docs/research-memory-and-artifacts.md](../docs/research-memory-and-artifacts.md).

## Interface Rule

Every interface is a caller of the service layer. No interface should create a parallel policy, order, approval, execution, portfolio, broker, research, or audit path.

| Surface | Main files | Boundary |
| --- | --- | --- |
| Product web | `tradingcodex_service/web.py`, `tradingcodex_service/templates/web/*` | Review, preview, broker/read-only setup, research display, order drafts/checks. No agent spawn, approval, or execution. |
| Django Admin | `apps/*/admin.py` | Local/staff DB inspection. No custom bypass path. |
| Ninja API | `tradingcodex_service/api.py` | Typed local/staff control endpoints that call services. |
| MCP | `tradingcodex_service/mcp_runtime.py` | Role-scoped approved action boundary for agents. No raw REST or broker proxy. |
| CLI | `tradingcodex_cli/commands/*` | Operator and generated-wrapper interface. Should call services. |

## Research Memory

Research is workspace-file-native. Canonical files:

- `trading/research/*.md`
- `trading/research/*.evidence.md`
- `trading/reports/<role>/*.md`
- `trading/research/source-snapshots/*.json`
- `*.run-card.json` beside research, report, decision, order, or approval artifacts
- `*.validation-card.json` beside research, report, decision, order, or approval artifacts
- `trading/forecasts/*.jsonl`

Research service calls may index, validate, search, preview, version, and write these files, but the markdown or JSON file is the source of truth. Research MCP calls intentionally skip DB tool-call ledger rows.

## Central DB Data

Central DB model families:

- policy: `Principal`, `Capability`, `RestrictedSymbol`, `PolicyDecision`
- orders: `OrderTicket`, `OrderCheckRun`, `ApprovalReceipt`, `ExecutionResult`, `OrderEvent`, `BrokerOrder`, `Fill`
- portfolio: `PortfolioSnapshot`, `Position`, `CashBalance`, `PortfolioLedgerEvent`, `BrokerSyncRun`, `ReconciliationRun`
- integrations: `AdapterDefinition`, `BrokerConnection`, `BrokerAccount`, `InstrumentMap`
- workflows: `WorkflowRun`, `ArtifactRef`
- MCP: tool definitions/calls and external MCP registry/review/call models
- harness: `WorkspaceContext`
- audit: `AuditEvent`

## Edit Checklist

When changing this area:

- put durable behavior in `tradingcodex_service/application/*`
- update `docs/interfaces-and-surfaces.md` for user/admin/API/MCP/CLI behavior changes
- update `docs/system-architecture.md` for app/model/service ownership changes
- update `docs/research-memory-and-artifacts.md` for research file contracts
- run focused tests plus `python manage.py check` for Django surface changes
- run MCP smoke for MCP registry, handler, bridge, or role allowlist changes

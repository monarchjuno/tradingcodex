from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class HarnessComponent:
    id: str
    label: str
    summary: str
    status: str
    tags: tuple[str, ...]
    surfaces: dict[str, tuple[str, ...]]
    depends_on: tuple[str, ...]
    owned_capabilities: tuple[str, ...]
    validation: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "summary": self.summary,
            "status": self.status,
            "tags": list(self.tags),
            "surfaces": {key: list(values) for key, values in self.surfaces.items()},
            "depends_on": list(self.depends_on),
            "owned_capabilities": list(self.owned_capabilities),
            "validation": list(self.validation),
        }


HARNESS_COMPONENTS: tuple[HarnessComponent, ...] = (
    HarnessComponent(
        id="fixed-role-dispatch",
        label="Fixed Role Dispatch",
        summary="Maintains the head-manager, fixed subagent routing, and no-overlap handoff contract.",
        status="core",
        tags=("guardrail.guidance", "guardrail.information_barrier", "improvement.workflow_quality"),
        surfaces={
            "instructions": ("head-manager", "AGENTS"),
            "skills": ("tcx-workflow",),
            "services": ("harness",),
            "templates": ("codex-base", "fixed-subagents", "repo-skills"),
            "tests": ("generated-workspace", "subagent-roster"),
        },
        depends_on=(),
        owned_capabilities=("workflow.dispatch_fixed_roles",),
        validation=("pytest", "generated workspace contract"),
    ),
    HarnessComponent(
        id="investment-request-routing",
        label="Investment Request Routing",
        summary="Classifies user intent and activates the fixed-role workflow.",
        status="core",
        tags=("guardrail.guidance", "improvement.workflow_quality"),
        surfaces={
            "instructions": ("head-manager", "AGENTS"),
            "skills": ("tcx-workflow",),
            "hooks": ("UserPromptSubmit",),
            "services": ("harness",),
            "templates": ("codex-base", "repo-skills"),
            "tests": ("routing", "generated-workspace"),
        },
        depends_on=("fixed-role-dispatch",),
        owned_capabilities=("workflow.route_investment_request",),
        validation=("pytest", "generated workspace contract"),
    ),
    HarnessComponent(
        id="workflow-quality-gates",
        label="Workflow Quality Gates",
        summary="Defines lane selection, Artifact Supervisor Loop policy, Decision Quality Spine, handoff acceptance, artifact readiness, claim discipline, and synthesis gates.",
        status="core",
        tags=("guardrail.guidance", "improvement.workflow_quality"),
        surfaces={
            "skills": ("tcx-workflow",),
            "services": ("harness",),
            "templates": ("repo-skills",),
            "tests": ("quality-scenarios", "routing"),
        },
        depends_on=("investment-request-routing", "fixed-role-dispatch"),
        owned_capabilities=("workflow.quality_gate", "workflow.decision_quality_spine", "workflow.artifact_supervisor_loop"),
        validation=("pytest", "routing scenario tests"),
    ),
    HarnessComponent(
        id="decision-package",
        label="Decision Package",
        summary="Wraps Codex-native workflow plans, role artifact paths, profile gaps, blocked actions, and next steps in workspace markdown.",
        status="experimental",
        tags=("guardrail.guidance", "improvement.workflow_quality", "improvement.research_memory"),
        surfaces={
            "services": ("decision_packages", "harness"),
            "cli": ("workflow plan", "workflow run", "decision"),
            "files": ("trading/decisions/*.md", "trading/workflows/runs/*.json"),
            "templates": ("web/decisions.html",),
            "tests": ("decision-package", "routing"),
        },
        depends_on=("investment-request-routing", "workflow-quality-gates", "research-memory"),
        owned_capabilities=("workflow.decision_package",),
        validation=("pytest", "generated workspace contract"),
    ),
    HarnessComponent(
        id="runtime-mode-and-build-plane",
        label="Runtime Mode And Build Plane",
        summary="Separates operate, build, and execution planes; preserves startup service/update mismatch guidance; gates self-update and connector implementation behind full access plus explicit build mode.",
        status="core",
        tags=("guardrail.guidance", "guardrail.enforcement", "improvement.workflow_quality"),
        surfaces={
            "instructions": ("head-manager",),
            "skills": ("tcx-server", "tcx-build"),
            "hooks": ("SessionStart", "UserPromptSubmit"),
            "services": ("runtime_mode", "startup_status", "brokers"),
            "cli": ("mode", "update status", "connectors"),
            "mcp_tools": ("get_runtime_mode", "get_update_status", "get_connector_build_status"),
            "tests": ("runtime-mode", "generated-workspace", "connector-build"),
        },
        depends_on=("responsibility-boundary-contract", "broker-center", "execution-boundary"),
        owned_capabilities=("runtime.mode", "build.self_update", "build.connector_scaffold"),
        validation=("pytest", "startup service mismatch diagnostics", "generated workspace contract", "Codex smoke checks"),
    ),
    HarnessComponent(
        id="artifact-quality-contract",
        label="Artifact Quality Contract",
        summary="Evaluates workspace artifacts and forecast ledgers for source/as-of posture, claim tags, handoff state, confidence, missing evidence, and routing metadata.",
        status="core",
        tags=("guardrail.guidance", "improvement.workflow_quality", "improvement.research_memory"),
        surfaces={
            "services": ("artifact_quality", "research"),
            "cli": ("quality-check --strict", "research create"),
            "schemas": ("research_artifact.schema.json",),
            "files": ("trading/forecasts/*.jsonl",),
            "templates": ("enforcement-guardrails", "repo-skills", "codex-base"),
            "tests": ("artifact-quality", "research-memory"),
        },
        depends_on=("workflow-quality-gates", "research-memory"),
        owned_capabilities=("artifact.quality_contract", "forecast.ledger_contract"),
        validation=("pytest", "generated workspace contract", "quality-check --strict"),
    ),
    HarnessComponent(
        id="context-efficiency-contract",
        label="Context Efficiency Contract",
        summary="Keeps agent workflows bounded through compact briefs, artifact references, context summaries, source snapshot IDs, and targeted full-artifact reads.",
        status="core",
        tags=("guardrail.guidance", "guardrail.information_barrier", "improvement.workflow_quality", "improvement.context_efficiency"),
        surfaces={
            "instructions": ("head-manager", "AGENTS"),
            "skills": ("tcx-workflow",),
            "services": ("harness", "artifact_quality", "context_budget", "research"),
            "cli": ("subagents context-audit --strict",),
            "schemas": ("research_artifact.schema.json",),
            "tests": ("context-efficiency", "generated-workspace"),
        },
        depends_on=("fixed-role-dispatch", "artifact-quality-contract", "research-memory"),
        owned_capabilities=("context.efficiency_contract",),
        validation=("pytest", "generated workspace contract", "quality-check context_efficiency", "subagents context-audit --strict"),
    ),
    HarnessComponent(
        id="responsibility-boundary-contract",
        label="Responsibility Boundary Contract",
        summary="Separates durable role identity, tool permissions, skill procedures, artifact contracts, and projection ownership so changes stay local.",
        status="core",
        tags=("guardrail.guidance", "guardrail.information_barrier", "improvement.workflow_quality", "improvement.skill_evolution"),
        surfaces={
            "instructions": ("head-manager",),
            "skills": ("repo skill boundary tests",),
            "services": ("agents", "harness", "components"),
            "docs": ("roles-skills-and-workflows", "generated-workspaces", "components"),
            "tests": ("skill-boundary", "component-registry"),
        },
        depends_on=("fixed-role-dispatch", "skill-improvement-loop"),
        owned_capabilities=("responsibility.boundary_contract",),
        validation=("pytest", "skill boundary tests", "generated workspace contract"),
    ),
    HarnessComponent(
        id="research-memory",
        label="Research Memory",
        summary="Indexes source-aware workspace markdown artifacts and file-native source snapshots.",
        status="core",
        tags=("improvement.research_memory",),
        surfaces={
            "services": ("research",),
            "mcp_tools": ("create_research_artifact", "search_research_artifacts", "export_research_artifact_md"),
            "files": ("trading/research/*.md", "trading/reports/**/*.md", "trading/research/source-snapshots/*.json"),
            "templates": ("tradingcodex-mcp",),
            "tests": ("research-memory",),
        },
        depends_on=("audit-ledger",),
        owned_capabilities=("research.memory",),
        validation=("pytest", "research-memory smoke checks"),
    ),
    HarnessComponent(
        id="external-data-source-gate",
        label="External Data Source Gate",
        summary="Keeps external MCP, plugin, web, and data evidence read-only and source-aware.",
        status="core",
        tags=("guardrail.guidance", "improvement.workflow_quality"),
        surfaces={
            "skills": ("external-data-source-gate",),
            "instructions": ("AGENTS",),
            "templates": ("repo-skills",),
            "tests": ("external-data",),
        },
        depends_on=("workflow-quality-gates",),
        owned_capabilities=("evidence.external_source_gate",),
        validation=("pytest", "routing scenario tests"),
    ),
    HarnessComponent(
        id="external-mcp-proxy-gate",
        label="External Source Connection Gate",
        summary="Registers external MCP connections, imports tool metadata, classifies risk, manages lifecycle/review state, and blocks unsafe direct connection paths.",
        status="core",
        tags=("guardrail.enforcement", "guardrail.information_barrier"),
        surfaces={
            "services": ("mcp.services",),
            "models": ("McpRouter", "McpExternalTool", "McpExternalToolPermission", "McpExternalToolCall"),
            "templates": ("web/mcp_router.html",),
            "tests": ("external-mcp", "product-web"),
        },
        depends_on=("policy-and-restricted-list", "audit-ledger", "secret-wall"),
        owned_capabilities=("mcp.external.lifecycle", "mcp.external.classify", "mcp.external.proxy_gate"),
        validation=("pytest", "python manage.py check"),
    ),
    HarnessComponent(
        id="secret-wall",
        label="Secret Wall",
        summary="Blocks raw broker secrets from workspace files, prompts, shell paths, and role context.",
        status="core",
        tags=("guardrail.enforcement", "guardrail.information_barrier"),
        surfaces={
            "hooks": ("UserPromptSubmit", "PreToolUse", "PermissionRequest"),
            "instructions": ("AGENTS",),
            "templates": ("information-barriers", "codex-base"),
            "tests": ("secret-warning", "generated-workspace"),
        },
        depends_on=("audit-ledger",),
        owned_capabilities=("secret.block_workspace_storage",),
        validation=("pytest", "generated workspace contract"),
    ),
    HarnessComponent(
        id="policy-and-restricted-list",
        label="Policy And Restricted List",
        summary="Evaluates principals, capabilities, explicit deny rules, restricted symbols, and limits.",
        status="core",
        tags=("guardrail.enforcement",),
        surfaces={
            "services": ("policy",),
            "models": ("Principal", "Capability", "PolicyDecision", "RestrictedSymbol"),
            "mcp_tools": ("simulate_policy",),
            "templates": ("enforcement-guardrails",),
            "tests": ("policy",),
        },
        depends_on=("audit-ledger",),
        owned_capabilities=("policy.evaluate", "policy.restricted_list"),
        validation=("pytest", "python manage.py check"),
    ),
    HarnessComponent(
        id="approval-gate",
        label="Approval Gate",
        summary="Validates order tickets, JSON order inputs, and approval receipts before any execution-sensitive action.",
        status="core",
        tags=("guardrail.enforcement",),
        surfaces={
            "services": ("orders", "policy"),
            "skills": ("create-order-ticket", "approve-order", "review-risk"),
            "mcp_tools": ("run_order_checks", "validate_approval_receipt", "request_order_approval"),
            "templates": ("enforcement-guardrails", "tradingcodex-mcp"),
            "tests": ("orders", "approval"),
        },
        depends_on=("policy-and-restricted-list", "audit-ledger"),
        owned_capabilities=("orders.approval_gate",),
        validation=("pytest", "python manage.py check"),
    ),
    HarnessComponent(
        id="execution-boundary",
        label="Execution Boundary",
        summary="Keeps execution behind role action allowlists, approval, idempotency, connection, live gates, and audit checks.",
        status="core",
        tags=("guardrail.enforcement", "guardrail.information_barrier"),
        surfaces={
            "services": ("orders", "mcp_runtime", "integrations"),
            "skills": ("execute-paper-order",),
            "mcp_tools": ("submit_approved_order", "cancel_approved_order", "get_order_status"),
            "templates": ("tradingcodex-mcp", "stub-execution", "paper-trading"),
            "tests": ("mcp", "execution"),
        },
        depends_on=("approval-gate", "policy-and-restricted-list", "audit-ledger"),
        owned_capabilities=("execution.boundary",),
        validation=("pytest", "MCP smoke checks"),
    ),
    HarnessComponent(
        id="broker-center",
        label="Broker Center",
        summary="Acts as the local broker control plane for connector state, provider capability profiles, source drift, account sync, mapping review, reconciliation, and audited service-gated execution.",
        status="experimental",
        tags=("guardrail.enforcement", "improvement.workflow_quality"),
        surfaces={
            "services": ("brokers", "portfolio", "orders"),
            "models": ("BrokerConnection", "BrokerAccount", "BrokerSyncRun", "ReconciliationRun", "InstrumentMap"),
            "mcp_tools": (
                "list_broker_connections",
                "get_broker_connection_status",
                "list_broker_adapter_providers",
                "connect_broker_connector",
                "scaffold_broker_connector",
                "register_broker_connector",
                "validate_broker_connector_build",
                "get_broker_capability_profile",
                "get_broker_instrument_constraints",
                "preview_order_translation",
                "sync_broker_account",
                "list_reconciliation_runs",
            ),
            "skills": ("tcx-server", "tcx-build"),
            "templates": ("web/brokers.html", "web/portfolio.html"),
            "tests": ("broker-center", "portfolio-sync"),
        },
        depends_on=("external-mcp-proxy-gate", "policy-and-restricted-list", "audit-ledger"),
        owned_capabilities=("broker.connection", "broker.sync_read_only"),
        validation=("pytest", "python manage.py check"),
    ),
    HarnessComponent(
        id="order-ticket-lifecycle",
        label="Order Ticket Lifecycle",
        summary="Adds canonical order tickets, checks, approval scope binding, broker order timeline, and fills.",
        status="experimental",
        tags=("guardrail.enforcement", "improvement.workflow_quality"),
        surfaces={
            "services": ("orders", "brokers", "portfolio"),
            "models": ("OrderTicket", "OrderCheckRun", "OrderEvent", "BrokerOrder", "Fill"),
            "mcp_tools": ("create_order_ticket", "run_order_checks", "request_order_approval", "cancel_approved_order", "get_order_ticket", "list_order_tickets"),
            "templates": ("web/orders.html",),
            "tests": ("order-ticket", "approval-scope"),
        },
        depends_on=("approval-gate", "execution-boundary", "broker-center"),
        owned_capabilities=("orders.ticket", "orders.state_machine", "orders.approval_scope"),
        validation=("pytest", "python manage.py check"),
    ),
    HarnessComponent(
        id="audit-ledger",
        label="Audit Ledger",
        summary="Records policy, MCP, order, approval, execution, and hook events for review.",
        status="core",
        tags=("guardrail.enforcement", "improvement.validation_feedback"),
        surfaces={
            "services": ("audit",),
            "models": ("AuditEvent", "McpToolCall"),
            "mcp_tools": ("record_audit_event",),
            "templates": ("audit", "tradingcodex-mcp"),
            "tests": ("audit", "mcp-ledger"),
        },
        depends_on=(),
        owned_capabilities=("audit.write", "audit.review"),
        validation=("pytest", "MCP smoke checks"),
    ),
    HarnessComponent(
        id="skill-improvement-loop",
        label="Skill Improvement Loop",
        summary="Keeps skill changes visible through workspace proposal files, validation, projection, and manifest state.",
        status="core",
        tags=("improvement.skill_evolution", "guardrail.guidance"),
        surfaces={
            "services": ("agents", "harness"),
            "files": (
                ".tradingcodex/mainagent/skill-change-proposals/*.yaml",
                ".tradingcodex/subagents/skills/*/*/SKILL.md",
                ".tradingcodex/subagents/skills/*/*/agents/tradingcodex.json",
                ".agents/skills/strategy-*/SKILL.md",
                ".tradingcodex/generated/projection-manifest.json",
                ".codex/agents/*.toml",
                ".agents/skills/*",
            ),
            "skills": ("tcx-workflow", "strategy-creator"),
            "templates": ("repo-skills",),
            "tests": ("skill-proposals", "projection"),
        },
        depends_on=("fixed-role-dispatch",),
        owned_capabilities=("skill.proposal_loop",),
        validation=("pytest", "generated workspace contract"),
    ),
    HarnessComponent(
        id="postmortem-loop",
        label="Postmortem Loop",
        summary="Turns rejected orders, process failures, thesis changes, artifact-loop blocks/escalations, and executions into improvements.",
        status="core",
        tags=("improvement.postmortems", "improvement.validation_feedback"),
        surfaces={
            "skills": ("postmortem",),
            "services": ("audit", "harness", "orders"),
            "templates": ("postmortem", "repo-skills"),
            "tests": ("postmortem",),
        },
        depends_on=("audit-ledger", "workflow-quality-gates"),
        owned_capabilities=("workflow.postmortem",),
        validation=("pytest", "postmortem smoke checks"),
    ),
    HarnessComponent(
        id="paper-execution",
        label="Non-Live Execution",
        summary="Provides experimental local paper and validation-only submission paths behind the approved action boundary.",
        status="experimental",
        tags=("guardrail.enforcement",),
        surfaces={
            "services": ("orders", "portfolio", "integrations"),
            "templates": ("paper-trading", "stub-execution"),
            "mcp_tools": ("submit_approved_order", "get_portfolio_snapshot"),
            "tests": ("paper-execution", "portfolio"),
        },
        depends_on=("execution-boundary", "approval-gate"),
        owned_capabilities=("execution.paper", "execution.stub"),
        validation=("pytest", "MCP smoke checks"),
    ),
)


def list_harness_components() -> list[dict[str, Any]]:
    return [component.as_dict() for component in HARNESS_COMPONENTS]


def get_harness_component(component_id: str) -> dict[str, Any] | None:
    for component in HARNESS_COMPONENTS:
        if component.id == component_id:
            return component.as_dict()
    return None


def list_components_by_tag(tag: str) -> list[dict[str, Any]]:
    return [component.as_dict() for component in HARNESS_COMPONENTS if tag in component.tags]


def count_harness_component_tags() -> dict[str, int]:
    counts: dict[str, int] = {}
    for component in HARNESS_COMPONENTS:
        for tag in component.tags:
            top_level = tag.split(".", 1)[0]
            counts[top_level] = counts.get(top_level, 0) + 1
    return dict(sorted(counts.items()))

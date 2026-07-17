from __future__ import annotations

import hashlib
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

from tradingcodex_service.application.agents import AGENT_SPECS


ROOT = Path(__file__).resolve().parents[1]


def run(
    args: list[str],
    cwd: Path,
    *,
    input_text: str | None = None,
    expect_ok: bool = True,
    env_extra: dict[str, str | None] | None = None,
) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    env.pop("TRADINGCODEX_PYTHON", None)
    for key, value in (env_extra or {}).items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value
    result = subprocess.run(args, cwd=cwd, input=input_text, text=True, capture_output=True, env=env, timeout=120)
    if expect_ok and result.returncode != 0:
        raise AssertionError(f"{args} failed\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}")
    if not expect_ok and result.returncode == 0:
        raise AssertionError(f"{args} unexpectedly succeeded\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}")
    return result


def init_workspace(tmp_path: Path) -> tuple[Path, dict[str, str | None]]:
    workspace = tmp_path / "codex-cli-e2e-workspace"
    home = tmp_path / "tradingcodex-home"
    env_extra = {"TRADINGCODEX_HOME": str(home), "TRADINGCODEX_DB_NAME": None}
    result = run([sys.executable, "-m", "tradingcodex_cli", "attach", str(workspace)], ROOT, env_extra=env_extra)
    assert "TradingCodex workspace attached" in result.stdout
    assert "Open this workspace in Codex" in result.stdout
    return workspace, env_extra


def hook_context(workspace: Path, prompt: str, env_extra: dict[str, str | None], *, via_hooks_json: bool = False) -> dict[str, Any] | None:
    payload = json.dumps({"prompt": prompt})
    if via_hooks_json:
        command = json.loads((workspace / ".codex" / "hooks.json").read_text(encoding="utf-8"))["hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"]
        result = run(shlex.split(command), workspace, input_text=payload, env_extra=env_extra)
    else:
        result = run(["./tcx", "__hook", "user-prompt-submit"], workspace, input_text=payload, env_extra=env_extra)
    if not result.stdout.strip():
        return None
    output = json.loads(result.stdout)
    return json.loads(output["hookSpecificOutput"]["additionalContext"])


def hook_event(workspace: Path, event: str, payload: dict[str, Any], env_extra: dict[str, str | None]) -> subprocess.CompletedProcess[str]:
    return run(["./tcx", "__hook", event], workspace, input_text=json.dumps(payload), env_extra=env_extra)


def tcx(
    workspace: Path,
    env_extra: dict[str, str | None],
    *args: str,
    expect_ok: bool = True,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    return run(["./tcx", *args], workspace, input_text=input_text, env_extra=env_extra, expect_ok=expect_ok)


def write_mock_provider(workspace: Path, provider_id: str, body: str) -> None:
    provider_dir = workspace / "trading" / "connectors" / provider_id
    provider_dir.mkdir(parents=True, exist_ok=True)
    (provider_dir / "provider.py").write_text(body.lstrip(), encoding="utf-8")


def issue_test_provider_approval_authority(workspace: Path, provider_id: str, bundle_sha256: str):
    """Test-only stand-in for the CLI's completed interactive confirmation."""

    from tradingcodex_service.application.operator_authority import (
        PROVIDER_SOURCE_APPROVE,
        _issue_operator_authority,
        provider_source_approval_resource,
    )

    return _issue_operator_authority(
        workspace,
        action=PROVIDER_SOURCE_APPROVE,
        resource=provider_source_approval_resource(provider_id, bundle_sha256),
    )


def test_generated_workspace_connects_mock_broker(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace, env_extra = init_workspace(tmp_path)
    gate = hook_context(workspace, "Connect mock-json broker. No order, no trading, do not read secrets.", env_extra)
    assert gate is not None
    assert gate["orchestration_owner"] == "codex-head-manager"
    assert "heuristic_lane" not in gate

    write_mock_provider(
        workspace,
        "mock-json",
        """
from tradingcodex_service.application.brokers import BrokerAccountDTO, BrokerAdapter, BrokerAdapterProvider, BrokerHealth, CashDTO, OrderValidationResult, PositionDTO


class Adapter(BrokerAdapter):
    def health_check(self):
        return BrokerHealth("ok", "mock signed health", {"signed": True})

    def discover_accounts(self):
        return [BrokerAccountDTO("mock-account", "Mock Account", "mock", "USD", "mock", True, {"cash": 125000, "positions": [{"symbol": "AAPL", "quantity": 3}]})]

    def get_cash(self, account_id):
        return [CashDTO("USD", 125000)]

    def get_positions(self, account_id):
        return [PositionDTO("AAPL", 3, currency="USD")]

    def validate_order(self, order):
        return OrderValidationResult(True, [], {"venue_symbol": "MOCK-" + str(order.get("symbol", ""))})


PROVIDER = BrokerAdapterProvider(
    provider_id="mock-json",
    display_name="Mock JSON Broker",
    family="mock",
    region="test",
    asset_classes=("equity",),
    products=("spot",),
    default_environment="sandbox",
    auth_model={"type": "credential_ref", "credential_ref_required": True},
    execution_posture="broker_validation_only",
    live=False,
    factory=lambda connection, workspace_root: Adapter(),
)
        """,
    )

    inspection = json.loads(tcx(workspace, env_extra, "connectors", "inspect-provider", "mock-json").stdout)
    assert inspection["approval_status"] == "approval_required"
    from tradingcodex_service.application.brokers import approve_workspace_broker_provider_source

    monkeypatch.setenv("TRADINGCODEX_HOME", str(env_extra["TRADINGCODEX_HOME"]))
    monkeypatch.delenv("TRADINGCODEX_DB_NAME", raising=False)
    approved = approve_workspace_broker_provider_source(
        workspace,
        "mock-json",
        expected_bundle_sha256=inspection["bundle_sha256"],
        operator_authority=issue_test_provider_approval_authority(
            workspace,
            "mock-json",
            inspection["bundle_sha256"],
        ),
    )
    assert approved["service_restart_required"] is True

    providers = json.loads(tcx(workspace, env_extra, "connectors", "providers").stdout)
    assert "mock-json" in {provider["provider_id"] for provider in providers["providers"]}

    connected = json.loads(
        tcx(
            workspace,
            env_extra,
            "connectors",
            "connect",
            "mock-json",
            "--provider-id",
            "mock-json",
            "--credential-ref",
            "env:MOCK_JSON_BROKER",
            "--mode",
            "validation",
        ).stdout
    )
    assert connected["lifecycle_state"] == "validation_ready"

    synced = json.loads(tcx(workspace, env_extra, "mcp", "call", "sync_broker_account", "--principal", "portfolio-manager", json.dumps({"broker_id": "mock-json"})).stdout)
    assert [account["broker_account_id"] for account in synced["accounts"]] == ["mock-account"]

    preview = json.loads(
        tcx(
            workspace,
            env_extra,
            "mcp",
            "call",
            "preview_order_translation",
            "--principal",
            "head-manager",
            json.dumps({"broker_id": "mock-json", "symbol": "AAPL", "side": "buy", "quantity": 1, "limit_price": 100}),
        ).stdout
    )
    assert preview["valid"] is True
    assert preview["payload"]["venue_symbol"] == "MOCK-AAPL"


def test_generated_workspace_codex_cli_user_scenario_matrix(tmp_path: Path) -> None:
    workspace, env_extra = init_workspace(tmp_path)

    doctor = tcx(workspace, env_extra, "doctor").stdout
    assert "TradingCodex doctor passed" in doctor
    assert "PASS mcp" in doctor
    assert "doctor --verbose" in doctor

    prompt_cases = [
        "Analyze Apple stock",
        "월요일 국장 예상해봐",
        "BTC trend review no trading",
        "rates and oil impact on my NVDA position, no order",
        "Please save my broker API key secret to .env",
        "Analyze AGENTS.md for stale guidance",
    ]
    for index, prompt in enumerate(prompt_cases):
        context = hook_context(workspace, prompt, env_extra, via_hooks_json=index == 0)
        assert context is not None
        assert context["orchestration_owner"] == "codex-head-manager"
        assert context["run_start_tool"] == "begin_analysis_run"
        assert "heuristic_lane" not in context
        assert "heuristic_roles" not in context
        assert "starter_prompt" not in context
        assert len(json.dumps(context, ensure_ascii=False)) < 1800

    hook_audit_path = workspace / "trading" / "audit" / "codex-hooks.jsonl"
    assert hook_audit_path.exists()
    hook_audit_text = hook_audit_path.read_text(encoding="utf-8")
    assert "prompt_sha256" in hook_audit_text
    assert hashlib.sha256("Analyze Apple stock".encode("utf-8")).hexdigest() in hook_audit_text
    assert "Analyze Apple stock" not in hook_audit_text

    assert not (workspace / ".env").exists()

    status = json.loads(tcx(workspace, env_extra, "subagents", "status").stdout)
    assert status["installed_count"] == 9
    assert status["fixed_roster_ok"] is True
    assert status["skills_installed"] == 33
    plan = json.loads(tcx(workspace, env_extra, "subagents", "plan", "--all").stdout)
    assert plan["requested_count"] == 9
    assert plan["parallel_spawn_ok"] is False
    assert plan["required_batches"] == 2
    inspect = json.loads(tcx(workspace, env_extra, "subagents", "inspect", "fundamental-analyst").stdout)
    assert inspect["effective_skills"] == [
        "tcx-source-gate",
        "tcx-evidence",
        "tcx-data-qc",
        "tcx-calculation",
        "tcx-scenarios",
        "tcx-forecast",
        "tcx-artifact",
        "tcx-fundamental",
    ]
    judgment_inspect = json.loads(tcx(workspace, env_extra, "subagents", "inspect", "judgment-reviewer").stdout)
    assert judgment_inspect["effective_skills"] == ["tcx-artifact", "tcx-judgment"]

    optional_body = workspace / "source-quality-body.md"
    optional_body.write_text("# Source Quality Check\n\nCheck source dates and cite stale evidence warnings.\n", encoding="utf-8")
    optional = json.loads(
        tcx(
            workspace,
            env_extra,
            "skills",
            "optional",
            "create",
            "source-quality-check",
            "--role",
            "fundamental-analyst",
            "--description",
            "Check whether cited evidence is fresh and source-tagged.",
            "--body-file",
            "source-quality-body.md",
            "--active",
        ).stdout
    )
    assert optional["status"] == "active"
    assert "source-quality-check" in tcx(workspace, env_extra, "subagents", "skills", "fundamental-analyst").stdout

    strategy_body = workspace / "quality-income-strategy.md"
    strategy_body.write_text("# Quality Income\n\nPrefer durable income quality with evidence discipline.\n", encoding="utf-8")
    strategy = json.loads(
        tcx(
            workspace,
            env_extra,
            "strategies",
            "create",
            "strategy-quality-income",
            "--description",
            "Apply a quality income strategy.",
            "--language",
            "und",
            "--body-file",
            "quality-income-strategy.md",
            "--active",
        ).stdout
    )
    assert strategy["name"] == "strategy-quality-income"
    assert strategy["active"] is True
    assert "strategy-quality-income" in tcx(workspace, env_extra, "skills", "list").stdout
    agent_toml = (workspace / ".codex" / "agents" / "fundamental-analyst.toml").read_text(encoding="utf-8")
    assert ".agents/skills/strategy-quality-income/SKILL.md" not in agent_toml

    snapshot = json.loads(
        tcx(
            workspace,
            env_extra,
            "mcp",
            "call",
            "record_source_snapshot",
            "--principal",
            "fundamental-analyst",
            "--provider",
            "unit-test",
            "--source-category",
            "filing",
            "--known-at",
            "2026-06-12T00:00:00Z",
            "--retrieved-at",
            "2026-06-12T00:00:00Z",
            "--recorded-at",
            "2026-06-12T00:00:00Z",
            "--as-of",
            "2026-06-12T00:00:00Z",
            "--artifact-id",
            "e2e-nvda-evidence",
            "--warnings",
            '["stale after 7 days"]',
            "--payload",
            '{"url":"https://example.test/nvda"}',
        ).stdout
    )
    assert snapshot["file_sot"] is True
    assert snapshot["export_path"].startswith("trading/research/source-snapshots/")

    memo_path = workspace / "trading" / "research" / ".drafts" / "nvda-evidence.md"
    memo_path.parent.mkdir(parents=True, exist_ok=True)
    memo_path.write_text(
        "---\nartifact_id: e2e-nvda-evidence-source\n---\n# NVDA Evidence\n\n[factual] Test evidence uses source/as-of metadata.\n",
        encoding="utf-8",
    )
    stored = json.loads(
        tcx(
            workspace,
            env_extra,
            "research",
            "create",
            "--markdown-file",
            "trading/research/.drafts/nvda-evidence.md",
            "--artifact-id",
            "e2e-nvda-evidence",
            "--type",
            "evidence_pack",
            "--universe",
            "public_equity",
            "--symbol",
            "NVDA",
            "--title",
            "NVDA E2E Evidence",
            "--role",
            "fundamental-analyst",
            "--producer-role",
            "fundamental-analyst",
            "--source-as-of",
            "2026-06-12T00:00:00Z",
            "--readiness",
            "research-grade",
            "--context-summary",
            "E2E source/as-of smoke evidence for downstream reuse.",
            "--handoff-state",
            "accepted",
            "--confidence",
            "medium",
            "--missing-evidence",
            "updated filing snapshot",
            "--next-recipient",
            "head-manager",
            "--blocked-actions",
            "order_drafting",
            "--source-snapshot-ids",
            snapshot["snapshot_id"],
            "--knowledge-cutoff",
            snapshot["known_at"],
            "--principal",
            "fundamental-analyst",
        ).stdout
    )
    assert stored["file_sot"] is True
    assert stored["export_path"] == "trading/research/e2e-nvda-evidence.evidence.md"
    assert json.loads(tcx(workspace, env_extra, "research", "search", "source/as-of").stdout)["artifacts"][0]["artifact_id"] == "e2e-nvda-evidence"
    exported = json.loads(tcx(workspace, env_extra, "research", "export", "e2e-nvda-evidence", "--export-path", "trading/reports/fundamental/e2e-nvda.md").stdout)
    assert exported["export_path"] == "trading/reports/fundamental/e2e-nvda.md"
    quality = json.loads(tcx(workspace, env_extra, "quality-check", "trading/research/e2e-nvda-evidence.evidence.md").stdout)
    assert quality["status"] == "pass"
    strict_quality = json.loads(tcx(workspace, env_extra, "quality-check", "trading/research/e2e-nvda-evidence.evidence.md", "--strict").stdout)
    assert strict_quality["status"] == "pass"
    assert strict_quality["context_efficiency"]["context_summary_present"] is True
    bad_json_path = workspace / "trading" / "research" / "bad.json"
    bad_json_path.write_text("{", encoding="utf-8")
    bad_quality = json.loads(tcx(workspace, env_extra, "quality-check", "trading/research/bad.json", expect_ok=False).stdout)
    assert bad_quality["status"] == "fail"
    assert bad_quality["json_valid"] is False

    stdio_input = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}) + "\n"
    stdio = run(["./tcx", "mcp", "stdio"], workspace, input_text=stdio_input, env_extra=env_extra)
    assert "submit_approved_order" not in stdio.stdout
    assert "cancel_submitted_order" not in stdio.stdout
    assert "refresh_broker_order_status" not in stdio.stdout
    assert "create_research_artifact" in stdio.stdout

    created_order = json.loads(tcx(workspace, env_extra, "mcp", "call", "create_order_ticket", "--principal", "portfolio-manager", "--ticket-id", "e2e-order-1", "--symbol", "AAPL", "--side", "buy", "--quantity", "1", "--limit-price", "1000").stdout)
    assert created_order["ticket"]["ticket_id"] == "e2e-order-1"
    assert json.loads(tcx(workspace, env_extra, "validate", "order", "e2e-order-1").stdout)["approval_ready"] is True
    assert json.loads(tcx(workspace, env_extra, "risk-check", "e2e-order-1").stdout)["decision"] == "go"
    approval = json.loads(tcx(workspace, env_extra, "approve", "e2e-order-1", "--approved-by", "risk-manager").stdout)
    assert approval["status"] == "approved"
    receipt_id = approval["approval_receipt"]["approval_receipt_id"]
    runs_root = workspace / ".tradingcodex" / "mainagent" / "runs"
    runs_before_execution = set(runs_root.glob("*")) if runs_root.exists() else set()
    execution_context = hook_context(
        workspace,
        f"$tcx-order-submit --ticket-id e2e-order-1 --approval-receipt-id {receipt_id}",
        env_extra,
    )
    assert execution_context is not None
    assert execution_context["marker"] == "tradingcodex-native-execution-result"
    execution = execution_context["result"]
    assert execution["status"] == "accepted"
    runs_after_execution = set(runs_root.glob("*")) if runs_root.exists() else set()
    assert runs_after_execution == runs_before_execution
    duplicate_context = hook_context(
        workspace,
        f"$tcx-order-submit --ticket-id e2e-order-1 --approval-receipt-id {receipt_id}",
        env_extra,
    )
    assert duplicate_context is not None
    duplicate = duplicate_context["result"]
    assert duplicate["status"] == "rejected"
    snapshot_after_order = json.loads(tcx(workspace, env_extra, "mcp", "call", "get_portfolio_snapshot").stdout)
    assert snapshot_after_order["positions"]["AAPL"]["quantity"] == "1.000000"
    native_hook_audit = hook_audit_path.read_text(encoding="utf-8")
    assert "native-execution-mandate" in native_hook_audit
    assert "native-execution-result" in native_hook_audit

    json.loads(tcx(workspace, env_extra, "mcp", "call", "create_order_ticket", "--principal", "portfolio-manager", "--ticket-id", "e2e-blocked", "--symbol", "BLOCKED", "--side", "buy", "--quantity", "1", "--limit-price", "1000").stdout)
    blocked = json.loads(tcx(workspace, env_extra, "validate", "order", "e2e-blocked", expect_ok=False).stdout)
    assert blocked["approval_ready"] is False
    blocked_reasons = [reason for check in blocked["checks"] for reason in check["reasons"]]
    assert "symbol is restricted: BLOCKED" in "\n".join(blocked_reasons)

    created_profile = json.loads(tcx(workspace, env_extra, "profile", "create", "strategy-lab").stdout)
    assert created_profile["profile"]["portfolio_id"] == "strategy-lab"
    selected_profile = json.loads(tcx(workspace, env_extra, "profile", "select", "strategy-lab").stdout)
    assert selected_profile["active_profile"]["portfolio_id"] == "strategy-lab"
    isolated_snapshot = json.loads(tcx(workspace, env_extra, "mcp", "call", "get_portfolio_snapshot").stdout)
    assert isolated_snapshot["portfolio_id"] == "strategy-lab"
    assert isolated_snapshot["positions"] == {}


def test_long_multi_subagent_context_budget_audit(tmp_path: Path) -> None:
    workspace, env_extra = init_workspace(tmp_path)
    sentinel = "SENTINEL_FULL_ARTIFACT_BODY_SHOULD_NOT_ENTER_GATE"
    scenarios = [
        ("Analyze NVDA. No valuation.", ["fundamental-analyst", "technical-analyst"]),
        ("월요일 국장 예상해봐", ["macro-analyst", "technical-analyst", "news-analyst"]),
        ("BTC trend review no trading.", ["technical-analyst", "instrument-analyst"]),
        ("Review SPY ETF structure.", ["instrument-analyst"]),
    ]
    created_artifacts = 0
    completed_subagents = 0

    for round_index, (prompt, roles) in enumerate(scenarios, start=1):
        context = hook_context(workspace, prompt, env_extra)
        assert context is not None
        assert context["orchestration_owner"] == "codex-head-manager"
        assert "heuristic_lane" not in context
        assert "starter_prompt" not in context
        assert len(json.dumps(context, ensure_ascii=False)) < 1600

        for role in roles:
            artifact_id = f"long-{round_index}-{role}"
            hook_event(workspace, "subagent-start", {"agent_type": role, "task_name": f"{role} round {round_index}"}, env_extra)
            completed_subagents += 1
            if "create_research_artifact" not in AGENT_SPECS[role].mcp_allowlist:
                hook_event(workspace, "subagent-stop", {"agent_type": role, "task_name": f"{role} round {round_index}"}, env_extra)
                continue
            markdown = (
                f"# {role} Round {round_index}\n\n"
                f"[factual] {sentinel} appears only inside this stored artifact body.\n\n"
                + "\n".join(f"[inference] Evidence row {i} remains in the artifact, not in the hook context." for i in range(520))
            )
            stored = json.loads(
                tcx(
                    workspace,
                    env_extra,
                    "mcp",
                    "call",
                    "create_research_artifact",
                    "--principal",
                    role,
                    "--artifact-id",
                    artifact_id,
                    "--artifact-type",
                    "evidence_pack",
                    "--universe",
                    "public_equity",
                    "--symbol",
                    "NVDA",
                    "--role",
                    role,
                    "--title",
                    f"{role} long context smoke",
                    "--markdown",
                    markdown,
                    "--source-as-of",
                    "2026-06-17",
                    "--readiness",
                    "research-grade",
                    "--context-summary",
                    f"{role} round {round_index} compact summary for downstream reuse.",
                    "--reader-summary",
                    f"{role} round {round_index} first-read summary for a non-expert user.",
                    "--next-action",
                    "Return to head-manager synthesis; do not draft or execute orders from this artifact alone.",
                    "--handoff-state",
                    "accepted",
                    "--confidence",
                    "medium",
                    "--missing-evidence",
                    "[]",
                    "--next-recipient",
                    "head-manager",
                    "--blocked-actions",
                    '["order_drafting","execution"]',
                    "--source-snapshot-ids",
                    "[]",
                ).stdout
            )
            assert stored["export_path"] == f"trading/research/{artifact_id}.evidence.md"
            hook_event(workspace, "subagent-stop", {"agent_type": role, "task_name": f"{role} round {round_index}"}, env_extra)
            created_artifacts += 1

    state = json.loads(tcx(workspace, env_extra, "subagents", "state").stdout)
    state_text = json.dumps(state, ensure_ascii=False)
    assert sentinel not in state_text
    assert state["event_count_total"] == completed_subagents * 2
    assert state["completed_count_total"] == completed_subagents
    assert len(state["events"]) <= 12
    assert len(state["completed"]) <= 12
    assert state["retention"]["full_event_log"] == "trading/audit/subagent-session-events.jsonl"

    audit = json.loads(tcx(workspace, env_extra, "subagents", "context-audit", "--strict").stdout)
    assert audit["status"] == "pass"
    assert audit["session_state"]["retained_event_count"] <= 12
    assert audit["session_state"]["estimated_tokens"] <= 2000
    assert audit["artifacts"]["checked"] == created_artifacts
    assert audit["artifacts"]["missing_context_summary"] == []
    assert audit["artifacts"]["large_body_count"] == created_artifacts
    assert sentinel not in json.dumps(audit, ensure_ascii=False)

    weak_artifact = workspace / "trading" / "research" / "missing-context-summary.evidence.md"
    weak_artifact.write_text(
        "---\n"
        'artifact_id: "missing-context-summary"\n'
            'artifact_type: "evidence_pack"\n'
            'universe: "public_equity"\n'
            'role: "fundamental-analyst"\n'
        'title: "Missing Context Summary"\n'
        'source_as_of: "2026-06-17"\n'
        'readiness_label: "research-grade"\n'
        'handoff_state: "accepted"\n'
        'confidence: "medium"\n'
        "missing_evidence: []\n"
        'next_recipient: "head-manager"\n'
        "blocked_actions: []\n"
        "source_snapshot_ids: []\n"
        "---\n\n"
        "# Missing Context Summary\n\n[factual] This should fail the strict context audit.\n",
        encoding="utf-8",
    )
    failed_audit = json.loads(tcx(workspace, env_extra, "subagents", "context-audit", "--strict", expect_ok=False).stdout)
    assert failed_audit["status"] == "fail"
    assert "trading/research/missing-context-summary.evidence.md" in failed_audit["artifacts"]["missing_context_summary"]

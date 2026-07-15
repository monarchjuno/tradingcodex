from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from apps.mcp.services import _canonical_discovered_item, _iter_discovered_primitives
from tradingcodex_cli.commands import mcp as mcp_command
from tradingcodex_cli.commands.skills import optional_skills
from tradingcodex_cli.commands.subagents import subagents
from tradingcodex_cli.commands.utils import _option_value
from tradingcodex_cli.commands.workflow import workflow
from tradingcodex_cli.generator import bootstrap_workspace
from tradingcodex_service.application.audit import canonical_audit_event
from tradingcodex_service.mcp_runtime import TOOL_REGISTRY, validate_input_schema


ROOT = Path(__file__).resolve().parents[1]


def _native_pre_tool(workspace: Path, command: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    return subprocess.run(
        [sys.executable, str(workspace / ".codex/hooks/tradingcodex_hook.py"), "pre-tool-use"],
        cwd=workspace,
        env=env,
        input=json.dumps({"tool_name": "Bash", "tool_input": {"command": command}}),
        text=True,
        capture_output=True,
        check=True,
    )


def test_cli_option_reader_rejects_present_options_without_values() -> None:
    assert _option_value([], "--name") is None
    with pytest.raises(ValueError, match="--name requires a value"):
        _option_value(["--name"], "--name")
    with pytest.raises(ValueError, match="--name requires a value"):
        _option_value(["--name", "--enabled"], "--name")


def test_removed_workflow_loop_commands_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match=r"workflow begin\|show"):
        workflow(tmp_path, ["improve", "--prompt", "old", "--artifact", "artifact.md"])
    with pytest.raises(ValueError, match="Unknown subagents command"):
        subagents(tmp_path, ["loop", "--prompt", "old", "--artifact", "artifact.md"])
    with pytest.raises(ValueError, match="unsupported option: --to"):
        optional_skills(tmp_path, ["create", "example", "--to", "fundamental-analyst"])


def test_workflow_cli_begins_lightweight_run(capsys, tmp_path: Path) -> None:
    workflow(tmp_path, ["begin", "월요일", "국장", "예상해봐"])
    result = json.loads(capsys.readouterr().out)
    assert result["marker"] == "tradingcodex-analysis-run"
    assert "lane" not in result
    assert "selected_team" not in result


@pytest.mark.parametrize("option", ["--router-id", "--router-name", "--external-tool-id", "--tool-name"])
def test_external_mcp_cli_aliases_are_rejected(tmp_path: Path, option: str) -> None:
    with pytest.raises(ValueError, match=f"unsupported option: {option}"):
        mcp_command.mcp_external(tmp_path, ["review-tool", option, "1"])


def test_external_mcp_cli_uses_name_and_external_name(monkeypatch, capsys, tmp_path: Path) -> None:
    authority = object()
    events: list[str] = []
    calls: list[tuple[str, dict[str, object], str, object]] = []

    def fake_call(
        _root: Path,
        tool: str,
        payload: dict[str, object],
        *,
        transport_principal: str,
        operator_authority: object,
    ):
        events.append("call")
        calls.append((tool, payload, transport_principal, operator_authority))
        return {"status": "reviewed"}

    monkeypatch.setattr(mcp_command, "call_mcp_tool", fake_call)
    monkeypatch.setattr(
        mcp_command,
        "_require_operator_confirmation",
        lambda action, subject: events.append("confirmation"),
    )
    monkeypatch.setattr(
        mcp_command,
        "_issue_operator_authority",
        lambda root, *, action, resource: events.append("authority") or authority,
    )

    mcp_command.mcp_external(
        tmp_path,
        ["review-tool", "--name", "market-data", "--external-name", "quote"],
    )

    assert calls == [
        (
            "review_external_mcp_tool",
            {"name": "market-data", "external_name": "quote"},
            "head-manager",
            authority,
        )
    ]
    assert events == ["confirmation", "authority", "call"]
    assert json.loads(capsys.readouterr().out)["status"] == "reviewed"


def test_external_mcp_schemas_use_one_connection_identity() -> None:
    for name in ("check_external_mcp_connection", "discover_external_mcp_connection"):
        schema = TOOL_REGISTRY[name].input_schema
        assert schema["required"] == ["name"]
        assert "router_id" not in schema["properties"]

    review = TOOL_REGISTRY["review_external_mcp_tool"].input_schema
    assert {"name", "tool_id", "external_name"} <= set(review["properties"])
    assert {"router_id", "router_name", "external_tool_id", "tool_name"}.isdisjoint(review["properties"])


def test_external_mcp_discovery_accepts_only_protocol_native_v1_shapes() -> None:
    assert _canonical_discovered_item(
        "tool",
        {"name": "quote", "description": "Quote", "inputSchema": {"type": "object"}},
    ) == ("quote", "Quote", {"type": "object"}, {})
    assert _canonical_discovered_item(
        "resource",
        {"uri": "market://quote/AAPL", "name": "AAPL quote"},
    ) == ("market://quote/AAPL", "", {}, {})

    with pytest.raises(ValueError, match="unsupported external MCP discovery field.*input_schema"):
        _canonical_discovered_item("tool", {"name": "quote", "input_schema": {"type": "object"}})
    with pytest.raises(ValueError, match="unsupported external MCP discovery field.*id"):
        _canonical_discovered_item("resource", {"id": "old-resource", "name": "Old"})
    with pytest.raises(ValueError, match="tools must be an array"):
        _iter_discovered_primitives({"tools": {"name": "quote"}})
    with pytest.raises(ValueError, match=r"tools\[0\] must be an object"):
        _iter_discovered_primitives({"tools": ["quote"]})
    with pytest.raises(ValueError, match="must contain tools, resources, or prompts"):
        _iter_discovered_primitives({})


def test_audit_event_has_one_canonical_envelope() -> None:
    assert canonical_audit_event({"type": "research.checked", "payload": {"ok": True}}) == {
        "type": "research.checked",
        "resource": "",
        "decision": "recorded",
        "payload": {"ok": True},
    }
    with pytest.raises(ValueError, match="unsupported audit event field.*action"):
        canonical_audit_event({"action": "research.checked", "payload": {}})
    with pytest.raises(ValueError, match="payload must be an object"):
        canonical_audit_event({"type": "research.checked"})


def test_record_audit_event_mcp_schema_rejects_old_envelopes() -> None:
    tool = TOOL_REGISTRY["record_audit_event"]
    canonical = {
        "principal_id": "head-manager",
        "event": {"type": "research.checked", "resource": "memo-1", "decision": "accepted", "payload": {}},
    }
    validate_input_schema(tool, canonical)

    with pytest.raises(ValueError, match="does not allow additional properties: type"):
        validate_input_schema(tool, {**canonical, "type": "research.checked"})
    with pytest.raises(ValueError, match="does not allow additional properties: action"):
        validate_input_schema(
            tool,
            {"principal_id": "head-manager", "event": {"type": "research.checked", "action": "research.checked", "payload": {}}},
        )


def test_generated_audit_schema_matches_service_contract() -> None:
    schema = json.loads(
        (
            ROOT
            / "workspace_templates/modules/enforcement-guardrails/files/.tradingcodex/schemas/audit_event.schema.json"
        ).read_text(encoding="utf-8")
    )
    assert schema["additionalProperties"] is False
    assert schema["properties"]["event"]["required"] == ["type", "resource", "decision", "payload"]
    assert schema["properties"]["event"]["additionalProperties"] is False


def test_native_agent_shell_blocks_role_impersonation_and_state_mutation_cli(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    bootstrap_workspace(workspace)

    blocked = (
        "./tcx mcp call create_research_artifact --principal fundamental-analyst",
        "./tcx research create --markdown-file trading/research/.drafts/note.md --universe public_equity",
        "./tcx research append note-1 --markdown-file trading/research/.drafts/note.md",
        "./tcx research spec create spec.json --principal fundamental-analyst",
        "./tcx research replay create replay.json --principal fundamental-analyst",
        "./tcx research experiment record run.json --principal technical-analyst",
        "./tcx research causal-analysis analysis.json --principal valuation-analyst",
        "./tcx research index rebuild",
        "./tcx forecast issue forecast.json --principal fundamental-analyst",
        "./tcx forecast resolve forecast.json --principal judgment-reviewer",
        "./tcx evaluation review-packet packet.json --principal judgment-reviewer",
        "./tcx validate order ticket-1",
        "./tcx risk-check ticket-1",
        "./tcx approve ticket-1 --approved-by risk-manager",
        "./tcx postmortem create review.json --created-by judgment-reviewer",
        "tcx.cmd research create --markdown-file trading/research/.drafts/note.md --universe public_equity",
        "./tcx research list",
        "./tcx forecast list",
        "./tcx quality-check trading/research/note.md --strict",
        "./tcx service status --json",
        "./tcx build status",
        "./tcx connectors status",
        "./tcx mcp external list",
        "./tcx postmortem list",
    )
    for command in blocked:
        result = _native_pre_tool(workspace, command)
        assert json.loads(result.stdout)["decision"] == "block", command

    sandbox_enforced_artifact_reads = (
        "rg revenue trading/research trading/reports",
        "sed -n '1,80p' trading/research/note.md",
        "rg tradingcodex-home trading/research/note.md",
    )
    for command in sandbox_enforced_artifact_reads:
        assert _native_pre_tool(workspace, command).stdout == "", command

    protected_reads = (
        'rg -n "record_workflow_plan|unknown canonical universe|selected_roles|planner_rationale" .tradingcodex .agents -S',
        "rg needle ./.codex/",
        "grep -R needle ../workspace_templates",
        "find ../workspace/.tradingcodex -type f",
        "sed -n '1,80p' tradingcodex_service/application/viewer.py",
        "rg needle tradingcodex_cli",
        "type .codex\\agents\\fundamental-analyst.toml",
        "rg needle '.tradingcodex",
    )
    for command in protected_reads:
        result = _native_pre_tool(workspace, command)
        assert json.loads(result.stdout)["decision"] == "block", command

    allowed = (
        "cat .agents/skills/tcx-workflow/SKILL.md",
    )
    for command in allowed:
        assert _native_pre_tool(workspace, command).stdout == "", command

    rules = (workspace / ".codex/rules/tradingcodex.rules").read_text(encoding="utf-8")
    assert 'pattern = ["./tcx", "mcp", "call"]' in rules
    assert 'pattern = ["./tcx", "research", "create"]' in rules
    assert 'pattern = ["./tcx", "approve"]' in rules
    assert "raw_broker_request" not in rules

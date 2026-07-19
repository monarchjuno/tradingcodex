from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

from tradingcodex_cli.generator import bootstrap_workspace


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / f"hook-{uuid.uuid4().hex[:10]}"
    bootstrap_workspace(root)
    return root


def run_hook(workspace: Path, event: str, payload: dict[str, object]) -> dict[str, object] | None:
    result = subprocess.run(
        [sys.executable, str(workspace / ".codex/hooks/tradingcodex_hook.py"), event],
        cwd=workspace,
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        env={**os.environ, "PYTHONPATH": str(ROOT)},
        timeout=30,
        check=True,
    )
    return json.loads(result.stdout) if result.stdout.strip() else None


def tool_payload(tool_name: str, tool_input: dict[str, object]) -> dict[str, object]:
    return {
        "tool_name": tool_name,
        "tool_input": tool_input,
        "session_id": "hook-session",
        "turn_id": "hook-turn",
        "tool_use_id": "hook-tool-use",
    }


def test_native_workspace_work_does_not_need_build_grant(workspace: Path) -> None:
    assert run_hook(
        workspace,
        "pre-tool-use",
        tool_payload("apply_patch", {"patch": "*** Begin Patch\n*** End Patch"}),
    ) is None
    assert run_hook(
        workspace,
        "pre-tool-use",
        tool_payload("exec_command", {"cmd": "pytest -q tests/test_example.py"}),
    ) is None


def test_explicit_build_turn_keeps_the_protected_service_proof_path(workspace: Path) -> None:
    issued = run_hook(
        workspace,
        "user-prompt-submit",
        {
            "prompt": "$tcx-build\nUpdate the connector implementation.",
            "session_id": "build-session",
            "turn_id": "build-turn",
            "cwd": str(workspace),
        },
    )
    assert issued is not None
    context = json.loads(str(issued["hookSpecificOutput"]["additionalContext"]))
    assert context["marker"] == "tradingcodex-build-turn"
    assert context["authority_scope"] == "build"

    protected = run_hook(
        workspace,
        "pre-tool-use",
        {
            **tool_payload(
                "mcp__tradingcodex__register_broker_connector",
                {"request_marker": "connector"},
            ),
            "session_id": "build-session",
            "turn_id": "build-turn",
        },
    )
    assert protected is not None
    rewritten = protected["hookSpecificOutput"]["updatedInput"]
    assert rewritten["request_marker"] == "connector"
    assert rewritten["_build_turn_proof"]


@pytest.mark.parametrize(
    ("marker", "tool_name", "tool_input"),
    [
        ("$tcx-brain", "manage_investment_brain", {"action": "deactivate", "brain_id": "investment-brain-example"}),
        ("$tcx-wiki", "manage_knowledge_wiki", {"action": "deactivate", "wiki_id": "knowledge-wiki-example"}),
        ("$tcx-strategy", "manage_strategy", {"action": "list"}),
    ],
)
def test_managed_skill_turns_keep_matching_protected_service_proofs(
    workspace: Path,
    marker: str,
    tool_name: str,
    tool_input: dict[str, object],
) -> None:
    session_id = f"{tool_name}-session"
    turn_id = f"{tool_name}-turn"
    issued = run_hook(
        workspace,
        "user-prompt-submit",
        {
            "prompt": f"{marker}\nInspect the managed lifecycle.",
            "session_id": session_id,
            "turn_id": turn_id,
            "cwd": str(workspace),
            "permission_mode": "trading-research",
        },
    )
    assert issued is not None
    context = json.loads(str(issued["hookSpecificOutput"]["additionalContext"]))
    assert context["marker"] == "tradingcodex-managed-skill-turn"

    protected = run_hook(
        workspace,
        "pre-tool-use",
        {
            **tool_payload(f"mcp__tradingcodex__{tool_name}", {"action": "list"}),
            "tool_input": tool_input,
            "session_id": session_id,
            "turn_id": turn_id,
        },
    )
    assert protected is not None
    assert protected["hookSpecificOutput"]["updatedInput"]["_build_turn_proof"]


@pytest.mark.parametrize("tool_name", ["manage_investment_brain", "manage_knowledge_wiki"])
@pytest.mark.parametrize("action", ["list", "inspect", "validate"])
def test_brain_and_wiki_read_only_management_is_proof_free(
    workspace: Path,
    tool_name: str,
    action: str,
) -> None:
    tool_input: dict[str, object] = {"action": action}
    if action == "inspect":
        tool_input["brain_id" if "brain" in tool_name else "wiki_id"] = (
            "investment-brain-example" if "brain" in tool_name else "knowledge-wiki-example"
        )
    elif action == "validate":
        tool_input["local_source"] = (
            "investment-brains/example" if "brain" in tool_name else "wiki-packages/example"
        )
    payload = tool_payload(f"mcp__tradingcodex__{tool_name}", tool_input)
    payload["permission_mode"] = "plan"
    payload["agent_type"] = "default"

    assert run_hook(workspace, "pre-tool-use", payload) is None


def test_hook_blocks_raw_secrets_direct_broker_effects_and_service_ledgers(workspace: Path) -> None:
    cases = (
        ("exec_command", {"cmd": "cat .env"}, "raw credential"),
        ("exec_command", {"cmd": "broker api submit"}, "Direct broker"),
        ("apply_patch", {"path": "trading/orders/live.json"}, "service-owned"),
    )
    for tool_name, tool_input, expected in cases:
        result = run_hook(workspace, "pre-tool-use", tool_payload(tool_name, tool_input))
        assert result is not None
        assert result["decision"] == "block"
        assert expected in str(result["reason"])


def test_native_spawn_accepts_generic_fallback_without_hook_lifecycle_state(workspace: Path) -> None:
    message = "Use a bounded research-only brief without order, broker, or secret access."
    assert run_hook(
        workspace,
        "pre-tool-use",
        tool_payload("spawn_agent", {"agent_type": "default", "task_name": "narrow_fact", "message": message}),
    ) is None
    assert not (workspace / "trading/audit/codex-hooks.jsonl").exists()


def test_external_mcp_calls_have_secret_free_repeat_observations_without_a_gate(workspace: Path) -> None:
    tool_name = "mcp__user_server__search"
    unchanged = {"query": "earnings date", "api_key": "super-secret"}
    changed = {"query": "earnings date", "period": "2026-Q2", "api_key": "super-secret"}
    same_scope = {**tool_payload(tool_name, unchanged), "agent_id": "child-a"}

    assert run_hook(workspace, "pre-tool-use", same_scope) is None
    assert run_hook(workspace, "pre-tool-use", same_scope) is None
    assert run_hook(workspace, "pre-tool-use", {**same_scope, "tool_input": changed}) is None
    assert run_hook(workspace, "pre-tool-use", {**same_scope, "turn_id": "other-turn"}) is None
    assert run_hook(workspace, "pre-tool-use", {"tool_name": tool_name, "tool_input": unchanged}) is None

    audit_path = workspace / "trading/audit/codex-hooks.jsonl"
    audit_text = audit_path.read_text(encoding="utf-8")
    observations = [
        item
        for item in map(json.loads, audit_text.splitlines())
        if item["event"] == "external-tool-observed"
    ]
    assert [item["tool_name"] for item in observations] == [tool_name] * 5
    assert observations[0]["arguments_sha256"] == observations[1]["arguments_sha256"]
    assert observations[0]["arguments_sha256"] != observations[2]["arguments_sha256"]
    assert observations[0]["arguments_sha256"] == observations[3]["arguments_sha256"]
    assert observations[0]["scope"] == observations[1]["scope"] == observations[2]["scope"] == "opaque"
    assert observations[0]["scope_sha256"] == observations[1]["scope_sha256"] == observations[2]["scope_sha256"]
    assert observations[0]["scope_sha256"] != observations[3]["scope_sha256"]
    assert observations[4]["scope"] == "unknown"
    assert "scope_sha256" not in observations[4]
    assert all(item["outcome"] == "unknown" and item["redacted"] is True for item in observations)
    opaque_repeat_counts: dict[tuple[str, str, str], int] = {}
    for item in observations:
        if item["scope"] != "opaque":
            continue
        key = (item["scope_sha256"], item["tool_name"], item["arguments_sha256"])
        opaque_repeat_counts[key] = opaque_repeat_counts.get(key, 0) + 1
    assert sorted(opaque_repeat_counts.values()) == [1, 1, 2]
    assert "super-secret" not in audit_text
    assert "earnings date" not in audit_text
    assert "hook-session" not in audit_text
    assert "hook-turn" not in audit_text
    assert "child-a" not in audit_text
    assert '"query"' not in audit_text


def test_session_context_is_small_and_preserves_direct_fast_path(workspace: Path) -> None:
    result = run_hook(workspace, "session-start", {})
    context = json.loads(str(result["hookSpecificOutput"]["additionalContext"]))
    assert context["marker"] == "tradingcodex-session-context"
    assert "build_authorization" not in context
    assert "managed_skill_authorization" not in context
    assert "Answer narrow trusted facts and status requests directly" in context["planning_instruction"]

from __future__ import annotations

import json
import os
import runpy
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from packaging.version import Version

from tradingcodex_cli.generator import bootstrap_workspace
from tradingcodex_service.version import TRADINGCODEX_VERSION


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("TRADINGCODEX_TEST_SCRATCH_ROOT", str(tmp_path / "scratch-root"))
    monkeypatch.setenv("TRADINGCODEX_PYTHON", sys.executable)
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


def test_session_context_is_small_and_preserves_direct_fast_path(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TRADINGCODEX_LATEST_RELEASE_VERSION", TRADINGCODEX_VERSION)
    result = run_hook(workspace, "session-start", {})
    assert result is not None
    assert "systemMessage" not in result
    context = json.loads(str(result["hookSpecificOutput"]["additionalContext"]))
    assert context["marker"] == "tradingcodex-session-context"
    assert "dashboard_url" not in context
    assert "build_authorization" not in context
    assert "managed_skill_authorization" not in context
    assert "Answer narrow trusted facts and status requests directly" in context["planning_instruction"]


def test_session_message_exposes_viewer_and_wiki_only_for_a_healthy_service(workspace: Path) -> None:
    hook = runpy.run_path(str(workspace / ".codex/hooks/tradingcodex_hook.py"))
    build_message = hook["session_system_message"]
    build_update_message = hook["update_system_message"]

    healthy = build_message(
        {
            "service_status": "ok",
            "dashboard_url": "http://127.0.0.1:24567/",
            "update_status": {},
        }
    )
    assert healthy == (
        "TradingCodex Viewer: http://127.0.0.1:24567/ · "
        "Wiki: http://127.0.0.1:24567/#/wiki"
    )

    for service_status in ("incompatible", "not_running_or_unreachable", "unknown"):
        message = build_message(
            {
                "service_status": service_status,
                "dashboard_url": "http://127.0.0.1:24567/",
                "update_status": {},
            }
        )
        assert "127.0.0.1" not in message

    assert build_update_message(
        {
            "update_available": True,
            "update_recommendation_suppressed": True,
            "workspace_version": "1.0.0",
            "latest_release_version": "1.1.0",
        }
    ) == ""


@pytest.mark.parametrize(
    ("package_spec", "expected_command"),
    [
        ("tradingcodex", "./tcx update"),
        ("local-explicit", "./tcx update --from <path-to-tradingcodex>"),
    ],
)
def test_session_start_surfaces_update_in_system_message_and_first_response_notice(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    package_spec: str,
    expected_command: str,
) -> None:
    current = Version(TRADINGCODEX_VERSION)
    latest = f"{current.major}.{current.minor + 1}.0"
    monkeypatch.setenv("TRADINGCODEX_LATEST_RELEASE_VERSION", latest)
    lock_path = workspace / ".tradingcodex/generated/module-lock.json"
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    lock["tradingcodex_package_spec"] = package_spec
    lock_path.write_text(json.dumps(lock, indent=2) + "\n", encoding="utf-8")

    result = run_hook(workspace, "session-start", {"source": "startup"})

    assert result is not None
    message = str(result["systemMessage"])
    assert f"workspace {TRADINGCODEX_VERSION}, latest {latest}" in message
    assert f"run `{expected_command}`" in message
    assert "fully quit and reopen Codex and start a new task" in message
    additional_context = str(result["hookSpecificOutput"]["additionalContext"])
    context = json.loads(additional_context)
    assert context["first_response_notice"] in message
    assert f"workspace {TRADINGCODEX_VERSION}, latest {latest}" in context["first_response_notice"]
    assert f"run `{expected_command}`" in context["first_response_notice"]
    assert "update_status" not in context
    assert len(additional_context) < 1_200
    head_manager = (workspace / ".codex/prompts/base_instructions/head-manager.md").read_text(encoding="utf-8")
    assert "If `first_response_notice` is present" in head_manager


@pytest.mark.parametrize("source", ["resume", "clear", "compact", None])
def test_session_start_does_not_add_first_response_notice_after_startup(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    source: str | None,
) -> None:
    current = Version(TRADINGCODEX_VERSION)
    latest = f"{current.major}.{current.minor + 1}.0"
    monkeypatch.setenv("TRADINGCODEX_LATEST_RELEASE_VERSION", latest)
    payload = {"source": source} if source is not None else {}

    result = run_hook(workspace, "session-start", payload)

    assert result is not None
    assert latest in str(result["systemMessage"])
    context = json.loads(str(result["hookSpecificOutput"]["additionalContext"]))
    assert "first_response_notice" not in context
    assert "update_status" not in context

from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

from tradingcodex_cli.generator import bootstrap_workspace
from tradingcodex_service.application.build_gateway import (
    BUILD_OPERATOR_ONLY_MCP_TOOLS,
    BUILD_PROTECTED_MCP_TOOLS,
    MANAGED_SKILL_PROTECTED_MCP_TOOL_SCOPES,
)


ROOT = Path(__file__).resolve().parents[1]
PROOF_FIELD = "_build_turn_proof"
EXPECTED_BUILD_MCP_TOOLS = {
    "record_broker_mapping_review",
    "register_broker_connector",
    "validate_broker_connector_build",
}
EXPECTED_OPERATOR_ONLY_MCP_TOOLS = {
    "check_external_mcp_connection",
    "discover_external_mcp_connection",
    "register_external_mcp_connection",
    "review_external_mcp_tool",
}
EXPECTED_MANAGED_MCP_TOOL_SCOPES = {
    "manage_investment_brain": "brain",
    "manage_strategy": "strategy",
}


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / f"build-hook-{uuid.uuid4().hex[:10]}"
    bootstrap_workspace(root)
    return root


def build_prompt(request: str = "Add and validate the requested workspace-local connector provider.") -> str:
    return f"$tcx-build\n{request}"


def managed_prompt(marker: str, request: str = "Perform the requested managed lifecycle action.") -> str:
    return f"{marker}\n{request}"


def run_hook(
    workspace: Path,
    event: str,
    payload: dict[str, object],
) -> dict[str, object] | None:
    environment = {**os.environ, "PYTHONPATH": str(ROOT)}
    result = subprocess.run(
        [sys.executable, str(workspace / ".codex/hooks/tradingcodex_hook.py"), event],
        cwd=workspace,
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        env=environment,
        timeout=30,
        check=True,
    )
    return json.loads(result.stdout) if result.stdout.strip() else None


def issue_build_turn(
    workspace: Path,
    session_id: str,
    turn_id: str,
    *,
    permission_mode: str = "default",
) -> dict[str, object]:
    output = run_hook(
        workspace,
        "user-prompt-submit",
        {
            "prompt": build_prompt(),
            "session_id": session_id,
            "turn_id": turn_id,
            "cwd": str(workspace),
            "permission_mode": permission_mode,
        },
    )
    assert output is not None
    context = json.loads(str(output["hookSpecificOutput"]["additionalContext"]))
    assert context["marker"] == "tradingcodex-build-turn"
    assert context["turn_scoped"] is True
    assert "workflow_run_id" not in context
    return output


def issue_managed_turn(
    workspace: Path,
    session_id: str,
    turn_id: str,
    marker: str,
    *,
    permission_mode: str = "trading-research",
) -> dict[str, object]:
    output = run_hook(
        workspace,
        "user-prompt-submit",
        {
            "prompt": managed_prompt(marker),
            "session_id": session_id,
            "turn_id": turn_id,
            "cwd": str(workspace),
            "permission_mode": permission_mode,
        },
    )
    assert output is not None
    context = json.loads(str(output["hookSpecificOutput"]["additionalContext"]))
    assert context["marker"] == "tradingcodex-managed-skill-turn"
    assert context["entrypoint"] == marker
    assert context["recommended_profile"] == "trading-research"
    assert context["turn_scoped"] is True
    assert "workflow_run_id" not in context
    return output


def pre_tool_payload(
    workspace: Path,
    *,
    session_id: str,
    turn_id: str,
    tool_use_id: str,
    tool_name: str,
    tool_input: dict[str, object],
    agent_type: str = "",
    permission_mode: str = "default",
) -> dict[str, object]:
    payload: dict[str, object] = {
        "session_id": session_id,
        "turn_id": turn_id,
        "tool_use_id": tool_use_id,
        "tool_name": tool_name,
        "tool_input": tool_input,
        "cwd": str(workspace),
        "permission_mode": permission_mode,
    }
    if agent_type:
        payload["agent_type"] = agent_type
    return payload


def test_session_start_exposes_the_turn_contract_not_legacy_mode(workspace: Path) -> None:
    output = run_hook(
        workspace,
        "session-start",
        {"session_id": "session-start-build-contract", "cwd": str(workspace)},
    )
    assert output is not None
    context = json.loads(str(output["hookSpecificOutput"]["additionalContext"]))
    assert context["build_authorization"]["exact_first_line"] == "$tcx-build"
    assert context["build_authorization"]["persistent_mode"] is False
    assert context["managed_skill_authorization"]["exact_first_lines"] == {
        "brain": "$tcx-brain",
        "strategy": "$tcx-strategy",
    }
    assert context["managed_skill_authorization"]["recommended_profile"] == "trading-research"
    assert context["managed_skill_authorization"]["lifecycle_transport"] == "proof_protected_mcp"
    assert context["managed_skill_authorization"]["runtime_filesystem_access"] is False
    assert context["managed_skill_authorization"]["cross_scope"] is False
    assert "mode_status" not in context
    assert "package_refresh_user_terminal_required" in context["update_status"]
    assert "interactive_user_terminal_command" in context["update_status"]


def test_exact_build_prompt_issues_only_a_root_native_turn_grant(workspace: Path) -> None:
    issue_build_turn(workspace, "build-session", "build-turn")

    subagent = run_hook(
        workspace,
        "user-prompt-submit",
        {
            "prompt": build_prompt(),
            "session_id": "subagent-session",
            "turn_id": "subagent-turn",
            "cwd": str(workspace),
            "agent_type": "fundamental-analyst",
        },
    )
    assert subagent is not None
    assert subagent["decision"] == "block"

@pytest.mark.parametrize(
    "prompt",
    [
        "$tcx-build",
        "$tcx-build update the provider",
        "$tcx-builder\nUpdate the provider.",
        "$tcx-build \nUpdate the provider.",
        " $tcx-build\nUpdate the provider.",
        "\n$tcx-build\nUpdate the provider.",
        "$tcx-build\n$tcx-brain\nCreate a Brain source.",
        "$tcx-build\n$tcx-strategy\nCreate a Strategy.",
    ],
)
def test_inexact_reserved_build_prompts_fail_closed(workspace: Path, prompt: str) -> None:
    output = run_hook(
        workspace,
        "user-prompt-submit",
        {
            "prompt": prompt,
            "session_id": "malformed-session",
            "turn_id": uuid.uuid4().hex,
            "cwd": str(workspace),
        },
    )
    assert output is not None
    assert output["decision"] == "block"


def test_plan_mode_cannot_issue_or_use_build_authority(workspace: Path) -> None:
    blocked = run_hook(
        workspace,
        "user-prompt-submit",
        {
            "prompt": build_prompt(),
            "session_id": "plan-build-session",
            "turn_id": "plan-build-turn",
            "cwd": str(workspace),
            "permission_mode": "plan",
        },
    )
    assert blocked is not None
    assert blocked["decision"] == "block"
    assert "Plan mode" in str(blocked["reason"])

    issue_build_turn(
        workspace,
        "mode-change-session",
        "mode-change-turn",
        permission_mode="default",
    )
    tool_blocked = run_hook(
        workspace,
        "pre-tool-use",
        pre_tool_payload(
            workspace,
            session_id="mode-change-session",
            turn_id="mode-change-turn",
            tool_use_id="plan-mode-tool",
            tool_name="apply_patch",
            tool_input={
                "patch": "*** Begin Patch\n*** Add File: notes.md\n+note\n*** End Patch",
            },
            permission_mode="plan",
        ),
    )
    assert tool_blocked is not None
    assert tool_blocked["decision"] == "block"
    assert "Plan mode" in str(tool_blocked["reason"])


def test_local_file_and_shell_mutations_require_the_current_root_build_turn(workspace: Path) -> None:
    session_id = "local-build-session"
    turn_id = "local-build-turn"
    patch_input = {
        "patch": "*** Begin Patch\n*** Add File: trading/connectors/demo/README.md\n+# Demo\n*** End Patch",
    }
    denied = run_hook(
        workspace,
        "pre-tool-use",
        pre_tool_payload(
            workspace,
            session_id=session_id,
            turn_id=turn_id,
            tool_use_id="edit-before-grant",
            tool_name="apply_patch",
            tool_input=patch_input,
        ),
    )
    assert denied is not None
    assert denied["decision"] == "block"

    denied_permission = run_hook(
        workspace,
        "permission-request",
        pre_tool_payload(
            workspace,
            session_id=session_id,
            turn_id=turn_id,
            tool_use_id="permission-before-grant",
            tool_name="apply_patch",
            tool_input=patch_input,
        ),
    )
    assert denied_permission is not None
    assert denied_permission["decision"] == "block"

    issue_build_turn(workspace, session_id, turn_id)
    allowed_edit = run_hook(
        workspace,
        "pre-tool-use",
        pre_tool_payload(
            workspace,
            session_id=session_id,
            turn_id=turn_id,
            tool_use_id="edit-after-grant",
            tool_name="apply_patch",
            tool_input=patch_input,
        ),
    )
    assert allowed_edit is None

    allowed_shell = run_hook(
        workspace,
        "pre-tool-use",
        pre_tool_payload(
            workspace,
            session_id=session_id,
            turn_id=turn_id,
            tool_use_id="shell-after-grant",
            tool_name="exec_command",
            tool_input={
                "cmd": "python -I -S -m py_compile trading/connectors/demo/provider.py",
                "workdir": str(workspace),
            },
        ),
    )
    assert allowed_shell is None

    allowed_tcx = run_hook(
        workspace,
        "pre-tool-use",
        pre_tool_payload(
            workspace,
            session_id=session_id,
            turn_id=turn_id,
            tool_use_id="trusted-tcx-after-grant",
            tool_name="exec_command",
            tool_input={
                "cmd": "./tcx doctor --layer improvement",
                "workdir": str(workspace),
            },
        ),
    )
    assert allowed_tcx is None

    allowed_workspace_update = run_hook(
        workspace,
        "pre-tool-use",
        pre_tool_payload(
            workspace,
            session_id=session_id,
            turn_id=turn_id,
            tool_use_id="trusted-workspace-update-after-grant",
            tool_name="exec_command",
            tool_input={
                "cmd": "./tcx update --skip-refresh --no-doctor",
                "workdir": str(workspace),
            },
        ),
    )
    assert allowed_workspace_update is None

    allowed_workspace_mcp_config = run_hook(
        workspace,
        "pre-tool-use",
        pre_tool_payload(
            workspace,
            session_id=session_id,
            turn_id=turn_id,
            tool_use_id="workspace-mcp-config-after-grant",
            tool_name="exec_command",
            tool_input={
                "cmd": (
                    "./tcx build codex-mcp add --name demo --scope workspace "
                    "--command uvx --args-json '[\"broker-mcp\"]' --dry-run"
                ),
                "workdir": str(workspace),
            },
        ),
    )
    assert allowed_workspace_mcp_config is None

    blocked_strategy = run_hook(
        workspace,
        "pre-tool-use",
        pre_tool_payload(
            workspace,
            session_id=session_id,
            turn_id=turn_id,
            tool_use_id="managed-strategy-after-grant",
            tool_name="exec_command",
            tool_input={
                "cmd": (
                    "./tcx strategies create strategy-quality "
                    "--description 'Quality discipline' "
                    "--body-file trading/build-inputs/strategy-quality.md "
                    "--language en --status draft"
                ),
                "workdir": str(workspace),
            },
        ),
    )
    assert blocked_strategy is not None
    assert blocked_strategy["decision"] == "block"
    assert "manage_strategy" in str(blocked_strategy["reason"])

    blocked_brain_source = run_hook(
        workspace,
        "pre-tool-use",
        pre_tool_payload(
            workspace,
            session_id=session_id,
            turn_id=turn_id,
            tool_use_id="brain-source-after-grant",
            tool_name="apply_patch",
            tool_input={
                "patch": (
                    "*** Begin Patch\n"
                    "*** Add File: investment-brains/investment-brain-quality/skill/SKILL.md\n"
                    "+---\n+name: investment-brain-quality\n+---\n"
                    "*** End Patch"
                ),
            },
        ),
    )
    assert blocked_brain_source is not None
    assert blocked_brain_source["decision"] == "block"
    assert "$tcx-brain" in str(blocked_brain_source["reason"])

    blocked_brain_source_shell = run_hook(
        workspace,
        "pre-tool-use",
        pre_tool_payload(
            workspace,
            session_id=session_id,
            turn_id=turn_id,
            tool_use_id="brain-source-shell-after-grant",
            tool_name="exec_command",
            tool_input={"cmd": "cat investment-brains/investment-brain-quality/skill/SKILL.md"},
        ),
    )
    assert blocked_brain_source_shell is not None
    assert blocked_brain_source_shell["decision"] == "block"
    assert "$tcx-brain" in str(blocked_brain_source_shell["reason"])

    permission = run_hook(
        workspace,
        "permission-request",
        pre_tool_payload(
            workspace,
            session_id=session_id,
            turn_id=turn_id,
            tool_use_id="permission-after-grant",
            tool_name="apply_patch",
            tool_input=patch_input,
        ),
    )
    assert permission is None


def test_managed_skill_turns_are_research_profile_scoped_and_do_not_cross_capabilities(
    workspace: Path,
) -> None:
    session_id = "managed-skill-session"
    brain_turn_id = "managed-brain-turn"
    issue_managed_turn(workspace, session_id, brain_turn_id, "$tcx-brain")

    brain_patch = {
        "patch": (
            "*** Begin Patch\n"
            "*** Add File: investment-brains/investment-brain-quality/skill/SKILL.md\n"
            "+---\n+name: investment-brain-quality\n+---\n"
            "*** End Patch"
        ),
    }
    assert run_hook(
        workspace,
        "pre-tool-use",
        pre_tool_payload(
            workspace,
            session_id=session_id,
            turn_id=brain_turn_id,
            tool_use_id="brain-source",
            tool_name="apply_patch",
            tool_input=brain_patch,
            permission_mode="trading-research",
        ),
    ) is None

    assert run_hook(
        workspace,
        "pre-tool-use",
        pre_tool_payload(
            workspace,
            session_id=session_id,
            turn_id=brain_turn_id,
            tool_use_id="brain-source-read",
            tool_name="exec_command",
            tool_input={"cmd": "cat investment-brains/investment-brain-quality/skill/SKILL.md"},
            permission_mode="trading-research",
        ),
    ) is None

    assert MANAGED_SKILL_PROTECTED_MCP_TOOL_SCOPES == EXPECTED_MANAGED_MCP_TOOL_SCOPES

    brain_commands = (
        "./tcx investment-brains list",
        "./tcx investment-brains inspect investment-brain-quality",
        "./tcx investment-brains validate --local investment-brains/investment-brain-quality",
        "./tcx investment-brains install --local investment-brains/investment-brain-quality --inactive",
        "./tcx investment-brains update investment-brain-quality --local investment-brains/investment-brain-quality",
        "./tcx investment-brains activate investment-brain-quality",
        "./tcx investment-brains deactivate investment-brain-quality",
        "./tcx investment-brains rollback investment-brain-quality --version 1.0.0",
        "./tcx investment-brains remove investment-brain-quality",
    )
    for index, command in enumerate(brain_commands):
        blocked_launcher = run_hook(
            workspace,
            "pre-tool-use",
            pre_tool_payload(
                workspace,
                session_id=session_id,
                turn_id=brain_turn_id,
                tool_use_id=f"brain-lifecycle-{index}",
                tool_name="exec_command",
                tool_input={"cmd": command, "workdir": str(workspace)},
                permission_mode="trading-research",
            ),
        )
        assert blocked_launcher is not None
        assert blocked_launcher["decision"] == "block"
        assert "manage_investment_brain" in str(blocked_launcher["reason"])

    brain_mcp = run_hook(
        workspace,
        "pre-tool-use",
        pre_tool_payload(
            workspace,
            session_id=session_id,
            turn_id=brain_turn_id,
            tool_use_id="brain-lifecycle-mcp",
            tool_name="mcp__tradingcodex__manage_investment_brain",
            tool_input={"action": "list"},
            permission_mode="trading-research",
        ),
    )
    assert brain_mcp is not None
    brain_mcp_output = brain_mcp["hookSpecificOutput"]
    assert brain_mcp_output["permissionDecision"] == "allow"
    assert brain_mcp_output["updatedInput"]["action"] == "list"
    assert brain_mcp_output["updatedInput"][PROOF_FIELD]

    mixed_patch = {
        "patch": (
            "*** Begin Patch\n"
            "*** Add File: investment-brains/investment-brain-quality/notes.md\n"
            "+brain\n"
            "*** Add File: unrelated.md\n"
            "+unrelated\n"
            "*** End Patch"
        ),
    }
    mixed = run_hook(
        workspace,
        "pre-tool-use",
        pre_tool_payload(
            workspace,
            session_id=session_id,
            turn_id=brain_turn_id,
            tool_use_id="brain-mixed-edit",
            tool_name="apply_patch",
            tool_input=mixed_patch,
            permission_mode="trading-research",
        ),
    )
    assert mixed is not None and mixed["decision"] == "block"

    crossed_strategy = run_hook(
        workspace,
        "pre-tool-use",
        pre_tool_payload(
            workspace,
            session_id=session_id,
            turn_id=brain_turn_id,
            tool_use_id="brain-crossed-strategy",
            tool_name="mcp__tradingcodex__manage_strategy",
            tool_input={"action": "list"},
            permission_mode="trading-research",
        ),
    )
    assert crossed_strategy is not None and crossed_strategy["decision"] == "block"
    assert "$tcx-strategy" in str(crossed_strategy["reason"])

    strategy_turn_id = "managed-strategy-turn"
    issue_managed_turn(workspace, session_id, strategy_turn_id, "$tcx-strategy")
    strategy_command = (
        "./tcx strategies create strategy-quality "
        "--description 'Quality discipline' --body-file strategy-quality.draft.md "
        "--language en --status draft"
    )
    blocked_strategy_launcher = run_hook(
        workspace,
        "pre-tool-use",
        pre_tool_payload(
            workspace,
            session_id=session_id,
            turn_id=strategy_turn_id,
            tool_use_id="strategy-lifecycle",
            tool_name="exec_command",
            tool_input={"cmd": strategy_command, "workdir": str(workspace)},
            permission_mode="trading-research",
        ),
    )
    assert blocked_strategy_launcher is not None
    assert blocked_strategy_launcher["decision"] == "block"
    assert "manage_strategy" in str(blocked_strategy_launcher["reason"])

    strategy_mcp = run_hook(
        workspace,
        "pre-tool-use",
        pre_tool_payload(
            workspace,
            session_id=session_id,
            turn_id=strategy_turn_id,
            tool_use_id="strategy-lifecycle-mcp",
            tool_name="mcp__tradingcodex__manage_strategy",
            tool_input={"action": "list"},
            permission_mode="trading-research",
        ),
    )
    assert strategy_mcp is not None
    strategy_mcp_output = strategy_mcp["hookSpecificOutput"]
    assert strategy_mcp_output["permissionDecision"] == "allow"
    assert strategy_mcp_output["updatedInput"]["action"] == "list"
    assert strategy_mcp_output["updatedInput"][PROOF_FIELD]

    crossed_brain = run_hook(
        workspace,
        "pre-tool-use",
        pre_tool_payload(
            workspace,
            session_id=session_id,
            turn_id=strategy_turn_id,
            tool_use_id="strategy-crossed-brain",
            tool_name="mcp__tradingcodex__manage_investment_brain",
            tool_input={"action": "list"},
            permission_mode="trading-research",
        ),
    )
    assert crossed_brain is not None and crossed_brain["decision"] == "block"
    assert "$tcx-brain" in str(crossed_brain["reason"])


@pytest.mark.parametrize("marker", ["$tcx-brain", "$tcx-strategy"])
def test_managed_skill_turns_fail_closed_in_plan_or_subagent_context(
    workspace: Path,
    marker: str,
) -> None:
    plan = run_hook(
        workspace,
        "user-prompt-submit",
        {
            "prompt": managed_prompt(marker),
            "session_id": "managed-plan-session",
            "turn_id": uuid.uuid4().hex,
            "cwd": str(workspace),
            "permission_mode": "plan",
        },
    )
    assert plan is not None and plan["decision"] == "block"
    assert "Plan mode" in str(plan["reason"])

    subagent = run_hook(
        workspace,
        "user-prompt-submit",
        {
            "prompt": managed_prompt(marker),
            "session_id": "managed-subagent-session",
            "turn_id": uuid.uuid4().hex,
            "cwd": str(workspace),
            "agent_type": "fundamental-analyst",
        },
    )
    assert subagent is not None and subagent["decision"] == "block"


def test_build_file_edits_reject_paths_outside_or_through_symlinks(workspace: Path) -> None:
    session_id = "path-bound-session"
    turn_id = "path-bound-turn"
    issue_build_turn(workspace, session_id, turn_id)
    outside = run_hook(
        workspace,
        "pre-tool-use",
        pre_tool_payload(
            workspace,
            session_id=session_id,
            turn_id=turn_id,
            tool_use_id="outside-write",
            tool_name="Write",
            tool_input={"file_path": "/tmp/tradingcodex-outside.txt", "content": "x"},
        ),
    )
    assert outside is not None and outside["decision"] == "block"

    external = workspace.parent / "external-edit-target"
    external.mkdir()
    (workspace / "linked-target").symlink_to(external, target_is_directory=True)
    symlinked = run_hook(
        workspace,
        "pre-tool-use",
        pre_tool_payload(
            workspace,
            session_id=session_id,
            turn_id=turn_id,
            tool_use_id="symlink-write",
            tool_name="apply_patch",
            tool_input={
                "patch": "*** Begin Patch\n*** Add File: linked-target/file.txt\n+x\n*** End Patch",
            },
        ),
    )
    assert symlinked is not None and symlinked["decision"] == "block"

    dotdot_protected = run_hook(
        workspace,
        "pre-tool-use",
        pre_tool_payload(
            workspace,
            session_id=session_id,
            turn_id=turn_id,
            tool_use_id="dotdot-protected-write",
            tool_name="Write",
            tool_input={
                "file_path": str(workspace / "trading/connectors/../../.codex/hooks.json"),
                "content": "{}",
            },
        ),
    )
    assert dotdot_protected is not None and dotdot_protected["decision"] == "block"

    git_config = run_hook(
        workspace,
        "pre-tool-use",
        pre_tool_payload(
            workspace,
            session_id=session_id,
            turn_id=turn_id,
            tool_use_id="git-config-write",
            tool_name="apply_patch",
            tool_input={
                "patch": "*** Begin Patch\n*** Add File: .git/config\n+[core]\n*** End Patch",
            },
        ),
    )
    assert git_config is not None and git_config["decision"] == "block"

    direct_write = run_hook(
        workspace,
        "pre-tool-use",
        pre_tool_payload(
            workspace,
            session_id=session_id,
            turn_id=turn_id,
            tool_use_id="direct-write",
            tool_name="Write",
            tool_input={
                "file_path": str(workspace / "trading/connectors/inside.txt"),
                "content": "x",
            },
        ),
    )
    assert direct_write is not None and direct_write["decision"] == "block"

    provider_source = run_hook(
        workspace,
        "pre-tool-use",
        pre_tool_payload(
            workspace,
            session_id=session_id,
            turn_id=turn_id,
            tool_use_id="provider-source-write",
            tool_name="apply_patch",
            tool_input={
                "patch": (
                    "*** Begin Patch\n"
                    "*** Add File: trading/connectors/demo/provider.py\n"
                    "+from tradingcodex_service.application.brokers import BrokerAdapterProvider\n"
                    "+# Documentation may say `git push`; this edit does not publish anything.\n"
                    "*** End Patch"
                ),
            },
        ),
    )
    assert provider_source is None


def test_all_build_protected_mcp_tools_receive_hook_owned_proof(workspace: Path) -> None:
    assert set(BUILD_PROTECTED_MCP_TOOLS) == EXPECTED_BUILD_MCP_TOOLS

    for index, tool_name in enumerate(sorted(EXPECTED_BUILD_MCP_TOOLS)):
        session_id = f"mcp-build-session-{index}"
        turn_id = f"mcp-build-turn-{index}"
        issue_build_turn(workspace, session_id, turn_id)
        output = run_hook(
            workspace,
            "pre-tool-use",
            pre_tool_payload(
                workspace,
                session_id=session_id,
                turn_id=turn_id,
                tool_use_id=f"build-mcp-{index}",
                tool_name=f"mcp__tradingcodex__{tool_name}",
                tool_input={"request_marker": f"request-{index}"},
            ),
        )
        assert output is not None
        hook_output = output["hookSpecificOutput"]
        assert hook_output["permissionDecision"] == "allow"
        rewritten = hook_output["updatedInput"]
        assert rewritten["request_marker"] == f"request-{index}"
        assert isinstance(rewritten[PROOF_FIELD], str) and rewritten[PROOF_FIELD]


def test_protected_mcp_permission_ui_requires_an_existing_build_turn(workspace: Path) -> None:
    tool_name = sorted(EXPECTED_BUILD_MCP_TOOLS)[0]
    payload = pre_tool_payload(
        workspace,
        session_id="mcp-permission-session",
        turn_id="mcp-permission-turn",
        tool_use_id="mcp-permission-tool",
        tool_name=f"mcp__tradingcodex__{tool_name}",
        tool_input={"broker_id": "demo", "provider_id": "demo"},
    )
    denied = run_hook(workspace, "permission-request", payload)
    assert denied is not None
    assert denied["decision"] == "block"

    issue_build_turn(workspace, "mcp-permission-session", "mcp-permission-turn")
    allowed = run_hook(workspace, "permission-request", payload)
    assert allowed is None


def test_build_mcp_proof_cannot_be_model_supplied_or_used_without_grant(workspace: Path) -> None:
    payload = pre_tool_payload(
        workspace,
        session_id="proof-session",
        turn_id="proof-turn",
        tool_use_id="proof-tool",
        tool_name="mcp__tradingcodex__register_broker_connector",
        tool_input={"broker_id": "demo", "provider_id": "demo", PROOF_FIELD: "forged"},
    )
    forged = run_hook(workspace, "pre-tool-use", payload)
    assert forged is not None
    assert forged["decision"] == "block"

    payload["tool_input"] = {"broker_id": "demo", "provider_id": "demo"}
    missing = run_hook(workspace, "pre-tool-use", payload)
    assert missing is not None
    assert missing["decision"] == "block"


@pytest.mark.parametrize(
    ("tool_name", "tool_input", "agent_type"),
    [
        ("write_stdin", {"session_id": 1, "chars": "status\n"}, ""),
        ("exec_command", {"cmd": "./tcx update --no-doctor"}, ""),
        ("exec_command", {"cmd": "./tcx skills optional create private --role fundamental-analyst --body-file /tmp/private.txt"}, ""),
        ("exec_command", {"cmd": "./tcx investment-brains install --local /tmp/untrusted-bundle --inactive"}, ""),
        ("exec_command", {"cmd": "./tcx investment-brains install --git file:///tmp/untrusted-bundle --inactive"}, ""),
        ("exec_command", {"cmd": "./tcx strategies create strategy-private --body-file /tmp/private.md"}, ""),
        ("exec_command", {"cmd": "./tcx strategies create strategy-inline --body '# Inline'"}, ""),
        ("exec_command", {"cmd": "./tcx strategies create strategy-missing-body"}, ""),
        ("exec_command", {"cmd": "tcx.cmd skills optional create private --role fundamental-analyst --body-file %TEMP%\\private.txt"}, ""),
        ("exec_command", {"cmd": "tcx.cmd investment-brains install --local %TEMP%\\bundle --inactive"}, ""),
        ("exec_command", {"cmd": "tcx.cmd skills optional create private --role fundamental-analyst --body-file ..^\\outside.txt"}, ""),
        ("exec_command", {"cmd": "cat .env"}, ""),
        ("exec_command", {"cmd": "./tcx mcp call use_order_turn_grant"}, ""),
        ("exec_command", {"cmd": "./tcx mcp permission approve --request-id 1"}, ""),
        ("exec_command", {"cmd": "./tcx mcp external register --name hostile --transport stdio --command /bin/sh --enabled"}, ""),
        ("exec_command", {"cmd": "./tcx mcp external check --name hostile"}, ""),
        ("exec_command", {"cmd": "./tcx mcp external discover --name hostile"}, ""),
        ("exec_command", {"cmd": "./tcx mcp external review-tool --tool-id 1 --enabled"}, ""),
        ("exec_command", {"cmd": "./tcx connectors register --provider-id demo --broker-id demo --credential-ref env:DEMO"}, ""),
        ("exec_command", {"cmd": "./tcx connectors approve-provider demo"}, ""),
        ("exec_command", {"cmd": "./tcx connectors revoke-provider demo"}, ""),
        ("exec_command", {"cmd": "./tcx mcp install-global --safe"}, ""),
        ("exec_command", {"cmd": "./tcx build codex-mcp add demo --scope=global --command demo"}, ""),
        ("exec_command", {"cmd": "./tcx build codex-mcp add --name demo --scope \"global\" --command demo"}, ""),
        ("exec_command", {"cmd": "./tcx build codex-mcp add --name demo --s global --command demo"}, ""),
        ("exec_command", {"cmd": "./tcx build codex-mcp import --source workspace --name demo"}, ""),
        ("exec_command", {"cmd": "codex mcp add demo --scope global -- echo demo"}, ""),
        ("exec_command", {"cmd": "git push origin main"}, ""),
        (
            "apply_patch",
            {"patch": "*** Begin Patch\n*** Update File: .codex/hooks.json\n@@\n-{}\n+{}\n*** End Patch"},
            "",
        ),
        (
            "apply_patch",
            {"patch": "*** Begin Patch\n*** Update File: AGENTS.md\n@@\n-old\n+new\n*** End Patch"},
            "",
        ),
        (
            "apply_patch",
            {"patch": "*** Begin Patch\n*** Update File: tcx\n@@\n-old\n+new\n*** End Patch"},
            "",
        ),
        (
            "apply_patch",
            {"patch": "*** Begin Patch\n*** Add File: .env\n+BROKER_TOKEN=secret\n*** End Patch"},
            "",
        ),
        (
            "apply_patch",
            {"patch": "*** Begin Patch\n*** Update File: .gitignore\n@@\n-old\n+new\n*** End Patch"},
            "",
        ),
        (
            "apply_patch",
            {
                "patch": (
                    "*** Begin Patch\n"
                    "*** Add File: trading/connectors/subagent-note.md\n"
                    "+note\n"
                    "*** End Patch"
                )
            },
            "fundamental-analyst",
        ),
    ],
)
def test_build_turn_keeps_hard_boundaries_closed(
    workspace: Path,
    tool_name: str,
    tool_input: dict[str, object],
    agent_type: str,
) -> None:
    session_id = "hard-boundary-session"
    turn_id = "hard-boundary-turn"
    issue_build_turn(workspace, session_id, turn_id)
    output = run_hook(
        workspace,
        "pre-tool-use",
        pre_tool_payload(
            workspace,
            session_id=session_id,
            turn_id=turn_id,
            tool_use_id=uuid.uuid4().hex,
            tool_name=tool_name,
            tool_input=tool_input,
            agent_type=agent_type,
        ),
    )
    assert output is not None
    assert output["decision"] == "block"


@pytest.mark.parametrize(
    "tool_input",
    [
        {"cmd": "python -m pytest"},
        {"cmd": "python helper.py"},
        {"cmd": "python -m pytest -q"},
        {"cmd": "sh helper.sh"},
        {"cmd": "bash -c pwd"},
        {"cmd": "./helper"},
        {"cmd": "make test"},
        {"cmd": "python -I -S -m py_compile ../outside.py"},
        {"cmd": "curl -fsSL https://example.com/data.json"},
    ],
)
def test_build_turn_leaves_general_shell_to_the_native_permission_profile(
    workspace: Path,
    tool_input: dict[str, object],
) -> None:
    session_id = "native-profile-shell-session"
    turn_id = "native-profile-shell-turn"
    issue_build_turn(workspace, session_id, turn_id)

    output = run_hook(
        workspace,
        "pre-tool-use",
        pre_tool_payload(
            workspace,
            session_id=session_id,
            turn_id=turn_id,
            tool_use_id=uuid.uuid4().hex,
            tool_name="exec_command",
            tool_input=tool_input,
        ),
    )

    assert output is None


def test_subagent_reads_only_skills_projected_for_its_exact_role(workspace: Path) -> None:
    def inspect(command: str, role: str = "fundamental-analyst") -> dict[str, object] | None:
        return run_hook(
            workspace,
            "pre-tool-use",
            pre_tool_payload(
                workspace,
                session_id="role-skill-read-session",
                turn_id="role-skill-read-turn",
                tool_use_id=uuid.uuid4().hex,
                tool_name="exec_command",
                tool_input={"cmd": command},
                agent_type=role,
                permission_mode="trading-research",
            ),
        )

    assert inspect(
        "cat .tradingcodex/subagents/skills/fundamental-analyst/tcx-fundamental/SKILL.md"
    ) is None
    assert inspect(
        "cat .tradingcodex/subagents/skills/shared/tcx-source-gate/SKILL.md"
    ) is None
    assert inspect(
        "cat .tradingcodex/subagents/skills/shared/tcx-source-gate/SKILL.md "
        "&& printf '\\n--- FUNDAMENTAL ---\\n' "
        "&& cat .tradingcodex/subagents/skills/fundamental-analyst/tcx-fundamental/SKILL.md "
        ".tradingcodex/subagents/skills/shared/tcx-evidence/SKILL.md"
    ) is None

    other_role = inspect(
        "cat .tradingcodex/subagents/skills/news-analyst/tcx-news/SKILL.md"
    )
    assert other_role is not None and other_role["decision"] == "block"

    generated_state = inspect("cat .tradingcodex/generated/skill-index.json")
    assert generated_state is not None and generated_state["decision"] == "block"

    combined_command = inspect(
        "cat .tradingcodex/subagents/skills/shared/tcx-source-gate/SKILL.md && pwd"
    )
    assert combined_command is not None and combined_command["decision"] == "block"

    for unsafe_command in (
        "cat .tradingcodex/subagents/skills/shared/tcx-source-gate/SKILL.md > /tmp/leak",
        "cat .tradingcodex/subagents/skills/shared/tcx-source-gate/SKILL.md; pwd",
        "cat .tradingcodex/subagents/skills/shared/tcx-source-gate/SKILL.md && printf \"$(id)\"",
    ):
        blocked = inspect(unsafe_command)
        assert blocked is not None and blocked["decision"] == "block"


def test_build_turn_never_allows_direct_external_mcp(workspace: Path) -> None:
    session_id = "external-mcp-session"
    turn_id = "external-mcp-turn"
    issue_build_turn(workspace, session_id, turn_id)
    output = run_hook(
        workspace,
        "pre-tool-use",
        pre_tool_payload(
            workspace,
            session_id=session_id,
            turn_id=turn_id,
            tool_use_id="external-mcp",
            tool_name="mcp__unmanaged_broker__place_order",
            tool_input={"symbol": "MSFT"},
        ),
    )
    assert output is not None
    assert output["decision"] == "block"


def test_operator_only_tradingcodex_mcp_tools_are_blocked_before_service(workspace: Path) -> None:
    assert set(BUILD_OPERATOR_ONLY_MCP_TOOLS) == EXPECTED_OPERATOR_ONLY_MCP_TOOLS
    issue_build_turn(workspace, "operator-tool-session", "operator-tool-turn")
    for index, tool_name in enumerate(sorted(EXPECTED_OPERATOR_ONLY_MCP_TOOLS)):
        output = run_hook(
            workspace,
            "pre-tool-use",
            pre_tool_payload(
                workspace,
                session_id="operator-tool-session",
                turn_id="operator-tool-turn",
                tool_use_id=f"operator-tool-{index}",
                tool_name=f"mcp__tradingcodex__{tool_name}",
                tool_input={},
            ),
        )
        assert output is not None
        assert output["decision"] == "block"


def test_build_file_edit_allows_credential_references_without_raw_secrets(workspace: Path) -> None:
    session_id = "credential-ref-session"
    turn_id = "credential-ref-turn"
    issue_build_turn(workspace, session_id, turn_id)
    output = run_hook(
        workspace,
        "pre-tool-use",
        pre_tool_payload(
            workspace,
            session_id=session_id,
            turn_id=turn_id,
            tool_use_id="credential-ref-tool",
            tool_name="apply_patch",
            tool_input={
                "patch": (
                    "*** Begin Patch\n"
                    "*** Add File: trading/connectors/credential-ref.txt\n"
                    "+env:ALPACA_API_KEY\n"
                    "*** End Patch"
                ),
            },
        ),
    )
    assert output is None


def test_namespaced_apply_patch_allows_ordinary_workspace_files_without_build(workspace: Path) -> None:
    patch_input = {
        "patch": "*** Begin Patch\n*** Add File: namespaced.md\n+reviewable\n*** End Patch",
    }
    without_grant = run_hook(
        workspace,
        "pre-tool-use",
        pre_tool_payload(
            workspace,
            session_id="namespaced-session",
            turn_id="namespaced-turn",
            tool_use_id="namespaced-without-grant",
            tool_name="functions.apply_patch",
            tool_input=patch_input,
        ),
    )
    assert without_grant is None

    native_command_shape = run_hook(
        workspace,
        "pre-tool-use",
        pre_tool_payload(
            workspace,
            session_id="native-command-session",
            turn_id="native-command-turn",
            tool_use_id="native-command-without-grant",
            tool_name="apply_patch",
            tool_input={"command": patch_input["patch"]},
        ),
    )
    assert native_command_shape is None

    role_edit = run_hook(
        workspace,
        "pre-tool-use",
        pre_tool_payload(
            workspace,
            session_id="role-file-session",
            turn_id="role-file-turn",
            tool_use_id="role-file-without-grant",
            tool_name="apply_patch",
            tool_input={
                "patch": "*** Begin Patch\n*** Add File: outputs/role-note.md\n+role\n*** End Patch",
            },
            agent_type="fundamental-analyst",
        ),
    )
    assert role_edit is None

    issue_build_turn(workspace, "namespaced-session", "namespaced-turn")
    allowed = run_hook(
        workspace,
        "pre-tool-use",
        pre_tool_payload(
            workspace,
            session_id="namespaced-session",
            turn_id="namespaced-turn",
            tool_use_id="namespaced-with-grant",
            tool_name="functions.apply_patch",
            tool_input=patch_input,
        ),
    )
    assert allowed is None

    direct_write = run_hook(
        workspace,
        "pre-tool-use",
        pre_tool_payload(
            workspace,
            session_id="namespaced-session",
            turn_id="namespaced-turn",
            tool_use_id="namespaced-direct-write",
            tool_name="tools__write",
            tool_input={"file_path": "namespaced.txt", "content": "hidden"},
        ),
    )
    assert direct_write is not None and direct_write["decision"] == "block"


def test_stop_and_next_user_turn_revoke_build_authority(workspace: Path) -> None:
    session_id = "revoke-build-session"
    first_turn = "revoke-build-turn-1"
    issue_build_turn(workspace, session_id, first_turn)
    run_hook(
        workspace,
        "stop",
        {"session_id": session_id, "turn_id": first_turn, "cwd": str(workspace)},
    )
    stopped = run_hook(
        workspace,
        "pre-tool-use",
        pre_tool_payload(
            workspace,
            session_id=session_id,
            turn_id=first_turn,
            tool_use_id="stopped-edit",
            tool_name="apply_patch",
            tool_input={
                "patch": (
                    "*** Begin Patch\n"
                    "*** Add File: trading/connectors/stopped.md\n"
                    "+x\n"
                    "*** End Patch"
                )
            },
        ),
    )
    assert stopped is not None
    assert stopped["decision"] == "block"

    second_turn = "revoke-build-turn-2"
    issue_build_turn(workspace, session_id, second_turn)
    ordinary = run_hook(
        workspace,
        "user-prompt-submit",
        {
            "prompt": "Summarize the existing connector status without changing files.",
            "session_id": session_id,
            "turn_id": "ordinary-turn",
            "cwd": str(workspace),
        },
    )
    assert ordinary is not None
    revoked = run_hook(
        workspace,
        "pre-tool-use",
        pre_tool_payload(
            workspace,
            session_id=session_id,
            turn_id=second_turn,
            tool_use_id="revoked-edit",
            tool_name="apply_patch",
            tool_input={
                "patch": (
                    "*** Begin Patch\n"
                    "*** Add File: trading/connectors/revoked.md\n"
                    "+x\n"
                    "*** End Patch"
                )
            },
        ),
    )
    assert revoked is not None
    assert revoked["decision"] == "block"


def test_duplicate_build_prompt_delivery_reuses_the_same_turn_grant(workspace: Path) -> None:
    session_id = "duplicate-build-session"
    turn_id = "duplicate-build-turn"
    issue_build_turn(workspace, session_id, turn_id)
    issue_build_turn(workspace, session_id, turn_id)

    allowed = run_hook(
        workspace,
        "pre-tool-use",
        pre_tool_payload(
            workspace,
            session_id=session_id,
            turn_id=turn_id,
            tool_use_id="edit-after-duplicate",
            tool_name="apply_patch",
            tool_input={
                "patch": "*** Begin Patch\n*** Add File: duplicate-ok.md\n+ok\n*** End Patch",
            },
        ),
    )
    assert allowed is None

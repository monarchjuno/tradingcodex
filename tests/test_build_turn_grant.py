from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path, PureWindowsPath

import pytest

from tradingcodex_cli.generator import bootstrap_workspace
from tradingcodex_service.application import build_gateway, skill_invocations
from tradingcodex_service.application.build_gateway import (
    BUILD_PROTECTED_MCP_TOOLS,
    BUILD_TURN_PROOF_FIELD,
    MANAGED_SKILL_PROTECTED_MCP_TOOL_SCOPES,
    BuildInvocationError,
    authorize_local_build_tool,
    begin_reserved_build_turn_use,
    finish_reserved_build_turn_use,
    issue_build_turn_grant,
    issue_managed_skill_turn_grant,
    parse_build_invocation,
    parse_managed_skill_invocation,
    reserve_build_turn_use,
    revoke_build_turn_grants,
)
from tradingcodex_service.application.runtime import (
    ensure_runtime_database,
    workspace_context_payload,
)


EXPECTED_BUILD_MCP_TOOLS = frozenset(
    {
        "register_broker_connector",
        "validate_broker_connector_build",
    }
)
EXPECTED_MANAGED_MCP_TOOL_SCOPES = {
    "manage_investment_brain": "brain",
    "manage_strategy": "strategy",
}


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / f"build-turn-{uuid.uuid4().hex[:10]}"
    bootstrap_workspace(root)
    ensure_runtime_database(root)
    return root


def build_prompt(request: str = "Add and validate the requested local connector provider.") -> str:
    return f"$tcx-build\n{request}"


def issue(workspace: Path, *, session_id: str = "build-session", turn_id: str = "build-turn") -> dict[str, object]:
    result = issue_build_turn_grant(
        workspace,
        build_prompt(),
        session_id=session_id,
        turn_id=turn_id,
        cwd=workspace,
    )
    assert result is not None
    return result


def workspace_grants(workspace: Path):
    from apps.harness.models import BuildTurnGrant

    context = workspace_context_payload(workspace)
    return BuildTurnGrant.objects.filter(
        workspace_id=context["workspace_id"],
        workspace_path_hash=context["path_hash"],
    )


def skill_link(workspace: Path, skill_id: str, *, windows_separators: bool = False) -> str:
    target = str(workspace / ".agents" / "skills" / skill_id / "SKILL.md")
    if windows_separators:
        target = target.replace("/", "\\")
    return f"[${skill_id}]({target})"


def test_build_parser_accepts_first_meaningful_line_and_workspace_links(workspace: Path) -> None:
    accepted = (
        build_prompt(),
        "$tcx-build\r\nValidate the provider.",
        "\ufeff\n \t\n $tcx-build \t\nValidate the provider.\n",
        "$tcx-build\x85Validate the provider.",
        "$tcx-build\u2028Validate the provider.",
        "$tcx-build\u2029Validate the provider.",
        "$tcx-build Update the provider.",
        "\u00a0$tcx-build\u3000Update the provider.\u00a0",
        f"{skill_link(workspace, 'tcx-build')} Update the provider.",
        "[$tcx-build](<.agents/skills/tcx-build/SKILL.md>)\nUpdate the provider.",
        f"\n{skill_link(workspace, 'tcx-build', windows_separators=True)}\nUpdate the provider.",
    )
    for prompt in accepted:
        assert parse_build_invocation(prompt, workspace) is True
    assert parse_build_invocation("Research how $tcx-build works without changing files.") is None
    assert parse_build_invocation("$TCX-build\nUpdate the provider.") is None
    assert parse_build_invocation("\u200b$tcx-build\nUpdate the provider.") is None
    assert parse_build_invocation("$tcx-buіld\nUpdate the provider.") is None

    malformed = (
        "$tcx-build",
        "$tcx-build \u200b",
        "$tcx-build\n\ufeff",
        "$tcx-build \u200b--force",
        "$tcx-build\n\u200b--force",
        "$tcx-build\n\ufeff--force",
        "$tcx-build\n\u0301--force",
        "$tcx-build \u200bUpdate the provider.",
        "$tcx-build --force\nUpdate the provider.",
        "$tcx-build\n--force",
        "$tcx-build\n$tcx-order-allow --mode paper\nUpdate the provider.",
        "$tcx-build\n$tcx-order-submit ticket-1 receipt-1 confirm\nUpdate the provider.",
        "$tcx-build\n$execute-paper-order --ticket-id ticket-1\nUpdate the provider.",
        "$tcx-build\nUpdate the provider.\n$tcx-build Update it again.",
        f"[$tcx-build]({workspace / '.agents/skills/tcx-brain/SKILL.md'}) Update the provider.",
    )
    for prompt in malformed:
        with pytest.raises(BuildInvocationError):
            parse_build_invocation(prompt, workspace)


def test_workspace_skill_links_reject_outside_symlink_aliases(workspace: Path) -> None:
    alias = workspace.parent / "outside-build-skill-alias.md"
    alias.symlink_to(workspace / ".agents/skills/tcx-build/SKILL.md")

    with pytest.raises(BuildInvocationError, match="must target"):
        parse_build_invocation(f"[$tcx-build]({alias}) Update the provider.", workspace)


def test_windows_drive_skill_links_require_the_lexical_projected_path() -> None:
    expected = PureWindowsPath(
        r"C:\Workspaces\Trading Codex\.agents\skills\tcx-build\SKILL.md"
    )

    assert skill_invocations._windows_drive_target_matches_expected(
        "C:/Workspaces/Trading Codex/.agents/skills/tcx-build/SKILL.md",
        expected,
    )
    assert skill_invocations._windows_drive_target_matches_expected(
        "c:/workspaces/trading codex/.agents/skills/tcx-build/skill.md",
        expected,
    )
    assert not skill_invocations._windows_drive_target_matches_expected(
        "C:/Outside/build-skill-alias.md",
        expected,
    )


@pytest.mark.parametrize(
    ("marker", "scope"),
    [("$tcx-brain", "brain"), ("$tcx-strategy", "strategy")],
)
def test_managed_skill_parser_and_grant_are_exact_and_capability_scoped(
    workspace: Path,
    marker: str,
    scope: str,
) -> None:
    prompt = f"\ufeff\n{skill_link(workspace, marker.removeprefix('$'))} Perform the requested managed action."
    assert parse_managed_skill_invocation(prompt, workspace) == scope
    assert parse_build_invocation(prompt) is None
    result = issue_managed_skill_turn_grant(
        workspace,
        prompt,
        session_id=f"{scope}-session",
        turn_id=f"{scope}-turn",
        cwd=workspace,
        permission_mode="trading-research",
    )
    assert result is not None
    assert result["marker"] == "tradingcodex-managed-skill-turn-grant"
    assert result["authority_scope"] == scope
    assert result["entrypoint"] == marker

    grant = workspace_grants(workspace).get()
    assert grant.authority_scope == scope
    assert grant.metadata["entrypoint"] == marker

    with pytest.raises(PermissionError, match="cannot authorize"):
        authorize_local_build_tool(
            workspace,
            f"{scope}-session",
            f"{scope}-turn",
            "wrong-scope-tool",
            "exec_command",
            {"cmd": "./tcx doctor"},
            permission_mode="trading-research",
            required_scope="build",
        )

    assert parse_managed_skill_invocation(f"{marker} now", workspace) == scope
    with pytest.raises(BuildInvocationError):
        parse_managed_skill_invocation(f"{marker}\n$tcx-build\nPerform the action.", workspace)
    with pytest.raises(BuildInvocationError):
        parse_managed_skill_invocation(f"{marker}\n\u200b", workspace)
    for hidden_request in ("\u200b--force", "\ufeff--force", "\u0301--force"):
        with pytest.raises(BuildInvocationError):
            parse_managed_skill_invocation(f"{marker}\n{hidden_request}", workspace)
    with pytest.raises(BuildInvocationError, match="cannot be combined"):
        parse_managed_skill_invocation(
            f"{marker}\n$execute-paper-order --ticket-id ticket-1\nPerform the action.",
            workspace,
        )


def test_formatted_build_grant_hashes_the_raw_prompt(workspace: Path) -> None:
    prompt = f"\ufeff\n{skill_link(workspace, 'tcx-build')} Update the provider.\n"
    result = issue_build_turn_grant(
        workspace,
        prompt,
        session_id="formatted-build-session",
        turn_id="formatted-build-turn",
        cwd=workspace,
    )

    assert result is not None
    assert workspace_grants(workspace).get().prompt_sha256 == hashlib.sha256(prompt.encode()).hexdigest()


def test_issue_requires_cwd_and_binds_only_hashed_turn_inputs(workspace: Path) -> None:
    from apps.audit.models import AuditEvent

    session_id = "raw-session-never-persist"
    turn_id = "raw-turn-never-persist"
    prompt = build_prompt("Use raw-request-never-persist to scaffold the provider.")

    assert issue_build_turn_grant(
        workspace,
        "Summarize connector status without changing it.",
        session_id=session_id,
        turn_id=turn_id,
    ) is None
    with pytest.raises(PermissionError, match="cwd"):
        issue_build_turn_grant(
            workspace,
            prompt,
            session_id=session_id,
            turn_id=turn_id,
        )
    with pytest.raises(PermissionError, match="workspace"):
        issue_build_turn_grant(
            workspace,
            prompt,
            session_id=session_id,
            turn_id=turn_id,
            cwd=workspace.parent,
        )

    projection = issue_build_turn_grant(
        workspace,
        prompt,
        session_id=session_id,
        turn_id=turn_id,
        cwd=workspace,
    )
    assert projection == {
        "marker": "tradingcodex-build-turn-grant",
        "status": "active",
        "expires_at": projection["expires_at"],
        "multi_use": True,
        "use_count": 0,
    }
    assert set(projection) == {"marker", "status", "expires_at", "multi_use", "use_count"}

    grant = workspace_grants(workspace).get()
    assert grant.session_id_hash == hashlib.sha256(session_id.encode()).hexdigest()
    assert grant.turn_id_hash == hashlib.sha256(turn_id.encode()).hexdigest()
    assert grant.prompt_sha256 == hashlib.sha256(prompt.encode()).hexdigest()
    stored = json.dumps(
        {
            "session_id_hash": grant.session_id_hash,
            "turn_id_hash": grant.turn_id_hash,
            "prompt_sha256": grant.prompt_sha256,
            "metadata": grant.metadata,
        },
        sort_keys=True,
    )
    for raw in (session_id, turn_id, prompt, "raw-request-never-persist"):
        assert raw not in stored

    context = workspace_context_payload(workspace)
    events = AuditEvent.objects.filter(
        action__startswith="build.turn_grant.",
        workspace_context__workspace_id=context["workspace_id"],
    )
    audit_blob = json.dumps([event.payload for event in events], sort_keys=True)
    for raw in (session_id, turn_id, prompt, "raw-request-never-persist"):
        assert raw not in audit_blob


def test_plan_mode_is_denied_and_permission_mode_is_turn_bound(workspace: Path) -> None:
    with pytest.raises(PermissionError, match="Plan mode"):
        issue_build_turn_grant(
            workspace,
            build_prompt(),
            session_id="plan-mode-session",
            turn_id="plan-mode-turn",
            cwd=workspace,
            permission_mode="plan",
        )

    issue_build_turn_grant(
        workspace,
        build_prompt(),
        session_id="bound-mode-session",
        turn_id="bound-mode-turn",
        cwd=workspace,
        permission_mode="workspace-write",
    )
    with pytest.raises(PermissionError, match="permission mode"):
        authorize_local_build_tool(
            workspace,
            "bound-mode-session",
            "bound-mode-turn",
            "bound-mode-tool",
            "apply_patch",
            {"file_path": "trading/connectors/demo/provider.py", "content": "pass\n"},
            permission_mode="read-only",
        )


def test_same_turn_reuses_grant_and_a_new_turn_supersedes_it(workspace: Path) -> None:
    from apps.harness.models import BuildTurnGrant

    session_id = "same-session"
    first = issue(workspace, session_id=session_id, turn_id="turn-1")
    assert issue(workspace, session_id=session_id, turn_id="turn-1") == first
    assert workspace_grants(workspace).count() == 1

    issue(workspace, session_id=session_id, turn_id="turn-2")
    grants = list(workspace_grants(workspace).order_by("issued_at", "id"))
    assert [grant.status for grant in grants] == [
        BuildTurnGrant.STATUS_REVOKED,
        BuildTurnGrant.STATUS_ACTIVE,
    ]
    assert grants[0].metadata["terminal_reason"] == "superseded_by_new_turn"


def test_local_authorization_is_turn_bound_multi_use_and_hides_input(workspace: Path) -> None:
    from apps.audit.models import AuditEvent
    from apps.harness.models import BuildTurnGrant

    issue(workspace)
    tool_input = {
        "patch": "*** Begin Patch\n*** Add File: provider.py\n+TOKEN = 'raw-input-never-persist'\n*** End Patch",
    }
    for tool_use_id in ("local-tool-1", "local-tool-2"):
        result = authorize_local_build_tool(
            workspace,
            "build-session",
            "build-turn",
            tool_use_id,
            "apply_patch",
            tool_input,
        )
    assert result["status"] == BuildTurnGrant.STATUS_ACTIVE
    assert result["use_count"] == 2

    grant = workspace_grants(workspace).get()
    assert grant.status == BuildTurnGrant.STATUS_ACTIVE
    assert grant.use_count == 2
    with pytest.raises(PermissionError):
        authorize_local_build_tool(
            workspace,
            "wrong-session",
            "build-turn",
            "wrong-session-use",
            "apply_patch",
            tool_input,
        )
    with pytest.raises(PermissionError):
        authorize_local_build_tool(
            workspace,
            "build-session",
            "wrong-turn",
            "wrong-turn-use",
            "apply_patch",
            tool_input,
        )
    with pytest.raises(BuildInvocationError, match="reserved"):
        authorize_local_build_tool(
            workspace,
            "build-session",
            "build-turn",
            "protected-use",
            "mcp__tradingcodex__validate_broker_connector_build",
            {},
        )

    context = workspace_context_payload(workspace)
    audit_blob = json.dumps(
        [
            event.payload
            for event in AuditEvent.objects.filter(
                action="build.turn_grant.tool_allowed",
                workspace_context__workspace_id=context["workspace_id"],
            )
        ],
        sort_keys=True,
    )
    for raw in ("local-tool-1", "local-tool-2", "raw-input-never-persist"):
        assert raw not in audit_blob


def test_protected_mcp_reservation_is_single_use_at_a_time_and_reusable_after_finish(
    workspace: Path,
) -> None:
    from apps.audit.models import AuditEvent
    from apps.harness.models import BuildTurnGrant

    assert BUILD_PROTECTED_MCP_TOOLS == EXPECTED_BUILD_MCP_TOOLS
    issue(workspace)
    public_args = {
        "broker_id": "raw-broker-input-never-persist",
        "provider_id": "sample",
    }
    proof = reserve_build_turn_use(
        workspace,
        "build-session",
        "build-turn",
        "protected-tool-1",
        "mcp__tradingcodex__validate_broker_connector_build",
        public_args,
    )
    grant = workspace_grants(workspace).get()
    assert grant.status == BuildTurnGrant.STATUS_RESERVED
    assert proof not in grant.reservation_proof_hash
    assert grant.reservation_proof_hash == hashlib.sha256(proof.encode()).hexdigest()
    assert "protected-tool-1" not in grant.reservation_tool_use_id_hash

    with pytest.raises(PermissionError, match="already in flight"):
        reserve_build_turn_use(
            workspace,
            "build-session",
            "build-turn",
            "protected-tool-2",
            "validate_broker_connector_build",
            public_args,
        )
    with pytest.raises(PermissionError, match="in flight"):
        authorize_local_build_tool(
            workspace,
            "build-session",
            "build-turn",
            "local-while-reserved",
            "apply_patch",
            {"patch": "safe"},
        )
    with pytest.raises(PermissionError, match="proof"):
        begin_reserved_build_turn_use(
            workspace,
            "validate_broker_connector_build",
            public_args,
            "forged-proof",
        )
    with pytest.raises(PermissionError, match="does not match"):
        begin_reserved_build_turn_use(
            workspace,
            "validate_broker_connector_build",
            {"broker_id": "changed", "provider_id": "sample"},
            proof,
        )

    grant_id = begin_reserved_build_turn_use(
        workspace,
        "validate_broker_connector_build",
        {
            **public_args,
            "principal_id": "head-manager",
            BUILD_TURN_PROOF_FIELD: proof,
        },
        proof,
    )
    assert grant_id == grant.grant_id
    with pytest.raises(PermissionError, match="proof"):
        begin_reserved_build_turn_use(
            workspace,
            "validate_broker_connector_build",
            public_args,
            proof,
        )

    finished = finish_reserved_build_turn_use(workspace, grant_id, "error")
    assert finished["status"] == BuildTurnGrant.STATUS_ACTIVE
    assert finished["use_count"] == 1
    with pytest.raises(PermissionError, match="no longer active"):
        finish_reserved_build_turn_use(workspace, grant_id, "ok")

    proof_2 = reserve_build_turn_use(
        workspace,
        "build-session",
        "build-turn",
        "protected-tool-2",
        "validate_broker_connector_build",
        {"broker_id": "demo"},
    )
    grant_id_2 = begin_reserved_build_turn_use(
        workspace,
        "validate_broker_connector_build",
        {
            "broker_id": "demo",
            "principal_id": "head-manager",
            BUILD_TURN_PROOF_FIELD: proof_2,
        },
        proof_2,
    )
    finished = finish_reserved_build_turn_use(workspace, grant_id_2, "ok")
    assert finished["status"] == BuildTurnGrant.STATUS_ACTIVE
    assert finished["use_count"] == 2

    grant.refresh_from_db()
    stored = json.dumps(
        {
            "reservation_tool_use_id_hash": grant.reservation_tool_use_id_hash,
            "reservation_arguments_hash": grant.reservation_arguments_hash,
            "reservation_proof_hash": grant.reservation_proof_hash,
            "metadata": grant.metadata,
        },
        sort_keys=True,
    )
    context = workspace_context_payload(workspace)
    audit_blob = json.dumps(
        [
            event.payload
            for event in AuditEvent.objects.filter(
                action__startswith="build.turn_grant.",
                workspace_context__workspace_id=context["workspace_id"],
            )
        ],
        sort_keys=True,
    )
    for raw in (
        proof,
        proof_2,
        "protected-tool-1",
        "protected-tool-2",
        "raw-broker-input-never-persist",
    ):
        assert raw not in stored
        assert raw not in audit_blob


def test_protected_argument_hash_omits_only_transport_fields(workspace: Path) -> None:
    issue(workspace)
    with pytest.raises(BuildInvocationError, match="unsupported private"):
        reserve_build_turn_use(
            workspace,
            "build-session",
            "build-turn",
            "private-field-use",
            "validate_broker_connector_build",
            {"broker_id": "demo", "_untrusted": "value"},
        )


def test_grant_is_bound_to_workspace_and_can_be_revoked_while_reserved(
    workspace: Path,
    tmp_path: Path,
) -> None:
    from apps.harness.models import BuildTurnGrant

    issue(workspace)
    proof = reserve_build_turn_use(
        workspace,
        "build-session",
        "build-turn",
        "revoke-tool",
        "validate_broker_connector_build",
        {"broker_id": "demo"},
    )
    other = tmp_path / f"other-build-{uuid.uuid4().hex[:10]}"
    bootstrap_workspace(other)
    with pytest.raises(PermissionError, match="proof"):
        begin_reserved_build_turn_use(
            other,
            "validate_broker_connector_build",
            {"broker_id": "demo"},
            proof,
        )

    assert revoke_build_turn_grants(workspace, "build-session", turn_id="wrong-turn") == 0
    assert revoke_build_turn_grants(workspace, "build-session", turn_id="build-turn") == 1
    grant = workspace_grants(workspace).get()
    assert grant.status == BuildTurnGrant.STATUS_REVOKED
    assert grant.reservation_proof_hash == ""
    assert grant.metadata["terminal_reason"] == "stop"
    with pytest.raises(PermissionError, match="proof"):
        begin_reserved_build_turn_use(
            workspace,
            "validate_broker_connector_build",
            {"broker_id": "demo"},
            proof,
        )


def test_abandoned_reservation_lease_is_released_and_old_proof_is_invalid(
    workspace: Path,
) -> None:
    from apps.harness.models import BuildTurnGrant

    issue(workspace)
    old_proof = reserve_build_turn_use(
        workspace,
        "build-session",
        "build-turn",
        "abandoned-tool",
        "validate_broker_connector_build",
        {"provider_id": "sample", "broker_id": "demo"},
    )
    grant = workspace_grants(workspace).get()
    grant.reserved_at = datetime.now(timezone.utc) - timedelta(minutes=3)
    grant.save(update_fields=["reserved_at"])

    new_proof = reserve_build_turn_use(
        workspace,
        "build-session",
        "build-turn",
        "replacement-tool",
        "validate_broker_connector_build",
        {"provider_id": "sample", "broker_id": "demo"},
    )
    assert new_proof != old_proof
    with pytest.raises(PermissionError, match="proof"):
        begin_reserved_build_turn_use(
            workspace,
            "validate_broker_connector_build",
            {"provider_id": "sample", "broker_id": "demo"},
            old_proof,
        )
    grant.refresh_from_db()
    assert grant.status == BuildTurnGrant.STATUS_RESERVED
    assert grant.metadata["last_reservation_terminal_reason"] == "reservation_lease_expired"


def test_started_service_call_defers_revoke_until_finish(workspace: Path) -> None:
    from apps.harness.models import BuildTurnGrant

    issue(workspace)
    arguments = {"provider_id": "sample", "broker_id": "demo"}
    proof = reserve_build_turn_use(
        workspace,
        "build-session",
        "build-turn",
        "started-tool",
        "validate_broker_connector_build",
        arguments,
    )
    grant_id = begin_reserved_build_turn_use(
        workspace,
        "validate_broker_connector_build",
        arguments,
        proof,
    )

    assert revoke_build_turn_grants(workspace, "build-session", turn_id="build-turn") == 1
    grant = workspace_grants(workspace).get()
    assert grant.status == BuildTurnGrant.STATUS_RESERVED
    assert grant.service_started_at is not None
    assert grant.metadata["revoke_after_finish"] == "stop"

    finished = finish_reserved_build_turn_use(workspace, grant_id, "ok")
    assert finished["status"] == BuildTurnGrant.STATUS_REVOKED
    grant.refresh_from_db()
    assert grant.metadata["terminal_reason"] == "stop"
    assert "revoke_after_finish" not in grant.metadata


def test_new_turn_waits_for_started_protected_call_before_issuing(workspace: Path) -> None:
    from apps.harness.models import BuildTurnGrant

    issue(workspace, session_id="serial-session", turn_id="turn-1")
    arguments = {"provider_id": "sample", "broker_id": "demo"}
    proof = reserve_build_turn_use(
        workspace,
        "serial-session",
        "turn-1",
        "serial-tool",
        "validate_broker_connector_build",
        arguments,
    )
    grant_id = begin_reserved_build_turn_use(
        workspace,
        "validate_broker_connector_build",
        arguments,
        proof,
    )

    with pytest.raises(PermissionError, match="still in flight"):
        issue(workspace, session_id="serial-session", turn_id="turn-2")
    grant = workspace_grants(workspace).get()
    assert grant.status == BuildTurnGrant.STATUS_RESERVED
    assert grant.metadata["revoke_after_finish"] == "superseded_by_new_turn"

    finish_reserved_build_turn_use(workspace, grant_id, "ok")
    result = issue(workspace, session_id="serial-session", turn_id="turn-2")
    assert result["status"] == BuildTurnGrant.STATUS_ACTIVE
    assert list(workspace_grants(workspace).values_list("status", flat=True)) == [
        BuildTurnGrant.STATUS_ACTIVE,
        BuildTurnGrant.STATUS_REVOKED,
    ]


def test_expiry_does_not_clear_an_already_started_service_call(workspace: Path) -> None:
    from apps.harness.models import BuildTurnGrant

    issue(workspace)
    arguments = {"provider_id": "sample", "broker_id": "demo"}
    proof = reserve_build_turn_use(
        workspace,
        "build-session",
        "build-turn",
        "expiry-in-flight-tool",
        "validate_broker_connector_build",
        arguments,
    )
    grant_id = begin_reserved_build_turn_use(
        workspace,
        "validate_broker_connector_build",
        arguments,
        proof,
    )
    grant = workspace_grants(workspace).get()
    grant.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    grant.save(update_fields=["expires_at"])

    assert revoke_build_turn_grants(workspace, "build-session", turn_id="build-turn") == 1
    grant.refresh_from_db()
    assert grant.status == BuildTurnGrant.STATUS_RESERVED
    assert grant.service_started_at is not None

    finished = finish_reserved_build_turn_use(workspace, grant_id, "ok")
    assert finished["status"] == BuildTurnGrant.STATUS_EXPIRED
    grant.refresh_from_db()
    assert grant.metadata["terminal_reason"] == "expired"


def test_expired_grant_fails_closed_and_is_marked_terminal(workspace: Path) -> None:
    from apps.harness.models import BuildTurnGrant

    result = issue_build_turn_grant(
        workspace,
        build_prompt(),
        session_id="expired-session",
        turn_id="expired-turn",
        cwd=workspace,
        issued_at=datetime.now(timezone.utc) - timedelta(hours=2),
    )
    assert result is not None
    with pytest.raises(PermissionError, match="expired"):
        authorize_local_build_tool(
            workspace,
            "expired-session",
            "expired-turn",
            "expired-tool",
            "apply_patch",
            {"patch": "safe"},
        )
    grant = workspace_grants(workspace).get()
    assert grant.status == BuildTurnGrant.STATUS_EXPIRED
    assert grant.metadata["terminal_reason"] == "expired"


def test_required_audit_failure_rolls_back_grant_state(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from apps.harness.models import BuildTurnGrant

    def fail_audit(*args, **kwargs):
        raise RuntimeError("required audit unavailable")

    monkeypatch.setattr(build_gateway, "write_audit_event_required", fail_audit)
    with pytest.raises(RuntimeError, match="required audit"):
        issue_build_turn_grant(
            workspace,
            build_prompt(),
            session_id="audit-session",
            turn_id="audit-turn",
            cwd=workspace,
        )
    assert not workspace_grants(workspace).exists()

    monkeypatch.undo()
    issue(workspace)
    monkeypatch.setattr(build_gateway, "write_audit_event_required", fail_audit)
    with pytest.raises(RuntimeError, match="required audit"):
        authorize_local_build_tool(
            workspace,
            "build-session",
            "build-turn",
            "audit-local-tool",
            "apply_patch",
            {"patch": "safe"},
        )
    grant = workspace_grants(workspace).get()
    assert grant.status == BuildTurnGrant.STATUS_ACTIVE
    assert grant.use_count == 0

    with pytest.raises(RuntimeError, match="required audit"):
        reserve_build_turn_use(
            workspace,
            "build-session",
            "build-turn",
            "audit-protected-tool",
            "validate_broker_connector_build",
            {"broker_id": "demo"},
        )
    grant.refresh_from_db()
    assert grant.status == BuildTurnGrant.STATUS_ACTIVE
    assert grant.reservation_proof_hash == ""


def test_stdio_mcp_build_tools_require_and_consume_the_hook_proof(workspace: Path) -> None:
    from apps.harness.models import BuildTurnGrant
    from apps.mcp.models import McpToolCall
    from tradingcodex_service.mcp_runtime import call_mcp_tool, handle_mcp_rpc

    arguments = {
        "provider_id": "paper",
        "broker_id": "proof-gated-broker",
        "credential_ref": "env:PROOF_GATED_BROKER",
    }
    message = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "register_broker_connector", "arguments": arguments},
    }

    with pytest.raises(BuildInvocationError, match="Build turn proof is required"):
        call_mcp_tool(
            workspace,
            "register_broker_connector",
            arguments,
            transport_principal="head-manager",
        )

    denied = handle_mcp_rpc(workspace, message, transport_principal="head-manager")
    assert denied is not None
    assert "Build turn proof is required" in denied["error"]["message"]
    assert not (workspace / "trading/connectors/proof-gated-broker").exists()

    issue(workspace)
    proof = reserve_build_turn_use(
        workspace,
        "build-session",
        "build-turn",
        "stdio-protected-tool",
        "register_broker_connector",
        arguments,
    )
    authorized = {
        **message,
        "id": 2,
        "params": {
            **message["params"],
            "arguments": {**arguments, BUILD_TURN_PROOF_FIELD: proof},
        },
    }
    response = handle_mcp_rpc(workspace, authorized, transport_principal="head-manager")
    assert response is not None and "result" in response
    assert not (workspace / "trading/connectors/proof-gated-broker/connector-profile.json").exists()

    grant = workspace_grants(workspace).get()
    assert grant.status == BuildTurnGrant.STATUS_ACTIVE
    assert grant.use_count == 1
    assert grant.reservation_proof_hash == ""

    replay = handle_mcp_rpc(workspace, authorized, transport_principal="head-manager")
    assert replay is not None
    assert "unique reserved $tcx-build proof" in replay["error"]["message"]

    ledger = McpToolCall.objects.filter(
        tool_name="register_broker_connector",
        status="ok",
        workspace_context__workspace_id=workspace_context_payload(workspace)["workspace_id"],
    ).latest("id")
    assert BUILD_TURN_PROOF_FIELD not in json.dumps(ledger.request, sort_keys=True)
    assert proof not in json.dumps(ledger.request, sort_keys=True)


def test_managed_skill_mcp_tools_are_scope_bound_and_consume_proof(workspace: Path) -> None:
    from apps.harness.models import BuildTurnGrant
    from tradingcodex_service.mcp_runtime import call_mcp_tool

    assert MANAGED_SKILL_PROTECTED_MCP_TOOL_SCOPES == EXPECTED_MANAGED_MCP_TOOL_SCOPES
    body_path = workspace / "strategy-permission-smoke.draft.md"
    body_path.write_text("# Permission Smoke\n\n## Thesis\n\nDraft only.\n", encoding="utf-8")
    strategy_prompt = "$tcx-strategy\nCreate strategy-permission-smoke as a draft."
    issued = issue_managed_skill_turn_grant(
        workspace,
        strategy_prompt,
        session_id="strategy-mcp-session",
        turn_id="strategy-mcp-turn",
        cwd=workspace,
        permission_mode="trading-research",
    )
    assert issued is not None and issued["authority_scope"] == "strategy"

    args = {
        "action": "create",
        "name": "strategy-permission-smoke",
        "description": "Permission-scoped Strategy smoke test.",
        "body_path": body_path.name,
        "language": "en",
        "status": "draft",
    }
    with pytest.raises(BuildInvocationError, match=r"\$tcx-strategy turn proof is required"):
        call_mcp_tool(
            workspace,
            "manage_strategy",
            args,
            transport_principal="head-manager",
        )
    with pytest.raises(PermissionError, match=r"\$tcx-brain"):
        reserve_build_turn_use(
            workspace,
            "strategy-mcp-session",
            "strategy-mcp-turn",
            "wrong-managed-scope",
            "manage_investment_brain",
            {"action": "list"},
            permission_mode="trading-research",
        )
    proof = reserve_build_turn_use(
        workspace,
        "strategy-mcp-session",
        "strategy-mcp-turn",
        "strategy-managed-tool",
        "manage_strategy",
        args,
        permission_mode="trading-research",
    )
    result = call_mcp_tool(
        workspace,
        "manage_strategy",
        {**args, BUILD_TURN_PROOF_FIELD: proof},
        transport_principal="head-manager",
    )
    assert result["action"] == "create"
    assert result["record"]["name"] == "strategy-permission-smoke"
    assert result["record"]["status"] == "draft"
    strategy_grant = workspace_grants(workspace).get(authority_scope="strategy")
    assert strategy_grant.status == BuildTurnGrant.STATUS_ACTIVE
    assert strategy_grant.use_count == 1
    assert strategy_grant.reservation_proof_hash == ""

    brain_prompt = "$tcx-brain\nList the managed Investment Brains."
    issued = issue_managed_skill_turn_grant(
        workspace,
        brain_prompt,
        session_id="brain-mcp-session",
        turn_id="brain-mcp-turn",
        cwd=workspace,
        permission_mode="trading-research",
    )
    assert issued is not None and issued["authority_scope"] == "brain"
    brain_args = {"action": "list"}
    with pytest.raises(BuildInvocationError, match=r"\$tcx-brain turn proof is required"):
        call_mcp_tool(
            workspace,
            "manage_investment_brain",
            brain_args,
            transport_principal="head-manager",
        )
    brain_proof = reserve_build_turn_use(
        workspace,
        "brain-mcp-session",
        "brain-mcp-turn",
        "brain-managed-tool",
        "manage_investment_brain",
        brain_args,
        permission_mode="trading-research",
    )
    brain_result = call_mcp_tool(
        workspace,
        "manage_investment_brain",
        {**brain_args, BUILD_TURN_PROOF_FIELD: brain_proof},
        transport_principal="head-manager",
    )
    assert brain_result["action"] == "list"
    assert brain_result["records"] == []


def test_managed_brain_mcp_rejects_private_git_sources_before_network(workspace: Path) -> None:
    from tradingcodex_service.mcp_runtime import call_mcp_tool

    issue_managed_skill_turn_grant(
        workspace,
        "$tcx-brain\nValidate the explicitly selected public source.",
        session_id="brain-private-source-session",
        turn_id="brain-private-source-turn",
        cwd=workspace,
        permission_mode="trading-research",
    )
    args = {
        "action": "validate",
        "git_source": "https://127.0.0.1/private/brain.git",
    }
    proof = reserve_build_turn_use(
        workspace,
        "brain-private-source-session",
        "brain-private-source-turn",
        "brain-private-source-tool",
        "manage_investment_brain",
        args,
        permission_mode="trading-research",
    )
    with pytest.raises(ValueError, match="public host"):
        call_mcp_tool(
            workspace,
            "manage_investment_brain",
            {**args, BUILD_TURN_PROOF_FIELD: proof},
            transport_principal="head-manager",
        )


def test_completed_protected_call_is_revoked_if_normal_finish_fails(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from apps.harness.models import BuildTurnGrant
    from tradingcodex_service import mcp_runtime

    arguments = {"broker_id": "finish-recovery"}
    issue(workspace)
    proof = reserve_build_turn_use(
        workspace,
        "build-session",
        "build-turn",
        "finish-recovery-tool",
        "validate_broker_connector_build",
        arguments,
    )
    raw_calls = 0

    def fake_raw_call(*_args, **_kwargs):
        nonlocal raw_calls
        raw_calls += 1
        return {"status": "ok"}

    def fail_normal_finish(*_args, **_kwargs):
        raise RuntimeError("simulated required finalization failure")

    monkeypatch.setattr(mcp_runtime, "raw_call_tool", fake_raw_call)
    monkeypatch.setattr(mcp_runtime, "finish_reserved_build_turn_use", fail_normal_finish)

    with pytest.raises(PermissionError, match="operation completed.*revoked fail-closed"):
        mcp_runtime.call_mcp_tool(
            workspace,
            "validate_broker_connector_build",
            {**arguments, BUILD_TURN_PROOF_FIELD: proof},
            transport_principal="head-manager",
        )
    assert raw_calls == 1
    grant = workspace_grants(workspace).get()
    assert grant.status == BuildTurnGrant.STATUS_REVOKED
    assert grant.service_started_at is None
    assert grant.reservation_tool_name == ""
    assert grant.reservation_arguments_hash == ""
    assert grant.reservation_proof_hash == ""
    assert grant.use_count == 1
    assert grant.metadata["terminal_reason"] == "finish_failed_after_service_completion"
    assert grant.metadata["finished_unfinalized"]["result_status"] == "ok"

    recovered = build_gateway.fail_closed_finalize_started_build_turn_use(
        workspace,
        grant.grant_id,
        "ok",
    )
    assert recovered["status"] == BuildTurnGrant.STATUS_REVOKED
    grant.refresh_from_db()
    assert grant.use_count == 1

    with pytest.raises(PermissionError, match="unique reserved .* proof"):
        mcp_runtime.call_mcp_tool(
            workspace,
            "validate_broker_connector_build",
            {**arguments, BUILD_TURN_PROOF_FIELD: proof},
            transport_principal="head-manager",
        )
    assert raw_calls == 1


def test_stdio_schema_failure_releases_started_build_reservation(workspace: Path) -> None:
    from apps.harness.models import BuildTurnGrant
    from tradingcodex_service.mcp_runtime import handle_mcp_rpc

    invalid_arguments = {"provider_id": "paper", "broker_id": "", "credential_ref": "env:SCHEMA_TEST"}
    issue(workspace)
    proof = reserve_build_turn_use(
        workspace,
        "build-session",
        "build-turn",
        "schema-invalid-tool",
        "register_broker_connector",
        invalid_arguments,
    )
    response = handle_mcp_rpc(
        workspace,
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "register_broker_connector",
                "arguments": {**invalid_arguments, BUILD_TURN_PROOF_FIELD: proof},
            },
        },
        transport_principal="head-manager",
    )
    assert response is not None and "error" in response
    grant = workspace_grants(workspace).get()
    assert grant.status == BuildTurnGrant.STATUS_ACTIVE
    assert grant.use_count == 1
    assert grant.reservation_proof_hash == ""
    reserve_build_turn_use(
        workspace,
        "build-session",
        "build-turn",
        "after-schema-error",
        "register_broker_connector",
        {"provider_id": "paper", "broker_id": "demo", "credential_ref": "env:SCHEMA_TEST"},
    )

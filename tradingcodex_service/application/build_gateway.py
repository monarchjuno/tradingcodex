from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

from django.db import transaction
from django.utils import timezone as django_timezone

from tradingcodex_service.application.audit import write_audit_event_if_available, write_audit_event_required
from tradingcodex_service.application.runtime import (
    ensure_runtime_database,
    workspace_context_payload,
    workspace_file_lock,
)
from tradingcodex_service.application.skill_invocations import (
    SkillInvocationError,
    has_visible_content,
    meaningful_lines,
    parse_first_meaningful_invocation,
    parse_line_invocation,
    raw_prompt,
)


BUILD_SKILL = "$tcx-build"
MANAGED_SKILL_SCOPES = {
    "$tcx-brain": "brain",
    "$tcx-strategy": "strategy",
}
AUTHORITY_SCOPE_MARKERS = {
    "build": BUILD_SKILL,
    **{scope: marker for marker, scope in MANAGED_SKILL_SCOPES.items()},
}
BUILD_TURN_PROOF_FIELD = "_build_turn_proof"
BUILD_TURN_GRANT_TTL = timedelta(hours=1)
BUILD_RESERVATION_TTL = timedelta(minutes=2)
BUILD_PROTECTED_MCP_TOOLS = frozenset(
    {
        "register_broker_connector",
        "validate_broker_connector_build",
    }
)
MANAGED_SKILL_PROTECTED_MCP_TOOL_SCOPES = {
    "manage_investment_brain": "brain",
    "manage_strategy": "strategy",
}
WORKSPACE_PROTECTED_MCP_TOOL_SCOPES = {
    **{tool_name: "build" for tool_name in BUILD_PROTECTED_MCP_TOOLS},
    **MANAGED_SKILL_PROTECTED_MCP_TOOL_SCOPES,
}
WORKSPACE_PROTECTED_MCP_TOOLS = frozenset(WORKSPACE_PROTECTED_MCP_TOOL_SCOPES)

_MAX_BUILD_PROMPT_BYTES = 20_000
_MAX_BINDING_LENGTH = 200
_MAX_PROOF_LENGTH = 256
_SAFE_TERMINAL_REASON = re.compile(r"^[a-z0-9_-]{1,80}$")
_FINISH_STATUSES = frozenset({"ok", "error"})


class BuildInvocationError(ValueError):
    """Raised when a reserved workspace invocation or binding is malformed."""


def parse_build_invocation(
    prompt: Any,
    workspace_root: Path | str | None = None,
) -> bool | None:
    """Recognize one Build invocation on the first meaningful prompt line."""

    marker = _parse_workspace_invocation(
        prompt,
        (BUILD_SKILL,),
        workspace_root=workspace_root,
    )
    return True if marker else None


def parse_managed_skill_invocation(
    prompt: Any,
    workspace_root: Path | str | None = None,
) -> str | None:
    """Return the capability scope for one managed-skill invocation."""

    marker = _parse_workspace_invocation(
        prompt,
        MANAGED_SKILL_SCOPES,
        workspace_root=workspace_root,
    )
    return MANAGED_SKILL_SCOPES[marker] if marker else None


def _parse_workspace_invocation(
    prompt: Any,
    markers: tuple[str, ...] | Mapping[str, str],
    *,
    workspace_root: Path | str | None,
) -> str:
    raw = raw_prompt(prompt)
    try:
        invocation = parse_first_meaningful_invocation(
            raw,
            markers,
            workspace_root=workspace_root,
        )
    except SkillInvocationError as exc:
        raise BuildInvocationError(str(exc)) from exc
    if invocation is None:
        return ""
    if len(raw.encode("utf-8")) > _MAX_BUILD_PROMPT_BYTES:
        raise BuildInvocationError(f"{invocation.marker} prompt is too long")

    lines = meaningful_lines(raw)
    request_head = invocation.tail or (lines[1][1] if len(lines) > 1 else "")
    if not has_visible_content(request_head):
        raise BuildInvocationError(f"{invocation.marker} requires a non-empty request")
    if unicodedata.category(request_head[0]).startswith("M"):
        raise BuildInvocationError(
            f"{invocation.marker} request cannot start with a combining character"
        )
    if request_head.startswith("-"):
        raise BuildInvocationError(f"{invocation.marker} request must not start with a flag")

    authority_markers = (
        BUILD_SKILL,
        *MANAGED_SKILL_SCOPES,
        "$tcx-order-allow",
        "$tcx-order-submit",
        "$tcx-order-cancel",
        "$execute-paper-order",
    )
    scan_lines = [invocation.tail] if invocation.tail else []
    scan_lines.extend(line for _, line in lines[1:])
    try:
        mixed = next(
            (
                found
                for line in scan_lines
                if (
                    found := parse_line_invocation(
                        line,
                        authority_markers,
                        workspace_root=workspace_root,
                    )
                )
            ),
            None,
        )
    except SkillInvocationError as exc:
        raise BuildInvocationError(str(exc)) from exc
    if mixed is not None:
        raise BuildInvocationError(
            f"{invocation.marker} cannot be combined with another Build, managed-skill, or order marker"
        )
    return invocation.marker


def issue_build_turn_grant(
    workspace_root: Path | str,
    prompt: Any,
    *,
    session_id: Any,
    turn_id: Any,
    cwd: Path | str | None = None,
    issued_at: datetime | None = None,
    permission_mode: Any = "",
) -> dict[str, Any] | None:
    """Issue or safely reuse one DB-canonical grant for the exact root turn."""

    root = Path(workspace_root).resolve()
    if parse_build_invocation(prompt, root) is None:
        return None
    return _issue_workspace_turn_grant(
        root,
        prompt,
        session_id=session_id,
        turn_id=turn_id,
        cwd=cwd,
        issued_at=issued_at,
        permission_mode=permission_mode,
        authority_scope="build",
        marker=BUILD_SKILL,
    )


def issue_managed_skill_turn_grant(
    workspace_root: Path | str,
    prompt: Any,
    *,
    session_id: Any,
    turn_id: Any,
    cwd: Path | str | None = None,
    issued_at: datetime | None = None,
    permission_mode: Any = "",
) -> dict[str, Any] | None:
    """Issue one capability-scoped grant from an exact managed-skill turn."""

    root = Path(workspace_root).resolve()
    authority_scope = parse_managed_skill_invocation(prompt, root)
    if authority_scope is None:
        return None
    marker = AUTHORITY_SCOPE_MARKERS[authority_scope]
    return _issue_workspace_turn_grant(
        root,
        prompt,
        session_id=session_id,
        turn_id=turn_id,
        cwd=cwd,
        issued_at=issued_at,
        permission_mode=permission_mode,
        authority_scope=authority_scope,
        marker=marker,
    )


def _issue_workspace_turn_grant(
    workspace_root: Path | str,
    prompt: Any,
    *,
    session_id: Any,
    turn_id: Any,
    cwd: Path | str | None,
    issued_at: datetime | None,
    permission_mode: Any,
    authority_scope: str,
    marker: str,
) -> dict[str, Any]:
    root = Path(workspace_root).resolve()
    if cwd is None or not str(cwd).strip():
        raise PermissionError(f"{marker} requires the generated workspace root cwd")
    try:
        resolved_cwd = Path(cwd).resolve()
    except (OSError, RuntimeError, ValueError) as exc:
        raise PermissionError(f"{marker} workspace cwd is invalid") from exc
    if resolved_cwd != root:
        raise PermissionError(f"{marker} must originate from the generated workspace root")
    session_hash = _secret_hash(_validate_binding_value("session_id", session_id))
    turn_hash = _secret_hash(_validate_binding_value("turn_id", turn_id))
    prompt_hash = hashlib.sha256(str(prompt).encode("utf-8")).hexdigest()
    normalized_permission_mode = _normalize_permission_mode(permission_mode)
    if normalized_permission_mode == "plan":
        raise PermissionError(f"{marker} managed changes are unavailable while Codex is in Plan mode")
    now = _aware_datetime(issued_at)
    expires_at = now + BUILD_TURN_GRANT_TTL
    context = workspace_context_payload(root)
    ensure_runtime_database(root)

    from apps.harness.models import BuildTurnGrant

    lock_name = f"workspace-turn-{context['path_hash']}-{session_hash}"
    with workspace_file_lock(root, lock_name):
        return _issue_build_turn_grant_locked(
            root,
            BuildTurnGrant,
            context=context,
            session_hash=session_hash,
            turn_hash=turn_hash,
            prompt_hash=prompt_hash,
            permission_mode=normalized_permission_mode,
            authority_scope=authority_scope,
            marker=marker,
            now=now,
            expires_at=expires_at,
        )


def _issue_build_turn_grant_locked(
    root: Path,
    grant_model: Any,
    *,
    context: Mapping[str, Any],
    session_hash: str,
    turn_hash: str,
    prompt_hash: str,
    permission_mode: str,
    authority_scope: str,
    marker: str,
    now: datetime,
    expires_at: datetime,
) -> dict[str, Any]:
    """Issue one grant while the workspace/session file lock is held."""

    error = ""
    grant: Any | None = None
    revoked: list[Any] = []
    expired: list[Any] = []
    with transaction.atomic():
        same_turn = list(
            grant_model.objects.select_for_update().filter(
                workspace_id=str(context["workspace_id"]),
                workspace_path_hash=str(context["path_hash"]),
                session_id_hash=session_hash,
                turn_id_hash=turn_hash,
            )
        )
        if len(same_turn) > 1:  # pragma: no cover - protected by the DB constraint
            error = "the current turn has multiple workspace grants"
        elif same_turn:
            candidate = same_turn[0]
            if candidate.status in {grant_model.STATUS_REVOKED, grant_model.STATUS_EXPIRED}:
                error = f"the current turn already has a terminal {marker} grant"
            elif candidate.expires_at <= now:
                if (
                    candidate.status == grant_model.STATUS_RESERVED
                    and candidate.service_started_at is not None
                ):
                    _request_revoke_after_finish(root, candidate, now, reason="expired")
                    error = "the protected workspace call is still in flight on an expired grant"
                else:
                    _expire_grant(candidate, now)
                    expired.append(candidate)
                    error = f"{marker} grant is expired for this turn"
            elif candidate.prompt_sha256 != prompt_hash:
                error = f"the current turn is already bound to another {marker} prompt"
            elif _grant_authority_scope(candidate) != authority_scope:
                error = "the current turn is already bound to another workspace authority scope"
            elif permission_mode != "unknown" and _grant_permission_mode(candidate) not in {"unknown", permission_mode}:
                error = "the current turn is already bound to another Codex permission mode"
            else:
                _recover_abandoned_reservation(root, candidate, now)
                grant = candidate

        active_session_grants = list(
            grant_model.objects.select_for_update().filter(
                workspace_id=str(context["workspace_id"]),
                workspace_path_hash=str(context["path_hash"]),
                session_id_hash=session_hash,
                status__in=[grant_model.STATUS_ACTIVE, grant_model.STATUS_RESERVED],
            )
        )
        for candidate in active_session_grants:
            if grant is not None and candidate.pk == grant.pk:
                continue
            if (
                candidate.status == grant_model.STATUS_RESERVED
                and candidate.service_started_at is not None
            ):
                reason = "expired" if candidate.expires_at <= now else "superseded_by_new_turn"
                _request_revoke_after_finish(root, candidate, now, reason=reason)
                if not (same_turn and candidate.pk == same_turn[0].pk):
                    error = "the previous protected workspace call is still in flight"
                continue
            if candidate.expires_at <= now:
                _expire_grant(candidate, now)
                if all(item.pk != candidate.pk for item in expired):
                    expired.append(candidate)
                continue
            _recover_abandoned_reservation(root, candidate, now)
            if same_turn and candidate.pk == same_turn[0].pk:
                continue
            _revoke_grant(candidate, now, reason="superseded_by_new_turn")
            revoked.append(candidate)

        if expired:
            _write_terminal_audit(root, expired, status="expired", reason="expired")
        if revoked:
            _write_terminal_audit(
                root,
                revoked,
                status="revoked",
                reason="superseded_by_new_turn",
            )

        if not error and grant is None:
            grant = grant_model.objects.create(
                grant_id=f"{authority_scope}-turn-" + secrets.token_urlsafe(24),
                authority_scope=authority_scope,
                status=grant_model.STATUS_ACTIVE,
                workspace_id=str(context["workspace_id"]),
                workspace_path_hash=str(context["path_hash"]),
                session_id_hash=session_hash,
                turn_id_hash=turn_hash,
                prompt_sha256=prompt_hash,
                issued_at=now,
                expires_at=expires_at,
                metadata={
                    "multi_use": True,
                    "permission_mode": permission_mode,
                    "entrypoint": marker,
                },
            )
            write_audit_event_required(
                root,
                "native-user",
                "native-hook",
                {
                    "type": _grant_audit_action(grant, "issued"),
                    "resource": grant.grant_id,
                    "decision": "issued",
                    "payload": _grant_audit_metadata(grant),
                },
            )

    if error:
        raise PermissionError(error)
    if grant is None:  # pragma: no cover - defensive guard
        raise PermissionError(f"{marker} grant could not be established")
    return _grant_projection(grant)


def authorize_local_build_tool(
    workspace_root: Path | str,
    session_id: Any,
    turn_id: Any,
    tool_use_id: Any,
    tool_name: Any,
    tool_input: Mapping[str, Any],
    *,
    permission_mode: Any = "",
    required_scope: str = "build",
) -> dict[str, Any]:
    """Authorize one local tool against the exact capability-scoped turn."""

    root = Path(workspace_root).resolve()
    session_hash = _secret_hash(_validate_binding_value("session_id", session_id))
    turn_hash = _secret_hash(_validate_binding_value("turn_id", turn_id))
    tool_use_hash = _secret_hash(_validate_binding_value("tool_use_id", tool_use_id))
    canonical_tool = _canonical_tool_name(tool_name)
    canonical_scope = _canonical_authority_scope(required_scope)
    normalized_permission_mode = _normalize_permission_mode(permission_mode)
    if canonical_tool in WORKSPACE_PROTECTED_MCP_TOOLS:
        raise BuildInvocationError("protected workspace MCP tools require a reserved hook-owned proof")
    arguments_hash = _build_arguments_hash(tool_input)
    now = django_timezone.now()
    context = workspace_context_payload(root)
    ensure_runtime_database(root)

    from apps.harness.models import BuildTurnGrant

    error = ""
    grant: BuildTurnGrant | None = None
    with transaction.atomic():
        grants = list(
            BuildTurnGrant.objects.select_for_update().filter(
                workspace_id=str(context["workspace_id"]),
                workspace_path_hash=str(context["path_hash"]),
                session_id_hash=session_hash,
                turn_id_hash=turn_hash,
                status__in=[BuildTurnGrant.STATUS_ACTIVE, BuildTurnGrant.STATUS_RESERVED],
            )
        )
        if len(grants) != 1:
            error = f"the current root turn has no unique active {_scope_marker(canonical_scope)} grant"
        else:
            grant = grants[0]
            scope_error = _grant_scope_error(grant, canonical_scope)
            permission_error = _grant_permission_mode_error(grant, normalized_permission_mode)
            if scope_error:
                error = scope_error
            elif permission_error:
                error = permission_error
            elif grant.status == BuildTurnGrant.STATUS_RESERVED and grant.service_started_at is not None:
                error = "another protected workspace tool is already in flight for this turn"
            elif grant.expires_at <= now:
                _expire_grant(grant, now)
                _write_terminal_audit(root, [grant], status="expired", reason="expired")
                error = f"{_scope_marker(canonical_scope)} grant is expired"
            else:
                _recover_abandoned_reservation(root, grant, now)
                if grant.status == BuildTurnGrant.STATUS_RESERVED:
                    error = "another protected workspace tool is already in flight for this turn"
                else:
                    grant.use_count += 1
                    grant.last_used_at = now
                    grant.save(update_fields=["use_count", "last_used_at"])
                    write_audit_event_required(
                        root,
                        "native-user",
                        "native-hook",
                        {
                            "type": _grant_audit_action(grant, "tool_allowed"),
                            "resource": grant.grant_id,
                            "decision": "allowed",
                            "payload": {
                                **_grant_audit_metadata(grant),
                                "tool_name": canonical_tool,
                                "tool_use_id_hash": tool_use_hash,
                                "arguments_hash": arguments_hash,
                                "use_count": grant.use_count,
                            },
                        },
                    )
    if error:
        raise PermissionError(error)
    if grant is None:  # pragma: no cover - defensive guard
        raise PermissionError(f"{_scope_marker(canonical_scope)} grant is unavailable")
    return _grant_projection(grant)


def has_active_build_turn_grant(
    workspace_root: Path | str,
    session_id: Any,
    turn_id: Any,
) -> bool:
    """Return whether the exact root turn still owns one active Build grant.

    Generated hooks use this read-only check to route *all* shell and edit
    tools in a Build turn through the Build hard-policy allowlist.  Content
    inspection alone is insufficient because an otherwise ordinary helper can
    hide provider imports or network effects.
    """

    root = Path(workspace_root).resolve()
    session_hash = _secret_hash(_validate_binding_value("session_id", session_id))
    turn_hash = _secret_hash(_validate_binding_value("turn_id", turn_id))
    context = workspace_context_payload(root)
    ensure_runtime_database(root)

    from apps.harness.models import BuildTurnGrant

    now = django_timezone.now()
    grants = list(
        BuildTurnGrant.objects.filter(
            workspace_id=str(context["workspace_id"]),
            workspace_path_hash=str(context["path_hash"]),
            session_id_hash=session_hash,
            turn_id_hash=turn_hash,
            authority_scope="build",
            status__in=[BuildTurnGrant.STATUS_ACTIVE, BuildTurnGrant.STATUS_RESERVED],
            expires_at__gt=now,
        ).values_list("pk", flat=True)[:2]
    )
    return len(grants) == 1


def validate_local_build_permission(
    workspace_root: Path | str,
    session_id: Any,
    turn_id: Any,
    tool_name: Any,
    tool_input: Mapping[str, Any],
    *,
    permission_mode: Any = "",
    required_scope: str = "build",
) -> dict[str, Any]:
    """Validate a local Build permission request without consuming tool use."""

    return _validate_build_permission(
        workspace_root,
        session_id,
        turn_id,
        tool_name,
        tool_input,
        permission_mode=permission_mode,
        protected_mcp=False,
        required_scope=required_scope,
    )


def validate_build_mcp_permission(
    workspace_root: Path | str,
    session_id: Any,
    turn_id: Any,
    tool_name: Any,
    tool_input: Mapping[str, Any],
    *,
    permission_mode: Any = "",
) -> dict[str, Any]:
    """Validate scoped protected MCP intent before Codex presents permission UI.

    The public name remains for generated-hook compatibility.
    """

    canonical_tool = _canonical_protected_tool_name(tool_name)

    return _validate_build_permission(
        workspace_root,
        session_id,
        turn_id,
        canonical_tool,
        tool_input,
        permission_mode=permission_mode,
        protected_mcp=True,
        required_scope=_protected_tool_scope(canonical_tool),
    )


def _validate_build_permission(
    workspace_root: Path | str,
    session_id: Any,
    turn_id: Any,
    tool_name: Any,
    tool_input: Mapping[str, Any],
    *,
    permission_mode: Any,
    protected_mcp: bool,
    required_scope: str,
) -> dict[str, Any]:
    """Validate a Build permission request without reserving or counting use.

    PermissionRequest is advisory in Codex: returning no decision preserves the
    configured sandbox/approval flow. This check prevents an unrelated turn
    from presenting a misleading approval prompt, while PreToolUse performs the
    final, independently bound authorization immediately before execution.
    """

    root = Path(workspace_root).resolve()
    session_hash = _secret_hash(_validate_binding_value("session_id", session_id))
    turn_hash = _secret_hash(_validate_binding_value("turn_id", turn_id))
    canonical_tool = (
        _canonical_protected_tool_name(tool_name)
        if protected_mcp
        else _canonical_tool_name(tool_name)
    )
    normalized_permission_mode = _normalize_permission_mode(permission_mode)
    canonical_scope = _canonical_authority_scope(required_scope)
    if not protected_mcp and canonical_tool in WORKSPACE_PROTECTED_MCP_TOOLS:
        raise BuildInvocationError("protected workspace MCP tools require a reserved hook-owned proof")
    _build_arguments_hash(tool_input)
    now = django_timezone.now()
    context = workspace_context_payload(root)
    ensure_runtime_database(root)

    from apps.harness.models import BuildTurnGrant

    error = ""
    grant: BuildTurnGrant | None = None
    with transaction.atomic():
        grants = list(
            BuildTurnGrant.objects.select_for_update().filter(
                workspace_id=str(context["workspace_id"]),
                workspace_path_hash=str(context["path_hash"]),
                session_id_hash=session_hash,
                turn_id_hash=turn_hash,
                status__in=[BuildTurnGrant.STATUS_ACTIVE, BuildTurnGrant.STATUS_RESERVED],
            )
        )
        if len(grants) != 1:
            error = f"the current root turn has no unique active {_scope_marker(canonical_scope)} grant"
        else:
            grant = grants[0]
            scope_error = _grant_scope_error(grant, canonical_scope)
            permission_error = _grant_permission_mode_error(grant, normalized_permission_mode)
            if scope_error:
                error = scope_error
            elif permission_error:
                error = permission_error
            elif grant.status == BuildTurnGrant.STATUS_RESERVED and grant.service_started_at is not None:
                error = "another protected workspace tool is already in flight for this turn"
            elif grant.expires_at <= now:
                _expire_grant(grant, now)
                _write_terminal_audit(root, [grant], status="expired", reason="expired")
                error = f"{_scope_marker(canonical_scope)} grant is expired"
            else:
                _recover_abandoned_reservation(root, grant, now)
                if grant.status == BuildTurnGrant.STATUS_RESERVED:
                    error = "another protected workspace tool is already in flight for this turn"
    if error:
        raise PermissionError(error)
    if grant is None:  # pragma: no cover - defensive guard
        raise PermissionError(f"{_scope_marker(canonical_scope)} grant is unavailable")
    return _grant_projection(grant)


def reserve_build_turn_use(
    workspace_root: Path | str,
    session_id: Any,
    turn_id: Any,
    tool_use_id: Any,
    tool_name: Any,
    args: Mapping[str, Any],
    *,
    permission_mode: Any = "",
) -> str:
    """Reserve the matching workspace grant for one protected MCP call.

    The public name remains for generated-hook compatibility.
    """

    root = Path(workspace_root).resolve()
    session_hash = _secret_hash(_validate_binding_value("session_id", session_id))
    turn_hash = _secret_hash(_validate_binding_value("turn_id", turn_id))
    tool_use_hash = _secret_hash(_validate_binding_value("tool_use_id", tool_use_id))
    canonical_tool = _canonical_protected_tool_name(tool_name)
    required_scope = _protected_tool_scope(canonical_tool)
    marker = _scope_marker(required_scope)
    normalized_permission_mode = _normalize_permission_mode(permission_mode)
    arguments_hash = _build_arguments_hash(args)
    proof = secrets.token_urlsafe(32)
    now = django_timezone.now()
    context = workspace_context_payload(root)
    ensure_runtime_database(root)

    from apps.harness.models import BuildTurnGrant

    error = ""
    with transaction.atomic():
        grants = list(
            BuildTurnGrant.objects.select_for_update().filter(
                workspace_id=str(context["workspace_id"]),
                workspace_path_hash=str(context["path_hash"]),
                session_id_hash=session_hash,
                turn_id_hash=turn_hash,
                status__in=[BuildTurnGrant.STATUS_ACTIVE, BuildTurnGrant.STATUS_RESERVED],
            )
        )
        if len(grants) != 1:
            error = f"the current root turn has no unique active {marker} grant"
        else:
            grant = grants[0]
            scope_error = _grant_scope_error(grant, required_scope)
            permission_error = _grant_permission_mode_error(grant, normalized_permission_mode)
            if scope_error:
                error = scope_error
            elif permission_error:
                error = permission_error
            elif grant.status == BuildTurnGrant.STATUS_RESERVED and grant.service_started_at is not None:
                error = "another protected workspace tool is already in flight for this turn"
            elif grant.expires_at <= now:
                _expire_grant(grant, now)
                _write_terminal_audit(root, [grant], status="expired", reason="expired")
                error = f"{marker} grant is expired"
            else:
                _recover_abandoned_reservation(root, grant, now)
                if grant.status == BuildTurnGrant.STATUS_RESERVED:
                    error = "another protected workspace tool is already in flight for this turn"
                else:
                    updated = BuildTurnGrant.objects.filter(
                        pk=grant.pk,
                        status=BuildTurnGrant.STATUS_ACTIVE,
                    ).update(
                        status=BuildTurnGrant.STATUS_RESERVED,
                        reserved_at=now,
                        service_started_at=None,
                        reservation_tool_use_id_hash=tool_use_hash,
                        reservation_tool_name=canonical_tool,
                        reservation_arguments_hash=arguments_hash,
                        reservation_proof_hash=_secret_hash(proof),
                    )
                    if updated != 1:
                        error = f"{marker} grant was already reserved"
                    else:
                        grant.status = BuildTurnGrant.STATUS_RESERVED
                        grant.reserved_at = now
                        grant.service_started_at = None
                        grant.reservation_tool_use_id_hash = tool_use_hash
                        grant.reservation_tool_name = canonical_tool
                        grant.reservation_arguments_hash = arguments_hash
                        grant.reservation_proof_hash = _secret_hash(proof)
                        write_audit_event_required(
                            root,
                            "native-user",
                            "native-hook",
                            {
                                "type": _grant_audit_action(grant, "tool_reserved"),
                                "resource": grant.grant_id,
                                "decision": "reserved",
                                "payload": {
                                    **_grant_audit_metadata(grant),
                                    "tool_name": canonical_tool,
                                    "tool_use_id_hash": tool_use_hash,
                                    "arguments_hash": arguments_hash,
                                },
                            },
                        )
    if error:
        raise PermissionError(error)
    return proof


def begin_reserved_build_turn_use(
    workspace_root: Path | str,
    tool_name: Any,
    args: Mapping[str, Any],
    proof: Any,
) -> str:
    """Atomically consume a scoped hook proof and begin one protected call."""

    root = Path(workspace_root).resolve()
    canonical_tool = _canonical_protected_tool_name(tool_name)
    required_scope = _protected_tool_scope(canonical_tool)
    marker = _scope_marker(required_scope)
    arguments_hash = _build_arguments_hash(args)
    proof_hash = _secret_hash(
        _validate_binding_value("Build turn proof", proof, max_length=_MAX_PROOF_LENGTH)
    )
    now = django_timezone.now()
    context = workspace_context_payload(root)
    ensure_runtime_database(root)

    from apps.harness.models import BuildTurnGrant

    error = ""
    grant_id = ""
    with transaction.atomic():
        grants = list(
            BuildTurnGrant.objects.select_for_update().filter(
                workspace_id=str(context["workspace_id"]),
                workspace_path_hash=str(context["path_hash"]),
                status=BuildTurnGrant.STATUS_RESERVED,
                reservation_proof_hash=proof_hash,
            )
        )
        if len(grants) != 1:
            error = f"a unique reserved {marker} proof is required"
        else:
            grant = grants[0]
            scope_error = _grant_scope_error(grant, required_scope)
            if scope_error:
                error = scope_error
            elif grant.expires_at <= now:
                _expire_grant(grant, now)
                _write_terminal_audit(root, [grant], status="expired", reason="expired")
                error = f"{marker} grant is expired"
            elif _recover_abandoned_reservation(root, grant, now):
                error = f"{marker} proof reservation expired before service start"
            elif grant.service_started_at is not None:
                error = f"{marker} proof was already started"
            elif not hmac.compare_digest(grant.reservation_tool_name, canonical_tool) or not hmac.compare_digest(
                grant.reservation_arguments_hash,
                arguments_hash,
            ):
                error = f"{marker} proof does not match the reserved tool call"
            else:
                updated = BuildTurnGrant.objects.filter(
                    pk=grant.pk,
                    status=BuildTurnGrant.STATUS_RESERVED,
                    reservation_proof_hash=proof_hash,
                    service_started_at__isnull=True,
                ).update(
                    service_started_at=now,
                    reservation_proof_hash="",
                )
                if updated != 1:
                    error = f"{marker} proof was already started"
                else:
                    grant.service_started_at = now
                    grant.reservation_proof_hash = ""
                    write_audit_event_required(
                        root,
                        "native-user",
                        "tradingcodex-mcp",
                        {
                            "type": _grant_audit_action(grant, "tool_started"),
                            "resource": grant.grant_id,
                            "decision": "started",
                            "payload": {
                                **_grant_audit_metadata(grant),
                                "tool_name": canonical_tool,
                                "tool_use_id_hash": grant.reservation_tool_use_id_hash,
                                "arguments_hash": arguments_hash,
                            },
                        },
                    )
                    grant_id = str(grant.grant_id)
    if error:
        raise PermissionError(error)
    if not grant_id:  # pragma: no cover - defensive guard
        raise PermissionError(f"reserved {marker} use is unavailable")
    return grant_id


def finish_reserved_build_turn_use(
    workspace_root: Path | str,
    grant_id: Any,
    result_status: str,
) -> dict[str, Any]:
    """Finish one protected service call and return the multi-use grant to active."""

    canonical_grant_id = _validate_binding_value("Workspace grant id", grant_id, max_length=80)
    normalized_result = str(result_status or "").strip().lower()
    if normalized_result not in _FINISH_STATUSES:
        raise BuildInvocationError("Workspace use result_status must be ok or error")
    root = Path(workspace_root).resolve()
    now = django_timezone.now()
    context = workspace_context_payload(root)
    ensure_runtime_database(root)

    from apps.harness.models import BuildTurnGrant

    error = ""
    grant: BuildTurnGrant | None = None
    with transaction.atomic():
        grants = list(
            BuildTurnGrant.objects.select_for_update().filter(
                grant_id=canonical_grant_id,
                workspace_id=str(context["workspace_id"]),
                workspace_path_hash=str(context["path_hash"]),
                status=BuildTurnGrant.STATUS_RESERVED,
                service_started_at__isnull=False,
                reservation_proof_hash="",
            )
        )
        if len(grants) != 1:
            error = "the reserved workspace use is no longer active"
        else:
            grant = grants[0]
            tool_name = grant.reservation_tool_name
            tool_use_id_hash = grant.reservation_tool_use_id_hash
            arguments_hash = grant.reservation_arguments_hash
            grant.use_count += 1
            grant.last_used_at = now
            expired = grant.expires_at <= now
            metadata = dict(grant.metadata or {})
            pending_reason = str(metadata.pop("revoke_after_finish", "") or "")
            terminal_reason = "expired" if expired else pending_reason
            if expired:
                grant.status = BuildTurnGrant.STATUS_EXPIRED
            elif pending_reason:
                grant.status = BuildTurnGrant.STATUS_REVOKED
            else:
                grant.status = BuildTurnGrant.STATUS_ACTIVE
            if expired:
                grant.revoked_at = now
            elif pending_reason:
                grant.revoked_at = now
            if terminal_reason:
                metadata["terminal_reason"] = terminal_reason
            grant.metadata = metadata
            _clear_reservation(grant)
            grant.save(
                update_fields=[
                    "status",
                    "revoked_at",
                    "reserved_at",
                    "service_started_at",
                    "reservation_tool_use_id_hash",
                    "reservation_tool_name",
                    "reservation_arguments_hash",
                    "reservation_proof_hash",
                    "use_count",
                    "last_used_at",
                    "metadata",
                ]
            )
            write_audit_event_required(
                root,
                "native-user",
                "tradingcodex-mcp",
                {
                    "type": _grant_audit_action(grant, "tool_finished"),
                    "resource": grant.grant_id,
                    "decision": normalized_result,
                    "payload": {
                        **_grant_audit_metadata(grant),
                        "tool_name": tool_name,
                        "tool_use_id_hash": tool_use_id_hash,
                        "arguments_hash": arguments_hash,
                        "result_status": normalized_result,
                        "use_count": grant.use_count,
                    },
                },
            )
            if terminal_reason:
                _write_terminal_audit(
                    root,
                    [grant],
                    status="expired" if expired else "revoked",
                    reason=terminal_reason,
                )
    if error:
        raise PermissionError(error)
    if grant is None:  # pragma: no cover - defensive guard
        raise PermissionError("reserved workspace use is unavailable")
    return _grant_projection(grant)


def fail_closed_finalize_started_build_turn_use(
    workspace_root: Path | str,
    grant_id: Any,
    result_status: str,
) -> dict[str, Any]:
    """Revoke a started grant when normal post-effect finalization fails.

    This recovery path never invokes the protected operation. It is idempotent
    for the same grant/result and intentionally uses only the canonical DB so a
    failed audit sink cannot leave a completed service effect permanently in
    the in-flight state.
    """

    canonical_grant_id = _validate_binding_value("Workspace grant id", grant_id, max_length=80)
    normalized_result = str(result_status or "").strip().lower()
    if normalized_result not in _FINISH_STATUSES:
        raise BuildInvocationError("Workspace use result_status must be ok or error")
    root = Path(workspace_root).resolve()
    now = django_timezone.now()
    context = workspace_context_payload(root)
    ensure_runtime_database(root)

    from apps.harness.models import BuildTurnGrant

    recovered = False
    grant: BuildTurnGrant | None = None
    recovery_payload: dict[str, Any] = {}
    with transaction.atomic():
        grant = (
            BuildTurnGrant.objects.select_for_update()
            .filter(
                grant_id=canonical_grant_id,
                workspace_id=str(context["workspace_id"]),
                workspace_path_hash=str(context["path_hash"]),
            )
            .first()
        )
        if grant is None:
            raise PermissionError("the completed workspace use cannot be recovered")
        metadata = dict(grant.metadata or {})
        prior_recovery = metadata.get("finished_unfinalized")
        if isinstance(prior_recovery, dict):
            if (
                str(prior_recovery.get("result_status") or "") != normalized_result
                or grant.status != BuildTurnGrant.STATUS_REVOKED
                or grant.service_started_at is not None
                or grant.reservation_proof_hash
            ):
                raise PermissionError("the completed workspace recovery result does not match")
            return _grant_projection(grant)
        if (
            grant.status != BuildTurnGrant.STATUS_RESERVED
            or grant.service_started_at is None
            or grant.reservation_proof_hash
        ):
            raise PermissionError("the completed workspace use is not recoverable")
        tool_name = str(grant.reservation_tool_name)
        tool_use_id_hash = str(grant.reservation_tool_use_id_hash)
        arguments_hash = str(grant.reservation_arguments_hash)
        pending_reason = str(metadata.pop("revoke_after_finish", "") or "")
        recovery_payload = {
            "recovered_at": _iso(now),
            "result_status": normalized_result,
            "tool_name": tool_name,
            "tool_use_id_hash": tool_use_id_hash,
            "arguments_hash": arguments_hash,
            "pending_terminal_reason": pending_reason,
        }
        metadata.update(
            {
                "terminal_reason": "finish_failed_after_service_completion",
                "finished_unfinalized": recovery_payload,
            }
        )
        grant.status = BuildTurnGrant.STATUS_REVOKED
        grant.revoked_at = now
        grant.use_count += 1
        grant.last_used_at = now
        grant.metadata = metadata
        _clear_reservation(grant)
        grant.save(
            update_fields=[
                "status",
                "revoked_at",
                "reserved_at",
                "service_started_at",
                "reservation_tool_use_id_hash",
                "reservation_tool_name",
                "reservation_arguments_hash",
                "reservation_proof_hash",
                "use_count",
                "last_used_at",
                "metadata",
            ]
        )
        recovered = True
    if recovered:
        write_audit_event_if_available(
            root,
            "native-user",
            "tradingcodex-mcp",
            {
                "type": _grant_audit_action(grant, "tool_finish_recovered"),
                "resource": canonical_grant_id,
                "decision": "revoked",
                "payload": {
                    **_grant_audit_metadata(grant),
                    **recovery_payload,
                    "retry_safe": False,
                },
            },
        )
    if grant is None:  # pragma: no cover - defensive guard
        raise PermissionError("the completed workspace use cannot be recovered")
    return _grant_projection(grant)


def revoke_build_turn_grants(
    workspace_root: Path | str,
    session_id: Any,
    *,
    turn_id: Any | None = None,
    reason: str = "stop",
) -> int:
    """Revoke active or in-flight workspace grants for a session or exact turn.

    The public name remains for generated-hook compatibility.
    """

    root = Path(workspace_root).resolve()
    session_hash = _secret_hash(_validate_binding_value("session_id", session_id))
    turn_hash = (
        _secret_hash(_validate_binding_value("turn_id", turn_id))
        if turn_id not in (None, "")
        else ""
    )
    safe_reason = str(reason or "").strip().lower()
    if not _SAFE_TERMINAL_REASON.fullmatch(safe_reason):
        raise BuildInvocationError("Workspace grant revocation reason must be a compact identifier")
    context = workspace_context_payload(root)
    ensure_runtime_database(root)

    from apps.harness.models import BuildTurnGrant

    filters: dict[str, Any] = {
        "workspace_id": str(context["workspace_id"]),
        "workspace_path_hash": str(context["path_hash"]),
        "session_id_hash": session_hash,
        "status__in": [BuildTurnGrant.STATUS_ACTIVE, BuildTurnGrant.STATUS_RESERVED],
    }
    if turn_hash:
        filters["turn_id_hash"] = turn_hash
    now = django_timezone.now()
    revoked: list[BuildTurnGrant] = []
    expired: list[BuildTurnGrant] = []
    pending = 0
    with transaction.atomic():
        grants = list(BuildTurnGrant.objects.select_for_update().filter(**filters))
        for grant in grants:
            if (
                grant.status == BuildTurnGrant.STATUS_RESERVED
                and grant.service_started_at is not None
            ):
                _request_revoke_after_finish(root, grant, now, reason=safe_reason)
                pending += 1
            elif grant.expires_at <= now:
                _expire_grant(grant, now)
                expired.append(grant)
            else:
                _recover_abandoned_reservation(root, grant, now)
                _revoke_grant(grant, now, reason=safe_reason)
                revoked.append(grant)
        if expired:
            _write_terminal_audit(root, expired, status="expired", reason="expired")
        if revoked:
            _write_terminal_audit(root, revoked, status="revoked", reason=safe_reason)
    return len(revoked) + len(expired) + pending


def _canonical_tool_name(value: Any) -> str:
    text = _validate_binding_value("tool_name", value, max_length=180).lower()
    prefix = "mcp__tradingcodex__"
    return text[len(prefix):] if text.startswith(prefix) else text


def _canonical_protected_tool_name(value: Any) -> str:
    tool_name = _canonical_tool_name(value)
    if tool_name not in WORKSPACE_PROTECTED_MCP_TOOLS:
        raise BuildInvocationError(f"tool is not a protected workspace MCP operation: {tool_name}")
    return tool_name


def _protected_tool_scope(tool_name: Any) -> str:
    canonical_tool = _canonical_protected_tool_name(tool_name)
    return _canonical_authority_scope(WORKSPACE_PROTECTED_MCP_TOOL_SCOPES[canonical_tool])


def _canonical_build_arguments(args: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(args, Mapping):
        raise BuildInvocationError("Workspace tool arguments must be an object")
    canonical: dict[str, Any] = {}
    for raw_key, value in args.items():
        if not isinstance(raw_key, str):
            raise BuildInvocationError("Workspace tool argument names must be strings")
        if raw_key in {BUILD_TURN_PROOF_FIELD, "principal_id"}:
            continue
        if raw_key.startswith("_"):
            raise BuildInvocationError(f"unsupported private workspace tool field: {raw_key}")
        canonical[raw_key] = value
    return canonical


def _build_arguments_hash(args: Mapping[str, Any]) -> str:
    canonical = _canonical_build_arguments(args)
    try:
        encoded = json.dumps(
            canonical,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise BuildInvocationError("Workspace tool arguments must be canonical JSON") from exc
    return hashlib.sha256(encoded).hexdigest()


def _grant_projection(grant: Any) -> dict[str, Any]:
    projection = {
        "marker": "tradingcodex-build-turn-grant",
        "status": str(grant.status),
        "expires_at": _iso(grant.expires_at),
        "multi_use": True,
        "use_count": int(grant.use_count),
    }
    scope = _grant_authority_scope(grant)
    if scope != "build":
        projection.update(
            {
                "marker": "tradingcodex-managed-skill-turn-grant",
                "authority_scope": scope,
                "entrypoint": _scope_marker(scope),
            }
        )
    return projection


def _grant_audit_metadata(grant: Any) -> dict[str, Any]:
    return {
        "grant_id": str(grant.grant_id),
        "authority_scope": _grant_authority_scope(grant),
        "entrypoint": _scope_marker(_grant_authority_scope(grant)),
        "status": str(grant.status),
        "multi_use": True,
        "workspace_id": str(grant.workspace_id),
        "workspace_path_hash": str(grant.workspace_path_hash),
        "session_id_hash": str(grant.session_id_hash),
        "turn_id_hash": str(grant.turn_id_hash),
        "prompt_sha256": str(grant.prompt_sha256),
        "issued_at": _iso(grant.issued_at),
        "expires_at": _iso(grant.expires_at),
        "permission_mode": _grant_permission_mode(grant),
    }


def _normalize_permission_mode(value: Any) -> str:
    text = str(value or "").strip().lower().replace("_", "-")
    if not text:
        return "unknown"
    if len(text) > 40 or any(
        character.isspace() or unicodedata.category(character).startswith("C")
        for character in text
    ):
        raise BuildInvocationError("Codex permission_mode must be one compact token")
    if text in {"plan", "planning"}:
        return "plan"
    if text in {"workspace-write", "workspace-writable"}:
        return "workspace-write"
    if text in {"read-only", "readonly"}:
        return "read-only"
    if text in {"danger-full-access", "full-access", "unrestricted"}:
        return "full-access"
    return text


def _grant_permission_mode(grant: Any) -> str:
    metadata = grant.metadata if isinstance(getattr(grant, "metadata", None), dict) else {}
    return _normalize_permission_mode(metadata.get("permission_mode"))


def _grant_permission_mode_error(grant: Any, current_mode: str) -> str:
    marker = _scope_marker(_grant_authority_scope(grant))
    if current_mode == "plan":
        return f"{marker} managed tools are unavailable while Codex is in Plan mode"
    granted_mode = _grant_permission_mode(grant)
    if current_mode != "unknown" and granted_mode not in {"unknown", current_mode}:
        return f"the current {marker} grant is bound to another Codex permission mode"
    return ""


def _canonical_authority_scope(value: Any) -> str:
    scope = str(value or "").strip().lower()
    if scope not in AUTHORITY_SCOPE_MARKERS:
        raise BuildInvocationError(f"unknown workspace authority scope: {scope or 'empty'}")
    return scope


def _grant_authority_scope(grant: Any) -> str:
    return _canonical_authority_scope(getattr(grant, "authority_scope", "build") or "build")


def _scope_marker(scope: Any) -> str:
    return AUTHORITY_SCOPE_MARKERS[_canonical_authority_scope(scope)]


def _grant_scope_error(grant: Any, required_scope: Any) -> str:
    actual = _grant_authority_scope(grant)
    expected = _canonical_authority_scope(required_scope)
    if actual == expected:
        return ""
    return (
        f"the current {_scope_marker(actual)} grant cannot authorize {_scope_marker(expected)} work; "
        f"start a new root turn whose first meaningful line invokes {_scope_marker(expected)}"
    )


def _grant_audit_action(grant: Any, suffix: str) -> str:
    prefix = "build.turn_grant" if _grant_authority_scope(grant) == "build" else "managed_skill.turn_grant"
    return f"{prefix}.{suffix}"


def _write_terminal_audit(
    root: Path,
    grants: list[Any],
    *,
    status: str,
    reason: str,
) -> None:
    write_audit_event_required(
        root,
        "native-user",
        "native-hook",
        {
            "type": _grant_audit_action(grants[0], status),
            "resource": "session",
            "decision": status,
            "payload": {
                "reason": reason,
                "grant_count": len(grants),
                "grant_id_hashes": [_secret_hash(str(grant.grant_id)) for grant in grants],
                "workspace_id": str(grants[0].workspace_id),
                "workspace_path_hash": str(grants[0].workspace_path_hash),
            },
        },
    )


def _recover_abandoned_reservation(root: Path, grant: Any, now: datetime) -> bool:
    """Return an unstarted, expired reservation lease to the active grant."""

    if grant.status != grant.STATUS_RESERVED or grant.service_started_at is not None:
        return False
    if grant.reserved_at is not None and grant.reserved_at > now - BUILD_RESERVATION_TTL:
        return False
    tool_name = str(grant.reservation_tool_name)
    tool_use_id_hash = str(grant.reservation_tool_use_id_hash)
    arguments_hash = str(grant.reservation_arguments_hash)
    metadata = dict(grant.metadata or {})
    metadata["last_reservation_terminal_reason"] = "reservation_lease_expired"
    metadata["last_reservation_expired_at"] = _iso(now)
    grant.status = grant.STATUS_ACTIVE
    grant.metadata = metadata
    _clear_reservation(grant)
    grant.save(
        update_fields=[
            "status",
            "reserved_at",
            "service_started_at",
            "reservation_tool_use_id_hash",
            "reservation_tool_name",
            "reservation_arguments_hash",
            "reservation_proof_hash",
            "metadata",
        ]
    )
    write_audit_event_required(
        root,
        "native-user",
        "native-hook",
        {
            "type": _grant_audit_action(grant, "reservation_expired"),
            "resource": grant.grant_id,
            "decision": "released",
            "payload": {
                **_grant_audit_metadata(grant),
                "tool_name": tool_name,
                "tool_use_id_hash": tool_use_id_hash,
                "arguments_hash": arguments_hash,
                "reservation_ttl_seconds": int(BUILD_RESERVATION_TTL.total_seconds()),
            },
        },
    )
    return True


def _request_revoke_after_finish(
    root: Path,
    grant: Any,
    now: datetime,
    *,
    reason: str,
) -> None:
    """Defer revocation without invalidating an already-started service call."""

    metadata = dict(grant.metadata or {})
    if metadata.get("revoke_after_finish"):
        return
    metadata["revoke_after_finish"] = reason
    metadata["revoke_requested_at"] = _iso(now)
    grant.metadata = metadata
    grant.save(update_fields=["metadata"])
    write_audit_event_required(
        root,
        "native-user",
        "native-hook",
        {
            "type": _grant_audit_action(grant, "revocation_deferred"),
            "resource": grant.grant_id,
            "decision": "pending",
            "payload": {
                **_grant_audit_metadata(grant),
                "reason": reason,
                "tool_name": str(grant.reservation_tool_name),
                "tool_use_id_hash": str(grant.reservation_tool_use_id_hash),
                "arguments_hash": str(grant.reservation_arguments_hash),
            },
        },
    )


def _expire_grant(grant: Any, now: datetime) -> None:
    grant.status = grant.STATUS_EXPIRED
    grant.revoked_at = now
    grant.metadata = {**dict(grant.metadata or {}), "terminal_reason": "expired"}
    _clear_reservation(grant)
    grant.save(
        update_fields=[
            "status",
            "revoked_at",
            "reserved_at",
            "service_started_at",
            "reservation_tool_use_id_hash",
            "reservation_tool_name",
            "reservation_arguments_hash",
            "reservation_proof_hash",
            "metadata",
        ]
    )


def _revoke_grant(grant: Any, now: datetime, *, reason: str) -> None:
    grant.status = grant.STATUS_REVOKED
    grant.revoked_at = now
    grant.metadata = {**dict(grant.metadata or {}), "terminal_reason": reason}
    _clear_reservation(grant)
    grant.save(
        update_fields=[
            "status",
            "revoked_at",
            "reserved_at",
            "service_started_at",
            "reservation_tool_use_id_hash",
            "reservation_tool_name",
            "reservation_arguments_hash",
            "reservation_proof_hash",
            "metadata",
        ]
    )


def _clear_reservation(grant: Any) -> None:
    grant.reserved_at = None
    grant.service_started_at = None
    grant.reservation_tool_use_id_hash = ""
    grant.reservation_tool_name = ""
    grant.reservation_arguments_hash = ""
    grant.reservation_proof_hash = ""


def _validate_binding_value(field_name: str, value: Any, *, max_length: int = _MAX_BINDING_LENGTH) -> str:
    text = str(value or "")
    if not text or text != text.strip():
        raise BuildInvocationError(f"{field_name} is required")
    if len(text) > max_length:
        raise BuildInvocationError(f"{field_name} is too long")
    if any(character.isspace() or unicodedata.category(character).startswith("C") for character in text):
        raise BuildInvocationError(f"{field_name} must be one printable token")
    return text


def _secret_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _aware_datetime(value: datetime | None) -> datetime:
    if value is None:
        return django_timezone.now()
    if not isinstance(value, datetime):
        raise TypeError("issued_at must be a datetime")
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

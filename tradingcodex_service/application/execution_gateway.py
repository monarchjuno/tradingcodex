from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import shlex
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

from django.db import transaction
from django.utils import timezone as django_timezone

from tradingcodex_service.application.audit import (
    write_audit_event_if_available,
    write_audit_event_required,
)
from tradingcodex_service.application.common import now_iso
from tradingcodex_service.application.runtime import ensure_runtime_database, workspace_context_payload
from tradingcodex_service.application.skill_invocations import (
    SkillInvocationError,
    has_visible_content,
    meaningful_lines,
    parse_first_meaningful_invocation,
    parse_line_invocation,
    raw_prompt,
)


EXECUTE_APPROVED_ORDER_SKILL = "$tcx-order-submit"
CANCEL_SUBMITTED_ORDER_SKILL = "$tcx-order-cancel"
RETIRED_EXECUTION_SKILL = "$execute-paper-order"
ORDER_ALLOW_SKILL = "$tcx-order-allow"

NATIVE_USER_PRINCIPAL_ID = "native-user"
NATIVE_USER_ROLE = "user"
NATIVE_SUBMIT_ACTION = "execution.submit_approved_order"
NATIVE_CANCEL_ACTION = "execution.cancel_submitted_order"
NATIVE_EXECUTION_SOURCE = "native_codex_user_prompt"
NATIVE_TURN_GRANT_SOURCE = "native_codex_turn_grant"

ORDER_TURN_GRANT_TTL = timedelta(hours=1)
ORDER_TURN_GRANT_MODES = frozenset({"paper", "validation", "live"})
ORDER_TURN_GRANT_ACTIONS = frozenset({"submit", "cancel"})
_ORDER_ALLOW_BODY = re.compile(
    r"^--mode[ \t\n]+(?P<mode>paper|validation|live)[ \t\n]+(?P<request>.+)$",
    re.ASCII | re.DOTALL | re.IGNORECASE,
)

_RESERVED_SKILL_TOKENS = frozenset(
    {
        EXECUTE_APPROVED_ORDER_SKILL,
        CANCEL_SUBMITTED_ORDER_SKILL,
        RETIRED_EXECUTION_SKILL,
        ORDER_ALLOW_SKILL,
    }
)
_AUTHORITY_SKILL_TOKENS = _RESERVED_SKILL_TOKENS.union(
    {
        "$tcx-build",
        "$tcx-brain",
        "$tcx-strategy",
    }
)
_TOKEN_TO_ACTION = {
    EXECUTE_APPROVED_ORDER_SKILL: NATIVE_SUBMIT_ACTION,
    CANCEL_SUBMITTED_ORDER_SKILL: NATIVE_CANCEL_ACTION,
}
_ACTION_FIELDS = {
    NATIVE_SUBMIT_ACTION: ("ticket_id", "approval_receipt_id"),
    NATIVE_CANCEL_ACTION: ("ticket_id", "broker_order_id", "approval_receipt_id"),
}
_OPTIONAL_FIELDS = ("live_confirmation",)
_FLAG_TO_FIELD = {
    "--ticket-id": "ticket_id",
    "--broker-order-id": "broker_order_id",
    "--approval-receipt-id": "approval_receipt_id",
    "--live-confirmation": "live_confirmation",
}
_FIELD_LIMITS = {
    "ticket_id": 160,
    "broker_order_id": 160,
    "approval_receipt_id": 160,
    "live_confirmation": 500,
}
_MAX_PROMPT_BYTES = 4096
_MAX_TURN_GRANT_PROMPT_BYTES = 20000
_MANDATE_PROOF_KEY = secrets.token_bytes(32)


class NativeExecutionInvocationError(ValueError):
    """Raised when a reserved native execution prompt is not exact and safe."""


class OrderTurnInFlightError(PermissionError):
    """Raised when a prior turn grant is already authorizing an order effect."""


@dataclass(frozen=True)
class NativeExecutionMandate:
    action: str
    ticket_id: str
    approval_receipt_id: str
    broker_order_id: str
    live_confirmation: str = field(repr=False)
    source: str
    prompt_sha256: str
    invoked_at: str
    workspace_id: str
    workspace_path_hash: str
    grant_id: str
    _proof: str = field(repr=False, compare=False)

    def service_arguments(self) -> dict[str, str]:
        arguments = {
            "principal_id": NATIVE_USER_PRINCIPAL_ID,
            "ticket_id": self.ticket_id,
            "approval_receipt_id": self.approval_receipt_id,
        }
        if self.action == NATIVE_CANCEL_ACTION:
            arguments["broker_order_id"] = self.broker_order_id
        if self.live_confirmation:
            arguments["live_confirmation"] = self.live_confirmation
        return arguments

    def audit_metadata(self) -> dict[str, Any]:
        metadata: dict[str, Any] = {
            "action": self.action,
            "principal_id": NATIVE_USER_PRINCIPAL_ID,
            "source": self.source,
            "prompt_sha256": self.prompt_sha256,
            "invoked_at": self.invoked_at,
            "workspace_id": self.workspace_id,
            "workspace_path_hash": self.workspace_path_hash,
            "ticket_id": self.ticket_id,
            "approval_receipt_id": self.approval_receipt_id,
            "has_live_confirmation": bool(self.live_confirmation),
        }
        if self.broker_order_id:
            metadata["broker_order_id"] = self.broker_order_id
        if self.grant_id:
            metadata["grant_id"] = self.grant_id
        return metadata


def reserved_native_execution_token(
    prompt: Any,
    workspace_root: Path | str | None = None,
) -> str:
    """Return the first reserved action token, without interpreting free-form text."""

    try:
        invocation = parse_first_meaningful_invocation(
            prompt,
            _RESERVED_SKILL_TOKENS,
            workspace_root=workspace_root,
        )
    except SkillInvocationError as exc:
        raise NativeExecutionInvocationError(str(exc)) from exc
    return invocation.marker if invocation is not None else ""


def parse_native_execution_invocation(
    prompt: Any,
    workspace_root: Path | str,
    *,
    invoked_at: str | None = None,
) -> NativeExecutionMandate | None:
    """Parse one exact root action prompt into a workspace-bound mandate."""

    raw = raw_prompt(prompt)
    try:
        invocation = parse_first_meaningful_invocation(
            raw,
            _RESERVED_SKILL_TOKENS,
            workspace_root=workspace_root,
        )
    except SkillInvocationError as exc:
        raise NativeExecutionInvocationError(str(exc)) from exc
    if invocation is None:
        return None
    token = invocation.marker
    if token == RETIRED_EXECUTION_SKILL:
        raise NativeExecutionInvocationError(
            "$execute-paper-order is retired; use $tcx-order-submit with exact ticket and approval receipt ids"
        )
    if token == ORDER_ALLOW_SKILL:
        raise NativeExecutionInvocationError(
            "$tcx-order-allow creates a turn-scoped grant and is not an immediate execution invocation"
        )
    if len(raw.encode("utf-8")) > _MAX_PROMPT_BYTES:
        raise NativeExecutionInvocationError("native execution invocation is too long")
    lines = meaningful_lines(raw)
    remaining_lines = [line for _, line in lines[1:]]
    value = " ".join(
        part
        for part in (token, invocation.tail, *remaining_lines)
        if part
    )
    if any(_is_control_character(character) for character in value):
        raise NativeExecutionInvocationError("native execution invocation contains a control character")
    if any(character in value for character in ("'", '"', "\\")):
        raise NativeExecutionInvocationError("quoted or escaped native execution values are not supported")
    try:
        tokens = shlex.split(value, comments=False, posix=True)
    except ValueError as exc:
        raise NativeExecutionInvocationError("native execution invocation has invalid token quoting") from exc
    if not tokens or tokens[0] != token:
        raise NativeExecutionInvocationError("native execution skill token must be first")
    if len(tokens) == 1 or (len(tokens) - 1) % 2:
        raise NativeExecutionInvocationError("native execution flags must use exact --name value pairs")

    action = _TOKEN_TO_ACTION[token]
    required_fields = set(_ACTION_FIELDS[action])
    allowed_fields = required_fields.union(_OPTIONAL_FIELDS)
    parsed: dict[str, str] = {}
    for index in range(1, len(tokens), 2):
        flag = tokens[index]
        raw_value = tokens[index + 1]
        if "=" in flag:
            raise NativeExecutionInvocationError("native execution flags do not support --name=value")
        field_name = _FLAG_TO_FIELD.get(flag)
        if field_name is None or field_name not in allowed_fields:
            raise NativeExecutionInvocationError(f"unsupported native execution flag: {flag}")
        if field_name in parsed:
            raise NativeExecutionInvocationError(f"duplicate native execution flag: {flag}")
        parsed[field_name] = _validate_field_value(field_name, raw_value)

    missing = sorted(required_fields - set(parsed))
    if missing:
        flags = ", ".join("--" + field.replace("_", "-") for field in missing)
        raise NativeExecutionInvocationError(f"missing required native execution flag(s): {flags}")

    context = workspace_context_payload(workspace_root)
    mandate_fields = {
        "action": action,
        "ticket_id": parsed["ticket_id"],
        "approval_receipt_id": parsed["approval_receipt_id"],
        "broker_order_id": parsed.get("broker_order_id", ""),
        "live_confirmation": parsed.get("live_confirmation", ""),
        "source": NATIVE_EXECUTION_SOURCE,
        "prompt_sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
        "invoked_at": invoked_at or now_iso(),
        "workspace_id": str(context["workspace_id"]),
        "workspace_path_hash": str(context["path_hash"]),
        "grant_id": "",
    }
    return NativeExecutionMandate(
        **mandate_fields,
        _proof=_mandate_proof(mandate_fields),
    )


def parse_order_allow_invocation(
    prompt: Any,
    workspace_root: Path | str | None = None,
) -> str | None:
    """Return a turn-grant mode from a reserved first invocation."""

    raw = raw_prompt(prompt)
    try:
        invocation = parse_first_meaningful_invocation(
            raw,
            (ORDER_ALLOW_SKILL,),
            workspace_root=workspace_root,
        )
    except SkillInvocationError as exc:
        raise NativeExecutionInvocationError(str(exc)) from exc
    if invocation is None:
        return None
    lines = meaningful_lines(raw)
    body = "\n".join(
        part
        for part in (invocation.tail, *(line for _, line in lines[1:]))
        if part
    )
    match = _ORDER_ALLOW_BODY.fullmatch(body)
    if match is None:
        raise NativeExecutionInvocationError(
            "$tcx-order-allow requires --mode paper|validation|live followed by a non-empty request"
        )
    request = match.group("request")
    if not has_visible_content(request):
        raise NativeExecutionInvocationError("$tcx-order-allow requires a non-empty request")
    if request.lstrip().startswith("--"):
        raise NativeExecutionInvocationError("$tcx-order-allow contains an unsupported option")
    incompatible = (
        "$tcx-build",
        "$tcx-brain",
        "$tcx-strategy",
        ORDER_ALLOW_SKILL,
        EXECUTE_APPROVED_ORDER_SKILL,
        CANCEL_SUBMITTED_ORDER_SKILL,
        RETIRED_EXECUTION_SKILL,
    )
    try:
        mixed = next(
            (
                found
                for _, line in meaningful_lines(request)
                if (
                    found := parse_line_invocation(
                        line,
                        incompatible,
                        workspace_root=workspace_root,
                    )
                )
            ),
            None,
        )
    except SkillInvocationError as exc:
        raise NativeExecutionInvocationError(str(exc)) from exc
    if mixed is not None:
        raise NativeExecutionInvocationError(
            "$tcx-order-allow cannot be combined with Build, managed-skill, or immediate order markers"
        )
    if len(raw.encode("utf-8")) > _MAX_TURN_GRANT_PROMPT_BYTES:
        raise NativeExecutionInvocationError("$tcx-order-allow prompt is too long")
    return match.group("mode").lower()


def issue_order_turn_grant(
    workspace_root: Path | str,
    prompt: Any,
    *,
    session_id: Any,
    turn_id: Any,
    cwd: Path | str | None = None,
    issued_at: datetime | None = None,
    permission_mode: Any = "",
) -> dict[str, Any] | None:
    """Issue one DB-canonical grant for an exact root turn prompt."""

    root = Path(workspace_root).resolve()
    mode = parse_order_allow_invocation(prompt, root)
    if mode is None:
        return None
    normalized_permission_mode = _normalize_permission_mode(permission_mode)
    if normalized_permission_mode == "plan":
        raise PermissionError("order execution is unavailable while Codex is in Plan mode")
    if cwd is not None:
        try:
            resolved_cwd = Path(cwd).resolve()
        except (OSError, RuntimeError, ValueError) as exc:
            raise PermissionError("$tcx-order-allow workspace cwd is invalid") from exc
        if resolved_cwd != root:
            raise PermissionError("$tcx-order-allow must originate from the generated workspace root")
    session_value = _validate_binding_value("session_id", session_id)
    turn_value = _validate_binding_value("turn_id", turn_id)
    raw_prompt = str(prompt)
    prompt_hash = hashlib.sha256(raw_prompt.encode("utf-8")).hexdigest()
    session_hash = _secret_hash(session_value)
    turn_hash = _secret_hash(turn_value)
    now = _aware_datetime(issued_at)
    expires_at = now + ORDER_TURN_GRANT_TTL
    context = workspace_context_payload(root)
    ensure_runtime_database(root)

    from apps.orders.models import OrderTurnGrant

    revoked_ids: list[str] = []
    reused: OrderTurnGrant | None = None
    with transaction.atomic():
        candidates = list(
            OrderTurnGrant.objects.select_for_update().filter(
                workspace_id=str(context["workspace_id"]),
                workspace_path_hash=str(context["path_hash"]),
                session_id_hash=session_hash,
                status__in=[OrderTurnGrant.STATUS_ACTIVE, OrderTurnGrant.STATUS_RESERVED],
            )
        )
        same_turn = [candidate for candidate in candidates if candidate.turn_id_hash == turn_hash]
        if len(same_turn) > 1:
            raise PermissionError("the current turn has multiple order grants; execution is blocked")
        if any(candidate.expires_at <= now for candidate in same_turn):
            raise PermissionError("$tcx-order-allow grant is expired for this turn")
        if any(candidate.status == OrderTurnGrant.STATUS_RESERVED for candidate in same_turn):
            raise PermissionError("$tcx-order-allow grant is already reserved for this turn")
        if any(
            candidate.prompt_sha256 != prompt_hash or candidate.mode != mode
            for candidate in same_turn
        ):
            raise PermissionError("the current turn is already bound to another $tcx-order-allow prompt")
        if any(
            normalized_permission_mode != "unknown"
            and _grant_permission_mode(candidate) not in {"unknown", normalized_permission_mode}
            for candidate in same_turn
        ):
            raise PermissionError("the current order turn grant is bound to another Codex permission mode")
        for candidate in candidates:
            if candidate.expires_at <= now:
                candidate.status = OrderTurnGrant.STATUS_EXPIRED
                candidate.revoked_at = now
                candidate.metadata = {**dict(candidate.metadata or {}), "terminal_reason": "expired"}
                candidate.save(update_fields=["status", "revoked_at", "metadata"])
                continue
            if candidate in same_turn and reused is None:
                reused = candidate
                continue
            candidate.status = OrderTurnGrant.STATUS_REVOKED
            candidate.revoked_at = now
            candidate.metadata = {
                **dict(candidate.metadata or {}),
                "terminal_reason": "superseded_by_new_turn",
            }
            candidate.save(update_fields=["status", "revoked_at", "metadata"])
            revoked_ids.append(candidate.grant_id)

        if reused is None:
            grant = OrderTurnGrant.objects.create(
                grant_id="order-turn-" + secrets.token_urlsafe(24),
                mode=mode,
                status=OrderTurnGrant.STATUS_ACTIVE,
                workspace_id=str(context["workspace_id"]),
                workspace_path_hash=str(context["path_hash"]),
                session_id_hash=session_hash,
                turn_id_hash=turn_hash,
                prompt_sha256=prompt_hash,
                issued_at=now,
                expires_at=expires_at,
                metadata={
                    "single_use": True,
                    "permission_mode": normalized_permission_mode,
                },
            )
            write_audit_event_required(
                root,
                NATIVE_USER_PRINCIPAL_ID,
                "native-hook",
                {
                    "type": "native_execution.turn_grant.issued",
                    "resource": grant.grant_id,
                    "decision": "issued",
                    "payload": _turn_grant_audit_metadata(grant),
                },
            )
        else:
            grant = reused

    if revoked_ids:
        write_audit_event_if_available(
            root,
            NATIVE_USER_PRINCIPAL_ID,
            "native-hook",
            {
                "type": "native_execution.turn_grant.revoked",
                "resource": "session",
                "decision": "revoked",
                "payload": {
                    "reason": "superseded_by_new_turn",
                    "grant_count": len(revoked_ids),
                    "grant_id_hashes": [_secret_hash(value) for value in revoked_ids],
                    "workspace_id": str(context["workspace_id"]),
                    "workspace_path_hash": str(context["path_hash"]),
                },
            },
        )
    return _turn_grant_projection(grant)


def reserve_order_turn_grant(
    workspace_root: Path | str,
    session_id: Any,
    turn_id: Any,
    tool_use_id: Any,
    args: Mapping[str, Any],
    *,
    permission_mode: Any = "",
) -> str:
    """Reserve the active grant once and return an unpersisted one-time proof."""

    root = Path(workspace_root).resolve()
    session_hash = _secret_hash(_validate_binding_value("session_id", session_id))
    turn_hash = _secret_hash(_validate_binding_value("turn_id", turn_id))
    tool_use_hash = _secret_hash(_validate_binding_value("tool_use_id", tool_use_id))
    canonical_args = _canonical_turn_execution_args(args)
    normalized_permission_mode = _normalize_permission_mode(permission_mode)
    if normalized_permission_mode == "plan":
        raise PermissionError("order execution is unavailable while Codex is in Plan mode")
    arguments_hash = _turn_execution_arguments_hash(canonical_args)
    now = django_timezone.now()
    context = workspace_context_payload(root)
    ensure_runtime_database(root)

    from apps.orders.models import OrderTurnGrant

    proof = secrets.token_urlsafe(32)
    error = ""
    with transaction.atomic():
        grants = list(
            OrderTurnGrant.objects.select_for_update().filter(
                workspace_id=str(context["workspace_id"]),
                workspace_path_hash=str(context["path_hash"]),
                session_id_hash=session_hash,
                turn_id_hash=turn_hash,
                status=OrderTurnGrant.STATUS_ACTIVE,
            )
        )
        if len(grants) != 1:
            error = "the current root turn has no unique active $tcx-order-allow grant"
        else:
            grant = grants[0]
            granted_permission_mode = _grant_permission_mode(grant)
            if (
                normalized_permission_mode != "unknown"
                and granted_permission_mode not in {"unknown", normalized_permission_mode}
            ):
                error = "the current order turn grant is bound to another Codex permission mode"
            elif grant.expires_at <= now:
                grant.status = OrderTurnGrant.STATUS_EXPIRED
                grant.revoked_at = now
                grant.metadata = {**dict(grant.metadata or {}), "terminal_reason": "expired"}
                grant.save(update_fields=["status", "revoked_at", "metadata"])
                error = "$tcx-order-allow grant is expired"
            else:
                _validate_grant_order_scope(root, grant, canonical_args)
                confirmation_hash = (
                    _secret_hash(canonical_args["live_confirmation"])
                    if canonical_args.get("live_confirmation")
                    else ""
                )
                updated = OrderTurnGrant.objects.filter(
                    pk=grant.pk,
                    status=OrderTurnGrant.STATUS_ACTIVE,
                ).update(
                    status=OrderTurnGrant.STATUS_RESERVED,
                    reserved_at=now,
                    reservation_tool_use_id_hash=tool_use_hash,
                    reservation_arguments_hash=arguments_hash,
                    reservation_proof_hash=_secret_hash(proof),
                    action=canonical_args["action"],
                    ticket_id=canonical_args["ticket_id"],
                    approval_receipt_id=canonical_args["approval_receipt_id"],
                    broker_order_id=canonical_args.get("broker_order_id", ""),
                    live_confirmation_hash=confirmation_hash,
                )
                if updated != 1:
                    error = "$tcx-order-allow grant was already reserved"
                else:
                    grant.status = OrderTurnGrant.STATUS_RESERVED
                    grant.reserved_at = now
                    grant.reservation_tool_use_id_hash = tool_use_hash
                    grant.reservation_arguments_hash = arguments_hash
                    grant.reservation_proof_hash = _secret_hash(proof)
                    grant.action = canonical_args["action"]
                    grant.ticket_id = canonical_args["ticket_id"]
                    grant.approval_receipt_id = canonical_args["approval_receipt_id"]
                    grant.broker_order_id = canonical_args.get("broker_order_id", "")
                    grant.live_confirmation_hash = confirmation_hash
                    write_audit_event_required(
                        root,
                        NATIVE_USER_PRINCIPAL_ID,
                        "native-hook",
                        {
                            "type": "native_execution.turn_grant.reserved",
                            "resource": grant.grant_id,
                            "decision": "reserved",
                            "payload": {
                                **_turn_grant_audit_metadata(grant),
                                "action": grant.action,
                                "ticket_id": grant.ticket_id,
                                "approval_receipt_id": grant.approval_receipt_id,
                                "broker_order_id": grant.broker_order_id,
                                "tool_use_id_hash": tool_use_hash,
                            },
                        },
                    )
    if error:
        raise PermissionError(error)
    return proof


def execute_reserved_order_turn_grant(
    workspace_root: Path | str,
    args: Mapping[str, Any],
    proof: Any,
) -> dict[str, Any]:
    """Consume one reserved proof before dispatching through the canonical boundary."""

    root = Path(workspace_root).resolve()
    try:
        proof_value = _validate_binding_value("turn grant proof", proof, max_length=256)
    except NativeExecutionInvocationError as exc:
        raise PermissionError("a valid turn grant proof is required") from exc
    proof_hash = _secret_hash(proof_value)
    now = django_timezone.now()
    context = workspace_context_payload(root)
    ensure_runtime_database(root)

    from apps.orders.models import OrderTurnGrant

    error = ""
    grant_pk: int | None = None
    grant: OrderTurnGrant | None = None
    with transaction.atomic():
        grants = list(
            OrderTurnGrant.objects.select_for_update().filter(
                workspace_id=str(context["workspace_id"]),
                workspace_path_hash=str(context["path_hash"]),
                reservation_proof_hash=proof_hash,
                status=OrderTurnGrant.STATUS_RESERVED,
            )
        )
        if len(grants) != 1:
            error = "a unique reserved $tcx-order-allow proof is required"
        else:
            grant = grants[0]
            grant_pk = grant.pk
            if grant.expires_at <= now:
                grant.status = OrderTurnGrant.STATUS_EXPIRED
                grant.revoked_at = now
                grant.metadata = {**dict(grant.metadata or {}), "terminal_reason": "expired"}
                grant.save(update_fields=["status", "revoked_at", "metadata"])
                error = "$tcx-order-allow grant is expired"
            else:
                updated = OrderTurnGrant.objects.filter(
                    pk=grant.pk,
                    status=OrderTurnGrant.STATUS_RESERVED,
                    reservation_proof_hash=proof_hash,
                ).update(
                    status=OrderTurnGrant.STATUS_CONSUMED,
                    consumed_at=now,
                    result_status="authorizing",
                )
                if updated != 1:
                    error = "$tcx-order-allow proof was already consumed"
    if error:
        raise PermissionError(error)
    if grant is None or grant_pk is None:  # pragma: no cover - defensive guard
        raise PermissionError("reserved $tcx-order-allow mandate is unavailable")
    try:
        canonical_args = _canonical_turn_execution_args(args)
        arguments_hash = _turn_execution_arguments_hash(canonical_args)
        if not hmac.compare_digest(grant.reservation_arguments_hash, arguments_hash):
            raise PermissionError("$tcx-order-allow proof arguments do not match the reserved action")
        _validate_grant_order_scope(root, grant, canonical_args)
    except Exception as exc:
        OrderTurnGrant.objects.filter(pk=grant_pk).update(
            result_status="rejected",
            metadata={
                **dict(grant.metadata or {}),
                "terminal_reason": "reserved_action_rejected",
            },
        )
        raise PermissionError(
            "$tcx-order-allow reserved action was rejected and the proof was consumed"
        ) from exc
    action = NATIVE_SUBMIT_ACTION if canonical_args["action"] == "submit" else NATIVE_CANCEL_ACTION
    mandate_fields = {
        "action": action,
        "ticket_id": canonical_args["ticket_id"],
        "approval_receipt_id": canonical_args["approval_receipt_id"],
        "broker_order_id": canonical_args.get("broker_order_id", ""),
        "live_confirmation": canonical_args.get("live_confirmation", ""),
        "source": NATIVE_TURN_GRANT_SOURCE,
        "prompt_sha256": grant.prompt_sha256,
        "invoked_at": now_iso(),
        "workspace_id": grant.workspace_id,
        "workspace_path_hash": grant.workspace_path_hash,
        "grant_id": grant.grant_id,
    }
    mandate = NativeExecutionMandate(
        **mandate_fields,
        _proof=_mandate_proof(mandate_fields),
    )
    try:
        result = execute_native_execution_mandate(root, mandate)
    except Exception:
        OrderTurnGrant.objects.filter(pk=grant_pk).update(result_status="error")
        raise
    OrderTurnGrant.objects.filter(pk=grant_pk).update(
        result_status=str(result.get("status") or "error")[:32]
    )
    return result


def revoke_order_turn_grants(
    workspace_root: Path | str,
    session_id: Any,
    *,
    turn_id: Any | None = None,
    reason: str = "stop",
    fail_if_authorizing: bool = False,
) -> int:
    """Revoke unused grants without resetting an already-started order effect.

    A consumed grant whose result is still ``authorizing`` is terminal from the
    proof's perspective but not from the broker-effect perspective.  It must
    never be reset or retried.  Sensitive new turns may ask this service to fail
    closed while such a canonical in-flight record exists; ordinary research
    and Stop handling can still revoke unused grants without being blocked.
    """

    root = Path(workspace_root).resolve()
    session_hash = _secret_hash(_validate_binding_value("session_id", session_id))
    turn_hash = _secret_hash(_validate_binding_value("turn_id", turn_id)) if turn_id not in (None, "") else ""
    safe_reason = str(reason or "stop").strip()[:80] or "stop"
    context = workspace_context_payload(root)
    ensure_runtime_database(root)

    from apps.orders.models import OrderTurnGrant

    now = django_timezone.now()
    filters: dict[str, Any] = {
        "workspace_id": str(context["workspace_id"]),
        "workspace_path_hash": str(context["path_hash"]),
        "session_id_hash": session_hash,
        "status__in": [OrderTurnGrant.STATUS_ACTIVE, OrderTurnGrant.STATUS_RESERVED],
    }
    authorizing_filters: dict[str, Any] = {
        "workspace_id": str(context["workspace_id"]),
        "workspace_path_hash": str(context["path_hash"]),
        "session_id_hash": session_hash,
        "status": OrderTurnGrant.STATUS_CONSUMED,
        "result_status": "authorizing",
    }
    if turn_hash:
        filters["turn_id_hash"] = turn_hash
        authorizing_filters["turn_id_hash"] = turn_hash
    with transaction.atomic():
        grants = list(OrderTurnGrant.objects.select_for_update().filter(**filters))
        authorizing = list(
            OrderTurnGrant.objects.select_for_update().filter(**authorizing_filters)
        )
        for grant in grants:
            grant.status = OrderTurnGrant.STATUS_REVOKED
            grant.revoked_at = now
            grant.metadata = {**dict(grant.metadata or {}), "terminal_reason": safe_reason}
            grant.save(update_fields=["status", "revoked_at", "metadata"])
    if grants:
        write_audit_event_if_available(
            root,
            NATIVE_USER_PRINCIPAL_ID,
            "native-hook",
            {
                "type": "native_execution.turn_grant.revoked",
                "resource": "session",
                "decision": "revoked",
                "payload": {
                    "reason": safe_reason,
                    "grant_count": len(grants),
                    "grant_id_hashes": [_secret_hash(grant.grant_id) for grant in grants],
                    "workspace_id": str(context["workspace_id"]),
                    "workspace_path_hash": str(context["path_hash"]),
                },
            },
        )
    if fail_if_authorizing and authorizing:
        raise OrderTurnInFlightError(
            "a prior $tcx-order-allow effect is still authorizing; inspect its canonical order status before starting another sensitive turn"
        )
    return len(grants)


def authorize_native_execution(
    workspace_root: Path | str,
    args: Mapping[str, Any],
    mandate: NativeExecutionMandate | None,
    expected_action: str,
) -> dict[str, str]:
    """Validate mandate provenance and return the only accepted service arguments."""

    if not isinstance(mandate, NativeExecutionMandate) or not hmac.compare_digest(
        mandate._proof,
        _mandate_proof(
            {
                "action": mandate.action,
                "ticket_id": mandate.ticket_id,
                "approval_receipt_id": mandate.approval_receipt_id,
                "broker_order_id": mandate.broker_order_id,
                "live_confirmation": mandate.live_confirmation,
                "source": mandate.source,
                "prompt_sha256": mandate.prompt_sha256,
                "invoked_at": mandate.invoked_at,
                "workspace_id": mandate.workspace_id,
                "workspace_path_hash": mandate.workspace_path_hash,
                "grant_id": mandate.grant_id,
            }
        ),
    ):
        raise PermissionError("a parser-issued native execution mandate is required")
    if expected_action not in _ACTION_FIELDS or mandate.action != expected_action:
        raise PermissionError("native execution mandate action does not match the service operation")
    if mandate.source not in {NATIVE_EXECUTION_SOURCE, NATIVE_TURN_GRANT_SOURCE}:
        raise PermissionError("native execution mandate source is invalid")

    expected = mandate.service_arguments()
    supplied = {str(key): str(value) for key, value in dict(args).items() if value not in (None, "")}
    if set(supplied) != set(expected) or any(supplied.get(key) != value for key, value in expected.items()):
        raise PermissionError("native execution service arguments do not match the exact user mandate")

    context = workspace_context_payload(workspace_root)
    if (
        mandate.workspace_id != str(context["workspace_id"])
        or mandate.workspace_path_hash != str(context["path_hash"])
    ):
        raise PermissionError("native execution mandate belongs to another workspace")
    return expected


def execute_native_execution_mandate(
    workspace_root: Path | str,
    mandate: NativeExecutionMandate,
) -> dict[str, Any]:
    """Audit and dispatch one mandate through the canonical order service."""

    root = Path(workspace_root)
    args = authorize_native_execution(root, mandate.service_arguments(), mandate, mandate.action)
    metadata = mandate.audit_metadata()
    write_audit_event_required(
        root,
        NATIVE_USER_PRINCIPAL_ID,
        "native-hook",
        {
            "type": "native_execution.mandate.accepted",
            "resource": mandate.ticket_id,
            "decision": "accepted",
            "payload": metadata,
        },
    )

    from tradingcodex_service.application import orders

    try:
        if mandate.action == NATIVE_SUBMIT_ACTION:
            result = orders.submit_approved_order(root, args, native_mandate=mandate)
        elif mandate.action == NATIVE_CANCEL_ACTION:
            result = orders.cancel_submitted_order(root, args, native_mandate=mandate)
        else:  # pragma: no cover - guarded by authorize_native_execution
            raise PermissionError("unsupported native execution mandate action")
    except Exception:
        projected = _error_projection(mandate)
        try:
            _write_result_audit(root, mandate, projected)
        except Exception:
            projected = {
                **projected,
                "result_audit": "unavailable",
                "recovery": "inspect canonical order status before any retry",
            }
        return projected

    projected = project_native_execution_result(mandate, result)
    try:
        _write_result_audit(root, mandate, projected)
    except Exception:
        projected = {
            **projected,
            "result_audit": "unavailable",
            "recovery": "inspect canonical order status before any retry",
        }
    return projected


def project_native_execution_result(
    mandate: NativeExecutionMandate,
    result: Mapping[str, Any],
) -> dict[str, Any]:
    """Return only fields safe for hook context and user-facing reporting."""

    nested = result.get("result") if isinstance(result.get("result"), Mapping) else {}
    workspace = result.get("workspace_context") if isinstance(result.get("workspace_context"), Mapping) else {}
    status = str(result.get("status") or "error").lower()
    if status not in {
        "accepted",
        "canceled",
        "rejected",
        "not_cancelable",
        "needs_review",
        "error",
    }:
        status = "error"
    broker_order_id = str(result.get("broker_order_id") or nested.get("broker_order_id") or "")
    broker_status = str(result.get("broker_status") or nested.get("status") or "")
    projection: dict[str, Any] = {
        "marker": "tradingcodex-native-execution-result",
        "action": mandate.action,
        "status": status,
        "ticket_id": mandate.ticket_id,
        "approval_receipt_id": mandate.approval_receipt_id,
        "prompt_sha256": mandate.prompt_sha256,
        "db_canonical": result.get("db_canonical") is True,
        "workspace": {
            "workspace_id": str(workspace.get("workspace_id") or mandate.workspace_id),
            "path_hash": str(workspace.get("path_hash") or mandate.workspace_path_hash),
        },
    }
    if broker_order_id:
        projection["broker_order_id"] = broker_order_id[:160]
    if broker_status:
        projection["broker_status"] = broker_status[:64]
    adapter = str(result.get("adapter") or nested.get("adapter") or "")
    if adapter:
        projection["adapter"] = adapter[:160]
    idempotency_key = str(result.get("idempotency_key") or "")
    if idempotency_key:
        projection["idempotency_key"] = idempotency_key[:160]
    reasons = result.get("reasons") if isinstance(result.get("reasons"), list) else []
    if reasons:
        projection["reason_codes"] = _reason_codes(reasons)
    if status == "needs_review":
        projection["recovery"] = "inspect canonical order status; do not retry automatically"
    return projection


def _write_result_audit(
    root: Path,
    mandate: NativeExecutionMandate,
    projected: Mapping[str, Any],
) -> None:
    write_audit_event_required(
        root,
        NATIVE_USER_PRINCIPAL_ID,
        "native-hook",
        {
            "type": "native_execution.result",
            "resource": mandate.ticket_id,
            "decision": str(projected.get("status") or "error"),
            "payload": dict(projected),
        },
    )


def _error_projection(mandate: NativeExecutionMandate) -> dict[str, Any]:
    return {
        "marker": "tradingcodex-native-execution-result",
        "action": mandate.action,
        "status": "error",
        "ticket_id": mandate.ticket_id,
        "approval_receipt_id": mandate.approval_receipt_id,
        "prompt_sha256": mandate.prompt_sha256,
        "reason_codes": ["service_error"],
        "db_canonical": False,
        "canonical_status": "unknown",
        "workspace": {
            "workspace_id": mandate.workspace_id,
            "path_hash": mandate.workspace_path_hash,
        },
        "recovery": "inspect canonical order status before any retry",
    }


def _reason_codes(reasons: list[Any]) -> list[str]:
    codes: list[str] = []
    for reason in reasons:
        lowered = str(reason).lower()
        if "already" in lowered or "duplicate" in lowered or "consumed" in lowered:
            code = "duplicate_or_consumed"
        elif "approval" in lowered or "receipt" in lowered or "expired" in lowered or "superseded" in lowered:
            code = "approval_invalid"
        elif "restricted" in lowered:
            code = "restricted_symbol"
        elif "confirmation" in lowered or "tradingcodex_enable_live_execution" in lowered:
            code = "live_confirmation_or_opt_in_required"
        elif "notional" in lowered or "money" in lowered or "currency" in lowered or "fx" in lowered:
            code = "money_or_limit_policy"
        elif "adapter" in lowered or "broker" in lowered or "provider" in lowered or "health" in lowered:
            code = "provider_unavailable_or_uncertain"
        elif "state" in lowered or "submitted" in lowered or "cancel" in lowered:
            code = "invalid_order_state"
        elif "policy" in lowered or "capability" in lowered or "principal" in lowered:
            code = "policy_denied"
        else:
            code = "request_rejected"
        if code not in codes:
            codes.append(code)
    return codes or ["request_rejected"]


def _canonical_turn_execution_args(args: Mapping[str, Any]) -> dict[str, str]:
    if not isinstance(args, Mapping):
        raise NativeExecutionInvocationError("turn-grant execution arguments must be an object")
    allowed_fields = {
        "action",
        "ticket_id",
        "approval_receipt_id",
        "broker_order_id",
        "live_confirmation",
    }
    unexpected = sorted(str(key) for key in set(args) - allowed_fields)
    if unexpected:
        raise NativeExecutionInvocationError(
            "unsupported turn-grant execution field(s): " + ", ".join(unexpected)
        )
    action = str(args.get("action") or "").strip().lower()
    if action not in ORDER_TURN_GRANT_ACTIONS:
        raise NativeExecutionInvocationError("turn-grant action must be submit or cancel")
    ticket_id = _validate_field_value("ticket_id", str(args.get("ticket_id") or ""))
    receipt_id = _validate_field_value(
        "approval_receipt_id",
        str(args.get("approval_receipt_id") or ""),
    )
    broker_order_id = str(args.get("broker_order_id") or "")
    live_confirmation = str(args.get("live_confirmation") or "")
    if broker_order_id:
        broker_order_id = _validate_field_value("broker_order_id", broker_order_id)
    if live_confirmation:
        live_confirmation = _validate_field_value("live_confirmation", live_confirmation)
    if action == "submit" and broker_order_id:
        raise NativeExecutionInvocationError("submit does not accept broker_order_id")
    if action == "cancel" and not broker_order_id:
        raise NativeExecutionInvocationError("cancel requires broker_order_id")
    return {
        "action": action,
        "ticket_id": ticket_id,
        "approval_receipt_id": receipt_id,
        "broker_order_id": broker_order_id,
        "live_confirmation": live_confirmation,
    }


def _validate_grant_order_scope(root: Path, grant: Any, args: Mapping[str, str]) -> None:
    from apps.orders.models import ApprovalReceipt
    from tradingcodex_service.application import orders

    ticket = orders.get_order_ticket_model(root, {"ticket_id": args["ticket_id"]})
    ticket_context = ticket.workspace_context if isinstance(ticket.workspace_context, dict) else {}
    current_context = workspace_context_payload(root)
    if ticket_context.get("workspace_id") and str(ticket_context["workspace_id"]) != str(
        current_context["workspace_id"]
    ):
        raise PermissionError("order ticket belongs to another workspace")
    if ticket_context.get("path_hash") and str(ticket_context["path_hash"]) != str(current_context["path_hash"]):
        raise PermissionError("order ticket belongs to another workspace path")
    if not ApprovalReceipt.objects.filter(
        approval_receipt_id=args["approval_receipt_id"],
        order_ticket=ticket,
    ).exists():
        raise PermissionError("approval receipt does not belong to the selected order ticket")

    connection = ticket.broker_connection
    broker_id = str(connection.broker_id if connection is not None else "")
    metadata = connection.metadata if connection is not None and isinstance(connection.metadata, dict) else {}
    profile = metadata.get("capability_profile") if isinstance(metadata.get("capability_profile"), dict) else {}
    posture = "paper_only" if broker_id == "paper-trading" else str(profile.get("execution_posture") or "")
    expected_posture = {
        "paper": "paper_only",
        "validation": "broker_validation_only",
        "live": "live_broker",
    }.get(str(grant.mode))
    if posture != expected_posture:
        raise PermissionError(
            f"$tcx-order-allow mode {grant.mode} does not match ticket execution posture {posture or 'unknown'}"
        )

    confirmation = str(args.get("live_confirmation") or "")
    if grant.mode != "live":
        if confirmation:
            raise PermissionError("live_confirmation is forbidden for paper and validation grants")
        return
    if args["action"] == "submit":
        order = orders.order_payload_from_ticket(ticket)
        expected_confirmation = orders._expected_live_confirmation(order)
    else:
        broker_order = ticket.broker_orders.filter(broker_order_id=args["broker_order_id"]).first()
        if broker_order is None:
            raise PermissionError("live cancellation requires a known broker order")
        expected_confirmation = orders._expected_live_cancel_confirmation(ticket, broker_order)
    if not confirmation or not hmac.compare_digest(confirmation, expected_confirmation):
        raise PermissionError("live confirmation does not match the exact ticket action")


def _turn_execution_arguments_hash(args: Mapping[str, str]) -> str:
    canonical = json.dumps(dict(args), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _turn_grant_projection(grant: Any) -> dict[str, Any]:
    return {
        "marker": "tradingcodex-order-turn-grant",
        "status": str(grant.status),
        "mode": str(grant.mode),
        "expires_at": grant.expires_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "single_use": True,
        "allowed_actions": sorted(ORDER_TURN_GRANT_ACTIONS),
    }


def _normalize_permission_mode(value: Any) -> str:
    text = str(value or "").strip().lower().replace("_", "-")
    if not text:
        return "unknown"
    if len(text) > 40 or any(
        character.isspace() or unicodedata.category(character).startswith("C")
        for character in text
    ):
        raise NativeExecutionInvocationError("Codex permission_mode must be one compact token")
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


def _turn_grant_audit_metadata(grant: Any) -> dict[str, Any]:
    return {
        "grant_id": str(grant.grant_id),
        "mode": str(grant.mode),
        "status": str(grant.status),
        "single_use": True,
        "workspace_id": str(grant.workspace_id),
        "workspace_path_hash": str(grant.workspace_path_hash),
        "session_id_hash": str(grant.session_id_hash),
        "turn_id_hash": str(grant.turn_id_hash),
        "prompt_sha256": str(grant.prompt_sha256),
        "permission_mode": _grant_permission_mode(grant),
        "issued_at": grant.issued_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "expires_at": grant.expires_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
    }


def _validate_binding_value(field_name: str, value: Any, *, max_length: int = 200) -> str:
    text = str(value or "")
    if not text or text != text.strip():
        raise NativeExecutionInvocationError(f"{field_name} is required")
    if len(text) > max_length:
        raise NativeExecutionInvocationError(f"{field_name} is too long")
    if any(character.isspace() or _is_control_character(character) for character in text):
        raise NativeExecutionInvocationError(f"{field_name} must be one printable token")
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


def _validate_field_value(field_name: str, value: str) -> str:
    if not value or value != value.strip():
        raise NativeExecutionInvocationError(f"{field_name.replace('_', '-')} value is required")
    if value.startswith("--"):
        raise NativeExecutionInvocationError(f"{field_name.replace('_', '-')} value cannot be another flag")
    if value in _AUTHORITY_SKILL_TOKENS:
        raise NativeExecutionInvocationError(
            f"{field_name.replace('_', '-')} value cannot be a Build, managed-skill, or order marker"
        )
    if unicodedata.category(value[0]).startswith("M"):
        raise NativeExecutionInvocationError(
            f"{field_name.replace('_', '-')} cannot start with a combining character"
        )
    if any(character.isspace() or _is_control_character(character) for character in value):
        raise NativeExecutionInvocationError(f"{field_name.replace('_', '-')} must be one printable token")
    if len(value) > _FIELD_LIMITS[field_name]:
        raise NativeExecutionInvocationError(f"{field_name.replace('_', '-')} is too long")
    return value


def _is_control_character(value: str) -> bool:
    return unicodedata.category(value).startswith("C")


def _mandate_proof(fields: Mapping[str, Any]) -> str:
    canonical = "\x1f".join(f"{key}={fields[key]}" for key in sorted(fields))
    return hmac.new(_MANDATE_PROOF_KEY, canonical.encode("utf-8"), hashlib.sha256).hexdigest()

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import threading
import time
from pathlib import Path
from typing import Final

from tradingcodex_service.application.runtime import workspace_context_payload


PROVIDER_SOURCE_APPROVE: Final = "provider-source-approve"
PROVIDER_SOURCE_REVOKE: Final = "provider-source-revoke"
_ALLOWED_ACTIONS = frozenset(
    {
        PROVIDER_SOURCE_APPROVE,
        PROVIDER_SOURCE_REVOKE,
    }
)
_AUTHORITY_LIFETIME_SECONDS: Final = 60.0
_PROOF_KEY = secrets.token_bytes(32)
_ISSUED: dict[str, tuple[str, float]] = {}
_SERVICE_ISSUED: dict[str, tuple[str, float]] = {}
_ISSUED_LOCK = threading.Lock()


class OperatorAuthority:
    """Opaque, process-local capability for one confirmed operator action."""

    __slots__ = (
        "_action",
        "_issuer_pid",
        "_nonce",
        "_proof",
        "_resource",
        "_workspace_id",
        "_workspace_path_hash",
    )

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        raise TypeError("OperatorAuthority values are issued internally")

    def __repr__(self) -> str:
        return "OperatorAuthority(<opaque>)"


class OperatorServiceAuthority:
    """Opaque second-stage capability passed only to a canonical service."""

    __slots__ = OperatorAuthority.__slots__

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        raise TypeError("OperatorServiceAuthority values are issued internally")

    def __repr__(self) -> str:
        return "OperatorServiceAuthority(<opaque>)"


def provider_source_approval_resource(provider_id: str, bundle_sha256: str) -> str:
    return f"broker-provider:{provider_id}:bundle:{bundle_sha256}"


def provider_source_revocation_resource(provider_id: str) -> str:
    return f"broker-provider:{provider_id}"


def _issue_operator_authority(
    workspace_root: Path | str,
    *,
    action: str,
    resource: str,
) -> OperatorAuthority:
    """Issue a one-shot capability for a trusted interactive CLI adapter.

    This internal function performs no consent UI. Callers must invoke it only
    after their transport has completed its own non-automatable confirmation.
    """

    if action not in _ALLOWED_ACTIONS:
        raise ValueError("unsupported operator authority action")
    if not isinstance(resource, str) or not resource or resource != resource.strip():
        raise ValueError("operator authority resource is invalid")
    context = workspace_context_payload(Path(workspace_root).expanduser().resolve())
    fields = {
        "action": action,
        "issuer_pid": os.getpid(),
        "nonce": secrets.token_urlsafe(24),
        "resource": resource,
        "workspace_id": str(context["workspace_id"]),
        "workspace_path_hash": str(context["path_hash"]),
    }
    proof = _authority_proof(fields)
    authority = object.__new__(OperatorAuthority)
    for field, value in fields.items():
        object.__setattr__(authority, f"_{field}", value)
    object.__setattr__(authority, "_proof", proof)
    with _ISSUED_LOCK:
        _discard_expired_locked()
        _ISSUED[str(fields["nonce"])] = (proof, time.monotonic() + _AUTHORITY_LIFETIME_SECONDS)
    return authority


def consume_operator_authority(
    authority: OperatorAuthority | None,
    workspace_root: Path | str,
    *,
    action: str,
    resource: str,
) -> OperatorServiceAuthority:
    """Consume one CLI capability and seal it for one canonical service call."""

    if action not in _ALLOWED_ACTIONS:
        raise PermissionError("operator authority action is unavailable")
    context = workspace_context_payload(Path(workspace_root).expanduser().resolve())
    if not isinstance(authority, OperatorAuthority):
        raise PermissionError("a confirmed interactive operator authority is required")
    fields, proof = _validated_authority_fields(authority)
    expected_proof = _authority_proof(fields)
    if (
        authority._issuer_pid != os.getpid()
        or authority._action != action
        or authority._resource != resource
        or authority._workspace_id != str(context["workspace_id"])
        or authority._workspace_path_hash != str(context["path_hash"])
        or not hmac.compare_digest(proof, expected_proof)
    ):
        raise PermissionError("operator authority does not match this operation")
    with _ISSUED_LOCK:
        _discard_expired_locked()
        issued = _ISSUED.pop(authority._nonce, None)
    if issued is None or not hmac.compare_digest(issued[0], proof):
        raise PermissionError("operator authority is expired, invalid, or already used")
    return _issue_service_authority(
        action=action,
        resource=resource,
        workspace_id=str(context["workspace_id"]),
        workspace_path_hash=str(context["path_hash"]),
    )


def consume_service_operator_authority(
    authority: OperatorAuthority | OperatorServiceAuthority | None,
    workspace_root: Path | str,
    *,
    action: str,
    resource: str,
) -> None:
    """Consume either a CLI capability or its sealed MCP-to-service context."""

    if isinstance(authority, OperatorAuthority):
        authority = consume_operator_authority(
            authority,
            workspace_root,
            action=action,
            resource=resource,
        )
    if not isinstance(authority, OperatorServiceAuthority):
        raise PermissionError("a confirmed interactive operator authority is required")
    context = workspace_context_payload(Path(workspace_root).expanduser().resolve())
    fields, proof = _validated_authority_fields(authority)
    expected_proof = _service_authority_proof(fields)
    if (
        authority._issuer_pid != os.getpid()
        or authority._action != action
        or authority._resource != resource
        or authority._workspace_id != str(context["workspace_id"])
        or authority._workspace_path_hash != str(context["path_hash"])
        or not hmac.compare_digest(proof, expected_proof)
    ):
        raise PermissionError("operator service authority does not match this operation")
    with _ISSUED_LOCK:
        _discard_expired_locked()
        issued = _SERVICE_ISSUED.pop(authority._nonce, None)
    if issued is None or not hmac.compare_digest(issued[0], proof):
        raise PermissionError("operator service authority is expired, invalid, or already used")


def _issue_service_authority(
    *,
    action: str,
    resource: str,
    workspace_id: str,
    workspace_path_hash: str,
) -> OperatorServiceAuthority:
    fields = {
        "action": action,
        "issuer_pid": os.getpid(),
        "nonce": secrets.token_urlsafe(24),
        "resource": resource,
        "workspace_id": workspace_id,
        "workspace_path_hash": workspace_path_hash,
    }
    proof = _service_authority_proof(fields)
    authority = object.__new__(OperatorServiceAuthority)
    for field, value in fields.items():
        object.__setattr__(authority, f"_{field}", value)
    object.__setattr__(authority, "_proof", proof)
    with _ISSUED_LOCK:
        _discard_expired_locked()
        _SERVICE_ISSUED[str(fields["nonce"])] = (
            proof,
            time.monotonic() + _AUTHORITY_LIFETIME_SECONDS,
        )
    return authority


def _validated_authority_fields(
    authority: OperatorAuthority | OperatorServiceAuthority,
) -> tuple[dict[str, object], str]:
    try:
        fields = {
            "action": authority._action,
            "issuer_pid": authority._issuer_pid,
            "nonce": authority._nonce,
            "resource": authority._resource,
            "workspace_id": authority._workspace_id,
            "workspace_path_hash": authority._workspace_path_hash,
        }
        proof = authority._proof
    except (AttributeError, TypeError, ValueError):
        raise PermissionError("operator authority is invalid") from None
    if not (
        isinstance(fields["action"], str)
        and isinstance(fields["issuer_pid"], int)
        and isinstance(fields["nonce"], str)
        and isinstance(fields["resource"], str)
        and isinstance(fields["workspace_id"], str)
        and isinstance(fields["workspace_path_hash"], str)
        and isinstance(proof, str)
    ):
        raise PermissionError("operator authority is invalid")
    return fields, proof


def _authority_proof(fields: dict[str, object]) -> str:
    canonical = "\0".join(
        str(fields[name])
        for name in (
            "action",
            "issuer_pid",
            "nonce",
            "resource",
            "workspace_id",
            "workspace_path_hash",
        )
    )
    return hmac.new(_PROOF_KEY, canonical.encode("utf-8"), hashlib.sha256).hexdigest()


def _service_authority_proof(fields: dict[str, object]) -> str:
    canonical = "\0".join(
        str(fields[name])
        for name in (
            "action",
            "issuer_pid",
            "nonce",
            "resource",
            "workspace_id",
            "workspace_path_hash",
        )
    )
    return hmac.new(
        _PROOF_KEY,
        ("service\0" + canonical).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _discard_expired_locked() -> None:
    now = time.monotonic()
    for ledger in (_ISSUED, _SERVICE_ISSUED):
        for nonce, (_proof, expires_at) in tuple(ledger.items()):
            if expires_at <= now:
                ledger.pop(nonce, None)

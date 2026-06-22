from __future__ import annotations

from fnmatch import fnmatch
from typing import Any, Iterable

from django.db.models import QuerySet

from apps.policy.models import Capability, Principal, RestrictedSymbol


BUILTIN_ROLE_IDS = {
    "head-manager",
    "fundamental-analyst",
    "technical-analyst",
    "news-analyst",
    "macro-analyst",
    "instrument-analyst",
    "valuation-analyst",
    "portfolio-manager",
    "risk-manager",
    "execution-operator",
}


def tool_capability_action(tool_name: str, capability_required: str = "") -> str:
    return capability_required or f"mcp.tradingcodex.{tool_name}"


def sync_builtin_principals_and_capabilities(tool_specs: Iterable[Any] | None = None) -> None:
    if tool_specs is None:
        from tradingcodex_service.mcp_runtime import TOOL_SPECS

        tool_specs = TOOL_SPECS
    tool_specs = tuple(tool_specs)

    for role in sorted(BUILTIN_ROLE_IDS):
        Principal.objects.get_or_create(principal_id=role, defaults={"role": role, "active": True})

    expected_builtin_allows: set[tuple[str, str]] = set()
    builtin_actions: set[str] = set()
    for tool in tool_specs:
        action = tool_capability_action(tool.name, tool.capability_required)
        builtin_actions.add(action)
        for role in tool.allowed_roles:
            expected_builtin_allows.add((role, action))
            principal, _ = Principal.objects.get_or_create(principal_id=role, defaults={"role": role, "active": True})
            if principal.role != role:
                principal.role = role
                principal.save(update_fields=["role"])
            Capability.objects.get_or_create(
                principal=principal,
                action=action,
                resource_pattern="*",
                defaults={"effect": "allow"},
            )

    stale_builtin_allows = Capability.objects.filter(
        principal__principal_id__in=BUILTIN_ROLE_IDS,
        action__in=builtin_actions,
        effect="allow",
    )
    for capability in stale_builtin_allows.select_related("principal"):
        if (capability.principal.principal_id, capability.action) not in expected_builtin_allows:
            capability.delete()


def role_for_principal_id(principal_id: str) -> str:
    principal = Principal.objects.filter(principal_id=principal_id).first()
    if principal is not None:
        return principal.role if principal.active else ""
    if principal_id in BUILTIN_ROLE_IDS:
        return principal_id
    return principal_id or "unknown"


def capability_check(principal_id: str, action: str, resource: str | None = None) -> tuple[bool, list[str]]:
    principal = Principal.objects.filter(principal_id=principal_id).first()
    if principal is None:
        return False, [f"principal is unknown: {principal_id}"]
    if not principal.active:
        return False, [f"principal is inactive: {principal_id}"]

    resource_value = str(resource or "*")
    candidates = [
        capability
        for capability in Capability.objects.filter(principal=principal, action=action)
        if resource_matches(capability.resource_pattern, resource_value)
    ]
    if any(capability.effect == "deny" for capability in candidates):
        return False, [f"capability denied: {principal_id} {action} {resource_value}"]
    if any(capability.effect == "allow" for capability in candidates):
        return True, []
    return False, [f"principal lacks capability: {principal_id} {action} {resource_value}"]


def resource_matches(pattern: str, resource: str) -> bool:
    normalized = pattern or "*"
    return normalized == "*" or fnmatch(resource, normalized)


def set_principal_active(queryset: QuerySet[Principal], active: bool, actor: str = "admin") -> int:
    count = queryset.update(active=active)
    _audit("principal.activated" if active else "principal.deactivated", {"count": count}, actor)
    return count


def set_capability_effect(queryset: QuerySet[Capability], effect: str, actor: str = "admin") -> int:
    if effect not in {"allow", "deny"}:
        raise ValueError("capability effect must be allow or deny")
    count = queryset.update(effect=effect)
    _audit("capability.allowed" if effect == "allow" else "capability.denied", {"count": count}, actor)
    return count


def set_restricted_symbols_active(queryset: QuerySet[RestrictedSymbol], active: bool, actor: str = "admin") -> int:
    count = queryset.update(active=active)
    _audit("restricted_symbol.activated" if active else "restricted_symbol.deactivated", {"count": count}, actor)
    return count


def _audit(action: str, payload: dict[str, Any], actor: str) -> None:
    from tradingcodex_service.application.audit import write_audit_event_if_available

    write_audit_event_if_available(None, actor, "admin", {"type": action, "payload": payload})

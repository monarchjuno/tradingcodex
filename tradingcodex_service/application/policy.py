from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tradingcodex_service.application.audit import write_policy_decision_if_available
from tradingcodex_service.application.common import _number, _safe_read
from tradingcodex_service.application.runtime import ensure_runtime_database, workspace_context_payload

DEFAULT_MAX_SINGLE_ORDER_KRW = 100_000_000
DEFAULT_ALLOWED_ADAPTERS = {"stub-execution", "paper-trading"}
EXPLICIT_DENY_ACTIONS = {
    "api_key.read",
    "api_key.rotate",
    "secret.read",
    "broker.raw_api",
    "broker_api.direct_call",
    "approval.self_issue",
    "approval_receipt.self_issue",
    "execute_order",
    "order.execute",
    "trade.execute",
    "trading.execute",
    "cash.withdraw",
    "cash.transfer",
    "permissions.write",
    "policy.write",
    "mcp.tradingcodex.write_policy_and_execute",
}

@dataclass(frozen=True)
class RuntimePolicy:
    max_single_order_krw: int = DEFAULT_MAX_SINGLE_ORDER_KRW
    allowed_adapters: frozenset[str] = frozenset(DEFAULT_ALLOWED_ADAPTERS)
    source: tuple[str, ...] = ("default-runtime-policy",)


def read_runtime_policy(workspace_root: Path | str) -> RuntimePolicy:
    root = Path(workspace_root)
    max_single_order = DEFAULT_MAX_SINGLE_ORDER_KRW
    allowed_adapters = set(DEFAULT_ALLOWED_ADAPTERS)
    source = [".tradingcodex/policies/access-policies.yaml", ".tradingcodex/config.yaml"]

    access_text = _safe_read(root / ".tradingcodex" / "policies" / "access-policies.yaml")
    max_match = re.search(r"order\.estimated_notional_krw\s*<=\s*(\d+)", access_text)
    if max_match:
        max_single_order = int(max_match.group(1))
    brokers_match = re.search(r"order\.broker\s+in\s+\[([^\]]+)\]", access_text)
    if brokers_match:
        parsed = re.findall(r'"([^"]+)"', brokers_match.group(1))
        if parsed:
            allowed_adapters = set(parsed)

    config_text = _safe_read(root / ".tradingcodex" / "config.yaml")
    section = re.search(r"enabled_adapters:[ \t]*\n((?:[ \t]*-[ \t]*[A-Za-z0-9._-]+[ \t]*(?:\n|$))+)", config_text)
    if section:
        configured = set(re.findall(r"^[ \t]*-[ \t]*([A-Za-z0-9._-]+)[ \t]*$", section.group(1), flags=re.M))
        if configured:
            allowed_adapters &= configured

    return RuntimePolicy(max_single_order, frozenset(allowed_adapters), tuple(source))


def read_restricted_symbols(workspace_root: Path | str) -> set[str]:
    text = _safe_read(Path(workspace_root) / ".tradingcodex" / "policies" / "restricted-list.yaml")
    symbols: set[str] = set()
    try:
        ensure_runtime_database(workspace_root)
        from apps.policy.models import RestrictedSymbol

        symbols.update(symbol.upper() for symbol in RestrictedSymbol.objects.filter(active=True).values_list("symbol", flat=True))
    except Exception:
        pass
    inline = re.search(r"restricted_symbols\s*:\s*\[([^\]]*)\]", text)
    if inline:
        for raw in inline.group(1).split(","):
            symbol = raw.strip().strip("'\"")
            if symbol:
                symbols.add(symbol.upper())
    block = re.search(r"restricted_symbols\s*:\s*\n((?:[ \t]*-[ \t]*[A-Za-z0-9_.:-]+[ \t]*(?:\n|$))+)", text)
    if block:
        symbols.update(symbol.upper() for symbol in re.findall(r"^[ \t]*-[ \t]*([A-Za-z0-9_.:-]+)[ \t]*$", block.group(1), flags=re.M))
    symbols.update(symbol.upper() for symbol in re.findall(r"\bsymbol\s*:\s*['\"]?([A-Za-z0-9_.:-]+)['\"]?", text))
    return symbols


def evaluate_policy(workspace_root: Path | str, args: dict[str, Any]) -> dict[str, Any]:
    ensure_runtime_database(workspace_root)
    from apps.policy.services import capability_check, sync_builtin_principals_and_capabilities
    from tradingcodex_service.application.orders import resolve_approval_receipt, resolve_order_intent

    sync_builtin_principals_and_capabilities()
    policy = read_runtime_policy(workspace_root)
    order = resolve_order_intent(Path(workspace_root), args)
    receipt = resolve_approval_receipt(Path(workspace_root), args, order)
    principal_id = args.get("principal_id") or "unknown"
    action = args.get("action") or "unknown"
    reasons: list[str] = []
    capability_allowed, capability_reasons = capability_check(principal_id, action, args.get("resource"))
    if not capability_allowed:
        reasons.extend(capability_reasons)

    if action in EXPLICIT_DENY_ACTIONS:
        reasons.append(f"explicit deny action: {action}")
    if action.startswith(("broker_api.", "broker.")):
        reasons.append("direct broker API actions are explicitly denied")
    if "live" in action.lower() and re.search(r"order|execution|submit|broker", action.lower()):
        reasons.append("live execution actions are disabled in the initial core")
    if "live" in str(args.get("resource") or "").lower() and re.search(r"order|execution|submit|broker", action.lower()):
        reasons.append("live execution resources are disabled in the initial core")
    if action in {"approval.create", "approval_receipt.create"} and principal_id != "risk-manager":
        reasons.append("only risk-manager can create approval receipts")
    if action == "mcp.tradingcodex.submit_approved_order" and principal_id != "execution-operator":
        reasons.append("only execution-operator can submit approved orders")
    if order.get("broker") == "live" and not (Path(workspace_root) / ".tradingcodex" / "mcp" / "adapters" / "live.py").exists():
        reasons.append("live broker adapter is not installed in this workspace")
    if order.get("broker") and order["broker"] not in policy.allowed_adapters:
        reasons.append(f"adapter not enabled: {order['broker']}")

    notional = _number(order.get("estimated_notional_krw"))
    if order.get("estimated_notional_krw") not in (None, "") and (notional is None or notional <= 0):
        reasons.append("estimated_notional_krw must be a positive number")
    elif notional is not None and notional > policy.max_single_order_krw:
        reasons.append(f"estimated_notional_krw exceeds {policy.max_single_order_krw}")

    if order.get("symbol") and str(order["symbol"]).upper() in read_restricted_symbols(workspace_root):
        reasons.append(f"symbol is restricted: {order['symbol']}")
    if args.get("require_approval_check") and receipt.get("valid") is not True:
        reasons.append("approval_receipt.valid == false")

    decision = "allow" if not reasons else "deny"
    result = {
        "decision": decision,
        "reasons": reasons,
        "enforced_by": ["TradingCodex MCP"],
        "policy_source": list(policy.source),
        "principal_id": principal_id,
        "action": action,
        "resource": args.get("resource"),
        "db_canonical": True,
        "workspace_context": workspace_context_payload(workspace_root),
    }
    write_policy_decision_if_available(workspace_root, result)
    return result


def simulate_policy(workspace_root: Path | str, args: dict[str, Any]) -> dict[str, Any]:
    return evaluate_policy(workspace_root, args)

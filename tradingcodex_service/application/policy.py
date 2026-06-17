from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from tradingcodex_service.application.audit import write_policy_decision_if_available
from tradingcodex_service.application.common import _number
from tradingcodex_service.application.runtime import ensure_runtime_database, workspace_context_payload

DEFAULT_MAX_SINGLE_ORDER_KRW = 100_000_000
DEFAULT_ALLOWED_ADAPTERS = {"stub-execution", "paper-trading"}
DEFAULT_ALLOWED_EXECUTION_POSTURES = {"paper_only", "broker_validation_only"}
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
    allowed_execution_postures: frozenset[str] = frozenset(DEFAULT_ALLOWED_EXECUTION_POSTURES)
    source: tuple[str, ...] = ("default-runtime-policy",)


class PolicyConfigurationError(ValueError):
    pass


def read_runtime_policy(workspace_root: Path | str) -> RuntimePolicy:
    root = Path(workspace_root)
    max_single_order = DEFAULT_MAX_SINGLE_ORDER_KRW
    allowed_adapters = set(DEFAULT_ALLOWED_ADAPTERS)
    allowed_execution_postures = set(DEFAULT_ALLOWED_EXECUTION_POSTURES)
    source = [".tradingcodex/policies/access-policies.yaml", ".tradingcodex/config.yaml"]

    access_data = _read_yaml_mapping(root / ".tradingcodex" / "policies" / "access-policies.yaml")
    for condition in _policy_conditions(access_data):
        if condition.startswith("order.estimated_notional_krw <="):
            raw_limit = condition.split("<=", 1)[1].strip()
            if not raw_limit.isdigit():
                raise PolicyConfigurationError("order.estimated_notional_krw limit must be an integer")
            max_single_order = int(raw_limit)
        elif condition.startswith("order.broker in "):
            parsed = yaml.safe_load(condition.split(" in ", 1)[1].strip())
            if not isinstance(parsed, list) or not all(isinstance(item, str) and item for item in parsed):
                raise PolicyConfigurationError("order.broker condition must be a string list")
            allowed_adapters = set(parsed)
        elif condition.startswith("order.execution_posture in "):
            parsed = yaml.safe_load(condition.split(" in ", 1)[1].strip())
            if not isinstance(parsed, list) or not all(isinstance(item, str) and item for item in parsed):
                raise PolicyConfigurationError("order.execution_posture condition must be a string list")
            allowed_execution_postures = set(parsed)

    config_data = _read_yaml_mapping(root / ".tradingcodex" / "config.yaml")
    execution = config_data.get("execution", {}) if config_data else {}
    if execution:
        if not isinstance(execution, dict):
            raise PolicyConfigurationError("config.execution must be a mapping")
        configured = execution.get("enabled_adapters")
        if configured is not None:
            if not isinstance(configured, list) or not all(isinstance(item, str) and item for item in configured):
                raise PolicyConfigurationError("config.execution.enabled_adapters must be a string list")
            allowed_adapters &= set(configured)
        configured_postures = execution.get("enabled_execution_postures")
        if configured_postures is not None:
            if not isinstance(configured_postures, list) or not all(isinstance(item, str) and item for item in configured_postures):
                raise PolicyConfigurationError("config.execution.enabled_execution_postures must be a string list")
            allowed_execution_postures &= set(configured_postures)

    return RuntimePolicy(max_single_order, frozenset(allowed_adapters), frozenset(allowed_execution_postures), tuple(source))


def read_restricted_symbols(workspace_root: Path | str) -> set[str]:
    symbols: set[str] = set()
    try:
        ensure_runtime_database(workspace_root)
        from apps.policy.models import RestrictedSymbol

        symbols.update(symbol.upper() for symbol in RestrictedSymbol.objects.filter(active=True).values_list("symbol", flat=True))
    except Exception as exc:
        raise PolicyConfigurationError(f"restricted symbol DB unavailable: {exc}") from exc
    data = _read_yaml_mapping(Path(workspace_root) / ".tradingcodex" / "policies" / "restricted-list.yaml")
    configured = data.get("restricted_symbols", []) if data else []
    if configured is None:
        configured = []
    if not isinstance(configured, list) or not all(isinstance(item, str) and item for item in configured):
        raise PolicyConfigurationError("restricted_symbols must be a string list")
    symbols.update(symbol.upper() for symbol in configured)
    return symbols


def _read_yaml_mapping(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise PolicyConfigurationError(f"invalid YAML in {path}: {exc}") from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise PolicyConfigurationError(f"{path} must contain a YAML mapping")
    return data


def _policy_conditions(access_data: dict[str, Any]) -> list[str]:
    conditions: list[str] = []
    allow_rules = access_data.get("allow", []) if access_data else []
    if allow_rules is None:
        return conditions
    if not isinstance(allow_rules, list):
        raise PolicyConfigurationError("access-policies.allow must be a list")
    for rule in allow_rules:
        if not isinstance(rule, dict):
            raise PolicyConfigurationError("access-policies.allow entries must be mappings")
        rule_conditions = rule.get("conditions", [])
        if rule_conditions is None:
            continue
        if not isinstance(rule_conditions, list) or not all(isinstance(item, str) for item in rule_conditions):
            raise PolicyConfigurationError("access-policies allow conditions must be a string list")
        conditions.extend(condition.strip() for condition in rule_conditions)
    return conditions


def evaluate_policy(workspace_root: Path | str, args: dict[str, Any]) -> dict[str, Any]:
    ensure_runtime_database(workspace_root)
    from apps.policy.services import capability_check, sync_builtin_principals_and_capabilities
    from tradingcodex_service.application.orders import resolve_approval_receipt, resolve_order_ticket_payload

    sync_builtin_principals_and_capabilities()
    reasons: list[str] = []
    try:
        policy = read_runtime_policy(workspace_root)
    except PolicyConfigurationError as exc:
        policy = RuntimePolicy(0, frozenset(), frozenset(), ("invalid-runtime-policy",))
        reasons.append(f"runtime policy invalid: {exc}")
    order = resolve_order_ticket_payload(Path(workspace_root), args)
    receipt = resolve_approval_receipt(Path(workspace_root), args, order)
    principal_id = args.get("principal_id") or "unknown"
    action = args.get("action") or "unknown"
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
    if order.get("broker"):
        broker_allowed, broker_reason = _broker_allowed_by_policy(workspace_root, str(order["broker"]), policy)
        if not broker_allowed:
            reasons.append(broker_reason)

    notional = _number(order.get("estimated_notional_krw"))
    if order.get("estimated_notional_krw") not in (None, "") and (notional is None or notional <= 0):
        reasons.append("estimated_notional_krw must be a positive number")
    elif notional is not None and notional > policy.max_single_order_krw:
        reasons.append(f"estimated_notional_krw exceeds {policy.max_single_order_krw}")

    try:
        restricted_symbols = read_restricted_symbols(workspace_root)
    except PolicyConfigurationError as exc:
        restricted_symbols = set()
        reasons.append(f"restricted-list policy invalid: {exc}")
    if order.get("symbol") and str(order["symbol"]).upper() in restricted_symbols:
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


def _broker_allowed_by_policy(workspace_root: Path | str, broker_id: str, policy: RuntimePolicy) -> tuple[bool, str]:
    if broker_id in policy.allowed_adapters:
        return True, ""
    try:
        from apps.integrations.models import BrokerConnection

        connection = BrokerConnection.objects.filter(broker_id=broker_id).first()
    except Exception as exc:
        return False, f"adapter not enabled: {broker_id} (broker registry unavailable: {exc})"
    if connection is None:
        return False, f"adapter not enabled: {broker_id}"
    metadata = connection.metadata if isinstance(connection.metadata, dict) else {}
    profile = metadata.get("capability_profile") if isinstance(metadata.get("capability_profile"), dict) else {}
    posture = str(profile.get("execution_posture") or "")
    normalized_posture = _normalize_execution_posture(posture)
    if normalized_posture not in policy.allowed_execution_postures:
        return False, f"execution posture not enabled: {broker_id} ({posture or 'unknown'})"
    if connection.status != "trading_enabled":
        return False, f"broker connection is not trading_enabled: {broker_id}"
    if not connection.enabled_trade_scopes:
        return False, f"broker connection has no enabled trade scopes: {broker_id}"
    return True, ""


def _normalize_execution_posture(posture: str) -> str:
    aliases = {
        "testnet_order_test": "broker_validation_only",
    }
    return aliases.get(str(posture or ""), str(posture or ""))


def simulate_policy(workspace_root: Path | str, args: dict[str, Any]) -> dict[str, Any]:
    return evaluate_policy(workspace_root, args)

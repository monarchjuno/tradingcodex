from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping


SCHEMA_VERSION = "tradingcodex.codex-trace-audit.v3"
FIXED_ROLE_MARKER = "You are a fixed-role child in TradingCodex"
HEAD_MANAGER_MARKER = "You are the `head-manager` agent"
MAX_FIXED_ROLE_BASE_CHARS = 7_000
MAX_CATALOG_NAMES = 12
MAX_TRACE_DURATION_SECS = 31_536_000
MAX_REPORTED_TRUNCATION_TOKENS = 999_999_999_999
MAX_CUSTOM_OUTPUT_CHARS = 20_000
# Mirror the service transport contract without importing Django into the CLI.
MAX_ARTIFACT_CARD_RESPONSE_CHARS = 10_000
MAX_ARTIFACT_REVIEW_RESPONSE_CHARS = 18_000
MAX_ARTIFACT_LIST_RESPONSE_CHARS = 12_000
MAX_FIRST_PROGRESS_LATENCY_MS = 60_000
MAX_VISIBLE_SILENCE_MS = 60_000
MIN_WAIT_TIMEOUT_MS = 10_000
MAX_WAIT_TIMEOUT_MS = 30_000
EXPECTED_ROOT_MODEL = "gpt-5.6-sol"
EXPECTED_ROOT_EFFORT = "xhigh"
EXPECTED_CHILD_MODEL = "gpt-5.6-terra"
EXPECTED_CHILD_EFFORT = "high"
EXPECTED_SANDBOX_TYPE = "workspace-write"
EXPECTED_PERMISSION_PROFILE_TYPE = "managed"
FIXED_ROLES = {
    "fundamental-analyst",
    "instrument-analyst",
    "judgment-reviewer",
    "macro-analyst",
    "news-analyst",
    "portfolio-manager",
    "risk-manager",
    "technical-analyst",
    "valuation-analyst",
}
OPENBB_EVIDENCE_ROLES = {
    "fundamental-analyst",
    "technical-analyst",
    "news-analyst",
    "macro-analyst",
    "instrument-analyst",
    "valuation-analyst",
}
OFFICIAL_SOURCE_DATA_SERVER = "tradingcodex"
OFFICIAL_SOURCE_DATA_TOOL = "fetch_official_source_data"
OFFICIAL_SOURCE_DATA_MCP_TOOL = (
    f"mcp__{OFFICIAL_SOURCE_DATA_SERVER}__{OFFICIAL_SOURCE_DATA_TOOL}"
)
_EXTERNAL_FINANCIAL_COORDINATE_KEYS = frozenset(
    {
        "adjusted",
        "adjustment",
        "as_of",
        "asof",
        "cik",
        "columns",
        "contract",
        "date",
        "date_from",
        "date_to",
        "end",
        "end_date",
        "fields",
        "frequency",
        "identifier",
        "identifiers",
        "interval",
        "isin",
        "period",
        "provider",
        "provider_name",
        "query",
        "series_id",
        "start",
        "start_date",
        "symbol",
        "symbols",
        "ticker",
        "tickers",
    }
)
_FINANCIAL_TOOL_HINT_RE = re.compile(
    r"(?:^|[_-])(?:"
    r"bond|candle|cftc|commodity|crypto|dart|derivative|dividend|earnings|"
    r"econom(?:ic|y)|edgar|equity|exchange|filing|forex|fundamental|future|fx|"
    r"gdp|index|inflation|interest|macro|market|ohlc|option|price|quote|rate|"
    r"sec|security|stock|treasury|valuation|yield"
    r")(?:[_-]|$)"
)
_NON_FINANCIAL_CONNECTOR_RE = re.compile(
    r"(?:^|[_-])(?:"
    r"atlassian|box|calendar|docs|drive|email|figma|gmail|notion|outlook|"
    r"sharepoint|sheets|slack|slides|teams"
    r")(?:[_-]|$)"
)
_ROW_RESULT_STATES = frozenset({"complete_valid", "partial_valid"})
_RECEIPT_ONLY_RESULT_STATES = frozenset(
    {
        "approval_required",
        "conflict",
        "correctable_error",
        "terminal_gap",
        "transient",
        "unsafe",
    }
)
_NESTED_TOOL_RE = re.compile(r"\btools\.([A-Za-z0-9_]+)\s*\(")
_TRUNCATION_RE = re.compile(r"Warning: truncated output \(original token count: ([0-9,]+)\)")
_OMITTED_TOKENS_RE = re.compile(r"…([0-9,]+) tokens truncated…")
_SAFE_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9_./:-]{1,160}$")
_TASK_NAME_RE = re.compile(r"^[a-z0-9_]{1,64}$")
_TOOL_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,199}$")
_ARTIFACT_PATH_RE = re.compile(r"^trading/[A-Za-z0-9._/-]{1,480}$")
_ARTIFACT_HANDOFF_STATES = {"accepted", "blocked", "revise", "waiting"}
_ARTIFACT_CARD_FIELDS = {
    "artifact_id",
    "path",
    "artifact_type",
    "universe",
    "workflow_type",
    "role",
    "symbol",
    "title",
    "source_as_of",
    "readiness_label",
    "context_summary",
    "reader_summary",
    "handoff_state",
    "confidence",
    "missing_evidence",
    "next_recipient",
    "next_action",
    "blocked_actions",
    "workflow_run_id",
    "producer_role",
    "knowledge_cutoff",
    "content_hash",
    "version",
    "workspace_native",
    "card_max_serialized_chars",
    "card_truncated_fields",
}
_ARTIFACT_CARD_STRING_FIELDS = {
    "artifact_id",
    "path",
    "artifact_type",
    "universe",
    "workflow_type",
    "role",
    "symbol",
    "title",
    "source_as_of",
    "readiness_label",
    "context_summary",
    "reader_summary",
    "handoff_state",
    "next_recipient",
    "next_action",
    "workflow_run_id",
    "producer_role",
    "knowledge_cutoff",
    "content_hash",
}
_DESCRIPTION_ACCESS_RE = re.compile(
    r"(?:\.\s*description\b|\[\s*['\"]description['\"]\s*\]|"
    r"\{[^{}\n]{0,200}\bdescription(?:\s*[:,}]|\s+as\b))"
)
_SUCCESSFUL_EXEC_STATUS_RE = re.compile(
    r"\AScript completed\nWall time (?:0|[1-9][0-9]*)(?:\.[0-9]+)? seconds\nOutput:\n\Z"
)
_SPAWN_ARGUMENT_KEYS = {"agent_type", "fork_turns", "message", "task_name"}
_TOKEN_REQUIRED_KEYS = {
    "input_tokens",
    "cached_input_tokens",
    "output_tokens",
    "reasoning_output_tokens",
    "total_tokens",
}
_TOKEN_KEYS = _TOKEN_REQUIRED_KEYS | {"cache_write_input_tokens"}
_KNOWN_ACTIVITY_KINDS = {
    "completed",
    "failed",
    "interacted",
    "started",
    "stopped",
}
_KNOWN_FUNCTION_TOOLS = {
    "followup_task",
    "get_goal",
    "interrupt_agent",
    "list_agents",
    "request_user_input",
    "send_message",
    "spawn_agent",
    "update_goal",
    "update_plan",
    "wait",
    "wait_agent",
}
_CHILD_FORBIDDEN_COORDINATION_TOOLS = {
    "followup_task",
    "interrupt_agent",
    "list_agents",
    "send_message",
    "spawn_agent",
    "wait_agent",
}
_KNOWN_NESTED_TOOL_NAMES = {
    "apply_patch",
    "exec_command",
    "shell_command",
    "view_image",
    "web__run",
    "write_stdin",
}
_KNOWN_TCX_TOOL_NAMES = {
    "append_research_artifact_version",
    "begin_analysis_run",
    "compare_calculation_runs",
    "compare_evaluation_runs",
    "complete_judgment_review",
    "create_blind_review_assignment",
    "create_causal_equity_analysis",
    "create_evaluation_corpus",
    "create_order_ticket",
    "create_replay_manifest",
    "create_research_artifact",
    "create_research_spec",
    "discard_draft_order",
    "export_research_artifact_md",
    "export_dataset_csv",
    "fetch_official_source_data",
    "get_blind_review_packet",
    "get_broker_capability_profile",
    "get_broker_connection_status",
    "get_broker_instrument_constraints",
    "get_calculation_run",
    "get_connector_build_status",
    "get_data_acquisition_receipt",
    "get_dataset_manifest",
    "get_dataset_rows",
    "get_data_source_status",
    "get_forecast",
    "get_forecast_calibration_report",
    "get_order_status",
    "get_order_ticket",
    "get_official_source_plan",
    "get_portfolio_snapshot",
    "get_positions",
    "get_research_artifact",
    "get_research_spec",
    "get_source_snapshot",
    "get_runtime_mode",
    "get_tradingcodex_status",
    "get_update_status",
    "issue_forecast",
    "list_artifact_catalog",
    "list_broker_adapter_providers",
    "list_broker_connections",
    "list_codex_capabilities",
    "list_forecasts",
    "list_order_tickets",
    "list_reconciliation_runs",
    "list_research_artifacts",
    "list_research_specs",
    "list_workflow_artifacts",
    "manage_investment_brain",
    "manage_strategy",
    "materialize_dataset_slice",
    "prepare_calculation",
    "preview_order_translation",
    "profile_dataset",
    "promote_lesson",
    "rebuild_artifact_catalog",
    "rebuild_research_index",
    "record_audit_event",
    "record_blind_human_review",
    "record_blind_judgment_prior",
    "record_calculation_run",
    "record_dataset_snapshot",
    "record_evaluation_run",
    "record_experiment_run",
    "record_external_data_result",
    "record_source_snapshot",
    "register_broker_connector",
    "render_broker_connector_scaffold",
    "request_order_approval",
    "resolve_forecast",
    "revise_forecast",
    "run_order_checks",
    "score_forecast",
    "search_artifact_catalog",
    "search_calculations",
    "search_datasets",
    "search_research_artifacts",
    "simulate_policy",
    "sync_broker_account",
    "use_order_turn_grant",
    "validate_approval_receipt",
    "validate_broker_connector_build",
}


class TraceAuditError(ValueError):
    """Raised when an explicitly selected Codex rollout cannot be audited."""


def _iter_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    try:
        handle = path.open(encoding="utf-8")
    except OSError as exc:
        raise TraceAuditError(f"cannot read rollout: {path}") from exc
    with handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                raise TraceAuditError(f"invalid JSONL at {path.name}:{line_number}") from exc
            if not isinstance(item, dict):
                raise TraceAuditError(f"JSONL item is not an object at {path.name}:{line_number}")
            yield item


def _session_meta(path: Path) -> dict[str, Any]:
    for item in _iter_jsonl(path):
        if item.get("type") == "session_meta" and isinstance(item.get("payload"), dict):
            return item["payload"]
    raise TraceAuditError(f"rollout has no session_meta: {path}")


def _thread_spawn(meta: Mapping[str, Any]) -> dict[str, Any]:
    source = meta.get("source")
    if not isinstance(source, Mapping):
        return {}
    subagent = source.get("subagent")
    if not isinstance(subagent, Mapping):
        return {}
    spawn = subagent.get("thread_spawn")
    return dict(spawn) if isinstance(spawn, Mapping) else {}


def _started_children(path: Path) -> list[dict[str, str]]:
    started: list[dict[str, str]] = []
    for item in _iter_jsonl(path):
        payload = item.get("payload")
        if item.get("type") != "event_msg" or not isinstance(payload, Mapping):
            continue
        if payload.get("type") != "sub_agent_activity" or payload.get("kind") != "started":
            continue
        thread_id = payload.get("agent_thread_id")
        if not isinstance(thread_id, str) or not thread_id:
            continue
        started.append(
            {
                "thread_id": thread_id,
                "event_id": str(payload.get("event_id") or ""),
                "agent_path": str(payload.get("agent_path") or ""),
            }
        )
    return started


def _session_tree_root(root_rollout: Path) -> Path:
    day = root_rollout.parent
    month = day.parent
    year = month.parent
    if (
        len(day.name) == 2
        and day.name.isdigit()
        and len(month.name) == 2
        and month.name.isdigit()
        and len(year.name) == 4
        and year.name.isdigit()
    ):
        return year.parent
    return root_rollout.parent


def _find_rollout_for_thread(search_root: Path, thread_id: str) -> tuple[Path, dict[str, Any]] | None:
    matches: list[tuple[Path, dict[str, Any]]] = []
    for candidate in search_root.rglob(f"*{thread_id}.jsonl"):
        try:
            meta = _session_meta(candidate)
        except TraceAuditError:
            continue
        if str(meta.get("id") or "") == thread_id:
            matches.append((candidate.resolve(), meta))
    if len(matches) > 1:
        raise TraceAuditError(f"multiple rollouts found for child thread: {thread_id}")
    return matches[0] if matches else None


def discover_rollouts(root_rollout: Path) -> list[tuple[Path, dict[str, Any]]]:
    """Follow authoritative started-child events to transitive descendant rollouts."""

    root_rollout = root_rollout.expanduser().resolve()
    root_meta = _session_meta(root_rollout)
    root_id = str(root_meta.get("id") or "")
    if not root_id:
        raise TraceAuditError(f"root rollout has no session id: {root_rollout}")

    search_root = _session_tree_root(root_rollout)
    discovered: list[tuple[Path, dict[str, Any]]] = [(root_rollout, root_meta)]
    queue = [(root_rollout, root_meta)]
    seen = {root_id}
    while queue:
        parent_path, _parent_meta = queue.pop(0)
        for child in _started_children(parent_path):
            thread_id = child["thread_id"]
            if thread_id in seen:
                continue
            seen.add(thread_id)
            located = _find_rollout_for_thread(search_root, thread_id)
            if located is None:
                continue
            discovered.append(located)
            queue.append(located)
    return discovered


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _fingerprint(server: str, tool: str, arguments: Any) -> str:
    material = _canonical_json({"server": server, "tool": tool, "arguments": arguments})
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _first_argument_value(arguments: Mapping[str, Any], names: tuple[str, ...]) -> Any:
    for name in names:
        if name in arguments:
            return arguments[name]
    return None


def _external_semantic_fingerprint(
    server: str,
    tool: str,
    arguments: Mapping[str, Any],
) -> str:
    is_official_source = (
        server == OFFICIAL_SOURCE_DATA_SERVER and tool == OFFICIAL_SOURCE_DATA_TOOL
    )
    material = {
        "server": server,
        "tool": tool,
        "provider": _coordinate_scalar(
            _first_argument_value(
                arguments, ("provider", "provider_name", "source_id")
            )
        ),
        "identifiers": _coordinate_values(
            _first_argument_value(
                arguments,
                (
                    "identifiers",
                    "identifier",
                    "symbols",
                    "symbol",
                    "tickers",
                    "ticker",
                    "isin",
                    "cik",
                    "series_id",
                    "contract",
                ),
            ),
            sort_items=True,
        ),
        "fields": _coordinate_values(
            _first_argument_value(arguments, ("fields", "columns")),
            sort_items=True,
        ),
        "start": _coordinate_scalar(
            _first_argument_value(
                arguments, ("start_date", "start", "date_from", "period_start")
            )
        ),
        "end": _coordinate_scalar(
            _first_argument_value(
                arguments, ("end_date", "end", "date_to", "period_end")
            )
        ),
        "as_of": _coordinate_scalar(
            _first_argument_value(arguments, ("as_of", "asof", "date"))
        ),
        "interval": _semantic_frequency(
            _first_argument_value(arguments, ("interval", "frequency", "period"))
        ),
        "adjustment": _semantic_adjustment(
            "not_applicable"
            if is_official_source
            else _first_argument_value(arguments, ("adjustment", "adjusted"))
        ),
        "query": (
            {
                "data_kind": _coordinate_scalar(arguments.get("data_kind")),
                "asset_class": _coordinate_scalar(arguments.get("asset_class")),
                "region": _coordinate_scalar(arguments.get("region")),
                "source_policy": _coordinate_scalar(
                    arguments.get("source_policy") or "best_available"
                ),
            }
            if is_official_source
            else {}
        ),
    }
    return hashlib.sha256(_canonical_json(material).encode("utf-8")).hexdigest()


def _recognized_external_financial_data_call(
    server: str,
    tool: str,
    arguments: Mapping[str, Any],
) -> bool:
    """Return whether an external invocation is an observable financial-data call.

    OpenBB's catalog and activation methods are administrative, not data results.
    Other MCP servers require both a financial endpoint hint and at least one
    query coordinate. This deliberately avoids treating ordinary connector
    searches (Gmail, Notion, Drive, and similar) as market-data activity merely
    because they are external MCP calls.
    """

    if server == OFFICIAL_SOURCE_DATA_SERVER:
        return tool == OFFICIAL_SOURCE_DATA_TOOL
    if server == "openbb":
        return not _openbb_admin_tool(tool) and not _openbb_forbidden_tool(
            tool, arguments
        )
    combined = f"{server}_{tool}".lower()
    if _NON_FINANCIAL_CONNECTOR_RE.search(combined):
        return False
    if not _FINANCIAL_TOOL_HINT_RE.search(combined):
        return False
    return any(str(key).lower() in _EXTERNAL_FINANCIAL_COORDINATE_KEYS for key in arguments)


def _external_tool_fqn(server: str, tool: str) -> str:
    return f"mcp__{server}__{tool}"


def _observable_returned_provider(result: Any) -> str:
    providers: set[str] = set()

    def collect(value: Any) -> None:
        if isinstance(value, Mapping):
            for key, child in value.items():
                if str(key).lower() in {"provider", "returned_provider"}:
                    if isinstance(child, str) and child.strip():
                        providers.add(child.strip())
                else:
                    collect(child)
        elif isinstance(value, list):
            for child in value:
                collect(child)

    for value in _parse_json_texts(_result_texts(result)):
        collect(value)
    return next(iter(providers)) if len(providers) == 1 else ""


def _observable_official_result(result: Any) -> dict[str, Any]:
    """Extract the official producer envelope needed for promotion matching.

    The raw result remains out of the emitted trace report. Only the in-memory
    matcher receives the reviewed recorder argument template returned by the
    exact TradingCodex producer tool.
    """

    candidates: dict[str, dict[str, Any]] = {}
    for value in _parse_json_texts(_result_texts(result)):
        if not isinstance(value, Mapping):
            continue
        accepted = value.get("accepted_results")
        attempts = value.get("attempts")
        if not isinstance(accepted, list) or not isinstance(attempts, list):
            continue
        record_arguments = value.get("record_external_data_result_args")
        if not isinstance(record_arguments, Mapping):
            continue
        observable = {
            "status": str(value.get("status") or "").strip(),
            "source_policy": str(value.get("source_policy") or "").strip(),
            "data_kind": str(value.get("data_kind") or "").strip(),
            "region": str(value.get("region") or "").strip(),
            "selected_source_id": str(
                value.get("selected_source_id") or ""
            ).strip(),
            "record_arguments": dict(record_arguments),
            "attempt_source_ids": [
                str(item.get("source_id") or "").strip()
                for item in attempts
                if isinstance(item, Mapping) and str(item.get("source_id") or "").strip()
            ],
        }
        candidates[_canonical_json(observable)] = observable
    return next(iter(candidates.values())) if len(candidates) == 1 else {}


def _coordinate_values(value: Any, *, sort_items: bool = False) -> list[str]:
    if value is None:
        return []
    raw = value if isinstance(value, list) else [value]
    normalized = [str(item).strip().casefold() for item in raw if str(item).strip()]
    return sorted(normalized) if sort_items else normalized


def _coordinate_scalar(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return text.casefold()
    date_only = len(text) == 10 and text[4:5] == "-" and text[7:8] == "-"
    if parsed.tzinfo is None:
        if date_only or parsed.time() == datetime.min.time():
            return parsed.date().isoformat()
        return parsed.isoformat().casefold()
    utc_value = parsed.astimezone(timezone.utc)
    if date_only or utc_value.time() == datetime.min.time():
        return utc_value.date().isoformat()
    return utc_value.isoformat().casefold()


def _semantic_frequency(value: Any) -> str:
    normalized = re.sub(r"[\s_-]+", "", str(value or "").strip().casefold())
    aliases = {
        "d": "1d",
        "1d": "1d",
        "1day": "1d",
        "day": "1d",
        "daily": "1d",
        "eod": "1d",
        "h": "1h",
        "1h": "1h",
        "1hour": "1h",
        "hour": "1h",
        "hourly": "1h",
        "1min": "1min",
        "minute": "1min",
        "minutely": "1min",
        "w": "1w",
        "1w": "1w",
        "1wk": "1w",
        "1week": "1w",
        "week": "1w",
        "weekly": "1w",
        "1mo": "1mo",
        "1month": "1mo",
        "month": "1mo",
        "monthly": "1mo",
        "3mo": "1q",
        "quarter": "1q",
        "quarterly": "1q",
        "1y": "1y",
        "1yr": "1y",
        "annual": "1y",
        "annually": "1y",
        "year": "1y",
        "yearly": "1y",
    }
    return aliases.get(normalized, normalized)


def _semantic_adjustment(value: Any) -> str:
    if value is True:
        return "adjusted"
    if value is False:
        return "unadjusted"
    normalized = re.sub(r"[\s_-]+", "", str(value or "").strip().casefold())
    return {
        "adjust": "adjusted",
        "adjusted": "adjusted",
        "true": "adjusted",
        "none": "unadjusted",
        "false": "unadjusted",
        "raw": "unadjusted",
        "unadjusted": "unadjusted",
    }.get(normalized, normalized)


def _coordinate_matches(left: Any, right: Any) -> bool:
    left_text = str(left or "").strip()
    right_text = str(right or "").strip()
    left_date_only = bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", left_text))
    right_date_only = bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", right_text))
    # A reviewed producer can accept a calendar date while DataNeed preserves
    # the same market-local boundary as an RFC 3339 timestamp. Compare that
    # explicit calendar component before UTC normalization shifts midnight to
    # the previous day (for example, 00:00 Asia/Seoul -> 15:00Z).
    if left_date_only and right_text[:10] == left_text:
        return True
    if right_date_only and left_text[:10] == right_text:
        return True
    left_normalized = _coordinate_scalar(left)
    right_normalized = _coordinate_scalar(right)
    if left_normalized == right_normalized:
        return True
    # Provider queries commonly use YYYY-MM-DD while DataNeed uses midnight UTC.
    return bool(
        left_normalized
        and right_normalized
        and left_normalized[:10] == right_normalized[:10]
        and (
            len(left_normalized) == 10
            or len(right_normalized) == 10
        )
    )


def _matching_argument(
    arguments: Mapping[str, Any], names: tuple[str, ...]
) -> tuple[bool, Any]:
    for name in names:
        if name in arguments:
            return True, arguments[name]
    return False, None


def _official_request_matches_data_need(
    external_arguments: Mapping[str, Any], data_need: Mapping[str, Any]
) -> bool:
    scalar_contracts = (
        (("data_kind",), "data_kind"),
        (("asset_class",), "asset_type"),
        (("period_start",), "period_start"),
        (("period_end",), "period_end"),
        (("as_of",), "as_of"),
    )
    for external_names, need_name in scalar_contracts:
        present, expected = _matching_argument(external_arguments, external_names)
        if present and not _coordinate_matches(expected, data_need.get(need_name)):
            return False

    expected_policy = str(
        external_arguments.get("source_policy") or "best_available"
    ).strip()
    if _coordinate_scalar(expected_policy) != _coordinate_scalar(
        data_need.get("source_policy")
    ):
        return False

    identifiers = _coordinate_values(
        external_arguments.get("identifiers"), sort_items=True
    )
    if not identifiers or identifiers != _coordinate_values(
        data_need.get("identifiers"), sort_items=True
    ):
        return False
    requested_fields = _coordinate_values(
        external_arguments.get("fields"), sort_items=True
    )
    need_fields = _coordinate_values(data_need.get("fields"), sort_items=True)
    if requested_fields and not set(requested_fields) <= set(need_fields):
        return False
    return True


def _official_promotion_matches_external_call(
    external: Mapping[str, Any], arguments: Mapping[str, Any]
) -> bool:
    if arguments.get("transport") != "tradingcodex-official":
        return False
    external_arguments = external.get("arguments")
    data_need = arguments.get("data_need")
    if not isinstance(external_arguments, Mapping) or not isinstance(data_need, Mapping):
        return False
    if not _official_request_matches_data_need(external_arguments, data_need):
        return False

    official_result = external.get("official_result")
    if not isinstance(official_result, Mapping):
        return False
    if _coordinate_scalar(official_result.get("data_kind")) != _coordinate_scalar(
        external_arguments.get("data_kind")
    ):
        return False
    if _coordinate_scalar(official_result.get("region")) != _coordinate_scalar(
        external_arguments.get("region")
    ):
        return False
    if _coordinate_scalar(official_result.get("source_policy")) != _coordinate_scalar(
        external_arguments.get("source_policy") or "best_available"
    ):
        return False
    record_arguments = official_result.get("record_arguments")
    if not isinstance(record_arguments, Mapping) or not record_arguments:
        return False
    returned_adjustment = _semantic_adjustment(
        record_arguments.get("returned_adjustment_policy")
    )
    requested_adjustment = _semantic_adjustment(
        data_need.get("adjustment_policy")
    )
    # A typed rowless failure cannot attest a returned adjustment policy. When
    # the producer does attest one, keep it bound to the DataNeed; the exact
    # recorder-template comparison below also prevents the caller changing it.
    if returned_adjustment and returned_adjustment != requested_adjustment:
        return False
    return all(
        key in arguments
        and _canonical_json(arguments.get(key)) == _canonical_json(expected)
        for key, expected in record_arguments.items()
    )


def _promotion_matches_external_call(
    external: Mapping[str, Any],
    arguments: Mapping[str, Any],
) -> bool:
    server = str(external.get("server") or "")
    tool = str(external.get("tool") or "")
    is_official_source = (
        server == OFFICIAL_SOURCE_DATA_SERVER and tool == OFFICIAL_SOURCE_DATA_TOOL
    )
    source_tier = (
        "tradingcodex"
        if is_official_source
        else "openbb"
        if server == "openbb"
        else "user_capability"
    )
    if arguments.get("source_tier") != source_tier:
        return False
    if arguments.get("tool_name") != _external_tool_fqn(server, tool):
        return False
    if is_official_source:
        return _official_promotion_matches_external_call(external, arguments)

    external_arguments = external.get("arguments")
    if not isinstance(external_arguments, Mapping):
        return False
    provider_present, provider = _matching_argument(
        external_arguments, ("provider", "provider_name")
    )
    provider_query = arguments.get("provider_query")
    data_need = arguments.get("data_need")
    if not isinstance(provider_query, Mapping) or not isinstance(data_need, Mapping):
        return False
    if provider_present:
        provider_text = str(provider or "").strip()
        if not provider_text or arguments.get("requested_provider") != provider_text:
            return False
        if arguments.get("upstream_provider") != provider_text:
            return False
        query_provider_present, query_provider = _matching_argument(
            provider_query, ("provider", "provider_name")
        )
        if not query_provider_present or str(query_provider or "").strip() != provider_text:
            return False
    returned_provider = str(external.get("returned_provider") or "")
    if returned_provider and arguments.get("returned_provider") != returned_provider:
        return False

    coordinate_contracts = (
        (
            ("identifiers", "identifier", "symbols", "symbol", "tickers", "ticker", "isin", "cik", "series_id", "contract"),
            "identifiers",
            True,
        ),
        (("fields", "columns"), "fields", True),
    )
    for external_names, need_name, sort_items in coordinate_contracts:
        present, expected = _matching_argument(external_arguments, external_names)
        if not present:
            continue
        query_present, query_value = _matching_argument(provider_query, external_names)
        if not query_present or _coordinate_values(
            query_value, sort_items=sort_items
        ) != _coordinate_values(expected, sort_items=sort_items):
            return False
        need_values = _coordinate_values(data_need.get(need_name), sort_items=True)
        expected_values = _coordinate_values(expected, sort_items=True)
        if need_name == "fields":
            if not set(expected_values) <= set(need_values):
                return False
        elif need_values != expected_values:
            return False

    scalar_contracts = (
        (("start_date", "start", "date_from"), "period_start"),
        (("end_date", "end", "date_to"), "period_end"),
        (("as_of", "asof", "date"), "as_of"),
        (("interval", "frequency", "period"), "frequency"),
        (("adjustment", "adjusted"), "adjustment_policy"),
    )
    for external_names, need_name in scalar_contracts:
        present, expected = _matching_argument(external_arguments, external_names)
        if not present:
            continue
        query_present, query_value = _matching_argument(provider_query, external_names)
        if need_name == "frequency":
            query_matches = _semantic_frequency(expected) == _semantic_frequency(
                query_value
            )
            need_matches = _semantic_frequency(expected) == _semantic_frequency(
                data_need.get(need_name)
            )
        elif need_name == "adjustment_policy":
            query_matches = _semantic_adjustment(expected) == _semantic_adjustment(
                query_value
            )
            need_matches = _semantic_adjustment(expected) == _semantic_adjustment(
                data_need.get(need_name)
            )
        else:
            query_matches = _coordinate_matches(expected, query_value)
            need_matches = _coordinate_matches(expected, data_need.get(need_name))
        if not query_present or not query_matches:
            return False
        if not need_matches:
            return False
    return True


def _validated_external_promotion(
    arguments: Mapping[str, Any], response: Mapping[str, Any] | None
) -> str | None:
    if response is None or response.get("status") not in {"recorded", "existing"}:
        return None
    receipt = response.get("receipt")
    if not isinstance(receipt, Mapping):
        return None
    receipt_id = response.get("receipt_id")
    snapshot_id = response.get("snapshot_id")
    dataset_id = response.get("dataset_id")
    result_status = arguments.get("result_status")
    if (
        not isinstance(receipt_id, str)
        or not receipt_id
        or receipt.get("receipt_id") != receipt_id
        or receipt.get("result_status") != result_status
        or receipt.get("tool_name") != arguments.get("tool_name")
        or receipt.get("requested_provider") != arguments.get("requested_provider")
        or receipt.get("returned_provider") != arguments.get("returned_provider", "")
        or receipt.get("upstream_provider") != arguments.get("upstream_provider")
    ):
        return None
    rows = arguments.get("rows")
    row_bearing = isinstance(rows, list) and bool(rows)
    if result_status in _ROW_RESULT_STATES and not row_bearing:
        return None
    if result_status in _ROW_RESULT_STATES or (
        result_status == "conflict" and row_bearing
    ):
        if not all(isinstance(value, str) and value for value in (snapshot_id, dataset_id)):
            return None
        if (
            receipt.get("snapshot_id") != snapshot_id
            or receipt.get("dataset_id") != dataset_id
            or not isinstance(receipt.get("row_count"), int)
            or int(receipt["row_count"]) <= 0
        ):
            return None
        return "dataset"
    if result_status not in _RECEIPT_ONLY_RESULT_STATES or row_bearing:
        return None
    if snapshot_id not in {"", None} or dataset_id not in {"", None}:
        return None
    if (
        receipt.get("snapshot_id") not in {"", None}
        or receipt.get("dataset_id") not in {"", None}
        or receipt.get("row_count") != 0
    ):
        return None
    return "receipt_only"


def _safe_external_server(value: Any) -> str:
    text = str(value or "")
    if text in {"openbb", OFFICIAL_SOURCE_DATA_SERVER}:
        return text
    return f"sha256:{_value_fingerprint(text)}"


def _safe_external_tool(server: str, value: Any) -> str:
    text = str(value or "")
    if (
        server == OFFICIAL_SOURCE_DATA_SERVER
        and text == OFFICIAL_SOURCE_DATA_TOOL
    ):
        return text
    if server == "openbb" and _TOOL_NAME_RE.fullmatch(text):
        return text
    return f"sha256:{_value_fingerprint(text)}"


def _openbb_admin_tool(tool: str) -> bool:
    return tool in {"activate_tools", "available_tools", "get_tool", "health", "status"}


def _openbb_admin_scope_fingerprints(
    *,
    root_id: str,
    session_id: str,
    role: str,
    tool: str,
    arguments: Mapping[str, Any],
) -> set[str]:
    if tool not in {"available_tools", "activate_tools"}:
        return set()
    workflow = _coordinate_scalar(
        _first_argument_value(arguments, ("workflow_run_id", "run_id"))
    ) or root_id
    category = str(arguments.get("category") or "").strip().casefold()
    subcategory = str(
        arguments.get("subcategory") or arguments.get("sub_category") or ""
    ).strip().casefold()
    scopes: set[tuple[str, str]] = set()
    if category and tool == "available_tools":
        scopes.add((category, subcategory or "*"))
    elif tool == "available_tools":
        # Keep malformed unscoped discovery observable; two such calls are
        # still the same invalid scope rather than distinct query strings.
        scopes.add(("", ""))
    else:
        requested = _first_argument_value(arguments, ("tool_names", "tools", "names"))
        if isinstance(requested, str):
            names = [item.strip().casefold() for item in requested.split(",") if item.strip()]
        elif isinstance(requested, list):
            names = [str(item).strip().casefold() for item in requested if str(item).strip()]
        else:
            names = []
        for name in names:
            tokens = [item for item in re.split(r"[^a-z0-9]+", name) if item]
            if len(tokens) >= 2 and (not category or tokens[0] == category):
                scopes.add((tokens[0], subcategory or tokens[1]))
    return {
        hashlib.sha256(
            _canonical_json(
                {
                    "workflow": workflow,
                    "role": role,
                    "session": session_id,
                    "tool": tool,
                    "category": scoped_category,
                    "subcategory": scoped_subcategory,
                }
            ).encode("utf-8")
        ).hexdigest()
        for scoped_category, scoped_subcategory in scopes
    }


def _openbb_forbidden_tool(tool: str, arguments: Mapping[str, Any]) -> bool:
    if tool == "install_skill" or "download" in tool:
        return True
    if re.search(
        r"(?:^|_)(?:account|broker|cancel|connect|create|delete|execute|install|order|patch|post|put|remove|submit|trade|transfer|update|upload|withdraw)(?:_|$)",
        tool,
    ):
        return True
    method = str(arguments.get("method") or arguments.get("http_method") or "GET").upper()
    return method not in {"GET", "HEAD"}


def _value_fingerprint(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _safe_identifier(value: Any) -> str:
    text = str(value or "")
    if _SAFE_IDENTIFIER_RE.fullmatch(text):
        return text
    return f"sha256:{_value_fingerprint(text)}"


def _safe_role(value: Any) -> str:
    text = str(value or "")
    return text if text in FIXED_ROLES else f"invalid-sha256:{_value_fingerprint(text)}"


def _safe_agent_nickname(value: Any, role: Any) -> str:
    """Normalize only Codex's exact native ordinal collision suffix."""

    text = str(value or "")
    role_text = str(role or "")
    if text == role_text:
        return _safe_role(role_text)
    if role_text in FIXED_ROLES and text.startswith(f"{role_text} the "):
        ordinal = text.removeprefix(f"{role_text} the ")
        match = re.fullmatch(r"([1-9][0-9]{0,2})(st|nd|rd|th)", ordinal)
        if match:
            number = int(match.group(1))
            suffix = match.group(2)
            expected_suffix = (
                "th"
                if 10 <= number % 100 <= 20
                else {1: "st", 2: "nd", 3: "rd"}.get(number % 10, "th")
            )
            if number >= 2 and suffix == expected_suffix:
                return role_text
    return f"invalid-sha256:{_value_fingerprint(text)}"


def _safe_contract_value(value: Any, expected: str) -> str:
    text = str(value or "")
    return expected if text == expected else f"invalid-sha256:{_value_fingerprint(text)}"


def _safe_activity_kind(value: Any) -> str:
    text = str(value or "")
    return text if text in _KNOWN_ACTIVITY_KINDS else f"invalid-sha256:{_value_fingerprint(text)}"


def _safe_function_tool(value: Any) -> str:
    text = str(value or "")
    return text if text in _KNOWN_FUNCTION_TOOLS else f"invalid-sha256:{_value_fingerprint(text)}"


def _safe_nested_tool(value: Any) -> str:
    text = str(value or "")
    if text in _KNOWN_NESTED_TOOL_NAMES or re.fullmatch(
        r"mcp__(?:openbb|tradingcodex)__[a-z][a-z0-9_]{0,127}", text
    ):
        return text
    return f"invalid-sha256:{_value_fingerprint(text)}"


def _safe_tcx_tool(value: Any) -> str:
    text = str(value or "")
    return (
        text
        if text in _KNOWN_TCX_TOOL_NAMES
        else f"invalid-sha256:{_value_fingerprint(text)}"
    )


def _optional_nonnegative_int(
    value: Any, *, field: str, max_value: int | None = None
) -> int | None:
    if value is None:
        return None
    if (
        isinstance(value, int)
        and not isinstance(value, bool)
        and value >= 0
        and (max_value is None or value <= max_value)
    ):
        return value
    suffix = f" no greater than {max_value}" if max_value is not None else ""
    raise TraceAuditError(f"{field} must be a non-negative integer{suffix}")


def _normalized_token_usage(value: Any) -> dict[str, int] | None:
    if not isinstance(value, Mapping) or not _TOKEN_REQUIRED_KEYS <= set(value):
        return None
    normalized = {key: value.get(key, 0) for key in _TOKEN_KEYS}
    if not all(
        isinstance(item, int) and not isinstance(item, bool) and item >= 0
        for item in normalized.values()
    ):
        return None
    if (
        normalized["cached_input_tokens"] > normalized["input_tokens"]
        or normalized["reasoning_output_tokens"] > normalized["output_tokens"]
        or normalized["total_tokens"]
        != normalized["input_tokens"] + normalized["output_tokens"]
    ):
        return None
    return {key: int(normalized[key]) for key in _TOKEN_KEYS}


def _duration_ms(value: Any) -> float:
    if not isinstance(value, Mapping):
        return 0.0
    secs = value.get("secs", 0)
    nanos = value.get("nanos", 0)
    if not isinstance(secs, (int, float)) or isinstance(secs, bool) or secs < 0:
        raise TraceAuditError("MCP duration secs/nanos must be finite non-negative numbers")
    if not isinstance(nanos, (int, float)) or isinstance(nanos, bool) or nanos < 0:
        raise TraceAuditError("MCP duration secs/nanos must be finite non-negative numbers")
    if (
        secs > MAX_TRACE_DURATION_SECS
        or nanos >= 1_000_000_000
        or (isinstance(secs, float) and not math.isfinite(secs))
        or (isinstance(nanos, float) and not math.isfinite(nanos))
    ):
        raise TraceAuditError("MCP duration secs/nanos are outside the supported finite range")
    duration_ms = (float(secs) * 1000) + (float(nanos) / 1_000_000)
    if not math.isfinite(duration_ms):
        raise TraceAuditError("MCP duration is outside the supported finite range")
    return round(duration_ms, 3)


def _observable_timestamp(value: Any) -> datetime | None:
    """Parse one timezone-aware rollout timestamp without exporting its value."""

    if not isinstance(value, str) or not value:
        return None
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    try:
        return parsed.astimezone(timezone.utc)
    except (OverflowError, ValueError):
        return None


def _elapsed_ms(start: datetime, end: datetime) -> int | None:
    delta = end - start
    if delta.total_seconds() < 0:
        return None
    return (
        delta.days * 86_400_000
        + delta.seconds * 1_000
        + delta.microseconds // 1_000
    )


def _progress_cadence(
    *,
    task_started_at: datetime | None,
    progress_timestamps: list[datetime],
    progress_timestamps_complete: bool,
    final_answer_at: datetime | None,
    final_answer_timestamp_complete: bool,
    task_complete_at: datetime | None,
    task_complete_timestamp_complete: bool,
    latest_observed_at: datetime | None,
) -> tuple[int | None, int | None]:
    """Return privacy-safe cadence timings only when the timeline proves them."""

    if task_started_at is None or not progress_timestamps_complete:
        return None, None

    first_progress_latency_ms: int | None = None
    if progress_timestamps:
        first_progress_latency_ms = _elapsed_ms(
            task_started_at, progress_timestamps[0]
        )
        if first_progress_latency_ms is None:
            return None, None

    if not final_answer_timestamp_complete:
        return first_progress_latency_ms, None
    if final_answer_at is not None:
        endpoint = final_answer_at
    else:
        if not task_complete_timestamp_complete:
            return first_progress_latency_ms, None
        endpoint = task_complete_at or latest_observed_at
    if endpoint is None:
        return first_progress_latency_ms, None

    visible_boundaries = [task_started_at, *progress_timestamps, endpoint]
    silences: list[int] = []
    for start, end in zip(visible_boundaries, visible_boundaries[1:]):
        elapsed = _elapsed_ms(start, end)
        if elapsed is None:
            return first_progress_latency_ms, None
        silences.append(elapsed)
    return first_progress_latency_ms, max(silences, default=0)


def _bounded_decimal_count(value: str) -> tuple[int, bool]:
    digits = value.replace(",", "")
    if len(digits) > len(str(MAX_REPORTED_TRUNCATION_TOKENS)):
        return MAX_REPORTED_TRUNCATION_TOKENS, True
    return min(int(digits), MAX_REPORTED_TRUNCATION_TOKENS), False


def _json_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        return str(value)


def _result_texts(result: Any) -> list[str]:
    if not isinstance(result, Mapping):
        return []
    ok = result.get("Ok")
    if not isinstance(ok, Mapping):
        return []
    content = ok.get("content")
    if not isinstance(content, list):
        return []
    return [str(block.get("text")) for block in content if isinstance(block, Mapping) and isinstance(block.get("text"), str)]


def _result_status(result: Any) -> str:
    if not isinstance(result, Mapping):
        return "error"
    if "Err" in result:
        return "error"
    ok = result.get("Ok")
    if not isinstance(ok, Mapping) or ok.get("isError") is True:
        return "error"
    content = ok.get("content")
    if not isinstance(content, list) or not all(
        isinstance(block, Mapping)
        and block.get("type") == "text"
        and isinstance(block.get("text"), str)
        for block in content
    ):
        return "error"
    return "ok"


def _error_contract(result: Any, parsed_texts: Iterable[Any]) -> dict[str, Any]:
    if _result_status(result) != "error":
        return {"structured": False, "same_arguments_retryable": None}
    ok = result.get("Ok") if isinstance(result, Mapping) else None
    for value in parsed_texts:
        if not isinstance(value, Mapping) or "same_arguments_retryable" not in value:
            continue
        retryable = value.get("same_arguments_retryable")
        if retryable is not True and retryable is not False and retryable is not None:
            return {"structured": False, "same_arguments_retryable": None}
        return {
            "structured": isinstance(ok, Mapping) and ok.get("isError") is True,
            "same_arguments_retryable": retryable,
        }
    return {"structured": False, "same_arguments_retryable": None}


def _parse_json_texts(texts: Iterable[str]) -> list[Any]:
    parsed: list[Any] = []
    for text in texts:
        try:
            parsed.append(json.loads(text))
        except (json.JSONDecodeError, TypeError):
            continue
    return parsed


def _single_json_mapping_result(result: Any, parsed_texts: list[Any]) -> dict[str, Any] | None:
    if not isinstance(result, Mapping):
        return None
    ok = result.get("Ok")
    if not isinstance(ok, Mapping):
        return None
    content = ok.get("content")
    if not isinstance(content, list) or len(content) != 1:
        return None
    block = content[0]
    if (
        not isinstance(block, Mapping)
        or block.get("type") != "text"
        or not isinstance(block.get("text"), str)
        or len(parsed_texts) != 1
        or not isinstance(parsed_texts[0], Mapping)
    ):
        return None
    return dict(parsed_texts[0])


def _extract_artifact_ids(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, Mapping):
        for key, item in value.items():
            if key == "artifact_id" and isinstance(item, str) and item:
                found.add(item)
            else:
                found.update(_extract_artifact_ids(item))
    elif isinstance(value, list):
        for item in value:
            found.update(_extract_artifact_ids(item))
    return found


def _extract_resource_ids(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, Mapping):
        for key, item in value.items():
            key_text = str(key)
            if key_text.endswith("_id") and isinstance(item, str) and item:
                found.add(item)
            elif key_text.endswith("_ids") and isinstance(item, list):
                found.update(
                    candidate
                    for candidate in item
                    if isinstance(candidate, str) and candidate
                )
            else:
                found.update(_extract_resource_ids(item))
    elif isinstance(value, list):
        for item in value:
            found.update(_extract_resource_ids(item))
    return found


def _parse_artifact_receipt(value: Any) -> tuple[str, str, str] | None:
    if not isinstance(value, str):
        return None
    first_line = value.splitlines()[0] if value.splitlines() else ""
    parts = first_line.split(" ")
    if len(parts) != 4 or parts[0] != "ARTIFACT":
        return None
    artifact_id, path, handoff_state = parts[1:]
    if (
        not _SAFE_IDENTIFIER_RE.fullmatch(artifact_id)
        or not _ARTIFACT_PATH_RE.fullmatch(path)
        or ".." in Path(path).parts
        or handoff_state not in _ARTIFACT_HANDOFF_STATES
    ):
        return None
    return artifact_id, path, handoff_state


def _authenticated_artifact_write_receipt(
    arguments: Mapping[str, Any], response: Mapping[str, Any]
) -> tuple[str, str, str] | None:
    authentication = response.get("authentication")
    artifact_id = response.get("artifact_id")
    path = response.get("path")
    export_path = response.get("export_path")
    handoff_state = response.get("handoff_state")
    if (
        not isinstance(authentication, Mapping)
        or authentication.get("status") != "verified"
        or response.get("status") not in {"stored", "updated"}
        or not isinstance(artifact_id, str)
        or not _SAFE_IDENTIFIER_RE.fullmatch(artifact_id)
        or not isinstance(path, str)
        or path != export_path
        or not _ARTIFACT_PATH_RE.fullmatch(path)
        or ".." in Path(path).parts
        or handoff_state not in _ARTIFACT_HANDOFF_STATES
        or arguments.get("handoff_state") != handoff_state
    ):
        return None
    return artifact_id, path, str(handoff_state)


def _artifact_read_key(arguments: Mapping[str, Any], response: Mapping[str, Any]) -> str:
    window = response.get("markdown_window")
    if not isinstance(window, Mapping):
        window = {
            "start": arguments.get("markdown_start", 0),
            "max_chars": arguments.get("markdown_max_chars"),
            "legacy_full": "detail_level" not in arguments,
        }
    key = {
        "artifact_id": response.get("artifact_id") or arguments.get("artifact_id"),
        "version": response.get("version"),
        "content_hash": response.get("content_hash"),
        "detail_level": arguments.get("detail_level", "legacy_full"),
        "window": window,
    }
    return hashlib.sha256(_canonical_json(key).encode("utf-8")).hexdigest()


def _artifact_read_is_bounded(
    arguments: Mapping[str, Any], response: Mapping[str, Any]
) -> bool:
    detail_level = arguments.get("detail_level")
    include_markdown = arguments.get("include_markdown", True)
    serialized_chars = len(
        json.dumps(response, ensure_ascii=False, separators=(",", ":"))
    )
    if detail_level == "card":
        return (
            response.get("card_max_serialized_chars")
            == MAX_ARTIFACT_CARD_RESPONSE_CHARS
            and serialized_chars <= MAX_ARTIFACT_CARD_RESPONSE_CHARS
            and arguments.get("markdown_start") is None
            and arguments.get("markdown_max_chars") is None
            and "markdown" not in response
        )
    if detail_level != "review":
        return False
    if (
        response.get("review_max_serialized_chars")
        != MAX_ARTIFACT_REVIEW_RESPONSE_CHARS
        or serialized_chars > MAX_ARTIFACT_REVIEW_RESPONSE_CHARS
    ):
        return False
    if include_markdown is False:
        return "markdown" not in response and "markdown_window" not in response

    start = arguments.get("markdown_start")
    max_chars = arguments.get("markdown_max_chars")
    window = response.get("markdown_window")
    markdown = response.get("markdown")
    if (
        not isinstance(start, int)
        or isinstance(start, bool)
        or start < 0
        or not isinstance(max_chars, int)
        or isinstance(max_chars, bool)
        or not 1 <= max_chars <= 12_000
        or not isinstance(window, Mapping)
        or not isinstance(markdown, str)
    ):
        return False
    end = window.get("end")
    total_chars = window.get("total_chars")
    has_more = window.get("has_more")
    next_start = window.get("next_start")
    return (
        window.get("start") == start
        and isinstance(end, int)
        and not isinstance(end, bool)
        and start <= end <= start + max_chars
        and isinstance(total_chars, int)
        and not isinstance(total_chars, bool)
        and total_chars >= end
        and isinstance(has_more, bool)
        and len(markdown) == end - start
        and ((has_more and next_start == end) or (not has_more and next_start is None))
        and has_more == (end < total_chars)
    )


def _safe_call_arguments(payload: Mapping[str, Any]) -> dict[str, Any]:
    arguments = payload.get("arguments")
    if isinstance(arguments, Mapping):
        return dict(arguments)
    if isinstance(arguments, str):
        try:
            parsed = json.loads(arguments)
        except json.JSONDecodeError:
            return {}
        return dict(parsed) if isinstance(parsed, Mapping) else {}
    return {}


def _changed_paths(left: Any, right: Any, prefix: str = "") -> list[str]:
    if isinstance(left, Mapping) and isinstance(right, Mapping):
        paths: list[str] = []
        for key in sorted(set(left) | set(right), key=str):
            escaped = str(key).replace("~", "~0").replace("/", "~1")
            child = f"{prefix}/{escaped}"
            if key not in left or key not in right:
                paths.append(child)
            else:
                paths.extend(_changed_paths(left[key], right[key], child))
        return paths
    if isinstance(left, list) and isinstance(right, list):
        paths = []
        for index in range(max(len(left), len(right))):
            child = f"{prefix}/{index}"
            if index >= len(left) or index >= len(right):
                paths.append(child)
            else:
                paths.extend(_changed_paths(left[index], right[index], child))
        return paths
    return [] if left == right else [prefix or "/"]


def _changed_path_metadata(left: Any, right: Any) -> dict[str, Any]:
    paths = sorted(_changed_paths(left, right))
    digest = hashlib.sha256(_canonical_json(paths).encode("utf-8")).hexdigest() if paths else None
    return {"changed_path_count": len(paths), "changed_paths_fingerprint": digest}


def _output_texts(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, Mapping):
        texts: list[str] = []
        if isinstance(value.get("text"), str):
            texts.append(value["text"])
        for key, item in value.items():
            if key != "text" and isinstance(item, (Mapping, list)):
                texts.extend(_output_texts(item))
        return texts
    if isinstance(value, list):
        return [text for item in value for text in _output_texts(item)]
    return []


def _exact_custom_exec_data_text(value: Any) -> str | None:
    """Return only the data block from either supported exact exec envelope."""

    if not isinstance(value, list):
        return None
    if len(value) == 1:
        block = value[0]
        if (
            isinstance(block, Mapping)
            and set(block) == {"type", "text"}
            and block.get("type") == "text"
            and isinstance(block.get("text"), str)
        ):
            return block["text"]
        return None
    if len(value) != 2:
        return None
    status, data = value
    if not (
        isinstance(status, Mapping)
        and set(status) == {"type", "text"}
        and status.get("type") == "input_text"
        and isinstance(status.get("text"), str)
        and _SUCCESSFUL_EXEC_STATUS_RE.fullmatch(status["text"])
        and isinstance(data, Mapping)
        and set(data) == {"type", "text"}
        and data.get("type") == "input_text"
        and isinstance(data.get("text"), str)
    ):
        return None
    return data["text"]


def _catalog_output_names(value: Any) -> list[str] | None:
    text = _exact_custom_exec_data_text(value)
    if text is None:
        return None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not (
        isinstance(parsed, list)
        and len(parsed) <= MAX_CATALOG_NAMES
        and all(
            isinstance(item, str) and bool(_TOOL_NAME_RE.fullmatch(item))
            for item in parsed
        )
    ):
        return None
    return parsed


def _compact_javascript(value: str) -> str | None:
    lines = value.splitlines()
    if lines and lines[0].lstrip().startswith("// @exec:"):
        lines = lines[1:]
    compact: list[str] = []
    quote: str | None = None
    escaped = False
    for character in "\n".join(lines):
        if quote is not None:
            compact.append(character)
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == quote:
                quote = None
        elif character in {'"', "'"}:
            quote = character
            compact.append(character)
        elif not character.isspace():
            compact.append(character)
    return "".join(compact) if quote is None and not escaped else None


def _catalog_filter_predicate_count(expression: str, variable: str) -> int | None:
    predicate = re.compile(
        rf"{re.escape(variable)}\.name\.includes\("
        r"(?:\"[^\"\\]{1,120}\"|'[^'\\]{1,120}')\)"
    )
    position = 0
    depth = 0
    count = 0
    expect_operand = True
    while position < len(expression):
        if expect_operand:
            if expression[position] == "(":
                depth += 1
                position += 1
                continue
            match = predicate.match(expression, position)
            if match is None:
                return None
            count += 1
            if count > 4:
                return None
            position = match.end()
            expect_operand = False
            continue
        if expression[position] == ")":
            if depth == 0:
                return None
            depth -= 1
            position += 1
            continue
        operator = expression[position : position + 2]
        if operator not in {"||", "&&"}:
            return None
        position += 2
        expect_operand = True
    if expect_operand or depth != 0 or count == 0:
        return None
    return count


def _catalog_query_kind(value: str) -> str:
    """Classify an exact names-only catalog projection without evaluating JS."""

    compact = _compact_javascript(value)
    if compact is None or len(compact) > 4_000:
        return "unsafe"
    identifier = r"[A-Za-z_$][A-Za-z0-9_$]*"
    direct = re.fullmatch(
        rf"text\(ALL_TOOLS\.filter\((?P<filter>{identifier})=>"
        rf"(?P<expression>.+)\)\.slice\(0,(?P<limit>[0-9]{{1,3}})\)"
        rf"\.map\((?P<map>{identifier})=>(?P=map)\.name\)\);?",
        compact,
    )
    declared = None
    if direct is None:
        declared = re.fullmatch(
            rf"(?P<declaration>const|let|var)(?P<output>{identifier})="
            rf"ALL_TOOLS\.filter\((?P<filter>{identifier})=>"
            rf"(?P<expression>.+)\)\.slice\(0,(?P<limit>[0-9]{{1,3}})\)"
            rf"\.map\((?P<map>{identifier})=>(?P=map)\.name\);"
            rf"text\((?P=output)\);?",
            compact,
        )
    match = direct or declared
    if match is None:
        return "unsafe"
    count = _catalog_filter_predicate_count(
        match.group("expression"), match.group("filter")
    )
    if count is None:
        return "unsafe"
    limit = int(match.group("limit"))
    if not 0 <= limit <= MAX_CATALOG_NAMES:
        return "unsafe"
    canonical = match.group("limit") == str(MAX_CATALOG_NAMES) and (
        declared is None or declared.group("declaration") == "const"
    )
    if not canonical:
        return "safe_noncanonical"
    return "canonical_single" if count == 1 else "canonical_compound"


def _parse_safe_flat_js_object(value: str) -> dict[str, Any] | None:
    if not value.startswith("{") or not value.endswith("}"):
        return None
    body = value[1:-1]
    if not body:
        return {}
    pair = re.compile(
        r"(?:\"(?P<double_key>[A-Za-z_][A-Za-z0-9_]*)\"|"
        r"'(?P<single_key>[A-Za-z_][A-Za-z0-9_]*)'|"
        r"(?P<bare_key>[A-Za-z_][A-Za-z0-9_]*)):"
        r"(?P<value>\"[^\"\\]*\"|'[^'\\]*'|true|false|-?[0-9]+)"
    )
    parsed: dict[str, Any] = {}
    position = 0
    while position < len(body):
        match = pair.match(body, position)
        if match is None:
            return None
        key = match.group("double_key") or match.group("single_key") or match.group(
            "bare_key"
        )
        assert key is not None
        if key in parsed:
            return None
        raw = match.group("value")
        if raw.startswith('"'):
            parsed[key] = json.loads(raw)
        elif raw.startswith("'"):
            parsed[key] = raw[1:-1]
        elif raw in {"true", "false"}:
            parsed[key] = raw == "true"
        else:
            parsed[key] = int(raw)
        position = match.end()
        if position == len(body):
            break
        if body[position] != ",":
            return None
        position += 1
    return parsed


def _bounded_artifact_wrapper_arguments(value: str) -> dict[str, Any] | None:
    compact = _compact_javascript(value)
    if compact is None or len(compact) > 2_000:
        return None
    identifier = r"[A-Za-z_$][A-Za-z0-9_$]*"
    match = re.fullmatch(
        rf"const(?P<result>{identifier})=awaittools\."
        r"mcp__tradingcodex__get_research_artifact\((?P<arguments>\{.*\})\);"
        r"text\((?P=result)\);?",
        compact,
    )
    if match is None:
        return None
    arguments = _parse_safe_flat_js_object(match.group("arguments"))
    allowed = {
        "artifact_id",
        "detail_level",
        "include_markdown",
        "markdown_start",
        "markdown_max_chars",
    }
    if (
        arguments is None
        or not set(arguments) <= allowed
        or not isinstance(arguments.get("artifact_id"), str)
        or not _SAFE_IDENTIFIER_RE.fullmatch(arguments.get("artifact_id", ""))
    ):
        return None
    return arguments


def _bounded_artifact_wrapper_output(
    output: Any, arguments: Mapping[str, Any]
) -> bool:
    data = _exact_custom_exec_data_text(output)
    if data is None:
        return False
    try:
        outer = json.loads(data)
    except json.JSONDecodeError:
        return False
    if not isinstance(outer, Mapping) or outer.get("isError") is not False:
        return False
    content = outer.get("content")
    if not isinstance(content, list) or len(content) != 1:
        return False
    block = content[0]
    if not (
        isinstance(block, Mapping)
        and block.get("type") == "text"
        and isinstance(block.get("text"), str)
    ):
        return False
    try:
        response = json.loads(block["text"])
    except json.JSONDecodeError:
        return False
    return (
        isinstance(response, Mapping)
        and response.get("artifact_id") == arguments.get("artifact_id")
        and _artifact_read_is_bounded(arguments, response)
    )


def _strict_json_value(value: str) -> Any | None:
    """Decode one JSON value while rejecting duplicate keys and non-JSON numbers."""

    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        parsed: dict[str, Any] = {}
        for key, item in pairs:
            if key in parsed:
                raise ValueError("duplicate JSON object key")
            parsed[key] = item
        return parsed

    def reject_constant(_value: str) -> None:
        raise ValueError("non-finite JSON number")

    try:
        return json.loads(
            value,
            object_pairs_hook=unique_object,
            parse_constant=reject_constant,
        )
    except (json.JSONDecodeError, TypeError, ValueError):
        return None


def _bounded_artifact_list_wrapper_arguments(value: str) -> dict[str, Any] | None:
    compact = _compact_javascript(value)
    if compact is None or len(compact) > 2_000:
        return None
    identifier = r"[A-Za-z_$][A-Za-z0-9_$]*"
    match = re.fullmatch(
        rf"const(?P<result>{identifier})=awaittools\."
        r"mcp__tradingcodex__list_research_artifacts\((?P<arguments>\{.*\})\);"
        r"text\((?P=result)\);?",
        compact,
    )
    if match is None:
        return None
    arguments = _parse_safe_flat_js_object(match.group("arguments"))
    string_fields = {
        "artifact_type",
        "universe",
        "workflow_type",
        "workflow_run_id",
        "symbol",
        "readiness_label",
        "handoff_state",
        "created_by",
        "producer_role",
    }
    allowed = string_fields | {"detail_level", "limit", "offset"}
    if (
        arguments is None
        or not set(arguments) <= allowed
        or arguments.get("detail_level") != "card"
    ):
        return None
    if any(
        not isinstance(arguments.get(field), str)
        or not str(arguments[field]).strip()
        or len(str(arguments[field])) > 500
        for field in string_fields & set(arguments)
    ):
        return None
    if "handoff_state" in arguments and arguments["handoff_state"] not in (
        _ARTIFACT_HANDOFF_STATES
    ):
        return None
    limit = arguments.get("limit", 50)
    offset = arguments.get("offset", 0)
    if (
        isinstance(limit, bool)
        or not isinstance(limit, int)
        or not 1 <= limit <= 200
        or isinstance(offset, bool)
        or not isinstance(offset, int)
        or not 0 <= offset <= 199
    ):
        return None
    return arguments


def _valid_bounded_artifact_card(value: Any) -> bool:
    if not isinstance(value, Mapping) or not set(value) <= _ARTIFACT_CARD_FIELDS:
        return False
    required = {
        "artifact_id",
        "content_hash",
        "version",
        "card_max_serialized_chars",
    }
    if not required <= set(value):
        return False
    artifact_id = value.get("artifact_id")
    content_hash = value.get("content_hash")
    version = value.get("version")
    if (
        not isinstance(artifact_id, str)
        or not _SAFE_IDENTIFIER_RE.fullmatch(artifact_id)
        or not isinstance(content_hash, str)
        or not 1 <= len(content_hash) <= 160
        or isinstance(version, bool)
        or not isinstance(version, int)
        or version < 1
        or value.get("card_max_serialized_chars")
        != MAX_ARTIFACT_CARD_RESPONSE_CHARS
    ):
        return False
    if any(
        not isinstance(value.get(field), str)
        for field in _ARTIFACT_CARD_STRING_FIELDS & set(value)
    ):
        return False
    confidence = value.get("confidence")
    if confidence is not None and (
        isinstance(confidence, bool)
        or not isinstance(confidence, (str, int, float))
        or (isinstance(confidence, float) and not math.isfinite(confidence))
    ):
        return False
    for field in ("missing_evidence", "blocked_actions"):
        items = value.get(field)
        if items is not None and (not isinstance(items, list) or len(items) > 8):
            return False
    try:
        serialized_chars = len(
            json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        )
    except (TypeError, ValueError):
        return False
    if serialized_chars > MAX_ARTIFACT_CARD_RESPONSE_CHARS:
        return False
    path = value.get("path")
    if path is not None and (
        not isinstance(path, str)
        or not _ARTIFACT_PATH_RE.fullmatch(path)
        or ".." in Path(path).parts
    ):
        return False
    handoff_state = value.get("handoff_state")
    if handoff_state is not None and handoff_state not in _ARTIFACT_HANDOFF_STATES:
        return False
    if "workspace_native" in value and not isinstance(
        value.get("workspace_native"), bool
    ):
        return False
    truncated_fields = value.get("card_truncated_fields")
    if truncated_fields is not None and not (
        isinstance(truncated_fields, list)
        and all(
            isinstance(field, str) and field in _ARTIFACT_CARD_FIELDS
            for field in truncated_fields
        )
        and truncated_fields == sorted(set(truncated_fields))
    ):
        return False
    return True


def _valid_bounded_artifact_list_response(
    response: Any,
    arguments: Mapping[str, Any],
    *,
    inner_chars: int,
) -> bool:
    expected_keys = {
        "db_canonical",
        "file_sot",
        "workspace_native",
        "workspace_context",
        "artifacts",
        "invalid_artifact_count",
        "run_bound_authentication",
        "artifact_page",
    }
    if not isinstance(response, Mapping) or set(response) != expected_keys:
        return False
    artifacts = response.get("artifacts")
    invalid_count = response.get("invalid_artifact_count")
    if (
        not isinstance(response.get("db_canonical"), bool)
        or response.get("file_sot") is not True
        or response.get("workspace_native") is not True
        or not isinstance(response.get("workspace_context"), Mapping)
        or not isinstance(invalid_count, int)
        or isinstance(invalid_count, bool)
        or invalid_count < 0
        or not isinstance(artifacts, list)
        or not artifacts
        or not all(_valid_bounded_artifact_card(item) for item in artifacts)
    ):
        return False

    authentication = response.get("run_bound_authentication")
    verified_count = (
        authentication.get("verified_artifact_count")
        if isinstance(authentication, Mapping)
        else None
    )
    if not (
        isinstance(authentication, Mapping)
        and set(authentication) == {"status", "verified_artifact_count"}
        and authentication.get("status") == "verified"
        and isinstance(verified_count, int)
        and not isinstance(verified_count, bool)
        and verified_count == len(artifacts)
    ):
        return False

    page = response.get("artifact_page")
    base_page_keys = {
        "offset",
        "requested_limit",
        "returned_count",
        "has_more",
        "response_truncated",
        "max_serialized_chars",
    }
    if not isinstance(page, Mapping):
        return False
    has_more = page.get("has_more")
    expected_page_keys = base_page_keys | ({"next_offset"} if has_more is True else set())
    marker = page.get("max_serialized_chars")
    offset = arguments.get("offset", 0)
    requested_limit = arguments.get("limit", 50)
    page_offset = page.get("offset")
    page_limit = page.get("requested_limit")
    returned_count = page.get("returned_count")
    if (
        set(page) != expected_page_keys
        or isinstance(marker, bool)
        or not isinstance(marker, int)
        or not 1 <= marker <= MAX_ARTIFACT_LIST_RESPONSE_CHARS
        or inner_chars > marker
        or not isinstance(page_offset, int)
        or isinstance(page_offset, bool)
        or page_offset != offset
        or not isinstance(page_limit, int)
        or isinstance(page_limit, bool)
        or page_limit != requested_limit
        or not isinstance(returned_count, int)
        or isinstance(returned_count, bool)
        or returned_count != len(artifacts)
        or len(artifacts) > requested_limit
        or not isinstance(has_more, bool)
        or not isinstance(page.get("response_truncated"), bool)
        or (page.get("response_truncated") is True and has_more is not True)
    ):
        return False
    if has_more is True:
        next_offset = page.get("next_offset")
        if (
            not isinstance(next_offset, int)
            or isinstance(next_offset, bool)
            or next_offset != offset + len(artifacts)
        ):
            return False
    return True


def _bounded_artifact_list_wrapper_output(
    output: Any, arguments: Mapping[str, Any]
) -> bool:
    """Recognize only the service-bounded list envelope; trace JSON may expand it."""

    data = _exact_custom_exec_data_text(output)
    if data is None:
        return False
    outer = _strict_json_value(data)
    if not (
        isinstance(outer, Mapping)
        and set(outer) == {"content", "isError"}
        and outer.get("isError") is False
    ):
        return False
    content = outer.get("content")
    if not isinstance(content, list) or len(content) != 1:
        return False
    block = content[0]
    if not (
        isinstance(block, Mapping)
        and set(block) == {"type", "text"}
        and block.get("type") == "text"
        and isinstance(block.get("text"), str)
    ):
        return False
    inner_text = block["text"]
    if len(inner_text) > MAX_ARTIFACT_LIST_RESPONSE_CHARS:
        return False
    response = _strict_json_value(inner_text)
    if not isinstance(response, Mapping):
        return False
    # The MCP transport renders this exact pretty JSON. Re-encoding equality
    # rules out partial, duplicate-key, and non-canonical semantic payloads.
    if inner_text != json.dumps(response, indent=2, ensure_ascii=False):
        return False
    return _valid_bounded_artifact_list_response(
        response,
        arguments,
        inner_chars=len(inner_text),
    )


def _canonical_schema_lookup(value: str) -> str | None:
    lines = value.splitlines()
    if lines and lines[0].lstrip().startswith("// @exec:"):
        lines = lines[1:]
    compact = re.sub(r"\s+", "", "\n".join(lines))
    pattern = re.compile(
        r"^const(?P<value_var>[A-Za-z_$][A-Za-z0-9_$]*)="
        r"ALL_TOOLS\.find\("
        r"(?P<item_var>[A-Za-z_$][A-Za-z0-9_$]*)=>"
        r"(?P=item_var)\.name===\"(?P<tool>[A-Za-z][A-Za-z0-9_]{0,199})\""
        r"\);text\("
        r"(?P=value_var)\?(?P=value_var)\.description:\"missing\""
        r"\);?$"
    )
    match = pattern.fullmatch(compact)
    return match.group("tool") if match else None


def _deterministic_success_tool(tool: str) -> bool:
    return tool.startswith(("compare_", "get_", "list_", "search_")) or tool in {
        "materialize_dataset_slice",
        "prepare_calculation",
        "preview_order_translation",
        "profile_dataset",
        "run_order_checks",
        "simulate_policy",
    }


def _mutation_affects_retry(
    source_call: Mapping[str, Any], mutation_call: Mapping[str, Any]
) -> bool:
    source_ids = source_call.get("resource_ids")
    mutation_ids = mutation_call.get("resource_ids")
    return (
        isinstance(source_ids, set)
        and isinstance(mutation_ids, set)
        and bool(source_ids & mutation_ids)
    )


def _summarize_session(path: Path, meta: Mapping[str, Any], *, root_id: str) -> dict[str, Any]:
    spawn = _thread_spawn(meta)
    is_root = str(meta.get("id") or "") == root_id
    expected_model = EXPECTED_ROOT_MODEL if is_root else EXPECTED_CHILD_MODEL
    expected_effort = EXPECTED_ROOT_EFFORT if is_root else EXPECTED_CHILD_EFFORT
    base = meta.get("base_instructions")
    base_text = str(base.get("text") or "") if isinstance(base, Mapping) else ""
    final_tokens: dict[str, Any] = {}
    token_event_count = 0
    valid_token_event_count = 0
    token_series_monotonic = True
    previous_token_total: dict[str, int] | None = None
    turn_context_seen = False
    turn_context_event_count = 0
    valid_turn_context_event_count = 0
    turn_context_series_consistent = True
    first_permission_profile_fingerprint: str | None = None
    context_window = 0
    max_last_input_tokens = 0
    model = ""
    effort = ""
    multi_agent_version = ""
    multi_agent_mode = ""
    sandbox_type = ""
    permission_profile_type = ""
    permission_profile_fingerprint = ""
    network_access: bool | None = None
    task_duration_ms: int | None = None
    time_to_first_token_ms: int | None = None
    termination = "incomplete"
    visible_progress_count = 0
    first_progress_event_index: int | None = None
    first_spawn_event_index: int | None = None
    first_planning_web_event_index: int | None = None
    task_started_at: datetime | None = None
    task_started_timestamp_complete = True
    progress_timestamps: list[datetime] = []
    progress_timestamps_complete = True
    final_answer_at: datetime | None = None
    final_answer_timestamp_complete = True
    task_complete_at: datetime | None = None
    task_complete_timestamp_complete = True
    latest_observed_at: datetime | None = None
    reasoning_items_ignored = 0
    function_counts: Counter[str] = Counter()
    nested_tool_counts: Counter[str] = Counter()
    mcp_tool_counts: Counter[str] = Counter()
    mcp_ok = 0
    mcp_errors = 0
    mcp_duration_ms = 0.0
    external_mcp_counts: Counter[str] = Counter()
    external_mcp_ok = 0
    external_mcp_errors = 0
    external_mcp_duration_ms = 0.0
    external_exact_counts: Counter[str] = Counter()
    external_semantic_counts: Counter[str] = Counter()
    external_semantic_labels: dict[str, str] = {}
    openbb_provider_omissions = 0
    openbb_forbidden_calls = 0
    openbb_discovery_calls = 0
    openbb_activation_calls = 0
    openbb_admin_scope_counts: Counter[str] = Counter()
    openbb_overbroad_activations = 0
    openbb_unbounded_row_calls = 0
    openbb_chart_calls = 0
    openbb_role_violations = 0
    external_oversized_results = 0
    pending_external_results: list[dict[str, Any]] = []
    promoted_external_results = 0
    receipt_only_external_failures = 0
    invalid_external_promotion_receipts = 0
    mismatched_external_promotions = 0
    external_results_before_handoff = 0
    external_calls_while_promotion_pending = 0
    invocation_counts: Counter[str] = Counter()
    invocation_labels: dict[str, str] = {}
    tcx_calls: list[dict[str, Any]] = []
    previous_tcx_call: dict[str, Any] | None = None
    consecutive_tcx_repeats = 0
    consecutive_deterministic_repeats = 0
    artifact_read_counts: Counter[str] = Counter()
    artifact_read_chars = 0
    artifact_read_max_chars = 0
    unbounded_artifact_reads = 0
    created_artifact_ids: set[str] = set()
    read_artifact_ids: set[str] = set()
    consumed_artifact_ids: set[str] = set()
    authenticated_artifact_writes: list[tuple[str, str, str]] = []
    authenticated_synthesis_inputs: list[set[str]] = []
    final_artifact_receipt: tuple[str, str, str] | None = None
    custom_calls: dict[str, dict[str, Any]] = {}
    discovered_catalog_names: set[str] = set()
    schema_lookup_counts: Counter[str] = Counter()
    catalog_queries = 0
    names_only_queries = 0
    canonical_single_catalog_queries = 0
    canonical_compound_catalog_queries = 0
    noncanonical_tool_catalog_queries = 0
    valid_names_only_results = 0
    catalog_description_scans = 0
    schema_lookup_queries = 0
    valid_schema_lookups = 0
    noncanonical_schema_lookups = 0
    unresolved_schema_lookups = 0
    repeated_schema_lookups = 0
    invalid_schema_outputs = 0
    missing_schema_outputs = 0
    truncated_schema_outputs = 0
    truncated_outputs = 0
    truncated_catalog_outputs = 0
    truncated_artifact_outputs = 0
    truncated_generic_outputs = 0
    oversized_custom_outputs = 0
    bounded_artifact_oversize_exemptions = 0
    max_truncated_original_tokens = 0
    max_truncated_omitted_tokens = 0
    oversized_truncation_counts = 0
    wait_agent_calls = 0
    wait_timeouts_outside_contract = 0
    chained_waits_without_progress = 0
    wait_requires_visible_progress = False
    spawn_calls: list[dict[str, Any]] = []
    subagent_activity_counts: Counter[str] = Counter()
    started_subagent_thread_ids: set[str] = set()
    started_subagent_events: list[dict[str, str]] = []

    for event_index, item in enumerate(_iter_jsonl(path)):
        item_type = item.get("type")
        payload = item.get("payload")
        item_timestamp = _observable_timestamp(item.get("timestamp"))
        if item_timestamp is not None and (
            latest_observed_at is None or item_timestamp > latest_observed_at
        ):
            latest_observed_at = item_timestamp
        if not isinstance(payload, Mapping):
            continue

        if item_type == "turn_context":
            turn_context_seen = True
            turn_context_event_count += 1
            model = str(payload.get("model") or "")
            effort = str(payload.get("effort") or "")
            multi_agent_version = str(payload.get("multi_agent_version") or "")
            multi_agent_mode = str(payload.get("multi_agent_mode") or "")
            sandbox_type = ""
            network_access = None
            sandbox = payload.get("sandbox_policy")
            if isinstance(sandbox, Mapping):
                sandbox_type = str(sandbox.get("type") or "")
                if isinstance(sandbox.get("network_access"), bool):
                    network_access = sandbox["network_access"]
            permission_profile_type = ""
            permission_profile_fingerprint = ""
            permission_profile = payload.get("permission_profile")
            if isinstance(permission_profile, Mapping):
                permission_profile_type = str(permission_profile.get("type") or "")
                permission_profile_fingerprint = _value_fingerprint(permission_profile)
                if first_permission_profile_fingerprint is None:
                    first_permission_profile_fingerprint = permission_profile_fingerprint
                elif permission_profile_fingerprint != first_permission_profile_fingerprint:
                    turn_context_series_consistent = False
            if (
                model == expected_model
                and effort == expected_effort
                and multi_agent_version == "v2"
                and multi_agent_mode == "explicitRequestOnly"
                and sandbox_type == EXPECTED_SANDBOX_TYPE
                and network_access is True
                and permission_profile_type == EXPECTED_PERMISSION_PROFILE_TYPE
                and bool(permission_profile_fingerprint)
            ):
                valid_turn_context_event_count += 1

        if item_type == "event_msg":
            event_type = payload.get("type")
            if event_type == "task_started":
                if item_timestamp is None:
                    task_started_timestamp_complete = False
                elif task_started_at is None:
                    task_started_at = item_timestamp
            elif event_type == "token_count":
                token_event_count += 1
                info = payload.get("info")
                if isinstance(info, Mapping):
                    total = info.get("total_token_usage")
                    last = info.get("last_token_usage")
                    event_context_window = info.get("model_context_window")
                    total_usage = _normalized_token_usage(total)
                    last_usage = _normalized_token_usage(last)
                    structurally_valid = (
                        total_usage is not None
                        and last_usage is not None
                        and isinstance(event_context_window, int)
                        and not isinstance(event_context_window, bool)
                        and event_context_window > 0
                    )
                    if structurally_valid:
                        assert total_usage is not None
                        assert last_usage is not None
                        last_within_total = all(
                            last_usage[key] <= total_usage[key] for key in _TOKEN_KEYS
                        )
                        current_total = total_usage
                        monotonic = previous_token_total is None or all(
                            current_total[key] >= previous_token_total[key] for key in _TOKEN_KEYS
                        )
                        token_series_monotonic = token_series_monotonic and monotonic
                        previous_token_total = current_total
                    else:
                        last_within_total = False
                        monotonic = False
                    if structurally_valid and last_within_total and monotonic:
                        valid_token_event_count += 1
                        final_tokens = current_total
                        context_window = max(context_window, event_context_window)
                        max_last_input_tokens = max(
                            max_last_input_tokens, last_usage["input_tokens"]
                        )
            elif event_type == "task_complete":
                termination = "task_complete"
                if item_timestamp is None:
                    task_complete_timestamp_complete = False
                elif task_complete_at is None:
                    task_complete_at = item_timestamp
                task_duration_ms = _optional_nonnegative_int(
                    payload.get("duration_ms"),
                    field="task_complete.duration_ms",
                    max_value=MAX_TRACE_DURATION_SECS * 1_000,
                )
                time_to_first_token_ms = _optional_nonnegative_int(
                    payload.get("time_to_first_token_ms"),
                    field="task_complete.time_to_first_token_ms",
                    max_value=MAX_TRACE_DURATION_SECS * 1_000,
                )
            elif event_type in {"turn_aborted", "task_aborted", "task_failed"}:
                termination = str(event_type)
            elif event_type == "agent_message":
                if payload.get("phase") == "final_answer":
                    for pending in pending_external_results:
                        if not pending["handoff_violation"]:
                            pending["handoff_violation"] = True
                            external_results_before_handoff += 1
                    final_artifact_receipt = _parse_artifact_receipt(
                        payload.get("message")
                    )
                    if item_timestamp is None:
                        final_answer_timestamp_complete = False
                    elif final_answer_at is None:
                        final_answer_at = item_timestamp
                else:
                    visible_progress_count += 1
                    if first_progress_event_index is None:
                        first_progress_event_index = event_index
                    wait_requires_visible_progress = False
                    if item_timestamp is None:
                        progress_timestamps_complete = False
                    else:
                        progress_timestamps.append(item_timestamp)
            elif event_type == "agent_reasoning":
                reasoning_items_ignored += 1
            elif event_type == "sub_agent_activity":
                raw_kind = payload.get("kind")
                kind = _safe_activity_kind(raw_kind)
                subagent_activity_counts[kind] += 1
                if raw_kind == "started" and isinstance(payload.get("agent_thread_id"), str):
                    started_subagent_thread_ids.add(_safe_identifier(payload["agent_thread_id"]))
                    event_id = payload.get("event_id")
                    started_subagent_events.append(
                        {
                            "thread_id": _safe_identifier(payload["agent_thread_id"]),
                            "event_id": _safe_identifier(event_id),
                            "event_id_valid": isinstance(event_id, str)
                            and bool(event_id),
                            "agent_path_fingerprint": _value_fingerprint(payload.get("agent_path")),
                        }
                    )
            elif event_type == "mcp_tool_call_end":
                invocation = payload.get("invocation")
                if not isinstance(invocation, Mapping):
                    continue
                server = str(invocation.get("server") or "")
                raw_tool = str(invocation.get("tool") or "")
                if not server or not raw_tool:
                    continue
                arguments = invocation.get("arguments")
                if not isinstance(arguments, Mapping):
                    arguments = {}
                result = payload.get("result")
                status = _result_status(result)
                is_official_source_call = (
                    server == OFFICIAL_SOURCE_DATA_SERVER
                    and raw_tool == OFFICIAL_SOURCE_DATA_TOOL
                )
                if server != "tradingcodex" or is_official_source_call:
                    if server == "openbb":
                        session_role = str(spawn.get("agent_role") or "") if spawn else "head-manager"
                        if session_role not in OPENBB_EVIDENCE_ROLES:
                            openbb_role_violations += 1
                        forbidden_openbb_call = _openbb_forbidden_tool(
                            raw_tool,
                            arguments,
                        )
                        if raw_tool == "available_tools":
                            openbb_discovery_calls += 1
                        elif raw_tool == "activate_tools":
                            openbb_activation_calls += 1
                            requested = _first_argument_value(
                                arguments,
                                ("tool_names", "tools", "names"),
                            )
                            if (
                                not isinstance(requested, list)
                                or not requested
                                or len(requested) > 3
                            ):
                                openbb_overbroad_activations += 1
                        elif not _openbb_admin_tool(raw_tool) and not forbidden_openbb_call:
                            provider = _first_argument_value(
                                arguments,
                                ("provider", "provider_name"),
                            )
                            if not isinstance(provider, str) or not provider.strip():
                                openbb_provider_omissions += 1
                        if forbidden_openbb_call:
                            openbb_forbidden_calls += 1
                        for admin_scope in _openbb_admin_scope_fingerprints(
                            root_id=root_id,
                            session_id=str(meta.get("id") or ""),
                            role=session_role,
                            tool=raw_tool,
                            arguments=arguments,
                        ):
                            openbb_admin_scope_counts[admin_scope] += 1
                        if arguments.get("chart") is True or arguments.get("include_chart") is True:
                            openbb_chart_calls += 1
                        for key in ("limit", "max_results", "max_rows", "page_size"):
                            value = arguments.get(key)
                            if (
                                isinstance(value, int)
                                and not isinstance(value, bool)
                                and value > 120
                            ):
                                openbb_unbounded_row_calls += 1
                                break
                    if not _recognized_external_financial_data_call(
                        server, raw_tool, arguments
                    ):
                        continue
                    safe_server = _safe_external_server(server)
                    safe_tool = _safe_external_tool(server, raw_tool)
                    label = f"{safe_server}::{safe_tool}"
                    external_mcp_counts[label] += 1
                    external_mcp_duration_ms += _duration_ms(payload.get("duration"))
                    if status == "ok":
                        external_mcp_ok += 1
                    else:
                        external_mcp_errors += 1
                    exact_fingerprint = _fingerprint(server, raw_tool, arguments)
                    semantic_fingerprint = _external_semantic_fingerprint(
                        server,
                        raw_tool,
                        arguments,
                    )
                    external_exact_counts[exact_fingerprint] += 1
                    external_semantic_counts[semantic_fingerprint] += 1
                    external_semantic_labels[semantic_fingerprint] = label
                    result_chars = sum(len(text) for text in _result_texts(result))
                    if result_chars > MAX_CUSTOM_OUTPUT_CHARS:
                        external_oversized_results += 1
                    if pending_external_results:
                        external_calls_while_promotion_pending += 1
                    pending_external_results.append(
                        {
                            "server": server,
                            "tool": raw_tool,
                            "arguments": dict(arguments),
                            "returned_provider": _observable_returned_provider(
                                result
                            ),
                            "official_result": (
                                _observable_official_result(result)
                                if is_official_source_call
                                else {}
                            ),
                            "handoff_violation": False,
                        }
                    )
                    continue
                tool = _safe_tcx_tool(raw_tool)
                mcp_tool_counts[tool] += 1
                mcp_duration_ms += _duration_ms(payload.get("duration"))
                if status == "ok":
                    mcp_ok += 1
                else:
                    mcp_errors += 1
                fingerprint = _fingerprint(server, tool, arguments)
                invocation_counts[fingerprint] += 1
                invocation_labels[fingerprint] = tool
                texts = _result_texts(result)
                if tool == "get_research_artifact" and (
                    _TRUNCATION_RE.search("\n".join(texts))
                    or _OMITTED_TOKENS_RE.search("\n".join(texts))
                ):
                    truncated_artifact_outputs += 1
                parsed = _parse_json_texts(texts)
                single_response = _single_json_mapping_result(result, parsed)
                error_contract = _error_contract(result, parsed)
                resource_ids = _extract_resource_ids(arguments)
                for value in parsed:
                    resource_ids.update(_extract_resource_ids(value))
                call_record = {
                    "tool": tool,
                    "fingerprint": fingerprint,
                    "arguments": dict(arguments),
                    "status": status,
                    "error_structured": bool(error_contract["structured"]),
                    "same_arguments_retryable": error_contract["same_arguments_retryable"],
                    "resource_ids": resource_ids,
                }
                tcx_calls.append(call_record)
                if tool in {
                    "append_research_artifact_version",
                    "create_research_artifact",
                }:
                    for pending in pending_external_results:
                        if not pending["handoff_violation"]:
                            pending["handoff_violation"] = True
                            external_results_before_handoff += 1
                if (
                    tool == "record_external_data_result"
                    and pending_external_results
                    and status == "ok"
                ):
                    promotion_kind = _validated_external_promotion(
                        arguments, single_response
                    )
                    if promotion_kind is None:
                        invalid_external_promotion_receipts += 1
                    else:
                        matching_index = next(
                            (
                                index
                                for index, pending in enumerate(
                                    pending_external_results
                                )
                                if _promotion_matches_external_call(
                                    pending, arguments
                                )
                            ),
                            None,
                        )
                        if matching_index is None:
                            mismatched_external_promotions += 1
                        else:
                            pending_external_results.pop(matching_index)
                            if promotion_kind == "dataset":
                                promoted_external_results += 1
                            else:
                                receipt_only_external_failures += 1
                if previous_tcx_call and fingerprint == previous_tcx_call["fingerprint"]:
                    consecutive_tcx_repeats += 1
                    if (
                        (
                            previous_tcx_call["status"] == "ok"
                            and _deterministic_success_tool(tool)
                        )
                        or previous_tcx_call["same_arguments_retryable"] is False
                    ):
                        consecutive_deterministic_repeats += 1
                previous_tcx_call = call_record
                if status == "ok" and tool in {
                    "append_research_artifact_version",
                    "create_research_artifact",
                }:
                    for value in parsed:
                        created_artifact_ids.update(_extract_artifact_ids(value))
                    input_artifact_ids = arguments.get("input_artifact_ids")
                    normalized_input_artifact_ids = (
                        input_artifact_ids
                        if isinstance(input_artifact_ids, list)
                        else []
                    )
                    if normalized_input_artifact_ids:
                        consumed_artifact_ids.update(
                            artifact_id
                            for artifact_id in normalized_input_artifact_ids
                            if isinstance(artifact_id, str) and artifact_id
                        )
                    response = single_response
                    receipt = (
                        _authenticated_artifact_write_receipt(arguments, response)
                        if response is not None
                        else None
                    )
                    if receipt is not None:
                        authenticated_artifact_writes.append(receipt)
                        if is_root and arguments.get("artifact_type") == "synthesis_report":
                            authenticated_synthesis_inputs.append(
                                {
                                    artifact_id
                                    for artifact_id in normalized_input_artifact_ids
                                    if isinstance(artifact_id, str) and artifact_id
                                }
                            )
                if tool == "get_research_artifact" and status == "ok":
                    chars = sum(len(text) for text in texts)
                    artifact_read_chars += chars
                    artifact_read_max_chars = max(artifact_read_max_chars, chars)
                    response = single_response
                    if response is None or not _artifact_read_is_bounded(arguments, response):
                        unbounded_artifact_reads += 1
                    response = response or {}
                    if isinstance(response.get("artifact_id"), str):
                        read_artifact_ids.add(response["artifact_id"])
                    artifact_read_counts[_artifact_read_key(arguments, response)] += 1

        if item_type == "response_item":
            response_type = payload.get("type")
            if response_type == "reasoning":
                reasoning_items_ignored += 1
            elif response_type == "function_call":
                name = str(payload.get("name") or "")
                if not name:
                    continue
                function_counts[_safe_function_tool(name)] += 1
                arguments = _safe_call_arguments(payload)
                if name == "wait_agent":
                    wait_agent_calls += 1
                    if wait_requires_visible_progress:
                        chained_waits_without_progress += 1
                    wait_requires_visible_progress = True
                    timeout_ms = arguments.get("timeout_ms")
                    if (
                        not isinstance(timeout_ms, int)
                        or isinstance(timeout_ms, bool)
                        or not MIN_WAIT_TIMEOUT_MS
                        <= timeout_ms
                        <= MAX_WAIT_TIMEOUT_MS
                    ):
                        wait_timeouts_outside_contract += 1
                if name == "spawn_agent":
                    if first_spawn_event_index is None:
                        first_spawn_event_index = event_index
                    argument_keys = set(arguments)
                    unknown_keys = sorted(argument_keys - _SPAWN_ARGUMENT_KEYS)
                    message = arguments.get("message")
                    task_name = arguments.get("task_name")
                    task_name_valid = isinstance(task_name, str) and bool(
                        _TASK_NAME_RE.fullmatch(task_name)
                    )
                    spawn_calls.append(
                        {
                            "call_id": _safe_identifier(payload.get("call_id")),
                            "call_id_valid": isinstance(payload.get("call_id"), str)
                            and bool(payload.get("call_id")),
                            "namespace_valid": payload.get("namespace") == "agents",
                            "argument_keys_valid": argument_keys == _SPAWN_ARGUMENT_KEYS,
                            "unknown_keys_fingerprint": _value_fingerprint(unknown_keys) if unknown_keys else None,
                            "agent_type": _safe_role(arguments.get("agent_type")),
                            "fork_turns": "none"
                            if arguments.get("fork_turns") == "none"
                            else f"invalid-sha256:{_value_fingerprint(arguments.get('fork_turns'))}",
                            "message_chars": len(message) if isinstance(message, str) else 0,
                            "message_valid": isinstance(message, str) and 0 < len(message) <= 4_000,
                            "task_name_valid": task_name_valid,
                            "expected_agent_path_fingerprint": _value_fingerprint(
                                f"/root/{task_name}"
                            )
                            if task_name_valid
                            else None,
                            "model_override": bool(arguments.get("model")),
                            "reasoning_override": bool(arguments.get("reasoning_effort")),
                        }
                    )
            elif response_type == "custom_tool_call":
                call_id = str(payload.get("call_id") or "")
                input_text = str(payload.get("input") or "")
                nested = _NESTED_TOOL_RE.findall(input_text)
                nested_tool_counts.update(
                    _safe_nested_tool(nested_name) for nested_name in nested
                )
                if "web__run" in nested and first_planning_web_event_index is None:
                    first_planning_web_event_index = event_index
                catalog_query = "ALL_TOOLS" in input_text
                catalog_query_kind = (
                    _catalog_query_kind(input_text) if catalog_query else "not_catalog"
                )
                names_only_catalog_query = catalog_query_kind in {
                    "canonical_single",
                    "canonical_compound",
                    "safe_noncanonical",
                }
                schema_lookup_name = (
                    _canonical_schema_lookup(input_text) if catalog_query else None
                )
                schema_lookup_candidate = catalog_query and bool(
                    re.search(r"\bALL_TOOLS\s*\.\s*find\s*\(", input_text)
                    and _DESCRIPTION_ACCESS_RE.search(input_text)
                )
                description_scan = catalog_query and bool(
                    _DESCRIPTION_ACCESS_RE.search(input_text)
                ) and schema_lookup_name is None
                if catalog_query:
                    catalog_queries += 1
                if names_only_catalog_query:
                    names_only_queries += 1
                if catalog_query_kind == "canonical_single":
                    canonical_single_catalog_queries += 1
                elif catalog_query_kind == "canonical_compound":
                    canonical_compound_catalog_queries += 1
                elif catalog_query_kind == "safe_noncanonical":
                    noncanonical_tool_catalog_queries += 1
                if schema_lookup_candidate:
                    schema_lookup_queries += 1
                    if schema_lookup_name is None:
                        noncanonical_schema_lookups += 1
                if description_scan:
                    catalog_description_scans += 1
                schema_name_was_discovered = False
                schema_lookup_repeated = False
                if schema_lookup_name is not None:
                    schema_name_was_discovered = (
                        schema_lookup_name in discovered_catalog_names
                    )
                    schema_lookup_repeated = schema_lookup_counts[schema_lookup_name] > 0
                    schema_lookup_counts[schema_lookup_name] += 1
                    if not schema_name_was_discovered:
                        unresolved_schema_lookups += 1
                    if schema_lookup_repeated:
                        repeated_schema_lookups += 1
                custom_calls[call_id] = {
                    "catalog_query": catalog_query,
                    "catalog_query_kind": catalog_query_kind,
                    "names_only_catalog_query": names_only_catalog_query,
                    "catalog_description_scan": description_scan,
                    "catalog_output_seen": False,
                    "catalog_output_entries": None,
                    "catalog_output_valid": False,
                    "schema_lookup_candidate": schema_lookup_candidate,
                    "schema_lookup_name_valid": schema_lookup_name is not None,
                    "schema_name_was_discovered": schema_name_was_discovered,
                    "schema_lookup_repeated": schema_lookup_repeated,
                    "schema_output_valid": False,
                    "bounded_artifact_arguments": _bounded_artifact_wrapper_arguments(
                        input_text
                    ),
                    "bounded_artifact_list_arguments": (
                        _bounded_artifact_list_wrapper_arguments(input_text)
                    ),
                }
            elif response_type == "custom_tool_call_output":
                call_id = str(payload.get("call_id") or "")
                output = payload.get("output")
                output_text = _json_text(output)
                call = custom_calls.get(call_id, {})
                artifact_arguments = call.get("bounded_artifact_arguments")
                bounded_artifact_read_output = isinstance(
                    artifact_arguments, Mapping
                ) and _bounded_artifact_wrapper_output(output, artifact_arguments)
                artifact_list_arguments = call.get("bounded_artifact_list_arguments")
                bounded_artifact_list_output = isinstance(
                    artifact_list_arguments, Mapping
                ) and _bounded_artifact_list_wrapper_output(
                    output,
                    artifact_list_arguments,
                )
                bounded_artifact_output = (
                    bounded_artifact_read_output or bounded_artifact_list_output
                )
                raw_oversized_output = len(output_text) > MAX_CUSTOM_OUTPUT_CHARS
                if raw_oversized_output and bounded_artifact_output:
                    bounded_artifact_oversize_exemptions += 1
                oversized_output = raw_oversized_output and not bounded_artifact_output
                if oversized_output:
                    oversized_custom_outputs += 1
                if call.get("names_only_catalog_query"):
                    call["catalog_output_seen"] = True
                    names = _catalog_output_names(output)
                    call["catalog_output_entries"] = len(names) if names is not None else None
                    call["catalog_output_valid"] = names is not None and not oversized_output
                    if call["catalog_output_valid"]:
                        assert names is not None
                        valid_names_only_results += 1
                        discovered_catalog_names.update(names)
                elif call.get("schema_lookup_name_valid"):
                    call["catalog_output_seen"] = True
                    schema_text = _exact_custom_exec_data_text(output)
                    schema_missing = (
                        schema_text is not None
                        and (
                            not schema_text.strip()
                            or schema_text.strip() == "missing"
                        )
                    )
                    schema_truncated = bool(
                        _TRUNCATION_RE.search(schema_text or "")
                        or _OMITTED_TOKENS_RE.search(schema_text or "")
                    )
                    schema_envelope_valid = schema_text is not None
                    call["schema_output_valid"] = (
                        schema_envelope_valid
                        and not schema_missing
                        and not schema_truncated
                        and not oversized_output
                    )
                    if not call["schema_output_valid"]:
                        invalid_schema_outputs += 1
                    if schema_missing:
                        missing_schema_outputs += 1
                    if schema_truncated:
                        truncated_schema_outputs += 1
                    if (
                        call["schema_output_valid"]
                        and call.get("schema_name_was_discovered")
                        and not call.get("schema_lookup_repeated")
                    ):
                        valid_schema_lookups += 1
                matches = _TRUNCATION_RE.findall(output_text)
                omitted_matches = _OMITTED_TOKENS_RE.findall(output_text)
                if matches or omitted_matches:
                    truncated_outputs += 1
                    if matches:
                        for value in matches:
                            count, capped = _bounded_decimal_count(value)
                            max_truncated_original_tokens = max(
                                max_truncated_original_tokens, count
                            )
                            oversized_truncation_counts += int(capped)
                    if omitted_matches:
                        for value in omitted_matches:
                            count, capped = _bounded_decimal_count(value)
                            max_truncated_omitted_tokens = max(
                                max_truncated_omitted_tokens, count
                            )
                            oversized_truncation_counts += int(capped)
                    if call.get("schema_lookup_name_valid"):
                        pass
                    elif call.get("catalog_query"):
                        truncated_catalog_outputs += 1
                    else:
                        truncated_generic_outputs += 1

    duplicate_groups = [
        {"tool": invocation_labels[fingerprint], "argument_fingerprint": fingerprint, "calls": count}
        for fingerprint, count in invocation_counts.items()
        if count > 1
    ]
    duplicate_groups.sort(key=lambda value: (value["tool"], value["argument_fingerprint"]))
    external_exact_repeat_occurrences = sum(
        count - 1 for count in external_exact_counts.values() if count > 1
    )
    external_semantic_duplicate_groups = [
        {
            "tool": external_semantic_labels[fingerprint],
            "semantic_fingerprint": fingerprint,
            "calls": count,
        }
        for fingerprint, count in external_semantic_counts.items()
        if count > 1
    ]
    external_semantic_duplicate_groups.sort(
        key=lambda value: (value["tool"], value["semantic_fingerprint"])
    )
    external_semantic_repeat_occurrences = sum(
        group["calls"] - 1 for group in external_semantic_duplicate_groups
    )
    deterministic_repeats_without_mutation = 0
    active_deterministic_successes: dict[str, dict[str, Any]] = {}
    for call in tcx_calls:
        if call["status"] != "ok":
            continue
        if _deterministic_success_tool(call["tool"]):
            if call["fingerprint"] in active_deterministic_successes:
                deterministic_repeats_without_mutation += 1
            active_deterministic_successes[call["fingerprint"]] = call
            continue
        for fingerprint, source_call in tuple(
            active_deterministic_successes.items()
        ):
            if _mutation_affects_retry(source_call, call):
                active_deterministic_successes.pop(fingerprint, None)
    artifact_duplicate_groups = sum(1 for count in artifact_read_counts.values() if count > 1)
    artifact_repeat_occurrences = sum(count - 1 for count in artifact_read_counts.values() if count > 1)
    unbounded_catalog_queries = sum(
        1
        for call in custom_calls.values()
        if call.get("catalog_query")
        and (
            (
                not call.get("names_only_catalog_query")
                and not call.get("schema_lookup_name_valid")
            )
            or (
                call.get("names_only_catalog_query")
                and not call.get("catalog_output_valid")
            )
            or call.get("catalog_description_scan")
        )
    )
    missing_schema_output_events = sum(
        1
        for call in custom_calls.values()
        if call.get("schema_lookup_name_valid")
        and not call.get("catalog_output_seen")
    )
    invalid_schema_outputs += missing_schema_output_events
    missing_schema_outputs += missing_schema_output_events
    retry_transitions: list[dict[str, Any]] = []
    errors_without_followup = 0
    blind_deterministic_retries = 0
    for index, call in enumerate(tcx_calls):
        if call["status"] != "error":
            continue
        followup: dict[str, Any] | None = None
        for candidate in tcx_calls[index + 1 :]:
            if candidate["tool"] == call["tool"]:
                followup = candidate
                break
            if (
                candidate["status"] == "ok"
                and not _deterministic_success_tool(candidate["tool"])
                and _mutation_affects_retry(call, candidate)
            ):
                break
        if (
            followup is not None
            and call["same_arguments_retryable"] is False
            and followup["fingerprint"] == call["fingerprint"]
        ):
            blind_deterministic_retries += 1
        if followup is None:
            errors_without_followup += 1
            continue
        same_arguments = followup["fingerprint"] == call["fingerprint"]
        if same_arguments and call["same_arguments_retryable"] is False:
            kind = "blind_deterministic_retry"
        elif same_arguments:
            kind = "same_arguments_retryability_unknown_or_allowed"
        elif followup["status"] == "ok":
            kind = "corrected_success"
        else:
            kind = "changed_arguments_error_followup"
        changed = _changed_path_metadata(call["arguments"], followup["arguments"])
        retry_transitions.append(
            {
                "tool": call["tool"],
                "kind": kind,
                "from_argument_fingerprint": call["fingerprint"],
                "to_argument_fingerprint": followup["fingerprint"],
                **changed,
                "followup_status": followup["status"],
                "same_arguments_retryable": call["same_arguments_retryable"],
            }
        )
    depth = _optional_nonnegative_int(
        spawn.get("depth") if spawn else 0, field="thread_spawn.depth"
    )
    assert depth is not None
    first_progress_latency_ms, max_visible_silence_ms = _progress_cadence(
        task_started_at=(task_started_at if task_started_timestamp_complete else None),
        progress_timestamps=progress_timestamps,
        progress_timestamps_complete=progress_timestamps_complete,
        final_answer_at=final_answer_at,
        final_answer_timestamp_complete=final_answer_timestamp_complete,
        task_complete_at=task_complete_at,
        task_complete_timestamp_complete=task_complete_timestamp_complete,
        latest_observed_at=latest_observed_at,
    )
    session = {
        "session_id": _safe_identifier(meta.get("id")),
        "kind": "root" if is_root else "subagent",
        "parent_session_id": _safe_identifier(spawn.get("parent_thread_id")) if spawn else None,
        "depth": depth,
        "agent_path_fingerprint": _value_fingerprint(spawn.get("agent_path")) if spawn else None,
        "agent_role": _safe_role(spawn.get("agent_role")) if spawn else "head-manager",
        "agent_nickname": _safe_agent_nickname(
            spawn.get("agent_nickname"), spawn.get("agent_role")
        )
        if spawn
        else None,
        "model": _safe_contract_value(model, expected_model),
        "reasoning_effort": _safe_contract_value(effort, expected_effort),
        "multi_agent_version": _safe_contract_value(multi_agent_version, "v2"),
        "multi_agent_mode": _safe_contract_value(
            multi_agent_mode, "explicitRequestOnly"
        ),
        "sandbox_type": _safe_contract_value(sandbox_type, EXPECTED_SANDBOX_TYPE),
        "permission_profile_type": _safe_contract_value(
            permission_profile_type, EXPECTED_PERMISSION_PROFILE_TYPE
        ),
        "permission_profile_fingerprint": permission_profile_fingerprint,
        "network_access": network_access,
        "turn_context_seen": turn_context_seen,
        "turn_context_events": {
            "event_count": turn_context_event_count,
            "valid_event_count": valid_turn_context_event_count,
            "all_events_valid": turn_context_event_count > 0
            and valid_turn_context_event_count == turn_context_event_count,
            "series_consistent": turn_context_series_consistent,
        },
        "base_instruction_chars": len(base_text),
        "fixed_role_base_loaded": FIXED_ROLE_MARKER in base_text,
        "head_manager_base_loaded": HEAD_MANAGER_MARKER in base_text,
        "termination": termination,
        "duration_ms": task_duration_ms,
        "time_to_first_token_ms": time_to_first_token_ms,
        "visible_progress_messages": visible_progress_count,
        "first_progress_latency_ms": first_progress_latency_ms,
        "max_visible_silence_ms": max_visible_silence_ms,
        "initial_progress_order": {
            "before_first_spawn": first_spawn_event_index is None
            or (
                first_progress_event_index is not None
                and first_progress_event_index < first_spawn_event_index
            ),
            "before_first_planning_web": first_planning_web_event_index is None
            or (
                first_progress_event_index is not None
                and first_progress_event_index < first_planning_web_event_index
            ),
        },
        "tokens": {
            **{key: int(value or 0) for key, value in final_tokens.items()},
            "event_count": token_event_count,
            "valid_event_count": valid_token_event_count,
            "invalid_event_count": token_event_count - valid_token_event_count,
            "all_events_valid": token_event_count > 0
            and valid_token_event_count == token_event_count,
            "series_monotonic": token_series_monotonic,
            "model_context_window": context_window,
            "max_last_input_tokens": max_last_input_tokens,
        },
        "function_tools": dict(sorted(function_counts.items())),
        "nested_tools": dict(sorted(nested_tool_counts.items())),
        "spawn_calls": spawn_calls,
        "subagent_activity": {
            "by_kind": dict(sorted(subagent_activity_counts.items())),
            "started_thread_ids": sorted(started_subagent_thread_ids),
            "started": started_subagent_events,
        },
        "tradingcodex_mcp": {
            "calls": sum(mcp_tool_counts.values()),
            "ok": mcp_ok,
            "errors": mcp_errors,
            "duration_ms": round(mcp_duration_ms, 3),
            "by_tool": dict(sorted(mcp_tool_counts.items())),
            "duplicate_groups": duplicate_groups,
            "repeat_occurrences": sum(group["calls"] - 1 for group in duplicate_groups),
            "consecutive_repeat_occurrences": consecutive_tcx_repeats,
            "consecutive_deterministic_repeat_occurrences": consecutive_deterministic_repeats,
            "deterministic_repeat_without_mutation_occurrences": deterministic_repeats_without_mutation,
            "retry_analysis": {
                "unstructured_errors": sum(
                    call["status"] == "error" and not call["error_structured"] for call in tcx_calls
                ),
                "blind_deterministic_retries": blind_deterministic_retries,
                "same_argument_retries_unknown_or_allowed": sum(
                    transition["kind"] == "same_arguments_retryability_unknown_or_allowed"
                    for transition in retry_transitions
                ),
                "corrected_successes": sum(
                    transition["kind"] == "corrected_success" for transition in retry_transitions
                ),
                "changed_argument_error_followups": sum(
                    transition["kind"] == "changed_arguments_error_followup" for transition in retry_transitions
                ),
                "errors_without_same_tool_followup": errors_without_followup,
                "transitions": retry_transitions,
            },
        },
        "external_mcp": {
            "calls": sum(external_mcp_counts.values()),
            "ok": external_mcp_ok,
            "errors": external_mcp_errors,
            "duration_ms": round(external_mcp_duration_ms, 3),
            "by_tool": dict(sorted(external_mcp_counts.items())),
            "exact_repeat_occurrences": external_exact_repeat_occurrences,
            "exact_fingerprints": sorted(external_exact_counts.elements()),
            "semantic_duplicate_groups": external_semantic_duplicate_groups,
            "semantic_repeat_occurrences": external_semantic_repeat_occurrences,
            "semantic_fingerprints": sorted(external_semantic_counts.elements()),
            "oversized_results": external_oversized_results,
            "promotion": {
                "dataset_results": promoted_external_results,
                "receipt_only_failures": receipt_only_external_failures,
                "unpromoted_results": len(pending_external_results),
                "invalid_receipts": invalid_external_promotion_receipts,
                "coordinate_mismatches": mismatched_external_promotions,
                "results_before_handoff": external_results_before_handoff,
                "calls_while_pending": external_calls_while_promotion_pending,
            },
            "openbb": {
                "provider_omissions": openbb_provider_omissions,
                "forbidden_calls": openbb_forbidden_calls,
                "discovery_calls": openbb_discovery_calls,
                "activation_calls": openbb_activation_calls,
                "admin_scope_fingerprints": sorted(
                    openbb_admin_scope_counts.elements()
                ),
                "overbroad_activations": openbb_overbroad_activations,
                "row_or_page_limit_violations": openbb_unbounded_row_calls,
                "chart_calls": openbb_chart_calls,
                "role_violations": openbb_role_violations,
            },
        },
        "artifact_reads": {
            "calls": mcp_tool_counts.get("get_research_artifact", 0),
            "result_text_chars": artifact_read_chars,
            "max_result_text_chars": artifact_read_max_chars,
            "duplicate_version_hash_window_groups": artifact_duplicate_groups,
            "repeat_occurrences": artifact_repeat_occurrences,
            "unbounded_or_legacy_reads": unbounded_artifact_reads,
        },
        "custom_exec": {
            "catalog_queries": catalog_queries,
            "names_only_queries": names_only_queries,
            "canonical_single_catalog_queries": canonical_single_catalog_queries,
            "canonical_compound_catalog_queries": canonical_compound_catalog_queries,
            "noncanonical_tool_catalog_queries": noncanonical_tool_catalog_queries,
            "valid_names_only_results": valid_names_only_results,
            "unbounded_catalog_queries": unbounded_catalog_queries,
            "catalog_description_scans": catalog_description_scans,
            "schema_lookup_queries": schema_lookup_queries,
            "valid_schema_lookups": valid_schema_lookups,
            "noncanonical_schema_lookups": noncanonical_schema_lookups,
            "unresolved_schema_lookups": unresolved_schema_lookups,
            "repeated_schema_lookups": repeated_schema_lookups,
            "invalid_schema_outputs": invalid_schema_outputs,
            "missing_schema_outputs": missing_schema_outputs,
            "truncated_schema_outputs": truncated_schema_outputs,
            "truncated_outputs": truncated_outputs,
            "truncated_catalog_outputs": truncated_catalog_outputs,
            "truncated_artifact_outputs": truncated_artifact_outputs,
            "truncated_generic_outputs": truncated_generic_outputs,
            "oversized_custom_outputs": oversized_custom_outputs,
            "bounded_artifact_oversize_exemptions": bounded_artifact_oversize_exemptions,
            "max_truncated_original_tokens": max_truncated_original_tokens,
            "max_truncated_omitted_tokens": max_truncated_omitted_tokens,
            "oversized_truncation_counts": oversized_truncation_counts,
        },
        "wait_agent": {
            "calls": wait_agent_calls,
            "timeouts_outside_contract": wait_timeouts_outside_contract,
            "chained_without_progress": chained_waits_without_progress,
        },
        # Retain the v3 compatibility field while the structured wait metrics
        # become the canonical report surface.
        "invalid_wait_timeouts": wait_timeouts_outside_contract,
        "created_artifact_ids": sorted(created_artifact_ids),
        "read_artifact_ids": sorted(read_artifact_ids),
        "consumed_artifact_ids": sorted(consumed_artifact_ids),
        "artifact_handoff": {
            "authenticated_write_count": len(authenticated_artifact_writes),
            "authenticated_write_ids": sorted(
                {receipt[0] for receipt in authenticated_artifact_writes}
            ),
            "final_receipt_present": final_artifact_receipt is not None,
            "final_receipt_artifact_id": (
                final_artifact_receipt[0]
                if final_artifact_receipt is not None
                else None
            ),
            "final_receipt_handoff_state": (
                final_artifact_receipt[2]
                if final_artifact_receipt is not None
                else None
            ),
            "final_receipt_matches_write": final_artifact_receipt
            in authenticated_artifact_writes,
            "authenticated_synthesis_count": len(authenticated_synthesis_inputs),
            "synthesis_input_artifact_ids": sorted(
                set().union(*authenticated_synthesis_inputs)
                if authenticated_synthesis_inputs
                else set()
            ),
        },
        "reasoning_items_ignored": reasoning_items_ignored,
    }
    return session


def _sum_token_usage(sessions: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    keys = (
        "input_tokens",
        "cached_input_tokens",
        "cache_write_input_tokens",
        "output_tokens",
        "reasoning_output_tokens",
        "total_tokens",
    )
    result = {key: 0 for key in keys}
    for session in sessions:
        tokens = session.get("tokens")
        if not isinstance(tokens, Mapping):
            continue
        for key in keys:
            result[key] += int(tokens.get(key) or 0)
    return result


def _candidate_violations(sessions: list[dict[str, Any]]) -> list[dict[str, str]]:
    violations: list[dict[str, str]] = []
    root = sessions[0]
    children = sessions[1:]

    def add(code: str, message: str) -> None:
        violations.append({"code": code, "message": message})

    if not children:
        add("no_subagents", "No descendant subagent rollout was discovered.")
    started = root["subagent_activity"]["started"]
    declared_children = {activity["thread_id"] for activity in started}
    children_by_id = {child["session_id"]: child for child in children}
    discovered_children = set(children_by_id)
    spawn_calls = root.get("spawn_calls") or []
    spawn_call_ids = [call["call_id"] for call in spawn_calls]
    started_event_ids = [activity["event_id"] for activity in started]
    started_thread_ids = [activity["thread_id"] for activity in started]
    spawn_by_call_id = {
        call["call_id"]: call
        for call in spawn_calls
        if call.get("call_id_valid")
    }
    if not declared_children:
        add("missing_started_lineage", "No authoritative sub_agent_activity(started) lineage was recorded.")
    if declared_children != discovered_children:
        add("subagent_rollout_mismatch", "Started child thread ids do not match the discovered descendant rollouts.")
    if (
        len(set(spawn_call_ids)) != len(spawn_call_ids)
        or len(set(started_event_ids)) != len(started_event_ids)
        or len(set(started_thread_ids)) != len(started_thread_ids)
        or set(spawn_call_ids) != set(started_event_ids)
    ):
        add("non_unique_spawn_lineage", "Spawn call ids, started event ids, and child thread ids are not one-to-one.")
    if len(spawn_calls) != len(started):
        add("spawn_activity_mismatch", "Root spawn calls and started-child activity records do not have equal counts.")
    expected_paths = [call.get("expected_agent_path_fingerprint") for call in spawn_calls]
    started_paths = [activity.get("agent_path_fingerprint") for activity in started]
    child_paths = [child.get("agent_path_fingerprint") for child in children]
    if any(
        len(paths) != len(set(paths))
        for paths in (expected_paths, started_paths, child_paths)
    ):
        add(
            "duplicate_child_path",
            "Spawn task names and observed child paths must identify one unique canonical child each.",
        )
    if any(not call.get("call_id_valid") for call in spawn_calls) or any(
        not activity.get("event_id_valid") for activity in started
    ):
        add(
            "invalid_spawn_lineage_id",
            "Every spawn call and started-child activity must carry a nonempty raw call/event id.",
        )
    lineage_flags = {
        "unlinked": False,
        "parent": False,
        "role": False,
        "nickname": False,
        "path": False,
    }
    for activity in started:
        child = children_by_id.get(activity["thread_id"])
        spawn_call = spawn_by_call_id.get(activity["event_id"])
        if spawn_call is None:
            lineage_flags["unlinked"] = True
            continue
        if child is None:
            continue
        if child["parent_session_id"] != root["session_id"]:
            lineage_flags["parent"] = True
        if child["agent_role"] != spawn_call.get("agent_type"):
            lineage_flags["role"] = True
        if child["agent_nickname"] != child["agent_role"]:
            lineage_flags["nickname"] = True
        if (
            child["agent_path_fingerprint"] != activity.get("agent_path_fingerprint")
            or child["agent_path_fingerprint"]
            != spawn_call.get("expected_agent_path_fingerprint")
        ):
            lineage_flags["path"] = True
    if lineage_flags["unlinked"]:
        add("unlinked_child_start", "A started child is not linked to a root spawn call id.")
    if lineage_flags["parent"]:
        add("child_parent_mismatch", "A child session does not identify the audited root as its parent.")
    if lineage_flags["role"]:
        add("child_role_mismatch", "A child role does not match the linked spawn agent_type.")
    if lineage_flags["nickname"]:
        add("child_nickname_mismatch", "A child nickname does not match its fixed agent role.")
    if lineage_flags["path"]:
        add(
            "child_path_mismatch",
            "A child path does not match its started activity and /root/{task_name} spawn path.",
        )
    if any(int(child["depth"]) != 1 for child in children):
        add("unexpected_subagent_depth", "All fixed-role children must remain at depth 1.")
    if any(child["agent_role"] not in FIXED_ROLES for child in children):
        add("unexpected_child_role", "At least one child is outside the exact fixed TradingCodex roster.")
    if not root["head_manager_base_loaded"]:
        add("head_manager_base_missing", "The root did not load the Head Manager base instructions.")
    if any(not child["fixed_role_base_loaded"] for child in children):
        add("fixed_role_base_missing", "At least one child did not load the compact fixed-role base.")
    if any(child["head_manager_base_loaded"] for child in children):
        add("head_manager_base_inherited", "At least one child inherited the Head Manager base.")
    if any(int(child["base_instruction_chars"]) > MAX_FIXED_ROLE_BASE_CHARS for child in children):
        add("fixed_role_base_oversized", "At least one child base exceeds the 7,000-character contract.")
    if any(
        int(child["artifact_handoff"]["authenticated_write_count"]) == 0
        for child in children
    ):
        add(
            "missing_authenticated_child_artifact",
            "At least one fixed-role child completed without an authenticated artifact write result.",
        )
    if any(
        not child["artifact_handoff"]["final_receipt_present"]
        for child in children
    ):
        add(
            "missing_child_artifact_receipt",
            "At least one fixed-role child final answer lacked the exact ARTIFACT receipt line.",
        )
    if any(
        child["artifact_handoff"]["final_receipt_present"]
        and not child["artifact_handoff"]["final_receipt_matches_write"]
        for child in children
    ):
        add(
            "mismatched_child_artifact_receipt",
            "At least one child ARTIFACT receipt did not exactly match an authenticated terminal write result.",
        )
    if any(
        child["artifact_handoff"]["final_receipt_matches_write"]
        and child["artifact_handoff"]["final_receipt_handoff_state"]
        != "accepted"
        for child in children
    ):
        add(
            "nonaccepted_child_artifact",
            "At least one fixed-role child completed with a non-accepted artifact, so the research run is not synthesis-ready.",
        )
    if int(root["artifact_handoff"]["authenticated_synthesis_count"]) == 0:
        add(
            "missing_authenticated_root_synthesis",
            "Head Manager did not create an authenticated synthesis_report artifact.",
        )
    child_receipt_ids = {
        str(child["artifact_handoff"]["final_receipt_artifact_id"])
        for child in children
        if child["artifact_handoff"]["final_receipt_matches_write"]
        and child["artifact_handoff"]["final_receipt_handoff_state"]
        == "accepted"
    }
    synthesis_input_ids = set(
        root["artifact_handoff"]["synthesis_input_artifact_ids"]
    )
    if child_receipt_ids and not child_receipt_ids <= synthesis_input_ids:
        add(
            "incomplete_synthesis_artifact_chain",
            "The authenticated root synthesis did not consume every matched child receipt artifact.",
        )
    if any(session["termination"] != "task_complete" for session in sessions):
        add("incomplete_rollout", "At least one root or child rollout did not terminate with task_complete.")
    if any(int(session["tokens"]["valid_event_count"]) == 0 for session in sessions):
        add("missing_token_evidence", "At least one rollout has no schema-complete cumulative token evidence.")
    if any(
        not session["tokens"]["all_events_valid"]
        or not session["tokens"]["series_monotonic"]
        for session in sessions
    ):
        add(
            "invalid_token_evidence",
            "At least one token event is malformed, exceeds its cumulative total, or regresses cumulative usage.",
        )
    if any(
        not session["turn_context_seen"]
        or not session["model"]
        or not session["reasoning_effort"]
        or session["multi_agent_version"] != "v2"
        or session["multi_agent_mode"] != "explicitRequestOnly"
        or not session["sandbox_type"]
        or not session["permission_profile_type"]
        or not session["permission_profile_fingerprint"]
        or session["network_access"] is None
        for session in sessions
    ):
        add(
            "incomplete_turn_context",
            "At least one rollout lacks model, effort, explicit V2 mode, sandbox, permission, or network context.",
        )
    if any(
        not session["turn_context_events"]["all_events_valid"]
        or not session["turn_context_events"]["series_consistent"]
        for session in sessions
    ):
        add(
            "invalid_turn_context_evidence",
            "At least one turn_context event violates or changes the projected model/runtime contract.",
        )
    if root["model"] != EXPECTED_ROOT_MODEL or root["reasoning_effort"] != EXPECTED_ROOT_EFFORT:
        add("unexpected_root_model", "The root model or reasoning effort differs from the TradingCodex contract.")
    if any(
        child["model"] != EXPECTED_CHILD_MODEL or child["reasoning_effort"] != EXPECTED_CHILD_EFFORT
        for child in children
    ):
        add("unexpected_child_model", "A child model or reasoning effort differs from the fixed-role contract.")
    if any(
        session["sandbox_type"] != EXPECTED_SANDBOX_TYPE
        or session["permission_profile_type"] != EXPECTED_PERMISSION_PROFILE_TYPE
        or session["network_access"] is not True
        for session in sessions
    ):
        add("unexpected_runtime_profile", "A rollout does not use the expected managed workspace/network profile.")
    if len({session["permission_profile_fingerprint"] for session in sessions}) != 1:
        add("permission_profile_mismatch", "Root and children did not load the same managed permission profile.")
    if any(int(session["custom_exec"]["unbounded_catalog_queries"]) for session in sessions):
        add("broad_tool_catalog_scan", "A runtime catalog query was not provably names-only and bounded to 12.")
    if any(
        int(session["custom_exec"]["noncanonical_tool_catalog_queries"])
        for session in sessions
    ):
        add(
            "noncanonical_tool_catalog_query",
            "A names-only catalog query was bounded but did not use the canonical slice(0, 12) const/direct form.",
        )
    if any(int(session["custom_exec"]["catalog_description_scans"]) for session in sessions):
        add(
            "tool_catalog_description_scan",
            "A runtime tool-catalog query accessed descriptions instead of names only.",
        )
    if any(int(session["custom_exec"]["noncanonical_schema_lookups"]) for session in sessions):
        add(
            "noncanonical_tool_schema_lookup",
            "A deferred tool-schema lookup did not use the exact anchored single-name form.",
        )
    if any(int(session["custom_exec"]["unresolved_schema_lookups"]) for session in sessions):
        add(
            "unresolved_tool_schema_lookup",
            "A deferred tool-schema lookup named a tool absent from a prior valid names result in the same session.",
        )
    if any(int(session["custom_exec"]["repeated_schema_lookups"]) for session in sessions):
        add(
            "repeated_tool_schema_lookup",
            "A deferred tool schema was requested more than once in one session.",
        )
    if any(int(session["custom_exec"]["invalid_schema_outputs"]) for session in sessions):
        add(
            "invalid_tool_schema_output",
            "A deferred tool-schema lookup lacked one exact, bounded, non-truncated data envelope.",
        )
    if any(int(session["custom_exec"]["missing_schema_outputs"]) for session in sessions):
        add(
            "missing_tool_schema_output",
            "A deferred tool-schema lookup returned the missing sentinel, an empty payload, or no output.",
        )
    if any(int(session["custom_exec"]["truncated_schema_outputs"]) for session in sessions):
        add(
            "truncated_tool_schema",
            "A deferred tool-schema payload was explicitly truncated.",
        )
    if any(int(session["custom_exec"]["truncated_catalog_outputs"]) for session in sessions):
        add("truncated_tool_catalog", "A tool-catalog response was explicitly truncated.")
    if any(int(session["custom_exec"]["truncated_artifact_outputs"]) for session in sessions):
        add("truncated_artifact_read", "An artifact-read response was explicitly truncated.")
    if any(int(session["custom_exec"]["truncated_generic_outputs"]) for session in sessions):
        add("truncated_generic_output", "A generic wrapper response was explicitly truncated.")
    if any(int(session["custom_exec"]["oversized_custom_outputs"]) for session in sessions):
        add(
            "oversized_custom_output",
            "A generic tool wrapper returned more than the 20,000-character candidate bound.",
        )
    external_semantic_counts = Counter(
        fingerprint
        for session in sessions
        for fingerprint in session["external_mcp"]["semantic_fingerprints"]
    )
    if any(
        int(session["external_mcp"]["promotion"]["unpromoted_results"])
        or int(session["external_mcp"]["promotion"]["results_before_handoff"])
        for session in sessions
    ):
        add(
            "external_data_not_promoted",
            "A recognized external financial-data result was not promoted to Dataset/Snapshot/Receipt ids, or a typed receipt-only failure, before artifact handoff.",
        )
    if any(
        int(session["external_mcp"]["promotion"]["calls_while_pending"])
        for session in sessions
    ):
        add(
            "external_data_promotion_not_immediate",
            "A second recognized external financial-data call occurred before the prior result was promoted through record_external_data_result.",
        )
    if any(
        int(session["external_mcp"]["promotion"]["invalid_receipts"])
        or int(session["external_mcp"]["promotion"]["coordinate_mismatches"])
        for session in sessions
    ):
        add(
            "external_data_promotion_mismatch",
            "A record_external_data_result response lacked valid lineage ids or did not match the observed external tool FQN, provider, and semantic coordinates.",
        )
    if any(count > 1 for count in external_semantic_counts.values()):
        add(
            "external_semantic_repeat",
            "An external MCP semantic call was repeated in the same analysis tree instead of reusing its promoted result.",
        )
    if any(int(session["external_mcp"]["oversized_results"]) for session in sessions):
        add(
            "oversized_external_result",
            "An external MCP result exceeded the 20,000-character context bound instead of being promoted and handed off by ID.",
        )
    if any(
        int(session["external_mcp"]["openbb"]["provider_omissions"])
        for session in sessions
    ):
        add(
            "openbb_provider_omitted",
            "An OpenBB data call relied on an implicit provider.",
        )
    if any(
        int(session["external_mcp"]["openbb"]["role_violations"])
        for session in sessions
    ):
        add(
            "openbb_role_violation",
            "OpenBB was called outside the six evidence-producing fixed roles.",
        )
    if any(
        int(session["external_mcp"]["openbb"]["forbidden_calls"])
        for session in sessions
    ):
        add(
            "openbb_forbidden_tool",
            "An OpenBB skill-install, download, account, broker, order, mutation, or non-read method was called.",
        )
    openbb_admin_scope_counts = Counter(
        fingerprint
        for session in sessions
        for fingerprint in session["external_mcp"]["openbb"][
            "admin_scope_fingerprints"
        ]
    )
    if any(count > 1 for count in openbb_admin_scope_counts.values()):
        add(
            "openbb_repeated_discovery",
            "An OpenBB workflow/role-session repeated discovery or activation for the same category/subcategory scope.",
        )
    if any(
        int(session["external_mcp"]["openbb"]["overbroad_activations"])
        for session in sessions
    ):
        add(
            "openbb_overbroad_activation",
            "An OpenBB activation did not contain one to three exact tool names.",
        )
    if any(
        int(session["external_mcp"]["openbb"]["row_or_page_limit_violations"])
        or int(session["external_mcp"]["openbb"]["chart_calls"])
        for session in sessions
    ):
        add(
            "openbb_unbounded_result_request",
            "An OpenBB call requested charts or more than 120 observations.",
        )
    if any(
        int(session["tradingcodex_mcp"]["consecutive_deterministic_repeat_occurrences"])
        for session in sessions
    ):
        add("consecutive_deterministic_repeat", "A deterministic result was followed by an identical TCX call.")
    if any(
        int(
            session["tradingcodex_mcp"][
                "deterministic_repeat_without_mutation_occurrences"
            ]
        )
        for session in sessions
    ):
        add(
            "deterministic_repeat_without_mutation",
            "A deterministic success was repeated with identical arguments before a successful mutation affected the same resource.",
        )
    if any(int(session["artifact_reads"]["repeat_occurrences"]) for session in sessions):
        add("artifact_window_repeat", "A session re-read the same artifact version/hash/window.")
    if any(int(session["artifact_reads"]["unbounded_or_legacy_reads"]) for session in sessions):
        add(
            "unbounded_artifact_read",
            "A successful artifact read was legacy/full or returned Markdown without an explicit bounded review window.",
        )
    if any(
        int(session["tradingcodex_mcp"]["retry_analysis"]["blind_deterministic_retries"])
        for session in sessions
    ):
        add("blind_deterministic_retry", "A deterministic MCP error was retried with unchanged arguments.")
    if any(
        int(session["tradingcodex_mcp"]["retry_analysis"]["unstructured_errors"])
        for session in sessions
    ):
        add("unstructured_mcp_error", "At least one TCX failure lacked structured retryability metadata.")
    if any(
        int(session["wait_agent"]["timeouts_outside_contract"])
        for session in sessions
    ):
        add(
            "invalid_wait_timeout",
            "A wait_agent call omitted timeout_ms or used a value outside the required 10,000-30,000 ms range.",
        )
    if any(
        int(session["wait_agent"]["chained_without_progress"])
        for session in sessions
    ):
        add(
            "chained_wait_without_progress",
            "A wait_agent call followed another wait_agent call without an intervening visible progress update.",
        )
    duration_ms = int(root.get("duration_ms") or 0)
    if duration_ms > 60_000 and int(root.get("visible_progress_messages") or 0) == 0:
        add("missing_progress_update", "A root run longer than 60 seconds emitted no observable progress update.")
    first_progress_latency_ms = root.get("first_progress_latency_ms")
    max_visible_silence_ms = root.get("max_visible_silence_ms")
    if (
        duration_ms > 60_000
        and int(root.get("visible_progress_messages") or 0) > 0
        and (
            not isinstance(first_progress_latency_ms, int)
            or not isinstance(max_visible_silence_ms, int)
        )
    ):
        add(
            "unverifiable_progress_cadence",
            "A root run longer than 60 seconds had progress events but incomplete timestamps prevented cadence verification.",
        )
    if (
        isinstance(first_progress_latency_ms, int)
        and first_progress_latency_ms > MAX_FIRST_PROGRESS_LATENCY_MS
    ):
        add(
            "late_first_progress_update",
            "The first timestamped root progress update arrived more than 60 seconds after task start.",
        )
    if (
        isinstance(max_visible_silence_ms, int)
        and max_visible_silence_ms > MAX_VISIBLE_SILENCE_MS
    ):
        add(
            "visible_progress_silence_exceeded",
            "A provable root interval between task start, progress updates, and the final visible boundary exceeded 60 seconds.",
        )
    if any(
        not call.get("namespace_valid")
        or not call.get("call_id_valid")
        or not call.get("argument_keys_valid")
        or call.get("agent_type") not in FIXED_ROLES
        or call.get("fork_turns") != "none"
        or not call.get("message_valid")
        or not call.get("task_name_valid")
        for call in spawn_calls
    ):
        add("invalid_spawn_contract", "A root spawn violated the strict compact four-field agents.spawn_agent contract.")
    if any(call.get("model_override") or call.get("reasoning_override") for call in spawn_calls):
        add("spawn_model_override", "A root spawn supplied an explicit model or reasoning override.")
    initial_order = root.get("initial_progress_order") or {}
    if not initial_order.get("before_first_spawn"):
        add(
            "initial_progress_after_spawn",
            "The first observable root progress update did not precede the first child spawn.",
        )
    if not initial_order.get("before_first_planning_web"):
        add(
            "initial_progress_after_planning_web",
            "The first observable root progress update did not precede planning web reconnaissance.",
        )
    if any(int(session["function_tools"].get("followup_task", 0)) for session in sessions):
        add("followup_task_used", "A fixed-role native smoke used followup_task.")
    if any(int(child["function_tools"].get("spawn_agent", 0)) for child in children):
        add("child_spawn_attempt", "A fixed-role child attempted to spawn another agent.")
    if any(
        any(int(child["function_tools"].get(tool, 0)) for tool in _CHILD_FORBIDDEN_COORDINATION_TOOLS)
        for child in children
    ):
        add(
            "child_coordination_attempt",
            "A fixed-role child attempted to use a forbidden coordination tool.",
        )
    return violations


def audit_codex_trace(root_rollout: Path, *, candidate: bool = False) -> dict[str, Any]:
    rollouts = discover_rollouts(root_rollout)
    root_meta = rollouts[0][1]
    root_id = str(root_meta.get("id") or "")
    sessions: list[dict[str, Any]] = []
    for path, meta in rollouts:
        sessions.append(_summarize_session(path, meta, root_id=root_id))

    totals = _sum_token_usage(sessions)
    created_artifacts = sorted({artifact_id for session in sessions for artifact_id in session["created_artifact_ids"]})
    consumed_artifacts = sorted({artifact_id for session in sessions for artifact_id in session["consumed_artifact_ids"]})
    by_role = Counter(session["agent_role"] for session in sessions)
    declared_children = set(sessions[0]["subagent_activity"]["started_thread_ids"])
    discovered_children = {session["session_id"] for session in sessions[1:]}
    violations = _candidate_violations(sessions) if candidate else []
    return {
        "schema_version": SCHEMA_VERSION,
        "root_session_id": _safe_identifier(root_id),
        "mode": "candidate" if candidate else "observation",
        "status": "fail" if violations else "pass",
        "scope": {
            "session_count": len(sessions),
            "subagent_count": max(0, len(sessions) - 1),
            "max_depth": max((int(session["depth"]) for session in sessions), default=0),
            "roles": dict(sorted(by_role.items())),
            "declared_subagent_count": len(declared_children),
            "missing_subagent_rollout_ids": sorted(declared_children - discovered_children),
            "unstarted_descendant_ids": sorted(discovered_children - declared_children),
        },
        "summary": {
            "root_duration_ms": sessions[0].get("duration_ms"),
            "tradingcodex_mcp_calls": sum(int(session["tradingcodex_mcp"]["calls"]) for session in sessions),
            "tradingcodex_mcp_ok": sum(int(session["tradingcodex_mcp"]["ok"]) for session in sessions),
            "tradingcodex_mcp_errors": sum(int(session["tradingcodex_mcp"]["errors"]) for session in sessions),
            "tradingcodex_mcp_duration_ms": round(
                sum(float(session["tradingcodex_mcp"]["duration_ms"]) for session in sessions), 3
            ),
            "external_mcp_calls": sum(
                int(session["external_mcp"]["calls"]) for session in sessions
            ),
            "external_mcp_ok": sum(
                int(session["external_mcp"]["ok"]) for session in sessions
            ),
            "external_mcp_errors": sum(
                int(session["external_mcp"]["errors"]) for session in sessions
            ),
            "external_mcp_duration_ms": round(
                sum(float(session["external_mcp"]["duration_ms"]) for session in sessions),
                3,
            ),
            "external_exact_repeat_occurrences": sum(
                count - 1
                for count in Counter(
                    fingerprint
                    for session in sessions
                    for fingerprint in session["external_mcp"]["exact_fingerprints"]
                ).values()
                if count > 1
            ),
            "external_semantic_repeat_occurrences": sum(
                count - 1
                for count in Counter(
                    fingerprint
                    for session in sessions
                    for fingerprint in session["external_mcp"]["semantic_fingerprints"]
                ).values()
                if count > 1
            ),
            "external_dataset_promotions": sum(
                int(session["external_mcp"]["promotion"]["dataset_results"])
                for session in sessions
            ),
            "external_receipt_only_failures": sum(
                int(session["external_mcp"]["promotion"]["receipt_only_failures"])
                for session in sessions
            ),
            "external_unpromoted_results": sum(
                int(session["external_mcp"]["promotion"]["unpromoted_results"])
                for session in sessions
            ),
            "external_promotion_mismatches": sum(
                int(session["external_mcp"]["promotion"]["invalid_receipts"])
                + int(
                    session["external_mcp"]["promotion"][
                        "coordinate_mismatches"
                    ]
                )
                for session in sessions
            ),
            "external_calls_while_promotion_pending": sum(
                int(session["external_mcp"]["promotion"]["calls_while_pending"])
                for session in sessions
            ),
            "openbb_discovery_calls": sum(
                int(session["external_mcp"]["openbb"]["discovery_calls"])
                for session in sessions
            ),
            "openbb_activation_calls": sum(
                int(session["external_mcp"]["openbb"]["activation_calls"])
                for session in sessions
            ),
            "openbb_repeated_admin_scope_occurrences": sum(
                count - 1
                for count in Counter(
                    fingerprint
                    for session in sessions
                    for fingerprint in session["external_mcp"]["openbb"][
                        "admin_scope_fingerprints"
                    ]
                ).values()
                if count > 1
            ),
            "exact_repeat_occurrences": sum(
                int(session["tradingcodex_mcp"]["repeat_occurrences"]) for session in sessions
            ),
            "deterministic_repeat_without_mutation_occurrences": sum(
                int(
                    session["tradingcodex_mcp"][
                        "deterministic_repeat_without_mutation_occurrences"
                    ]
                )
                for session in sessions
            ),
            "artifact_read_calls": sum(int(session["artifact_reads"]["calls"]) for session in sessions),
            "artifact_read_result_text_chars": sum(
                int(session["artifact_reads"]["result_text_chars"]) for session in sessions
            ),
            "artifact_read_repeat_occurrences": sum(
                int(session["artifact_reads"]["repeat_occurrences"]) for session in sessions
            ),
            "unbounded_artifact_reads": sum(
                int(session["artifact_reads"]["unbounded_or_legacy_reads"])
                for session in sessions
            ),
            "catalog_description_scans": sum(
                int(session["custom_exec"]["catalog_description_scans"]) for session in sessions
            ),
            "catalog_queries": sum(int(session["custom_exec"]["catalog_queries"]) for session in sessions),
            "names_only_queries": sum(
                int(session["custom_exec"]["names_only_queries"]) for session in sessions
            ),
            "canonical_single_catalog_queries": sum(
                int(session["custom_exec"]["canonical_single_catalog_queries"])
                for session in sessions
            ),
            "canonical_compound_catalog_queries": sum(
                int(session["custom_exec"]["canonical_compound_catalog_queries"])
                for session in sessions
            ),
            "noncanonical_tool_catalog_queries": sum(
                int(session["custom_exec"]["noncanonical_tool_catalog_queries"])
                for session in sessions
            ),
            "valid_names_only_results": sum(
                int(session["custom_exec"]["valid_names_only_results"])
                for session in sessions
            ),
            "unbounded_catalog_queries": sum(
                int(session["custom_exec"]["unbounded_catalog_queries"]) for session in sessions
            ),
            "schema_lookup_queries": sum(
                int(session["custom_exec"]["schema_lookup_queries"]) for session in sessions
            ),
            "valid_schema_lookups": sum(
                int(session["custom_exec"]["valid_schema_lookups"]) for session in sessions
            ),
            "noncanonical_schema_lookups": sum(
                int(session["custom_exec"]["noncanonical_schema_lookups"])
                for session in sessions
            ),
            "unresolved_schema_lookups": sum(
                int(session["custom_exec"]["unresolved_schema_lookups"])
                for session in sessions
            ),
            "repeated_schema_lookups": sum(
                int(session["custom_exec"]["repeated_schema_lookups"])
                for session in sessions
            ),
            "invalid_schema_outputs": sum(
                int(session["custom_exec"]["invalid_schema_outputs"])
                for session in sessions
            ),
            "missing_schema_outputs": sum(
                int(session["custom_exec"]["missing_schema_outputs"])
                for session in sessions
            ),
            "truncated_schema_outputs": sum(
                int(session["custom_exec"]["truncated_schema_outputs"])
                for session in sessions
            ),
            "truncated_outputs": sum(int(session["custom_exec"]["truncated_outputs"]) for session in sessions),
            "truncated_catalog_outputs": sum(
                int(session["custom_exec"]["truncated_catalog_outputs"]) for session in sessions
            ),
            "truncated_artifact_outputs": sum(
                int(session["custom_exec"]["truncated_artifact_outputs"]) for session in sessions
            ),
            "truncated_generic_outputs": sum(
                int(session["custom_exec"]["truncated_generic_outputs"])
                for session in sessions
            ),
            "oversized_truncation_counts": sum(
                int(session["custom_exec"]["oversized_truncation_counts"])
                for session in sessions
            ),
            "oversized_custom_outputs": sum(
                int(session["custom_exec"]["oversized_custom_outputs"])
                for session in sessions
            ),
            "bounded_artifact_oversize_exemptions": sum(
                int(session["custom_exec"]["bounded_artifact_oversize_exemptions"])
                for session in sessions
            ),
            "unstructured_mcp_errors": sum(
                int(session["tradingcodex_mcp"]["retry_analysis"]["unstructured_errors"])
                for session in sessions
            ),
            "blind_deterministic_retries": sum(
                int(session["tradingcodex_mcp"]["retry_analysis"]["blind_deterministic_retries"])
                for session in sessions
            ),
            "corrected_argument_retries": sum(
                int(session["tradingcodex_mcp"]["retry_analysis"]["corrected_successes"])
                for session in sessions
            ),
            "changed_argument_error_followups": sum(
                int(session["tradingcodex_mcp"]["retry_analysis"]["changed_argument_error_followups"])
                for session in sessions
            ),
            "errors_without_same_tool_followup": sum(
                int(session["tradingcodex_mcp"]["retry_analysis"]["errors_without_same_tool_followup"])
                for session in sessions
            ),
            "root_visible_progress_messages": int(sessions[0]["visible_progress_messages"]),
            "root_first_progress_latency_ms": sessions[0]["first_progress_latency_ms"],
            "root_max_visible_silence_ms": sessions[0]["max_visible_silence_ms"],
            "wait_agent_calls": sum(
                int(session["wait_agent"]["calls"]) for session in sessions
            ),
            "wait_timeouts_outside_contract": sum(
                int(session["wait_agent"]["timeouts_outside_contract"])
                for session in sessions
            ),
            "chained_waits_without_progress": sum(
                int(session["wait_agent"]["chained_without_progress"])
                for session in sessions
            ),
            "reasoning_items_ignored": sum(int(session["reasoning_items_ignored"]) for session in sessions),
            "created_artifact_ids": created_artifacts,
            "consumed_artifact_ids": consumed_artifacts,
            "authenticated_artifact_writes": sum(
                int(session["artifact_handoff"]["authenticated_write_count"])
                for session in sessions
            ),
            "matched_child_artifact_receipts": sum(
                bool(child["artifact_handoff"]["final_receipt_matches_write"])
                for child in sessions[1:]
            ),
            "authenticated_root_syntheses": int(
                sessions[0]["artifact_handoff"]["authenticated_synthesis_count"]
            ),
        },
        "aggregate_final_token_usage": totals,
        "max_observed_session": {
            "model_context_window": max(
                (int(session["tokens"]["model_context_window"]) for session in sessions), default=0
            ),
            "last_input_tokens": max(
                (int(session["tokens"]["max_last_input_tokens"]) for session in sessions), default=0
            ),
            "base_instruction_chars": max((int(session["base_instruction_chars"]) for session in sessions), default=0),
        },
        "sessions": sessions,
        "candidate_violations": violations,
        "privacy": {
            "private_reasoning_analyzed": False,
            "raw_reasoning_or_message_content_exported": False,
            "unwhitelisted_raw_tool_arguments_exported": False,
            "argument_metadata_whitelist": [
                "validated spawn agent_type/fork, size/shape flags, and safe call id",
                "artifact identifiers",
                "changed-path count and SHA-256 fingerprint",
                "safe session ids and hashed agent paths",
                "syntactically valid direct TCX tool identifiers plus recognized event/nested-tool names; invalid TCX and unknown event/nested names are hashed",
                "OpenBB tool names plus hashed user-MCP server/tool names and external semantic argument fingerprints; raw external arguments and results are never exported",
            ],
            "invalid_contract_and_unknown_event_metadata_hashed": True,
            "duplicate_argument_values_reported_as_sha256": True,
        },
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Audit observable Codex JSONL behavior for one root rollout and its descendant subagents."
    )
    parser.add_argument("root_rollout", type=Path, help="Explicit root rollout JSONL path")
    parser.add_argument(
        "--candidate",
        action="store_true",
        help="Apply TradingCodex compact-context and deterministic-retry acceptance gates",
    )
    parser.add_argument("--compact", action="store_true", help="Emit compact JSON")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = audit_codex_trace(args.root_rollout, candidate=args.candidate)
    except TraceAuditError as exc:
        print(
            json.dumps(
                {"schema_version": SCHEMA_VERSION, "status": "error", "error": str(exc)},
                allow_nan=False,
            )
        )
        return 2
    try:
        rendered = json.dumps(
            result,
            ensure_ascii=False,
            sort_keys=True,
            indent=None if args.compact else 2,
            allow_nan=False,
        )
    except (OverflowError, ValueError) as exc:
        print(
            json.dumps(
                {
                    "schema_version": SCHEMA_VERSION,
                    "status": "error",
                    "error": f"audit output is not finite JSON: {exc}",
                },
                allow_nan=False,
            )
        )
        return 2
    print(rendered)
    return 1 if args.candidate and result["status"] != "pass" else 0


if __name__ == "__main__":
    sys.exit(main())

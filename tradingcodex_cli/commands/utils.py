from __future__ import annotations

import json
import sys
import tomllib
from pathlib import Path
from typing import Any

import yaml

from tradingcodex_service.application.common import _safe_read, read_json as _read_json
from tradingcodex_service.application.agents import (
    build_projection_state,
    list_user_visible_skills,
    project_agent_configuration,
    skills_for_role as file_native_skills_for_role,
    write_skill_proposal_file,
)

def list_subagents(root: Path) -> list[dict[str, str]]:
    agents = []
    for path in sorted((root / ".codex" / "agents").glob("*.toml")):
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        name = str(data.get("name") or path.stem)
        agents.append({"name": name, "runtime_label": name, "description": str(data.get("description") or "")})
    return agents


def list_skills(root: Path, include_internal: bool = True) -> list[str]:
    if include_internal:
        return sorted(build_projection_state(root)["skills"])
    return list_user_visible_skills(root)


def read_thread_policy(root: Path) -> dict[str, Any]:
    codex_path = root / ".codex" / "config.toml"
    tradingcodex_path = root / ".tradingcodex" / "config.yaml"
    try:
        codex = tomllib.loads(codex_path.read_text(encoding="utf-8"))
        tradingcodex = yaml.safe_load(tradingcodex_path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError, yaml.YAMLError) as exc:
        raise ValueError("canonical thread policy configuration is unavailable") from exc
    agents = codex.get("agents") if isinstance(codex, dict) else None
    features = codex.get("features") if isinstance(codex, dict) else None
    multi_agent_v2 = features.get("multi_agent_v2") if isinstance(features, dict) else None
    subagents = tradingcodex.get("subagents") if isinstance(tradingcodex, dict) else None
    if not isinstance(agents, dict) or not isinstance(multi_agent_v2, dict) or not isinstance(subagents, dict):
        raise ValueError("canonical thread policy sections are required")
    if multi_agent_v2.get("enabled") is not True:
        raise ValueError("features.multi_agent_v2.enabled must be true")
    if "max_threads" in agents:
        raise ValueError("agents.max_threads is incompatible with enabled MultiAgent V2")
    max_session_threads = _thread_policy_integer(
        multi_agent_v2,
        "max_concurrent_threads_per_session",
        minimum=3,
    )
    max_threads = max_session_threads - 1
    max_depth = _thread_policy_integer(agents, "max_depth", minimum=1)
    reserved = _thread_policy_integer(subagents, "reserved_threads", minimum=0)
    overflow = subagents.get("overflow_strategy")
    if overflow != "batch_queue":
        raise ValueError("subagents.overflow_strategy must be batch_queue")
    if reserved >= max_threads:
        raise ValueError("subagents.reserved_threads must be smaller than the MultiAgent V2 child-thread capacity")
    return {
        "multi_agent_version": "v2",
        "max_concurrent_threads_per_session": max_session_threads,
        "max_threads": max_threads,
        "max_depth": max_depth,
        "reserved_threads": reserved,
        "max_parallel_subagents": max_threads - reserved,
        "overflow_strategy": overflow,
    }


def _thread_policy_integer(section: dict[str, Any], field: str, *, minimum: int) -> int:
    value = section.get(field)
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise ValueError(f"thread policy {field} must be an integer >= {minimum}")
    return value


def read_subagent_state(root: Path, run_id: str | None) -> dict[str, Any]:
    state = _read_json(root / ".tradingcodex" / "mainagent" / "subagent-session-state.json", {"updated_at": None, "active": {}, "completed": [], "events": []})
    if not run_id:
        return {"run_filter": None, **state}
    return {
        "run_filter": run_id,
        "updated_at": state.get("updated_at"),
        "active": {role: record for role, record in state.get("active", {}).items() if record.get("run_id") == run_id},
        "completed": [record for record in state.get("completed", []) if record.get("run_id") == run_id],
        "events": [record for record in state.get("events", []) if record.get("run_id") == run_id],
    }


def skills_for_role(root: Path, role: str) -> list[str]:
    return file_native_skills_for_role(root, role)


def write_skill_proposal(root: Path, type_: str, target: str, skill: str) -> dict[str, Any]:
    return write_skill_proposal_file(root, type_, target, skill)


def apply_skill_proposal(root: Path, proposal_path: Path, approved_by: str | None) -> None:
    result = project_agent_configuration(root, proposal_path=proposal_path, applied_by=approved_by or "local-cli")
    print_json({"status": "applied", "proposal_path": proposal_path.relative_to(root).as_posix(), "projection_hash": result["projection_hash"]})


def path_check(root: Path, layer: str, name: str, rel: str, codex_native: bool) -> dict[str, Any]:
    ok = (root / rel).exists()
    return {"layer": layer, "name": name, "ok": ok, "codexNative": codex_native, "detail": "found" if ok else "missing"}


def text_check(root: Path, layer: str, name: str, rel: str, pattern: str, codex_native: bool) -> dict[str, Any]:
    ok = pattern in _safe_read(root / rel)
    return {"layer": layer, "name": name, "ok": ok, "codexNative": codex_native, "detail": f"contains {pattern}" if ok else f"missing {pattern}"}


def classify_artifact_path(rel: str) -> str:
    if rel.endswith(".run-card.json") or rel.endswith(".run-card.md"):
        return "evidence_run_card"
    if rel.endswith(".validation-card.json") or rel.endswith(".validation-card.md"):
        return "validation_card"
    if rel.startswith("trading/research/source-snapshots/"):
        return "source_snapshot"
    if rel.startswith("trading/forecasts/"):
        return "forecast_ledger"
    if rel.startswith("trading/decisions/"):
        return "decision_package"
    if rel.startswith("trading/research/"):
        return "evidence_pack"
    if "order_ticket" in rel:
        return "order_ticket"
    if "approval_receipt" in rel:
        return "approval_receipt"
    if rel.startswith("trading/reports/"):
        return "report"
    return "artifact"


def _option_value(args: list[str], name: str) -> str | None:
    if name not in args:
        return None
    index = args.index(name)
    if index + 1 >= len(args) or args[index + 1].startswith("--"):
        raise ValueError(f"{name} requires a value")
    return args[index + 1]


def _validate_options(
    args: list[str],
    *,
    value_options: set[str],
    flag_options: set[str] | None = None,
) -> None:
    flags = flag_options or set()
    index = 0
    while index < len(args):
        option = args[index]
        if not option.startswith("--"):
            index += 1
            continue
        if option in flags:
            index += 1
            continue
        if option not in value_options:
            raise ValueError(f"unsupported option: {option}")
        if index + 1 >= len(args) or args[index + 1].startswith("--"):
            raise ValueError(f"{option} requires a value")
        index += 2


def _list_option(args: list[str], name: str) -> list[Any] | None:
    value = _option_value(args, name)
    if value in (None, ""):
        return None
    try:
        parsed = json.loads(value)
    except Exception:
        return [item.strip() for item in value.replace(";", ",").split(",") if item.strip()]
    if isinstance(parsed, list):
        return parsed
    if parsed in (None, ""):
        return []
    return [parsed]


def json_object_input(root: Path, value: str | None, usage: str) -> dict[str, Any]:
    if not value:
        raise ValueError(usage)
    if value == "-":
        text = sys.stdin.read()
    else:
        path = Path(value)
        text = (path if path.is_absolute() else root / path).read_text(encoding="utf-8")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"input must be valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("input JSON must be an object")
    return payload


def _parse_agent_list(args: list[str]) -> list[str]:
    return [item.strip() for arg in args for item in arg.split(",") if item.strip()]


def print_json(value: Any) -> None:
    print(json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True, default=str))

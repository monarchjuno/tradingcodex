from __future__ import annotations

import json
import tomllib
from pathlib import Path
from typing import Any

from tradingcodex_service.application.common import _safe_read, read_json as _read_json, sanitize_id
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
    config = _safe_read(root / ".codex" / "config.toml")
    tc_config = _safe_read(root / ".tradingcodex" / "config.yaml")
    max_threads = int(_regex(config, r"^max_threads\s*=\s*(\d+)", "1"))
    max_depth = int(_regex(config, r"^max_depth\s*=\s*(\d+)", "1"))
    reserved = int(_regex(tc_config, r"^\s*reserved_threads:\s*(\d+)", "0"))
    return {"max_threads": max_threads, "max_depth": max_depth, "reserved_threads": reserved, "max_parallel_subagents": max(1, max_threads - reserved), "overflow_strategy": _regex(tc_config, r"^\s*overflow_strategy:\s*([A-Za-z0-9_-]+)", "batch_queue")}


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


def read_loop_state(root: Path, run_id: str | None = None) -> dict[str, Any]:
    if run_id:
        return _read_json(root / ".tradingcodex" / "mainagent" / "workflows" / sanitize_id(run_id) / "loop-state.json", {})
    latest_path = root / ".tradingcodex" / "mainagent" / "workflow-loop-state.json"
    latest = _read_json(latest_path, {})
    state_path = str(latest.get("state_path") or "") if isinstance(latest, dict) else ""
    if state_path and state_path != ".tradingcodex/mainagent/workflow-loop-state.json":
        return _read_json(root / state_path, latest)
    return latest


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
    if rel.startswith("trading/forecasts/"):
        return "forecast_ledger"
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
    try:
        return args[args.index(name) + 1]
    except Exception:
        return None


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


def _parse_agent_list(args: list[str]) -> list[str]:
    return [item.strip() for arg in args for item in arg.split(",") if item.strip()]


def _regex(text: str, pattern: str, default: str) -> str:
    import re

    match = re.search(pattern, text, flags=re.M)
    return match.group(1) if match else default


def print_json(value: Any) -> None:
    print(json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True, default=str))

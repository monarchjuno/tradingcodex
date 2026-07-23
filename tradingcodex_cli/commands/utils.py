from __future__ import annotations

import json
import sys
import tomllib
from pathlib import Path
from typing import Any

from tradingcodex_service.application.common import _safe_read
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


def print_json(value: Any) -> None:
    print(json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True, default=str))

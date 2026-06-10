from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tradingcodex_service.domain import (
    ROLE_SKILL_MAP,
    USER_VISIBLE_SKILLS,
    sanitize_id,
    write_audit_event,
    write_json,
)

def list_subagents(root: Path) -> list[dict[str, str]]:
    agents = []
    for path in sorted((root / ".codex" / "agents").glob("*.toml")):
        text = path.read_text(encoding="utf-8")
        name = _toml_string(text, "name") or path.stem
        agents.append({"name": name, "runtime_label": name, "description": _toml_string(text, "description") or ""})
    return agents


def list_skills(root: Path, include_internal: bool = True) -> list[str]:
    skill_dir = root / ".agents" / "skills"
    if not skill_dir.exists():
        return []
    installed = {path.name for path in skill_dir.iterdir() if path.is_dir()}
    if include_internal:
        return sorted(installed)
    return [skill for skill in USER_VISIBLE_SKILLS if skill in installed]


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


def skills_for_role(root: Path, role: str) -> list[str]:
    applied = []
    for line in _safe_read(root / ".tradingcodex" / "mainagent" / "applied-skill-changes.jsonl").splitlines():
        try:
            record = json.loads(line)
        except Exception:
            continue
        if record.get("target") == role and record.get("skill"):
            applied.append(record["skill"])
    return [skill for skill in dict.fromkeys(ROLE_SKILL_MAP.get(role, []) + applied) if (root / ".agents" / "skills" / skill / "SKILL.md").exists()]


def write_skill_proposal(root: Path, type_: str, target: str, skill: str) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    proposal_id = f"skill-{type_}-{target}-{skill}-{now.strftime('%Y%m%dT%H%M%S%fZ')}"
    path = root / ".tradingcodex" / "mainagent" / "skill-change-proposals" / f"{sanitize_id(proposal_id)}.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join([f"id: {proposal_id}", f"type: {type_}", f"target: {target}", f"skill: {skill}", f"created_at: {now.isoformat().replace('+00:00', 'Z')}", "requires_validation: true", "requires_audit: true", "status: proposed", ""]), encoding="utf-8")
    write_audit_event(root, {"type": "skill_change.proposed", "payload": {"id": proposal_id, "type": type_, "target": target, "skill": skill, "path": path.relative_to(root).as_posix()}}, "head-manager", "cli")
    return {"status": "proposed", "id": proposal_id, "path": path.relative_to(root).as_posix()}


def apply_skill_proposal(root: Path, proposal_path: Path, approved_by: str | None) -> None:
    text = proposal_path.read_text(encoding="utf-8")
    type_ = _yaml_value(text, "type") or "update"
    target = _yaml_value(text, "target")
    skill = _yaml_value(text, "skill")
    if not target or not skill:
        raise ValueError("Invalid skill proposal")
    execution_sensitive = target == "execution-operator" or "execute" in skill or "order" in skill
    if execution_sensitive and not approved_by:
        raise ValueError("execution-sensitive skill changes require --approved-by <principal>")
    record = {"applied_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"), "proposal_path": proposal_path.relative_to(root).as_posix(), "type": type_, "target": target, "skill": skill, "approved_by": approved_by, "execution_sensitive": execution_sensitive}
    with (root / ".tradingcodex" / "mainagent" / "applied-skill-changes.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    write_audit_event(root, {"type": "skill_change.applied", "payload": record}, approved_by or "head-manager", "cli")
    print_json({"status": "applied", **record})


def path_check(root: Path, layer: str, name: str, rel: str, codex_native: bool) -> dict[str, Any]:
    ok = (root / rel).exists()
    return {"layer": layer, "name": name, "ok": ok, "codexNative": codex_native, "detail": "found" if ok else "missing"}


def text_check(root: Path, layer: str, name: str, rel: str, pattern: str, codex_native: bool) -> dict[str, Any]:
    ok = pattern in _safe_read(root / rel)
    return {"layer": layer, "name": name, "ok": ok, "codexNative": codex_native, "detail": f"contains {pattern}" if ok else f"missing {pattern}"}


def classify_artifact_path(rel: str) -> str:
    if rel.startswith("trading/research/"):
        return "evidence_pack"
    if "order_intent" in rel:
        return "order_intent"
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


def _parse_agent_list(args: list[str]) -> list[str]:
    return [item.strip() for arg in args for item in arg.split(",") if item.strip()]


def _toml_string(text: str, key: str) -> str | None:
    for line in text.splitlines():
        if line.startswith(f"{key} = "):
            return line.split('"')[1]
    return None


def _safe_read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return ""


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _regex(text: str, pattern: str, default: str) -> str:
    import re

    match = re.search(pattern, text, flags=re.M)
    return match.group(1) if match else default


def _yaml_value(text: str, key: str) -> str | None:
    for line in text.splitlines():
        if line.startswith(f"{key}:"):
            return line.split(":", 1)[1].strip()
    return None


def print_json(value: Any) -> None:
    print(json.dumps(value, indent=2, ensure_ascii=False))

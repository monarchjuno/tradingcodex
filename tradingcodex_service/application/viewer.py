from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

import yaml

from tradingcodex_service.application.agents import (
    SKILL_SPECS,
    build_projection_state,
    list_optional_role_skills,
    read_strategy_skill_records,
)
from tradingcodex_service.application.codex_capabilities import list_codex_capabilities
from tradingcodex_service.application.calculations import get_calculation_run, search_calculations
from tradingcodex_service.application.datasets import get_dataset_manifest, profile_dataset, search_datasets
from tradingcodex_service.application.brokers import list_broker_connections
from tradingcodex_service.application.common import now_iso
from tradingcodex_service.application.forecasting import calibration_report, list_forecasts
from tradingcodex_service.application.harness import list_recent_activity
from tradingcodex_service.application.investor_context import read_investor_context
from tradingcodex_service.application.markdown_preview import read_markdown_preview, render_markdown_preview
from tradingcodex_service.application.orders import list_order_tickets
from tradingcodex_service.application.portfolio import list_positions
from tradingcodex_service.application.research import get_research_artifact, list_research_artifacts
from tradingcodex_service.application.runtime import active_profile_for_workspace, workspace_context_payload
from tradingcodex_service.application.workspaces import workspace_options
from tradingcodex_service.log_safety import redact_log_text


def viewer_snapshot(root: Path | str) -> dict[str, Any]:
    """Return the read-only workspace state used by the local web viewer."""
    root = Path(root).expanduser().resolve()
    sections: dict[str, dict[str, Any]] = {
        "workspace": _section(
            lambda: {
                "context": workspace_context_payload(root),
                "profile": active_profile_for_workspace(root),
                "options": workspace_options(root),
            }
        ),
        "investor_context": _section(lambda: _investor_context_status(root)),
        "skills": _section(lambda: skill_catalog(root)),
        "agents": _section(lambda: _agent_catalog(root)),
        "activity": _section(lambda: _recent_activity(root)),
        "artifacts": _section(lambda: list_research_artifacts(root, {"limit": 100})["artifacts"]),
        "datasets": _section(lambda: search_datasets(root, {"limit": 100})["datasets"]),
        "calculations": _section(lambda: search_calculations(root, {"limit": 100})["calculations"]),
        "forecasts": _section(
            lambda: {
                "items": list_forecasts(root, {"limit": 100}),
                "calibration": calibration_report(root, {"minimum_sample": 20}),
            }
        ),
        "codex_capabilities": _section(lambda: list_codex_capabilities(root)),
        "strategies": _section(lambda: read_strategy_skill_records(root)),
        "optional_skills": _section(lambda: list_optional_role_skills(root, include_archived=True)),
        "portfolio": _section(lambda: list_positions(root)),
        "orders": _section(lambda: list_order_tickets(root, {"limit": 50})),
        "brokers": _section(lambda: list_broker_connections(root)),
    }
    return _json_safe({"generated_at": now_iso(), "sections": sections})


def skill_catalog(root: Path | str) -> list[dict[str, Any]]:
    root = Path(root).resolve()
    state = build_projection_state(root)
    records = []
    for skill_id, item in sorted(state.get("skills", {}).items()):
        if skill_id.startswith("investment-brain-"):
            continue
        path = _skill_path(root, skill_id, item)
        preview = read_markdown_preview(path, source_file=_display_path(root, path), source_label="skill")
        metadata = _read_yaml(path.parent / "agents" / "openai.yaml").get("interface", {})
        spec = SKILL_SPECS.get(skill_id)
        risk_tags = list(item.get("risk_tags") or (spec.risk_tags if spec else ()))
        source = str(item.get("source") or "core")
        status = str(item.get("status") or "active")
        validation_status = str(item.get("validation_status") or "valid")
        records.append(
            {
                "id": skill_id,
                "label": str(metadata.get("display_name") or item.get("label") or preview.heading or skill_id),
                "description": str(
                    metadata.get("short_description")
                    or preview.frontmatter.get("description")
                    or item.get("description")
                    or ""
                ),
                "default_prompt": str(metadata.get("default_prompt") or ""),
                "owner_roles": list(item.get("owner_roles") or (spec.owner_roles if spec else ())),
                "risk_tags": risk_tags,
                "scope": str(item.get("scope") or (spec.scope if spec else "mainagent")),
                "source": source,
                "status": status,
                "validation_status": validation_status,
                "installed": path.is_file(),
                "user_visible": bool(item.get("user_visible")),
                "route_through_head_manager": bool(not spec or spec.scope != "mainagent"),
                "available_in_codex": path.is_file() and status == "active" and validation_status == "valid",
            }
        )
    return records


def get_skill_detail(root: Path | str, skill_id: str) -> dict[str, Any]:
    record = next((item for item in skill_catalog(root) if item["id"] == skill_id), None)
    if record is None:
        raise ValueError(f"unknown skill: {skill_id}")
    root = Path(root).resolve()
    state = build_projection_state(root)
    path = _skill_path(root, skill_id, state["skills"][skill_id])
    preview = read_markdown_preview(path, source_file=_display_path(root, path), source_label="skill")
    return _json_safe(
        {**record, "preview": {"heading": preview.heading, "html": preview.html, "frontmatter": preview.frontmatter}}
    )


def get_artifact_detail(root: Path | str, artifact_id: str) -> dict[str, Any]:
    artifact = get_research_artifact(root, {"artifact_id": artifact_id, "include_markdown": True})
    markdown = str(artifact.pop("markdown", ""))
    preview = render_markdown_preview(
        markdown,
        source_file=str(artifact.get("path") or ""),
        source_label="research artifact",
    )
    return _json_safe({**artifact, "preview": {"heading": preview.heading, "html": preview.html}})


def get_dataset_detail(root: Path | str, dataset_id: str) -> dict[str, Any]:
    manifest = get_dataset_manifest(root, {"dataset_id": dataset_id})
    profile: dict[str, Any] | None = None
    profile_error = ""
    if manifest["payload_available"] and not manifest["withdrawn"]:
        try:
            profile = profile_dataset(root, {"dataset_id": dataset_id, "sample_rows": 20})
        except Exception as exc:
            profile_error = redact_log_text(str(exc))[:500]
    return _json_safe(
        {
            **manifest,
            "profile": profile,
            "profile_error": profile_error,
            "read_only": True,
        }
    )


def get_calculation_detail(root: Path | str, calculation_run_id: str) -> dict[str, Any]:
    return _json_safe(
        {
            **get_calculation_run(
                root,
                {"calculation_run_id": calculation_run_id},
            ),
            "read_only": True,
        }
    )


def _investor_context_status(root: Path) -> dict[str, Any]:
    try:
        value = read_investor_context(root)
    except ValueError:
        return {"configured": False, "enabled_by_default": False, "field_count": 0}
    fields = value.get("fields") if isinstance(value.get("fields"), dict) else {}
    return {
        "configured": True,
        "enabled_by_default": value.get("enabled_by_default", True) is not False,
        "field_count": len(fields),
        "updated_at": value.get("updated_at", ""),
        "content_hash": value.get("content_hash", ""),
    }


def _agent_catalog(root: Path) -> list[dict[str, Any]]:
    state = build_projection_state(root)
    return [
        {
            "role": role,
            "label": item.get("label") or role,
            "group": item.get("group") or "",
            "purpose": item.get("purpose") or "",
            "skills": item.get("effective_skills") or [],
            "validation_errors": item.get("validation_errors") or [],
        }
        for role, item in state.get("agents", {}).items()
    ]


def _recent_activity(root: Path) -> dict[str, Any]:
    items = list_recent_activity(root, limit=50)
    return {
        "items": items,
        "tool_names": list(dict.fromkeys(item["title"] for item in items if item.get("kind") == "MCP")),
    }


def _skill_path(root: Path, skill_id: str, item: dict[str, Any]) -> Path:
    raw = str(item.get("resolved_source_file") or "")
    if not raw:
        raise ValueError(f"projected skill has no source file: {skill_id}")
    candidate = Path(raw).expanduser()
    candidate = candidate if candidate.is_absolute() else root / candidate
    resolved_root = root.resolve(strict=False)
    resolved_candidate = candidate.resolve(strict=False)
    try:
        resolved_candidate.relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError(f"projected skill source escapes the workspace: {skill_id}") from exc
    if not resolved_candidate.is_file():
        raise ValueError(f"projected skill source is missing: {skill_id}")
    return resolved_candidate


def _display_path(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


def _read_yaml(path: Path) -> dict[str, Any]:
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"skill metadata is unavailable: {path}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"skill metadata must be an object: {path}")
    return value


def _section(loader: Callable[[], Any]) -> dict[str, Any]:
    try:
        return {"ok": True, "data": _json_safe(loader())}
    except Exception as exc:
        return {
            "ok": False,
            "error": {"code": type(exc).__name__, "message": redact_log_text(str(exc))[:500]},
        }


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (Path, Decimal)):
        return str(value)
    return value

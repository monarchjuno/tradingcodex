from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tradingcodex_service.application.common import safe_workspace_path, sanitize_id
from tradingcodex_service.application.harness import build_subagent_starter_prompt, build_workflow_intake_summary
from tradingcodex_service.application.markdown_preview import split_markdown_frontmatter
from tradingcodex_service.application.runtime import ensure_runtime_database, workspace_context_payload
from tradingcodex_service.application.workflow_planner import build_deterministic_workflow_plan

DECISION_ROOT = Path("trading/decisions")
WORKFLOW_RUN_ROOT = Path("trading/workflows/runs")


def build_workflow_plan(workspace_root: Path | str, prompt: str) -> dict[str, Any]:
    if not prompt.strip():
        raise ValueError("prompt is required")
    summary = build_workflow_intake_summary(prompt, workspace_root)
    staged_plan = build_deterministic_workflow_plan(workspace_root, prompt)
    return {
        "lane": summary["workflow_lane"],
        "universe": summary["investment_universe"],
        "universe_label": summary["investment_universe_label"],
        "selected_roles": [item["role"] for item in summary.get("subagents") or []],
        "staged_plan": staged_plan,
        "dynamic_plan_required": True,
        "missing_profile": summary.get("investor_profile_inputs") or [],
        "blocked_actions": summary.get("blocked_actions") or [],
        "routing_flags": summary.get("routing_flags") or {},
        "allowed_next_actions": summary.get("next_allowed_actions") or [],
        "starter_prompt": build_subagent_starter_prompt(prompt, workspace_root),
        "intake_summary": summary,
        "workspace_native": True,
        "workspace_context": workspace_context_payload(workspace_root),
    }


def create_decision_package(workspace_root: Path | str, prompt: str) -> dict[str, Any]:
    root = Path(workspace_root).expanduser().resolve()
    plan = build_workflow_plan(root, prompt)
    suffix = _decision_suffix(prompt)
    run_id = f"workflow-{suffix}"
    decision_id = f"decision-{suffix}"
    package_rel = DECISION_ROOT / f"{decision_id}.md"
    run_rel = WORKFLOW_RUN_ROOT / f"{run_id}.json"
    metadata = _run_metadata(run_id, decision_id, prompt, plan, package_rel)

    run_path = safe_workspace_path(root, run_rel, allowed_roots=(WORKFLOW_RUN_ROOT,))
    run_path.parent.mkdir(parents=True, exist_ok=True)
    run_path.write_text(json.dumps(metadata, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")

    package_path = safe_workspace_path(root, package_rel, allowed_roots=(DECISION_ROOT,))
    package_path.parent.mkdir(parents=True, exist_ok=True)
    package_path.write_text(_decision_markdown(metadata, plan), encoding="utf-8")
    _store_workflow_run(root, metadata)

    return {
        "status": "planned",
        "run_id": run_id,
        "decision_id": decision_id,
        "workflow_run_path": run_rel.as_posix(),
        "decision_package_path": package_rel.as_posix(),
        "plan": plan,
        "workspace_native": True,
        "workspace_context": plan["workspace_context"],
    }


def list_decision_packages(workspace_root: Path | str, limit: int = 50) -> dict[str, Any]:
    root = Path(workspace_root).expanduser().resolve()
    packages = [_decision_payload(root, path) for path in sorted((root / DECISION_ROOT).glob("*.md"))]
    packages.sort(key=lambda item: item["updated_at"], reverse=True)
    return {
        "packages": packages[: max(1, min(int(limit), 200))],
        "workspace_native": True,
        "workspace_context": workspace_context_payload(root),
    }


def get_decision_package(workspace_root: Path | str, decision_id: str) -> dict[str, Any]:
    root = Path(workspace_root).expanduser().resolve()
    if not decision_id:
        raise ValueError("decision_id is required")
    for path in sorted((root / DECISION_ROOT).glob("*.md")):
        payload = _decision_payload(root, path, include_markdown=True)
        if decision_id in {payload["decision_id"], payload["path"]}:
            return payload
    raise ValueError(f"decision package not found: {decision_id}")


def export_decision_package(workspace_root: Path | str, decision_id: str, export_path: str | None = None) -> dict[str, Any]:
    root = Path(workspace_root).expanduser().resolve()
    package = get_decision_package(root, decision_id)
    source = safe_workspace_path(root, package["path"], allowed_roots=(DECISION_ROOT,))
    target_rel = export_path or package["path"]
    target = safe_workspace_path(root, target_rel, allowed_roots=(DECISION_ROOT,))
    if target.resolve() != source.resolve():
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    return {
        "status": "exported",
        "decision_id": package["decision_id"],
        "export_path": target.relative_to(root).as_posix(),
        "workspace_native": True,
        "workspace_context": workspace_context_payload(root),
    }


def _run_metadata(run_id: str, decision_id: str, prompt: str, plan: dict[str, Any], package_rel: Path) -> dict[str, Any]:
    summary = plan["intake_summary"]
    return {
        "schema_version": 1,
        "run_id": run_id,
        "decision_id": decision_id,
        "status": "planned",
        "handoff_state": "waiting",
        "readiness_label": "waiting",
        "original_prompt": prompt,
        "interpreted_question": (summary.get("idea_translation") or {}).get("plain_english") or summary.get("primary_question") or "",
        "workflow_lane": plan["lane"],
        "workflow_label": summary.get("label") or plan["lane"],
        "universe": plan["universe"],
        "selected_roles": plan["selected_roles"],
        "missing_profile": plan["missing_profile"],
        "missing_evidence": ["accepted role artifacts"],
        "artifact_paths": [],
        "source_as_of": "pending accepted artifacts",
        "blocked_actions": plan["blocked_actions"],
        "routing_flags": plan.get("routing_flags") or {},
        "allowed_next_actions": plan["allowed_next_actions"],
        "order_gate_status": "blocked" if any(action in plan["blocked_actions"] for action in ("order ticket", "approval", "execution")) else "waiting",
        "decision_package_path": package_rel.as_posix(),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "workspace_context": plan["workspace_context"],
    }


def _decision_markdown(metadata: dict[str, Any], plan: dict[str, Any]) -> str:
    frontmatter = {
        "decision_id": metadata["decision_id"],
        "workflow_run_id": metadata["run_id"],
        "artifact_type": "decision_package",
        "workflow_lane": metadata["workflow_lane"],
        "workflow_label": metadata["workflow_label"],
        "universe": metadata["universe"],
        "status": metadata["status"],
        "handoff_state": metadata["handoff_state"],
        "readiness_label": metadata["readiness_label"],
        "created_by": "head-manager",
        "blocked_actions": metadata["blocked_actions"],
        "missing_evidence": metadata["missing_evidence"],
        "decision_quality_required": bool(metadata.get("routing_flags", {}).get("decision_quality_required")),
        "forecast_contract_required": bool(metadata.get("routing_flags", {}).get("forecast_contract_required")),
        "anti_overfit_required": bool(metadata.get("routing_flags", {}).get("anti_overfit_required")),
    }
    next_actions = "\n".join(f"- {item['label']}: {item['detail']}" for item in metadata["allowed_next_actions"]) or "- None yet."
    roles = ", ".join(metadata["selected_roles"]) or "head-manager"
    blocked = ", ".join(metadata["blocked_actions"]) or "none"
    profile = "\n".join(f"- {item}" for item in metadata["missing_profile"]) or "- No required profile gaps for this lane."
    stages = "\n".join(f"- {stage['label']}: {stage['summary']}" for stage in plan["intake_summary"].get("workflow_stages") or [])
    body = f"""# Decision Package: {metadata['decision_id']}

## Overview

- Original prompt: {metadata['original_prompt']}
- Interpreted question: {metadata['interpreted_question']}
- Workflow lane: {metadata['workflow_lane']}
- Workflow label: {metadata['workflow_label']}
- Universe: {metadata['universe']}
- Selected roles: {roles}
- Handoff state: {metadata['handoff_state']}
- Readiness label: {metadata['readiness_label']}

## Evidence

- Source/as-of posture: {metadata['source_as_of']}
- Artifact paths: waiting for accepted role artifacts
- Missing evidence: accepted role artifacts

## Profile Gaps

{profile}

## Portfolio And Risk

- Portfolio/risk status: waiting for selected Codex role artifacts.
- Order gate status: {metadata['order_gate_status']}
- Blocked actions: {blocked}

## Next Allowed Actions

{next_actions}

## Workflow Stages

{stages}

## Codex Starter Prompt

```text
{plan['starter_prompt']}
```
"""
    header = "---\n" + "\n".join(f"{key}: {json.dumps(value, ensure_ascii=False)}" for key, value in frontmatter.items()) + "\n---\n\n"
    return header + body.rstrip() + "\n"


def _decision_suffix(prompt: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
    digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:8]
    return sanitize_id(f"{stamp}-{digest}")


def _store_workflow_run(root: Path, metadata: dict[str, Any]) -> None:
    try:
        ensure_runtime_database(root)
        from apps.workflows.models import WorkflowRun

        WorkflowRun.objects.update_or_create(
            run_id=metadata["run_id"],
            defaults={
                "lane": metadata["workflow_lane"],
                "universe": metadata["universe"],
                "readiness_label": metadata["readiness_label"],
                "status": metadata["status"],
                "original_request": metadata["original_prompt"],
                "workspace_context": metadata["workspace_context"],
            },
        )
    except Exception:
        pass


def _decision_payload(root: Path, path: Path, *, include_markdown: bool = False) -> dict[str, Any]:
    rel = path.relative_to(root).as_posix()
    text = path.read_text(encoding="utf-8")
    document = split_markdown_frontmatter(text)
    frontmatter = document.frontmatter
    payload = {
        "decision_id": str(frontmatter.get("decision_id") or path.stem),
        "workflow_run_id": str(frontmatter.get("workflow_run_id") or ""),
        "path": rel,
        "title": document.heading or path.stem,
        "workflow_lane": str(frontmatter.get("workflow_lane") or ""),
        "workflow_label": str(frontmatter.get("workflow_label") or frontmatter.get("workflow_lane") or ""),
        "universe": str(frontmatter.get("universe") or ""),
        "status": str(frontmatter.get("status") or ""),
        "handoff_state": str(frontmatter.get("handoff_state") or ""),
        "readiness_label": str(frontmatter.get("readiness_label") or ""),
        "blocked_actions": frontmatter.get("blocked_actions") if isinstance(frontmatter.get("blocked_actions"), list) else [],
        "missing_evidence": frontmatter.get("missing_evidence") if isinstance(frontmatter.get("missing_evidence"), list) else [],
        "updated_at": datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc),
        "workspace_native": True,
    }
    if include_markdown:
        payload["markdown"] = document.body
        payload["frontmatter"] = frontmatter
    return payload

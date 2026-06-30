from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tradingcodex_service.application.common import safe_workspace_path, sanitize_id
from tradingcodex_service.application.markdown_preview import split_markdown_frontmatter
from tradingcodex_service.application.runtime import workspace_context_payload

RESEARCH_FILE_ROOTS = (Path("trading/research"), Path("trading/reports"))
SOURCE_SNAPSHOT_ROOT = Path("trading/research/source-snapshots")
SOURCE_SNAPSHOT_ROOTS = (SOURCE_SNAPSHOT_ROOT,)


def list_workflow_artifacts(workspace_root: Path | str) -> dict[str, Any]:
    root = Path(workspace_root)
    files = []
    for prefix in ["trading/research", "trading/reports", "trading/orders", "trading/approvals"]:
        base = root / prefix
        if base.exists():
            files.extend(str(path.relative_to(root)) for path in base.rglob("*") if path.is_file() and path.name != ".gitkeep")
    return {
        "artifacts": sorted(files),
        "research_artifacts": list_research_artifacts(root, {"include_markdown": False}).get("artifacts", []),
        "workspace_native": True,
        "workspace_context": workspace_context_payload(root),
    }


def create_research_artifact(workspace_root: Path | str, args: dict[str, Any]) -> dict[str, Any]:
    root = Path(workspace_root)
    markdown = args.get("markdown")
    markdown_path = args.get("markdown_path") or args.get("markdown_file")
    if not markdown and markdown_path:
        markdown = safe_workspace_path(root, markdown_path, allowed_roots=RESEARCH_FILE_ROOTS).read_text(encoding="utf-8")
    if not markdown:
        raise ValueError("research artifact markdown is required")

    source_document = split_markdown_frontmatter(str(markdown))
    source_frontmatter = source_document.frontmatter
    markdown_body = source_document.body or str(markdown)
    artifact_type = str(args.get("artifact_type") or args.get("type") or source_frontmatter.get("artifact_type") or source_frontmatter.get("type") or "research_memo")
    title = str(args.get("title") or source_frontmatter.get("title") or source_document.heading or args.get("artifact_id") or "Untitled research artifact")
    symbol = str(args.get("symbol") or source_frontmatter.get("symbol") or "").upper()
    content_hash = hashlib.sha256(markdown_body.encode("utf-8")).hexdigest()
    artifact_id = str(args.get("artifact_id") or source_frontmatter.get("artifact_id") or f"{sanitize_id(artifact_type)}-{sanitize_id(symbol or title)}-{content_hash[:12]}")
    metadata = args.get("metadata") if isinstance(args.get("metadata"), dict) else {}
    if args.get("role") and not metadata.get("role"):
        metadata = {**metadata, "role": args.get("role")}
    created_by = str(args.get("created_by") or args.get("principal_id") or source_frontmatter.get("created_by") or "system")
    existing = find_workspace_research_artifact(root, artifact_id)
    if existing and existing.get("content_hash") != content_hash and not args.get("_append_version"):
        raise ValueError("research artifact already exists in this workspace; use append_research_artifact_version to create a new version")

    existing_version = _int_value(existing.get("version") if existing else None, default=0)
    version = existing_version + 1 if args.get("_append_version") else existing_version or 1
    export_path = str(args.get("export_path") or (existing.get("path") if existing else "") or default_research_export_path_from_values(artifact_id, artifact_type, metadata))
    frontmatter = {
        **source_frontmatter,
        "artifact_id": artifact_id,
        "artifact_type": artifact_type,
        "universe": args.get("universe") or source_frontmatter.get("universe") or "public_equity",
        "workflow_type": args.get("workflow_type") or source_frontmatter.get("workflow_type") or "",
        "role": args.get("role") or metadata.get("role") or source_frontmatter.get("role") or _role_alias_from_actor(created_by),
        "symbol": symbol,
        "title": title,
        "source_as_of": args.get("source_as_of") or source_frontmatter.get("source_as_of") or "",
        "readiness_label": args.get("readiness_label") or source_frontmatter.get("readiness_label") or "",
        "context_summary": _frontmatter_value(args, metadata, source_frontmatter, "context_summary", ""),
        "reader_summary": _frontmatter_value(args, metadata, source_frontmatter, "reader_summary", ""),
        "handoff_state": _frontmatter_value(args, metadata, source_frontmatter, "handoff_state", ""),
        "confidence": _frontmatter_value(args, metadata, source_frontmatter, "confidence", ""),
        "missing_evidence": _frontmatter_list(args, metadata, source_frontmatter, "missing_evidence"),
        "next_recipient": _frontmatter_value(args, metadata, source_frontmatter, "next_recipient", ""),
        "next_action": _frontmatter_value(args, metadata, source_frontmatter, "next_action", ""),
        "blocked_actions": _frontmatter_list(args, metadata, source_frontmatter, "blocked_actions"),
        "source_snapshot_ids": _frontmatter_list(args, metadata, source_frontmatter, "source_snapshot_ids"),
        "follow_up_requests": _frontmatter_list(args, metadata, source_frontmatter, "follow_up_requests"),
        "version": version,
        "content_hash": content_hash,
        "workspace_native": True,
        "created_by": created_by,
    }
    path = safe_workspace_path(root, export_path, allowed_roots=RESEARCH_FILE_ROOTS)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_render_research_markdown(frontmatter, markdown_body), encoding="utf-8")

    result = {
        "status": "updated" if existing else "stored",
        "db_canonical": False,
        "file_sot": True,
        "workspace_native": True,
        "artifact_id": artifact_id,
        "version": version,
        "content_hash": content_hash,
        "export_path": path.relative_to(root).as_posix(),
        "workspace_context": workspace_context_payload(root),
    }
    return result


def append_research_artifact_version(workspace_root: Path | str, args: dict[str, Any]) -> dict[str, Any]:
    if not args.get("artifact_id"):
        raise ValueError("artifact_id is required")
    current = get_research_artifact(workspace_root, {"artifact_id": args["artifact_id"]})
    payload = {
        **current,
        **args,
        "_append_version": True,
        "metadata": args.get("metadata") or current.get("metadata") or {},
        "export_path": args.get("export_path") or current.get("path") or current.get("export_path"),
    }
    if args.get("markdown"):
        payload["markdown"] = args["markdown"]
    elif args.get("markdown_path") or args.get("markdown_file"):
        payload.pop("markdown", None)
    else:
        payload["markdown"] = current.get("markdown")
    return create_research_artifact(workspace_root, payload)


def get_research_artifact(workspace_root: Path | str, args: dict[str, Any]) -> dict[str, Any]:
    artifact_id = args.get("artifact_id") or args.get("id")
    if not artifact_id:
        raise ValueError("artifact_id is required")
    artifact = find_workspace_research_artifact(Path(workspace_root), str(artifact_id))
    if not artifact:
        raise ValueError(f"research artifact not found in workspace: {artifact_id}")
    if args.get("include_markdown", True) is not False:
        artifact["markdown"] = _read_research_markdown_body(Path(workspace_root) / artifact["path"])
    return artifact


def list_research_artifacts(workspace_root: Path | str, args: dict[str, Any] | None = None) -> dict[str, Any]:
    args = args or {}
    artifacts = list_workspace_research_artifacts(Path(workspace_root), include_markdown=args.get("include_markdown") is True)
    for field in ["artifact_type", "universe", "workflow_type", "symbol", "readiness_label", "handoff_state", "created_by"]:
        value = args.get(field)
        if value:
            artifacts = [artifact for artifact in artifacts if str(artifact.get(field) or "").lower() == str(value).lower()]
    limit = max(1, min(int(args.get("limit") or 50), 200))
    return {
        "db_canonical": False,
        "file_sot": True,
        "workspace_native": True,
        "workspace_context": workspace_context_payload(workspace_root),
        "artifacts": artifacts[:limit],
    }


def search_research_artifacts(workspace_root: Path | str, args: dict[str, Any]) -> dict[str, Any]:
    query = str(args.get("query") or args.get("q") or "").strip()
    if not query:
        raise ValueError("query is required")
    artifacts = list_workspace_research_artifacts(Path(workspace_root), include_markdown=True)
    query_lower = query.lower()
    artifacts = [
        artifact
        for artifact in artifacts
        if query_lower in str(artifact.get("title") or "").lower()
        or query_lower in str(artifact.get("symbol") or "").lower()
        or query_lower in str(artifact.get("markdown") or "").lower()
    ]
    for field in ["universe", "artifact_type"]:
        if args.get(field):
            artifacts = [artifact for artifact in artifacts if str(artifact.get(field) or "").lower() == str(args[field]).lower()]
    limit = max(1, min(int(args.get("limit") or 20), 100))
    for artifact in artifacts:
        artifact.pop("markdown", None)
    return {
        "query": query,
        "db_canonical": False,
        "file_sot": True,
        "workspace_native": True,
        "workspace_context": workspace_context_payload(workspace_root),
        "artifacts": artifacts[:limit],
    }


def export_research_artifact_md(workspace_root: Path | str, args: dict[str, Any]) -> dict[str, Any]:
    root = Path(workspace_root)
    artifact_id = args.get("artifact_id") or args.get("id")
    if not artifact_id:
        raise ValueError("artifact_id is required")
    artifact = get_research_artifact(root, {"artifact_id": artifact_id, "include_markdown": True})
    target_rel = str(args.get("export_path") or artifact["path"])
    target = safe_workspace_path(root, target_rel, allowed_roots=RESEARCH_FILE_ROOTS)
    source = safe_workspace_path(root, artifact["path"], allowed_roots=RESEARCH_FILE_ROOTS)
    if target.resolve() != source.resolve():
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    return {
        "status": "exported",
        "artifact_id": artifact["artifact_id"],
        "export_path": target.relative_to(root).as_posix(),
        "db_canonical": False,
        "file_sot": True,
        "workspace_native": True,
        "workspace_context": workspace_context_payload(root),
    }


def record_source_snapshot(workspace_root: Path | str, args: dict[str, Any]) -> dict[str, Any]:
    root = Path(workspace_root)
    recorded_at = datetime.now(timezone.utc).isoformat()
    payload = {
        "provider": args.get("provider") or "unknown",
        "source_category": args.get("source_category") or args.get("category") or "unknown",
        "as_of": args.get("as_of") or "",
        "artifact_id": args.get("artifact_id") or "",
        "warnings": args.get("warnings") if isinstance(args.get("warnings"), list) else [],
        "payload": args.get("payload") if isinstance(args.get("payload"), dict) else {},
        "created_by": args.get("principal_id") or args.get("created_by") or "system",
        "recorded_at": recorded_at,
        "workspace_native": True,
    }
    snapshot_id = _source_snapshot_id(payload)
    rel_path = SOURCE_SNAPSHOT_ROOT / f"{snapshot_id}.json"
    path = safe_workspace_path(root, rel_path, allowed_roots=SOURCE_SNAPSHOT_ROOTS)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({**payload, "snapshot_id": snapshot_id}, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    result = {
        "status": "recorded",
        "snapshot_id": snapshot_id,
        "artifact_id": payload["artifact_id"],
        "provider": payload["provider"],
        "source_category": payload["source_category"],
        "export_path": path.relative_to(root).as_posix(),
        "db_canonical": False,
        "file_sot": True,
        "workspace_native": True,
        "workspace_context": workspace_context_payload(root),
    }
    return result


def list_workspace_research_artifacts(root: Path, *, include_markdown: bool = False) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for rel_root in RESEARCH_FILE_ROOTS:
        base = root / rel_root
        if not base.exists():
            continue
        for path in sorted(base.rglob("*.md")):
            if path.name == ".gitkeep":
                continue
            try:
                safe_path = safe_workspace_path(root, path.relative_to(root), allowed_roots=RESEARCH_FILE_ROOTS)
            except ValueError:
                continue
            records.append(_research_file_payload(root, safe_path, include_markdown=include_markdown))
    return sorted(records, key=lambda item: item["updated_at"], reverse=True)


def find_workspace_research_artifact(root: Path, artifact_id: str) -> dict[str, Any] | None:
    if "/" in artifact_id or "\\" in artifact_id or artifact_id.endswith(".md"):
        try:
            direct = safe_workspace_path(root, artifact_id, allowed_roots=RESEARCH_FILE_ROOTS)
        except ValueError:
            direct = None
        if direct and direct.exists() and direct.is_file():
            return _research_file_payload(root, direct, include_markdown=False)
    for artifact in list_workspace_research_artifacts(root, include_markdown=False):
        if artifact["artifact_id"] == artifact_id or artifact["path"] == artifact_id:
            return artifact
    return None


def _research_file_payload(root: Path, path: Path, *, include_markdown: bool = False) -> dict[str, Any]:
    rel = path.relative_to(root).as_posix()
    frontmatter, heading, body = _research_file_parts(path)
    content_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
    artifact_id = str(frontmatter.get("artifact_id") or rel)
    payload = {
        "artifact_id": artifact_id,
        "path": rel,
        "export_path": rel,
        "artifact_type": str(frontmatter.get("artifact_type") or _infer_research_artifact_type(path)),
        "universe": str(frontmatter.get("universe") or _infer_research_universe(path)),
        "workflow_type": str(frontmatter.get("workflow_type") or ""),
        "role": str(frontmatter.get("role") or ""),
        "symbol": str(frontmatter.get("symbol") or ""),
        "title": str(frontmatter.get("title") or heading or path.stem.replace("-", " ").title()),
        "metadata": {},
        "workspace_context": workspace_context_payload(root),
        "source_as_of": str(frontmatter.get("source_as_of") or ""),
        "readiness_label": str(frontmatter.get("readiness_label") or frontmatter.get("handoff_state") or "workspace-file"),
        "context_summary": str(frontmatter.get("context_summary") or ""),
        "reader_summary": str(frontmatter.get("reader_summary") or ""),
        "handoff_state": str(frontmatter.get("handoff_state") or ""),
        "confidence": str(frontmatter.get("confidence") or ""),
        "missing_evidence": _coerce_list(frontmatter.get("missing_evidence")),
        "next_recipient": str(frontmatter.get("next_recipient") or ""),
        "next_action": str(frontmatter.get("next_action") or ""),
        "blocked_actions": _coerce_list(frontmatter.get("blocked_actions")),
        "source_snapshot_ids": _coerce_list(frontmatter.get("source_snapshot_ids")),
        "follow_up_requests": _coerce_list(frontmatter.get("follow_up_requests")),
        "created_by": str(frontmatter.get("created_by") or "workspace"),
        "content_hash": str(frontmatter.get("content_hash") or content_hash),
        "version": _int_value(frontmatter.get("version"), default=1),
        "parent_artifact_id": str(frontmatter.get("parent_artifact_id") or ""),
        "updated_at": datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc),
        "created_at": datetime.fromtimestamp(path.stat().st_ctime, tz=timezone.utc),
        "db_canonical": False,
        "file_sot": True,
        "workspace_native": True,
    }
    if include_markdown:
        payload["markdown"] = body
    return payload


def _research_file_parts(path: Path) -> tuple[dict[str, Any], str, str]:
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return {}, "", ""
    document = split_markdown_frontmatter(text)
    return document.frontmatter, document.heading, document.body


def _read_research_markdown_body(path: Path) -> str:
    return _research_file_parts(path)[2]


def _render_research_markdown(frontmatter: dict[str, Any], markdown: str) -> str:
    header = "---\n" + "\n".join(f"{key}: {json.dumps(value, ensure_ascii=False)}" for key, value in frontmatter.items()) + "\n---\n\n"
    return header + markdown.rstrip() + "\n"


def _frontmatter_value(args: dict[str, Any], metadata: dict[str, Any], source_frontmatter: dict[str, Any], field: str, default: Any) -> Any:
    for container in (args, metadata, source_frontmatter):
        value = container.get(field)
        if value not in (None, ""):
            return value
    return default


def _frontmatter_list(args: dict[str, Any], metadata: dict[str, Any], source_frontmatter: dict[str, Any], field: str) -> list[Any]:
    return _coerce_list(_frontmatter_value(args, metadata, source_frontmatter, field, []))


def _coerce_list(value: Any) -> list[Any]:
    if value in (None, ""):
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except Exception:
            return [item.strip() for item in value.replace(";", ",").split(",") if item.strip()]
        if isinstance(parsed, list):
            return parsed
        if parsed in (None, ""):
            return []
        return [parsed]
    return [value]


def _int_value(value: Any, *, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _infer_research_universe(path: Path) -> str:
    text = path.as_posix().lower()
    if "crypto" in text:
        return "public_crypto"
    if "macro" in text:
        return "macro"
    return "workspace"


def _infer_research_artifact_type(path: Path) -> str:
    parts = path.as_posix().split("/")
    if "reports" in parts:
        index = parts.index("reports")
        if len(parts) > index + 1:
            return f"{parts[index + 1]}_report"
        return "role_report"
    if path.name.endswith(".evidence.md"):
        return "evidence_pack"
    return "research_handoff"


def _role_alias_from_actor(actor: str) -> str:
    return actor.replace("-analyst", "").replace("-manager", "").replace("-operator", "")


def _source_snapshot_id(payload: dict[str, Any]) -> str:
    base = "-".join(
        filter(
            None,
            [
                sanitize_id(str(payload.get("provider") or "unknown")),
                sanitize_id(str(payload.get("source_category") or "unknown")),
                sanitize_id(str(payload.get("artifact_id") or "")),
            ],
        )
    )
    digest = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:12]
    return f"{base or 'source-snapshot'}-{digest}"


def default_research_export_path_from_values(artifact_id: str, artifact_type: str, metadata: dict[str, Any]) -> str:
    stem = sanitize_id(artifact_id)
    role = metadata.get("role") if isinstance(metadata, dict) else ""
    if artifact_type == "evidence_pack":
        return f"trading/research/{stem}.evidence.md"
    if role in {"fundamental", "technical", "news", "macro", "instrument", "valuation", "portfolio", "risk", "policy"}:
        return f"trading/reports/{role}/{stem}.md"
    return f"trading/research/{stem}.md"

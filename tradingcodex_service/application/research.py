from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from tradingcodex_service.application.audit import write_audit_event
from tradingcodex_service.application.common import _resolve_path, sanitize_id
from tradingcodex_service.application.runtime import (
    ensure_runtime_database,
    persist_workspace_context_if_available,
    workspace_context_payload,
)

def list_workflow_artifacts(workspace_root: Path | str) -> dict[str, Any]:
    root = Path(workspace_root)
    files = []
    for prefix in ["trading/research", "trading/reports", "trading/orders", "trading/approvals"]:
        base = root / prefix
        if base.exists():
            files.extend(str(path.relative_to(root)) for path in base.rglob("*") if path.is_file() and path.name != ".gitkeep")
    return {"artifacts": sorted(files), "db_artifacts": list_research_artifacts(root, {"include_markdown": False}).get("artifacts", []), "db_canonical": True, "workspace_context": workspace_context_payload(root)}


def create_research_artifact(workspace_root: Path | str, args: dict[str, Any]) -> dict[str, Any]:
    root = Path(workspace_root)
    markdown = args.get("markdown")
    markdown_path = args.get("markdown_path") or args.get("markdown_file")
    if not markdown and markdown_path:
        markdown = _resolve_path(root, markdown_path).read_text(encoding="utf-8")
    if not markdown:
        raise ValueError("research artifact markdown is required")

    artifact_type = args.get("artifact_type") or args.get("type") or "research_memo"
    title = args.get("title") or args.get("artifact_id") or "Untitled research artifact"
    symbol = str(args.get("symbol") or "").upper()
    content_hash = hashlib.sha256(str(markdown).encode("utf-8")).hexdigest()
    artifact_id = args.get("artifact_id") or f"{sanitize_id(artifact_type)}-{sanitize_id(symbol or title)}-{content_hash[:12]}"
    metadata = args.get("metadata") if isinstance(args.get("metadata"), dict) else {}
    created_by = args.get("created_by") or args.get("principal_id") or "system"

    ensure_runtime_database(root)
    workspace_context = persist_workspace_context_if_available(root)
    from django.db import transaction
    from apps.research.models import ResearchArtifact, ResearchArtifactVersion

    with transaction.atomic():
        existing = ResearchArtifact.objects.filter(artifact_id=artifact_id).first()
        version = (existing.version + 1) if existing else 1
        artifact, created = ResearchArtifact.objects.update_or_create(
            artifact_id=artifact_id,
            defaults={
                "artifact_type": artifact_type,
                "universe": args.get("universe") or "public_equity",
                "workflow_type": args.get("workflow_type") or "",
                "symbol": symbol,
                "title": title,
                "markdown": markdown,
                "metadata": metadata,
                "workspace_context": workspace_context,
                "source_as_of": args.get("source_as_of") or "",
                "readiness_label": args.get("readiness_label") or "",
                "created_by": created_by,
                "content_hash": content_hash,
                "version": version,
                "parent_artifact_id": args.get("parent_artifact_id") or "",
            },
        )
        ResearchArtifactVersion.objects.create(
            artifact=artifact,
            version=version,
            markdown=markdown,
            metadata=metadata,
            workspace_context=workspace_context,
            content_hash=content_hash,
            created_by=created_by,
        )

    export_path = ""
    if args.get("export", True) is not False:
        export = export_research_artifact_md(root, {"artifact_id": artifact_id, "export_path": args.get("export_path")})
        export_path = export.get("export_path", "")
    result = {
        "status": "stored" if created else "updated",
        "db_canonical": True,
        "artifact_id": artifact_id,
        "version": version,
        "content_hash": content_hash,
        "export_path": export_path,
        "workspace_context": workspace_context,
    }
    write_audit_event(root, {"type": "research_artifact.saved", "payload": result}, principal_id=created_by, source="service")
    return result


def append_research_artifact_version(workspace_root: Path | str, args: dict[str, Any]) -> dict[str, Any]:
    if not args.get("artifact_id"):
        raise ValueError("artifact_id is required")
    current = get_research_artifact(workspace_root, {"artifact_id": args["artifact_id"]})
    return create_research_artifact(workspace_root, {
        **current,
        **args,
        "markdown": args.get("markdown") or current.get("markdown"),
        "metadata": args.get("metadata") or current.get("metadata") or {},
    })


def get_research_artifact(workspace_root: Path | str, args: dict[str, Any]) -> dict[str, Any]:
    artifact_id = args.get("artifact_id") or args.get("id")
    if not artifact_id:
        raise ValueError("artifact_id is required")
    ensure_runtime_database(workspace_root)
    from apps.research.models import ResearchArtifact

    artifact = ResearchArtifact.objects.get(artifact_id=artifact_id)
    return research_artifact_to_dict(artifact, include_markdown=args.get("include_markdown", True) is not False)


def list_research_artifacts(workspace_root: Path | str, args: dict[str, Any] | None = None) -> dict[str, Any]:
    args = args or {}
    try:
        ensure_runtime_database(workspace_root)
        from apps.research.models import ResearchArtifact

        queryset = ResearchArtifact.objects.all()
        for field in ["artifact_type", "universe", "workflow_type", "symbol", "readiness_label", "created_by"]:
            value = args.get(field)
            if value:
                queryset = queryset.filter(**{field: str(value).upper() if field == "symbol" else value})
        limit = max(1, min(int(args.get("limit") or 50), 200))
        return {"db_canonical": True, "workspace_context": workspace_context_payload(workspace_root), "artifacts": [research_artifact_to_dict(artifact, include_markdown=args.get("include_markdown") is True) for artifact in queryset[:limit]]}
    except Exception as exc:
        return {"db_canonical": False, "artifacts": [], "error": str(exc)}


def search_research_artifacts(workspace_root: Path | str, args: dict[str, Any]) -> dict[str, Any]:
    query = str(args.get("query") or args.get("q") or "").strip()
    if not query:
        raise ValueError("query is required")
    ensure_runtime_database(workspace_root)
    from django.db.models import Q
    from apps.research.models import ResearchArtifact

    queryset = ResearchArtifact.objects.filter(Q(title__icontains=query) | Q(markdown__icontains=query) | Q(symbol__icontains=query))
    if args.get("universe"):
        queryset = queryset.filter(universe=args["universe"])
    if args.get("artifact_type"):
        queryset = queryset.filter(artifact_type=args["artifact_type"])
    limit = max(1, min(int(args.get("limit") or 20), 100))
    return {"query": query, "db_canonical": True, "workspace_context": workspace_context_payload(workspace_root), "artifacts": [research_artifact_to_dict(artifact, include_markdown=False) for artifact in queryset[:limit]]}


def export_research_artifact_md(workspace_root: Path | str, args: dict[str, Any]) -> dict[str, Any]:
    root = Path(workspace_root)
    artifact_id = args.get("artifact_id") or args.get("id")
    if not artifact_id:
        raise ValueError("artifact_id is required")
    ensure_runtime_database(root)
    from apps.research.models import ResearchArtifact

    artifact = ResearchArtifact.objects.get(artifact_id=artifact_id)
    rel = args.get("export_path") or artifact.export_path or default_research_export_path(artifact)
    path = _resolve_path(root, rel)
    frontmatter = {
        "artifact_id": artifact.artifact_id,
        "artifact_type": artifact.artifact_type,
        "universe": artifact.universe,
        "workflow_type": artifact.workflow_type,
        "symbol": artifact.symbol,
        "readiness_label": artifact.readiness_label,
        "version": artifact.version,
        "content_hash": artifact.content_hash,
        "db_canonical": True,
    }
    body = "---\n" + "\n".join(f"{key}: {json.dumps(value, ensure_ascii=False)}" for key, value in frontmatter.items()) + "\n---\n\n" + artifact.markdown.rstrip() + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    if artifact.export_path != path.relative_to(root).as_posix():
        artifact.export_path = path.relative_to(root).as_posix()
        artifact.save(update_fields=["export_path", "updated_at"])
    return {"status": "exported", "artifact_id": artifact.artifact_id, "export_path": path.relative_to(root).as_posix(), "db_canonical": True, "workspace_context": workspace_context_payload(root)}


def record_source_snapshot(workspace_root: Path | str, args: dict[str, Any]) -> dict[str, Any]:
    ensure_runtime_database(workspace_root)
    from apps.research.models import SourceSnapshot

    snapshot = SourceSnapshot.objects.create(
        provider=args.get("provider") or "unknown",
        source_category=args.get("source_category") or args.get("category") or "unknown",
        as_of=args.get("as_of") or "",
        artifact_id=args.get("artifact_id") or "",
        warnings=args.get("warnings") if isinstance(args.get("warnings"), list) else [],
        payload=args.get("payload") if isinstance(args.get("payload"), dict) else {},
        workspace_context=workspace_context_payload(workspace_root),
    )
    result = {"status": "recorded", "snapshot_id": snapshot.id, "artifact_id": snapshot.artifact_id, "provider": snapshot.provider, "source_category": snapshot.source_category, "db_canonical": True, "workspace_context": workspace_context_payload(workspace_root)}
    write_audit_event(workspace_root, {"type": "source_snapshot.recorded", "payload": result}, principal_id=args.get("principal_id", "system"), source="service")
    return result


def research_artifact_to_dict(artifact: Any, include_markdown: bool = True) -> dict[str, Any]:
    result = {
        "artifact_id": artifact.artifact_id,
        "artifact_type": artifact.artifact_type,
        "universe": artifact.universe,
        "workflow_type": artifact.workflow_type,
        "symbol": artifact.symbol,
        "title": artifact.title,
        "metadata": artifact.metadata,
        "workspace_context": artifact.workspace_context,
        "source_as_of": artifact.source_as_of,
        "readiness_label": artifact.readiness_label,
        "created_by": artifact.created_by,
        "content_hash": artifact.content_hash,
        "version": artifact.version,
        "export_path": artifact.export_path,
        "parent_artifact_id": artifact.parent_artifact_id,
        "created_at": artifact.created_at.isoformat(),
        "updated_at": artifact.updated_at.isoformat(),
        "db_canonical": True,
    }
    if include_markdown:
        result["markdown"] = artifact.markdown
    return result


def default_research_export_path(artifact: Any) -> str:
    stem = sanitize_id(artifact.artifact_id)
    if artifact.artifact_type == "evidence_pack":
        return f"trading/research/{stem}.evidence.md"
    role = artifact.metadata.get("role") if isinstance(artifact.metadata, dict) else ""
    if role in {"fundamental", "technical", "news", "macro", "instrument", "valuation", "portfolio", "risk", "policy"}:
        return f"trading/reports/{role}/{stem}.md"
    return f"trading/research/{stem}.md"

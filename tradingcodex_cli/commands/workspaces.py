from __future__ import annotations

from pathlib import Path

from tradingcodex_cli.commands.utils import print_json
from tradingcodex_service.domain import (
    ensure_runtime_database,
    ensure_workspace_manifest,
    persist_workspace_context_if_available,
    tradingcodex_db_path,
)


def workspace(root: Path, argv: list[str]) -> None:
    sub = argv[0] if argv else "status"
    if sub == "status":
        manifest = ensure_workspace_manifest(root)
        ensure_runtime_database(root)
        context = persist_workspace_context_if_available(root)
        print_json({
            "status": "ok",
            "workspace_name": manifest["project_name"],
            "workspace_id": manifest["workspace_id"],
            "active_profile": manifest["active_profile"],
            "db_path": str(tradingcodex_db_path()),
            "mcp_scope": manifest["mcp_scope"],
            "execution_mode": manifest["execution_mode"],
            "workspace_context": context,
            "db_canonical": True,
        })
        return
    if sub == "list":
        ensure_runtime_database(root)
        from apps.harness.models import WorkspaceContext

        print_json({
            "db_path": str(tradingcodex_db_path()),
            "workspaces": [
                {
                    "workspace_id": item.workspace_id,
                    "project_name": item.project_name,
                    "path": item.path,
                    "git_remote": item.git_remote,
                    "git_branch": item.git_branch,
                    "active_profile": item.active_profile,
                    "last_seen_at": item.last_seen_at.isoformat(),
                }
                for item in WorkspaceContext.objects.all()[:200]
            ],
            "db_canonical": True,
        })
        return
    raise ValueError("Usage: tcx workspace status|list")

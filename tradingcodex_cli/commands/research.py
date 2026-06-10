from __future__ import annotations

from pathlib import Path

from tradingcodex_service.domain import (
    create_research_artifact,
    export_research_artifact_md,
    get_research_artifact,
    list_research_artifacts,
    search_research_artifacts,
)
from tradingcodex_cli.commands.utils import _option_value, print_json

def research(root: Path, argv: list[str]) -> None:
    sub = argv[0] if argv else "list"
    args = argv[1:]
    if sub == "create":
        markdown_file = _option_value(args, "--markdown-file") or _option_value(args, "--file")
        if not markdown_file:
            raise ValueError("Usage: tcx research create --markdown-file <file.md> [--id <id>] [--title <title>]")
        payload = {
            "artifact_id": _option_value(args, "--id"),
            "artifact_type": _option_value(args, "--type") or "research_memo",
            "universe": _option_value(args, "--universe") or "public_equity",
            "workflow_type": _option_value(args, "--workflow-type") or "",
            "symbol": _option_value(args, "--symbol") or "",
            "title": _option_value(args, "--title") or Path(markdown_file).stem,
            "markdown_path": markdown_file,
            "readiness_label": _option_value(args, "--readiness") or "",
            "created_by": _option_value(args, "--created-by") or "head-manager",
            "export_path": _option_value(args, "--export-path"),
        }
        print_json(create_research_artifact(root, payload))
        return
    if sub == "get":
        artifact_id = args[0] if args and not args[0].startswith("--") else _option_value(args, "--id")
        if not artifact_id:
            raise ValueError("Usage: tcx research get <artifact-id>")
        print_json(get_research_artifact(root, {"artifact_id": artifact_id}))
        return
    if sub == "list":
        print_json(list_research_artifacts(root, {
            "artifact_type": _option_value(args, "--type"),
            "universe": _option_value(args, "--universe"),
            "symbol": _option_value(args, "--symbol"),
            "limit": _option_value(args, "--limit") or 50,
        }))
        return
    if sub == "search":
        query = " ".join(args).strip()
        if not query:
            raise ValueError("Usage: tcx research search <query>")
        print_json(search_research_artifacts(root, {"query": query}))
        return
    if sub == "export":
        artifact_id = args[0] if args and not args[0].startswith("--") else _option_value(args, "--id")
        if not artifact_id:
            raise ValueError("Usage: tcx research export <artifact-id> [--export-path <file.md>]")
        print_json(export_research_artifact_md(root, {"artifact_id": artifact_id, "export_path": _option_value(args, "--export-path")}))
        return
    raise ValueError(f"Unknown research command: {sub}")

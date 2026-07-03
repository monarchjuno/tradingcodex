from __future__ import annotations

from pathlib import Path

from tradingcodex_service.application.research import (
    create_evidence_run_card,
    create_research_artifact,
    create_validation_card,
    export_research_artifact_md,
    get_research_artifact,
    append_research_artifact_version,
    list_research_artifacts,
    search_research_artifacts,
)
from tradingcodex_cli.commands.utils import _list_option, _option_value, print_json

def research(root: Path, argv: list[str]) -> None:
    sub = argv[0] if argv else "list"
    args = argv[1:]
    if sub == "create":
        markdown_file = _option_value(args, "--markdown-file") or _option_value(args, "--file")
        if not markdown_file:
            raise ValueError("Usage: tcx research create --markdown-file <file.md> [--id <id>] [--title <title>] [--source-as-of <date>]")
        payload = {
            "artifact_id": _option_value(args, "--id"),
            "artifact_type": _option_value(args, "--type") or "research_memo",
            "universe": _option_value(args, "--universe") or "public_equity",
            "workflow_type": _option_value(args, "--workflow-type") or "",
            "symbol": _option_value(args, "--symbol") or "",
            "role": _option_value(args, "--role"),
            "title": _option_value(args, "--title"),
            "markdown_path": markdown_file,
            "source_as_of": _option_value(args, "--source-as-of") or "",
            "readiness_label": _option_value(args, "--readiness") or "",
            "context_summary": _option_value(args, "--context-summary") or "",
            "reader_summary": _option_value(args, "--reader-summary") or "",
            "handoff_state": _option_value(args, "--handoff-state") or "",
            "confidence": _option_value(args, "--confidence") or "",
            "missing_evidence": _list_option(args, "--missing-evidence") or [],
            "next_recipient": _option_value(args, "--next-recipient") or "",
            "next_action": _option_value(args, "--next-action") or "",
            "blocked_actions": _list_option(args, "--blocked-actions") or [],
            "source_snapshot_ids": _list_option(args, "--source-snapshot-ids") or [],
            "follow_up_requests": _list_option(args, "--follow-up-requests") or [],
            "improvements": _list_option(args, "--improvements") or [],
            "created_by": _option_value(args, "--created-by") or "head-manager",
            "export_path": _option_value(args, "--export-path"),
        }
        print_json(create_research_artifact(root, payload))
        return
    if sub == "append":
        artifact_id = args[0] if args and not args[0].startswith("--") else _option_value(args, "--id")
        markdown_file = _option_value(args, "--markdown-file") or _option_value(args, "--file")
        if not artifact_id or not markdown_file:
            raise ValueError("Usage: tcx research append <artifact-id> --markdown-file <file.md> [--source-as-of <date>]")
        print_json(append_research_artifact_version(root, {
            "artifact_id": artifact_id,
            "markdown_path": markdown_file,
            "source_as_of": _option_value(args, "--source-as-of") or "",
            "readiness_label": _option_value(args, "--readiness") or "",
            "context_summary": _option_value(args, "--context-summary") or "",
            "reader_summary": _option_value(args, "--reader-summary") or "",
            "handoff_state": _option_value(args, "--handoff-state") or "",
            "confidence": _option_value(args, "--confidence") or "",
            "missing_evidence": _list_option(args, "--missing-evidence"),
            "next_recipient": _option_value(args, "--next-recipient") or "",
            "next_action": _option_value(args, "--next-action") or "",
            "blocked_actions": _list_option(args, "--blocked-actions"),
            "source_snapshot_ids": _list_option(args, "--source-snapshot-ids"),
            "follow_up_requests": _list_option(args, "--follow-up-requests"),
            "improvements": _list_option(args, "--improvements"),
            "created_by": _option_value(args, "--created-by") or "head-manager",
            "export_path": _option_value(args, "--export-path"),
        }))
        return
    if sub in {"run-card", "evidence-run-card"}:
        artifact_path = args[0] if args and not args[0].startswith("--") else _option_value(args, "--artifact-path") or _option_value(args, "--related-artifact-path")
        if not artifact_path:
            raise ValueError("Usage: tcx research run-card <artifact-path> [--validation-summary <text>] [--config-hash <hash>]")
        print_json(create_evidence_run_card(root, {
            "related_artifact_path": artifact_path,
            "config_hash": _option_value(args, "--config-hash"),
            "input_refs": _list_option(args, "--input-ref") or _list_option(args, "--input-refs"),
            "data_source_refs": _list_option(args, "--data-source-ref") or _list_option(args, "--data-source-refs"),
            "validation_summary": _option_value(args, "--validation-summary") or "",
            "warnings": _list_option(args, "--warning") or _list_option(args, "--warnings"),
            "source_limitations": _list_option(args, "--source-limitation") or _list_option(args, "--source-limitations"),
            "created_by": _option_value(args, "--created-by") or "head-manager",
            "export_path": _option_value(args, "--export-path"),
        }))
        return
    if sub == "validation-card":
        artifact_path = args[0] if args and not args[0].startswith("--") else _option_value(args, "--artifact-path") or _option_value(args, "--related-artifact-path")
        if not artifact_path:
            raise ValueError("Usage: tcx research validation-card <artifact-path> [--validation-summary <text>] [--quality-label <label>]")
        print_json(create_validation_card(root, {
            "related_artifact_path": artifact_path,
            "validation_scope": _option_value(args, "--scope") or _option_value(args, "--validation-scope") or "evidence_quality",
            "evidence_quality_label": _option_value(args, "--quality-label") or _option_value(args, "--evidence-quality-label") or "not_validated",
            "input_refs": _list_option(args, "--input-ref") or _list_option(args, "--input-refs"),
            "data_source_refs": _list_option(args, "--data-source-ref") or _list_option(args, "--data-source-refs"),
            "validation_summary": _option_value(args, "--validation-summary") or "",
            "warnings": _list_option(args, "--warning") or _list_option(args, "--warnings"),
            "source_limitations": _list_option(args, "--source-limitation") or _list_option(args, "--source-limitations"),
            "created_by": _option_value(args, "--created-by") or "head-manager",
            "export_path": _option_value(args, "--export-path"),
        }))
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

from __future__ import annotations

from pathlib import Path

from tradingcodex_service.application.artifact_catalog import (
    list_artifact_catalog,
    rebuild_artifact_catalog,
    search_artifact_catalog,
)
from tradingcodex_service.application.research import (
    create_evidence_run_card,
    create_research_artifact,
    create_validation_card,
    export_research_artifact_md,
    get_research_artifact,
    append_research_artifact_version,
    list_research_artifacts,
    rebuild_research_index,
    search_research_artifacts,
)
from tradingcodex_service.application.research_specs import (
    get_research_spec,
    list_research_specs,
)
from tradingcodex_cli.commands.utils import _list_option, _option_value, json_object_input, print_json
from tradingcodex_service.mcp_runtime import call_mcp_tool


def _required_principal(args: list[str], usage: str) -> str:
    principal = _option_value(args, "--principal")
    if not principal:
        raise ValueError(f"{usage} --principal <role>")
    return principal

def research(root: Path, argv: list[str]) -> None:
    sub = argv[0] if argv else "list"
    args = argv[1:]
    if sub == "catalog":
        action = args[0] if args else "list"
        action_args = args[1:]
        filters = {
            "artifact_type": _option_value(action_args, "--type"),
            "universe": _option_value(action_args, "--universe"),
            "symbol": _option_value(action_args, "--symbol"),
            "workflow_run_id": _option_value(action_args, "--workflow-run-id"),
            "readiness_label": _option_value(action_args, "--readiness"),
            "handoff_state": _option_value(action_args, "--handoff-state"),
            "compatibility": _option_value(action_args, "--compatibility"),
            "knowledge_cutoff": _option_value(action_args, "--knowledge-cutoff"),
            "limit": _option_value(action_args, "--limit") or (20 if action == "search" else 100),
        }
        if action == "list":
            print_json(list_artifact_catalog(root, filters))
            return
        if action == "search":
            query_parts = []
            for value in action_args:
                if value.startswith("--"):
                    break
                query_parts.append(value)
            query = " ".join(query_parts).strip()
            if not query:
                raise ValueError("Usage: tcx research catalog search <query> [--knowledge-cutoff <timestamp>]")
            print_json(search_artifact_catalog(root, {**filters, "query": query}))
            return
        if action == "rebuild":
            print_json(rebuild_artifact_catalog(root))
            return
        raise ValueError(f"Unknown research catalog command: {action}")
    if sub == "spec":
        action = args[0] if args else "list"
        action_args = args[1:]
        if action == "create":
            input_path = _option_value(action_args, "--json-file") or (action_args[0] if action_args and not action_args[0].startswith("--") else None)
            usage = "Usage: tcx research spec create <payload.json|->"
            payload = json_object_input(root, input_path, usage)
            print_json(call_mcp_tool(root, "create_research_spec", {**payload, "principal_id": _required_principal(action_args, usage)}))
            return
        if action == "get":
            spec_id = action_args[0] if action_args and not action_args[0].startswith("--") else None
            if not spec_id:
                raise ValueError("Usage: tcx research spec get <spec-id>")
            print_json(get_research_spec(root, {"spec_id": spec_id}))
            return
        if action == "list":
            print_json(list_research_specs(root))
            return
        raise ValueError(f"Unknown research spec command: {action}")
    if sub == "replay":
        action = args[0] if args else ""
        action_args = args[1:]
        if action != "create":
            raise ValueError("Usage: tcx research replay create <payload.json|->")
        input_path = _option_value(action_args, "--json-file") or (action_args[0] if action_args and not action_args[0].startswith("--") else None)
        usage = "Usage: tcx research replay create <payload.json|->"
        payload = json_object_input(root, input_path, usage)
        print_json(call_mcp_tool(root, "create_replay_manifest", {**payload, "principal_id": _required_principal(action_args, usage)}))
        return
    if sub == "experiment":
        action = args[0] if args else ""
        action_args = args[1:]
        if action != "record":
            raise ValueError("Usage: tcx research experiment record <payload.json|->")
        input_path = _option_value(action_args, "--json-file") or (action_args[0] if action_args and not action_args[0].startswith("--") else None)
        usage = "Usage: tcx research experiment record <payload.json|->"
        payload = json_object_input(root, input_path, usage)
        print_json(call_mcp_tool(root, "record_experiment_run", {**payload, "principal_id": _required_principal(action_args, usage)}))
        return
    if sub in {"causal-analysis", "judgment-prior", "judgment-review"}:
        input_path = _option_value(args, "--json-file") or (args[0] if args and not args[0].startswith("--") else None)
        usage = f"Usage: tcx research {sub} <payload.json|->"
        payload = json_object_input(root, input_path, usage)
        tool = {
            "causal-analysis": "create_causal_equity_analysis",
            "judgment-prior": "record_blind_judgment_prior",
            "judgment-review": "complete_judgment_review",
        }[sub]
        print_json(call_mcp_tool(root, tool, {**payload, "principal_id": _required_principal(args, usage)}))
        return
    if sub == "index":
        action = args[0] if args else "rebuild"
        if action != "rebuild":
            raise ValueError("Usage: tcx research index rebuild")
        print_json(rebuild_research_index(root))
        return
    if sub == "create":
        markdown_file = _option_value(args, "--markdown-file")
        universe = _option_value(args, "--universe")
        if not markdown_file or not universe:
            raise ValueError("Usage: tcx research create --markdown-file <file.md> --universe <universe> [--artifact-id <id>] [--title <title>] [--source-as-of <date>]")
        payload = {
            "artifact_id": _option_value(args, "--artifact-id"),
            "artifact_type": _option_value(args, "--type") or "research_memo",
            "universe": universe,
            "workflow_type": _option_value(args, "--workflow-type") or "",
            "symbol": _option_value(args, "--symbol") or "",
            "role": _option_value(args, "--role"),
            "producer_role": _option_value(args, "--producer-role"),
            "title": _option_value(args, "--title"),
            "markdown_path": markdown_file,
            "source_as_of": _option_value(args, "--source-as-of") or "",
            "knowledge_cutoff": _option_value(args, "--knowledge-cutoff") or "",
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
            "principal_id": _option_value(args, "--principal") or "head-manager",
            "export_path": _option_value(args, "--export-path"),
        }
        print_json(create_research_artifact(root, payload))
        return
    if sub == "append":
        artifact_id = args[0] if args and not args[0].startswith("--") else _option_value(args, "--artifact-id")
        markdown_file = _option_value(args, "--markdown-file")
        if not artifact_id or not markdown_file:
            raise ValueError("Usage: tcx research append <artifact-id> --markdown-file <file.md> [--source-as-of <date>]")
        print_json(append_research_artifact_version(root, {
            "artifact_id": artifact_id,
            "markdown_path": markdown_file,
            "source_as_of": _option_value(args, "--source-as-of") or "",
            "knowledge_cutoff": _option_value(args, "--knowledge-cutoff") or "",
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
            "principal_id": _option_value(args, "--principal") or "head-manager",
            "export_path": _option_value(args, "--export-path"),
        }))
        return
    if sub == "run-card":
        artifact_path = args[0] if args and not args[0].startswith("--") else None
        if not artifact_path:
            raise ValueError("Usage: tcx research run-card <artifact-path> [--validation-summary <text>] [--config-hash <hash>]")
        print_json(create_evidence_run_card(root, {
            "related_artifact_path": artifact_path,
            "config_hash": _option_value(args, "--config-hash"),
            "input_refs": _list_option(args, "--input-ref"),
            "data_source_refs": _list_option(args, "--data-source-ref"),
            "validation_summary": _option_value(args, "--validation-summary") or "",
            "warnings": _list_option(args, "--warning"),
            "source_limitations": _list_option(args, "--source-limitation"),
            "principal_id": _option_value(args, "--principal") or "head-manager",
            "export_path": _option_value(args, "--export-path"),
        }))
        return
    if sub == "validation-card":
        artifact_path = args[0] if args and not args[0].startswith("--") else None
        if not artifact_path:
            raise ValueError("Usage: tcx research validation-card <artifact-path> [--validation-summary <text>] [--quality-label <label>]")
        print_json(create_validation_card(root, {
            "related_artifact_path": artifact_path,
            "validation_scope": _option_value(args, "--scope") or "evidence_quality",
            "evidence_quality_label": _option_value(args, "--quality-label") or "not_validated",
            "input_refs": _list_option(args, "--input-ref"),
            "data_source_refs": _list_option(args, "--data-source-ref"),
            "validation_summary": _option_value(args, "--validation-summary") or "",
            "warnings": _list_option(args, "--warning"),
            "source_limitations": _list_option(args, "--source-limitation"),
            "principal_id": _option_value(args, "--principal") or "head-manager",
            "export_path": _option_value(args, "--export-path"),
        }))
        return
    if sub == "get":
        artifact_id = args[0] if args and not args[0].startswith("--") else None
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
        artifact_id = args[0] if args and not args[0].startswith("--") else None
        if not artifact_id:
            raise ValueError("Usage: tcx research export <artifact-id> [--export-path <file.md>]")
        print_json(export_research_artifact_md(root, {"artifact_id": artifact_id, "export_path": _option_value(args, "--export-path")}))
        return
    raise ValueError(f"Unknown research command: {sub}")

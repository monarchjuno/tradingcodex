from __future__ import annotations

from pathlib import Path

from tradingcodex_service.application.artifact_catalog import (
    list_artifact_catalog,
    rebuild_artifact_catalog,
    search_artifact_catalog,
)
from tradingcodex_service.application.research import (
    create_evidence_run_card,
    create_validation_card,
    export_research_artifact_md,
    get_source_snapshot,
    rebuild_research_index,
)
from tradingcodex_service.application.datasets import export_dataset_csv, get_dataset_rows
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
    if sub == "source":
        action = args[0] if args else ""
        action_args = args[1:]
        snapshot_id = (
            action_args[0]
            if action_args and not action_args[0].startswith("--")
            else None
        )
        if action != "get" or not snapshot_id:
            raise ValueError(
                "Usage: tcx research source get <snapshot-id> [--include-payload] "
                "[--max-payload-chars <count>]"
            )
        print_json(
            get_source_snapshot(
                root,
                {
                    "snapshot_id": snapshot_id,
                    "include_payload": "--include-payload" in action_args,
                    "max_payload_chars": _option_value(
                        action_args, "--max-payload-chars"
                    )
                    or 20_000,
                },
            )
        )
        return
    if sub == "dataset":
        action = args[0] if args else ""
        action_args = args[1:]
        dataset_id = (
            action_args[0]
            if action_args and not action_args[0].startswith("--")
            else None
        )
        if action == "rows" and dataset_id:
            print_json(
                get_dataset_rows(
                    root,
                    {
                        "dataset_id": dataset_id,
                        "columns": _list_option(action_args, "--columns"),
                        "time_column": _option_value(action_args, "--time-column")
                        or "",
                        "start": _option_value(action_args, "--start") or "",
                        "end": _option_value(action_args, "--end") or "",
                        "cursor": _option_value(action_args, "--cursor") or "",
                        "limit": _option_value(action_args, "--limit") or 120,
                    },
                )
            )
            return
        if action == "export" and dataset_id:
            print_json(
                export_dataset_csv(
                    root,
                    {
                        "dataset_id": dataset_id,
                        "columns": _list_option(action_args, "--columns"),
                        "export_path": _option_value(action_args, "--export-path"),
                    },
                )
            )
            return
        raise ValueError(
            "Usage: tcx research dataset rows <dataset-id> [--columns <csv|json>] "
            "[--time-column <name>] [--start <timestamp>] [--end <timestamp>] "
            "[--cursor <cursor>] [--limit <1-120>]\n"
            "   or: tcx research dataset export <dataset-id> [--columns <csv|json>] "
            "[--export-path trading/research/datasets/exports/<file.csv>]"
        )
    if sub == "catalog":
        action = args[0] if args else "list"
        action_args = args[1:]
        filters = {
            "artifact_type": _option_value(action_args, "--type"),
            "universe": _option_value(action_args, "--universe"),
            "symbol": _option_value(action_args, "--symbol"),
            "workflow_run_id": _option_value(action_args, "--workflow-run-id"),
            "evidence_readiness": _option_value(action_args, "--evidence-readiness"),
            "action_readiness": _option_value(action_args, "--action-readiness"),
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
        usage = "Usage: tcx research create <payload.json|-> --principal <role>"
        input_path = _option_value(args, "--json-file") or (
            args[0] if args and not args[0].startswith("--") else None
        )
        payload = json_object_input(root, input_path, usage)
        print_json(
            call_mcp_tool(
                root,
                "create_research_artifact",
                {**payload, "principal_id": _required_principal(args, usage)},
            )
        )
        return
    if sub == "append":
        usage = "Usage: tcx research append <payload.json|-> --principal <role>"
        input_path = _option_value(args, "--json-file") or (
            args[0] if args and not args[0].startswith("--") else None
        )
        payload = json_object_input(root, input_path, usage)
        print_json(
            call_mcp_tool(
                root,
                "append_research_artifact_version",
                {**payload, "principal_id": _required_principal(args, usage)},
            )
        )
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
        print_json(call_mcp_tool(root, "get_research_artifact", {"artifact_id": artifact_id}))
        return
    if sub == "list":
        print_json(call_mcp_tool(root, "list_research_artifacts", {
            "artifact_type": _option_value(args, "--type"),
            "universe": _option_value(args, "--universe"),
            "symbol": _option_value(args, "--symbol"),
            "workflow_run_id": _option_value(args, "--workflow-run-id"),
            "evidence_readiness": _option_value(args, "--evidence-readiness"),
            "action_readiness": _option_value(args, "--action-readiness"),
            "limit": _option_value(args, "--limit") or 50,
        }))
        return
    if sub == "search":
        query = " ".join(args).strip()
        if not query:
            raise ValueError("Usage: tcx research search <query>")
        print_json(call_mcp_tool(root, "search_research_artifacts", {"query": query}))
        return
    if sub == "export":
        artifact_id = args[0] if args and not args[0].startswith("--") else None
        if not artifact_id:
            raise ValueError("Usage: tcx research export <artifact-id> [--export-path <file.md>]")
        print_json(export_research_artifact_md(root, {"artifact_id": artifact_id, "export_path": _option_value(args, "--export-path")}))
        return
    raise ValueError(f"Unknown research command: {sub}")

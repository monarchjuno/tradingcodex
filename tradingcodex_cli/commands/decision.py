from __future__ import annotations

from pathlib import Path

from tradingcodex_cli.commands.utils import _option_value, json_object_input, print_json
from tradingcodex_service.application.decision_packages import (
    export_decision_package,
    get_decision_package,
    get_decision_snapshot,
    list_decision_packages,
    list_decision_snapshots,
)
from tradingcodex_service.application.judgments import (
    get_judgment_snapshot,
    list_judgment_snapshots,
    record_decision_adoption,
    terminal_adoption_args,
)


def decision(root: Path, argv: list[str]) -> None:
    sub = argv[0] if argv else "list"
    args = argv[1:]
    if sub == "list":
        print_json(list_decision_packages(root, int(_option_value(args, "--limit") or 50)))
        return
    if sub == "show":
        decision_id = args[0] if args and not args[0].startswith("--") else _option_value(args, "--id")
        if not decision_id:
            raise ValueError("Usage: tcx decision show <decision-id>")
        print_json(get_decision_package(root, decision_id))
        return
    if sub == "export":
        decision_id = args[0] if args and not args[0].startswith("--") else _option_value(args, "--id")
        if not decision_id:
            raise ValueError("Usage: tcx decision export <decision-id> [--export-path trading/decisions/file.md]")
        print_json(export_decision_package(root, decision_id, _option_value(args, "--export-path")))
        return
    if sub == "adopt":
        usage = "Usage: tcx decision adopt <payload.json|->"
        input_path = _option_value(args, "--json-file") or (
            args[0] if args and not args[0].startswith("--") else None
        )
        payload = json_object_input(root, input_path, usage)
        print_json(record_decision_adoption(root, terminal_adoption_args(payload)))
        return
    if sub == "judgment":
        action = args[0] if args else "list"
        action_args = args[1:]
        if action == "list":
            print_json(list_judgment_snapshots(root, int(_option_value(action_args, "--limit") or 50)))
            return
        if action == "show":
            judgment_id = action_args[0] if action_args and not action_args[0].startswith("--") else _option_value(action_args, "--id")
            if not judgment_id:
                raise ValueError("Usage: tcx decision judgment show <judgment-id>")
            print_json(get_judgment_snapshot(root, judgment_id))
            return
        raise ValueError("Usage: tcx decision judgment list|show")
    if sub == "snapshot":
        action = args[0] if args else "list"
        action_args = args[1:]
        if action == "list":
            print_json(list_decision_snapshots(root, int(_option_value(action_args, "--limit") or 50)))
            return
        if action == "record":
            raise ValueError(
                "legacy DecisionSnapshot writes are retired; use "
                "the Head Manager record_judgment_snapshot MCP tool"
            )
        if action == "show":
            decision_id = action_args[0] if action_args and not action_args[0].startswith("--") else _option_value(action_args, "--id")
            if not decision_id:
                raise ValueError("Usage: tcx decision snapshot show <decision-id>")
            print_json(get_decision_snapshot(root, decision_id))
            return
        raise ValueError("Usage: tcx decision snapshot list|show")
    raise ValueError("Usage: tcx decision list|show|export|judgment|snapshot|adopt")

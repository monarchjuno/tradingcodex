from __future__ import annotations

import json
import sys
from pathlib import Path

from tradingcodex_cli.commands.utils import print_json
from tradingcodex_service.application.decision_packages import build_workflow_plan, create_decision_package
from tradingcodex_service.application.common import read_json
from tradingcodex_service.application.workflow_planner import (
    build_deterministic_workflow_plan,
    read_workflow_intake,
    record_workflow_intake,
    record_workflow_plan,
    validate_workflow_plan,
)


def workflow(root: Path, argv: list[str]) -> None:
    sub = argv[0] if argv else "plan"
    args = argv[1:]
    prompt = " ".join(args).strip()
    if sub == "intake":
        if not prompt:
            raise ValueError("Usage: tcx workflow intake <request>")
        print_json(record_workflow_intake(root, prompt))
        return
    if sub == "validate":
        plan = _read_plan_arg(root, args)
        intake = read_workflow_intake(root, str(plan.get("workflow_run_id") or ""))
        print_json(validate_workflow_plan(plan, intake=intake))
        return
    if sub == "record":
        plan = _read_plan_arg(root, args)
        intake = read_workflow_intake(root, str(plan.get("workflow_run_id") or ""))
        result = record_workflow_plan(root, plan, intake=intake)
        print_json(result)
        if result["status"] != "recorded":
            sys.exit(1)
        return
    if sub == "plan":
        if not prompt:
            raise ValueError("Usage: tcx workflow plan <investment request>")
        print_json(build_workflow_plan(root, prompt))
        return
    if sub == "preview":
        if not prompt:
            raise ValueError("Usage: tcx workflow preview <investment request>")
        print_json(build_deterministic_workflow_plan(root, prompt))
        return
    if sub == "run":
        if not prompt:
            raise ValueError("Usage: tcx workflow run <investment request>")
        print_json(create_decision_package(root, prompt))
        return
    raise ValueError("Usage: tcx workflow intake|validate|record|plan|preview|run ...")


def _read_plan_arg(root: Path, args: list[str]) -> dict:
    if "--plan" not in args:
        raise ValueError("Usage: tcx workflow validate|record --plan <path|->")
    index = args.index("--plan")
    if index + 1 >= len(args):
        raise ValueError("Usage: tcx workflow validate|record --plan <path|->")
    raw = args[index + 1]
    if raw == "-":
        return json.loads(sys.stdin.read() or "{}")
    value = read_json(root / raw, {})
    if not isinstance(value, dict):
        raise ValueError(f"workflow plan is not a JSON object: {raw}")
    return value

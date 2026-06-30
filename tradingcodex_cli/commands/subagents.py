from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from tradingcodex_service.application.agents import (
    AGENT_SPECS,
    diff_agent_configuration,
    inspect_agent_configuration,
    project_agent_configuration,
    EXPECTED_SUBAGENTS,
)
from tradingcodex_service.application.context_budget import audit_context_budget
from tradingcodex_service.application.harness import (
    build_subagent_starter_prompt,
    build_workflow_intake_summary,
    evaluate_artifact_supervisor_loop,
    is_connector_build_request,
    is_investment_workflow_request,
)
from tradingcodex_cli.commands.utils import (
    _option_value,
    _parse_agent_list,
    list_skills,
    list_subagents,
    print_json,
    read_loop_state,
    read_subagent_state,
    read_thread_policy,
    skills_for_role,
)

def subagents(root: Path, argv: list[str]) -> None:
    sub = argv[0] if argv else "list"
    args = argv[1:]
    if sub == "list":
        for agent in list_subagents(root):
            print(f"{agent['name']}\t{agent['description']}")
        return
    if sub == "prompt":
        json_output = "--json" in args
        explain = "--explain" in args
        request = " ".join(arg for arg in args if arg not in {"--json", "--explain"}).strip()
        if not request:
            raise ValueError("Usage: tcx subagents prompt [--json|--explain] <investment request>")
        prompt = build_subagent_starter_prompt(request, root)
        summary = build_workflow_intake_summary(request, root)
        if json_output:
            print_json({"intake_summary": summary, "starter_prompt": prompt})
            return
        if explain:
            print(_format_prompt_explanation(summary, prompt))
            return
        print(prompt)
        return
    if sub == "status":
        agents = list_subagents(root)
        print_json({
            "expected_count": len(EXPECTED_SUBAGENTS),
            "installed_count": len(agents),
            "fixed_roster_ok": len(agents) == len(EXPECTED_SUBAGENTS),
            "skills_installed": len(list_skills(root)),
            "thread_policy": read_thread_policy(root),
            "agents": agents,
        })
        return
    if sub == "state":
        print_json(read_subagent_state(root, _option_value(args, "--run")))
        return
    if sub == "context-audit":
        result = audit_context_budget(root, strict="--strict" in args)
        print_json(result)
        if result["status"] != "pass":
            sys.exit(1)
        return
    if sub == "loop":
        request = _option_value(args, "--request") or _option_value(args, "--prompt") or ""
        artifacts = _artifact_args(args)
        if not artifacts:
            raise ValueError("Usage: tcx subagents loop --request <request> --artifact <path> [--record]")
        print_json(evaluate_artifact_supervisor_loop(root, request, artifacts, record="--record" in args))
        return
    if sub == "inspect":
        role = args[0] if args else ""
        if not role:
            raise ValueError("Usage: tcx subagents inspect <role>")
        print_json(inspect_agent_configuration(root, role))
        return
    if sub == "diff":
        role = args[0] if args and not args[0].startswith("--") else _option_value(args, "--role")
        if not role:
            raise ValueError("Usage: tcx subagents diff <role>")
        print_json(diff_agent_configuration(root, role))
        return
    if sub == "project":
        role = _option_value(args, "--role")
        proposal = _option_value(args, "--proposal")
        applied_by = _option_value(args, "--applied-by") or "local-cli"
        result = project_agent_configuration(
            root,
            role=role,
            proposal_path=(Path(proposal) if proposal else None),
            applied_by=applied_by,
        )
        print_json({"status": "projected", "projection_hash": result["projection_hash"], "manifest": ".tradingcodex/generated/projection-manifest.json"})
        return
    if sub == "plan":
        installed = list_subagents(root)
        request_text = " ".join(arg for arg in args if arg != "--json").strip()
        if "--all" not in args and request_text and (is_investment_workflow_request(request_text) or is_connector_build_request(request_text)):
            print_json(_workflow_loop_plan(root, request_text, installed))
            return
        requested = [agent["name"] for agent in installed] if "--all" in args else _parse_agent_list(args)
        if not requested:
            raise ValueError("Usage: tcx subagents plan <agent...>|--all")
        installed_names = {agent["name"] for agent in installed}
        unknown = [agent for agent in requested if agent not in installed_names]
        thread_policy = read_thread_policy(root)
        size = max(1, int(thread_policy["max_parallel_subagents"]))
        batches = [{"batch": i + 1, "agents": requested[i:i + size]} for i in range(0, len(requested), size)]
        print_json({
            "requested_count": len(requested),
            "requested_agents": requested,
            "all_fixed_roster": "--all" in args,
            "unknown_agents": unknown,
            "thread_policy": thread_policy,
            "parallel_spawn_ok": not unknown and len(batches) == 1,
            "required_batches": len(batches),
            "batches": batches,
            "recommendation": "spawn requested subagents in one batch" if len(batches) == 1 else "spawn each batch sequentially and hand off artifacts before starting the next batch",
        })
        if unknown:
            sys.exit(1)
        return
    if sub == "skills":
        role = args[0] if args else ""
        if role not in AGENT_SPECS:
            raise ValueError(f"Unknown subagent or role: {role}")
        print_json({"agent": role, "skills": skills_for_role(root, role)})
        return
    raise ValueError(f"Unknown subagents command: {sub}")


def _workflow_loop_plan(root: Path, request: str, installed: list[dict[str, str]]) -> dict[str, Any]:
    summary = build_workflow_intake_summary(request, root)
    selected = list(summary.get("selected_team") or [item.get("role") for item in summary.get("subagents") or [] if item.get("role")])
    installed_names = {agent["name"] for agent in installed}
    unknown = [agent for agent in selected if agent not in installed_names]
    thread_policy = read_thread_policy(root)
    size = max(1, int(thread_policy["max_parallel_subagents"]))
    batches = [{"batch": i + 1, "agents": selected[i:i + size]} for i in range(0, len(selected), size)]
    loop_state = read_loop_state(root)
    return {
        "request": request,
        "workflow_lane": summary.get("workflow_lane"),
        "initial_dispatch": selected,
        "allowed_followup_team": summary.get("allowed_followup_team") or [],
        "escalation_team": summary.get("escalation_team") or [],
        "loop_policy": summary.get("loop_policy") or {},
        "workflow_loop_state_path": summary.get("workflow_loop_state_path"),
        "workflow_loop_run_state_path": loop_state.get("state_path", "") if isinstance(loop_state, dict) else "",
        "pending_tasks": loop_state.get("pending_tasks", []) if isinstance(loop_state, dict) else [],
        "stop_reason": loop_state.get("stop_reason", "") if isinstance(loop_state, dict) else "",
        "unknown_agents": unknown,
        "thread_policy": thread_policy,
        "parallel_spawn_ok": not unknown and len(batches) <= 1,
        "required_batches": len(batches),
        "batches": batches,
        "terminal_workflow_actions": summary.get("terminal_workflow_actions") or [],
        "artifact_handoff_states": summary.get("artifact_handoff_states") or [],
        "planner_actions": summary.get("planner_actions") or [],
        "exit_criteria": summary.get("exit_criteria") or [],
        "recommendation": "record assisted loop state and dispatch selected roles" if selected else "head-manager lane; no fixed-role dispatch",
    }


def _artifact_args(args: list[str]) -> list[str]:
    artifacts: list[str] = []
    for index, arg in enumerate(args):
        if arg in {"--artifact", "--artifacts"} and index + 1 < len(args):
            artifacts.extend(item.strip() for item in args[index + 1].split(",") if item.strip())
    return artifacts


def _format_prompt_explanation(summary: dict, prompt: str) -> str:
    lines = [
        f"Workflow: {summary.get('label') or 'Unknown'}",
        f"Question: {summary.get('primary_question') or 'Review the request before dispatch.'}",
        f"Universe: {summary.get('investment_universe_label') or summary.get('investment_universe') or 'unknown'}",
    ]
    idea_translation = summary.get("idea_translation") or {}
    if idea_translation:
        lines.append(f"Idea translated: {idea_translation.get('plain_english')}")
        lines.append(f"  Working hypothesis: {idea_translation.get('working_hypothesis')}")
        lines.append(f"  Safety boundary: {idea_translation.get('safety_boundary')}")
    subagents = summary.get("subagents") or []
    if subagents:
        lines.append("Selected roles: " + ", ".join(agent.get("label") or agent.get("role") for agent in subagents))
        lines.append("Why these roles:")
        for agent in subagents:
            lines.append(f"  - {agent.get('label') or agent.get('role')}: {agent.get('why_selected')}")
    else:
        lines.append("Selected roles: head-manager only")
    blocked = summary.get("blocked_actions") or []
    if blocked:
        lines.append("Still blocked: " + ", ".join(blocked))
    blocked_details = summary.get("blocked_action_details") or []
    if blocked_details:
        lines.append("Why blocked:")
        for item in blocked_details:
            lines.append(f"  - {item.get('label')}: {item.get('reason')}")
    review_highlights = summary.get("review_highlights") or []
    if review_highlights:
        lines.append("Decision checks:")
        for item in review_highlights:
            lines.append(f"  - {item.get('label')}: {item.get('detail')}")
    next_actions = summary.get("next_allowed_actions") or []
    if next_actions:
        lines.append("Next allowed actions:")
        for item in next_actions:
            lines.append(f"  - {item.get('label')}: {item.get('detail')}")
    method_lenses = summary.get("method_lenses") or []
    if method_lenses:
        lines.append("Method lenses:")
        for item in method_lenses:
            plain = item.get("plain")
            lines.append(f"  - {item.get('label')}: {item.get('detail')}")
            if plain:
                lines.append(f"     Plain meaning: {plain}")
            if item.get("reference"):
                lines.append(f"     Reference: {item.get('reference')}")
    loop_controls = summary.get("loop_controls") or []
    if loop_controls:
        lines.append("Iteration controls:")
        for item in loop_controls:
            lines.append(f"  - {item.get('label')}: {item.get('detail')}")
    judgment_controls = summary.get("judgment_controls") or []
    if judgment_controls:
        lines.append("Judgment controls:")
        for item in judgment_controls:
            lines.append(f"  - {item.get('label')}: {item.get('detail')}")
    strategy_baseline = summary.get("strategy_baseline") or {}
    if strategy_baseline:
        lines.append(f"Strategy baseline: {strategy_baseline.get('summary')}")
    profile_inputs = summary.get("investor_profile_inputs") or []
    if profile_inputs:
        lines.append("Profile needed before advice: " + ", ".join(profile_inputs))
    questions = summary.get("questions_to_answer") or []
    if questions:
        lines.append("Questions to answer:")
        for item in questions:
            lines.append(f"  - {item.get('question')} ({item.get('why_required')})")
    stages = summary.get("workflow_stages") or []
    if stages:
        lines.append("Workflow steps:")
        for index, stage in enumerate(stages, start=1):
            lines.append(f"  {index}. {stage.get('label')}: {stage.get('summary')}")
            exit_criteria = stage.get("exit_criteria") or []
            if exit_criteria:
                lines.append("     Needs: " + "; ".join(exit_criteria))
    lines.extend(["", "Codex prompt:", prompt])
    return "\n".join(lines)

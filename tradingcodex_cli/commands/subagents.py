from __future__ import annotations

import sys
from pathlib import Path

from tradingcodex_service.domain import EXPECTED_SUBAGENTS, ROLE_SKILL_MAP, build_subagent_starter_prompt
from tradingcodex_cli.commands.utils import (
    _option_value,
    _parse_agent_list,
    list_skills,
    list_subagents,
    print_json,
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
        request = " ".join(args).strip()
        if not request:
            raise ValueError("Usage: tcx subagents prompt <investment request>")
        print(build_subagent_starter_prompt(request))
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
    if sub == "plan":
        installed = list_subagents(root)
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
        if role not in ROLE_SKILL_MAP:
            raise ValueError(f"Unknown subagent or role: {role}")
        print_json({"agent": role, "skills": skills_for_role(root, role)})
        return
    raise ValueError(f"Unknown subagents command: {sub}")

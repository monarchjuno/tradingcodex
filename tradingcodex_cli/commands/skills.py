from __future__ import annotations

from pathlib import Path

from tradingcodex_cli.commands.utils import _option_value, apply_skill_proposal, list_skills, print_json, write_skill_proposal

def skills(root: Path, argv: list[str]) -> None:
    sub = argv[0] if argv else "list"
    args = argv[1:]
    if sub == "list":
        for skill in list_skills(root, include_internal="--all" in args):
            print(skill)
        return
    if sub == "inspect":
        name = args[0] if args else ""
        skill_path = root / ".agents" / "skills" / name / "SKILL.md"
        if not skill_path.exists():
            raise ValueError(f"Unknown skill: {name}")
        print(skill_path.read_text(encoding="utf-8"))
        return
    if sub in {"propose-add", "propose-update"}:
        target = _option_value(args, "--to")
        skill = _option_value(args, "--skill")
        if not target or not skill:
            raise ValueError(f"Usage: tcx skills {sub} --to <agent> --skill <skill>")
        print_json(write_skill_proposal(root, sub.replace("propose-", ""), target, skill))
        return
    if sub == "apply-proposal":
        proposal_path = Path(args[0]) if args else None
        if not proposal_path:
            raise ValueError("Usage: tcx skills apply-proposal <proposal.yaml> [--approved-by <principal>]")
        apply_skill_proposal(root, proposal_path if proposal_path.is_absolute() else root / proposal_path, _option_value(args, "--approved-by"))
        return
    raise ValueError(f"Unknown skills command: {sub}")

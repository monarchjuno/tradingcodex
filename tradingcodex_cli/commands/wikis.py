from __future__ import annotations

from pathlib import Path

from tradingcodex_cli.commands.utils import _option_value, _validate_options, print_json
from tradingcodex_service.application.knowledge_wikis import (
    get_knowledge_wiki_record,
    install_knowledge_wiki,
    read_knowledge_wiki_records,
    remove_knowledge_wiki,
    rollback_knowledge_wiki,
    set_knowledge_wiki_status,
    update_knowledge_wiki,
    validate_knowledge_wiki_source,
)


def wikis(root: Path, argv: list[str]) -> None:
    sub = argv[0] if argv else "list"
    args = argv[1:]
    if sub == "list":
        _validate_args(args, value_options=set(), flag_options={"--active", "--json"}, positional_count=0)
        records = read_knowledge_wiki_records(root, include_removed="--active" not in args)
        if "--active" in args:
            records = [record for record in records if record["status"] == "active"]
        if "--json" in args:
            print_json(records)
        else:
            for record in records:
                print(f"{record['wiki_id']}\t{record['version']}\t{record['status']}")
        return
    if sub == "inspect":
        _validate_args(args, value_options=set(), flag_options=set(), positional_count=1)
        print_json(get_knowledge_wiki_record(root, _required_id(args, sub)))
        return
    if sub in {"validate", "install"}:
        _validate_args(
            args,
            value_options={"--git", "--local", "--ref"},
            flag_options=set(),
            positional_count=0,
        )
        kwargs = {
            "local_source": _option_value(args, "--local"),
            "git_source": _option_value(args, "--git"),
            "ref": _option_value(args, "--ref") or "",
        }
        print_json(
            validate_knowledge_wiki_source(root, **kwargs)
            if sub == "validate"
            else install_knowledge_wiki(root, **kwargs, active=False, actor="local-cli")
        )
        return
    if sub == "update":
        _validate_args(
            args,
            value_options={"--git", "--local", "--ref"},
            flag_options=set(),
            positional_count=1,
        )
        print_json(
            update_knowledge_wiki(
                root,
                _required_id(args, sub),
                local_source=_option_value(args, "--local"),
                git_source=_option_value(args, "--git"),
                ref=_option_value(args, "--ref"),
                actor="local-cli",
            )
        )
        return
    if sub in {"activate", "deactivate"}:
        _validate_args(args, value_options=set(), flag_options=set(), positional_count=1)
        print_json(
            set_knowledge_wiki_status(
                root,
                _required_id(args, sub),
                "active" if sub == "activate" else "inactive",
                actor="local-cli",
            )
        )
        return
    if sub == "rollback":
        _validate_args(args, value_options={"--version"}, flag_options=set(), positional_count=1)
        print_json(
            rollback_knowledge_wiki(
                root,
                _required_id(args, sub),
                version=_option_value(args, "--version") or "",
                actor="local-cli",
            )
        )
        return
    if sub == "remove":
        _validate_args(args, value_options=set(), flag_options=set(), positional_count=1)
        print_json(remove_knowledge_wiki(root, _required_id(args, sub), actor="local-cli"))
        return
    raise ValueError(f"Unknown wikis command: {sub}")


def _required_id(args: list[str], sub: str) -> str:
    wiki_id = args[0] if args and not args[0].startswith("--") else ""
    if not wiki_id:
        raise ValueError(f"Usage: tcx wikis {sub} <knowledge-wiki-id>")
    return wiki_id


def _validate_args(
    args: list[str],
    *,
    value_options: set[str],
    flag_options: set[str],
    positional_count: int,
) -> None:
    _validate_options(args, value_options=value_options, flag_options=flag_options)
    positionals: list[str] = []
    seen: set[str] = set()
    index = 0
    while index < len(args):
        value = args[index]
        if value in value_options:
            if value in seen:
                raise ValueError(f"wikis option may be supplied only once: {value}")
            seen.add(value)
            index += 2
            continue
        if value in flag_options:
            if value in seen:
                raise ValueError(f"wikis option may be supplied only once: {value}")
            seen.add(value)
            index += 1
            continue
        if not value.startswith("--"):
            positionals.append(value)
        index += 1
    if len(positionals) != positional_count:
        raise ValueError("wikis command received unexpected positional arguments")

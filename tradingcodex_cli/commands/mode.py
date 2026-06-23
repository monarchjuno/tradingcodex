from __future__ import annotations

import argparse
import json
from pathlib import Path

from tradingcodex_service.application.runtime_mode import get_runtime_mode_status, set_runtime_mode


def mode(root: Path, argv: list[str]) -> None:
    sub = argv[0] if argv else "status"
    if sub == "status":
        parser = argparse.ArgumentParser(prog="tcx mode status")
        parser.add_argument("--json", action="store_true")
        args = parser.parse_args(argv[1:])
        status = get_runtime_mode_status(root)
        if args.json:
            print(json.dumps(status, indent=2, ensure_ascii=False, sort_keys=True))
            return
        print(f"TradingCodex mode: {status['mode']}")
        print(f"Build enabled: {status['build_enabled']}")
        if status.get("expires_at"):
            print(f"Build expires: {status['expires_at']}")
        if status.get("build_blocked_reason"):
            print(f"Blocked: {status['build_blocked_reason']}")
        return
    if sub == "set":
        parser = argparse.ArgumentParser(prog="tcx mode set")
        parser.add_argument("mode", choices=["operate", "build"])
        parser.add_argument("--reason", default="")
        args = parser.parse_args(argv[1:])
        if args.mode == "build" and not args.reason.strip():
            raise ValueError("tcx mode set build requires --reason <text>")
        status = set_runtime_mode(root, args.mode, reason=args.reason)
        print(f"TradingCodex mode set: {status['mode']}")
        if status.get("expires_at"):
            print(f"Build expires: {status['expires_at']}")
        return
    raise ValueError("Usage: tcx mode status [--json]\n       tcx mode set build --reason <reason>\n       tcx mode set operate")

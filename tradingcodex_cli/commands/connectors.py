from __future__ import annotations

import argparse
import json
from pathlib import Path

from tradingcodex_service.application import brokers


def connectors(root: Path, argv: list[str]) -> None:
    sub = argv[0] if argv else "status"
    if sub == "status":
        result = brokers.get_connector_build_status(root, {})
        print(json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True))
        return
    if sub == "scaffold":
        parser = argparse.ArgumentParser(prog="tcx connectors scaffold")
        parser.add_argument("template")
        parser.add_argument("--broker-id", required=True)
        parser.add_argument("--credential-ref", default="")
        parser.add_argument("--environment", default="")
        args = parser.parse_args(argv[1:])
        result = brokers.scaffold_broker_connector(
            root,
            {
                "template": args.template,
                "broker_id": args.broker_id,
                "credential_ref": args.credential_ref,
                "environment": args.environment,
                "principal_id": "head-manager",
            },
        )
        print(json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True))
        return
    if sub == "register":
        parser = argparse.ArgumentParser(prog="tcx connectors register")
        parser.add_argument("template")
        parser.add_argument("--broker-id", required=True)
        parser.add_argument("--credential-ref", required=True)
        parser.add_argument("--environment", default="")
        args = parser.parse_args(argv[1:])
        result = brokers.register_broker_connector(
            root,
            {
                "template": args.template,
                "broker_id": args.broker_id,
                "credential_ref": args.credential_ref,
                "environment": args.environment,
                "principal_id": "head-manager",
            },
        )
        print(json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True))
        return
    if sub == "validate":
        parser = argparse.ArgumentParser(prog="tcx connectors validate")
        parser.add_argument("broker_id")
        args = parser.parse_args(argv[1:])
        result = brokers.validate_broker_connector_build(root, {"broker_id": args.broker_id, "principal_id": "head-manager"})
        print(json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True))
        return
    raise ValueError(
        "Usage: tcx connectors status\n"
        "       tcx connectors scaffold <template-or-alias> --broker-id <id> [--credential-ref <ref>] [--environment <env>]\n"
        "       tcx connectors register <template-or-alias> --broker-id <id> --credential-ref <ref> [--environment <env>]\n"
        "       tcx connectors validate <broker-id>"
    )

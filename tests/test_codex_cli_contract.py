from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


SCRIPT = Path(__file__).with_name("codex_cli_contract.py")
SPEC = importlib.util.spec_from_file_location("tradingcodex_codex_cli_contract", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
contract = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(contract)


def _hook_result(workspace: Path, *, trust_status: str = "trusted") -> dict:
    source = str((workspace / ".codex" / "hooks.json").resolve())
    return {
        "data": [
            {
                "cwd": str(workspace),
                "hooks": [
                    {
                        "eventName": event,
                        "enabled": True,
                        "trustStatus": trust_status,
                        "sourcePath": source,
                    }
                    for event in sorted(contract.REQUIRED_PROJECT_HOOK_EVENTS)
                ],
                "warnings": [],
                "errors": [],
            }
        ]
    }


def test_project_hook_contract_accepts_all_persistently_trusted_hooks(
    tmp_path: Path,
) -> None:
    result = contract._validate_project_hooks(_hook_result(tmp_path), tmp_path)

    assert result["status"] == "trusted"
    assert result["count"] == 8
    assert set(result["events"]) == contract.REQUIRED_PROJECT_HOOK_EVENTS


def test_project_hook_contract_rejects_one_run_bypass_posture(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="not persistently trusted"):
        contract._validate_project_hooks(
            _hook_result(tmp_path, trust_status="untrusted"),
            tmp_path,
        )


def test_project_hook_contract_rejects_missing_lifecycle_event(tmp_path: Path) -> None:
    result = _hook_result(tmp_path)
    result["data"][0]["hooks"] = [
        hook
        for hook in result["data"][0]["hooks"]
        if hook["eventName"] != "subagentStop"
    ]

    with pytest.raises(SystemExit, match="subagentStop"):
        contract._validate_project_hooks(result, tmp_path)

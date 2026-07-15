from __future__ import annotations

import argparse
import json
import queue
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from packaging.version import Version

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tradingcodex_service.application.agents import (
    MINIMUM_CODEX_VERSION,
    REFERENCE_CODEX_VERSION,
)


REQUIRED_DOCTOR_CHECKS = (
    "config.load",
    "mcp.config",
    "sandbox.helpers",
)
REQUIRED_FEATURE_STATES = {
    "computer_use": False,
    "hooks": True,
    "multi_agent": True,
    "multi_agent_v2": True,
    "network_proxy": True,
    "unified_exec": False,
}
REQUIRED_PROJECT_HOOK_EVENTS = {
    "permissionRequest",
    "postToolUse",
    "preToolUse",
    "sessionStart",
    "stop",
    "subagentStart",
    "subagentStop",
    "userPromptSubmit",
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--codex", default="codex")
    parser.add_argument("--require-reference", action="store_true")
    parser.add_argument("--require-hook-trust", action="store_true")
    args = parser.parse_args()

    workspace = args.workspace.expanduser().resolve()
    if not (workspace / ".codex" / "config.toml").is_file():
        raise SystemExit(f"generated Codex config is missing: {workspace}")

    version_result = _run([args.codex, "--version"])
    match = re.search(r"\bcodex-cli\s+([^\s]+)", version_result.stdout)
    if version_result.returncode != 0 or not match:
        raise SystemExit("unable to read `codex --version`")
    installed = match.group(1)
    if Version(installed) < Version(MINIMUM_CODEX_VERSION):
        raise SystemExit(
            f"Codex {installed} is older than required {MINIMUM_CODEX_VERSION}"
        )
    if args.require_reference and Version(installed) != Version(REFERENCE_CODEX_VERSION):
        raise SystemExit(
            f"Codex reference validation requires {REFERENCE_CODEX_VERSION}, got {installed}"
        )

    project_trust = (
        f"projects={{{json.dumps(str(workspace))}={{trust_level=\"trusted\"}}}}"
    )
    features_result = _run(
        [args.codex, "-c", project_trust, "-C", str(workspace), "features", "list"]
    )
    if features_result.returncode != 0:
        raise SystemExit(features_result.stderr.strip() or "Codex feature inspection failed")
    available_features = set(_feature_states(features_result.stdout))
    missing_features = sorted(set(REQUIRED_FEATURE_STATES) - available_features)
    if missing_features:
        raise SystemExit(f"Codex feature registry is missing: {', '.join(missing_features)}")

    doctor_result = _run(
        [
            args.codex,
            "--strict-config",
            "-c",
            project_trust,
            "-C",
            str(workspace),
            "doctor",
            "--json",
        ]
    )
    try:
        report = json.loads(doctor_result.stdout)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Codex doctor did not return JSON: {exc}") from exc
    checks = report.get("checks") if isinstance(report, dict) else None
    if not isinstance(checks, dict):
        raise SystemExit("Codex doctor JSON has no checks object")
    check_states = {
        name: str((checks.get(name) or {}).get("status") or "missing")
        for name in REQUIRED_DOCTOR_CHECKS
    }
    failed_checks = {name: status for name, status in check_states.items() if status != "ok"}
    if failed_checks:
        raise SystemExit(
            f"Codex strict config preflight failed: {json.dumps(failed_checks, sort_keys=True)}"
        )
    config_details = (checks.get("config.load") or {}).get("details") or {}
    enabled_features = {
        item.strip()
        for item in str(config_details.get("enabled feature flags") or "").split(",")
        if item.strip()
    }
    features = {name: name in enabled_features for name in REQUIRED_FEATURE_STATES}
    feature_errors = {
        name: {"expected": expected, "actual": features.get(name)}
        for name, expected in REQUIRED_FEATURE_STATES.items()
        if features.get(name) is not expected
    }
    if feature_errors:
        raise SystemExit(f"Codex feature contract mismatch: {json.dumps(feature_errors, sort_keys=True)}")

    hook_report: dict[str, Any] | None = None
    if args.require_hook_trust:
        hook_report = _inspect_project_hook_trust(
            args.codex,
            workspace,
            project_trust,
        )

    print(
        json.dumps(
            {
                "status": "pass",
                "codex_version": installed,
                "minimum_codex_version": MINIMUM_CODEX_VERSION,
                "reference_codex_version": REFERENCE_CODEX_VERSION,
                "reference_match": Version(installed) == Version(REFERENCE_CODEX_VERSION),
                "features": {
                    name: features[name] for name in sorted(REQUIRED_FEATURE_STATES)
                },
                "doctor_checks": check_states,
                "project_hooks": hook_report or {"status": "not_checked"},
            },
            indent=2,
            sort_keys=True,
        )
    )


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        text=True,
        capture_output=True,
        timeout=90,
        check=False,
    )


def _feature_states(output: str) -> dict[str, bool]:
    states: dict[str, bool] = {}
    for line in output.splitlines():
        fields = line.split()
        if len(fields) < 3 or fields[-1] not in {"true", "false"}:
            continue
        states[fields[0]] = fields[-1] == "true"
    return states


def _inspect_project_hook_trust(
    codex: str,
    workspace: Path,
    project_trust: str,
) -> dict[str, Any]:
    process = subprocess.Popen(
        [codex, "--strict-config", "-c", project_trust, "app-server"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        bufsize=1,
    )
    if process.stdin is None or process.stdout is None:
        process.kill()
        raise SystemExit("unable to open Codex app-server pipes for hook inspection")

    output: queue.Queue[str] = queue.Queue()

    def read_stdout() -> None:
        assert process.stdout is not None
        for line in process.stdout:
            output.put(line)

    threading.Thread(target=read_stdout, daemon=True).start()
    try:
        _write_rpc(
            process,
            {
                "method": "initialize",
                "id": 0,
                "params": {
                    "clientInfo": {
                        "name": "tradingcodex_compat_audit",
                        "title": "TradingCodex compatibility audit",
                        "version": "1.0.0",
                    },
                    "capabilities": {"experimentalApi": True},
                },
            },
        )
        _wait_rpc(output, process, request_id=0)
        _write_rpc(process, {"method": "initialized", "params": {}})
        _write_rpc(
            process,
            {
                "method": "hooks/list",
                "id": 1,
                "params": {"cwds": [str(workspace)]},
            },
        )
        response = _wait_rpc(output, process, request_id=1)
    finally:
        try:
            process.stdin.close()
        except OSError:
            pass
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)

    if response.get("error"):
        raise SystemExit(
            f"Codex hooks/list failed: {json.dumps(response['error'], sort_keys=True)}"
        )
    return _validate_project_hooks(response.get("result"), workspace)


def _write_rpc(process: subprocess.Popen[str], payload: dict[str, Any]) -> None:
    if process.stdin is None:
        raise SystemExit("Codex app-server stdin closed during hook inspection")
    process.stdin.write(json.dumps(payload, separators=(",", ":")) + "\n")
    process.stdin.flush()


def _wait_rpc(
    output: queue.Queue[str],
    process: subprocess.Popen[str],
    *,
    request_id: int,
    timeout: float = 15,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None and output.empty():
            raise SystemExit(
                f"Codex app-server exited before hooks inspection completed: {process.returncode}"
            )
        try:
            line = output.get(timeout=min(0.25, max(0.01, deadline - time.monotonic())))
        except queue.Empty:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and payload.get("id") == request_id:
            return payload
    raise SystemExit(f"Codex app-server request {request_id} timed out")


def _validate_project_hooks(result: Any, workspace: Path) -> dict[str, Any]:
    data = result.get("data") if isinstance(result, dict) else None
    if not isinstance(data, list) or len(data) != 1 or not isinstance(data[0], dict):
        raise SystemExit("Codex hooks/list returned an unexpected result")
    entry = data[0]
    errors = entry.get("errors")
    if isinstance(errors, list) and errors:
        raise SystemExit(f"Codex project hook discovery failed: {json.dumps(errors)}")

    expected_source = (workspace / ".codex" / "hooks.json").resolve()
    project_hooks = []
    for hook in entry.get("hooks") or []:
        if not isinstance(hook, dict):
            continue
        source = hook.get("sourcePath")
        if isinstance(source, str) and Path(source).resolve() == expected_source:
            project_hooks.append(hook)

    events = {
        str(hook.get("eventName"))
        for hook in project_hooks
        if hook.get("enabled") is True
    }
    missing = sorted(REQUIRED_PROJECT_HOOK_EVENTS - events)
    if missing:
        raise SystemExit(
            f"Codex project hook contract is missing enabled events: {', '.join(missing)}"
        )
    untrusted = sorted(
        str(hook.get("eventName"))
        for hook in project_hooks
        if hook.get("eventName") in REQUIRED_PROJECT_HOOK_EVENTS
        and hook.get("trustStatus") != "trusted"
    )
    if untrusted:
        raise SystemExit(
            "Codex project hooks are not persistently trusted: "
            f"{', '.join(untrusted)}. Open the generated workspace in interactive Codex, "
            "review and trust its hooks, then rerun. "
            "--dangerously-bypass-hook-trust is not accepted for lifecycle validation."
        )
    return {
        "status": "trusted",
        "count": len(project_hooks),
        "events": sorted(events),
    }


if __name__ == "__main__":
    try:
        main()
    except subprocess.TimeoutExpired as exc:
        print(f"Codex CLI contract timed out: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

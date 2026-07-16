from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tomllib
from importlib.metadata import PackageNotFoundError, version as distribution_version
from pathlib import Path
from typing import Any

from packaging.version import InvalidVersion, Version

from tradingcodex_service.application.agents import (
    AGENT_SPECS,
    EXPECTED_SKILLS,
    EXPECTED_SUBAGENTS,
    MINIMUM_CODEX_VERSION,
    MODEL_POLICY_MANIFEST_PATH,
    REFERENCE_CODEX_VERSION,
    SKILL_SPECS,
    build_projection_state,
    inspect_skill_projection,
    resolve_agent_model_policy,
)
from tradingcodex_service.application.runtime import (
    assert_runtime_home_outside_workspace,
    read_workspace_manifest,
    ensure_runtime_database,
    runtime_home_status,
    tradingcodex_db_path,
)
from tradingcodex_service.application.common import paths_equivalent
from tradingcodex_service.application.workspace_git import (
    gitignore_contract_status,
    workspace_git_status,
)
from tradingcodex_service.application.postmortems import verified_lesson_records
from tradingcodex_cli.commands.utils import (
    list_subagents,
    path_check,
    read_thread_policy,
    text_check,
)
from tradingcodex_cli.generator import (
    generated_python_path_is_ephemeral,
    workspace_scratch_permission_aliases,
)
from tradingcodex_service.version import TRADINGCODEX_VERSION

def doctor(root: Path, layer: str, *, verbose: bool = False) -> None:
    allowed = {"all", "codex-native", "guidance", "enforcement", "information-barrier", "improvement", "mcp", "service"}
    if layer not in allowed:
        raise ValueError(f'unknown layer "{layer}"')
    checks = [*_central_service_checks(root)]
    layer_checks = {
        "guidance": _guidance_checks,
        "enforcement": _enforcement_checks,
        "information-barrier": _information_barrier_checks,
        "improvement": _improvement_checks,
        "mcp": _mcp_checks,
    }
    if layer in {"all", "codex-native"}:
        for build_checks in layer_checks.values():
            checks.extend(build_checks(root))
    elif layer != "service":
        checks.extend(layer_checks[layer](root))
    checks = [
        check
        for check in checks
        if layer == "all"
        or check["layer"] == layer
        or (layer == "codex-native" and check.get("codexNative"))
        or (check.get("globalPreflight") and not check["ok"])
    ]
    failed = sum(
        1 for check in checks if not check["ok"] and not check.get("warn")
    )
    print("TradingCodex Harness\n")
    if verbose:
        for check in checks:
            _print_check(check)
    else:
        _print_check_summary(checks)
        issues = [
            check for check in checks if check.get("warn") or not check["ok"]
        ]
        if issues:
            print("\nAttention:")
            for check in issues:
                _print_check(check)
        print("\nRun the workspace launcher with `doctor --verbose` for every check.")
    if failed:
        print(f"TradingCodex doctor failed: {failed} check(s) failed", file=sys.stderr)
        sys.exit(1)
    print("\nTradingCodex doctor passed")


def _check_status(check: dict[str, Any]) -> str:
    return "WARN" if check.get("warn") else "PASS" if check["ok"] else "FAIL"


def _print_check(check: dict[str, Any]) -> None:
    status = _check_status(check)
    print(
        f"{status.ljust(4)} {check['layer'].ljust(20)} "
        f"{check['name']} - {check['detail']}"
    )


def _print_check_summary(checks: list[dict[str, Any]]) -> None:
    layers: dict[str, list[dict[str, Any]]] = {}
    for check in checks:
        layers.setdefault(str(check["layer"]), []).append(check)
    for layer, layer_items in layers.items():
        passed = sum(1 for check in layer_items if _check_status(check) == "PASS")
        warned = sum(1 for check in layer_items if _check_status(check) == "WARN")
        failed = sum(1 for check in layer_items if _check_status(check) == "FAIL")
        status = "FAIL" if failed else "WARN" if warned else "PASS"
        detail = f"{passed} passed"
        if warned:
            detail += f", {warned} warning(s)"
        if failed:
            detail += f", {failed} failed"
        print(f"{status.ljust(4)} {layer.ljust(20)} {detail}")

def _guidance_checks(root: Path) -> list[dict[str, Any]]:
    thread_policy = read_thread_policy(root)
    roster_size = len(list_subagents(root))
    return [
        path_check(root, "guidance", "AGENTS.md installed", "AGENTS.md", True),
        _codex_cli_runtime_check(),
        text_check(root, "guidance", "head-manager model instructions file configured", ".codex/config.toml", 'model_instructions_file = "prompts/base_instructions/head-manager.md"', True),
        text_check(root, "guidance", "head-manager instructions installed", ".codex/prompts/base_instructions/head-manager.md", "You are the `head-manager` agent", True),
        *_launcher_checks(root),
        text_check(root, "guidance", "hooks configured", ".codex/hooks.json", "\"PreToolUse\"", True),
        text_check(root, "guidance", "session context configured", ".codex/hooks/tradingcodex_hook.py", "tradingcodex-session-context", True),
        text_check(root, "guidance", "three-plane routing configured", ".codex/prompts/base_instructions/head-manager.md", "TradingCodex has three planes", True),
        text_check(root, "guidance", "build gate configured", ".codex/prompts/base_instructions/head-manager.md", "Use `$tcx-build` only when it is the first meaningful invocation", True),
        text_check(root, "guidance", "brain management gate configured", ".agents/skills/tcx-brain/SKILL.md", "Require `$tcx-brain` on the first meaningful line", True),
        text_check(root, "guidance", "strategy management gate configured", ".agents/skills/tcx-strategy/SKILL.md", "`$tcx-strategy` on its first meaningful line", True),
        text_check(root, "guidance", "research profile keeps runtime state denied", ".codex/config.toml", '".tradingcodex" = "deny"', True),
        text_check(root, "guidance", "strategy lifecycle MCP configured", ".codex/config.toml", '"manage_strategy"', True),
        text_check(root, "guidance", "brain lifecycle MCP configured", ".codex/config.toml", '"manage_investment_brain"', True),
        text_check(root, "guidance", "compact context discipline configured", ".codex/prompts/base_instructions/head-manager.md", "# Context Discipline", True),
        {"layer": "guidance", "name": "subagent scheduler ceiling is independent of roster", "ok": 1 < thread_policy["max_threads"] < roster_size, "codexNative": True, "detail": f"v2_session_threads={thread_policy['max_concurrent_threads_per_session']}, child_threads={thread_policy['max_threads']}, subagents={roster_size}"},
        {"layer": "guidance", "name": "subagent recursion remains disabled", "ok": thread_policy["max_depth"] == 1, "codexNative": True, "detail": f"max_depth={thread_policy['max_depth']}"},
        *_fixed_role_dispatch_checks(root),
        *_model_policy_checks(root),
    ]


def _codex_cli_runtime_check() -> dict[str, Any]:
    executable = shutil.which("codex")
    if not executable:
        return {
            "layer": "guidance",
            "name": "Codex CLI reference version",
            "ok": False,
            "warn": True,
            "codexNative": True,
            "detail": f"not found on PATH; required>={MINIMUM_CODEX_VERSION}, reference={REFERENCE_CODEX_VERSION}",
        }
    try:
        result = subprocess.run(
            [executable, "--version"],
            text=True,
            capture_output=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            "layer": "guidance",
            "name": "Codex CLI reference version",
            "ok": False,
            "warn": True,
            "codexNative": True,
            "detail": f"unable to inspect {executable}: {exc}",
        }
    output = " ".join(part.strip() for part in (result.stdout, result.stderr) if part.strip())
    match = re.search(r"\bcodex-cli\s+([^\s]+)", output)
    if result.returncode != 0 or not match:
        return {
            "layer": "guidance",
            "name": "Codex CLI reference version",
            "ok": False,
            "warn": True,
            "codexNative": True,
            "detail": f"unrecognized `codex --version` output from {executable}: {output or 'empty'}",
        }
    installed = match.group(1)
    try:
        parsed = Version(installed)
        minimum = Version(MINIMUM_CODEX_VERSION)
        reference = Version(REFERENCE_CODEX_VERSION)
    except InvalidVersion:
        return {
            "layer": "guidance",
            "name": "Codex CLI reference version",
            "ok": False,
            "warn": True,
            "codexNative": True,
            "detail": f"non-PEP-440 Codex version: {installed}",
        }
    compatible = parsed >= minimum
    differs_from_reference = parsed != reference
    reference_note = ""
    if not compatible:
        reference_note = "; older than required; upgrade Codex before using the generated harness"
    elif parsed < reference:
        reference_note = "; compatible but older than reference; upgrade before release validation"
    elif parsed > reference:
        reference_note = "; newer client requires harness revalidation"
    return {
        "layer": "guidance",
        "name": "Codex CLI reference version",
        "ok": compatible,
        "warn": compatible and differs_from_reference,
        "codexNative": True,
        "detail": (
            f"installed={installed}, required>={MINIMUM_CODEX_VERSION}, "
            f"reference={REFERENCE_CODEX_VERSION}"
            + reference_note
        ),
    }


def _launcher_checks(root: Path) -> list[dict[str, Any]]:
    unix_launcher = root / "tcx"
    windows_launcher = root / "tcx.cmd"
    active = windows_launcher if os.name == "nt" else unix_launcher
    active_ok = active.is_file() and (os.name == "nt" or os.access(active, os.X_OK))
    return [
        {
            "layer": "guidance",
            "name": "native workspace launcher",
            "ok": active_ok,
            "codexNative": True,
            "detail": str(active),
        },
        {
            "layer": "guidance",
            "name": "cross-platform launcher pair",
            "ok": unix_launcher.is_file() and windows_launcher.is_file(),
            "codexNative": True,
            "detail": "tcx + tcx.cmd" if unix_launcher.is_file() and windows_launcher.is_file() else "missing tcx or tcx.cmd",
        },
    ]


def _model_policy_checks(root: Path) -> list[dict[str, Any]]:
    manifest_path = root / MODEL_POLICY_MANIFEST_PATH
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return [{"layer": "guidance", "name": "runtime model policy manifest", "ok": False, "codexNative": True, "detail": str(exc)}]
    roles = manifest.get("roles") if isinstance(manifest.get("roles"), dict) else {}
    checks = [{
        "layer": "guidance",
        "name": "runtime model policy manifest",
        "ok": set(roles) == set(AGENT_SPECS),
        "codexNative": True,
        "detail": f"roles={len(roles)}, expected={len(AGENT_SPECS)}",
    }]
    comparison_refs = {
        str(policy.get("evaluation_comparison_ref") or "")
        for policy in roles.values()
        if isinstance(policy, dict)
    }
    comparison_ready = len(comparison_refs) == 1 and "" not in comparison_refs
    checks.append({
        "layer": "guidance",
        "name": "role-model paired evaluation promotion",
        "ok": comparison_ready,
        "warn": not comparison_ready,
        "codexNative": True,
        "detail": next(iter(comparison_refs)) if comparison_ready else "active-but-unpromoted: no paired evaluation comparison reference",
    })
    for role in AGENT_SPECS:
        policy = roles.get(role) if isinstance(roles.get(role), dict) else resolve_agent_model_policy(role)
        config_path = root / (".codex/config.toml" if role == "head-manager" else f".codex/agents/{role}.toml")
        try:
            config = tomllib.loads(config_path.read_text(encoding="utf-8"))
        except Exception as exc:
            checks.append({"layer": "guidance", "name": f"runtime model policy: {role}", "ok": False, "codexNative": True, "detail": str(exc)})
            continue
        ok = config.get("model") == policy["resolved_model"] and config.get("model_reasoning_effort") == policy["reasoning_effort"]
        checks.append({"layer": "guidance", "name": f"runtime model policy: {role}", "ok": ok, "codexNative": True, "detail": f"{config.get('model')}/{config.get('model_reasoning_effort')} ({policy['support_status']})"})
    return checks


def _fixed_role_dispatch_checks(root: Path) -> list[dict[str, Any]]:
    config_path = root / ".codex" / "config.toml"
    try:
        config = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return [{
            "layer": "guidance",
            "name": "exact fixed-role dispatch configuration",
            "ok": False,
            "codexNative": True,
            "detail": str(exc),
        }]
    features = config.get("features") if isinstance(config.get("features"), dict) else {}
    multi_agent_v2 = features.get("multi_agent_v2") if isinstance(features.get("multi_agent_v2"), dict) else {}
    exact_runtime = (
        features.get("multi_agent") is True
        and multi_agent_v2.get("enabled") is True
        and multi_agent_v2.get("max_concurrent_threads_per_session") == 7
        and multi_agent_v2.get("hide_spawn_agent_metadata") is False
        and multi_agent_v2.get("tool_namespace") == "agents"
        and "max_threads" not in (config.get("agents") or {})
    )
    prompt_path = root / ".codex" / "prompts" / "base_instructions" / "head-manager.md"
    prompt = prompt_path.read_text(encoding="utf-8") if prompt_path.is_file() else ""
    dispatch_contract = all(
        marker in prompt
        for marker in (
            "exact `agent_type`",
            "Do not use a generic/default agent",
            "waiting_for_subagent_dispatch",
        )
    )
    return [
        {
            "layer": "guidance",
            "name": "exact fixed-role dispatch configuration",
            "ok": exact_runtime,
            "codexNative": True,
            "detail": (
                f"multi_agent={features.get('multi_agent')}, "
                f"multi_agent_v2={multi_agent_v2.get('enabled')}, "
                f"session_threads={multi_agent_v2.get('max_concurrent_threads_per_session')}, "
                f"hide_spawn_agent_metadata={multi_agent_v2.get('hide_spawn_agent_metadata')}, "
                f"tool_namespace={multi_agent_v2.get('tool_namespace')}"
            ),
        },
        {
            "layer": "guidance",
            "name": "fixed-role dispatch fail-closed contract",
            "ok": dispatch_contract,
            "codexNative": True,
            "detail": "exact agent_type or waiting; no generic role emulation" if dispatch_contract else "missing exact-dispatch fail-closed instructions",
        },
    ]


def _central_service_checks(root: Path) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = [*_version_checks(root), *_workspace_git_checks(root)]
    try:
        home_status = runtime_home_status()
        assert_runtime_home_outside_workspace(root, str(home_status["home"]))
    except Exception as exc:
        detail = str(exc)
        checks.extend(
            (
                {
                    "layer": "service",
                    "name": "global home selection",
                    "ok": False,
                    "codexNative": False,
                    "globalPreflight": True,
                    "detail": detail,
                },
                {
                    "layer": "service",
                    "name": "runtime home is outside workspace",
                    "ok": False,
                    "codexNative": True,
                    "globalPreflight": True,
                    "detail": detail,
                },
            )
        )
        return checks
    checks.append({
        "layer": "service",
        "name": "global home selection",
        "ok": True,
        "codexNative": False,
        "globalPreflight": True,
        "detail": f"{home_status['home']} ({home_status['home_source']})",
    })
    checks.append({
        "layer": "service",
        "name": "runtime home is outside workspace",
        "ok": True,
        "codexNative": True,
        "globalPreflight": True,
        "detail": f"workspace={root}, home={home_status['home']}",
    })
    try:
        module_lock = json.loads((root / ".tradingcodex" / "generated" / "module-lock.json").read_text(encoding="utf-8"))
        projected_home = str(module_lock.get("tradingcodex_home") or "")
        projected_source = str(module_lock.get("home_source") or "")
        projected_db = str(module_lock.get("tradingcodex_db_path") or "")
        projected_db_source = str(module_lock.get("db_source") or "")
        home_source_ok = projected_source == home_status["home_source"] or home_status["home_source"] == "environment_override"
        db_source_ok = projected_db_source == home_status["db_source"] or home_status["db_source"] == "environment_override"
        projection_ok = (
            paths_equivalent(projected_home, str(home_status["home"]))
            and home_source_ok
            and paths_equivalent(projected_db, str(home_status["db_path"]))
            and db_source_ok
        )
        projection_detail = (
            f"home={projected_home or 'missing'} ({projected_source or 'missing'}), "
            f"db={projected_db or 'missing'} ({projected_db_source or 'missing'})"
        )
    except Exception as exc:
        projection_ok = False
        projection_detail = str(exc)
    checks.append({
        "layer": "service",
        "name": "generated home/DB projection matches runtime",
        "ok": projection_ok,
        "codexNative": True,
        "globalPreflight": True,
        "detail": projection_detail,
    })
    if not projection_ok:
        return checks
    try:
        ensure_runtime_database(root)
        db_path = tradingcodex_db_path()
        checks.append({"layer": "service", "name": "central DB reachable", "ok": db_path.exists(), "codexNative": False, "detail": str(db_path)})
        checks.append({
            "layer": "service",
            "name": "workspace root is provenance only",
            "ok": db_path != (root / ".tradingcodex" / "state" / "tradingcodex.sqlite3").resolve(),
            "codexNative": False,
            "detail": f"workspace={root}",
        })
        manifest = read_workspace_manifest(root)
        has_workspace_id = bool(str(manifest.get("workspace_id", "")).startswith("tcxw_"))
        checks.append({
            "layer": "service",
            "name": "workspace identity manifest installed",
            "ok": has_workspace_id,
            "warn": not has_workspace_id,
            "codexNative": False,
            "detail": str(manifest.get("workspace_id") or "missing .tradingcodex/workspace.json"),
        })
        has_profile = bool((manifest.get("active_profile") or {}).get("portfolio_id"))
        checks.append({
            "layer": "service",
            "name": "paper account scope configured",
            "ok": has_profile,
            "warn": not has_profile,
            "codexNative": False,
            "detail": (manifest.get("active_profile") or {}).get("label", "missing paper account scope"),
        })
        from apps.mcp.models import McpToolCall
        from tradingcodex_service.application.health import readiness_payload

        McpToolCall.objects.count()
        checks.append({"layer": "service", "name": "central MCP ledger reachable", "ok": True, "codexNative": False, "detail": "McpToolCall table available"})
        readiness = readiness_payload()
        checks.append({
            "layer": "service",
            "name": "service readiness contract",
            "ok": readiness["ready"],
            "codexNative": False,
            "detail": "ready" if readiness["ready"] else ", ".join(readiness["reason_codes"]),
        })
    except Exception as exc:
        checks.append({"layer": "service", "name": "central DB reachable", "ok": False, "codexNative": False, "detail": str(exc)})
    export_dirs = ["trading/research", "trading/reports", "trading/audit"]
    for rel in export_dirs:
        path = root / rel
        checks.append({"layer": "service", "name": f"workspace export/cache writable: {rel}", "ok": path.exists() and os.access(path, os.W_OK), "codexNative": False, "detail": "writable" if path.exists() and os.access(path, os.W_OK) else "missing or not writable"})
    return checks


def _workspace_git_checks(root: Path) -> list[dict[str, Any]]:
    try:
        git = workspace_git_status(root)
    except Exception as exc:
        git = {
            "is_worktree": False,
            "git_root": "",
            "git_dirty": False,
        }
        git_detail = str(exc)
    else:
        git_detail = (
            f"root={git['git_root'] or 'missing'}, "
            f"workspace_dirty={str(bool(git['git_dirty'])).lower()}"
        )
    ignore = gitignore_contract_status(root)
    return [
        {
            "layer": "service",
            "name": "workspace Git worktree and dirty state",
            "ok": bool(git["is_worktree"]),
            "codexNative": True,
            "detail": git_detail,
        },
        {
            "layer": "service",
            "name": "workspace privacy-first Git ignore contract",
            "ok": bool(ignore["current"]),
            "codexNative": True,
            "detail": str(ignore["detail"]),
        },
    ]


def _version_checks(root: Path) -> list[dict[str, Any]]:
    source_root = Path(__file__).resolve().parents[2]
    if (source_root / "pyproject.toml").is_file():
        package_version = TRADINGCODEX_VERSION
    else:
        try:
            package_version = distribution_version("tradingcodex")
        except PackageNotFoundError:
            package_version = TRADINGCODEX_VERSION
    try:
        module_lock = json.loads((root / ".tradingcodex" / "generated" / "module-lock.json").read_text(encoding="utf-8"))
        workspace_version = str(module_lock.get("tradingcodex_version") or "")
    except Exception:
        workspace_version = ""
    return [
        {
            "layer": "service",
            "name": "package and runtime versions match",
            "ok": package_version == TRADINGCODEX_VERSION,
            "codexNative": False,
            "detail": f"package={package_version}, runtime={TRADINGCODEX_VERSION}",
        },
        {
            "layer": "service",
            "name": "workspace and runtime versions match",
            "ok": workspace_version == TRADINGCODEX_VERSION,
            "codexNative": False,
            "detail": f"workspace={workspace_version or 'missing'}, runtime={TRADINGCODEX_VERSION}",
        },
    ]


def _enforcement_checks(root: Path) -> list[dict[str, Any]]:
    schemas = ["research_artifact.schema.json", "evidence_pack.schema.json", "fundamental_report.schema.json", "technical_report.schema.json", "news_report.schema.json", "thesis.schema.json", "valuation.schema.json", "portfolio_review.schema.json", "risk_report.schema.json", "order_ticket.schema.json", "approval_receipt.schema.json", "execution_result.schema.json", "postmortem_report.schema.json", "audit_event.schema.json"]
    return [
        text_check(root, "enforcement", "command rules configured", ".codex/rules/tradingcodex.rules", "prefix_rule(", True),
        text_check(root, "enforcement", "lifecycle hooks enabled", ".codex/config.toml", "hooks = true", True),
        text_check(root, "enforcement", "unified exec disabled", ".codex/config.toml", "unified_exec = false", True),
        text_check(root, "enforcement", "interactive computer use disabled", ".codex/config.toml", "computer_use = false", True),
        text_check(root, "enforcement", "unified exec hook coverage", ".codex/hooks.json", ".*exec_command|.*write_stdin", True),
        *_codex_mcp_config_checks(root),
        path_check(root, "enforcement", "TradingCodex MCP installed", ".tradingcodex/mcp/server.py", False),
        {"layer": "enforcement", "name": "live broker disabled by default", "ok": not (root / ".tradingcodex" / "mcp" / "adapters" / "live.py").exists(), "detail": "no generated live adapter override; live provider gates remain service-controlled"},
        *[path_check(root, "enforcement", f"schema installed: {schema}", f".tradingcodex/schemas/{schema}", False) for schema in schemas],
    ]


def _codex_mcp_config_checks(root: Path) -> list[dict[str, Any]]:
    root_mcp = _read_codex_mcp_config(root / ".codex" / "config.toml")
    risk_mcp = _read_codex_mcp_config(root / ".codex" / "agents" / "risk-manager.toml")
    root_tools = set(root_mcp.get("enabled_tools") or [])
    risk_tools = set(risk_mcp.get("enabled_tools") or [])
    retired_execution_tools = {
        "submit_approved_order",
        "cancel_submitted_order",
        "refresh_broker_order_status",
    }
    execution_exposure = []
    role_mcp_configs: dict[str, dict[str, Any]] = {}
    for agent_path in sorted((root / ".codex" / "agents").glob("*.toml")):
        agent_mcp = _read_codex_mcp_config(agent_path)
        role_mcp_configs[agent_path.stem] = agent_mcp
        enabled = set(agent_mcp.get("enabled_tools") or [])
        exposed = retired_execution_tools & enabled
        if exposed:
            execution_exposure.append(f"{agent_path.stem}: {', '.join(sorted(exposed))}")
    root_exposed = retired_execution_tools & root_tools
    if root_exposed:
        execution_exposure.append(f"head-manager: {', '.join(sorted(root_exposed))}")
    workspace_binding_errors = sorted(
        role
        for role, config in {"head-manager": root_mcp, **role_mcp_configs}.items()
        if not Path(str(config.get("cwd") or "")).is_absolute()
        or not Path(str(config.get("env", {}).get("TRADINGCODEX_WORKSPACE_ROOT") or "")).is_absolute()
        or not paths_equivalent(str(config.get("cwd") or ""), root)
        or not paths_equivalent(str(config.get("env", {}).get("TRADINGCODEX_WORKSPACE_ROOT") or ""), root)
    )
    expected_mcp_args = ["-m", "tradingcodex_cli", "mcp", "stdio"]
    python_probe_cache: dict[tuple[str, str], bool] = {}

    def python_runtime_ready(config: dict[str, Any]) -> bool:
        command = str(config.get("command") or "")
        pythonpath = str(config.get("env", {}).get("PYTHONPATH") or "")
        key = (command, pythonpath)
        if key not in python_probe_cache:
            environment = os.environ.copy()
            environment.pop("PYTHONHOME", None)
            environment.pop("PYTHONPATH", None)
            if pythonpath:
                environment["PYTHONPATH"] = pythonpath
            try:
                probe = subprocess.run(
                    [
                        command,
                        "-c",
                        "import tradingcodex_cli.__main__, tradingcodex_service.mcp_runtime",
                    ],
                    cwd=str(config.get("cwd") or root),
                    env=environment,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=15,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired):
                python_probe_cache[key] = False
            else:
                python_probe_cache[key] = probe.returncode == 0
        return python_probe_cache[key]

    python_binding_errors = sorted(
        role
        for role, config in {"head-manager": root_mcp, **role_mcp_configs}.items()
        if not Path(str(config.get("command") or "")).is_absolute()
        or not Path(str(config.get("command") or "")).is_file()
        or generated_python_path_is_ephemeral(str(config.get("command") or ""))
        or config.get("args") != expected_mcp_args
        or not python_runtime_ready(config)
    )
    raw_broker_tools = {"place_order", "replace_order", "cancel_order", "withdraw", "transfer"}
    broker_connector_tools = {
        "list_broker_adapter_providers",
        "render_broker_connector_scaffold",
        "register_broker_connector",
        "validate_broker_connector_build",
        "get_broker_capability_profile",
        "get_broker_instrument_constraints",
        "preview_order_translation",
    }
    workflow_control_tools = {
        "begin_analysis_run",
    }
    return [
        {
            "layer": "enforcement",
            "name": "TradingCodex MCP root server configured",
            "ok": bool(root_mcp.get("enabled") is True and root_mcp.get("command") and root_mcp.get("args")),
            "codexNative": True,
            "detail": "enabled with command/args" if root_mcp else "missing mcp_servers.tradingcodex",
        },
        {
            "layer": "enforcement",
            "name": "TradingCodex MCP initialization is required for every role",
            "ok": root_mcp.get("required") is True
            and all(config.get("required") is True for config in role_mcp_configs.values()),
            "codexNative": True,
            "detail": "root and fixed roles fail closed when canonical MCP initialization fails"
            if root_mcp.get("required") is True
            and all(config.get("required") is True for config in role_mcp_configs.values())
            else "set mcp_servers.tradingcodex.required=true in root and fixed-role configs",
        },
        {
            "layer": "enforcement",
            "name": "TradingCodex MCP workspace binding configured",
            "ok": not workspace_binding_errors,
            "codexNative": True,
            "detail": "root and fixed-role MCP cwd/env bind to the launched workspace" if not workspace_binding_errors else f"invalid MCP workspace binding: {', '.join(workspace_binding_errors)}",
        },
        {
            "layer": "enforcement",
            "name": "TradingCodex MCP uses attached Python runtime",
            "ok": not python_binding_errors,
            "codexNative": True,
            "detail": "root and fixed-role MCP launch without a package-manager cache write" if not python_binding_errors else f"invalid MCP Python binding: {', '.join(python_binding_errors)}",
        },
        {
            "layer": "enforcement",
            "name": "TradingCodex MCP autostarts local service",
            "ok": root_mcp.get("env", {}).get("TRADINGCODEX_MCP_AUTOSTART_SERVICE") == "1",
            "codexNative": True,
            "detail": "MCP env enables viewer/service autostart" if root_mcp.get("env", {}).get("TRADINGCODEX_MCP_AUTOSTART_SERVICE") == "1" else "missing TRADINGCODEX_MCP_AUTOSTART_SERVICE=1",
        },
        {
            "layer": "enforcement",
            "name": "TradingCodex MCP safe tools auto-approved",
            "ok": root_mcp.get("default_tools_approval_mode") == "approve",
            "codexNative": True,
            "detail": "default tool approval is approve" if root_mcp.get("default_tools_approval_mode") == "approve" else "default tool approval should be approve",
        },
        {
            "layer": "enforcement",
            "name": "head-manager analysis run tool configured",
            "ok": workflow_control_tools.issubset(root_tools),
            "codexNative": True,
            "detail": "root allowlist includes begin_analysis_run" if workflow_control_tools.issubset(root_tools) else f"missing analysis-run tools: {', '.join(sorted(workflow_control_tools - root_tools))}",
        },
        {
            "layer": "enforcement",
            "name": "native execution mutations excluded from every MCP config",
            "ok": not execution_exposure,
            "codexNative": True,
            "detail": "submit, cancel, and broker status refresh are not projected as MCP tools" if not execution_exposure else "; ".join(execution_exposure),
        },
        {
            "layer": "enforcement",
            "name": "head-manager Codex capability inventory is read-only",
            "ok": "list_codex_capabilities" in root_tools,
            "codexNative": True,
            "detail": "root allowlist exposes the secret-free native capability inventory"
            if "list_codex_capabilities" in root_tools
            else "list_codex_capabilities is missing from the root allowlist",
        },
        {
            "layer": "enforcement",
            "name": "head-manager broker connector tools configured",
            "ok": broker_connector_tools.issubset(root_tools),
            "codexNative": True,
            "detail": "root allowlist includes native connector management tools" if broker_connector_tools.issubset(root_tools) else "missing native connector management tools",
        },
        {
            "layer": "enforcement",
            "name": "retired execution role is absent",
            "ok": not (root / ".codex" / "agents" / "execution-operator.toml").exists(),
            "codexNative": True,
            "detail": "execution-operator.toml is absent" if not (root / ".codex" / "agents" / "execution-operator.toml").exists() else "retired execution-operator.toml is still installed",
        },
        {
            "layer": "enforcement",
            "name": "fixed roles exclude raw broker MCP tools",
            "ok": all(raw_broker_tools.isdisjoint(set(config.get("enabled_tools") or [])) for config in role_mcp_configs.values()),
            "codexNative": True,
            "detail": "fixed-role configs expose no raw broker mutation tools",
        },
        {
            "layer": "enforcement",
            "name": "risk-manager MCP approval allowlist configured",
            "ok": "request_order_approval" in risk_tools and "submit_approved_order" not in risk_tools,
            "codexNative": True,
            "detail": "risk-manager can approve but not submit" if "request_order_approval" in risk_tools and "submit_approved_order" not in risk_tools else "risk-manager approval/submit allowlist mismatch",
        },
    ]


def _read_codex_mcp_config(path: Path) -> dict[str, Any]:
    try:
        parsed = tomllib.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return parsed.get("mcp_servers", {}).get("tradingcodex", {})


def _information_barrier_checks(root: Path) -> list[dict[str, Any]]:
    return [
        path_check(root, "information-barrier", "restricted list installed", ".tradingcodex/policies/restricted-list.yaml", False),
    ]


def _required_scratch_permission_paths(scratch: str) -> set[str]:
    required = {scratch} if scratch else set()
    if not scratch:
        return required
    workspace_id = Path(scratch).name
    required.update(
        str(alias)
        for alias in workspace_scratch_permission_aliases(workspace_id, scratch)
    )
    return required


def _improvement_checks(root: Path) -> list[dict[str, Any]]:
    checks = _skill_projection_checks(root)
    project_config = root / ".codex" / "config.toml"
    try:
        project_config_text = project_config.read_text(encoding="utf-8")
        project_config_data = tomllib.loads(project_config_text)
    except Exception:
        project_config_text = ""
        project_config_data = {}
    permissions = project_config_data.get("permissions", {})
    research = permissions.get("trading-research", {})
    build = permissions.get("trading-build", {})
    research_filesystem = research.get("filesystem", {})
    build_filesystem = build.get("filesystem", {})
    research_workspace = research_filesystem.get(":workspace_roots", {})
    build_workspace = build_filesystem.get(":workspace_roots", {})
    research_network = research.get("network", {})
    build_network = build.get("network", {})
    shell_environment = project_config_data.get("shell_environment_policy", {})
    shell_environment_set = shell_environment.get("set", {})
    scratch = shell_environment.get("set", {}).get("TRADINGCODEX_SCRATCH", "")
    scratch_aliases = _required_scratch_permission_paths(scratch)
    project_mcp = project_config_data.get("mcp_servers", {}).get("tradingcodex", {})
    service_home = project_mcp.get("env", {}).get("TRADINGCODEX_HOME", "")
    attached_python = project_mcp.get("command", "")
    attached_python_runtime = (
        str(Path(attached_python).expanduser().absolute().parent.parent)
        if attached_python
        else ""
    )
    configured_codex_home = str(os.environ.get("CODEX_HOME") or "").strip()
    active_codex_home = str(
        (
            Path(configured_codex_home).expanduser()
            if configured_codex_home
            else Path.home() / ".codex"
        ).resolve(strict=False)
    )
    active_codex_proxy = str(Path(active_codex_home) / "proxy")
    active_codex_standalone = str(Path(active_codex_home) / "packages" / "standalone")
    split_profile_ok = bool(scratch) and all(
        (
            project_config_data.get("default_permissions") == "trading-research",
            research.get("extends") == ":workspace",
            research_filesystem.get(scratch) == "write",
            all(research_filesystem.get(alias) == "write" for alias in scratch_aliases),
            research_filesystem.get(":tmpdir") == "deny",
            research_filesystem.get(":slash_tmp") == "deny",
            bool(service_home) and research_filesystem.get(service_home) == "deny",
            bool(attached_python) and research_filesystem.get(attached_python) == "deny",
            research_filesystem.get("~/.codex") == "deny",
            research_filesystem.get("~/.codex/proxy") == "read",
            research_filesystem.get("~/.codex/packages/standalone") == "read",
            research_filesystem.get(active_codex_home) == "deny",
            research_filesystem.get(active_codex_proxy) == "read",
            research_filesystem.get(active_codex_standalone) == "read",
            research_filesystem.get("~/.ssh") == "deny",
            research_filesystem.get("~/.gitconfig") == "deny",
            research_filesystem.get("~/.config/git/config") == "deny",
            research_filesystem.get("~/.curlrc") == "deny",
            research_filesystem.get("~/.wgetrc") == "deny",
            research_workspace.get(".") == "write",
            research_workspace.get(".git") == "read",
            research_workspace.get(".gitignore") == "read",
            research_workspace.get(".codex") == "deny",
            ".codex/proxy" not in research_workspace,
            research_workspace.get(".agents") == "read",
            research_workspace.get("AGENTS.md") == "read",
            research_workspace.get("tcx") == "read",
            research_workspace.get("tcx.cmd") == "read",
            research_workspace.get("trading") == "read",
            research_workspace.get("trading/research") == "deny",
            research_network.get("enabled") is True,
            research_network.get("mode") == "limited",
            research_network.get("allow_local_binding") is False,
            research_network.get("allow_upstream_proxy") is False,
            research_network.get("dangerously_allow_all_unix_sockets") is False,
            research_network.get("domains", {}).get("*") == "allow",
            build.get("extends") == ":workspace",
            build_filesystem.get(scratch) == "write",
            all(build_filesystem.get(alias) == "write" for alias in scratch_aliases),
            build_filesystem.get(":tmpdir") == "deny",
            build_filesystem.get(":slash_tmp") == "deny",
            bool(service_home) and build_filesystem.get(service_home) == "deny",
            bool(attached_python_runtime) and build_filesystem.get(attached_python_runtime) == "read",
            bool(attached_python) and build_filesystem.get(attached_python) == "read",
            build_filesystem.get("~/.codex") == "deny",
            build_filesystem.get("~/.codex/proxy") == "read",
            build_filesystem.get("~/.codex/packages/standalone") == "read",
            build_filesystem.get(active_codex_home) == "deny",
            build_filesystem.get(active_codex_proxy) == "read",
            build_filesystem.get(active_codex_standalone) == "read",
            build_filesystem.get("~/.ssh") == "deny",
            build_filesystem.get("~/.gitconfig") == "deny",
            build_filesystem.get("~/.config/git/config") == "deny",
            build_filesystem.get("~/.curlrc") == "deny",
            build_filesystem.get("~/.wgetrc") == "deny",
            build_workspace.get(".") == "write",
            build_workspace.get(".codex") == "deny",
            build_workspace.get(".tradingcodex/cli.py") == "read",
            build_workspace.get(".tradingcodex/workspace.json") == "read",
            build_workspace.get("trading/research") == "deny",
            build_network.get("enabled") is True,
            build_network.get("mode") == "full",
            build_network.get("allow_local_binding") is False,
            build_network.get("allow_upstream_proxy") is False,
            build_network.get("dangerously_allow_all_unix_sockets") is False,
            build_network.get("domains", {}).get("*") == "allow",
            project_config_data.get("features", {}).get("network_proxy") is True,
            shell_environment.get("inherit") == "core",
            shell_environment_set.get("TMPDIR") == scratch,
            shell_environment_set.get("TEMP") == scratch,
            shell_environment_set.get("TMP") == scratch,
            shell_environment_set.get("CURL_HOME") == os.devnull,
            shell_environment_set.get("WGETRC") == os.devnull,
            shell_environment_set.get("GIT_CONFIG_GLOBAL") == os.devnull,
            shell_environment_set.get("GIT_CONFIG_SYSTEM") == os.devnull,
            shell_environment_set.get("GIT_CONFIG_NOSYSTEM") == "1",
            shell_environment_set.get("GIT_TERMINAL_PROMPT") == "0",
            shell_environment_set.get("GCM_INTERACTIVE") == "Never",
            {
                "CURL_HOME",
                "WGETRC",
                "GIT_CONFIG_GLOBAL",
                "GIT_CONFIG_SYSTEM",
                "GIT_CONFIG_NOSYSTEM",
                "GIT_TERMINAL_PROMPT",
                "GCM_INTERACTIVE",
            }.issubset(set(shell_environment.get("include_only", []))),
            "TRADINGCODEX_HOME" not in shell_environment.get("include_only", []),
            "*TOKEN*" in shell_environment.get("exclude", []),
        )
    )
    checks.extend([
        text_check(root, "improvement", "native Research permission profile is default", ".codex/config.toml", 'default_permissions = "trading-research"', True),
        text_check(root, "improvement", "native Build permission profile is installed", ".codex/config.toml", "[permissions.trading-build.filesystem]", True),
        {
            "layer": "improvement",
            "name": "legacy sandbox mode is absent",
            "ok": "sandbox_mode" not in project_config_text,
            "codexNative": True,
            "detail": "custom permission profiles remain authoritative" if "sandbox_mode" not in project_config_text else "sandbox_mode overrides custom permission profiles",
        },
        {
            "layer": "improvement",
            "name": "native Research and Build authority is split",
            "ok": split_profile_ok,
            "codexNative": True,
            "detail": "Research uses read-only-method public networking; Build adds Git Smart HTTP transport while hooks retain credential-free fetch-only commands and shared sensitive denials" if split_profile_ok else "permission profile, public-network proxy, scratch, or shell-environment contract mismatch",
        },
    ])
    for subagent in EXPECTED_SUBAGENTS:
        checks.append(path_check(root, "improvement", f"subagent installed: {subagent}", f".codex/agents/{subagent}.toml", True))
        subagent_path = root / ".codex" / "agents" / f"{subagent}.toml"
        try:
            subagent_text = subagent_path.read_text(encoding="utf-8")
        except Exception:
            subagent_text = ""
        checks.append({
            "layer": "improvement",
            "name": f"subagent inherits native permission profile: {subagent}",
            "ok": bool(subagent_text) and "sandbox_mode" not in subagent_text,
            "codexNative": True,
            "detail": "inherits the parent Research permission profile" if subagent_text and "sandbox_mode" not in subagent_text else "legacy sandbox override present or role config missing",
        })
    for skill in EXPECTED_SKILLS:
        checks.append(path_check(root, "improvement", f"skill installed: {skill}", _skill_check_path(skill), False))
    checks.append(path_check(root, "improvement", "agent index projected", ".tradingcodex/generated/agent-index.json", False))
    checks.append(path_check(root, "improvement", "skill index projected", ".tradingcodex/generated/skill-index.json", False))
    checks.append(path_check(root, "improvement", "projection manifest projected", ".tradingcodex/generated/projection-manifest.json", False))
    checks.append(text_check(root, "improvement", "no-overlap handoff contract installed", ".codex/prompts/base_instructions/head-manager.md", "Never edit, wrap, or recreate another role's report", False))
    checks.append(text_check(root, "improvement", "decision quality review installed", ".agents/skills/tcx-workflow/SKILL.md", "Decision Quality Spine", False))
    checks.append(text_check(root, "improvement", "method profile routing installed", ".codex/prompts/base_instructions/head-manager.md", "listed-equity FCFF DCF", False))
    checks.append(text_check(root, "improvement", "Codex-native workflow skill installed", ".agents/skills/tcx-workflow/SKILL.md", "Reassess the workflow after each wave", False))
    checks.append(text_check(root, "improvement", "analysis run hook installed", ".codex/hooks/tradingcodex_hook.py", "begin_analysis_run", True))
    checks.append(text_check(root, "improvement", "native execution parser installed", ".codex/hooks/tradingcodex_hook.py", "parse_native_execution_invocation", True))
    checks.append(text_check(root, "improvement", "native submit skill is explicit only", ".agents/skills/tcx-order-submit/agents/openai.yaml", "allow_implicit_invocation: false", False))
    checks.append(text_check(root, "improvement", "native cancel skill is explicit only", ".agents/skills/tcx-order-cancel/agents/openai.yaml", "allow_implicit_invocation: false", False))
    checks.append({
        "layer": "improvement",
        "name": "retired execution skill absent",
        "ok": not (root / ".tradingcodex" / "subagents" / "skills" / "execution-operator" / "execute-paper-order" / "SKILL.md").exists(),
        "codexNative": True,
        "detail": "retired execute-paper-order skill is absent",
    })
    checks.append(text_check(root, "improvement", "run-specific workflow session map installed", ".codex/hooks/tradingcodex_hook.py", "session-workflow-runs.json", True))
    checks.append(text_check(root, "improvement", "artifact follow-up contract schema installed", ".tradingcodex/schemas/research_artifact.schema.json", "follow_up_requests", False))
    checks.append(text_check(root, "improvement", "artifact improve schema installed", ".tradingcodex/schemas/research_artifact.schema.json", "improvements", False))
    improve_ledger = root / ".tradingcodex" / "mainagent" / "improve.jsonl"
    if improve_ledger.exists():
        try:
            lesson_records = verified_lesson_records(root)
        except (OSError, ValueError) as exc:
            checks.append({
                "layer": "improvement",
                "name": "improve ledger integrity",
                "ok": False,
                "codexNative": True,
                "detail": str(exc),
            })
        else:
            checks.append({
                "layer": "improvement",
                "name": "improve ledger integrity",
                "ok": True,
                "codexNative": True,
                "detail": f"verified {len(lesson_records)} chained lesson event(s)",
            })
    else:
        checks.append({
            "layer": "improvement",
            "name": "improve ledger integrity",
            "ok": True,
            "warn": True,
            "codexNative": True,
            "detail": "no improve ledger until postmortem lessons are captured",
        })
    checks.append(path_check(root, "improvement", "forecast ledger directory installed", "trading/forecasts", False))
    checks.append(text_check(root, "improvement", "build skill installed", ".agents/skills/tcx-build/SKILL.md", "its first meaningful line", False))
    checks.append(text_check(root, "improvement", "brain skill uses direct managed turn", ".agents/skills/tcx-brain/SKILL.md", "do not wrap it in `$tcx-build`", False))
    checks.append(text_check(root, "improvement", "strategy skill uses direct managed turn", ".agents/skills/tcx-strategy/SKILL.md", "do not wrap it in `$tcx-build`", False))
    checks.append(text_check(root, "improvement", "strategy root skill config installed", ".codex/config.toml", "# BEGIN TradingCodex strategy skills", True))
    return checks


def _skill_projection_checks(root: Path) -> list[dict[str, Any]]:
    try:
        state = build_projection_state(root)
    except Exception as exc:
        return [{"layer": "improvement", "name": "skill projection inventory", "ok": False, "codexNative": True, "detail": str(exc)}]
    checks: list[dict[str, Any]] = []
    for role in AGENT_SPECS:
        name = "head-manager projected skills current" if role == "head-manager" else f"subagent projected skills current: {role}"
        try:
            projection = inspect_skill_projection(root, role, state)
            if projection["ok"]:
                detail = "enabled skill paths exactly match managed projection"
            else:
                detail = "; ".join(
                    f"{label}={projection[key]}"
                    for label, key in (
                        ("missing", "missing_paths"),
                        ("extra", "extra_paths"),
                        ("unregistered", "unregistered_paths"),
                        ("duplicates", "duplicate_paths"),
                    )
                    if projection[key]
                )
            checks.append({"layer": "improvement", "name": name, "ok": projection["ok"], "codexNative": True, "detail": detail})
        except Exception as exc:
            checks.append({"layer": "improvement", "name": name, "ok": False, "codexNative": True, "detail": str(exc)})
    collisions = state["host_global_skill_collisions"]
    checks.append({
        "layer": "improvement",
        "name": "host-global skill name collisions",
        "ok": not collisions,
        "codexNative": True,
        "detail": "no managed skill name collisions" if not collisions else "; ".join(f"{item['id']}: {item['resolved_source_file']}" for item in collisions),
    })
    return checks


def _skill_check_path(skill: str) -> str:
    spec = SKILL_SPECS[skill]
    if spec.scope == "subagent_shared":
        return f".tradingcodex/subagents/skills/shared/{skill}/SKILL.md"
    if spec.scope == "subagent_role":
        role = spec.owner_roles[0]
        return f".tradingcodex/subagents/skills/{role}/{skill}/SKILL.md"
    return f".agents/skills/{skill}/SKILL.md"


def _mcp_checks(root: Path) -> list[dict[str, Any]]:
    return [
        text_check(root, "mcp", "MCP server instructions installed", ".tradingcodex/mcp/server.py", "not a raw broker proxy or an execution-mutation surface", False),
    ]

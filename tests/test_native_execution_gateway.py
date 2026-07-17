from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tomllib
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest
from django.test import Client

from tradingcodex_cli.generator import bootstrap_workspace
from tradingcodex_service.application.execution_gateway import (
    NATIVE_CANCEL_ACTION,
    NATIVE_SUBMIT_ACTION,
    NATIVE_USER_PRINCIPAL_ID,
    NativeExecutionInvocationError,
    authorize_native_execution,
    parse_native_execution_invocation,
    project_native_execution_result,
    reserved_native_execution_token,
)
from tradingcodex_service.application.analysis_runs import begin_analysis_run
from tradingcodex_service.application.orders import cancel_submitted_order, submit_approved_order
from tradingcodex_service.application.runtime import ensure_runtime_database


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "native-execution-workspace"
    bootstrap_workspace(root)
    ensure_runtime_database(root)
    return root


def run_user_prompt_hook(
    workspace: Path,
    payload: dict[str, object],
) -> dict[str, object]:
    environment = {**os.environ, "PYTHONPATH": str(ROOT)}
    result = subprocess.run(
        [sys.executable, str(workspace / ".codex/hooks/tradingcodex_hook.py"), "user-prompt-submit"],
        cwd=workspace,
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        env=environment,
        timeout=30,
        check=True,
    )
    return json.loads(result.stdout)


def run_policy_hook(
    workspace: Path,
    event: str,
    *,
    tool_name: str,
    tool_input: dict[str, object],
) -> dict[str, object] | None:
    result = subprocess.run(
        [sys.executable, str(workspace / ".codex/hooks/tradingcodex_hook.py"), event],
        cwd=workspace,
        input=json.dumps({"tool_name": tool_name, "tool_input": tool_input}),
        text=True,
        capture_output=True,
        env={**os.environ, "PYTHONPATH": str(ROOT)},
        timeout=30,
        check=True,
    )
    return json.loads(result.stdout) if result.stdout.strip() else None


@pytest.mark.parametrize(
    ("prompt", "action", "broker_order_id", "live_confirmation"),
    [
        (
            "$tcx-order-submit --ticket-id ticket-1 --approval-receipt-id receipt-1",
            NATIVE_SUBMIT_ACTION,
            "",
            "",
        ),
        (
            "$tcx-order-submit --ticket-id ticket-1 --approval-receipt-id receipt-1 "
            "--live-confirmation LIVE:ticket-1:paper:AAPL:buy:1",
            NATIVE_SUBMIT_ACTION,
            "",
            "LIVE:ticket-1:paper:AAPL:buy:1",
        ),
        (
            "$tcx-order-cancel --ticket-id ticket-1 --broker-order-id broker-1 "
            "--approval-receipt-id receipt-1",
            NATIVE_CANCEL_ACTION,
            "broker-1",
            "",
        ),
        (
            "$tcx-order-cancel --ticket-id ticket-1 --broker-order-id broker-1 "
            "--approval-receipt-id receipt-1 --live-confirmation CANCEL:ticket-1:paper:broker-1",
            NATIVE_CANCEL_ACTION,
            "broker-1",
            "CANCEL:ticket-1:paper:broker-1",
        ),
    ],
)
def test_exact_native_invocations_create_workspace_bound_mandates(
    workspace: Path,
    prompt: str,
    action: str,
    broker_order_id: str,
    live_confirmation: str,
) -> None:
    mandate = parse_native_execution_invocation(prompt, workspace, invoked_at="2026-07-13T00:00:00Z")

    assert mandate is not None
    assert mandate.action == action
    assert mandate.broker_order_id == broker_order_id
    assert mandate.live_confirmation == live_confirmation
    assert mandate.service_arguments()["principal_id"] == NATIVE_USER_PRINCIPAL_ID
    assert mandate.workspace_id
    assert mandate.workspace_path_hash
    assert mandate.prompt_sha256
    assert "live_confirmation" not in mandate.audit_metadata()
    with pytest.raises(FrozenInstanceError):
        mandate.ticket_id = "changed"  # type: ignore[misc]


def test_native_invocation_accepts_formatting_and_exact_workspace_link(workspace: Path) -> None:
    target = workspace / ".agents/skills/tcx-order-submit/SKILL.md"
    prompt = (
        "\ufeff\u2028\t"
        f"[$tcx-order-submit]({str(target).replace('/', chr(92))}) "
        "--ticket-id ticket-1 --approval-receipt-id receipt-1 \n\n"
    )

    mandate = parse_native_execution_invocation(prompt, workspace)

    assert mandate is not None
    assert reserved_native_execution_token(prompt, workspace) == "$tcx-order-submit"
    assert mandate.prompt_sha256 == hashlib.sha256(prompt.encode()).hexdigest()


def test_native_invocation_rejects_mismatched_workspace_link(workspace: Path) -> None:
    prompt = (
        f"[$tcx-order-submit]({workspace / '.agents/skills/tcx-order-cancel/SKILL.md'}) "
        "--ticket-id ticket-1 --approval-receipt-id receipt-1"
    )

    with pytest.raises(NativeExecutionInvocationError, match="must target"):
        parse_native_execution_invocation(prompt, workspace)


@pytest.mark.parametrize(
    "prompt",
    [
        "$tcx-order-submit",
        "$tcx-order-submit --ticket-id ticket-1",
        "$tcx-order-submit --ticket-id ticket-1 --ticket-id ticket-2 --approval-receipt-id receipt-1",
        "$tcx-order-submit --ticket-id=ticket-1 --approval-receipt-id receipt-1",
        "$tcx-order-submit --ticket-id ticket-1 --approval-receipt-id receipt-1 --unknown value",
        "$tcx-order-submit ticket-1 --approval-receipt-id receipt-1",
        "$tcx-order-submit --ticket-id 'ticket-1' --approval-receipt-id receipt-1",
        "$tcx-order-submit --ticket-id ticket\\-1 --approval-receipt-id receipt-1",
        "$tcx-order-submit --ticket-id ticket-1\n--approval-receipt-id receipt-1",
        "$tcx-order-submit --ticket-id ticket-1 --approval-receipt-id receipt-1 extra",
        "$tcx-order-submit --ticket-id ticket-1 --approval-receipt-id receipt-1 "
        "$tcx-order-cancel --ticket-id ticket-1",
        "$tcx-order-submit --ticket-id $tcx-order-submit --approval-receipt-id receipt-1",
        "$tcx-order-submit --ticket-id ticket-1 --approval-receipt-id $tcx-order-cancel",
        "$tcx-order-cancel --ticket-id $tcx-build --broker-order-id broker-1 "
        "--approval-receipt-id receipt-1",
        "$tcx-order-cancel --ticket-id ticket-1 --broker-order-id $tcx-brain "
        "--approval-receipt-id receipt-1",
        "$tcx-order-cancel --ticket-id ticket-1 --broker-order-id broker-1 "
        "--approval-receipt-id $execute-paper-order",
        "$execute-paper-order --ticket-id ticket-1 --approval-receipt-id receipt-1",
    ],
)
def test_malformed_or_retired_native_invocations_are_rejected(workspace: Path, prompt: str) -> None:
    with pytest.raises(NativeExecutionInvocationError):
        parse_native_execution_invocation(prompt, workspace)


def test_free_form_mentions_do_not_create_execution_authority(workspace: Path) -> None:
    prompt = "Explain $tcx-order-submit but do not submit anything."

    assert reserved_native_execution_token(prompt) == ""
    assert parse_native_execution_invocation(prompt, workspace) is None
    assert parse_native_execution_invocation(
        "\u200b$tcx-order-submit --ticket-id ticket-1 --approval-receipt-id receipt-1",
        workspace,
    ) is None


@pytest.mark.parametrize(
    "prompt",
    [
        "$execute-approved-order --ticket-id ticket-1 --approval-receipt-id receipt-1",
        "$cancel-submitted-order --ticket-id ticket-1 --broker-order-id broker-1 "
        "--approval-receipt-id receipt-1",
        "$order-allow --mode paper\nSubmit the approved order.",
    ],
)
def test_legacy_pre_namespace_tokens_create_no_native_authority(
    workspace: Path,
    prompt: str,
) -> None:
    assert reserved_native_execution_token(prompt) == ""
    assert parse_native_execution_invocation(prompt, workspace) is None


def test_mandate_proof_binds_every_execution_field(workspace: Path) -> None:
    mandate = parse_native_execution_invocation(
        "$tcx-order-submit --ticket-id ticket-1 --approval-receipt-id receipt-1",
        workspace,
    )
    assert mandate is not None
    forged = replace(mandate, ticket_id="ticket-2")

    with pytest.raises(PermissionError, match="parser-issued"):
        authorize_native_execution(
            workspace,
            forged.service_arguments(),
            forged,
            NATIVE_SUBMIT_ACTION,
        )


def test_order_services_reject_calls_without_parser_mandate(workspace: Path) -> None:
    submit_args = {
        "principal_id": NATIVE_USER_PRINCIPAL_ID,
        "ticket_id": "ticket-1",
        "approval_receipt_id": "receipt-1",
    }
    cancel_args = {**submit_args, "broker_order_id": "broker-1"}

    with pytest.raises(PermissionError, match="parser-issued"):
        submit_approved_order(workspace, submit_args)
    with pytest.raises(PermissionError, match="parser-issued"):
        cancel_submitted_order(workspace, cancel_args)


def test_public_result_projection_drops_prompt_confirmation_and_provider_payload(workspace: Path) -> None:
    prompt = (
        "$tcx-order-submit --ticket-id ticket-1 --approval-receipt-id receipt-1 "
        "--live-confirmation LIVE:secret-like-confirmation"
    )
    mandate = parse_native_execution_invocation(prompt, workspace)
    assert mandate is not None

    projected = project_native_execution_result(
        mandate,
        {
            "status": "accepted",
            "ticket_id": "ticket-1",
            "adapter": "paper-trading",
            "result": {
                "broker_order_id": "broker-1",
                "status": "submitted",
                "credential": "must-not-leak",
                "raw_status": {"secret": "must-not-leak"},
            },
            "workspace_context": {},
        },
    )
    serialized = repr(projected)

    assert projected["status"] == "accepted"
    assert projected["db_canonical"] is False
    assert projected["broker_order_id"] == "broker-1"
    assert "LIVE:secret-like-confirmation" not in serialized
    assert "must-not-leak" not in serialized
    assert prompt not in serialized


def test_native_user_service_capabilities_and_retired_principal_cleanup(workspace: Path) -> None:
    from apps.policy.models import Capability, Principal
    from apps.policy.services import capability_check, sync_builtin_principals_and_capabilities

    retired, _ = Principal.objects.update_or_create(
        principal_id="execution-operator",
        defaults={"role": "execution-operator", "active": True},
    )
    Capability.objects.update_or_create(
        principal=retired,
        action="mcp.tradingcodex.submit_approved_order",
        resource_pattern="*",
        defaults={"effect": "allow"},
    )

    sync_builtin_principals_and_capabilities()

    retired.refresh_from_db()
    native = Principal.objects.get(principal_id=NATIVE_USER_PRINCIPAL_ID)
    assert retired.active is False
    assert not retired.capabilities.exists()
    assert native.role == "user"
    assert capability_check(NATIVE_USER_PRINCIPAL_ID, NATIVE_SUBMIT_ACTION)[0] is True
    assert capability_check(NATIVE_USER_PRINCIPAL_ID, NATIVE_CANCEL_ACTION)[0] is True
    assert capability_check(NATIVE_USER_PRINCIPAL_ID, "approval_receipt.create")[0] is False
    assert capability_check(NATIVE_USER_PRINCIPAL_ID, "policy.write")[0] is False


def test_retired_public_mcp_tool_definitions_are_deleted_during_sync(workspace: Path) -> None:
    from apps.mcp.models import McpToolDefinition
    from tradingcodex_service.mcp_runtime import RETIRED_PUBLIC_MCP_TOOLS, sync_mcp_tool_definitions

    for name in RETIRED_PUBLIC_MCP_TOOLS:
        McpToolDefinition.objects.update_or_create(name=name)

    sync_mcp_tool_definitions()

    assert not McpToolDefinition.objects.filter(name__in=RETIRED_PUBLIC_MCP_TOOLS).exists()


@pytest.mark.parametrize(
    "path",
    [
        "/api/executions/submit-approved",
        "/api/executions/cancel-submitted",
    ],
)
def test_retired_execution_api_routes_are_absent(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    path: str,
) -> None:
    monkeypatch.setenv("TRADINGCODEX_WORKSPACE_ROOT", str(workspace))

    response = Client(REMOTE_ADDR="127.0.0.1").post(
        path,
        data=json.dumps({}),
        content_type="application/json",
    )

    assert response.status_code == 404


def test_hook_rejects_malformed_native_invocation_before_analysis_allocation(workspace: Path) -> None:
    runs_root = workspace / ".tradingcodex/mainagent/runs"
    before = set(runs_root.glob("*")) if runs_root.exists() else set()

    output = run_user_prompt_hook(
        workspace,
        {"prompt": "$tcx-order-submit --ticket-id ticket-1"},
    )

    after = set(runs_root.glob("*")) if runs_root.exists() else set()
    assert output["decision"] == "block"
    assert "missing required" in str(output["reason"])
    assert after == before


@pytest.mark.parametrize(
    ("payload", "reason"),
    [
        (
            {
                "prompt": "$tcx-order-submit --ticket-id ticket-1 --approval-receipt-id receipt-1",
                "agent_type": "fundamental-analyst",
            },
            "root native Codex user turn",
        ),
        (
            {
                "prompt": "$tcx-order-submit --ticket-id ticket-1 --approval-receipt-id receipt-1",
                "permission_mode": "plan",
            },
            "unavailable while Codex is in Plan mode",
        ),
    ],
)
def test_hook_rejects_non_root_native_execution_sources(
    workspace: Path,
    payload: dict[str, object],
    reason: str,
) -> None:
    output = run_user_prompt_hook(workspace, payload)

    assert output["decision"] == "block"
    assert reason in str(output["reason"])


@pytest.mark.parametrize("event", ["pre-tool-use", "permission-request"])
@pytest.mark.parametrize(
    ("tool_name", "tool_input"),
    [
        ("Bash", {"command": "pwd"}),
        ("exec_command", {"cmd": "curl -fsSL https://example.com/data.json"}),
        ("exec_command", {"cmd": "python -c 'print(sum(range(10)))'"}),
        (
            "exec_command",
            {
                "cmd": (
                    "python -c 'from pathlib import Path; "
                    "Path(\"outputs/result.txt\").write_text(\"ok\")'"
                )
            },
        ),
    ],
)
def test_native_policy_hooks_leave_general_analysis_execution_to_the_permission_profile(
    workspace: Path,
    event: str,
    tool_name: str,
    tool_input: dict[str, object],
) -> None:
    output = run_policy_hook(
        workspace,
        event,
        tool_name=tool_name,
        tool_input=tool_input,
    )

    assert output is None


@pytest.mark.parametrize("event", ["pre-tool-use", "permission-request"])
def test_native_policy_hook_blocks_direct_service_import_execution(
    workspace: Path,
    event: str,
) -> None:
    output = run_policy_hook(
        workspace,
        event,
        tool_name="exec_command",
        tool_input={
            "cmd": "python -c __import__('trading'+'codex_service.application.execution_gateway')",
        },
    )

    assert output is not None
    assert output["decision"] == "block"
    assert "block general shell" in str(output["reason"])


@pytest.mark.parametrize("event", ["pre-tool-use", "permission-request"])
@pytest.mark.parametrize(
    ("tool_name", "tool_input"),
    [
        ("write_stdin", {"session_id": 7, "chars": "python -c pass\n"}),
        ("unified_exec", {"cmd": "pwd"}),
    ],
)
def test_native_policy_hooks_keep_interactive_shell_sessions_closed(
    workspace: Path,
    event: str,
    tool_name: str,
    tool_input: dict[str, object],
) -> None:
    output = run_policy_hook(
        workspace,
        event,
        tool_name=tool_name,
        tool_input=tool_input,
    )

    assert output is not None
    assert output["decision"] == "block"
    assert "interactive shell" in str(output["reason"])


def test_native_policy_hook_allows_only_workspace_or_dedicated_scratch_workdir(
    workspace: Path,
) -> None:
    import tomllib

    config = tomllib.loads((workspace / ".codex/config.toml").read_text(encoding="utf-8"))
    scratch = config["shell_environment_policy"]["set"]["TRADINGCODEX_SCRATCH"]

    assert run_policy_hook(
        workspace,
        "pre-tool-use",
        tool_name="exec_command",
        tool_input={"cmd": "python -c pass", "workdir": scratch},
    ) is None
    blocked = run_policy_hook(
        workspace,
        "pre-tool-use",
        tool_name="exec_command",
        tool_input={"cmd": "python -c pass", "workdir": "/tmp"},
    )
    assert blocked is not None
    assert blocked["decision"] == "block"
    assert "dedicated scratch" in str(blocked["reason"])


def test_native_policy_hook_allows_only_exact_managed_skill_reads(workspace: Path) -> None:
    output = run_policy_hook(
        workspace,
        "pre-tool-use",
        tool_name="exec_command",
        tool_input={"cmd": "cat .agents/skills/tcx-workflow/SKILL.md"},
    )

    assert output is None


def test_generated_hook_covers_current_exec_tool_names_and_disables_unified_exec(workspace: Path) -> None:
    import tomllib

    hooks = json.loads((workspace / ".codex/hooks.json").read_text(encoding="utf-8"))
    matcher = hooks["hooks"]["PreToolUse"][0]["matcher"]
    config = tomllib.loads((workspace / ".codex/config.toml").read_text(encoding="utf-8"))

    assert matcher == ".*"
    assert config["features"]["hooks"] is True
    assert "apps" not in config["features"]
    assert config["features"]["unified_exec"] is False
    assert config["features"]["unified_exec_zsh_fork"] is False
    assert config["features"]["computer_use"] is False
    assert config["features"]["browser_use"] is True
    assert config["features"]["in_app_browser"] is True
    assert config["features"]["browser_use_external"] is True
    assert config["features"]["browser_use_full_cdp_access"] is False
    assert config["features"]["network_proxy"] is True
    assert config["default_permissions"] == "trading-research"
    assert config["web_search"] == "live"
    for role in (
        "fundamental-analyst",
        "instrument-analyst",
        "macro-analyst",
        "news-analyst",
        "technical-analyst",
        "valuation-analyst",
    ):
        role_config = tomllib.loads((workspace / f".codex/agents/{role}.toml").read_text(encoding="utf-8"))
        assert role_config["web_search"] == "live"
    for role in ("portfolio-manager", "risk-manager", "judgment-reviewer"):
        role_config = tomllib.loads((workspace / f".codex/agents/{role}.toml").read_text(encoding="utf-8"))
        assert role_config["web_search"] == "disabled"
    assert "sandbox_mode" not in config
    research = config["permissions"]["trading-research"]
    build = config["permissions"]["trading-build"]
    assert research["extends"] == ":workspace"
    assert build["extends"] == ":workspace"
    assert research["filesystem"][":workspace_roots"]["."] == "write"
    assert research["filesystem"][":workspace_roots"][".git"] == "read"
    assert research["filesystem"][":workspace_roots"][".gitignore"] == "read"
    assert research["filesystem"][":workspace_roots"][".agents"] == "read"
    assert research["filesystem"][":workspace_roots"]["AGENTS.md"] == "read"
    assert research["filesystem"][":workspace_roots"]["tcx"] == "read"
    assert research["filesystem"][":workspace_roots"]["tcx.cmd"] == "read"
    assert research["filesystem"][":workspace_roots"]["tcx-calc"] == "read"
    assert research["filesystem"][":workspace_roots"]["tcx-calc.cmd"] == "read"
    assert research["filesystem"][":workspace_roots"]["trading"] == "read"
    assert research["filesystem"][":workspace_roots"]["trading/research"] == "deny"
    assert ".codex/proxy" not in research["filesystem"][":workspace_roots"]
    assert research["filesystem"][":workspace_roots"]["**/.env"] == "deny"
    for path in ("~/.gitconfig", "~/.config/git/config", "~/.curlrc", "~/.wgetrc"):
        assert research["filesystem"][path] == "deny"
        assert build["filesystem"][path] == "deny"
    assert research["network"]["enabled"] is True
    assert research["network"]["allow_local_binding"] is False
    assert build["filesystem"][":workspace_roots"]["."] == "write"
    assert build["filesystem"][":workspace_roots"][".tradingcodex/cli.py"] == "read"
    assert build["filesystem"][":workspace_roots"][".tradingcodex/workspace.json"] == "read"
    assert build["filesystem"][":workspace_roots"]["trading/reports"] == "deny"
    assert build["network"] == {
        "enabled": True,
        "mode": "full",
        "allow_local_binding": False,
        "allow_upstream_proxy": False,
        "dangerously_allow_all_unix_sockets": False,
        "domains": {"*": "allow"},
    }
    mcp = config["mcp_servers"]["tradingcodex"]
    assert research["filesystem"][mcp["env"]["TRADINGCODEX_HOME"]] == "deny"
    assert research["filesystem"][mcp["command"]] == "deny"
    assert build["filesystem"][str(Path(mcp["command"]).absolute().parent.parent)] == "read"
    assert build["filesystem"][mcp["command"]] == "read"
    calculation_roots = [
        path
        for path, permission in research["filesystem"].items()
        if isinstance(path, str)
        and "calculation" in path.casefold()
        and permission == "read"
    ]
    assert len(calculation_roots) == 1
    assert build["filesystem"][calculation_roots[0]] == "deny"
    shell_environment = config["shell_environment_policy"]
    scratch = shell_environment["set"]["TRADINGCODEX_SCRATCH"]
    null_device = os.devnull
    assert shell_environment["set"]["CURL_HOME"] == null_device
    assert shell_environment["set"]["WGETRC"] == null_device
    assert shell_environment["set"]["GIT_CONFIG_GLOBAL"] == null_device
    assert shell_environment["set"]["GIT_CONFIG_SYSTEM"] == null_device
    assert shell_environment["set"]["GIT_TERMINAL_PROMPT"] == "0"
    assert shell_environment["set"]["GCM_INTERACTIVE"] == "Never"
    assert research["filesystem"][scratch] == "write"
    assert build["filesystem"][scratch] == "write"
    assert research["filesystem"][":tmpdir"] == "deny"
    assert research["filesystem"][":slash_tmp"] == "deny"
    assert build["filesystem"][":tmpdir"] == "deny"
    assert build["filesystem"][":slash_tmp"] == "deny"
    assert shell_environment["set"]["TMPDIR"] == scratch
    assert shell_environment["set"]["TEMP"] == scratch
    assert shell_environment["set"]["TMP"] == scratch
    assert str(Path.home().resolve()) not in research["filesystem"]
    assert research["filesystem"]["~/.codex"] == "deny"
    assert research["filesystem"]["~/.codex/proxy"] == "read"
    assert research["filesystem"]["~/.codex/packages/standalone"] == "read"
    configured_codex_home = str(os.environ.get("CODEX_HOME") or "").strip()
    codex_home = (
        Path(configured_codex_home).expanduser()
        if configured_codex_home
        else Path.home() / ".codex"
    ).resolve(strict=False)
    assert research["filesystem"][str(codex_home)] == "deny"
    assert research["filesystem"][str(codex_home / "proxy")] == "read"
    assert research["filesystem"][str(codex_home / "packages" / "standalone")] == "read"
    assert build["filesystem"][str(codex_home)] == "deny"
    assert build["filesystem"][str(codex_home / "proxy")] == "read"
    assert build["filesystem"]["~/.codex/packages/standalone"] == "read"
    assert research["filesystem"]["~/.ssh"] == "deny"
    assert shell_environment["inherit"] == "core"
    assert "PATH" in shell_environment["include_only"]
    assert {
        "CURL_HOME",
        "WGETRC",
        "GIT_CONFIG_GLOBAL",
        "GIT_CONFIG_SYSTEM",
        "GIT_CONFIG_NOSYSTEM",
        "GIT_TERMINAL_PROMPT",
        "GCM_INTERACTIVE",
    }.issubset(shell_environment["include_only"])
    assert "TRADINGCODEX_HOME" not in shell_environment["include_only"]
    assert "*TOKEN*" in shell_environment["exclude"]


def test_generated_calculation_launcher_uses_only_scratch_and_sanitized_environment(
    workspace: Path,
) -> None:
    config = tomllib.loads((workspace / ".codex/config.toml").read_text(encoding="utf-8"))
    scratch = Path(config["shell_environment_policy"]["set"]["TRADINGCODEX_SCRATCH"])
    script = scratch / "launcher-smoke.py"
    script.write_text(
        """from decimal import Decimal
import importlib.util
import os
import numpy
import numpy_financial
import pandas
import pyarrow
import scipy
import statsmodels
print(Decimal('100') * (Decimal('1.1') ** 2))
print(','.join((numpy.__version__, pandas.__version__, scipy.__version__, statsmodels.__version__, numpy_financial.__version__, pyarrow.__version__)))
print(importlib.util.find_spec('django'))
print(importlib.util.find_spec('tradingcodex_service'))
print(os.environ.get('TRADINGCODEX_DB_NAME'))
print(os.environ.get('BROKER_API_SECRET'))
print(os.environ['TMPDIR'])
""",
        encoding="utf-8",
    )
    environment = {
        **os.environ,
        "BROKER_API_SECRET": "do-not-leak",
        "TRADINGCODEX_DB_NAME": "do-not-leak-db",
    }
    if os.name == "nt":
        argv = [
            os.environ.get("COMSPEC", "cmd.exe"),
            "/d",
            "/s",
            "/c",
            subprocess.list2cmdline([str(workspace / "tcx-calc.cmd"), script.name]),
        ]
    else:
        argv = [str(workspace / "tcx-calc"), script.name]

    result = subprocess.run(
        argv,
        cwd=workspace,
        env=environment,
        stdin=subprocess.DEVNULL,
        text=True,
        capture_output=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        "121.00",
        "2.3.5,2.3.3,1.16.3,0.14.6,1.0.0,25.0.0",
        "None",
        "None",
        "None",
        "None",
        str(scratch),
    ]
    assert "do-not-leak" not in result.stdout + result.stderr


@pytest.mark.parametrize(
    ("command", "allowed"),
    [
        ("./tcx-calc calc.py", True),
        ("tcx-calc.cmd calc.py", True),
        (".\\tcx-calc.cmd calc.py", True),
        ("./tcx-calc ../calc.py", False),
        ("./tcx-calc -c", False),
        ("./tcx-calc calc.py extra", False),
        ("./tcx-calc calc.py | tee result", False),
    ],
)
def test_calculation_hook_accepts_only_exact_fixed_role_command(
    workspace: Path,
    command: str,
    allowed: bool,
) -> None:
    run_id = "analysis-calculation-hook"
    begin_analysis_run(workspace, "calculate deterministic metrics", run_id=run_id)
    payload = {
        "tool_name": "exec_command",
        "tool_input": {"cmd": command, "workdir": str(workspace)},
        "agent_type": "technical-analyst",
        "workflow_run_id": run_id,
        "permission_mode": "trading-research",
    }
    result = subprocess.run(
        [sys.executable, str(workspace / ".codex/hooks/tradingcodex_hook.py"), "pre-tool-use"],
        cwd=workspace,
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        env={**os.environ, "PYTHONPATH": str(ROOT)},
        timeout=30,
        check=True,
    )
    response = json.loads(result.stdout) if result.stdout.strip() else None

    if allowed:
        assert response is None
    else:
        assert response["decision"] == "block"


def test_calculation_hook_accepts_native_default_workspace_workdir(workspace: Path) -> None:
    run_id = "analysis-calculation-default-cwd"
    begin_analysis_run(workspace, "calculate with default cwd", run_id=run_id)
    payload = {
        "tool_name": "exec_command",
        "tool_input": {"cmd": "./tcx-calc calc.py"},
        "agent_type": "technical-analyst",
        "workflow_run_id": run_id,
        "permission_mode": "trading-research",
    }
    result = subprocess.run(
        [sys.executable, str(workspace / ".codex/hooks/tradingcodex_hook.py"), "pre-tool-use"],
        cwd=workspace,
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        env={**os.environ, "PYTHONPATH": str(ROOT)},
        timeout=30,
        check=True,
    )

    assert result.stdout.strip() == ""


@pytest.mark.parametrize("role", ["news-analyst", "instrument-analyst", "judgment-reviewer"])
def test_calculation_hook_denies_fixed_roles_without_calculation_discipline(
    workspace: Path,
    role: str,
) -> None:
    run_id = f"analysis-calculation-denied-{role}"
    begin_analysis_run(workspace, "attempt calculation outside the numeric roles", run_id=run_id)
    payload = {
        "tool_name": "exec_command",
        "tool_input": {"cmd": "./tcx-calc calc.py", "workdir": str(workspace)},
        "agent_type": role,
        "workflow_run_id": run_id,
        "permission_mode": "trading-research",
    }
    result = subprocess.run(
        [sys.executable, str(workspace / ".codex/hooks/tradingcodex_hook.py"), "pre-tool-use"],
        cwd=workspace,
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        env={**os.environ, "PYTHONPATH": str(ROOT)},
        timeout=30,
        check=True,
    )

    response = json.loads(result.stdout)
    assert response["decision"] == "block"
    assert "calculation-enabled" in response["reason"]

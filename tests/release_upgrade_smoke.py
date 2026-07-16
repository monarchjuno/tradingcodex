from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import shutil
import socket
import sqlite3
import stat
import subprocess
import sys
import tempfile
import tomllib
import venv
import zipfile
from dataclasses import dataclass
from email.parser import BytesParser
from email.policy import default as email_policy
from pathlib import Path


TARGET_VERSION = "1.1.1"
DEFAULT_FROM_VERSION = "1.0.2"
PUBLIC_PYPI_INDEX = "https://pypi.org/simple"
MODULE_LOCK = Path(".tradingcodex/generated/module-lock.json")
WORKSPACE_MANIFEST = Path(".tradingcodex/workspace.json")
BRAIN_ID = "investment-brain-upgrade-smoke"
REGISTERED_BROKER_ID = "release-upgrade-paper"
PROFILE_ID = "release-upgrade-profile"
HISTORICAL_PROVIDER_ID = "release-upgrade-provider"


@dataclass(frozen=True)
class PreservationFixtures:
    roots: tuple[Path, ...]
    files: dict[str, bytes]
    manifest_state: dict[str, object]
    brain_record: dict[str, object]
    connector_state: dict[str, object]
    ledger_state: dict[str, object]
    source_snapshot_path: str
    provider_state: dict[str, object]
    provider_snapshot_files: dict[str, bytes]


def require(condition: object, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def run(
    argv: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    input_text: str | None = None,
    timeout: int = 900,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        argv,
        cwd=cwd,
        env=env,
        input=input_text,
        text=True,
        encoding="utf-8",
        errors="strict",
        capture_output=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"command failed ({result.returncode}): {subprocess.list2cmdline(argv)}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


def windows_launcher_argv(workspace: Path, *args: str) -> list[str]:
    return [
        os.environ.get("COMSPEC", "cmd.exe"),
        "/d",
        "/s",
        "/c",
        "call",
        str(workspace / "tcx.cmd"),
        *args,
    ]


def launcher_argv(workspace: Path, *args: str) -> list[str]:
    if os.name == "nt":
        return windows_launcher_argv(workspace, *args)
    return [str(workspace / "tcx"), *args]


def package_runner_argv(env: dict[str, str], package_spec: str, *args: str) -> list[str]:
    search_path = env.get("PATH")
    uvx = shutil.which("uvx", path=search_path)
    if uvx:
        return [uvx, "--refresh", "--from", package_spec, "tcx", *args]
    uv = shutil.which("uv", path=search_path)
    if uv:
        return [uv, "tool", "run", "--refresh", "--from", package_spec, "tcx", *args]
    raise RuntimeError("release upgrade smoke requires uvx or uv on PATH")


def venv_executable(virtualenv: Path, name: str) -> Path:
    scripts = virtualenv / ("Scripts" if os.name == "nt" else "bin")
    suffix = ".exe" if os.name == "nt" else ""
    return scripts / f"{name}{suffix}"


def free_loopback_port() -> int:
    for port in range(47999, 47000, -1):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
                listener.bind(("127.0.0.1", port))
                return port
        except OSError:
            continue
    raise RuntimeError("no free product-range loopback port for release upgrade smoke")


def clean_environment(root: Path) -> dict[str, str]:
    env = os.environ.copy()
    for key in (
        "PYTHONPATH",
        "PYTHONHOME",
        "VIRTUAL_ENV",
        "TRADINGCODEX_HOME",
        "TRADINGCODEX_HOME_SOURCE",
        "TRADINGCODEX_DB_NAME",
        "TRADINGCODEX_WORKSPACE_ROOT",
        "TRADINGCODEX_SERVICE_ADDR",
        "TRADINGCODEX_MCP_AUTOSTART_SERVICE",
        "TRADINGCODEX_PYTHON",
        "TRADINGCODEX_LAUNCHED_BY_UVX",
        "TRADINGCODEX_LAUNCHED_BY_PACKAGE_RUNNER",
        "TRADINGCODEX_MCP_PACKAGE_SPEC",
        "_TRADINGCODEX_EXECUTABLE_SOURCE_KIND",
        "_TRADINGCODEX_DEV_SOURCE_ROOT",
        "_TRADINGCODEX_PRIOR_RUNTIME_PYTHON",
        "CODEX_HOME",
        "DJANGO_SETTINGS_MODULE",
        "XDG_CACHE_HOME",
        "XDG_DATA_HOME",
        "PIP_INDEX_URL",
        "PIP_EXTRA_INDEX_URL",
        "PIP_FIND_LINKS",
        "PIP_NO_INDEX",
        "PIP_TRUSTED_HOST",
        "UV_INDEX",
        "UV_INDEX_URL",
        "UV_DEFAULT_INDEX",
        "UV_EXTRA_INDEX_URL",
        "UV_FIND_LINKS",
        "UV_PROJECT_ENVIRONMENT",
    ):
        env.pop(key, None)
    user_home = root / "Isolated User Home With Spaces"
    user_home.mkdir(parents=True, exist_ok=True)
    env["HOME"] = str(user_home)
    if os.name == "nt":
        local_app_data = root / "Isolated Local App Data With Spaces"
        local_app_data.mkdir(parents=True, exist_ok=True)
        env["USERPROFILE"] = str(user_home)
        env["LOCALAPPDATA"] = str(local_app_data)
    else:
        xdg_data = root / "Isolated XDG Data With Spaces"
        xdg_cache = root / "Isolated XDG Cache With Spaces"
        xdg_data.mkdir(parents=True, exist_ok=True)
        xdg_cache.mkdir(parents=True, exist_ok=True)
        env["XDG_DATA_HOME"] = str(xdg_data)
        env["XDG_CACHE_HOME"] = str(xdg_cache)
    env["PIP_CONFIG_FILE"] = os.devnull
    env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    env["PYTHONNOUSERSITE"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["TRADINGCODEX_DISABLE_LATEST_RELEASE_CHECK"] = "1"
    env["UV_CACHE_DIR"] = str(root / "Isolated UV Cache With Spaces")
    return env


def wheel_identity(path: Path) -> tuple[str, str]:
    with zipfile.ZipFile(path) as archive:
        metadata_files = [
            name
            for name in archive.namelist()
            if name.count("/") == 1 and name.endswith(".dist-info/METADATA")
        ]
        require(len(metadata_files) == 1, f"wheel must contain exactly one dist-info/METADATA: {path}")
        metadata = BytesParser(policy=email_policy).parsebytes(archive.read(metadata_files[0]))
    return str(metadata["Name"] or ""), str(metadata["Version"] or "")


def read_json(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    require(isinstance(payload, dict), f"expected a JSON object: {path}")
    return payload


def generated_git_command_from_hook(path: Path) -> Path:
    module = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    values: list[str] = []
    for node in module.body:
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Constant):
            continue
        if not isinstance(node.value.value, str):
            continue
        if any(isinstance(target, ast.Name) and target.id == "GENERATED_GIT_COMMAND" for target in node.targets):
            values.append(node.value.value)
    require(len(values) == 1, f"expected exactly one generated Git command in {path}, found {len(values)}")
    command = Path(values[0])
    require(command.is_absolute(), f"generated Git command is not absolute: {command}")
    require(command.is_file(), f"generated Git command is missing: {command}")
    require(os.access(command, os.X_OK), f"generated Git command is not executable: {command}")
    if sys.platform == "darwin":
        require(
            os.path.normcase(os.path.abspath(command)) != os.path.normcase("/usr/bin/git"),
            "generated Git command must bypass the macOS /usr/bin/git shim",
        )
    return command


def same_path(left: object, right: Path) -> bool:
    return os.path.normcase(os.path.abspath(str(left))) == os.path.normcase(os.path.abspath(str(right)))


def snapshot_paths(workspace: Path, relative_paths: tuple[Path, ...]) -> dict[str, bytes]:
    snapshot: dict[str, bytes] = {}
    for relative in relative_paths:
        target = workspace / relative
        require(target.exists(), f"preservation fixture is missing: {relative.as_posix()}")
        candidates = [target] if target.is_file() else sorted(path for path in target.rglob("*") if path.is_file())
        for candidate in candidates:
            key = candidate.relative_to(workspace).as_posix()
            snapshot[key] = candidate.read_bytes()
    return snapshot


def snapshot_tree(root: Path) -> dict[str, bytes]:
    require(root.is_dir(), f"snapshot tree is missing: {root}")
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def provider_runtime_environment(
    env: dict[str, str],
    *,
    workspace: Path,
    home: Path,
    database: Path,
) -> dict[str, str]:
    return {
        **env,
        "DJANGO_SETTINGS_MODULE": "tradingcodex_service.settings",
        "TRADINGCODEX_WORKSPACE_ROOT": str(workspace),
        "TRADINGCODEX_HOME": str(home),
        "TRADINGCODEX_HOME_SOURCE": "environment_override",
        "TRADINGCODEX_DB_NAME": str(database),
    }


def stable_provider_state(status: dict[str, object]) -> dict[str, object]:
    return {
        key: status.get(key)
        for key in (
            "kind",
            "provider_id",
            "path",
            "source_hash",
            "bundle_sha256",
            "provider_py_sha256",
            "approval_status",
            "approval_id",
            "approved_at",
            "snapshot_relative_path",
        )
    }


def provider_source_state(
    python: Path,
    workspace: Path,
    home: Path,
    database: Path,
    cwd: Path,
    env: dict[str, str],
) -> dict[str, object]:
    script = (
        "import json, sys\n"
        "from pathlib import Path\n"
        "import django\n"
        "django.setup()\n"
        "from tradingcodex_service.application.brokers import broker_provider_source_status\n"
        "status = broker_provider_source_status(sys.argv[1], Path(sys.argv[2]))\n"
        "print(json.dumps(status, sort_keys=True))\n"
    )
    result = run(
        [str(python), "-c", script, HISTORICAL_PROVIDER_ID, str(workspace)],
        cwd=cwd,
        env=provider_runtime_environment(
            env,
            workspace=workspace,
            home=home,
            database=database,
        ),
    )
    payload = json.loads(result.stdout)
    require(isinstance(payload, dict), "provider source status is not a JSON object")
    return stable_provider_state(payload)


def loaded_provider_state(
    workspace: Path,
    cwd: Path,
    env: dict[str, str],
) -> dict[str, object]:
    payload = json.loads(
        run(
            launcher_argv(workspace, "connectors", "providers"),
            cwd=cwd,
            env=env,
        ).stdout
    )
    require(isinstance(payload, dict), "provider registry output is not a JSON object")
    providers = payload.get("providers")
    require(isinstance(providers, list), "provider registry output has no providers list")
    matching = [
        provider
        for provider in providers
        if isinstance(provider, dict) and provider.get("provider_id") == HISTORICAL_PROVIDER_ID
    ]
    require(len(matching) == 1, "historical approved provider was not loaded exactly once")
    provider = matching[0]
    require(
        provider.get("display_name") == "Release upgrade approved provider",
        "historical approved provider loaded from unexpected source bytes",
    )
    source = provider.get("provider_source")
    require(isinstance(source, dict), "loaded historical provider has no source status")
    require(
        source.get("approval_status") == "approved"
        and source.get("loaded_source_hash") == source.get("bundle_sha256")
        and source.get("service_restart_required") is False,
        "historical provider was not loaded from its active approved bundle",
    )
    return {
        "provider_id": provider.get("provider_id"),
        "display_name": provider.get("display_name"),
        "provider_source": stable_provider_state(source),
    }


def create_and_approve_historical_provider(
    python: Path,
    workspace: Path,
    home: Path,
    database: Path,
    cwd: Path,
    env: dict[str, str],
) -> tuple[Path, dict[str, object], dict[str, bytes]]:
    provider_dir = workspace / "trading" / "connectors" / HISTORICAL_PROVIDER_ID
    provider_dir.mkdir(parents=True, exist_ok=True)
    (provider_dir / "helper.py").write_text(
        'DISPLAY_NAME = "Release upgrade approved provider"\n',
        encoding="utf-8",
    )
    (provider_dir / "provider.py").write_text(
        (
            "from tradingcodex_service.application.brokers import BrokerAdapterProvider\n"
            "from .helper import DISPLAY_NAME\n\n"
            "PROVIDER = BrokerAdapterProvider(\n"
            f"    provider_id={HISTORICAL_PROVIDER_ID!r},\n"
            "    display_name=DISPLAY_NAME,\n"
            "    execution_posture='broker_validation_only',\n"
            ")\n"
        ),
        encoding="utf-8",
    )
    script = (
        "import json, sys\n"
        "from pathlib import Path\n"
        "import django\n"
        "django.setup()\n"
        "from tradingcodex_service.application.brokers import (\n"
        "    approve_workspace_broker_provider_source,\n"
        "    broker_provider_source_status,\n"
        "    inspect_workspace_broker_provider_source,\n"
        ")\n"
        "from tradingcodex_service.application.operator_authority import (\n"
        "    PROVIDER_SOURCE_APPROVE,\n"
        "    _issue_operator_authority,\n"
        "    provider_source_approval_resource,\n"
        ")\n"
        "provider_id, workspace = sys.argv[1], Path(sys.argv[2])\n"
        "inspected = inspect_workspace_broker_provider_source(workspace, provider_id)\n"
        "bundle_sha256 = inspected['bundle_sha256']\n"
        "authority = _issue_operator_authority(\n"
        "    workspace,\n"
        "    action=PROVIDER_SOURCE_APPROVE,\n"
        "    resource=provider_source_approval_resource(provider_id, bundle_sha256),\n"
        ")\n"
        "approved = approve_workspace_broker_provider_source(\n"
        "    workspace,\n"
        "    provider_id,\n"
        "    expected_bundle_sha256=bundle_sha256,\n"
        "    operator_authority=authority,\n"
        ")\n"
        "status = broker_provider_source_status(provider_id, workspace)\n"
        "print(json.dumps({'approved': approved, 'status': status}, sort_keys=True))\n"
    )
    result = run(
        [str(python), "-c", script, HISTORICAL_PROVIDER_ID, str(workspace)],
        cwd=cwd,
        env=provider_runtime_environment(
            env,
            workspace=workspace,
            home=home,
            database=database,
        ),
    )
    payload = json.loads(result.stdout)
    require(isinstance(payload, dict), "historical provider approval is not a JSON object")
    approved = payload.get("approved")
    status = payload.get("status")
    require(isinstance(approved, dict), "historical provider approval payload is missing")
    require(isinstance(status, dict), "historical provider status payload is missing")
    require(approved.get("status") == "approved", "historical provider was not approved")
    require(status.get("approval_status") == "approved", "historical provider approval is not active")
    require(
        approved.get("bundle_sha256") == status.get("bundle_sha256"),
        "historical provider approval hash does not match its source status",
    )
    snapshot_relative_path = Path(str(approved.get("snapshot_relative_path") or ""))
    require(
        snapshot_relative_path.parts[:2] == ("provider-snapshots", "v1"),
        "historical provider snapshot path is invalid",
    )
    snapshot_root = home / snapshot_relative_path
    snapshot_files = snapshot_tree(snapshot_root)
    require(snapshot_files, "historical provider snapshot is empty")
    return provider_dir, stable_provider_state(status), snapshot_files


def create_investment_brain_source(workspace: Path) -> Path:
    source = workspace / "investment-brains" / BRAIN_ID
    (source / ".tradingcodex").mkdir(parents=True)
    (source / "skill" / "agents").mkdir(parents=True)
    (source / "skill" / "references").mkdir(parents=True)
    (source / ".tradingcodex" / "plugin.json").write_text(
        json.dumps(
            {
                "format": "tradingcodex.investment-brain",
                "schema_version": 1,
                "type": "investment-brain",
                "id": BRAIN_ID,
                "version": "1.0.0",
                "skill": "skill",
                "source": {
                    "publisher": "TradingCodex release upgrade smoke",
                    "license": "Apache-2.0",
                },
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    (source / "skill" / "SKILL.md").write_text(
        "---\n"
        f"name: {BRAIN_ID}\n"
        "description: Preserve one installed Investment Brain across a release update.\n"
        "---\n\n"
        "# Release Upgrade Brain\n\n"
        "Require point-in-time evidence, explicit falsifiers, and a recorded knowledge cutoff.\n",
        encoding="utf-8",
    )
    (source / "skill" / "agents" / "openai.yaml").write_text(
        "interface:\n"
        '  display_name: "Release Upgrade Brain"\n'
        '  short_description: "Verify managed Brain preservation"\n'
        f'  default_prompt: "Use ${BRAIN_ID} for this investment analysis."\n'
        "policy:\n"
        "  allow_implicit_invocation: false\n",
        encoding="utf-8",
    )
    (source / "skill" / "references" / "preservation.md").write_text(
        "# Preservation Contract\n\n"
        "Keep the source, immutable installed package, registry record, and active projection unchanged.\n",
        encoding="utf-8",
    )
    return source


def create_source_snapshot(workspace: Path, cwd: Path, env: dict[str, str]) -> Path:
    result = json.loads(
        run(
            launcher_argv(
                workspace,
                "mcp",
                "call",
                "record_source_snapshot",
                "--principal",
                "fundamental-analyst",
                "--provider",
                "release-upgrade-smoke",
                "--source-category",
                "filing",
                "--known-at",
                "2025-01-02T03:04:05Z",
                "--retrieved-at",
                "2025-01-02T03:04:05Z",
                "--recorded-at",
                "2025-01-02T03:04:05Z",
                "--as-of",
                "2025-01-02T03:04:05Z",
                "--artifact-id",
                "release-upgrade-evidence",
                "--warnings",
                '["release upgrade preservation fixture"]',
                "--payload",
                '{"document":"annual-report","revision":"original"}',
            ),
            cwd=cwd,
            env=env,
        ).stdout
    )
    require(result.get("status") == "recorded", "source snapshot was not recorded by the historical service")
    relative = Path(str(result.get("export_path") or ""))
    require(
        relative.parts[:3] == ("trading", "research", "source-snapshots") and relative.suffix == ".json",
        "historical service returned an invalid source snapshot path",
    )
    require((workspace / relative).is_file(), "service-issued source snapshot file is missing")
    document = read_json(workspace / relative)
    require(document.get("snapshot_id") == result.get("snapshot_id"), "source snapshot identity is inconsistent")
    require(document.get("snapshot_hash"), "source snapshot does not contain its content hash")
    return relative


def connection_semantic_state(connection: dict[str, object]) -> dict[str, object]:
    fields = (
        "broker_id",
        "provider_id",
        "display_name",
        "transport",
        "status",
        "credential_ref",
        "capabilities",
        "enabled_read_scopes",
        "enabled_trade_scopes",
        "trust_level",
        "last_sync_at",
        "last_health_status",
        "drift_status",
        "accounts_count",
        "metadata",
    )
    state = {field: connection.get(field) for field in fields}
    accounts = connection.get("accounts")
    require(isinstance(accounts, list), "broker connection accounts are missing")
    state["accounts"] = [
        {
            field: account.get(field)
            for field in (
                "broker_account_id",
                "account_label",
                "account_type",
                "base_currency",
                "masked_identifier",
                "trading_enabled",
            )
        }
        for account in accounts
        if isinstance(account, dict)
    ]
    return state


def read_connector_state(workspace: Path, cwd: Path, env: dict[str, str]) -> dict[str, object]:
    payload = json.loads(
        run(
            launcher_argv(workspace, "mcp", "call", "list_broker_connections"),
            cwd=cwd,
            env=env,
        ).stdout
    )
    require(payload.get("db_canonical") is True, "broker connection response is not DB-canonical")
    connections = payload.get("connections")
    require(isinstance(connections, list), "broker connection list is missing")

    def selected(broker_id: str) -> dict[str, object]:
        matches = [item for item in connections if isinstance(item, dict) and item.get("broker_id") == broker_id]
        require(len(matches) == 1, f"expected one central-ledger broker connection: {broker_id}")
        return connection_semantic_state(matches[0])

    return {
        "registered": selected(REGISTERED_BROKER_ID),
        "paper_scope": selected("paper-trading"),
    }


def central_ledger_state(database: Path, active_profile: dict[str, object]) -> dict[str, object]:
    require(database.is_file(), "explicit TradingCodex database is missing")
    connection = sqlite3.connect(str(database))
    connection.row_factory = sqlite3.Row
    try:
        registered = connection.execute(
            "SELECT id, broker_id, provider_id, display_name, transport, status, credential_ref, "
            "capabilities, enabled_read_scopes, enabled_trade_scopes, trust_level, last_sync_at, "
            "last_health_status, drift_status, metadata, created_at, updated_at "
            "FROM integrations_brokerconnection WHERE broker_id = ?",
            (REGISTERED_BROKER_ID,),
        ).fetchone()
        require(registered is not None, "registered connector is missing from the central ledger")
        adapter = connection.execute(
            "SELECT id, adapter_id, kind, enabled, live, config "
            "FROM integrations_adapterdefinition WHERE adapter_id = ?",
            ("paper",),
        ).fetchone()
        require(adapter is not None, "paper adapter definition is missing from the central ledger")
        paper = connection.execute(
            "SELECT id, broker_id, provider_id, display_name, transport, status, credential_ref, "
            "capabilities, enabled_read_scopes, enabled_trade_scopes, trust_level, "
            "last_health_status, drift_status, metadata "
            "FROM integrations_brokerconnection WHERE broker_id = ?",
            ("paper-trading",),
        ).fetchone()
        require(paper is not None, "paper-scope connector is missing from the central ledger")
        paper_accounts = connection.execute(
            "SELECT broker_account_id, account_label, account_type, base_currency, masked_identifier, "
            "trading_enabled, metadata FROM integrations_brokeraccount "
            "WHERE broker_connection_id = ? ORDER BY broker_account_id",
            (paper["id"],),
        ).fetchall()
    finally:
        connection.close()

    expected_account_id = active_profile.get("account_id")
    matching_accounts = [row for row in paper_accounts if row["broker_account_id"] == expected_account_id]
    require(len(matching_accounts) == 1, "active paper account scope is missing from the central ledger")
    account_metadata = json.loads(str(matching_accounts[0]["metadata"]))
    require(
        account_metadata.get("portfolio_id") == active_profile.get("portfolio_id")
        and account_metadata.get("strategy_id") == active_profile.get("strategy_id"),
        "central-ledger paper account no longer matches the active workspace profile",
    )
    return {
        "registered_connection": dict(registered),
        "adapter_definition": dict(adapter),
        "paper_connection": dict(paper),
        "paper_accounts": [dict(row) for row in paper_accounts],
    }


def create_user_fixtures(
    workspace: Path,
    home: Path,
    database: Path,
    cwd: Path,
    env: dict[str, str],
) -> PreservationFixtures:
    research = workspace / "trading/research/release-upgrade-smoke.md"
    research.parent.mkdir(parents=True, exist_ok=True)
    research.write_bytes("# Release upgrade research\r\n\r\n사용자 연구 자료를 그대로 보존한다.\r\n".encode())

    source_snapshot = create_source_snapshot(workspace, cwd, env)
    brain_source = create_investment_brain_source(workspace)
    installed_brain = json.loads(
        run(
            launcher_argv(
                workspace,
                "investment-brains",
                "install",
                "--local",
                str(brain_source),
                "--active",
            ),
            cwd=cwd,
            env=env,
        ).stdout
    )
    require(installed_brain.get("brain_id") == BRAIN_ID, "historical Brain install returned the wrong id")
    require(installed_brain.get("status") == "active", "historical Brain install is not active")
    require(installed_brain.get("validation_status") == "valid", "historical Brain install is invalid")

    run(
        launcher_argv(
            workspace,
            "strategies",
            "create",
            "strategy-upgrade-smoke",
            "--description",
            "Cross-version user strategy preservation fixture.",
            "--body",
            "# Upgrade Smoke\n\n## Thesis\nPreserve disciplined evidence across release updates.",
            "--language",
            "en",
            "--active",
        ),
        cwd=cwd,
        env=env,
    )
    run(
        launcher_argv(workspace, "profile", "create", PROFILE_ID),
        cwd=cwd,
        env=env,
    )
    run(
        launcher_argv(workspace, "profile", "select", PROFILE_ID),
        cwd=cwd,
        env=env,
    )
    run(
        launcher_argv(
            workspace,
            "profile",
            "update",
            "--label",
            "Release Upgrade Profile",
            "--base-currency",
            "KRW",
        ),
        cwd=cwd,
        env=env,
    )

    run(
        launcher_argv(
            workspace,
            "connectors",
            "scaffold",
            "legacy-upgrade",
            "--provider-id",
            "legacy-upgrade",
            "--display-name",
            "Legacy Upgrade",
            "--credential-ref",
            "env:LEGACY_UPGRADE_SMOKE",
            "--environment",
            "paper",
        ),
        cwd=cwd,
        env=env,
    )
    connector_dir = workspace / "trading/connectors/legacy-upgrade"
    connector_files = sorted(path.name for path in connector_dir.iterdir() if path.is_file())
    require(
        connector_files == ["README.md", "connector-profile.json", "secret-schema.json"],
        f"unexpected legacy connector scaffold files: {connector_files}",
    )
    profile = read_json(connector_dir / "connector-profile.json")
    build_lane = profile.get("build_lane")
    require(isinstance(build_lane, dict), "legacy scaffold build_lane is missing")
    require(build_lane.get("provider_development_required") is True, "legacy scaffold is not provider-blocked")

    registered = json.loads(
        run(
            launcher_argv(
                workspace,
                "connectors",
                "register",
                "--provider-id",
                "paper",
                "--broker-id",
                REGISTERED_BROKER_ID,
                "--display-name",
                "Release Upgrade Paper",
                "--credential-ref",
                "env:RELEASE_UPGRADE_PAPER",
                "--environment",
                "paper",
            ),
            cwd=cwd,
            env=env,
        ).stdout
    )
    require(registered.get("status") == "created", "historical central-ledger connector was not created")
    registered_connection = registered.get("connection")
    require(
        isinstance(registered_connection, dict) and registered_connection.get("broker_id") == REGISTERED_BROKER_ID,
        "historical connector registration returned the wrong central-ledger record",
    )

    manifest = read_json(workspace / WORKSPACE_MANIFEST)
    active_profile = manifest.get("active_profile")
    require(isinstance(active_profile, dict), "historical workspace active profile is missing")
    manifest_state = {
        "active_profile": active_profile,
        "execution_mode": manifest.get("execution_mode"),
    }
    require(
        active_profile.get("profile_id") == PROFILE_ID
        and active_profile.get("label") == "Release Upgrade Profile"
        and active_profile.get("base_currency") == "KRW",
        "non-default active paper scope was not established",
    )
    connector_state = read_connector_state(workspace, cwd, env)
    ledger_state = central_ledger_state(database, active_profile)
    attached_python = Path(
        tomllib.loads((workspace / ".codex/config.toml").read_text(encoding="utf-8"))["mcp_servers"][
            "tradingcodex"
        ]["command"]
    )
    provider_dir, provider_state, provider_snapshot_files = create_and_approve_historical_provider(
        attached_python,
        workspace,
        home,
        database,
        cwd,
        env,
    )
    loaded_provider = loaded_provider_state(workspace, cwd, env)
    require(
        loaded_provider["provider_source"] == provider_state,
        "historical provider registry did not load the approved immutable snapshot",
    )
    brain_record = json.loads(
        run(
            launcher_argv(workspace, "investment-brains", "inspect", BRAIN_ID),
            cwd=cwd,
            env=env,
        ).stdout
    )
    require(brain_record.get("active") is True, "installed Brain is not active after projection")
    require(brain_record.get("validation_status") == "valid", "installed Brain projection is invalid")

    roots = (
        research.relative_to(workspace),
        source_snapshot,
        brain_source.relative_to(workspace),
        Path(".tradingcodex/investment-brains"),
        Path(".agents/skills") / BRAIN_ID,
        Path(".agents/skills/strategy-upgrade-smoke"),
        connector_dir.relative_to(workspace),
        provider_dir.relative_to(workspace),
        Path(".tradingcodex/profiles.json"),
    )
    return PreservationFixtures(
        roots=roots,
        files=snapshot_paths(workspace, roots),
        manifest_state=manifest_state,
        brain_record=brain_record,
        connector_state=connector_state,
        ledger_state=ledger_state,
        source_snapshot_path=source_snapshot.as_posix(),
        provider_state=provider_state,
        provider_snapshot_files=provider_snapshot_files,
    )


def assert_runtime_identity(
    lock: dict[str, object],
    *,
    workspace_id: str,
    home: Path,
    database: Path,
    version: str,
) -> None:
    require(lock.get("workspace_id") == workspace_id, "module-lock workspace_id changed")
    require(lock.get("tradingcodex_version") == version, f"module-lock version is not {version}")
    require(same_path(lock.get("tradingcodex_home"), home), "explicit TradingCodex home changed")
    require(lock.get("home_source") == "environment_override", "explicit home source was not preserved")
    require(same_path(lock.get("tradingcodex_db_path"), database), "explicit database path changed")
    require(lock.get("db_source") == "environment_override", "explicit database source was not preserved")


def parse_config_yaml(attached_python: Path, path: Path, cwd: Path, env: dict[str, str]) -> dict[str, object]:
    script = (
        "import json,pathlib,sys,yaml; "
        "print(json.dumps(yaml.safe_load(pathlib.Path(sys.argv[1]).read_text(encoding='utf-8'))))"
    )
    return json.loads(run([str(attached_python), "-c", script, str(path)], cwd=cwd, env=env).stdout)


def assert_candidate_projection(
    workspace: Path,
    *,
    lock: dict[str, object],
    home: Path,
    database: Path,
    service_addr: str,
    cwd: Path,
    env: dict[str, str],
) -> None:
    config_path = workspace / ".codex/config.toml"
    config = tomllib.loads(config_path.read_text(encoding="utf-8"))
    require(config.get("web_search") == "disabled", "Build must not enable native web_search")
    features = config.get("features")
    require(isinstance(features, dict), "Codex feature configuration is missing")
    require(features.get("hooks") is True, "Codex hooks are not enabled")
    require(features.get("network_proxy") is True, "Codex network proxy is not enabled")

    permissions = config.get("permissions")
    require(isinstance(permissions, dict), "Codex permission profiles are missing")
    build = permissions.get("trading-build")
    require(isinstance(build, dict), "trading-build permission profile is missing")
    require(
        build.get("network")
        == {
            "enabled": True,
            "mode": "full",
            "allow_local_binding": False,
            "allow_upstream_proxy": False,
            "dangerously_allow_all_unix_sockets": False,
            "domains": {"*": "allow"},
        },
        "trading-build public-only network projection does not match the 1.1.1 contract",
    )

    shell_policy = config.get("shell_environment_policy")
    require(isinstance(shell_policy, dict), "shell environment policy is missing")
    shell_set = shell_policy.get("set")
    require(isinstance(shell_set, dict), "shell environment set projection is missing")
    for key in ("CURL_HOME", "WGETRC", "GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM"):
        require(shell_set.get(key) == os.devnull, f"{key} is not projected to the platform null device")
    require(shell_set.get("GIT_CONFIG_NOSYSTEM") == "1", "Git system config bypass is missing")
    git_overrides = (
        ("core.hooksPath", os.devnull), ("core.fsmonitor", "false"), ("core.askPass", ""),
        ("credential.helper", ""), ("credential.interactive", "never"), ("http.extraHeader", ""),
        ("http.version", "HTTP/1.1"), ("http.cookieFile", ""), ("http.saveCookies", "false"),
        ("http.followRedirects", "false"), ("http.sslVerify", "true"), ("protocol.allow", "never"),
        ("protocol.https.allow", "always"), ("protocol.http.allow", "never"),
        ("protocol.ssh.allow", "never"), ("protocol.git.allow", "never"),
        ("protocol.file.allow", "never"), ("protocol.ext.allow", "never"),
    )
    require(shell_set.get("GIT_CONFIG_COUNT") == str(len(git_overrides)), "Git hardening override count is missing")
    for index, (key, value) in enumerate(git_overrides):
        require(shell_set.get(f"GIT_CONFIG_KEY_{index}") == key, f"Git config key {index} is missing")
        require(shell_set.get(f"GIT_CONFIG_VALUE_{index}") == value, f"Git config value {index} is missing")
    require(shell_set.get("GIT_OPTIONAL_LOCKS") == "0", "optional Git writes are not disabled")
    require(shell_set.get("GIT_PAGER") == "cat", "interactive Git paging is not disabled")
    require(shell_set.get("GIT_TERMINAL_PROMPT") == "0", "interactive Git prompting is not disabled")
    require(shell_set.get("GCM_INTERACTIVE") == "Never", "Git credential-manager prompting is not disabled")
    scratch = Path(str(shell_set.get("TRADINGCODEX_SCRATCH") or ""))
    provider_sources = scratch / "provider-sources"
    require(shell_set.get("GIT_CEILING_DIRECTORIES") == str(scratch), "Git discovery ceiling is missing")
    require(shell_set.get("GIT_PROTOCOL_FROM_USER") == "0", "Git user protocol fallback is not disabled")
    workspace_id = str(lock.get("workspace_id") or "")
    if os.name == "nt":
        cache_base = Path(env["LOCALAPPDATA"]) / "TradingCodex"
    elif sys.platform == "darwin":
        cache_base = Path(env["HOME"]) / "Library" / "Caches" / "TradingCodex"
    else:
        cache_base = Path(env.get("XDG_CACHE_HOME") or (Path(env["HOME"]) / ".cache")) / "tradingcodex"
    scratch_display_path = (cache_base / "scratch-v1" / workspace_id).absolute()
    expected_scratch = scratch_display_path.resolve(strict=False)
    require(same_path(scratch, expected_scratch), "scratch is not under the platform cache location")
    require(scratch.is_dir(), "workspace scratch directory was not created during update")
    require(not scratch.is_symlink(), "workspace scratch directory must not be a symlink")
    require(provider_sources.is_dir(), "provider source staging directory was not created during update")
    require(not provider_sources.is_symlink(), "provider source staging directory must not be a symlink")
    if os.name != "nt":
        require(stat.S_IMODE(scratch.stat().st_mode) == 0o700, "workspace scratch mode is not 0700")
        require(
            stat.S_IMODE(provider_sources.stat().st_mode) == 0o700,
            "provider source staging mode is not 0700",
        )
    if sys.platform == "darwin" and str(scratch_display_path) != str(expected_scratch):
        build_filesystem = build.get("filesystem")
        require(isinstance(build_filesystem, dict), "trading-build filesystem projection is missing")
        require(
            build_filesystem.get(str(scratch_display_path)) == "write",
            "Darwin scratch alias is not writable for Git subprocesses",
        )
    shell_include = shell_policy.get("include_only")
    require(isinstance(shell_include, list), "shell environment include-only projection is missing")
    require(
        {
            "CURL_HOME",
            "WGETRC",
            "GIT_CONFIG_GLOBAL",
            "GIT_CONFIG_SYSTEM",
            "GIT_CONFIG_NOSYSTEM",
            "GIT_CONFIG_COUNT",
            "GIT_CEILING_DIRECTORIES",
            "GIT_PROTOCOL_FROM_USER",
            "GIT_OPTIONAL_LOCKS",
            "GIT_PAGER",
            "GIT_TERMINAL_PROMPT",
            "GCM_INTERACTIVE",
        }.issubset(shell_include),
        "credential-free fetch overrides are not visible to spawned commands",
    )
    require(
        {
            value
            for index in range(len(git_overrides))
            for value in (f"GIT_CONFIG_KEY_{index}", f"GIT_CONFIG_VALUE_{index}")
        }.issubset(shell_include),
        "all command-scope Git overrides must be visible to spawned commands",
    )

    mcp_servers = config.get("mcp_servers")
    tradingcodex_mcp = mcp_servers.get("tradingcodex") if isinstance(mcp_servers, dict) else None
    require(isinstance(tradingcodex_mcp, dict), "TradingCodex MCP projection is missing")
    mcp_env = tradingcodex_mcp.get("env")
    require(isinstance(mcp_env, dict), "TradingCodex MCP environment is missing")
    require(mcp_env.get("TRADINGCODEX_SERVICE_ADDR") == service_addr, "explicit service address changed")
    require(same_path(mcp_env.get("TRADINGCODEX_HOME"), home), "MCP home projection changed")
    require(same_path(mcp_env.get("TRADINGCODEX_DB_NAME"), database), "MCP database projection changed")

    hooks = read_json(workspace / ".codex/hooks.json")
    expected_hook = r".\tcx.cmd __hook session-start" if os.name == "nt" else "./tcx __hook session-start"
    require(
        hooks["hooks"]["SessionStart"][0]["hooks"][0]["command"] == expected_hook,
        "SessionStart hook command does not use the platform workspace launcher",
    )
    hook_path = workspace / ".codex/hooks/tradingcodex_hook.py"
    hook_text = hook_path.read_text(encoding="utf-8")
    generated_git_command_from_hook(hook_path)
    for marker in ("def public_fetch_command_reason(", "def provider_sources_shell_command_reason("):
        require(marker in hook_text, f"new Build fetch hook marker is missing: {marker}")
    generated_files = lock.get("generated_files")
    hook_lock = generated_files.get(hook_path.relative_to(workspace).as_posix()) if isinstance(generated_files, dict) else None
    require(isinstance(hook_lock, dict), "generated hook is absent from module-lock")
    require(
        hook_lock.get("sha256") == hashlib.sha256(hook_path.read_bytes()).hexdigest(),
        "generated hook hash does not match module-lock",
    )

    for launcher in (workspace / "tcx", workspace / "tcx.cmd"):
        require(service_addr in launcher.read_text(encoding="utf-8"), f"service address is missing from {launcher.name}")

    attached_python = Path(str(tradingcodex_mcp.get("command") or ""))
    require(attached_python.is_file(), "candidate managed Python is missing")
    build_filesystem = build.get("filesystem")
    require(isinstance(build_filesystem, dict), "trading-build filesystem projection is missing")
    require(
        build_filesystem.get(str(attached_python.parent.parent)) == "read",
        "candidate launcher runtime root is not read-only in trading-build",
    )
    require(
        build_filesystem.get(str(attached_python)) == "read",
        "candidate launcher Python is not read-only in trading-build",
    )
    build_workspace = build_filesystem.get(":workspace_roots")
    require(isinstance(build_workspace, dict), "trading-build workspace filesystem projection is missing")
    require(
        build_workspace.get(".tradingcodex/workspace.json") == "read",
        "candidate launcher cannot read the immutable workspace manifest",
    )
    require(not same_path(attached_python, Path(sys.executable)), "candidate unexpectedly reused the smoke driver")
    require(
        attached_python.absolute().is_relative_to((home / "runtime/python").absolute()),
        "candidate runtime was not provisioned from the wheel under the explicit TradingCodex home",
    )
    require(
        not attached_python.resolve().is_relative_to(Path(__file__).resolve().parents[1]),
        "candidate runtime leaked the source checkout",
    )
    yaml_config = parse_config_yaml(
        attached_python,
        workspace / ".tradingcodex/config.yaml",
        cwd,
        env,
    )
    require(yaml_config.get("version") == TARGET_VERSION, "generated YAML version is not 1.1.1")
    service = yaml_config.get("service")
    require(isinstance(service, dict), "generated YAML service configuration is missing")
    require(same_path(service.get("default_db"), database), "generated YAML database changed")
    require(service.get("db_source") == "environment_override", "generated YAML database source changed")


def stop_service_best_effort(workspace: Path, cwd: Path, env: dict[str, str], addr: str) -> None:
    if not (workspace / ("tcx.cmd" if os.name == "nt" else "tcx")).is_file():
        return
    try:
        status_result = run(
            launcher_argv(workspace, "service", "status", addr, "--json"),
            cwd=cwd,
            env=env,
            timeout=60,
        )
        status = json.loads(status_result.stdout)
        if status.get("reachable") and status.get("service") == "tradingcodex":
            run(launcher_argv(workspace, "service", "stop", addr), cwd=cwd, env=env, timeout=60)
    except Exception:
        return


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Attach from a public historical TradingCodex wheel and update with the 1.1.1 candidate wheel.",
    )
    parser.add_argument("--wheel-dir", type=Path, required=True, help="directory containing exactly one 1.1.1 wheel")
    parser.add_argument("--from-version", default=DEFAULT_FROM_VERSION, help="public PyPI release to attach first")
    args = parser.parse_args()

    wheel_dir = args.wheel_dir.expanduser().resolve()
    wheels = sorted(wheel_dir.glob("*.whl"))
    if len(wheels) != 1:
        raise SystemExit(f"expected exactly one wheel in {wheel_dir}, found {len(wheels)}")
    candidate_name, candidate_version = wheel_identity(wheels[0])
    if candidate_name.casefold().replace("_", "-") != "tradingcodex" or candidate_version != TARGET_VERSION:
        raise SystemExit(
            f"expected one TradingCodex {TARGET_VERSION} wheel, found {candidate_name or '<unknown>'} "
            f"{candidate_version or '<unknown>'}"
        )
    if args.from_version == TARGET_VERSION:
        raise SystemExit("--from-version must name a release older than the 1.1.1 candidate")

    summary: dict[str, object] = {}
    with tempfile.TemporaryDirectory(prefix="tradingcodex-release-upgrade-") as temporary:
        root = Path(temporary).resolve()
        environment = clean_environment(root)
        require(
            shutil.which("uvx", path=environment.get("PATH")) is not None
            or shutil.which("uv", path=environment.get("PATH")) is not None,
            "release upgrade smoke requires uvx or uv on PATH",
        )
        candidate_dir = root / "Candidate Wheel With Spaces"
        candidate_dir.mkdir()
        candidate_wheel = candidate_dir / wheels[0].name
        shutil.copy2(wheels[0], candidate_wheel)

        release_venv = root / "Public Release Wheel Environment With Spaces"
        venv.EnvBuilder(with_pip=True).create(release_venv)
        release_python = venv_executable(release_venv, "python")
        release_tcx = venv_executable(release_venv, "tcx")
        run(
            [
                str(release_python),
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                "--no-cache-dir",
                "--only-binary=:all:",
                "--index-url",
                PUBLIC_PYPI_INDEX,
                f"tradingcodex=={args.from_version}",
            ],
            cwd=root,
            env=environment,
        )
        public_metadata = run(
            [
                str(release_python),
                "-c",
                (
                    "import sys; from importlib.metadata import distribution; "
                    "d=distribution('tradingcodex'); "
                    "assert d.version == sys.argv[1]; "
                    "assert any(str(p).endswith('.dist-info/WHEEL') for p in (d.files or ())); "
                    "print(d.version)"
                ),
                args.from_version,
            ],
            cwd=root,
            env=environment,
        ).stdout.strip()
        require(public_metadata == args.from_version, "public release wheel metadata mismatch")
        require(
            run([str(release_tcx), "--version"], cwd=root, env=environment).stdout.strip() == args.from_version,
            "public release CLI version mismatch",
        )

        workspace = root / "Workspace Upgraded Across Releases With Spaces"
        explicit_home = root / "Explicit TradingCodex Home With Spaces"
        explicit_database = root / "Explicit TradingCodex State With Spaces/upgrade.sqlite3"
        explicit_database.parent.mkdir(parents=True)
        service_addr = f"127.0.0.1:{free_loopback_port()}"
        attach_environment = {
            **environment,
            "TRADINGCODEX_HOME": str(explicit_home),
            "TRADINGCODEX_HOME_SOURCE": "environment_override",
            "TRADINGCODEX_DB_NAME": str(explicit_database),
            "TRADINGCODEX_SERVICE_ADDR": service_addr,
        }
        run(
            [
                str(release_tcx),
                "attach",
                str(workspace),
                "--from",
                f"tradingcodex=={args.from_version}",
            ],
            cwd=root,
            env=attach_environment,
        )

        clean_post_attach_environment = clean_environment(root)
        workspace_manifest = read_json(workspace / WORKSPACE_MANIFEST)
        workspace_id = str(workspace_manifest.get("workspace_id") or "")
        require(workspace_id.startswith("tcxw_"), "attached workspace_id is invalid")
        initial_lock = read_json(workspace / MODULE_LOCK)
        assert_runtime_identity(
            initial_lock,
            workspace_id=workspace_id,
            home=explicit_home,
            database=explicit_database,
            version=args.from_version,
        )
        initial_config = tomllib.loads((workspace / ".codex/config.toml").read_text(encoding="utf-8"))
        initial_python = Path(initial_config["mcp_servers"]["tradingcodex"]["command"])
        require(initial_python.is_file(), "historical attached Python is missing")
        require(
            not initial_python.resolve().is_relative_to(Path(__file__).resolve().parents[1]),
            "historical public-wheel attach leaked the source checkout",
        )
        require(
            run(
                [
                    str(initial_python),
                    "-c",
                    "from importlib.metadata import version; print(version('tradingcodex'))",
                ],
                cwd=root,
                env=clean_post_attach_environment,
            ).stdout.strip()
            == args.from_version,
            "historical attached Python does not contain the requested public release",
        )
        require(
            run(launcher_argv(workspace, "--version"), cwd=root, env=clean_post_attach_environment).stdout.strip()
            == args.from_version,
            "attached historical launcher version mismatch",
        )
        initial_service = json.loads(
            run(
                launcher_argv(workspace, "service", "status", "--json"),
                cwd=root,
                env=clean_post_attach_environment,
            ).stdout
        )
        require(initial_service.get("addr") == service_addr, "historical launcher lost the explicit service address")
        require(initial_service.get("reachable") is False, "selected upgrade-smoke service address is unexpectedly occupied")

        preserved = create_user_fixtures(
            workspace,
            explicit_home,
            explicit_database,
            root,
            clean_post_attach_environment,
        )
        require(preserved.files, "user preservation fixtures were not created")

        try:
            candidate_spec = str(candidate_wheel)
            update_argv = package_runner_argv(
                clean_post_attach_environment,
                candidate_spec,
                "update",
                str(workspace),
                "--from",
                candidate_spec,
                "--no-doctor",
            )
            run(
                update_argv,
                cwd=root,
                env=clean_post_attach_environment,
                timeout=1200,
            )

            updated_manifest = read_json(workspace / WORKSPACE_MANIFEST)
            require(updated_manifest.get("workspace_id") == workspace_id, "workspace manifest identity changed")
            require(
                {
                    "active_profile": updated_manifest.get("active_profile"),
                    "execution_mode": updated_manifest.get("execution_mode"),
                }
                == preserved.manifest_state,
                "active paper profile or execution mode changed during update",
            )
            updated_lock = read_json(workspace / MODULE_LOCK)
            assert_runtime_identity(
                updated_lock,
                workspace_id=workspace_id,
                home=explicit_home,
                database=explicit_database,
                version=TARGET_VERSION,
            )
            require(
                updated_lock.get("tradingcodex_package_spec") == "local-explicit",
                "candidate wheel update was not recorded as a local explicit source",
            )
            require(
                snapshot_paths(workspace, preserved.roots) == preserved.files,
                "user-owned research, source snapshot, Brain, Strategy, profile, or connector files changed during update",
            )
            updated_brain = json.loads(
                run(
                    launcher_argv(workspace, "investment-brains", "inspect", BRAIN_ID),
                    cwd=root,
                    env=clean_post_attach_environment,
                ).stdout
            )
            require(updated_brain == preserved.brain_record, "managed Investment Brain state changed during update")
            require(updated_brain.get("active") is True, "managed Investment Brain is no longer active")
            require(
                f"../.agents/skills/{BRAIN_ID}/SKILL.md"
                in (workspace / ".codex/config.toml").read_text(encoding="utf-8"),
                "managed Investment Brain is absent from the updated Head Manager projection",
            )
            candidate_config = tomllib.loads((workspace / ".codex/config.toml").read_text(encoding="utf-8"))
            candidate_python = Path(candidate_config["mcp_servers"]["tradingcodex"]["command"])
            updated_provider_state = provider_source_state(
                candidate_python,
                workspace,
                explicit_home,
                explicit_database,
                root,
                clean_post_attach_environment,
            )
            require(
                updated_provider_state == preserved.provider_state,
                "historical approved provider state changed during update",
            )
            provider_snapshot = explicit_home / Path(
                str(updated_provider_state.get("snapshot_relative_path") or "")
            )
            require(
                snapshot_tree(provider_snapshot) == preserved.provider_snapshot_files,
                "historical approved provider snapshot changed during update",
            )
            loaded_provider = loaded_provider_state(workspace, root, clean_post_attach_environment)
            require(
                loaded_provider["provider_source"] == preserved.provider_state,
                "candidate runtime did not load the preserved approved provider snapshot",
            )
            updated_connector_state = read_connector_state(workspace, root, clean_post_attach_environment)
            require(
                updated_connector_state == preserved.connector_state,
                "central-ledger connector or paper-account scope changed during update",
            )
            require(
                central_ledger_state(
                    explicit_database,
                    dict(preserved.manifest_state["active_profile"]),
                )
                == preserved.ledger_state,
                "central-ledger connector rows changed during update",
            )

            assert_candidate_projection(
                workspace,
                lock=updated_lock,
                home=explicit_home,
                database=explicit_database,
                service_addr=service_addr,
                cwd=root,
                env=clean_post_attach_environment,
            )
            require(
                run(launcher_argv(workspace, "--version"), cwd=root, env=clean_post_attach_environment).stdout.strip()
                == TARGET_VERSION,
                "updated launcher version is not 1.1.1",
            )
            pre_service = json.loads(
                run(
                    launcher_argv(workspace, "service", "status", "--json"),
                    cwd=root,
                    env=clean_post_attach_environment,
                ).stdout
            )
            require(pre_service.get("addr") == service_addr, "updated launcher lost the explicit service address")
            run(launcher_argv(workspace, "doctor"), cwd=root, env=clean_post_attach_environment)
            run(launcher_argv(workspace, "service", "ensure"), cwd=root, env=clean_post_attach_environment)
            service = json.loads(
                run(
                    launcher_argv(workspace, "service", "status", "--json"),
                    cwd=root,
                    env=clean_post_attach_environment,
                ).stdout
            )
            require(service.get("addr") == service_addr, "service started on the wrong address")
            require(service.get("service") == "tradingcodex", "unexpected loopback service identity")
            require(service.get("version") == TARGET_VERSION, "service version is not 1.1.1")
            require(service.get("package_version") == TARGET_VERSION, "service package version is not 1.1.1")
            require(service.get("reachable") is True, "updated service is not reachable")
            require(service.get("compatible") is True, "updated service is not compatible")
            require(service.get("ready") is True, "updated service is not ready")
            require(same_path(service.get("db_path"), explicit_database), "service database path changed")
            require(same_path(service.get("expected_db_path"), explicit_database), "expected service database path changed")
            require(service.get("issue") == "", f"service reported an issue: {service.get('issue')}")
            require(
                central_ledger_state(
                    explicit_database,
                    dict(preserved.manifest_state["active_profile"]),
                )
                == preserved.ledger_state,
                "central-ledger connector rows changed after candidate service startup",
            )

            run(
                launcher_argv(workspace, "service", "stop", service_addr),
                cwd=root,
                env=clean_post_attach_environment,
                timeout=60,
            )
            stopped = json.loads(
                run(
                    launcher_argv(workspace, "service", "status", service_addr, "--json"),
                    cwd=root,
                    env=clean_post_attach_environment,
                ).stdout
            )
            require(stopped.get("reachable") is False, "service cleanup did not release the loopback address")
            summary = {
                "status": "ok",
                "platform": sys.platform,
                "from_version": args.from_version,
                "candidate_version": TARGET_VERSION,
                "workspace_id": workspace_id,
                "update_route": "direct-candidate-package-runner",
                "package_runner": Path(update_argv[0]).name,
                "preserved_files": sorted(preserved.files),
                "source_snapshot_path": preserved.source_snapshot_path,
                "investment_brain": BRAIN_ID,
                "approved_provider": HISTORICAL_PROVIDER_ID,
                "registered_broker": REGISTERED_BROKER_ID,
                "explicit_home": str(explicit_home),
                "explicit_database": str(explicit_database),
                "service_addr": service_addr,
            }
        finally:
            stop_service_best_effort(workspace, root, clean_post_attach_environment, service_addr)

    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()

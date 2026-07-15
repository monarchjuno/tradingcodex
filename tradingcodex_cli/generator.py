from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from packaging.version import InvalidVersion, Version

from tradingcodex_service.version import TRADINGCODEX_VERSION
from tradingcodex_service.application.agents import (
    AGENT_INDEX_PATH,
    MANIFEST_PATH,
    MODEL_POLICY_MANIFEST_PATH,
    SKILL_INDEX_PATH,
    project_agent_configuration,
)
from tradingcodex_service.application.components import list_harness_components
from tradingcodex_service.application.common import atomic_write_text, exclusive_file_lock, workspace_launcher_command
from tradingcodex_service.application.customization import CODEX_MCP_BLOCK_NAME, replace_managed_block
from tradingcodex_service.application.runtime import (
    assert_runtime_home_outside_workspace,
    assert_runtime_database_compatible,
    ensure_workspace_manifest,
    read_workspace_manifest,
    resolve_tradingcodex_home,
    tradingcodex_db_path,
)
from tradingcodex_service.application.workspace_git import ensure_workspace_git
from tradingcodex_cli.package_source import (
    EXECUTABLE_SOURCE_ENV,
    LOCAL_EXECUTABLE_SOURCE_KIND,
    LOCAL_EXECUTABLE_SOURCE_PROVENANCE,
    PACKAGE_SOURCE_KIND_ENV,
    PERSISTENT_EXECUTABLE_SOURCE_KIND,
    PRIOR_RUNTIME_PYTHON_ENV,
    canonical_executable_source,
    configured_executable_source,
    executable_source_is_local,
    validate_executable_source,
)
from tradingcodex_cli.startup_status import write_server_status_snapshot

DEFAULT_MODULE_IDS = [
    "codex-base",
    "fixed-subagents",
    "repo-skills",
    "guidance-guardrails",
    "enforcement-guardrails",
    "information-barriers",
    "audit",
    "tradingcodex-mcp",
    "paper-trading",
]
MODULE_LOCK_FORMAT = "tradingcodex.module-lock"
MODULE_LOCK_SCHEMA_VERSION = 1
MODULE_LOCK_FIELDS = frozenset({
    "format",
    "schema_version",
    "generated_at",
    "workspace_id",
    "tradingcodex_version",
    "tradingcodex_package_spec",
    "tradingcodex_home",
    "home_source",
    "tradingcodex_db_path",
    "db_source",
    "modules",
    "generated_files",
})
MODULE_LOCK_MODULE_FIELDS = frozenset({"id", "description", "capabilities"})
MODULE_LOCK_GENERATED_FILE_FIELDS = frozenset({"sha256", "owner"})
MODULE_LOCK_HOME_SOURCES = frozenset({"platform_default", "environment_override"})
MODULE_LOCK_DB_SOURCES = frozenset({"home_default", "environment_override"})
MODULE_LOCK_GENERATED_FILE_OWNERS = frozenset({"template", "projection"})
GENERATED_INDEX_PATHS = frozenset({
    AGENT_INDEX_PATH.as_posix(),
    MANIFEST_PATH.as_posix(),
    MODEL_POLICY_MANIFEST_PATH.as_posix(),
    SKILL_INDEX_PATH.as_posix(),
    ".tradingcodex/generated/capability-index.json",
    ".tradingcodex/generated/component-index.json",
})
BOOTSTRAP_WRITE_PATHS = frozenset({
    ".gitignore",
    ".tradingcodex/generated/.bootstrap.lock",
    ".tradingcodex/generated/module-lock.json",
    ".tradingcodex/mainagent/server-status.json",
    ".tradingcodex/profiles.json",
    ".tradingcodex/workspace.json",
})
MANAGED_PYTHON_RUNTIME_ROOT = Path("runtime/python")
LOCAL_RUNTIME_SOURCE_ROOTS = (
    "pyproject.toml",
    "uv.lock",
    "MANIFEST.in",
    "setup.cfg",
    "setup.py",
    "README.md",
    "LICENSE",
    "NOTICE",
    "tradingcodex_cli",
    "tradingcodex_service",
    "apps",
    "workspace_templates",
)
LOCAL_RUNTIME_IGNORED_PARTS = frozenset({
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "state",
    "venv",
})
LOCAL_RUNTIME_IGNORED_SUFFIXES = frozenset({".pyc", ".pyo", ".sqlite3"})


@dataclass(frozen=True)
class Module:
    id: str
    description: str
    dir: Path
    manifest: dict[str, Any]


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def templates_dir() -> Path:
    return repo_root() / "workspace_templates"


def bootstrap_workspace(
    project_dir: Path | str,
    dry_run: bool = False,
    module_ids: list[str] | None = None,
    *,
    update: bool = False,
) -> dict[str, Any]:
    requested_target = Path(project_dir).expanduser().absolute()
    if requested_target.is_symlink():
        raise ValueError(f"TradingCodex workspace target cannot be a symlink: {requested_target}")
    target = requested_target.resolve(strict=False)
    if target.exists() and not target.is_dir():
        raise ValueError(f"TradingCodex workspace target is not a directory: {target}")
    existing_metadata = workspace_metadata_exists(target)
    validated_workspace: dict[str, Any] = {}
    if update:
        validated_workspace = validate_generated_workspace(target)
    elif existing_metadata:
        raise ValueError(f"TradingCodex is already attached at {target}; use tcx update")
    elif target.exists() and not target_has_only_bootstrap_files(target):
        raise ValueError(
            f"Target directory already has files: {target}. "
            "Use an empty directory or a git-initialized empty directory."
        )
    registry = load_module_registry(templates_dir())
    modules = resolve_module_graph(registry, module_ids or DEFAULT_MODULE_IDS)
    existing_manifest = read_workspace_manifest(target)
    workspace_id = str(existing_manifest.get("workspace_id") or f"tcxw_{uuid.uuid4().hex}")
    context = _generation_context(target, workspace_id, provision_runtime=False)
    assert_runtime_home_outside_workspace(target, context["TRADINGCODEX_HOME"])
    rendered_preview = render_template_modules(modules, context)
    _preserve_managed_codex_mcp_block(target, rendered_preview)
    previous_lock = validated_workspace.get("module_lock") or {}
    current_generated_paths = set(rendered_preview) | set(GENERATED_INDEX_PATHS)
    _validate_generated_destinations(
        target,
        current_generated_paths | set(previous_lock.get("generated_files") or {}) | set(BOOTSTRAP_WRITE_PATHS),
    )
    _validate_stale_generated_files(
        target,
        previous_lock.get("generated_files") or {},
        current_generated_paths,
    )
    result = {
        "target_dir": str(target),
        "workspace_id": workspace_id,
        "modules": [module.id for module in modules],
        "capabilities": collect_capabilities(modules),
        "tradingcodex_home": context["TRADINGCODEX_HOME"],
        "home_source": context["TRADINGCODEX_HOME_SOURCE"],
        "tradingcodex_db_path": context["TRADINGCODEX_DB_PATH"],
        "db_source": context["TRADINGCODEX_DB_SOURCE"],
    }
    if dry_run:
        return result
    assert_runtime_database_compatible(target)
    context = _generation_context(target, workspace_id, provision_runtime=True)
    result.update({
        "tradingcodex_home": context["TRADINGCODEX_HOME"],
        "home_source": context["TRADINGCODEX_HOME_SOURCE"],
        "tradingcodex_db_path": context["TRADINGCODEX_DB_PATH"],
        "db_source": context["TRADINGCODEX_DB_SOURCE"],
    })
    rendered = render_template_modules(modules, context)
    _preserve_managed_codex_mcp_block(target, rendered)
    current_generated_paths = set(rendered) | set(GENERATED_INDEX_PATHS)
    _validate_generated_destinations(
        target,
        current_generated_paths | set(previous_lock.get("generated_files") or {}) | set(BOOTSTRAP_WRITE_PATHS),
    )
    _validate_stale_generated_files(
        target,
        previous_lock.get("generated_files") or {},
        current_generated_paths,
    )
    target.mkdir(parents=True, exist_ok=True)
    ensure_workspace_git(target, initialize_if_missing=not update)
    bootstrap_lock = target / ".tradingcodex" / "generated" / "bootstrap"
    with exclusive_file_lock(bootstrap_lock, timeout_seconds=30):
        existing_manifest = read_workspace_manifest(target)
        workspace_id = str(existing_manifest.get("workspace_id") or workspace_id)
        if workspace_id != context["WORKSPACE_ID"]:
            raise ValueError("TradingCodex workspace identity changed during generation")
        _preserve_managed_codex_mcp_block(target, rendered)
        current_generated_paths = set(rendered) | set(GENERATED_INDEX_PATHS)
        _validate_generated_destinations(
            target,
            current_generated_paths | set(previous_lock.get("generated_files") or {}) | set(BOOTSTRAP_WRITE_PATHS),
        )
        _validate_stale_generated_files(
            target,
            previous_lock.get("generated_files") or {},
            current_generated_paths,
        )
        write_rendered_templates(target, rendered)
        ensure_workspace_manifest(
            target,
            project_name=context["PROJECT_NAME"],
            generated_at=context["GENERATED_AT"],
            workspace_id=workspace_id,
        )
        # Remove preflight-validated retired files before projection so renamed
        # bundled role skills cannot be mistaken for user optional skills.
        _remove_stale_generated_files(
            target,
            previous_lock.get("generated_files") or {},
            current_generated_paths,
        )
        project_agent_configuration(target, applied_by="bootstrap", generated_at=context["GENERATED_AT"])
        write_generated_indexes(target, modules, context, set(rendered), previous_lock)
        write_server_status_snapshot(target)
    result["workspace_id"] = workspace_id
    return result


def _generation_context(
    target: Path,
    workspace_id: str,
    *,
    provision_runtime: bool,
) -> dict[str, str]:
    resolution = resolve_tradingcodex_home()
    assert_runtime_home_outside_workspace(target, resolution.home)
    configured_codex_home = str(os.environ.get("CODEX_HOME") or "").strip()
    codex_home = (
        Path(configured_codex_home).expanduser()
        if configured_codex_home
        else Path.home() / ".codex"
    ).resolve(strict=False)
    if (
        codex_home == resolution.home
        or codex_home.is_relative_to(resolution.home)
        or resolution.home.is_relative_to(codex_home)
    ):
        raise ValueError(
            "CODEX_HOME and TRADINGCODEX_HOME must be separate paths so Codex proxy access "
            "cannot reopen TradingCodex runtime state"
        )
    scratch_path = (
        Path(tempfile.gettempdir()).resolve()
        / "tradingcodex-scratch-v1"
        / workspace_id
    )
    scratch_path.mkdir(parents=True, exist_ok=True)
    if scratch_path.is_symlink() or not scratch_path.is_dir():
        raise ValueError("TradingCodex scratch path must be a real directory")
    try:
        scratch_path.chmod(0o700)
    except OSError:
        pass
    db_override = bool(str(os.environ.get("TRADINGCODEX_DB_NAME") or "").strip())
    declared_source = str(os.environ.get(EXECUTABLE_SOURCE_ENV) or "")
    recorded_source_kind = str(os.environ.get(PACKAGE_SOURCE_KIND_ENV) or "")
    if declared_source:
        executable_source = configured_executable_source(None)
        source_is_local = executable_source_is_local(executable_source)
    elif recorded_source_kind == LOCAL_EXECUTABLE_SOURCE_KIND:
        executable_source = ""
        source_is_local = True
    else:
        executable_source = configured_executable_source(None)
        source_is_local = False
    persisted_package_spec = (
        LOCAL_EXECUTABLE_SOURCE_PROVENANCE if source_is_local else executable_source
    )
    generated_python = resolve_generated_python(
        package_spec=executable_source or persisted_package_spec,
        runtime_home=resolution.home,
        provision_managed=provision_runtime,
        prior_runtime_python=str(os.environ.get(PRIOR_RUNTIME_PYTHON_ENV) or ""),
        force_managed=(
            bool(executable_source and source_is_local)
            or os.environ.get("TRADINGCODEX_LAUNCHED_BY_PACKAGE_RUNNER") == "1"
        ),
    )
    raw = {
        "PROJECT_NAME": sanitize_project_name(target.name or "tradingcodex-workspace"),
        "WORKSPACE_ID": workspace_id,
        "GENERATED_AT": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "TRADINGCODEX_VERSION": TRADINGCODEX_VERSION,
        "TRADINGCODEX_MCP_PACKAGE_SPEC": "" if source_is_local else executable_source,
        "TRADINGCODEX_MCP_PYTHONPATH": "",
        "TRADINGCODEX_PACKAGE_PROVENANCE": persisted_package_spec,
        "TRADINGCODEX_PACKAGE_SOURCE_KIND": (
            LOCAL_EXECUTABLE_SOURCE_KIND
            if source_is_local
            else PERSISTENT_EXECUTABLE_SOURCE_KIND
        ),
        "TRADINGCODEX_PYTHON": generated_python,
        "TRADINGCODEX_WORKSPACE_ROOT": str(target.resolve()),
        "TRADINGCODEX_SCRATCH_PATH": str(scratch_path),
        "CODEX_HOME_PATH": str(codex_home),
        "CODEX_HOME_PROXY_PATH": str(codex_home / "proxy"),
        "CODEX_HOME_STANDALONE_PATH": str(codex_home / "packages" / "standalone"),
        "TRADINGCODEX_HOME": str(resolution.home),
        "TRADINGCODEX_HOME_SOURCE": resolution.home_source,
        "TRADINGCODEX_DB_PATH": str(tradingcodex_db_path()),
        "TRADINGCODEX_DB_SOURCE": "environment_override" if db_override else "home_default",
        "TRADINGCODEX_SERVICE_ADDR": os.environ.get("TRADINGCODEX_SERVICE_ADDR", "127.0.0.1:48267"),
        "TRADINGCODEX_HOOK_COMMAND": f"{workspace_launcher_command()} __hook",
        "TRADINGCODEX_WORKSPACE_LAUNCHER": workspace_launcher_command(),
    }
    return serialized_template_context(raw)


def resolve_generated_python(
    *,
    pythonpath: str = "",
    package_spec: str = "tradingcodex",
    runtime_home: Path | str | None = None,
    provision_managed: bool = True,
    prior_runtime_python: str = "",
    force_managed: bool = False,
) -> str:
    """Select a durable interpreter for generated launchers and MCP config."""

    configured = str(os.environ.get("TRADINGCODEX_PYTHON") or "").strip()
    candidate = Path(configured).expanduser() if configured else Path(sys.executable)
    if configured and not candidate.is_absolute():
        raise ValueError("TRADINGCODEX_PYTHON must be an absolute interpreter path")
    candidate = candidate.absolute()
    if configured:
        if generated_python_path_is_ephemeral(candidate):
            raise ValueError(
                "TradingCodex cannot persist a uv cache or ephemeral package-runner Python; "
                "set TRADINGCODEX_PYTHON to a stable Python 3.11 through 3.14 interpreter"
            )
        _validate_generated_python(candidate, pythonpath=pythonpath)
        return str(candidate)
    if prior_runtime_python or force_managed or generated_python_path_is_ephemeral(candidate):
        if runtime_home is None:
            raise ValueError("TradingCodex managed Python runtime home is unavailable")
        managed_python = managed_python_runtime_path(runtime_home, package_spec)
        if not provision_managed:
            _runtime_bootstrap_python(prior_runtime_python)
            return str(managed_python)
        return str(
            ensure_managed_python_runtime(
                runtime_home,
                package_spec,
                pythonpath=pythonpath,
                bootstrap_python=prior_runtime_python,
            )
        )
    _validate_generated_python(candidate, pythonpath=pythonpath)
    return str(candidate)


def _validate_generated_python(candidate: Path, *, pythonpath: str = "") -> None:
    if not candidate.is_file():
        raise ValueError(f"generated TradingCodex Python does not exist or is not a file: {candidate}")
    if generated_python_path_is_ephemeral(candidate):
        raise ValueError(f"generated TradingCodex Python is under a disposable uv cache: {candidate}")
    probe_environment = os.environ.copy()
    probe_environment.pop("PYTHONHOME", None)
    probe_environment.pop("PYTHONPATH", None)
    if pythonpath:
        probe_environment["PYTHONPATH"] = pythonpath
    try:
        probe = subprocess.run(
            [
                str(candidate),
                "-c",
                (
                    "import sys; "
                    "sys.exit(2) if not ((3, 11) <= sys.version_info < (3, 15)) else None; "
                    "import tradingcodex_cli.__main__, tradingcodex_service.mcp_runtime; "
                    "from tradingcodex_service.version import TRADINGCODEX_VERSION as v; "
                    f"sys.exit(3) if v != {TRADINGCODEX_VERSION!r} else None"
                ),
            ],
            cwd=candidate.parent,
            env=probe_environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError(f"generated TradingCodex Python is not executable: {candidate}") from exc
    if probe.returncode == 2:
        raise ValueError(
            "generated TradingCodex Python must be a working Python 3.11 through 3.14 "
            f"interpreter: {candidate}"
        )
    if probe.returncode == 3:
        raise ValueError(
            "generated TradingCodex Python has a different TradingCodex version: "
            f"{candidate}"
        )
    if probe.returncode != 0:
        raise ValueError(
            "generated TradingCodex Python cannot import the TradingCodex MCP runtime: "
            f"{candidate}"
        )


def managed_python_runtime_path(runtime_home: Path | str, package_spec: str) -> Path:
    home = Path(runtime_home).expanduser().resolve(strict=False)
    runtime_key = _managed_python_runtime_key(package_spec)
    environment = home / MANAGED_PYTHON_RUNTIME_ROOT / runtime_key
    return environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def ensure_managed_python_runtime(
    runtime_home: Path | str,
    package_spec: str,
    *,
    pythonpath: str = "",
    bootstrap_python: str = "",
) -> Path:
    runtime_python = managed_python_runtime_path(runtime_home, package_spec)
    runtime_environment = runtime_python.parent.parent
    runtime_parent = runtime_environment.parent
    runtime_parent.mkdir(parents=True, exist_ok=True)
    lock_target = runtime_parent / f"{runtime_environment.name}.provision"
    with exclusive_file_lock(lock_target, timeout_seconds=300):
        if runtime_python.is_file():
            _validate_generated_python(runtime_python, pythonpath=pythonpath)
            return runtime_python
        if runtime_environment.exists() or runtime_environment.is_symlink():
            raise ValueError(
                "TradingCodex managed Python runtime is incomplete; remove it and retry: "
                f"{runtime_environment}"
            )
        uv = shutil.which("uv")
        if not uv:
            raise ValueError(
                "uv is required to provision the durable TradingCodex Python runtime"
            )
        base_python = _runtime_bootstrap_python(bootstrap_python)
        staging = Path(
            tempfile.mkdtemp(prefix=f".{runtime_environment.name}.", dir=runtime_parent)
        )
        source_snapshot: Path | None = None
        install_spec = _managed_runtime_install_spec(package_spec)
        if executable_source_is_local(package_spec):
            local_source = Path(
                canonical_executable_source(package_spec, require_local_exists=True)
            )
            if local_source.is_dir():
                source_snapshot = _snapshot_local_runtime_source(
                    local_source,
                    runtime_parent,
                    prefix=f".{runtime_environment.name}.source.",
                )
                install_spec = str(source_snapshot)
        environment = os.environ.copy()
        for key in (
            "PYTHONHOME",
            "PYTHONPATH",
            "UV_NO_CACHE",
            "VIRTUAL_ENV",
            PRIOR_RUNTIME_PYTHON_ENV,
        ):
            environment.pop(key, None)
        try:
            _run_runtime_provision_command(
                [uv, "venv", "--no-project", "--python", str(base_python), str(staging)],
                environment,
            )
            staging_python = staging / (
                "Scripts/python.exe" if os.name == "nt" else "bin/python"
            )
            _run_runtime_provision_command(
                [
                    uv,
                    "pip",
                    "install",
                    "--python",
                    str(staging_python),
                    "--link-mode",
                    "copy",
                    "--strict",
                    install_spec,
                ],
                environment,
            )
            _validate_generated_python(staging_python, pythonpath=pythonpath)
            staging.replace(runtime_environment)
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        finally:
            if source_snapshot is not None:
                shutil.rmtree(source_snapshot, ignore_errors=True)
    _validate_generated_python(runtime_python, pythonpath=pythonpath)
    return runtime_python


def _runtime_bootstrap_python(prior_runtime_python: str) -> Path:
    if prior_runtime_python:
        prior = Path(prior_runtime_python).expanduser()
        if not prior.is_absolute():
            raise ValueError("TradingCodex prior runtime Python must be an absolute path")
        candidates = [prior]
    else:
        candidates = [
            Path(str(getattr(sys, "_base_executable", "") or sys.executable)).expanduser(),
            Path(sys.executable).expanduser(),
        ]
    for candidate in dict.fromkeys(path.absolute() for path in candidates):
        if _bootstrap_python_is_usable(
            candidate,
            require_tradingcodex_runtime=bool(prior_runtime_python),
        ):
            return candidate
    raise ValueError("a stable Python 3.11 through 3.14 is required to provision the TradingCodex runtime")


def _bootstrap_python_is_usable(
    candidate: Path,
    *,
    require_tradingcodex_runtime: bool,
) -> bool:
    if not candidate.is_file() or generated_python_path_is_ephemeral(candidate):
        return False
    try:
        probe_environment = os.environ.copy()
        probe_environment.pop("PYTHONHOME", None)
        probe_environment.pop("PYTHONPATH", None)
        probe_source = (
            "import sys; "
            "sys.exit(2) if not ((3, 11) <= sys.version_info < (3, 15)) else None; "
        )
        if require_tradingcodex_runtime:
            probe_source += (
                "import tradingcodex_cli.commands.mcp, "
                "tradingcodex_service.mcp_runtime"
            )
        probe = subprocess.run(
            [
                str(candidate),
                "-c",
                probe_source,
            ],
            env=probe_environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return probe.returncode == 0


def _run_runtime_provision_command(argv: list[str], environment: dict[str, str]) -> None:
    try:
        result = subprocess.run(
            argv,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=300,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError("TradingCodex managed Python runtime provisioning failed") from exc
    if result.returncode != 0:
        raise ValueError("TradingCodex managed Python runtime provisioning failed")


def _managed_python_runtime_key(package_spec: str) -> str:
    package_spec = validate_executable_source(package_spec)
    digest = hashlib.sha256()
    digest.update(TRADINGCODEX_VERSION.encode("utf-8"))
    digest.update(b"\0")
    digest.update(str(package_spec).encode("utf-8"))
    if executable_source_is_local(package_spec):
        source = Path(canonical_executable_source(package_spec)).expanduser()
        if source.is_dir():
            _update_local_runtime_source_digest(digest, source)
        elif source.is_file():
            with source.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
    else:
        digest.update(b"\0executing-package\0")
        executing_root = repo_root()
        _update_local_runtime_source_digest(digest, executing_root)
    return f"tradingcodex-{TRADINGCODEX_VERSION}-{digest.hexdigest()[:16]}"


def _update_local_runtime_source_digest(digest: Any, source: Path) -> None:
    for name in LOCAL_RUNTIME_SOURCE_ROOTS:
        root = source / name
        if root.is_symlink():
            digest.update(name.encode("utf-8"))
            digest.update(b"\0L\0")
            digest.update(os.readlink(root).encode("utf-8", errors="surrogateescape"))
            continue
        if root.is_file():
            _update_runtime_file_digest(digest, source, root)
            continue
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*")):
            relative = path.relative_to(source)
            if any(part in LOCAL_RUNTIME_IGNORED_PARTS for part in relative.parts):
                continue
            if path.is_symlink():
                digest.update(relative.as_posix().encode("utf-8"))
                digest.update(b"\0L\0")
                digest.update(os.readlink(path).encode("utf-8", errors="surrogateescape"))
            elif path.is_file() and path.suffix.casefold() not in LOCAL_RUNTIME_IGNORED_SUFFIXES:
                _update_runtime_file_digest(digest, source, path)


def _update_runtime_file_digest(digest: Any, source: Path, path: Path) -> None:
    digest.update(path.relative_to(source).as_posix().encode("utf-8"))
    digest.update(b"\0F\0")
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)


def _snapshot_local_runtime_source(
    source: Path,
    destination_parent: Path,
    *,
    prefix: str = ".tradingcodex-source.",
) -> Path:
    """Copy only runtime-relevant source, excluding stale build products."""

    snapshot = Path(tempfile.mkdtemp(prefix=prefix, dir=destination_parent))
    try:
        for name in LOCAL_RUNTIME_SOURCE_ROOTS:
            source_path = source / name
            destination_path = snapshot / name
            if source_path.is_dir():
                shutil.copytree(
                    source_path,
                    destination_path,
                    ignore=_runtime_source_copy_ignore,
                )
            elif source_path.is_file():
                destination_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source_path, destination_path)
    except Exception:
        shutil.rmtree(snapshot, ignore_errors=True)
        raise
    return snapshot


def _runtime_source_copy_ignore(_directory: str, names: list[str]) -> set[str]:
    return {
        name
        for name in names
        if name in LOCAL_RUNTIME_IGNORED_PARTS
        or Path(name).suffix.casefold() in LOCAL_RUNTIME_IGNORED_SUFFIXES
    }


def _managed_runtime_install_spec(package_spec: str) -> str:
    package_spec = validate_executable_source(package_spec)
    if executable_source_is_local(package_spec):
        return canonical_executable_source(package_spec, require_local_exists=True)
    return package_spec


def generated_python_path_is_ephemeral(path: Path | str) -> bool:
    """Return whether a path belongs to a removable uv cache or temp environment."""

    candidates = [Path(path).expanduser().absolute()]
    try:
        candidates.append(candidates[0].resolve(strict=False))
    except OSError:
        pass
    for candidate in candidates:
        parts = tuple(part.casefold() for part in candidate.parts)
        if "archive-v0" in parts or "builds-v0" in parts:
            return True
    return False


def serialized_template_context(raw: dict[str, str]) -> dict[str, str]:
    raw = dict(raw)
    package_spec = raw.get("TRADINGCODEX_MCP_PACKAGE_SPEC", "tradingcodex")
    source_kind = raw.get("TRADINGCODEX_PACKAGE_SOURCE_KIND")
    if source_kind is None:
        source_kind = (
            LOCAL_EXECUTABLE_SOURCE_KIND
            if package_spec and executable_source_is_local(package_spec)
            else PERSISTENT_EXECUTABLE_SOURCE_KIND
        )
    if source_kind not in {
        LOCAL_EXECUTABLE_SOURCE_KIND,
        PERSISTENT_EXECUTABLE_SOURCE_KIND,
    }:
        raise ValueError("TradingCodex executable source kind is invalid")
    if source_kind == LOCAL_EXECUTABLE_SOURCE_KIND:
        if package_spec:
            raise ValueError("local TradingCodex executable source must not be rendered")
        raw["TRADINGCODEX_MCP_PACKAGE_SPEC"] = ""
        raw["TRADINGCODEX_PACKAGE_PROVENANCE"] = LOCAL_EXECUTABLE_SOURCE_PROVENANCE
        package_runner, package_prefix = "uvx", ("--refresh", "--from")
    else:
        package_spec = validate_executable_source(package_spec)
        raw["TRADINGCODEX_MCP_PACKAGE_SPEC"] = package_spec
        raw["TRADINGCODEX_PACKAGE_PROVENANCE"] = package_spec
        package_runner, package_prefix = resolve_package_runner(package_spec)
    raw["TRADINGCODEX_PACKAGE_SOURCE_KIND"] = source_kind
    raw.setdefault("TRADINGCODEX_PYTHON", str(Path(sys.executable).absolute()))
    raw.setdefault("TRADINGCODEX_MCP_PYTHONPATH", "")
    raw.setdefault("TRADINGCODEX_PACKAGE_RUNNER", package_runner)
    context = dict(raw)
    for key, value in raw.items():
        literal = json.dumps(str(value), ensure_ascii=False)
        context[f"{key}_JSON"] = literal
        context[f"{key}_JSON_INNER"] = literal[1:-1]
        context[f"{key}_PYTHON"] = literal
        context[f"{key}_TOML"] = literal
        context[f"{key}_YAML"] = literal
        context[f"{key}_SHELL"] = shlex.quote(str(value))
        cmd_value = _cmd_set_value(str(value))
        context[f"{key}_CMD_SET"] = cmd_value
        context[f"{key}_CMD"] = f'"{cmd_value}"'
    context["TRADINGCODEX_PACKAGE_RUNNER_PREFIX_TOML"] = ", ".join(
        json.dumps(item, ensure_ascii=False) for item in package_prefix
    )
    context["TRADINGCODEX_PACKAGE_RUNNER_PREFIX_PYTHON"] = json.dumps(list(package_prefix), ensure_ascii=False)
    context["TRADINGCODEX_PACKAGE_RUNNER_PREFIX_SHELL"] = " ".join(shlex.quote(item) for item in package_prefix)
    context["TRADINGCODEX_PACKAGE_RUNNER_PREFIX_CMD"] = " ".join(package_prefix)
    context["TRADINGCODEX_MCP_PACKAGE_SPEC_ENV_TOML"] = (
        f", TRADINGCODEX_MCP_PACKAGE_SPEC = {context['TRADINGCODEX_MCP_PACKAGE_SPEC_TOML']}"
        if package_spec
        else ""
    )
    context["TRADINGCODEX_PACKAGE_SOURCE_KIND_ENV_TOML"] = (
        f", {PACKAGE_SOURCE_KIND_ENV} = {context['TRADINGCODEX_PACKAGE_SOURCE_KIND_TOML']}"
    )
    if source_kind == LOCAL_EXECUTABLE_SOURCE_KIND:
        context["TRADINGCODEX_PACKAGE_RUNNER_FALLBACK_SHELL"] = (
            'echo "tcx: local package source is not stored; rerun update with --from <package-spec>." >&2\n'
            "exit 1"
        )
        context["TRADINGCODEX_PACKAGE_RUNNER_FALLBACK_CMD"] = (
            "echo tcx: local package source is not stored; rerun update with --from ^<package-spec^>. 1^>^&2\n"
            "exit /b 1"
        )
    else:
        context["TRADINGCODEX_PACKAGE_RUNNER_FALLBACK_SHELL"] = (
            f"if command -v {context['TRADINGCODEX_PACKAGE_RUNNER_SHELL']} >/dev/null 2>&1; then\n"
            f"  exec {context['TRADINGCODEX_PACKAGE_RUNNER_SHELL']} "
            f"{context['TRADINGCODEX_PACKAGE_RUNNER_PREFIX_SHELL']} "
            f"{context['TRADINGCODEX_MCP_PACKAGE_SPEC_SHELL']} python "
            '"$TRADINGCODEX_ROOT/.tradingcodex/cli.py" "$@"\n'
            "fi\n\n"
            'echo "tcx: no compatible Python or package runner executable was found." >&2\n'
            "exit 127"
        )
        context["TRADINGCODEX_PACKAGE_RUNNER_FALLBACK_CMD"] = (
            f"where {context['TRADINGCODEX_PACKAGE_RUNNER_CMD']} >nul 2>nul\n"
            "if not errorlevel 1 goto package_runner_launcher\n"
            "echo tcx: no compatible Python or package runner executable was found. 1>&2\n"
            "exit /b 127"
        )
    context["TRADINGCODEX_DB_ENV_TOML"] = (
        f", TRADINGCODEX_DB_NAME = {context['TRADINGCODEX_DB_PATH_TOML']}"
        if raw.get("TRADINGCODEX_DB_SOURCE") == "environment_override"
        else ""
    )
    context["TRADINGCODEX_MCP_PYTHONPATH_ENV_TOML"] = (
        f", PYTHONPATH = {context['TRADINGCODEX_MCP_PYTHONPATH_TOML']}"
        if raw.get("TRADINGCODEX_MCP_PYTHONPATH")
        else ""
    )
    context["TRADINGCODEX_DB_ENV_SHELL"] = (
        "if [ -z \"${TRADINGCODEX_DB_NAME:-}\" ]; then\n"
        f"  export TRADINGCODEX_DB_NAME={context['TRADINGCODEX_DB_PATH_SHELL']}\n"
        "fi"
        if raw.get("TRADINGCODEX_DB_SOURCE") == "environment_override"
        else ""
    )
    context["TRADINGCODEX_DB_ENV_CMD"] = (
        f'if not defined TRADINGCODEX_DB_NAME set "TRADINGCODEX_DB_NAME={context["TRADINGCODEX_DB_PATH_CMD_SET"]}"'
        if raw.get("TRADINGCODEX_DB_SOURCE") == "environment_override"
        else "rem TRADINGCODEX_DB_NAME uses the selected home"
    )
    return context


def resolve_package_runner(package_spec: str) -> tuple[str, tuple[str, ...]]:
    package_spec = validate_executable_source(package_spec)
    if executable_source_is_local(package_spec) and Path(
        canonical_executable_source(package_spec)
    ).is_dir():
        return "uv", ("run", "--no-project", "--with-editable")
    return "uvx", ("--refresh", "--from")


def _cmd_set_value(value: str) -> str:
    if any(character in value for character in ('"', "\r", "\n", "\0")):
        raise ValueError("generated CMD values must not contain quotes or control newlines")
    # Batch expands percent expressions while parsing the file. Doubling the
    # marker preserves it as data inside set "NAME=value" assignments.
    return value.replace("%", "%%")


def load_module_registry(base_templates_dir: Path) -> dict[str, Module]:
    modules_dir = base_templates_dir / "modules"
    registry: dict[str, Module] = {}
    for module_dir in sorted(path for path in modules_dir.iterdir() if path.is_dir()):
        manifest_path = module_dir / "module.json"
        if not manifest_path.exists():
            continue
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        module_id = manifest["id"]
        if module_id != module_dir.name:
            raise ValueError(f'Module id "{module_id}" does not match directory "{module_dir.name}"')
        registry[module_id] = Module(module_id, manifest.get("description", ""), module_dir, manifest)
    return registry


def resolve_module_graph(registry: dict[str, Module], requested_ids: list[str]) -> list[Module]:
    resolved: list[Module] = []
    seen: set[str] = set()
    visiting: set[str] = set()

    def visit(module_id: str, parent_id: str | None = None) -> None:
        if module_id in seen:
            return
        if module_id in visiting:
            raise ValueError(f'Circular module dependency detected at "{module_id}"')
        if module_id not in registry:
            suffix = f' required by "{parent_id}"' if parent_id else ""
            raise ValueError(f'Unknown module "{module_id}"{suffix}')
        visiting.add(module_id)
        module = registry[module_id]
        for dependency in module.manifest.get("requires", {}).get("modules", []):
            visit(dependency, module_id)
        visiting.remove(module_id)
        seen.add(module_id)
        resolved.append(module)

    for module_id in requested_ids:
        visit(module_id)
    assert_no_conflicts(resolved)
    return resolved


def collect_capabilities(modules: list[Module]) -> list[str]:
    capabilities: set[str] = set()
    for module in modules:
        capabilities.update(module.manifest.get("provides", {}).get("capabilities", []))
    return sorted(capabilities)


def target_has_only_bootstrap_files(target: Path) -> bool:
    allowed_names = {".git", ".gitignore", ".gitattributes"}
    return all(child.name in allowed_names for child in target.iterdir())


def workspace_metadata_exists(target: Path) -> bool:
    return any((target / rel).exists() for rel in (".tradingcodex/workspace.json", ".tradingcodex/generated/module-lock.json"))


def read_module_lock(target: Path, *, allow_newer: bool = False) -> dict[str, Any]:
    path = target / ".tradingcodex" / "generated" / "module-lock.json"
    try:
        lock = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"TradingCodex v1 module lock is missing: {path}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"TradingCodex module lock is invalid: {path}") from exc
    _validate_module_lock(target, lock, allow_newer=allow_newer)
    return lock


def _validate_module_lock(target: Path, lock: Any, *, allow_newer: bool = False) -> None:
    if not isinstance(lock, dict):
        raise ValueError("TradingCodex module lock must be an object")
    if lock.get("format") != MODULE_LOCK_FORMAT or lock.get("schema_version") != MODULE_LOCK_SCHEMA_VERSION:
        raise ValueError("unsupported pre-v1 TradingCodex workspace; attach v1 to a clean workspace")
    if set(lock) != MODULE_LOCK_FIELDS:
        raise ValueError("TradingCodex module lock fields do not match the v1 schema")
    if type(lock["schema_version"]) is not int:
        raise ValueError("TradingCodex module lock schema_version must be an integer")
    _validate_module_lock_timestamp(lock["generated_at"])
    if type(lock["workspace_id"]) is not str or not re.fullmatch(r"tcxw_[0-9a-f]{32}", lock["workspace_id"]):
        raise ValueError("TradingCodex module lock workspace_id is invalid")
    workspace_version = lock["tradingcodex_version"]
    if type(workspace_version) is not str:
        raise ValueError("TradingCodex module lock version is invalid")
    try:
        parsed_workspace_version = Version(workspace_version)
        parsed_runtime_version = Version(TRADINGCODEX_VERSION)
    except InvalidVersion as exc:
        raise ValueError("TradingCodex module lock version is invalid") from exc
    if str(parsed_workspace_version) != workspace_version:
        raise ValueError("TradingCodex module lock version must use canonical PEP 440 syntax")
    if (
        parsed_workspace_version.epoch != parsed_runtime_version.epoch
        or parsed_workspace_version.major != parsed_runtime_version.major
    ):
        raise ValueError("TradingCodex workspaces can only be updated within the same major version")
    if parsed_workspace_version > parsed_runtime_version and not allow_newer:
        raise ValueError(
            f"TradingCodex workspace {workspace_version} is newer than runtime {TRADINGCODEX_VERSION}; "
            "update the installed package before updating this workspace"
        )
    package_spec = lock["tradingcodex_package_spec"]
    if type(package_spec) is not str or not package_spec.strip():
        raise ValueError("TradingCodex module lock package spec is missing")
    if package_spec != LOCAL_EXECUTABLE_SOURCE_PROVENANCE:
        validate_executable_source(package_spec)
    _validate_module_lock_absolute_path(lock["tradingcodex_home"], "tradingcodex_home")
    if type(lock["home_source"]) is not str or lock["home_source"] not in MODULE_LOCK_HOME_SOURCES:
        raise ValueError("TradingCodex module lock home_source is invalid")
    _validate_module_lock_absolute_path(lock["tradingcodex_db_path"], "tradingcodex_db_path")
    if type(lock["db_source"]) is not str or lock["db_source"] not in MODULE_LOCK_DB_SOURCES:
        raise ValueError("TradingCodex module lock db_source is invalid")
    modules = lock["modules"]
    if not isinstance(modules, list):
        raise ValueError("TradingCodex module lock modules must be a list")
    module_ids: set[str] = set()
    for module in modules:
        if not isinstance(module, dict) or set(module) != MODULE_LOCK_MODULE_FIELDS:
            raise ValueError("TradingCodex module lock module fields do not match the v1 schema")
        module_id = module["id"]
        if type(module_id) is not str or not module_id.strip():
            raise ValueError("TradingCodex module lock module id is invalid")
        if module_id in module_ids:
            raise ValueError(f"TradingCodex module lock module id is duplicated: {module_id}")
        module_ids.add(module_id)
        if type(module["description"]) is not str:
            raise ValueError(f"TradingCodex module lock module description is invalid: {module_id}")
        capabilities = module["capabilities"]
        if not isinstance(capabilities, list) or any(type(capability) is not str for capability in capabilities):
            raise ValueError(f"TradingCodex module lock module capabilities are invalid: {module_id}")
    generated_files = lock["generated_files"]
    if not isinstance(generated_files, dict):
        raise ValueError("TradingCodex module lock generated_files must be an object")
    for rel, record in generated_files.items():
        if type(rel) is not str or not _is_canonical_generated_path(rel):
            raise ValueError("TradingCodex module lock generated file path is invalid")
        _owned_generated_path(target, rel)
        if not isinstance(record, dict) or set(record) != MODULE_LOCK_GENERATED_FILE_FIELDS:
            raise ValueError(f"TradingCodex module lock generated file fields are invalid: {rel}")
        if type(record["sha256"]) is not str or not re.fullmatch(r"[0-9a-f]{64}", record["sha256"]):
            raise ValueError(f"TradingCodex module lock generated file hash is invalid: {rel}")
        if type(record["owner"]) is not str or record["owner"] not in MODULE_LOCK_GENERATED_FILE_OWNERS:
            raise ValueError(f"TradingCodex module lock generated file owner is invalid: {rel}")


def _validate_module_lock_timestamp(value: Any) -> None:
    if type(value) is not str or not value.strip():
        raise ValueError("TradingCodex module lock generated_at is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("TradingCodex module lock generated_at must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError("TradingCodex module lock generated_at must include a timezone")


def _validate_module_lock_absolute_path(value: Any, field: str) -> None:
    if (
        type(value) is not str
        or not value
        or not (PurePosixPath(value).is_absolute() or PureWindowsPath(value).is_absolute())
    ):
        raise ValueError(f"TradingCodex module lock {field} must be an absolute path")


def _is_canonical_generated_path(rel: str) -> bool:
    path = PurePosixPath(rel)
    return (
        not path.is_absolute()
        and rel == path.as_posix()
        and path.as_posix() != "."
        and ".." not in path.parts
    )


def validate_generated_workspace(target: Path) -> dict[str, Any]:
    manifest = read_workspace_manifest(target)
    if not manifest:
        raise ValueError(f"Not a TradingCodex v1 workspace: {target}")
    lock = read_module_lock(target)
    if lock["workspace_id"] != manifest["workspace_id"]:
        raise ValueError("TradingCodex workspace manifest and module lock identities differ")
    return {"manifest": manifest, "module_lock": lock}


def render_template_modules(modules: list[Module], context: dict[str, str]) -> dict[str, str]:
    rendered: dict[str, str] = {}
    for module in modules:
        files_dir = module.dir / "files"
        if not files_dir.exists():
            continue
        for item in sorted(files_dir.rglob("*")):
            if not item.is_file() or item.name in {"__pycache__", ".DS_Store"} or item.suffix in {".pyc", ".pyo"}:
                continue
            rel = item.relative_to(files_dir).as_posix()
            text = render_template(item.read_text(encoding="utf-8"), context)
            if rel in rendered and rendered[rel] != text:
                raise ValueError(f"generated modules render conflicting content at {rel}")
            rendered[rel] = text
    return rendered


def write_rendered_templates(target: Path, rendered: dict[str, str]) -> None:
    for rel, text in rendered.items():
        destination = _owned_generated_path(target, rel)
        atomic_write_text(destination, text)
        if os.name != "nt":
            destination.chmod(0o755 if text.startswith("#!") else 0o644)


def _preserve_managed_codex_mcp_block(target: Path, rendered: dict[str, str]) -> None:
    rel = ".codex/config.toml"
    existing_path = target / rel
    if rel not in rendered or not existing_path.is_file():
        return
    existing = existing_path.read_text(encoding="utf-8")
    start = f"# BEGIN {CODEX_MCP_BLOCK_NAME}"
    end = f"# END {CODEX_MCP_BLOCK_NAME}"
    if start not in existing and end not in existing:
        return
    if existing.count(start) != 1 or existing.count(end) != 1 or existing.index(start) > existing.index(end):
        raise ValueError("TradingCodex managed Codex MCP block is malformed")
    body = existing.split(start, 1)[1].split(end, 1)[0]
    block = f"{start}{body}{end}\n"
    rendered[rel] = replace_managed_block(rendered[rel], block, CODEX_MCP_BLOCK_NAME)


def write_generated_indexes(
    target: Path,
    modules: list[Module],
    context: dict[str, str],
    template_paths: set[str],
    previous_lock: dict[str, Any],
) -> None:
    generated_dir = target / ".tradingcodex" / "generated"
    generated_dir.mkdir(parents=True, exist_ok=True)
    lock = {
        "format": MODULE_LOCK_FORMAT,
        "schema_version": MODULE_LOCK_SCHEMA_VERSION,
        "generated_at": context["GENERATED_AT"],
        "workspace_id": context["WORKSPACE_ID"],
        "tradingcodex_version": context["TRADINGCODEX_VERSION"],
        "tradingcodex_package_spec": context["TRADINGCODEX_PACKAGE_PROVENANCE"],
        "tradingcodex_home": context["TRADINGCODEX_HOME"],
        "home_source": context["TRADINGCODEX_HOME_SOURCE"],
        "tradingcodex_db_path": context["TRADINGCODEX_DB_PATH"],
        "db_source": context["TRADINGCODEX_DB_SOURCE"],
        "modules": [
            {
                "id": module.id,
                "description": module.description,
                "capabilities": module.manifest.get("provides", {}).get("capabilities", []),
            }
            for module in modules
        ],
    }
    capability_index = {
        "generated_at": context["GENERATED_AT"],
        "capabilities": collect_capabilities(modules),
    }
    component_index = {
        "generated_at": context["GENERATED_AT"],
        "source": "tradingcodex_service.application.components",
        "components": list_harness_components(),
    }
    capability_path = generated_dir / "capability-index.json"
    component_path = generated_dir / "component-index.json"
    atomic_write_text(capability_path, json.dumps(capability_index, indent=2) + "\n")
    atomic_write_text(component_path, json.dumps(component_index, indent=2) + "\n")
    owned_paths = set(template_paths) | set(GENERATED_INDEX_PATHS)
    _remove_stale_generated_files(target, previous_lock.get("generated_files") or {}, owned_paths)
    lock["generated_files"] = {
        rel: {
            "sha256": hashlib.sha256(_owned_generated_path(target, rel).read_bytes()).hexdigest(),
            "owner": "template" if rel in template_paths else "projection",
        }
        for rel in sorted(owned_paths)
    }
    _validate_module_lock(target, lock)
    atomic_write_text(generated_dir / "module-lock.json", json.dumps(lock, indent=2) + "\n")


def _remove_stale_generated_files(target: Path, previous: dict[str, Any], current: set[str]) -> None:
    _validate_stale_generated_files(target, previous, current)
    for rel in sorted(set(previous) - current):
        path = _owned_generated_path(target, rel)
        if path.is_file() or path.is_symlink():
            path.unlink()


def _validate_stale_generated_files(target: Path, previous: dict[str, Any], current: set[str]) -> None:
    for rel in sorted(set(previous) - current):
        path = _owned_generated_path(target, rel)
        if not path.is_file() and not path.is_symlink():
            continue
        record = previous.get(rel)
        expected_hash = str(record.get("sha256") or "") if isinstance(record, dict) else ""
        actual_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        if not expected_hash or actual_hash != expected_hash:
            raise ValueError(f"retired generated file was modified and must be resolved manually: {rel}")


def _owned_generated_path(target: Path, rel: str) -> Path:
    if not _is_canonical_generated_path(rel):
        raise ValueError(f"generated path is not canonical: {rel}")
    root = target.resolve()
    _validate_generated_destinations(root, {rel})
    return root.joinpath(*PurePosixPath(rel).parts)


def _validate_generated_destinations(target: Path, relative_paths: set[str]) -> None:
    root = target.resolve(strict=False)
    for rel in sorted(relative_paths):
        if not _is_canonical_generated_path(rel):
            raise ValueError(f"generated path is not canonical: {rel}")
        parts = PurePosixPath(rel).parts
        current = root
        for index, part in enumerate(parts):
            current /= part
            if current.is_symlink():
                raise ValueError(f"generated destination cannot be a symlink or traverse one: {rel}")
            if current.exists() and index < len(parts) - 1 and not current.is_dir():
                raise ValueError(f"generated destination ancestor is not a directory: {rel}")
        if current.exists() and current.is_dir():
            raise ValueError(f"generated destination must be a file: {rel}")


def assert_no_conflicts(modules: list[Module]) -> None:
    ids = {module.id for module in modules}
    for module in modules:
        for conflict in module.manifest.get("conflicts", []):
            if conflict in ids:
                raise ValueError(f'Module "{module.id}" conflicts with "{conflict}"')


def render_template(source: str, context: dict[str, str]) -> str:
    pattern = re.compile(r"\{\{([A-Z][A-Z0-9_]*)\}\}")
    requested = set(pattern.findall(source))
    missing = sorted(requested - context.keys())
    if missing:
        unresolved = ", ".join(f"{{{{{key}}}}}" for key in missing)
        raise ValueError(f"unresolved generated template values: {unresolved}")
    return pattern.sub(lambda match: context[match.group(1)], source)


def sanitize_project_name(name: str) -> str:
    cleaned = "".join(ch.lower() if ch.isalnum() or ch in "._-" else "-" for ch in name)
    return cleaned.strip("-") or "tradingcodex-workspace"

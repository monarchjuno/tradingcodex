from __future__ import annotations

import hashlib
import hmac
import json
import os
import platform
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import tomllib
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
from tradingcodex_service.application.common import atomic_write_text, exclusive_file_lock, stable_hash, workspace_launcher_command
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
CALCULATION_RUNTIME_SCHEMA_VERSION = 2
CALCULATION_RUNNER_SOURCE = Path(__file__).with_name("calculation_runner.py")
CALCULATION_RUNTIME_LOCK = Path(__file__).with_name("calculation-runtime-lock.json")
CALCULATION_RUNTIME_REQUIREMENTS = Path(__file__).with_name(
    "calculation-runtime-requirements.txt"
)
CALCULATION_RUNTIME_MANIFEST_NAME = "runtime-manifest.json"
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
WINDOWS_REPARSE_POINT_ATTRIBUTE = int(
    getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x0400)
)


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
    context = _generation_context(
        target,
        workspace_id,
        provision_runtime=False,
        provision_scratch=False,
    )
    assert_runtime_home_outside_workspace(target, context["TRADINGCODEX_HOME"])
    rendered_preview = render_template_modules(modules, context)
    _preserve_user_codex_capabilities(target, rendered_preview)
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
    context = _generation_context(
        target,
        workspace_id,
        provision_runtime=True,
        provision_scratch=True,
    )
    result.update({
        "tradingcodex_home": context["TRADINGCODEX_HOME"],
        "home_source": context["TRADINGCODEX_HOME_SOURCE"],
        "tradingcodex_db_path": context["TRADINGCODEX_DB_PATH"],
        "db_source": context["TRADINGCODEX_DB_SOURCE"],
    })
    rendered = render_template_modules(modules, context)
    _preserve_user_codex_capabilities(target, rendered)
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
        _preserve_user_codex_capabilities(target, rendered)
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
    provision_scratch: bool,
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
    _scratch_display_path, scratch_path = _workspace_scratch_paths(
        workspace_id,
        provision=provision_scratch,
        protected_paths={
            "generated workspace": target,
            "TRADINGCODEX_HOME": resolution.home,
            "CODEX_HOME": codex_home,
        },
    )
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
    calculation_runtime_root, calculation_python, calculation_runner = (
        calculation_runtime_paths(
            workspace_id,
            provision=provision_runtime,
            protected_paths={
                "generated workspace": target,
                "TradingCodex scratch": scratch_path,
                "TRADINGCODEX_HOME": resolution.home,
                "TradingCodex database": tradingcodex_db_path(),
                "CODEX_HOME": codex_home,
            },
        )
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
        "TRADINGCODEX_CALCULATION_RUNTIME_ROOT": str(calculation_runtime_root),
        "TRADINGCODEX_CALCULATION_PYTHON": str(calculation_python),
        "TRADINGCODEX_CALCULATION_RUNNER": str(calculation_runner),
        "TRADINGCODEX_WORKSPACE_ROOT": str(target.resolve()),
        "TRADINGCODEX_SCRATCH_PATH": str(scratch_path),
        "TRADINGCODEX_NULL_DEVICE": os.devnull,
        "TRADINGCODEX_GIT_COMMAND": resolve_generated_git_command(),
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
    context = serialized_template_context(raw)
    scratch_aliases = sorted(
        str(alias)
        for alias in workspace_scratch_permission_aliases(workspace_id, scratch_path)
    )
    context["TRADINGCODEX_SCRATCH_ALIAS_RULE_TOML"] = "\n".join(
        f'{json.dumps(alias, ensure_ascii=False)} = "write"'
        for alias in scratch_aliases
    )
    return context


def resolve_generated_git_command() -> str:
    """Resolve a real Git executable that is safe to invoke inside Codex.

    On macOS, ``/usr/bin/git`` is an Xcode shim that attempts to populate an
    OS-temporary xcrun cache before launching the developer-tool binary. The
    generated Build profile intentionally denies broad OS temp roots, so pin
    the selected developer directory's real Git binary instead.
    """

    candidate = shutil.which("git") or ""
    if sys.platform == "darwin":
        selected = ""
        if Path("/usr/bin/xcode-select").is_file():
            try:
                selected = subprocess.run(
                    ["/usr/bin/xcode-select", "-p"],
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=5,
                    env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"},
                ).stdout.strip()
            except (OSError, subprocess.SubprocessError):
                pass
        selected_git = Path(selected) / "usr" / "bin" / "git" if selected else Path()
        if selected and selected_git.is_file() and not selected_git.is_symlink() and os.access(selected_git, os.X_OK):
            candidate = str(selected_git)
        elif Path(candidate).absolute() == Path("/usr/bin/git"):
            candidate = ""
    if not candidate:
        return ""
    return str(Path(candidate).expanduser().absolute()).replace("\\", "/")


def _workspace_scratch_display_path(workspace_id: str) -> Path:
    if os.name == "nt":
        cache_base = Path(os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()) / "TradingCodexScratch"
    elif sys.platform == "darwin":
        cache_base = Path.home() / "Library" / "Caches" / "TradingCodex"
    else:
        cache_base = Path(os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache")) / "tradingcodex"
    return cache_base.expanduser().absolute() / "scratch-v1" / workspace_id


def _calculation_runtime_cache_root() -> Path:
    if os.name == "nt":
        cache_base = Path(os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()) / "TradingCodexCalculation"
    elif sys.platform == "darwin":
        cache_base = Path.home() / "Library" / "Caches" / "TradingCodexCalculation"
    else:
        cache_base = Path(os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache")) / "tradingcodex-calculation"
    return cache_base.expanduser().absolute() / f"runtime-v{CALCULATION_RUNTIME_SCHEMA_VERSION}"


def calculation_runtime_paths(
    _workspace_id: str,
    *,
    provision: bool,
    protected_paths: dict[str, Path | str] | None = None,
) -> tuple[Path, Path, Path]:
    """Resolve and optionally provision the pinned financial calculation runtime.

    This runtime is intentionally independent of Django, the MCP process, the
    service ledger, and the package-bearing generated launcher interpreter.
    """

    source = CALCULATION_RUNNER_SOURCE.read_bytes()
    lock_bytes = CALCULATION_RUNTIME_LOCK.read_bytes()
    lock = _load_calculation_runtime_lock(lock_bytes)
    requirements_bytes = CALCULATION_RUNTIME_REQUIREMENTS.read_bytes()
    _validate_calculation_runtime_requirements(requirements_bytes, lock)
    bootstrap_python = _runtime_bootstrap_python("")
    try:
        bootstrap_stat = bootstrap_python.stat()
        bootstrap_identity = {
            "path": str(bootstrap_python.resolve()),
            "sha256": hashlib.sha256(bootstrap_python.read_bytes()).hexdigest(),
            "size": bootstrap_stat.st_size,
            "mtime_ns": bootstrap_stat.st_mtime_ns,
            "host_system": platform.system(),
            "host_machine": platform.machine(),
        }
    except OSError as exc:
        raise ValueError(
            "TradingCodex calculation runtime bootstrap Python is unreadable"
        ) from exc
    digest = hashlib.sha256()
    digest.update(source)
    digest.update(b"\0")
    digest.update(lock_bytes)
    digest.update(b"\0")
    digest.update(requirements_bytes)
    digest.update(b"\0")
    digest.update(
        json.dumps(bootstrap_identity, sort_keys=True).encode("utf-8")
    )
    runtime_root = _calculation_runtime_cache_root() / f"finance-{digest.hexdigest()[:16]}"
    runtime_python = runtime_root / (
        "Scripts/python.exe" if os.name == "nt" else "bin/python"
    )
    runtime_runner = runtime_root / "calculation_runner.py"
    runtime_manifest = runtime_root / CALCULATION_RUNTIME_MANIFEST_NAME
    _validate_calculation_runtime_location(
        runtime_root,
        protected_paths=protected_paths,
    )
    if not provision:
        return runtime_root, runtime_python, runtime_runner

    runtime_parent = runtime_root.parent
    runtime_parent.mkdir(parents=True, exist_ok=True)
    if _path_is_link_or_reparse_point(runtime_parent):
        raise ValueError("TradingCodex calculation runtime parent must be a real directory")
    lock_target = runtime_parent / f".{runtime_root.name}.provision"
    with exclusive_file_lock(lock_target, timeout_seconds=300):
        if runtime_root.exists():
            _validate_calculation_runtime(
                runtime_root,
                runtime_python,
                runtime_runner,
                runtime_manifest,
                source,
                lock_bytes,
                requirements_bytes,
                lock,
            )
            return runtime_root, runtime_python, runtime_runner
        staging = Path(tempfile.mkdtemp(prefix=f".{runtime_root.name}.", dir=runtime_parent))
        environment = os.environ.copy()
        for key in ("PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV", "PYTHONSTARTUP"):
            environment.pop(key, None)
        try:
            _run_runtime_provision_command(
                [
                    str(bootstrap_python),
                    "-m",
                    "venv",
                    "--copies",
                    "--without-pip",
                    str(staging),
                ],
                environment,
            )
            staging_python = staging / (
                "Scripts/python.exe" if os.name == "nt" else "bin/python"
            )
            staging_runner = staging / "calculation_runner.py"
            atomic_write_text(staging_runner, source.decode("utf-8"))
            uv = shutil.which("uv")
            if not uv:
                raise ValueError(
                    "uv is required to provision the TradingCodex calculation runtime"
                )
            try:
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
                        "--no-deps",
                        "--only-binary",
                        ":all:",
                        "--require-hashes",
                        "-r",
                        str(CALCULATION_RUNTIME_REQUIREMENTS),
                    ],
                    environment,
                )
            except ValueError as exc:
                raise ValueError(
                    "the pinned wheel-only TradingCodex financial calculation runtime "
                    "is unavailable for this Python/platform"
                ) from exc
            staging_manifest = staging / CALCULATION_RUNTIME_MANIFEST_NAME
            _write_calculation_runtime_manifest(
                staging,
                staging_python,
                staging_runner,
                staging_manifest,
                lock_bytes,
                requirements_bytes,
                lock,
            )
            try:
                staging_runner.chmod(0o444)
                staging_manifest.chmod(0o444)
            except OSError:
                pass
            _validate_calculation_runtime(
                staging,
                staging_python,
                staging_runner,
                staging_manifest,
                source,
                lock_bytes,
                requirements_bytes,
                lock,
            )
            staging.replace(runtime_root)
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise
    _validate_calculation_runtime(
        runtime_root,
        runtime_python,
        runtime_runner,
        runtime_manifest,
        source,
        lock_bytes,
        requirements_bytes,
        lock,
    )
    return runtime_root, runtime_python, runtime_runner


def _load_calculation_runtime_lock(lock_bytes: bytes) -> dict[str, Any]:
    try:
        lock = json.loads(lock_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("TradingCodex calculation runtime lock is invalid") from exc
    expected_fields = {
        "schema_version",
        "python_requires",
        "requirements_sha256",
        "installer",
        "direct_packages",
        "packages",
    }
    packages = lock.get("packages") if isinstance(lock, dict) else None
    if (
        not isinstance(lock, dict)
        or set(lock) != expected_fields
        or lock.get("schema_version") != 1
        or lock.get("python_requires") != ">=3.11,<3.15"
        or re.fullmatch(r"[0-9a-f]{64}", str(lock.get("requirements_sha256") or ""))
        is None
        or lock.get("installer")
        != {"dependencies": False, "source_distributions": False}
        or not isinstance(packages, dict)
        or not packages
    ):
        raise ValueError("TradingCodex calculation runtime lock is invalid")
    for name, version in packages.items():
        if re.fullmatch(r"[a-z0-9][a-z0-9.-]*", str(name)) is None:
            raise ValueError("TradingCodex calculation runtime lock has an invalid package")
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.+!-]*", str(version)) is None:
            raise ValueError("TradingCodex calculation runtime lock has an invalid version")
    required = {
        "numpy": "2.3.5",
        "pandas": "2.3.3",
        "scipy": "1.16.3",
        "statsmodels": "0.14.6",
        "numpy-financial": "1.0.0",
        "pyarrow": "25.0.0",
    }
    if lock.get("direct_packages") != sorted(required) or any(
        packages.get(name) != version for name, version in required.items()
    ):
        raise ValueError("TradingCodex calculation runtime direct package pins changed")
    return lock


def _validate_calculation_runtime_requirements(
    requirements_bytes: bytes,
    lock: dict[str, Any],
) -> dict[str, tuple[str, ...]]:
    expected_digest = str(lock.get("requirements_sha256") or "")
    actual_digest = hashlib.sha256(requirements_bytes).hexdigest()
    if not hmac.compare_digest(expected_digest, actual_digest):
        raise ValueError("TradingCodex calculation requirements hash mismatch")
    try:
        lines = requirements_bytes.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise ValueError("TradingCodex calculation requirements must be UTF-8") from exc
    packages: dict[str, dict[str, Any]] = {}
    current = ""
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        requirement = re.fullmatch(
            r"([a-z0-9][a-z0-9.-]*)==([A-Za-z0-9][A-Za-z0-9_.+!-]*) \\",
            line,
        )
        if requirement:
            current = requirement.group(1)
            if current in packages:
                raise ValueError("TradingCodex calculation requirements contain duplicates")
            packages[current] = {"version": requirement.group(2), "hashes": []}
            continue
        hash_line = re.fullmatch(r"--hash=sha256:([0-9a-f]{64})(?: \\)?", line)
        if hash_line and current:
            packages[current]["hashes"].append(hash_line.group(1))
            continue
        raise ValueError("TradingCodex calculation requirements structure is invalid")
    expected_packages = lock.get("packages")
    actual_packages = {
        name: record["version"] for name, record in packages.items()
    }
    if actual_packages != expected_packages or any(
        not record["hashes"] for record in packages.values()
    ):
        raise ValueError("TradingCodex calculation requirements do not match the package lock")
    return {
        name: tuple(record["hashes"])
        for name, record in packages.items()
    }


def _calculation_site_packages(runtime_root: Path, runtime_python: Path) -> Path:
    if os.name == "nt":
        return runtime_root / "Lib" / "site-packages"
    environment = {
        key: value
        for key, value in os.environ.items()
        if key in {"COMSPEC", "LANG", "LC_ALL", "PATH", "PATHEXT", "SYSTEMROOT", "WINDIR"}
        or key.startswith("LC_")
    }
    version = subprocess.run(
        [str(runtime_python), "-I", "-S", "-c", "import sys; print(f'{sys.version_info[0]}.{sys.version_info[1]}')"],
        cwd=runtime_root,
        env=environment,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=15,
        check=True,
    ).stdout.strip()
    return runtime_root / "lib" / f"python{version}" / "site-packages"


def _write_calculation_runtime_manifest(
    runtime_root: Path,
    runtime_python: Path,
    runtime_runner: Path,
    runtime_manifest: Path,
    lock_bytes: bytes,
    requirements_bytes: bytes,
    lock: dict[str, Any],
) -> None:
    site_packages = _calculation_site_packages(runtime_root, runtime_python)
    probe_source = (
        "import importlib.metadata as m,json,platform,sys;"
        f"sys.path.insert(0,{str(site_packages)!r});"
        "import numpy;"
        f"names={sorted(lock['packages'])!r};"
        "packages={name:m.version(name) for name in names};"
        "config=getattr(numpy.__config__,'CONFIG',{});"
        "print(json.dumps({'python_version':platform.python_version(),"
        "'python_implementation':platform.python_implementation(),"
        "'platform_system':platform.system(),'platform_machine':platform.machine(),"
        "'packages':packages,'numpy_config':config},sort_keys=True,default=str))"
    )
    environment = {
        key: value
        for key, value in os.environ.items()
        if key in {"COMSPEC", "LANG", "LC_ALL", "PATHEXT", "SYSTEMROOT", "WINDIR"}
        or key.startswith("LC_")
    }
    try:
        probe = subprocess.run(
            [str(runtime_python), "-I", "-B", "-S", "-c", probe_source],
            cwd=runtime_root,
            env=environment,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )
        platform_payload = json.loads(probe.stdout)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        raise ValueError("TradingCodex calculation runtime package probe failed") from exc
    manifest = {
        "schema_version": CALCULATION_RUNTIME_SCHEMA_VERSION,
        "lock_sha256": hashlib.sha256(lock_bytes).hexdigest(),
        "requirements_sha256": hashlib.sha256(requirements_bytes).hexdigest(),
        "runner_sha256": hashlib.sha256(runtime_runner.read_bytes()).hexdigest(),
        "site_packages": str(site_packages.relative_to(runtime_root)).replace("\\", "/"),
        **platform_payload,
    }
    manifest["manifest_sha256"] = stable_hash(manifest)
    atomic_write_text(
        runtime_manifest,
        json.dumps(manifest, indent=2, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n",
    )


def _validate_calculation_runtime_location(
    runtime_root: Path,
    *,
    protected_paths: dict[str, Path | str] | None,
) -> None:
    resolved = runtime_root.resolve(strict=False)
    for label, raw_path in (protected_paths or {}).items():
        protected = Path(raw_path).expanduser().resolve(strict=False)
        if _paths_overlap(resolved, protected):
            raise ValueError(f"TradingCodex calculation runtime must not overlap {label}")
    for candidate in (runtime_root.parent, runtime_root):
        if _path_is_link_or_reparse_point(candidate):
            raise ValueError(
                "TradingCodex calculation runtime must not use a symlink or reparse point"
            )


def _validate_calculation_runtime(
    runtime_root: Path,
    runtime_python: Path,
    runtime_runner: Path,
    runtime_manifest: Path,
    expected_source: bytes,
    lock_bytes: bytes,
    requirements_bytes: bytes,
    lock: dict[str, Any],
) -> None:
    _validate_calculation_runtime_requirements(requirements_bytes, lock)
    if not runtime_root.is_dir() or _path_is_link_or_reparse_point(runtime_root):
        raise ValueError("TradingCodex calculation runtime is incomplete")
    for candidate in (runtime_python, runtime_runner, runtime_manifest):
        if not candidate.is_file() or _path_is_link_or_reparse_point(candidate):
            raise ValueError("TradingCodex calculation runtime contains an invalid file")
    if runtime_runner.read_bytes() != expected_source:
        raise ValueError("TradingCodex calculation runner does not match this release")
    try:
        manifest = json.loads(runtime_manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("TradingCodex calculation runtime manifest is invalid") from exc
    manifest_payload = dict(manifest)
    manifest_hash = str(manifest_payload.pop("manifest_sha256", ""))
    if (
        manifest.get("schema_version") != CALCULATION_RUNTIME_SCHEMA_VERSION
        or manifest_hash != stable_hash(manifest_payload)
        or manifest.get("lock_sha256") != hashlib.sha256(lock_bytes).hexdigest()
        or manifest.get("requirements_sha256")
        != hashlib.sha256(requirements_bytes).hexdigest()
        or manifest.get("runner_sha256") != hashlib.sha256(expected_source).hexdigest()
        or manifest.get("packages") != lock["packages"]
    ):
        raise ValueError("TradingCodex calculation runtime manifest does not match this release")
    site_relative = str(manifest.get("site_packages") or "")
    site_packages = (runtime_root / site_relative).resolve(strict=False)
    if not site_packages.is_dir() or not site_packages.is_relative_to(runtime_root.resolve()):
        raise ValueError("TradingCodex calculation runtime site-packages is invalid")
    environment = {
        key: value
        for key, value in os.environ.items()
        if key in {"COMSPEC", "LANG", "LC_ALL", "PATHEXT", "SYSTEMROOT", "WINDIR"}
        or key.startswith("LC_")
    }
    try:
        probe = subprocess.run(
            [
                str(runtime_python),
                "-I",
                "-S",
                "-c",
                (
                    "import importlib.metadata as m,json,sys; "
                    "sys.exit(2) if not (sys.flags.isolated and sys.flags.no_site) else None; "
                    f"sys.path.insert(0,{str(site_packages)!r}); "
                    f"expected={lock['packages']!r}; "
                    "actual={name:m.version(name) for name in expected}; "
                    "import numpy,pandas,scipy,statsmodels,numpy_financial,pyarrow; "
                    "sys.exit(3 if actual != expected else 0)"
                ),
            ],
            cwd=runtime_root,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError("TradingCodex calculation runtime is not executable") from exc
    if probe.returncode != 0:
        raise ValueError(
            "TradingCodex calculation runtime package set is unavailable or changed "
            f"(probe exit {probe.returncode})"
        )


def workspace_scratch_permission_aliases(
    workspace_id: str,
    scratch_path: Path | str,
) -> tuple[Path, ...]:
    scratch_display_path = _workspace_scratch_display_path(workspace_id)
    configured_scratch_path = Path(scratch_path).expanduser().absolute()
    if str(scratch_display_path) == str(configured_scratch_path):
        return ()
    if scratch_display_path.resolve(strict=False) != configured_scratch_path.resolve(
        strict=False
    ):
        return ()
    return (scratch_display_path,)


def _workspace_scratch_paths(
    workspace_id: str,
    *,
    provision: bool,
    protected_paths: dict[str, Path | str] | None = None,
) -> tuple[Path, Path]:
    scratch_display_path = _workspace_scratch_display_path(workspace_id)
    scratch_path, provider_sources_path = _validated_workspace_scratch_location(
        scratch_display_path,
        protected_paths=protected_paths,
    )
    if not provision:
        return scratch_display_path, scratch_path

    scratch_display_path.mkdir(parents=True, exist_ok=True)
    scratch_path, provider_sources_path = _validated_workspace_scratch_location(
        scratch_display_path,
        protected_paths=protected_paths,
    )
    try:
        scratch_path.chmod(0o700)
    except OSError:
        pass

    provider_sources_display_path = scratch_display_path / "provider-sources"
    provider_sources_display_path.mkdir(parents=False, exist_ok=True)
    scratch_path, provider_sources_path = _validated_workspace_scratch_location(
        scratch_display_path,
        protected_paths=protected_paths,
    )
    try:
        provider_sources_path.chmod(0o700)
    except OSError:
        pass
    return scratch_display_path, scratch_path


def _validated_workspace_scratch_location(
    scratch_display_path: Path,
    *,
    protected_paths: dict[str, Path | str] | None,
) -> tuple[Path, Path]:
    provider_sources_display_path = scratch_display_path / "provider-sources"
    if _path_is_link_or_reparse_point(scratch_display_path.parent):
        raise ValueError(
            "TradingCodex scratch parent must be a real directory, not a symlink or reparse point"
        )
    if _path_is_link_or_reparse_point(scratch_display_path):
        raise ValueError(
            "TradingCodex scratch path must be a real directory, not a symlink or reparse point"
        )
    if _path_is_link_or_reparse_point(provider_sources_display_path):
        raise ValueError(
            "TradingCodex provider source staging path must be a real directory, "
            "not a symlink or reparse point"
        )
    if scratch_display_path.exists() and not scratch_display_path.is_dir():
        raise ValueError("TradingCodex scratch path must be a real directory")
    if provider_sources_display_path.exists() and not provider_sources_display_path.is_dir():
        raise ValueError("TradingCodex provider source staging path must be a real directory")

    trusted_alias_prefix = _workspace_scratch_trusted_alias_prefix(scratch_display_path)
    for candidate in _path_components_after(
        scratch_display_path / "provider-sources",
        trusted_alias_prefix,
    ):
        if _path_is_link_or_reparse_point(candidate):
            raise ValueError(
                "TradingCodex scratch ancestors must be real directories, "
                "not symlinks or reparse points"
            )
        if candidate.exists() and not candidate.is_dir():
            raise ValueError("TradingCodex scratch ancestors must be real directories")

    scratch_path = scratch_display_path.resolve(strict=False)
    provider_sources_path = scratch_path / "provider-sources"
    for label, raw_path in (protected_paths or {}).items():
        protected_path = Path(raw_path).expanduser().resolve(strict=False)
        if _paths_overlap(scratch_path, protected_path):
            raise ValueError(f"TradingCodex scratch path must not overlap {label}")
    return scratch_path, provider_sources_path


def _path_is_link_or_reparse_point(path: Path) -> bool:
    try:
        status = path.lstat()
    except (FileNotFoundError, NotADirectoryError):
        return False
    attributes = int(getattr(status, "st_file_attributes", 0) or 0)
    return stat.S_ISLNK(status.st_mode) or bool(
        attributes & WINDOWS_REPARSE_POINT_ATTRIBUTE
    )


def _workspace_scratch_trusted_alias_prefix(scratch_display_path: Path) -> Path | None:
    if sys.platform != "darwin":
        return None
    home_display_path = Path.home().expanduser().absolute()
    if scratch_display_path == home_display_path or scratch_display_path.is_relative_to(
        home_display_path
    ):
        # macOS may expose a user's home through a canonical filesystem alias
        # such as /var -> /private/var. Only the prefix through the home itself
        # is trusted; cache descendants remain subject to the symlink checks.
        return home_display_path
    return None


def _path_components_after(path: Path, trusted_prefix: Path | None) -> list[Path]:
    absolute_path = path.expanduser().absolute()
    if trusted_prefix is not None:
        current = trusted_prefix
        relative = absolute_path.relative_to(trusted_prefix)
    else:
        current = Path(absolute_path.anchor)
        relative = absolute_path.relative_to(current)
    components: list[Path] = []
    for part in relative.parts:
        current /= part
        components.append(current)
    return components


def _paths_overlap(left: Path, right: Path) -> bool:
    return (
        left == right
        or left.is_relative_to(right)
        or right.is_relative_to(left)
    )


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
        path_candidates = [
            Path(candidate).expanduser()
            for name in ("python3.13", "python3.12", "python3.11", "python3.14", "python3", "python")
            if (candidate := shutil.which(name))
        ]
        candidates = [
            *path_candidates,
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
    python_value = raw["TRADINGCODEX_PYTHON"]
    windows_python = PureWindowsPath(python_value)
    python_path: Path | PureWindowsPath = (
        windows_python if windows_python.is_absolute() else Path(python_value)
    )
    raw.setdefault("TRADINGCODEX_PYTHON_RUNTIME_ROOT", str(python_path.parent.parent))
    raw.setdefault(
        "TRADINGCODEX_CALCULATION_RUNTIME_ROOT",
        str(python_path.parent.parent / "calculation-stdlib"),
    )
    calculation_root = raw["TRADINGCODEX_CALCULATION_RUNTIME_ROOT"]
    calculation_root_path: Path | PureWindowsPath = (
        PureWindowsPath(calculation_root)
        if PureWindowsPath(calculation_root).is_absolute()
        else Path(calculation_root)
    )
    raw.setdefault(
        "TRADINGCODEX_CALCULATION_PYTHON",
        str(
            calculation_root_path
            / ("Scripts/python.exe" if isinstance(calculation_root_path, PureWindowsPath) else "bin/python")
        ),
    )
    raw.setdefault(
        "TRADINGCODEX_CALCULATION_RUNNER",
        str(calculation_root_path / "calculation_runner.py"),
    )
    raw.setdefault("TRADINGCODEX_MCP_PYTHONPATH", "")
    raw.setdefault("TRADINGCODEX_PACKAGE_RUNNER", package_runner)
    if "TRADINGCODEX_GIT_COMMAND" not in raw:
        raw["TRADINGCODEX_GIT_COMMAND"] = resolve_generated_git_command()
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
    context.setdefault("TRADINGCODEX_SCRATCH_ALIAS_RULE_TOML", "")
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


def _preserve_user_codex_capabilities(target: Path, rendered: dict[str, str]) -> None:
    rel = ".codex/config.toml"
    existing_path = target / rel
    if rel not in rendered or not existing_path.is_file():
        return
    if "# BEGIN User Codex capabilities" in rendered[rel]:
        return
    existing = existing_path.read_text(encoding="utf-8")
    legacy_start = "# BEGIN TradingCodex managed Codex MCP"
    legacy_end = "# END TradingCodex managed Codex MCP"
    if legacy_start in existing or legacy_end in existing:
        if existing.count(legacy_start) != 1 or existing.count(legacy_end) != 1:
            raise ValueError("legacy TradingCodex Codex MCP block is malformed")
        prefix, remainder = existing.split(legacy_start, 1)
        _, suffix = remainder.split(legacy_end, 1)
        existing = f"{prefix}\n{suffix}"
    blocks = _user_codex_capability_blocks(existing)
    if not blocks:
        return
    preserved = "\n\n# BEGIN User Codex capabilities\n" + "\n\n".join(block.strip() for block in blocks)
    preserved += "\n# END User Codex capabilities\n"
    candidate = rendered[rel].rstrip() + preserved
    try:
        tomllib.loads(candidate)
    except tomllib.TOMLDecodeError as exc:
        raise ValueError("user Codex capability configuration cannot be preserved safely") from exc
    rendered[rel] = candidate


def _user_codex_capability_blocks(text: str) -> list[str]:
    sections = re.split(r"(?=^\s*\[\[?[^\n]+\]\]?\s*$)", text, flags=re.M)
    result: list[str] = []
    for section in sections:
        header_match = re.match(r"\s*(\[\[?[^\n]+\]\]?)", section)
        if not header_match:
            continue
        header = header_match.group(1).strip()
        if header == "[[skills.config]]":
            path_match = re.search(r'^\s*path\s*=\s*["\']([^"\']+)["\']', section, flags=re.M)
            path = path_match.group(1).replace("\\", "/") if path_match else ""
            skill_id = Path(path).parent.name if path else ""
            managed_skill = skill_id.startswith(("tcx-", "strategy-", "investment-brain-"))
            if path and not managed_skill and "/.tradingcodex/" not in path:
                result.append(section)
            continue
        mcp_match = re.match(r'^\[mcp_servers\.("[^"]+"|[A-Za-z0-9_-]+)', header)
        if mcp_match:
            name = mcp_match.group(1).strip('"')
            if name not in {"tradingcodex", "tradingcodex-home"}:
                result.append(section)
            continue
        if header.startswith("[plugins."):
            result.append(section)
    return result


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

from __future__ import annotations

import argparse
import builtins
import hashlib
import importlib.metadata
import json
import math
import os
import platform
import re
import stat
import sys
import threading
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, TextIO


MAX_SCRIPT_BYTES = 1_000_000
MAX_OUTPUT_BYTES = 1_000_000
MAX_RESULT_BYTES = 1_000_000
MAX_DECLARED_FILE_BYTES = 256 * 1024 * 1024
WALL_TIMEOUT_SECONDS = 30
SCRIPT_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,120}\.py")
DIRECT_FILE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,180}")
HASH = re.compile(r"[0-9a-f]{64}")
SIDECAR_SUFFIX = ".tcx.json"
FORBIDDEN_OUTPUT_SUFFIXES = (".pickle", ".pkl", ".joblib", ".pyc", ".pyo")
BLOCKED_IMPORT_ROOTS = frozenset(
    {
        "_posixsubprocess",
        "_socket",
        "ctypes",
        "distutils",
        "ensurepip",
        "multiprocessing",
        "pip",
        "pty",
        "runpy",
        "setuptools",
        "socket",
        "subprocess",
        "venv",
    }
)
BLOCKED_PROCESS_EVENTS = frozenset(
    {
        "os.exec",
        "os.fork",
        "os.forkpty",
        "os.kill",
        "os.killpg",
        "os.posix_spawn",
        "os.system",
        "signal.pthread_kill",
        "subprocess.Popen",
    }
)
REQUIRED_DIRECT_PACKAGES = {
    "numpy": "2.3.5",
    "numpy-financial": "1.0.0",
    "pandas": "2.3.3",
    "pyarrow": "25.0.0",
    "scipy": "1.16.3",
    "statsmodels": "0.14.6",
}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tcx-calc",
        description="Run one scratch-local Python calculation in the native Codex sandbox.",
    )
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--scratch", required=True)
    parser.add_argument("script")
    return parser


def _real_directory(raw_path: str, *, label: str) -> Path:
    path = Path(raw_path).expanduser().absolute()
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise ValueError(f"{label} is unavailable") from exc
    if _is_link_or_reparse_point(path, metadata) or not stat.S_ISDIR(metadata.st_mode):
        raise ValueError(f"{label} must be a real directory")
    return path


def _open_single_link_file(path: Path, *, maximum: int, label: str) -> tuple[int, os.stat_result]:
    try:
        path_metadata = path.lstat()
    except OSError as exc:
        raise ValueError(f"{label} must be a real readable file") from exc
    if _is_link_or_reparse_point(path, path_metadata):
        raise ValueError(f"{label} must not be a symlink or reparse point")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ValueError(f"{label} must be a real readable file") from exc
    opened = os.fstat(descriptor)
    if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1:
        os.close(descriptor)
        raise ValueError(f"{label} must be a single-link regular file")
    if opened.st_size <= 0 or opened.st_size > maximum:
        os.close(descriptor)
        raise ValueError(f"{label} size is outside the supported bound")
    try:
        current_path_metadata = path.lstat()
        if _is_link_or_reparse_point(path, current_path_metadata):
            raise ValueError(f"{label} must not become a symlink or reparse point")
        if not os.path.samestat(opened, path.stat()):
            raise ValueError(f"{label} changed while it was being opened")
    except Exception:
        os.close(descriptor)
        raise
    return descriptor, opened


def _is_link_or_reparse_point(path: Path, metadata: os.stat_result) -> bool:
    if path.is_symlink() or stat.S_ISLNK(metadata.st_mode):
        return True
    attributes = getattr(metadata, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return bool(reparse_flag and attributes & reparse_flag)


def _read_bounded_file(path: Path, *, maximum: int, label: str) -> bytes:
    descriptor, _opened = _open_single_link_file(path, maximum=maximum, label=label)
    try:
        chunks: list[bytes] = []
        remaining = maximum + 1
        while remaining > 0:
            chunk = os.read(descriptor, min(64 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
    finally:
        os.close(descriptor)
    payload = b"".join(chunks)
    if len(payload) > maximum:
        raise ValueError(f"{label} exceeds the supported size bound")
    return payload


def _read_script(scratch: Path, script_name: str) -> tuple[Path, bytes]:
    if SCRIPT_NAME.fullmatch(script_name) is None:
        raise ValueError("calculation script must be one direct scratch-local .py filename")
    script = scratch / script_name
    return script, _read_bounded_file(
        script,
        maximum=MAX_SCRIPT_BYTES,
        label="calculation script",
    )


def _stable_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, default=str, allow_nan=False).encode("utf-8")
    ).hexdigest()


def _runtime_contract() -> tuple[Path, dict[str, Any] | None]:
    runtime_root = Path(__file__).absolute().parent
    path = runtime_root / "runtime-manifest.json"
    if not path.is_file():
        return runtime_root, None
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("calculation runtime manifest is invalid") from exc
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 2:
        raise ValueError("calculation runtime manifest is invalid")
    payload = dict(manifest)
    expected = str(payload.pop("manifest_sha256", ""))
    if HASH.fullmatch(expected) is None or _stable_hash(payload) != expected:
        raise ValueError("calculation runtime manifest hash mismatch")
    required_fields = {
        "schema_version",
        "lock_sha256",
        "requirements_sha256",
        "runner_sha256",
        "site_packages",
        "python_version",
        "python_implementation",
        "platform_system",
        "platform_machine",
        "packages",
        "numpy_config",
        "manifest_sha256",
    }
    if set(manifest) != required_fields:
        raise ValueError("calculation runtime manifest fields are invalid")
    for field in ("lock_sha256", "requirements_sha256", "runner_sha256"):
        if HASH.fullmatch(str(manifest.get(field) or "")) is None:
            raise ValueError(f"calculation runtime {field} is invalid")
    runner_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    if manifest["runner_sha256"] != runner_hash:
        raise ValueError("calculation runtime runner hash mismatch")
    expected_platform = {
        "python_version": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "platform_system": platform.system(),
        "platform_machine": platform.machine(),
    }
    if any(manifest.get(field) != value for field, value in expected_platform.items()):
        raise ValueError("calculation runtime Python or platform identity mismatch")
    if not isinstance(manifest.get("packages"), dict) or not isinstance(
        manifest.get("numpy_config"), dict
    ):
        raise ValueError("calculation runtime package manifest is invalid")
    site_relative = str(manifest.get("site_packages") or "")
    if (
        not site_relative
        or "\\" in site_relative
        or Path(site_relative).is_absolute()
        or ".." in Path(site_relative).parts
    ):
        raise ValueError("calculation runtime site-packages is invalid")
    site = (runtime_root / site_relative).resolve(strict=False)
    if not site.is_dir() or not site.is_relative_to(runtime_root.resolve()):
        raise ValueError("calculation runtime site-packages is invalid")
    expected_packages = {
        _canonical_distribution_name(str(name)): str(version)
        for name, version in manifest["packages"].items()
    }
    if len(expected_packages) != len(manifest["packages"]) or any(
        not name or not version for name, version in expected_packages.items()
    ):
        raise ValueError("calculation runtime package manifest is invalid")
    if any(expected_packages.get(name) != version for name, version in REQUIRED_DIRECT_PACKAGES.items()):
        raise ValueError("calculation runtime direct package set mismatch")
    installed_packages: dict[str, str] = {}
    for distribution in importlib.metadata.distributions(path=[str(site)]):
        name = _canonical_distribution_name(str(distribution.metadata.get("Name") or ""))
        if not name or name in installed_packages:
            raise ValueError("calculation runtime installed package metadata is invalid")
        installed_packages[name] = str(distribution.version)
    if installed_packages != expected_packages:
        raise ValueError("calculation runtime installed package set mismatch")
    return runtime_root, manifest


def _canonical_distribution_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).casefold().strip("-")


def _inject_runtime_site_packages(runtime_root: Path, manifest: dict[str, Any] | None) -> None:
    if manifest is None:
        return
    site = (runtime_root / str(manifest["site_packages"])).resolve()
    sys.path.insert(0, str(site))


def _sidecar_payload(
    scratch: Path,
    script_name: str,
    source: bytes,
    manifest: dict[str, Any] | None,
) -> tuple[Path | None, dict[str, Any] | None]:
    sidecar_path = scratch / f"{script_name}{SIDECAR_SUFFIX}"
    if not sidecar_path.exists():
        return None, None
    if manifest is None:
        raise ValueError("prepared calculation requires a validated runtime manifest")
    raw = _read_bounded_file(sidecar_path, maximum=MAX_RESULT_BYTES, label="calculation sidecar")
    try:
        sidecar = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("calculation sidecar must be valid JSON") from exc
    if not isinstance(sidecar, dict) or sidecar.get("schema_version") != 1 or sidecar.get("mode") != "prepared":
        raise ValueError("calculation sidecar schema is invalid")
    payload = dict(sidecar)
    expected_sidecar_hash = str(payload.pop("sidecar_sha256", ""))
    if HASH.fullmatch(expected_sidecar_hash) is None or _stable_hash(payload) != expected_sidecar_hash:
        raise ValueError("calculation sidecar hash mismatch")
    if sidecar.get("script_name") != script_name:
        raise ValueError("calculation sidecar script_name mismatch")
    if sidecar.get("script_sha256") != hashlib.sha256(source).hexdigest():
        raise ValueError("calculation script changed after preparation")
    if sidecar.get("runtime_lock_sha256") != manifest.get("lock_sha256"):
        raise ValueError("calculation runtime lock changed after preparation")
    if sidecar.get("runtime_requirements_sha256") != manifest.get("requirements_sha256"):
        raise ValueError("calculation runtime requirements changed after preparation")
    if sidecar.get("runtime_manifest_sha256") != manifest.get("manifest_sha256"):
        raise ValueError("calculation runtime identity changed after preparation")
    for field in ("calculation_spec_id", "fingerprint", "workflow_run_id"):
        if not str(sidecar.get(field) or "").strip():
            raise ValueError(f"calculation sidecar {field} is required")
    if HASH.fullmatch(str(sidecar.get("fingerprint") or "")) is None:
        raise ValueError("calculation sidecar fingerprint is invalid")
    return sidecar_path, sidecar


def _direct_name(value: Any, *, label: str) -> str:
    name = str(value or "")
    if DIRECT_FILE_NAME.fullmatch(name) is None or "/" in name or "\\" in name:
        raise ValueError(f"{label} must be one direct scratch-local filename")
    return name


def _declared_io(
    scratch: Path,
    sidecar: dict[str, Any],
) -> tuple[set[Path], dict[str, Path], str]:
    inputs = sidecar.get("inputs")
    outputs = sidecar.get("outputs")
    if not isinstance(inputs, list) or not isinstance(outputs, list):
        raise ValueError("calculation sidecar inputs and outputs must be arrays")
    readable: set[Path] = set()
    writable: dict[str, Path] = {}
    for index, item in enumerate(inputs):
        if not isinstance(item, dict):
            raise ValueError(f"calculation input {index} must be an object")
        filename = _direct_name(item.get("filename"), label=f"calculation input {index}")
        expected_hash = str(item.get("sha256") or "")
        if HASH.fullmatch(expected_hash) is None:
            raise ValueError(f"calculation input {index} hash is invalid")
        path = scratch / filename
        payload = _read_bounded_file(path, maximum=MAX_DECLARED_FILE_BYTES, label=f"calculation input {index}")
        if hashlib.sha256(payload).hexdigest() != expected_hash:
            raise ValueError(f"calculation input {index} hash mismatch")
        readable.add(path.absolute())
    for index, item in enumerate(outputs):
        if not isinstance(item, dict):
            raise ValueError(f"calculation output {index} must be an object")
        filename = _direct_name(item.get("filename"), label=f"calculation output {index}")
        if filename.casefold().endswith(FORBIDDEN_OUTPUT_SUFFIXES):
            raise ValueError("executable calculation output serialization is forbidden")
        path = scratch / filename
        if path.exists() or path.is_symlink():
            raise ValueError(f"calculation output already exists: {filename}")
        if filename in writable:
            raise ValueError(f"duplicate calculation output filename: {filename}")
        writable[filename] = path.absolute()
    result_name = _direct_name(sidecar.get("result_file"), label="calculation result_file")
    if not result_name.endswith(".json"):
        raise ValueError("calculation result_file must be JSON")
    result_path = scratch / result_name
    if result_path.exists() or result_path.is_symlink():
        raise ValueError("calculation result_file already exists")
    return readable, writable, result_name


def _apply_resource_limits() -> None:
    try:
        import resource
    except ImportError:
        return
    limits = (
        (getattr(resource, "RLIMIT_CPU", None), 20),
        (getattr(resource, "RLIMIT_FSIZE", None), 8 * 1024 * 1024),
        (getattr(resource, "RLIMIT_NOFILE", None), 128),
    )
    for resource_id, maximum in limits:
        if resource_id is None:
            continue
        try:
            _current_soft, current_hard = resource.getrlimit(resource_id)
            bounded_hard = maximum if current_hard < 0 else min(current_hard, maximum)
            bounded_soft = min(maximum, bounded_hard)
            resource.setrlimit(resource_id, (bounded_soft, bounded_hard))
        except (OSError, ValueError):
            continue


class _BoundedTextWriter:
    def __init__(self, wrapped: TextIO, maximum: int) -> None:
        self._wrapped = wrapped
        self._remaining = maximum
        self._digest = hashlib.sha256()
        self._bytes = 0

    def write(self, value: str) -> int:
        encoded = value.encode(getattr(self._wrapped, "encoding", None) or "utf-8", errors="replace")
        if len(encoded) > self._remaining:
            raise RuntimeError("calculation output exceeded the supported bound")
        self._remaining -= len(encoded)
        self._bytes += len(encoded)
        self._digest.update(encoded)
        return self._wrapped.write(value)

    def flush(self) -> None:
        self._wrapped.flush()

    def isatty(self) -> bool:
        return False

    @property
    def encoding(self) -> str:
        return getattr(self._wrapped, "encoding", None) or "utf-8"

    def summary(self) -> dict[str, Any]:
        return {"bytes": self._bytes, "sha256": self._digest.hexdigest()}


def _timeout_exit() -> None:
    os.write(2, b"tcx-calc: calculation exceeded the wall-time limit\n")
    os._exit(124)


def _sanitize_environment(workspace: Path, scratch: Path) -> None:
    platform_environment = {
        key: value
        for key, value in os.environ.items()
        if key in {"COMSPEC", "LANG", "LC_ALL", "PATHEXT", "SYSTEMROOT", "TERM", "WINDIR"}
        or key.startswith("LC_")
    }
    os.environ.clear()
    os.environ.update(platform_environment)
    os.environ.update(
        {
            "HOME": str(scratch),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONNOUSERSITE": "1",
            "TEMP": str(scratch),
            "TMP": str(scratch),
            "TMPDIR": str(scratch),
            "TRADINGCODEX_CALCULATION": "1",
            "TRADINGCODEX_SCRATCH": str(scratch),
            "TRADINGCODEX_WORKSPACE_ROOT": str(workspace),
        }
    )


def _is_write_open(mode: Any, flags: Any) -> bool:
    if isinstance(mode, str) and any(marker in mode for marker in ("w", "a", "x", "+")):
        return True
    if isinstance(flags, int):
        write_flags = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
        return bool(flags & write_flags)
    return False


def _install_io_audit(
    *,
    runtime_root: Path,
    script_path: Path,
    sidecar_path: Path,
    readable: set[Path],
    writable: set[Path],
) -> None:
    exact_reads = {script_path.absolute(), sidecar_path.absolute(), *readable}
    immutable_roots = {
        runtime_root.resolve(strict=False),
        Path(sys.base_prefix).resolve(strict=False),
        Path(sys.exec_prefix).resolve(strict=False),
    }
    for raw in (os.environ.get("SYSTEMROOT"), os.environ.get("WINDIR")):
        if raw:
            immutable_roots.add(Path(raw).resolve(strict=False))

    def permitted_under_roots(path: Path) -> bool:
        resolved = path.resolve(strict=False)
        return any(resolved.is_relative_to(root) for root in immutable_roots)

    def audit(event: str, args: tuple[Any, ...]) -> None:
        if event == "open" and args:
            raw_path = args[0]
            if isinstance(raw_path, int):
                return
            path = Path(os.fsdecode(raw_path)).absolute()
            writing = _is_write_open(args[1] if len(args) > 1 else None, args[2] if len(args) > 2 else None)
            if writing:
                if path not in writable:
                    raise PermissionError(f"undeclared calculation output is denied: {path.name}")
                return
            if path in exact_reads or path in writable or permitted_under_roots(path):
                return
            raise PermissionError(f"undeclared calculation input is denied: {path.name}")
        if event in {"os.remove", "os.rmdir", "os.mkdir", "os.rename"}:
            raise PermissionError("calculation filesystem mutation is limited to declared outputs")

    sys.addaudithook(audit)


def _install_execution_audit() -> None:
    """Deny process, network, FFI, and installer escapes inside calculations."""

    def audit(event: str, args: tuple[Any, ...]) -> None:
        if (
            event in BLOCKED_PROCESS_EVENTS
            or event.startswith("os.spawn")
            or event.startswith("socket.")
            or event == "ensurepip.bootstrap"
        ):
            raise PermissionError(
                "calculation process, network, and package-install operations are denied"
            )
        if event == "ctypes.dlopen" and args and args[0] is not None:
            raise PermissionError("calculation native-library loading is denied")
        if event.startswith("ctypes.dlsym"):
            raise PermissionError("calculation native symbol access is denied")

    sys.addaudithook(audit)


def _restricted_builtins() -> dict[str, Any]:
    namespace = dict(vars(builtins))
    original_import = builtins.__import__

    def restricted_import(
        name: str,
        globals: dict[str, Any] | None = None,
        locals: dict[str, Any] | None = None,
        fromlist: tuple[str, ...] = (),
        level: int = 0,
    ) -> Any:
        root = str(name or "").partition(".")[0]
        if level == 0 and root in BLOCKED_IMPORT_ROOTS:
            raise PermissionError(f"calculation import is denied: {root}")
        return original_import(name, globals, locals, fromlist, level)

    namespace["__import__"] = restricted_import
    return namespace


def _validate_json_value(value: Any, *, label: str) -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{label} must not contain NaN or Infinity")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _validate_json_value(item, label=f"{label}[{index}]")
        return
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        for key, item in value.items():
            _validate_json_value(item, label=f"{label}.{key}")
        return
    raise ValueError(f"{label} must be a finite JSON value")


def _validate_emitted_result(value: Any, declared_outputs: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("tcx_emit_result requires one result object")
    allowed = {"metrics", "diagnostics", "assumptions", "warnings", "output_files"}
    if set(value) - allowed:
        raise ValueError("tcx_emit_result contains unknown fields")
    metrics = value.get("metrics")
    if not isinstance(metrics, list) or not metrics:
        raise ValueError("tcx_emit_result metrics must be a non-empty array")
    normalized_metrics = []
    for index, metric in enumerate(metrics):
        if not isinstance(metric, dict):
            raise ValueError(f"metric {index} must be an object")
        required = {"name", "value", "value_type", "unit", "currency", "precision"}
        if set(metric) != required:
            raise ValueError(f"metric {index} fields do not match the typed metric schema")
        name = str(metric.get("name") or "").strip()
        value_type = str(metric.get("value_type") or "")
        if not name or value_type not in {"number", "integer", "decimal", "string", "boolean"}:
            raise ValueError(f"metric {index} has an invalid name or value_type")
        raw = metric.get("value")
        if value_type == "number" and (isinstance(raw, bool) or not isinstance(raw, (int, float))):
            raise ValueError(f"metric {index} value must be numeric")
        if value_type == "integer" and (isinstance(raw, bool) or not isinstance(raw, int)):
            raise ValueError(f"metric {index} value must be an integer")
        if value_type in {"decimal", "string"} and not isinstance(raw, str):
            raise ValueError(f"metric {index} value must be a string")
        if value_type == "decimal":
            if not raw or raw != raw.strip():
                raise ValueError(f"metric {index} decimal value must be an exact finite string")
            try:
                decimal_value = Decimal(raw)
            except InvalidOperation as exc:
                raise ValueError(
                    f"metric {index} decimal value must be an exact finite string"
                ) from exc
            if not decimal_value.is_finite():
                raise ValueError(f"metric {index} decimal value must be finite")
        if value_type == "boolean" and not isinstance(raw, bool):
            raise ValueError(f"metric {index} value must be a boolean")
        if metric["precision"] is not None and (isinstance(metric["precision"], bool) or not isinstance(metric["precision"], int) or metric["precision"] < 0):
            raise ValueError(f"metric {index} precision must be a non-negative integer or null")
        for field in ("unit", "currency"):
            if metric[field] is not None and not isinstance(metric[field], str):
                raise ValueError(f"metric {index} {field} must be a string or null")
        _validate_json_value(metric, label=f"metric {index}")
        normalized_metrics.append(dict(metric))
    diagnostics = value.get("diagnostics", {})
    if not isinstance(diagnostics, dict):
        raise ValueError("diagnostics must be an object")
    assumptions = value.get("assumptions", [])
    warnings = value.get("warnings", [])
    if not isinstance(assumptions, list) or not all(isinstance(item, str) for item in assumptions):
        raise ValueError("assumptions must be an array of strings")
    if not isinstance(warnings, list) or not all(isinstance(item, str) for item in warnings):
        raise ValueError("warnings must be an array of strings")
    output_files = value.get("output_files", [])
    if not isinstance(output_files, list):
        raise ValueError("output_files must be an array")
    normalized_files = []
    for item in output_files:
        filename = _direct_name(item, label="result output file")
        if filename not in declared_outputs:
            raise ValueError(f"result references an undeclared output: {filename}")
        normalized_files.append(filename)
    normalized = {
        "metrics": normalized_metrics,
        "diagnostics": diagnostics,
        "assumptions": assumptions,
        "warnings": warnings,
        "output_files": normalized_files,
    }
    _validate_json_value(normalized, label="result")
    return normalized


def _write_result_fd(descriptor: int, payload: dict[str, Any]) -> None:
    encoded = (
        json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n"
    ).encode("utf-8")
    if len(encoded) > MAX_RESULT_BYTES:
        raise RuntimeError("calculation result envelope exceeded the supported bound")
    os.write(descriptor, encoded)
    os.fsync(descriptor)


def run_calculation(*, workspace: str, scratch: str, script_name: str) -> int:
    workspace_path = _real_directory(workspace, label="workspace")
    scratch_path = _real_directory(scratch, label="scratch")
    script_path, source = _read_script(scratch_path, script_name)
    _sanitize_environment(workspace_path, scratch_path)
    runtime_root, runtime_manifest = _runtime_contract()
    _inject_runtime_site_packages(runtime_root, runtime_manifest)
    sidecar_path, sidecar = _sidecar_payload(scratch_path, script_name, source, runtime_manifest)
    _apply_resource_limits()
    os.chdir(scratch_path if sidecar else workspace_path)
    sys.argv = [str(script_path)]
    sys.stdin = open(os.devnull, "r", encoding="utf-8")
    stdout = _BoundedTextWriter(sys.stdout, MAX_OUTPUT_BYTES)
    stderr = _BoundedTextWriter(sys.stderr, MAX_OUTPUT_BYTES)
    sys.stdout = stdout  # type: ignore[assignment]
    sys.stderr = stderr  # type: ignore[assignment]
    timer = threading.Timer(WALL_TIMEOUT_SECONDS, _timeout_exit)
    timer.daemon = True
    timer.start()
    result_descriptor: int | None = None
    emitted: dict[str, Any] | None = None
    emitted_once = False
    declared_outputs: dict[str, Path] = {}

    def tcx_emit_result(value: Any) -> None:
        nonlocal emitted, emitted_once
        if sidecar is None:
            raise RuntimeError("tcx_emit_result is available only for prepared calculations")
        if emitted_once:
            raise RuntimeError("tcx_emit_result may be called exactly once")
        emitted = _validate_emitted_result(value, set(declared_outputs))
        emitted_once = True

    status = "succeeded"
    error_type = ""
    try:
        if sidecar is not None and sidecar_path is not None:
            readable, declared_outputs, result_name = _declared_io(scratch_path, sidecar)
            result_path = scratch_path / result_name
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
            result_descriptor = os.open(result_path, flags, 0o600)
            _install_io_audit(
                runtime_root=runtime_root,
                script_path=script_path,
                sidecar_path=sidecar_path,
                readable=readable,
                writable=set(declared_outputs.values()),
            )
        _install_execution_audit()
        code = compile(source, str(script_path), "exec", dont_inherit=True)
        namespace = {
            "__builtins__": _restricted_builtins(),
            "__file__": str(script_path),
            "__name__": "__main__",
            "__package__": None,
            "tcx_emit_result": tcx_emit_result,
        }
        exec(code, namespace, namespace)
        if sidecar is not None and not emitted_once:
            raise RuntimeError("prepared calculation must call tcx_emit_result exactly once")
    except BaseException as exc:
        if sidecar is None:
            raise
        status = "failed"
        error_type = type(exc).__name__
        print(f"tcx-calc: calculation failed ({error_type})", file=sys.stderr)
    finally:
        timer.cancel()
        if result_descriptor is not None and sidecar is not None:
            output_records = []
            if emitted is not None:
                for filename in emitted["output_files"]:
                    path = declared_outputs[filename]
                    descriptor, opened = _open_single_link_file(
                        path,
                        maximum=MAX_DECLARED_FILE_BYTES,
                        label=f"calculation output {filename}",
                    )
                    os.close(descriptor)
                    output_records.append(
                        {
                            "filename": filename,
                            "bytes": opened.st_size,
                            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                        }
                    )
            envelope = {
                "schema_version": 1,
                "calculation_spec_id": sidecar["calculation_spec_id"],
                "fingerprint": sidecar["fingerprint"],
                "workflow_run_id": sidecar["workflow_run_id"],
                "status": status,
                "error_type": error_type,
                "result": emitted,
                "outputs": output_records,
                "stdout": stdout.summary(),
                "stderr": stderr.summary(),
                "runtime_manifest_sha256": runtime_manifest["manifest_sha256"],
            }
            envelope["envelope_sha256"] = _stable_hash(envelope)
            try:
                _write_result_fd(result_descriptor, envelope)
            finally:
                os.close(result_descriptor)
    return 0 if status == "succeeded" else 1


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        return run_calculation(
            workspace=args.workspace,
            scratch=args.scratch,
            script_name=args.script,
        )
    except (OSError, RuntimeError, SyntaxError, ValueError) as exc:
        print(f"tcx-calc: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import os
import json
import http.client
import ipaddress
import signal
import re
import shlex
import shutil
import socket
import socketserver
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from django.core.servers.basehttp import WSGIServer

from tradingcodex_cli.versioning import version_less_than as _version_less_than
from tradingcodex_service.application.common import paths_equivalent
from tradingcodex_service.application.runtime import tradingcodex_db_path, tradingcodex_file_lock, tradingcodex_state_dir
from tradingcodex_service.runtime_profile import assert_service_binding_allowed
from tradingcodex_service.version import TRADINGCODEX_VERSION


DEFAULT_SERVICE_HOST = "127.0.0.1"
DEFAULT_SERVICE_PORT = 48267
DEFAULT_SERVICE_ADDR = f"{DEFAULT_SERVICE_HOST}:{DEFAULT_SERVICE_PORT}"
DEFAULT_SERVICE_START_TIMEOUT = 30.0
DEFAULT_SERVICE_HEALTH_TIMEOUT = 2.0
DEFAULT_SERVICE_LOG_MAX_BYTES = 5 * 1024 * 1024
DEFAULT_SERVICE_LOG_BACKUPS = 3
_LOOPBACK_HTTP_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class NonResolvingWSGIServer(WSGIServer):
    """Bind without a reverse-DNS lookup for the local server name."""

    def server_bind(self) -> None:
        socketserver.TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name = str(host)
        self.server_port = int(port)
        self.setup_environ()


def configured_service_addr() -> str:
    return str(os.environ.get("TRADINGCODEX_SERVICE_ADDR") or "").strip() or DEFAULT_SERVICE_ADDR


def maybe_autostart_service(workspace_root: Path, source_root: Path | None = None) -> bool:
    if os.environ.get("TRADINGCODEX_MCP_AUTOSTART_SERVICE", "").lower() not in {"1", "true", "yes", "on"}:
        return False
    addr = configured_service_addr()
    timeout = float(os.environ.get("TRADINGCODEX_MCP_AUTOSTART_TIMEOUT", str(DEFAULT_SERVICE_START_TIMEOUT)))
    return ensure_service_up(workspace_root, addr=addr, source_root=source_root, timeout=timeout)


def ensure_service_up(
    workspace_root: Path,
    addr: str | None = None,
    source_root: Path | None = None,
    timeout: float = DEFAULT_SERVICE_START_TIMEOUT,
) -> bool:
    addr = addr or configured_service_addr()
    assert_service_binding_allowed(addr)
    host, port = _parse_addr(addr)
    if _tcp_open(host, port) and _compatible_service(host, port):
        return False
    with tradingcodex_file_lock(f"service-{host}-{port}"):
        if _tcp_open(host, port) and _compatible_service(host, port):
            return False
        if _tcp_open(host, port):
            _replace_stale_tradingcodex_service_or_raise(host, port, timeout=timeout)
        process = _start_service(workspace_root, addr, source_root)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if _tcp_open(host, port) and _compatible_service(host, port):
                return True
            if process is not None and process.poll() is not None:
                break
            time.sleep(0.2)
    if _tcp_open(host, port) and _compatible_service(host, port):
        return True
    if process is not None and process.poll() is None:
        _terminate_failed_startup(process)
    try:
        _assert_compatible_service(host, port)
    except RuntimeError as exc:
        process_state = (
            f"exited with code {process.returncode}"
            if process is not None and process.poll() is not None
            else "is still running"
        )
        startup_tail = _service_startup_log_tail() or "(no startup output)"
        raise RuntimeError(
            f"{exc} Detached service process {process_state}. "
            f"Redacted startup output: {startup_tail}"
        ) from exc
    return False


def compatible_service_running(addr: str | None = None) -> bool:
    addr = addr or configured_service_addr()
    assert_service_binding_allowed(addr)
    host, port = _parse_addr(addr)
    if not _tcp_open(host, port):
        return False
    _assert_compatible_service(host, port)
    return True


def stop_service(addr: str | None = None, timeout: float = 5.0) -> bool:
    addr = addr or configured_service_addr()
    host, port = _parse_addr(addr)
    normalized_addr = f"{host}:{port}"
    if not _is_loopback_host(host):
        raise RuntimeError("tcx service stop only supports a loopback TradingCodex service")
    if not _tcp_open(host, port):
        return False
    health = _service_health(host, port)
    if not health or health.get("service") != "tradingcodex":
        raise RuntimeError(f"{normalized_addr} is not a TradingCodex service; stop it outside tcx or choose a free service address.")
    pids = _service_pids(host, port, health)
    if not pids:
        raise RuntimeError(f"Could not find the TradingCodex service process on {normalized_addr}; stop the process using port {port} and retry.")
    for pid in pids:
        try:
            _terminate_pid(pid)
        except ProcessLookupError:
            pass
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _tcp_open(host, port):
            return True
        time.sleep(0.2)
    raise RuntimeError(f"Timed out stopping TradingCodex service on {normalized_addr}.")


def service_status(addr: str | None = None) -> dict:
    addr = addr or configured_service_addr()
    host, port = _parse_addr(addr)
    normalized_addr = f"{host}:{port}"
    url = service_http_url(normalized_addr)
    current_db = str(tradingcodex_db_path())
    status = {
        "addr": normalized_addr,
        "url": url,
        "reachable": False,
        "compatible": False,
        "service": "",
        "version": "",
        "package_version": TRADINGCODEX_VERSION,
        "db_path": "",
        "expected_db_path": current_db,
        "ready": False,
        "checks": [],
        "reason_codes": [],
        "log": _service_log_status(),
        "issue": "not_running",
        "next_action": f"Run `tcx service ensure {normalized_addr}` to start the local TradingCodex service.",
    }
    if not _tcp_open(host, port):
        return status
    status["reachable"] = True
    health = _service_health(host, port)
    if not health or health.get("service") != "tradingcodex":
        status["issue"] = "port_occupied"
        status["next_action"] = "Stop the non-TradingCodex process on this port or choose a free service address."
        return status
    status.update({
        "service": str(health.get("service") or ""),
        "version": str(health.get("version") or ""),
        "db_path": str(health.get("db_path") or ""),
        "ready": health.get("ready") is True,
        "checks": list(health.get("checks") or []),
        "reason_codes": list(health.get("reason_codes") or []),
    })
    if status["version"] != TRADINGCODEX_VERSION:
        status["issue"] = "version_mismatch"
        status["next_action"] = _version_mismatch_next_action(status["version"], normalized_addr)
        return status
    if not status["db_path"] or not paths_equivalent(status["db_path"], current_db):
        status["issue"] = "db_mismatch"
        status["next_action"] = "Use an address backed by the same central DB or stop the mismatched TradingCodex service."
        return status
    if not status["ready"]:
        status["issue"] = "not_ready"
        reasons = ", ".join(status["reason_codes"]) or "readiness checks failed"
        status["next_action"] = f"Resolve service readiness checks ({reasons}), then retry."
        return status
    status["compatible"] = True
    status["issue"] = ""
    status["next_action"] = "No action needed."
    return status


def service_http_url(addr: str | None = None) -> str:
    addr = addr or configured_service_addr()
    host, port = _parse_addr(addr)
    return f"http://{host}:{port}/"


def open_loopback_url(url: str, *, timeout: float):
    """Open a loopback service URL without consulting host proxy settings."""

    return _LOOPBACK_HTTP_OPENER.open(url, timeout=timeout)


def _start_service(
    workspace_root: Path,
    addr: str,
    source_root: Path | None,
) -> subprocess.Popen[bytes]:
    run_dir = tradingcodex_state_dir() / "run"
    run_dir.mkdir(parents=True, exist_ok=True)
    startup_log = run_dir / "service-startup.log"
    env = os.environ.copy()
    env.setdefault("PYTHONFAULTHANDLER", "1")
    env.setdefault("DJANGO_SETTINGS_MODULE", "tradingcodex_service.settings")
    env.setdefault("TRADINGCODEX_WORKSPACE_ROOT", str(workspace_root.resolve()))
    if source_root:
        current = env.get("PYTHONPATH")
        env["PYTHONPATH"] = str(source_root.resolve()) + (f"{os.pathsep}{current}" if current else "")
    platform_kwargs = _detached_process_kwargs()
    with startup_log.open("wb") as output:
        return subprocess.Popen(
            [sys.executable, "-m", "tradingcodex_cli", "service", "runserver", addr, "--noreload"],
            cwd=workspace_root,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=subprocess.STDOUT,
            close_fds=True,
            **platform_kwargs,
        )


def _terminate_failed_startup(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    try:
        if os.name != "nt" and hasattr(signal, "SIGABRT"):
            process.send_signal(signal.SIGABRT)
        else:
            process.terminate()
        process.wait(timeout=2)
        return
    except (OSError, subprocess.TimeoutExpired):
        pass
    try:
        process.kill()
        process.wait(timeout=2)
    except (OSError, subprocess.TimeoutExpired):
        pass


def _detached_process_kwargs(platform_name: str | None = None) -> dict:
    if (platform_name or os.name) == "nt":
        return {
            "creationflags": (
                getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                | getattr(subprocess, "DETACHED_PROCESS", 0)
            )
        }
    return {"start_new_session": True}


def _parse_addr(addr: str) -> tuple[str, int]:
    if ":" in addr:
        host, port_text = addr.rsplit(":", 1)
        return host or "127.0.0.1", int(port_text)
    return "127.0.0.1", int(addr)


def _tcp_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.25) as connection:
            local_port = int(connection.getsockname()[1])
            # macOS can self-connect when the target is an unbound port in its
            # ephemeral range. That is a client socket, not a listening service.
            return local_port != port
    except OSError:
        return False


def _compatible_service(host: str, port: int) -> bool:
    try:
        _assert_compatible_service(host, port)
        return True
    except Exception:
        return False


def _replace_stale_tradingcodex_service_or_raise(host: str, port: int, *, timeout: float) -> None:
    health = _service_health(host, port)
    service_db = str(health.get("db_path") or "")
    if (
        health.get("service") == "tradingcodex"
        and _version_less_than(str(health.get("version") or ""), TRADINGCODEX_VERSION)
        and service_db
        and paths_equivalent(service_db, tradingcodex_db_path())
    ):
        stop_service(f"{host}:{port}", timeout=max(1.0, min(timeout, 5.0)))
        return
    _assert_compatible_service(host, port)


def _assert_compatible_service(host: str, port: int) -> None:
    health = _service_health(host, port)
    addr = f"{host}:{port}"
    if not health or health.get("service") != "tradingcodex":
        raise RuntimeError(
            f"{addr} is already in use by a non-TradingCodex service. "
            f"Stop that process or run `tcx service ensure {addr}` on a free address."
        )
    if health.get("version") != TRADINGCODEX_VERSION:
        raise RuntimeError(
            f"TradingCodex service version mismatch: service={health.get('version')} package={TRADINGCODEX_VERSION}. "
            f"{_version_mismatch_next_action(str(health.get('version') or ''), addr)}"
        )
    service_db = str(health.get("db_path") or "")
    current_db = str(tradingcodex_db_path())
    if not service_db or not paths_equivalent(service_db, current_db):
        raise RuntimeError(
            f"TradingCodex service DB mismatch: service={service_db} package={current_db}. "
            f"Stop the TradingCodex service at {service_http_url(addr)} "
            "or use an address backed by the same central DB."
        )
    if health.get("ready") is not True:
        reasons = ", ".join(str(item) for item in health.get("reason_codes") or []) or "readiness checks failed"
        raise RuntimeError(f"TradingCodex service is live but not ready: {reasons}")


def _service_health(host: str, port: int) -> dict:
    connection = http.client.HTTPConnection(host, port, timeout=DEFAULT_SERVICE_HEALTH_TIMEOUT)
    try:
        connection.request("GET", "/api/health/ready")
        response = connection.getresponse()
        if response.status not in {200, 503}:
            return {}
        payload = response.read().decode("utf-8")
        data = json.loads(payload)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}
    finally:
        connection.close()


def _service_log_status() -> dict:
    run_dir = tradingcodex_state_dir() / "run"
    path = run_dir / "service.log"
    backups = sorted(run_dir.glob("service.log.*")) if run_dir.exists() else []
    try:
        size_bytes = path.stat().st_size
    except OSError:
        size_bytes = 0
    last_error = ""
    if path.exists():
        try:
            from tradingcodex_service.log_safety import redact_log_text

            with path.open("rb") as handle:
                handle.seek(max(0, path.stat().st_size - 65_536))
                lines = handle.read().decode("utf-8", errors="replace").splitlines()
            last_error = next((redact_log_text(line)[-1000:] for line in reversed(lines) if " ERROR " in line or " CRITICAL " in line), "")
        except OSError:
            pass
    return {
        "path": str(path),
        "size_bytes": size_bytes,
        "backup_count": len(backups),
        "max_bytes": int(os.environ.get("TRADINGCODEX_SERVICE_LOG_MAX_BYTES", DEFAULT_SERVICE_LOG_MAX_BYTES)),
        "max_backups": int(os.environ.get("TRADINGCODEX_SERVICE_LOG_BACKUPS", DEFAULT_SERVICE_LOG_BACKUPS)),
        "last_error": last_error,
        "startup_path": str(run_dir / "service-startup.log"),
        "startup_tail": _service_startup_log_tail(),
    }


def _service_startup_log_tail() -> str:
    path = tradingcodex_state_dir() / "run" / "service-startup.log"
    if not path.exists():
        return ""
    try:
        from tradingcodex_service.log_safety import redact_log_text

        with path.open("rb") as handle:
            handle.seek(max(0, path.stat().st_size - 65_536))
            return redact_log_text(handle.read().decode("utf-8", errors="replace"))[-4000:]
    except OSError:
        return ""


def _service_pids(host: str, port: int, health: dict) -> list[int]:
    pid = health.get("pid")
    health_pid = int(pid) if isinstance(pid, int) and pid > 0 else None
    listeners = _listener_pids(port)
    if not listeners:
        return []
    if health_pid is None or health_pid not in listeners:
        raise RuntimeError(
            f"TradingCodex health PID could not be verified as the listener on {host}:{port}; refusing to stop a process."
        )
    return [health_pid]


def _listener_pids(port: int) -> set[int]:
    if os.name == "nt":
        try:
            result = subprocess.run(
                ["netstat", "-ano", "-p", "tcp"],
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                timeout=3,
                check=False,
            )
        except Exception:
            return set()
        pattern = re.compile(rf"^\s*TCP\s+\S+:{port}\s+\S+\s+LISTENING\s+(\d+)\s*$", re.I)
        return {int(match.group(1)) for line in result.stdout.splitlines() if (match := pattern.match(line))}
    lsof = shutil.which("lsof")
    if not lsof:
        return set()
    try:
        result = subprocess.run(
            [lsof, f"-tiTCP:{port}", "-sTCP:LISTEN"],
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=1,
            check=False,
        )
        return {int(line) for line in result.stdout.splitlines() if line.strip().isdigit()}
    except Exception:
        return set()


def _terminate_pid(pid: int) -> None:
    if os.name != "nt":
        os.kill(pid, signal.SIGTERM)
        return
    result = subprocess.run(
        ["taskkill", "/PID", str(pid), "/T", "/F"],
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=5,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
        raise RuntimeError(f"Windows could not terminate TradingCodex PID {pid}: {detail}")


def _is_loopback_host(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _version_mismatch_next_action(service_version: str, addr: str) -> str:
    if _version_less_than(TRADINGCODEX_VERSION, service_version):
        from tradingcodex_cli.package_source import (
            EXECUTABLE_SOURCE_ENV,
            LOCAL_EXECUTABLE_SOURCE_KIND,
            PACKAGE_SOURCE_KIND_ENV,
            configured_executable_source,
            executable_source_is_local,
        )

        raw_source = str(os.environ.get(EXECUTABLE_SOURCE_ENV) or "")
        try:
            package_spec = configured_executable_source(None)
        except ValueError:
            return (
                "Resolve the invalid TradingCodex package source, then refresh the workspace "
                "and fully restart Codex."
            )
        if os.environ.get(PACKAGE_SOURCE_KIND_ENV) == LOCAL_EXECUTABLE_SOURCE_KIND or (
            raw_source and executable_source_is_local(package_spec)
        ):
            rendered = (
                "uvx --refresh --from <package-spec> tcx update . --from <package-spec>"
            )
        else:
            command = [
                "uvx",
                "--refresh",
                "--from",
                package_spec,
                "tcx",
                "update",
                ".",
                "--from",
                package_spec,
            ]
            rendered = (
                subprocess.list2cmdline(command)
                if os.name == "nt"
                else " ".join(shlex.quote(item) for item in command)
            )
        return f"Run `{rendered}`, then fully restart Codex so MCP reloads the refreshed package."
    return f"Run `tcx service stop {addr}` and then restart Codex if MCP uses the default address."

from __future__ import annotations

import os
import json
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from tradingcodex_service.application.runtime import tradingcodex_db_path, tradingcodex_file_lock, tradingcodex_state_dir
from tradingcodex_service.version import TRADINGCODEX_VERSION


DEFAULT_SERVICE_HOST = "127.0.0.1"
DEFAULT_SERVICE_PORT = 48267
DEFAULT_SERVICE_ADDR = f"{DEFAULT_SERVICE_HOST}:{DEFAULT_SERVICE_PORT}"


def maybe_autostart_service(workspace_root: Path, source_root: Path | None = None) -> bool:
    if os.environ.get("TRADINGCODEX_MCP_AUTOSTART_SERVICE", "").lower() not in {"1", "true", "yes", "on"}:
        return False
    addr = os.environ.get("TRADINGCODEX_SERVICE_ADDR", DEFAULT_SERVICE_ADDR)
    timeout = float(os.environ.get("TRADINGCODEX_MCP_AUTOSTART_TIMEOUT", "8"))
    return ensure_service_up(workspace_root, addr=addr, source_root=source_root, timeout=timeout)


def ensure_service_up(workspace_root: Path, addr: str = DEFAULT_SERVICE_ADDR, source_root: Path | None = None, timeout: float = 8.0) -> bool:
    host, port = _parse_addr(addr)
    if _tcp_open(host, port) and _compatible_service(host, port):
        return False
    with tradingcodex_file_lock(f"service-{host}-{port}"):
        if _tcp_open(host, port) and _compatible_service(host, port):
            return False
        if _tcp_open(host, port):
            _replace_stale_tradingcodex_service_or_raise(host, port, timeout=timeout)
        _start_service(workspace_root, addr, source_root)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if _tcp_open(host, port) and _compatible_service(host, port):
                return True
            time.sleep(0.2)
    _assert_compatible_service(host, port)
    return False


def compatible_service_running(addr: str = DEFAULT_SERVICE_ADDR) -> bool:
    host, port = _parse_addr(addr)
    if not _tcp_open(host, port):
        return False
    _assert_compatible_service(host, port)
    return True


def stop_service(addr: str = DEFAULT_SERVICE_ADDR, timeout: float = 5.0) -> bool:
    host, port = _parse_addr(addr)
    normalized_addr = f"{host}:{port}"
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
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _tcp_open(host, port):
            return True
        time.sleep(0.2)
    raise RuntimeError(f"Timed out stopping TradingCodex service on {normalized_addr}.")


def service_status(addr: str = DEFAULT_SERVICE_ADDR) -> dict:
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
    })
    if status["version"] != TRADINGCODEX_VERSION:
        status["issue"] = "version_mismatch"
        status["next_action"] = f"Run `tcx service stop {normalized_addr}` and then restart Codex if MCP uses the default address."
        return status
    if status["db_path"] and status["db_path"] != current_db:
        status["issue"] = "db_mismatch"
        status["next_action"] = "Use an address backed by the same central DB or stop the mismatched TradingCodex service."
        return status
    status["compatible"] = True
    status["issue"] = ""
    status["next_action"] = "No action needed."
    return status


def service_http_url(addr: str = DEFAULT_SERVICE_ADDR) -> str:
    host, port = _parse_addr(addr)
    return f"http://{host}:{port}/"


def _start_service(workspace_root: Path, addr: str, source_root: Path | None) -> None:
    run_dir = tradingcodex_state_dir() / "run"
    run_dir.mkdir(parents=True, exist_ok=True)
    log_path = run_dir / "service.log"
    env = os.environ.copy()
    env.setdefault("DJANGO_SETTINGS_MODULE", "tradingcodex_service.settings")
    env.setdefault("TRADINGCODEX_WORKSPACE_ROOT", str(workspace_root.resolve()))
    if source_root:
        current = env.get("PYTHONPATH")
        env["PYTHONPATH"] = str(source_root.resolve()) + (f":{current}" if current else "")
    with log_path.open("ab") as log_handle:
        subprocess.Popen(
            [sys.executable, "-m", "tradingcodex_cli", "service", "runserver", addr, "--noreload"],
            cwd=workspace_root,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            close_fds=True,
            start_new_session=True,
        )


def _parse_addr(addr: str) -> tuple[str, int]:
    if ":" in addr:
        host, port_text = addr.rsplit(":", 1)
        return host or "127.0.0.1", int(port_text)
    return "127.0.0.1", int(addr)


def _tcp_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.25):
            return True
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
        and health.get("version") != TRADINGCODEX_VERSION
        and (not service_db or service_db == str(tradingcodex_db_path()))
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
            f"Run `tcx service stop {addr}` or choose a free service address, "
            "then fully restart Codex if project MCP uses the default address."
        )
    service_db = str(health.get("db_path") or "")
    current_db = str(tradingcodex_db_path())
    if service_db and service_db != current_db:
        raise RuntimeError(
            f"TradingCodex service DB mismatch: service={service_db} package={current_db}. "
            f"Stop the TradingCodex service at {service_http_url(addr)} "
            "or use an address backed by the same central DB."
        )


def _service_health(host: str, port: int) -> dict:
    url = f"http://{host}:{port}/api/health"
    try:
        with urllib.request.urlopen(url, timeout=0.5) as response:
            payload = response.read().decode("utf-8")
        data = json.loads(payload)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _service_pids(host: str, port: int, health: dict) -> list[int]:
    pid = health.get("pid")
    pids = {int(pid)} if isinstance(pid, int) and pid > 0 else set()
    try:
        result = subprocess.run(
            ["lsof", f"-tiTCP:{port}", "-sTCP:LISTEN"],
            text=True,
            capture_output=True,
            timeout=1,
            check=False,
        )
        pids.update(int(line) for line in result.stdout.splitlines() if line.strip().isdigit())
    except Exception:
        pass
    return sorted(pids)

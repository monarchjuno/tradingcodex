from __future__ import annotations

from contextlib import nullcontext
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import signal
import subprocess
import sys
import threading
from pathlib import Path

import pytest
from django.core.servers.basehttp import WSGIRequestHandler

from tradingcodex_cli import service_autostart
from tradingcodex_cli.commands.mode import mode
from tradingcodex_service.application.runtime_mode import get_runtime_mode_status, set_runtime_mode
from tradingcodex_service.runtime_profile import (
    assert_service_binding_allowed,
    is_loopback_host,
    remote_configuration_errors,
)


def remote_env() -> dict[str, str]:
    return {
        "TRADINGCODEX_SERVICE_PROFILE": "remote",
        "TRADINGCODEX_DEBUG": "0",
        "TRADINGCODEX_SECRET_KEY": "django-secret-key-that-is-long-and-random-123",
        "TRADINGCODEX_API_KEY": "api-key-that-is-distinct-and-long-enough-456",
        "TRADINGCODEX_API_PRINCIPAL": "remote-operator",
        "TRADINGCODEX_ALLOWED_HOSTS": "trading.example.com",
        "TRADINGCODEX_CSRF_TRUSTED_ORIGINS": "https://trading.example.com",
        "TRADINGCODEX_TRANSPORT_SECURITY": "reverse-proxy",
    }


@pytest.mark.parametrize("host", ["localhost", "localhost.", "127.0.0.1", "127.12.34.56", "::1", "[::1]"])
def test_loopback_host_detection(host: str) -> None:
    assert is_loopback_host(host) is True


def test_local_profile_is_allowed_only_on_loopback() -> None:
    assert_service_binding_allowed("127.0.0.1:48267", {})

    with pytest.raises(RuntimeError, match="Refusing non-loopback") as exc_info:
        assert_service_binding_allowed("0.0.0.0:48267", {})

    message = str(exc_info.value)
    assert "TRADINGCODEX_SERVICE_PROFILE=remote" in message
    assert "TRADINGCODEX_DEBUG=0" in message
    assert "authenticated mutations" in message
    assert "TRADINGCODEX_TRANSPORT_SECURITY=reverse-proxy" in message


def test_complete_remote_profile_allows_non_loopback_binding() -> None:
    env = remote_env()
    assert remote_configuration_errors(env) == []
    assert_service_binding_allowed("0.0.0.0:48267", env)


def test_incomplete_remote_profile_is_rejected_even_on_loopback() -> None:
    with pytest.raises(RuntimeError, match="Invalid TradingCodex remote profile"):
        assert_service_binding_allowed("127.0.0.1:48267", {"TRADINGCODEX_SERVICE_PROFILE": "remote"})


def test_remote_profile_rejects_wildcard_hosts_and_insecure_origins() -> None:
    env = remote_env()
    env["TRADINGCODEX_ALLOWED_HOSTS"] = "*"
    env["TRADINGCODEX_CSRF_TRUSTED_ORIGINS"] = "http://trading.example.com"

    errors = remote_configuration_errors(env)

    assert any("ALLOWED_HOSTS" in error for error in errors)
    assert any("HTTPS" in error for error in errors)


def test_service_entrypoint_refuses_insecure_non_loopback_before_socket_access(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    for name in remote_env():
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("TRADINGCODEX_SERVICE_PROFILE", "local")
    socket_calls: list[tuple[str, int]] = []
    monkeypatch.setattr(
        service_autostart,
        "_tcp_open",
        lambda host, port: socket_calls.append((host, port)) or False,
    )

    with pytest.raises(RuntimeError, match="Refusing non-loopback"):
        service_autostart.ensure_service_up(tmp_path, addr="0.0.0.0:48267")

    assert socket_calls == []


def test_service_start_allows_slow_ready_health_within_default_timeout(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    state = {"started": False, "now": 0.0}

    monkeypatch.setattr(service_autostart, "assert_service_binding_allowed", lambda _addr: None)
    monkeypatch.setattr(service_autostart, "tradingcodex_file_lock", lambda _name: nullcontext())
    monkeypatch.setattr(service_autostart, "_tcp_open", lambda _host, _port: state["started"])
    monkeypatch.setattr(
        service_autostart,
        "_compatible_service",
        lambda _host, _port: state["now"] >= 9.0,
    )
    monkeypatch.setattr(
        service_autostart,
        "_start_service",
        lambda _workspace, _addr, _source: state.__setitem__("started", True),
    )
    monkeypatch.setattr(service_autostart.time, "monotonic", lambda: state["now"])
    monkeypatch.setattr(
        service_autostart.time,
        "sleep",
        lambda seconds: state.__setitem__("now", state["now"] + seconds),
    )

    assert service_autostart.ensure_service_up(tmp_path, addr="127.0.0.1:49193") is True
    assert state["now"] >= 9.0


@pytest.mark.parametrize(
    ("local_port", "expected"),
    [
        (49197, False),
        (53000, True),
    ],
)
def test_tcp_open_rejects_ephemeral_self_connection(
    monkeypatch: pytest.MonkeyPatch,
    local_port: int,
    expected: bool,
) -> None:
    class Connection:
        def __enter__(self) -> "Connection":
            return self

        def __exit__(self, *_args: object) -> None:
            pass

        def getsockname(self) -> tuple[str, int]:
            return "127.0.0.1", local_port

    monkeypatch.setattr(
        service_autostart.socket,
        "create_connection",
        lambda _address, timeout: Connection(),
    )

    assert service_autostart._tcp_open("127.0.0.1", 49197) is expected


def test_loopback_health_probe_ignores_environment_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    requests: list[str] = []

    class HealthHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            requests.append(self.path)
            body = json.dumps({"service": "tradingcodex", "ready": True}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format: str, *_args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), HealthHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:1")
    monkeypatch.setenv("NO_PROXY", "")
    monkeypatch.setenv("no_proxy", "")
    try:
        health = service_autostart._service_health("127.0.0.1", server.server_port)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert health == {"service": "tradingcodex", "ready": True}
    assert requests == ["/api/health/ready"]


def test_loopback_health_probe_accepts_not_ready_identity() -> None:
    class HealthHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            body = json.dumps(
                {
                    "service": "tradingcodex",
                    "version": service_autostart.TRADINGCODEX_VERSION,
                    "ready": False,
                    "reason_codes": ["migrations_pending"],
                }
            ).encode("utf-8")
            self.send_response(503)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format: str, *_args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), HealthHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        health = service_autostart._service_health("127.0.0.1", server.server_port)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert health["service"] == "tradingcodex"
    assert health["ready"] is False
    assert health["reason_codes"] == ["migrations_pending"]


def test_service_startup_log_tail_redacts_environment_secrets(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    canary = "native-startup-secret-value"
    monkeypatch.setenv("TRADINGCODEX_HOME", str(tmp_path))
    monkeypatch.setenv("PROVIDER_API_KEY", canary)
    startup_log = tmp_path / "state/run/service-startup.log"
    startup_log.parent.mkdir(parents=True)
    startup_log.write_text(f"provider failed: {canary}\n", encoding="utf-8")

    tail = service_autostart._service_startup_log_tail()

    assert canary not in tail
    assert "<redacted>" in tail


def test_failed_detached_startup_is_terminated() -> None:
    class Process:
        returncode: int | None = None
        signal_sent: int | None = None
        terminated = False

        def poll(self) -> int | None:
            return self.returncode

        def send_signal(self, signum: int) -> None:
            self.signal_sent = signum
            self.returncode = -signum

        def terminate(self) -> None:
            self.terminated = True
            self.returncode = 1

        def wait(self, timeout: int) -> int:
            assert timeout == 2
            assert self.returncode is not None
            return self.returncode

        def kill(self) -> None:
            raise AssertionError("graceful failed-startup termination should succeed")

    process = Process()

    service_autostart._terminate_failed_startup(process)  # type: ignore[arg-type]

    if os.name == "nt":
        assert process.terminated is True
    else:
        assert process.signal_sent == signal.SIGABRT


def test_local_wsgi_bind_does_not_resolve_reverse_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        service_autostart.socket,
        "getfqdn",
        lambda _host: (_ for _ in ()).throw(AssertionError("reverse DNS lookup attempted")),
    )

    server = service_autostart.NonResolvingWSGIServer(
        ("127.0.0.1", 0),
        WSGIRequestHandler,
    )
    try:
        assert server.server_name == "127.0.0.1"
        assert server.server_port > 0
    finally:
        server.server_close()


def test_remote_settings_enable_django_transport_security(tmp_path: Path) -> None:
    env = os.environ.copy()
    env.update(remote_env())
    env["TRADINGCODEX_HOME"] = str(tmp_path / "home")
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from tradingcodex_service import settings as s; "
                "assert s.SERVICE_PROFILE == 'remote'; "
                "assert s.DEBUG is False; "
                "assert s.SECURE_SSL_REDIRECT is True; "
                "assert s.SESSION_COOKIE_SECURE is True; "
                "assert s.CSRF_COOKIE_SECURE is True; "
                "assert s.SECURE_PROXY_SSL_HEADER == ('HTTP_X_FORWARDED_PROTO', 'https')"
            ),
        ],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_retired_persistent_mode_is_inert_and_preserved(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    legacy = tmp_path / ".tradingcodex/runtime/mode.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("{not valid json", encoding="utf-8")
    original = legacy.read_bytes()
    monkeypatch.setenv("TRADINGCODEX_CODEX_PERMISSION", "unrestricted")

    mode(tmp_path, ["status"])

    output = capsys.readouterr().out
    assert "TradingCodex persistent mode command: compatibility status only" in output
    assert "Build enabled" not in output
    assert "exact `$tcx-build`" in output
    assert "(ignored)" in output
    status = get_runtime_mode_status(tmp_path, full_access_detected=True)
    assert status["status"] == "retired"
    assert status["authority"] == "none"
    assert status["build_enabled"] is False
    assert status["full_access_required"] is False
    assert status["permission_is_advisory"] is True
    assert status["full_access_detected"] is True
    assert status["legacy_mode_file_present"] is True
    assert status["legacy_mode_file_ignored"] is True
    assert legacy.read_bytes() == original

    with pytest.raises(ValueError, match="Persistent TradingCodex build mode is retired"):
        mode(tmp_path, ["set", "build", "--reason", "test"])
    assert legacy.read_bytes() == original

    unused_root = tmp_path / "unused"
    with pytest.raises(ValueError, match="Persistent TradingCodex build mode is retired"):
        set_runtime_mode(unused_root, "build", reason="test")
    assert not (unused_root / ".tradingcodex/runtime/mode.json").exists()


def test_retired_persistent_mode_never_reads_or_follows_a_legacy_symlink(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    legacy = tmp_path / ".tradingcodex/runtime/mode.json"
    legacy.parent.mkdir(parents=True)
    outside = tmp_path / "outside-mode-target"
    outside.mkdir()
    marker = outside / "marker.txt"
    marker.write_text("must remain untouched", encoding="utf-8")
    try:
        legacy.symlink_to(outside, target_is_directory=True)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"symlinks are unavailable: {exc}")

    real_readlink = os.readlink

    def guarded_readlink(path: os.PathLike[str] | str, *args: object, **kwargs: object) -> str:
        if Path(path) == legacy:
            raise AssertionError("retired mode status followed the legacy mode symlink")
        return real_readlink(path, *args, **kwargs)

    monkeypatch.setattr(os, "readlink", guarded_readlink)

    status = get_runtime_mode_status(tmp_path, full_access_detected=True)

    assert status["status"] == "retired"
    assert status["build_enabled"] is False
    assert status["legacy_mode_file_present"] is True
    assert status["legacy_mode_file_ignored"] is True
    assert legacy.is_symlink()
    with pytest.raises(ValueError, match="Persistent TradingCodex build mode is retired"):
        set_runtime_mode(tmp_path, "build")
    assert marker.read_text(encoding="utf-8") == "must remain untouched"

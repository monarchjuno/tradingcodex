from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tradingcodex_cli.commands import doctor


@pytest.mark.parametrize(
    ("installed", "ok", "warn"),
    [
        ("0.144.0", False, False),
        ("0.144.1", True, True),
        ("0.144.2", True, True),
        ("0.144.4", True, False),
        ("0.145.0", True, True),
    ],
)
def test_codex_cli_reference_version_check(
    monkeypatch: pytest.MonkeyPatch,
    installed: str,
    ok: bool,
    warn: bool,
) -> None:
    monkeypatch.setattr(doctor.shutil, "which", lambda _name: "/usr/local/bin/codex")
    monkeypatch.setattr(
        doctor.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            args=["codex", "--version"],
            returncode=0,
            stdout=f"codex-cli {installed}\n",
            stderr="",
        ),
    )

    result = doctor._codex_cli_runtime_check()

    assert result["ok"] is ok
    assert bool(result.get("warn")) is warn
    assert f"installed={installed}" in result["detail"]
    assert "required>=0.144.1" in result["detail"]
    assert "reference=0.144.4" in result["detail"]
    if installed in {"0.144.1", "0.144.2"}:
        assert "compatible but older than reference" in result["detail"]
    if installed == "0.145.0":
        assert "newer client requires harness revalidation" in result["detail"]


def test_codex_cli_reference_version_check_warns_when_cli_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(doctor.shutil, "which", lambda _name: None)

    result = doctor._codex_cli_runtime_check()

    assert result["ok"] is False
    assert result["warn"] is True
    assert "not found on PATH" in result["detail"]


def test_doctor_defaults_to_layer_summary_and_verbose_keeps_full_evidence(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    service_check = {
        "layer": "service",
        "name": "service detail",
        "ok": True,
        "detail": "ready",
    }
    guidance_warning = {
        "layer": "guidance",
        "name": "model evidence",
        "ok": False,
        "warn": True,
        "detail": "not promoted",
    }
    monkeypatch.setattr(doctor, "_central_service_checks", lambda _root: [service_check])
    monkeypatch.setattr(doctor, "_guidance_checks", lambda _root: [guidance_warning])
    for name in (
        "_enforcement_checks",
        "_information_barrier_checks",
        "_improvement_checks",
        "_mcp_checks",
    ):
        monkeypatch.setattr(doctor, name, lambda _root: [])

    doctor.doctor(tmp_path, "all")
    concise = capsys.readouterr().out
    assert "PASS service              1 passed" in concise
    assert "WARN guidance             0 passed, 1 warning(s)" in concise
    assert "model evidence - not promoted" in concise
    assert "service detail - ready" not in concise
    assert "doctor --verbose" in concise

    doctor.doctor(tmp_path, "all", verbose=True)
    verbose = capsys.readouterr().out
    assert "service detail - ready" in verbose
    assert "model evidence - not promoted" in verbose


def test_focused_doctor_skips_unrequested_layer_work(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    called: list[str] = []
    monkeypatch.setattr(doctor, "_central_service_checks", lambda _root: [])

    def checks(name: str):
        def build(_root: Path) -> list[dict[str, object]]:
            called.append(name)
            return [{"layer": name, "name": name, "ok": True, "detail": "ok"}]

        return build

    for name in (
        "guidance",
        "enforcement",
        "information-barrier",
        "improvement",
        "mcp",
    ):
        monkeypatch.setattr(doctor, f"_{name.replace('-', '_')}_checks", checks(name))

    doctor.doctor(tmp_path, "improvement")
    capsys.readouterr()
    assert called == ["improvement"]

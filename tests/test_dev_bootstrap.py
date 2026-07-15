from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path

import pytest

from tradingcodex_cli.commands import bootstrap
from tradingcodex_cli.package_source import (
    EXECUTABLE_SOURCE_ENV,
    LOCAL_EXECUTABLE_SOURCE_KIND,
    PACKAGE_SOURCE_KIND_ENV,
)
from tradingcodex_cli.service_autostart import configured_service_addr
from tradingcodex_service.application.runtime import resolve_tradingcodex_home


ROOT = Path(__file__).resolve().parents[1]


def test_attach_dev_selects_the_running_source_checkout(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setenv("HOME", str(tmp_path / "user-home"))
    for name in (
        "TRADINGCODEX_HOME",
        "TRADINGCODEX_HOME_SOURCE",
        "TRADINGCODEX_DB_NAME",
        "TRADINGCODEX_SERVICE_ADDR",
        bootstrap.DEVELOPMENT_SOURCE_ENV,
    ):
        monkeypatch.delenv(name, raising=False)

    def fake_bootstrap(target: Path, *, dry_run: bool) -> dict[str, object]:
        captured.update(
            {
                "target": target,
                "dry_run": dry_run,
                "source": os.environ[EXECUTABLE_SOURCE_ENV],
                "source_kind": os.environ[PACKAGE_SOURCE_KIND_ENV],
                "home": os.environ["TRADINGCODEX_HOME"],
                "home_source": os.environ["TRADINGCODEX_HOME_SOURCE"],
                "service_addr": os.environ["TRADINGCODEX_SERVICE_ADDR"],
            }
        )
        return {"target_dir": str(target), "modules": ["codex-base"]}

    monkeypatch.setattr(bootstrap, "bootstrap_workspace", fake_bootstrap)

    target = tmp_path / "workspace"
    bootstrap.attach([str(target), "--dev", "--dry-run"])
    platform_home = resolve_tradingcodex_home(
        environ={"HOME": str(tmp_path / "user-home")},
    ).home
    source_key = hashlib.sha256(os.path.normcase(str(ROOT)).encode("utf-8")).hexdigest()[:12]
    expected_home = Path(platform_home) / "development" / f"source-{source_key}"

    assert captured == {
        "target": target.resolve(),
        "dry_run": True,
        "source": str(ROOT),
        "source_kind": LOCAL_EXECUTABLE_SOURCE_KIND,
        "home": str(expected_home),
        "home_source": "environment_override",
        "service_addr": bootstrap._development_service_addr(expected_home),
    }


def test_attach_dev_and_from_are_mutually_exclusive() -> None:
    with pytest.raises(SystemExit) as exc:
        bootstrap.attach(["--dev", "--from", "tradingcodex"])

    assert exc.value.code == 2


def test_dev_update_preserves_existing_home_and_db_override(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    home = tmp_path / "existing-home"
    db = tmp_path / "existing-db" / "ledger.sqlite3"
    captured: dict[str, str] = {}
    for name in (
        "TRADINGCODEX_HOME",
        "TRADINGCODEX_HOME_SOURCE",
        "TRADINGCODEX_DB_NAME",
        "TRADINGCODEX_SERVICE_ADDR",
        bootstrap.DEVELOPMENT_SOURCE_ENV,
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(
        bootstrap,
        "validate_generated_workspace",
        lambda _target: {
            "module_lock": {
                "tradingcodex_package_spec": "local-explicit",
                "tradingcodex_home": str(home),
                "home_source": "environment_override",
                "tradingcodex_db_path": str(db),
                "db_source": "environment_override",
            }
        },
    )

    def fake_bootstrap(target: Path, *, dry_run: bool, update: bool) -> dict[str, object]:
        captured.update(
            home=os.environ["TRADINGCODEX_HOME"],
            home_source=os.environ["TRADINGCODEX_HOME_SOURCE"],
            db=os.environ["TRADINGCODEX_DB_NAME"],
            service_addr=os.environ["TRADINGCODEX_SERVICE_ADDR"],
        )
        assert target == (tmp_path / "workspace").resolve()
        assert dry_run is True
        assert update is True
        return {"target_dir": str(target), "modules": ["codex-base"]}

    monkeypatch.setattr(bootstrap, "bootstrap_workspace", fake_bootstrap)

    bootstrap.update([str(tmp_path / "workspace"), "--dev", "--dry-run"])

    assert captured == {
        "home": str(home),
        "home_source": "environment_override",
        "db": str(db),
        "service_addr": bootstrap._development_service_addr(home),
    }


def test_dev_update_rejects_release_workspace(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        bootstrap,
        "validate_generated_workspace",
        lambda _target: {
            "module_lock": {
                "tradingcodex_package_spec": "tradingcodex",
            }
        },
    )

    with pytest.raises(ValueError, match="separate development workspace"):
        bootstrap.update([str(tmp_path / "workspace"), "--dev", "--dry-run"])


def test_configured_service_addr_prefers_workspace_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TRADINGCODEX_SERVICE_ADDR", "127.0.0.1:29123")
    assert configured_service_addr() == "127.0.0.1:29123"


def test_posix_installer_dev_uses_checkout_for_runner_and_provenance(
    tmp_path: Path,
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    capture = tmp_path / "uvx-args.txt"
    dev_source_capture = tmp_path / "dev-source.txt"
    fake_uvx = bin_dir / "uvx"
    fake_uvx.write_text(
        "#!/bin/sh\n"
        "printf '%s' \"${_TRADINGCODEX_DEV_SOURCE_ROOT:-}\" > \"$TCX_TEST_DEV_SOURCE\"\n"
        "printf '%s\\n' \"$@\" > \"$TCX_TEST_UVX_ARGS\"\n",
        encoding="utf-8",
    )
    fake_uvx.chmod(0o755)
    workspace = tmp_path / "workspace"
    environment = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
        "TCX_TEST_UVX_ARGS": str(capture),
        "TCX_TEST_DEV_SOURCE": str(dev_source_capture),
    }

    result = subprocess.run(
        [
            "sh",
            str(ROOT / "install.sh"),
            "--dev",
            "--no-doctor",
            str(workspace),
        ],
        cwd=tmp_path,
        env=environment,
        text=True,
        capture_output=True,
        check=True,
    )

    assert "development workspace" in result.stderr
    assert capture.read_text(encoding="utf-8").splitlines() == [
        "--isolated",
        "--refresh",
        "--with-editable",
        str(ROOT),
        "python",
        "-m",
        "tradingcodex_cli",
        "attach",
        str(workspace),
        "--dev",
    ]
    assert dev_source_capture.read_text(encoding="utf-8") == str(ROOT)


def test_posix_installer_dev_update_forwards_dev_mode(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    capture = tmp_path / "uvx-args.txt"
    fake_uvx = bin_dir / "uvx"
    fake_uvx.write_text(
        "#!/bin/sh\n"
        "printf '%s\\n' \"$@\" > \"$TCX_TEST_UVX_ARGS\"\n",
        encoding="utf-8",
    )
    fake_uvx.chmod(0o755)
    workspace = tmp_path / "workspace"
    environment = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
        "TCX_TEST_UVX_ARGS": str(capture),
    }

    result = subprocess.run(
        [
            "sh",
            str(ROOT / "install.sh"),
            "--dev",
            "--update",
            "--no-doctor",
            str(workspace),
        ],
        cwd=tmp_path,
        env=environment,
        text=True,
        capture_output=True,
        check=True,
    )

    assert "updating TradingCodex development workspace" in result.stderr
    assert capture.read_text(encoding="utf-8").splitlines() == [
        "--isolated",
        "--refresh",
        "--with-editable",
        str(ROOT),
        "python",
        "-m",
        "tradingcodex_cli",
        "update",
        str(workspace),
        "--dev",
        "--no-doctor",
    ]


def test_posix_installer_rejects_dev_with_explicit_source(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            "sh",
            str(ROOT / "install.sh"),
            "--dev",
            "--from",
            "tradingcodex",
            str(tmp_path / "workspace"),
        ],
        cwd=tmp_path,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 2
    assert "--dev and --from cannot be used together" in result.stderr
    assert not (tmp_path / "workspace").exists()

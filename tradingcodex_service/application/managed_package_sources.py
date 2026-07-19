from __future__ import annotations

import ipaddress
import os
import re
import subprocess
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Callable, Iterator, Sequence
from urllib.parse import unquote, urlsplit

from tradingcodex_service.application.git_subprocess import (
    isolated_git_command,
    isolated_git_environment,
)


@contextmanager
def materialized_package_source(
    *,
    local_source: Path | str | None,
    git_source: str | None,
    ref: str,
    label: str,
    temporary_prefix: str,
    checkout_paths: Sequence[str],
    preflight: Callable[[Path], None],
) -> Iterator[tuple[Path, dict[str, Any]]]:
    """Materialize one explicit local or Git package source safely."""

    if bool(local_source) == bool(git_source):
        raise ValueError(f"select exactly one explicit {label} source: local or git")
    if local_source:
        if ref:
            raise ValueError(f"{label} --ref is valid only for an explicit Git source")
        path = Path(local_source).expanduser().resolve()
        if not path.is_dir():
            raise ValueError(f"{label} local source is not a directory: {path}")
        yield path, {
            "kind": "local",
            "location": str(path),
            "ref": "",
            "resolved_revision": git_revision(path),
        }
        return

    location = str(git_source or "").strip()
    if (
        not location
        or location.startswith("-")
        or "\x00" in location
        or "\n" in location
        or "\r" in location
        or len(location) > 4096
    ):
        raise ValueError(f"{label} Git source is invalid")
    validate_git_location(location, label=label)
    if ref and (
        ref.startswith("-")
        or "\x00" in ref
        or "\n" in ref
        or "\r" in ref
        or len(ref) > 256
    ):
        raise ValueError(f"{label} Git ref is invalid")
    with tempfile.TemporaryDirectory(prefix=temporary_prefix) as temporary:
        checkout = Path(temporary) / "checkout"
        run_git(["init", "--quiet", str(checkout)], label=label)
        run_git(["-C", str(checkout), "remote", "add", "origin", location], label=label)
        run_git(
            [
                "-C", str(checkout), "fetch", "--quiet", "--depth", "1",
                "--no-tags", "--filter=blob:none", "origin", ref or "HEAD",
            ],
            label=label,
        )
        revision = git_revision(checkout, "FETCH_HEAD^{commit}", label=label)
        if not revision:
            raise ValueError(f"{label} Git source did not resolve to a commit")
        preflight(checkout)
        run_git(
            ["-C", str(checkout), "checkout", "--quiet", "FETCH_HEAD", "--", *checkout_paths],
            label=label,
        )
        yield checkout, {
            "kind": "git",
            "location": location,
            "ref": ref or "HEAD",
            "resolved_revision": revision,
        }


def git_tree_records(
    checkout: Path,
    *,
    recursive: bool,
    paths: Sequence[str] = (),
    max_bytes: int = 256 * 1024,
    label: str,
) -> list[tuple[str, str, int | None, str]]:
    arguments = ["-C", str(checkout), "ls-tree"]
    if recursive:
        arguments.append("-r")
    arguments.extend(("-z", "-l", "FETCH_HEAD"))
    if paths:
        arguments.extend(("--", *paths))
    output = run_git_bounded_output(
        arguments,
        max_bytes=max_bytes,
        read_only=True,
        timeout=15,
        label=label,
    )
    records: list[tuple[str, str, int | None, str]] = []
    for raw_record in output.split(b"\0"):
        if not raw_record:
            continue
        try:
            header, raw_path = raw_record.split(b"\t", 1)
            mode, object_type, _object_id, raw_size = header.split(b" ", 3)
            size = None if raw_size.strip() == b"-" else int(raw_size.strip())
            records.append(
                (mode.decode("ascii"), object_type.decode("ascii"), size, raw_path.decode("utf-8"))
            )
        except (UnicodeError, ValueError) as exc:
            raise ValueError(f"{label} Git tree metadata is invalid") from exc
    return records


def run_git_bounded_output(
    args: list[str],
    *,
    max_bytes: int,
    read_only: bool,
    timeout: int,
    label: str,
) -> bytes:
    try:
        process = subprocess.Popen(
            isolated_git_command(args),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=isolated_git_environment(read_only=read_only),
        )
    except OSError as exc:
        raise ValueError(f"{label} Git tree could not be inspected") from exc
    expired = threading.Event()

    def terminate_expired() -> None:
        if process.poll() is None:
            expired.set()
            try:
                process.kill()
            except OSError:
                pass

    timer = threading.Timer(timeout, terminate_expired)
    timer.daemon = True
    timer.start()
    try:
        if process.stdout is None:  # pragma: no cover
            raise ValueError(f"{label} Git tree output is unavailable")
        output = process.stdout.read(max_bytes + 1)
        overflow = len(output) > max_bytes
        if overflow:
            process.kill()
        return_code = process.wait(timeout=5)
    except (OSError, subprocess.TimeoutExpired) as exc:
        process.kill()
        process.wait()
        raise ValueError(f"{label} Git tree could not be inspected") from exc
    finally:
        timer.cancel()
        if process.stdout is not None:
            process.stdout.close()
    if expired.is_set():
        raise ValueError(f"{label} Git tree inspection timed out")
    if overflow:
        raise ValueError(f"{label} Git tree exceeds the inspection output limit")
    if return_code != 0:
        detail = output.decode("utf-8", errors="replace").strip().splitlines()
        suffix = f": {detail[-1]}" if detail else ""
        raise ValueError(f"{label} Git tree could not be inspected{suffix}")
    return output


def run_git(args: list[str], *, label: str) -> None:
    try:
        run_git_bounded_output(
            args,
            max_bytes=64 * 1024,
            read_only=False,
            timeout=120,
            label=label,
        )
    except ValueError as exc:
        raise ValueError(f"{label} Git source could not be materialized") from exc


def git_revision(path: Path, revision_name: str = "HEAD^{commit}", *, label: str = "package") -> str:
    try:
        output = run_git_bounded_output(
            ["-C", str(path), "rev-parse", "--verify", revision_name],
            max_bytes=4096,
            read_only=True,
            timeout=10,
            label=label,
        )
    except ValueError:
        return ""
    revision = output.decode("ascii", errors="ignore").strip()
    return revision if re.fullmatch(r"[0-9a-fA-F]{40,64}", revision) else ""


def validate_git_location(location: str, *, label: str) -> None:
    if "::" in location:
        raise ValueError(f"{label} Git source uses a prohibited remote helper")
    if "://" in location:
        parsed = urlsplit(location)
        if parsed.scheme not in {"https", "ssh", "file"}:
            raise ValueError(f"{label} Git source scheme is not allowed")
        if parsed.query or parsed.fragment or parsed.password:
            raise ValueError(f"{label} Git source must not contain credentials, query, or fragment")
        if parsed.scheme == "https" and parsed.username:
            raise ValueError(f"{label} HTTPS source must not contain user information")
        return
    if Path(location).expanduser().exists():
        return
    if not re.fullmatch(r"[A-Za-z0-9._-]+@[A-Za-z0-9.-]+:[A-Za-z0-9._~/-]+", location):
        raise ValueError(f"{label} Git source must be an allowed URL, local path, or SSH location")


def validate_public_https_url(value: str, *, label: str, require_path: bool = True) -> str:
    if (
        not value
        or len(value) > 4096
        or value != value.strip()
        or "\\" in value
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
    ):
        raise ValueError(f"{label} is invalid")
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError(f"{label} must be a public HTTPS URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError(f"{label} must not contain credentials, query, or fragment")
    try:
        hostname = parsed.hostname or ""
        port = parsed.port
        ascii_hostname = hostname.encode("idna").decode("ascii").lower()
    except (UnicodeError, ValueError) as exc:
        raise ValueError(f"{label} host is invalid") from exc
    if port not in {None, 443}:
        raise ValueError(f"{label} must use the standard HTTPS port")
    reserved_suffixes = (
        ".localhost", ".local", ".localdomain", ".internal", ".lan", ".home",
        ".home.arpa", ".invalid", ".test", ".example", ".onion", ".alt",
    )
    if (
        not ascii_hostname
        or ascii_hostname.endswith(".")
        or ascii_hostname == "localhost"
        or ascii_hostname in {suffix[1:] for suffix in reserved_suffixes}
        or ascii_hostname.endswith(reserved_suffixes)
    ):
        raise ValueError(f"{label} must use a public host")
    try:
        address = ipaddress.ip_address(ascii_hostname)
    except ValueError:
        labels = ascii_hostname.split(".")
        hostname_label = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")
        numeric_label = re.compile(r"(?:0x[0-9a-f]+|[0-9]+)", re.I)
        if (
            len(labels) < 2
            or any(not hostname_label.fullmatch(item) for item in labels)
            or all(numeric_label.fullmatch(item) for item in labels)
            or labels[-1].isdigit()
        ):
            raise ValueError(f"{label} must use a public host")
    else:
        if not address.is_global:
            raise ValueError(f"{label} must use a public host")
    if require_path and (not parsed.path or parsed.path == "/"):
        raise ValueError(f"{label} must identify a path")
    return value


def tracked_source_metadata(
    workspace_root: Path,
    source_root: Path,
    source: dict[str, Any],
    declared: dict[str, Any],
    *,
    label: str,
) -> dict[str, Any]:
    tracked = {**source, "declared": declared}
    if tracked["kind"] == "local":
        tracked["location"] = workspace_source_locator(workspace_root, source_root, label=label)
        return tracked
    location = str(tracked["location"])
    if not is_remote_git_location(location):
        local_path = local_git_source_path(location)
        tracked["location"] = (
            workspace_source_locator(workspace_root, local_path, label=label)
            if local_path is not None
            else ""
        )
    return tracked


def workspace_source_locator(workspace_root: Path, source_root: Path, *, label: str) -> str:
    try:
        relative = source_root.resolve().relative_to(workspace_root.resolve())
    except ValueError:
        return ""
    locator = relative.as_posix()
    if locator == ".":
        return ""
    validate_workspace_source_locator(locator, label=label)
    return locator


def resolve_workspace_source_locator(workspace_root: Path, locator: str, *, label: str) -> Path:
    validate_workspace_source_locator(locator, label=label)
    return workspace_root.joinpath(*PurePosixPath(locator).parts)


def validate_workspace_source_locator(locator: str, *, label: str) -> None:
    path = PurePosixPath(locator)
    if (
        not locator
        or path.is_absolute()
        or PureWindowsPath(locator).is_absolute()
        or locator != path.as_posix()
        or not path.parts
        or any(part in {"", ".", ".."} for part in path.parts)
        or any(ord(character) < 32 or ord(character) == 127 for character in locator)
        or "\\" in locator
        or re.match(r"^[A-Za-z]:", locator)
    ):
        raise ValueError(f"{label} tracked source must be a canonical workspace-relative POSIX path")


def is_remote_git_location(location: str) -> bool:
    if "://" in location:
        return urlsplit(location).scheme != "file"
    return bool(re.fullmatch(r"[A-Za-z0-9._-]+@[A-Za-z0-9.-]+:[A-Za-z0-9._~/-]+", location))


def local_git_source_path(location: str) -> Path | None:
    if location.startswith("file://"):
        parsed = urlsplit(location)
        if parsed.netloc not in {"", "localhost"}:
            return None
        return Path(unquote(parsed.path)).expanduser().resolve()
    return Path(location).expanduser().resolve()

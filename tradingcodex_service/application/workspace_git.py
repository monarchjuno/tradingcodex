from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from tradingcodex_service.application.common import atomic_write_text
from tradingcodex_service.application.git_subprocess import (
    isolated_git_command,
    isolated_git_environment,
)


GITIGNORE_BLOCK_NAME = "TradingCodex local/private state"
GITIGNORE_START = f"# BEGIN {GITIGNORE_BLOCK_NAME}"
GITIGNORE_END = f"# END {GITIGNORE_BLOCK_NAME}"
GITIGNORE_RULES = """# Runtime databases and journals
/state/
/.tradingcodex/state/
*.sqlite3
*.sqlite3-*
*.db-journal
*.db-shm
*.db-wal

# Process, session, and service-status runtime files
/.tradingcodex/mainagent/server-status.json
/.tradingcodex/mainagent/session-start.json
/.tradingcodex/mainagent/subagent-session-state.json
/.tradingcodex/mainagent/session-workflow-runs.json
/.tradingcodex/mainagent/runs/*/web-run.json
/.tradingcodex/mainagent/runs/*/web-run-events.jsonl
/.tradingcodex/mainagent/**/*.pid
/.tradingcodex/mainagent/**/*.sock

# Transient audit streams
/trading/audit/
/.tradingcodex/audit/**/*.jsonl
/.tradingcodex/audit/**/*.log

# Rebuildable caches, indexes, and native lock files
**/__pycache__/
*.py[cod]
/.cache/
/.pytest_cache/
/.ruff_cache/
/trading/research/.index/
/trading/research/datasets/objects/
/.tradingcodex/**/*.lock
/trading/**/*.lock
.DS_Store

# Private Investor Context (hash bindings remain versionable)
/.tradingcodex/user/investor-context.md
/.tradingcodex/mainagent/runs/*/investor-context-snapshot.md

# Raw secrets and credentials
.env
.env.*
!.env.example
/.tradingcodex/secrets/
/.tradingcodex/private/
credentials.json
credentials.yaml
credentials.yml
*.pem
*.key
*.p12
*.pfx
*.secret
*.secrets

# Local logs and process markers
*.log
*.pid"""


def managed_gitignore_block() -> str:
    return f"{GITIGNORE_START}\n{GITIGNORE_RULES}\n{GITIGNORE_END}\n"


def ensure_workspace_git(
    workspace_root: Path | str,
    *,
    initialize_if_missing: bool = True,
) -> dict[str, Any]:
    root = Path(workspace_root).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    before = workspace_git_status(root)
    initialized = False
    if not before["is_worktree"]:
        if not initialize_if_missing:
            raise ValueError(f"TradingCodex workspace must already belong to a Git worktree: {root}")
        _run_git(root, "init", "--quiet", mutating=True, check=True)
        initialized = True
    merge_tradingcodex_gitignore(root)
    status = workspace_git_status(root)
    if not status["is_worktree"]:
        raise ValueError(f"TradingCodex workspace must belong to a Git worktree: {root}")
    return {**status, "initialized": initialized}


def merge_tradingcodex_gitignore(workspace_root: Path | str) -> Path:
    root = Path(workspace_root).expanduser().resolve()
    path = root / ".gitignore"
    if path.is_symlink():
        raise ValueError("workspace .gitignore must be a regular file, not a symlink")
    try:
        existing = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        existing = ""
    except OSError as exc:
        raise ValueError(f"unable to read workspace .gitignore: {path}") from exc

    start_count = existing.count(GITIGNORE_START)
    end_count = existing.count(GITIGNORE_END)
    markers_reversed = (
        start_count == 1
        and end_count == 1
        and existing.index(GITIGNORE_START) > existing.index(GITIGNORE_END)
    )
    if start_count != end_count or start_count > 1 or markers_reversed:
        raise ValueError("TradingCodex managed .gitignore block is malformed")

    block = managed_gitignore_block()
    if start_count == 1:
        before, remainder = existing.split(GITIGNORE_START, 1)
        _, after = remainder.split(GITIGNORE_END, 1)
        replacement = block.rstrip("\n") if after.startswith(("\n", "\r")) else block
        merged = before + replacement + after
    else:
        separator = "" if not existing else "\n" if existing.endswith(("\n", "\r")) else "\n\n"
        merged = existing + separator + block
    atomic_write_text(path, merged)
    return path


def gitignore_contract_status(workspace_root: Path | str) -> dict[str, Any]:
    root = Path(workspace_root).expanduser().resolve()
    path = root / ".gitignore"
    if path.is_symlink():
        return {"path": str(path), "present": True, "current": False, "detail": "workspace .gitignore is a symlink"}
    try:
        content = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {"path": str(path), "present": False, "current": False, "detail": "missing .gitignore"}
    except OSError as exc:
        return {"path": str(path), "present": True, "current": False, "detail": str(exc)}
    start_count = content.count(GITIGNORE_START)
    end_count = content.count(GITIGNORE_END)
    markers_reversed = (
        start_count == 1
        and end_count == 1
        and content.index(GITIGNORE_START) > content.index(GITIGNORE_END)
    )
    if start_count != 1 or end_count != 1 or markers_reversed:
        return {
            "path": str(path),
            "present": True,
            "current": False,
            "detail": f"managed markers start={start_count}, end={end_count}",
        }
    body = content.split(GITIGNORE_START, 1)[1].split(GITIGNORE_END, 1)[0]
    current = body == f"\n{GITIGNORE_RULES}\n"
    return {
        "path": str(path),
        "present": True,
        "current": current,
        "detail": "privacy block current" if current else "TradingCodex privacy block is stale or modified",
    }


def workspace_git_status(workspace_root: Path | str) -> dict[str, Any]:
    root = Path(workspace_root).expanduser().resolve()
    discovered = _run_git(root, "rev-parse", "--show-toplevel")
    if discovered.returncode != 0:
        return {
            "is_worktree": False,
            "git_root": "",
            "git_dirty": False,
            "git_branch": "",
            "git_remote": "",
        }

    git_root = str(Path(discovered.stdout.strip()).expanduser().resolve())
    dirty = _run_git(root, "status", "--porcelain=v1", "--untracked-files=normal", "--", ".", check=True)
    branch = _run_git(root, "symbolic-ref", "--quiet", "--short", "HEAD")
    branch_name = branch.stdout.strip() if branch.returncode == 0 else ""
    if not branch_name:
        detached = _run_git(root, "rev-parse", "--short", "HEAD")
        branch_name = detached.stdout.strip() if detached.returncode == 0 else ""
    remote = _run_git(root, "config", "--get", "remote.origin.url")
    return {
        "is_worktree": True,
        "git_root": git_root,
        "git_dirty": bool(dirty.stdout.strip()),
        "git_branch": branch_name,
        "git_remote": _redacted_remote_url(remote.stdout) if remote.returncode == 0 else "",
    }


def _run_git(
    cwd: Path,
    *args: str,
    mutating: bool = False,
    check: bool = False,
) -> subprocess.CompletedProcess[str]:
    env = isolated_git_environment(read_only=not mutating)
    try:
        result = subprocess.run(
            isolated_git_command(args),
            cwd=cwd,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except FileNotFoundError as exc:
        raise ValueError("Git is required for TradingCodex generated workspaces") from exc
    if check and result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
        raise ValueError(f"Git command failed ({' '.join(args)}): {detail}")
    return result


def _redacted_remote_url(raw_value: str) -> str:
    value = str(raw_value or "").strip()
    if not value:
        return ""
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        return "[redacted]"
    if "::" in value:
        return "[redacted]"
    if "://" not in value:
        sanitized = value.split("?", 1)[0].split("#", 1)[0]
        # Treat SCP-style SSH user information as credential material too.
        match = re.fullmatch(r"[^/@:]+@([^:]+):(.+)", sanitized)
        return f"{match.group(1)}:{match.group(2)}" if match else sanitized
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        return "[redacted]"
    if hostname is None and parsed.scheme != "file":
        return "[redacted]"
    host = hostname or ""
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    netloc = host + (f":{port}" if port is not None else "")
    return urlunsplit((parsed.scheme, netloc, parsed.path, "", ""))

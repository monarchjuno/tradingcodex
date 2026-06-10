from __future__ import annotations

import hashlib
import os
import re
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from tradingcodex_service.application.common import _safe_read, sanitize_id

_RUNTIME_DB_READY = False
_RUNTIME_DB_NAME = ""

def tradingcodex_home() -> Path:
    return Path(os.environ.get("TRADINGCODEX_HOME", "~/.tradingcodex")).expanduser().resolve()


def tradingcodex_state_dir() -> Path:
    return tradingcodex_home() / "state"


def tradingcodex_db_path() -> Path:
    configured = os.environ.get("TRADINGCODEX_DB_NAME")
    if configured:
        return Path(configured).expanduser().resolve()
    return tradingcodex_state_dir() / "tradingcodex.sqlite3"


def configure_tradingcodex_database(workspace_root: Path | str | None = None) -> None:
    global _RUNTIME_DB_READY, _RUNTIME_DB_NAME
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tradingcodex_service.settings")
    db_path = tradingcodex_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db_name = str(db_path)
    if os.environ.get("TRADINGCODEX_DB_NAME") == db_name and _RUNTIME_DB_NAME == db_name:
        return
    os.environ["TRADINGCODEX_DB_NAME"] = db_name
    if _RUNTIME_DB_NAME and _RUNTIME_DB_NAME != db_name:
        _RUNTIME_DB_READY = False
    try:
        from django.conf import settings
        from django.db import connections

        if settings.configured:
            current_name = settings.DATABASES["default"].get("NAME")
            settings.DATABASES["default"]["NAME"] = db_name
            settings.DATABASES["default"].setdefault("OPTIONS", {})["timeout"] = int(os.environ.get("TRADINGCODEX_SQLITE_TIMEOUT", "30"))
            connections["default"].settings_dict["NAME"] = db_name
            connections["default"].settings_dict.setdefault("OPTIONS", {})["timeout"] = int(os.environ.get("TRADINGCODEX_SQLITE_TIMEOUT", "30"))
            if current_name != db_name:
                connections.close_all()
                _RUNTIME_DB_READY = False
    except Exception:
        pass
    _RUNTIME_DB_NAME = db_name


def configure_workspace_database(workspace_root: Path | str | None = None) -> None:
    configure_tradingcodex_database(workspace_root)


def workspace_context_payload(workspace_root: Path | str | None = None) -> dict[str, Any]:
    raw_root = workspace_root or os.environ.get("TRADINGCODEX_WORKSPACE_ROOT") or os.getcwd()
    root = Path(raw_root).expanduser().resolve()
    return {
        "path_hash": hashlib.sha256(str(root).encode("utf-8")).hexdigest(),
        "project_name": root.name or "tradingcodex-workspace",
        "path": str(root),
        "git_remote": _git_remote(root),
        "git_branch": _git_branch(root),
        "db_path": str(tradingcodex_db_path()),
    }


def persist_workspace_context_if_available(workspace_root: Path | str | None = None) -> dict[str, Any]:
    context = workspace_context_payload(workspace_root)
    try:
        ensure_runtime_database(None)
        from apps.harness.models import WorkspaceContext

        WorkspaceContext.objects.update_or_create(
            path_hash=context["path_hash"],
            defaults={
                "project_name": context["project_name"],
                "path": context["path"],
                "git_remote": context["git_remote"],
                "git_branch": context["git_branch"],
                "metadata": {"db_path": context["db_path"]},
            },
        )
    except Exception:
        pass
    return context


def ensure_runtime_database(workspace_root: Path | str | None = None) -> None:
    global _RUNTIME_DB_READY
    configure_tradingcodex_database(workspace_root)
    import django
    from django.apps import apps
    from django.core.management import call_command

    if not apps.ready:
        django.setup()
    if _RUNTIME_DB_READY or os.environ.get("TRADINGCODEX_AUTO_MIGRATE", "1") == "0":
        return
    with tradingcodex_file_lock("migrate"):
        call_command("migrate", interactive=False, verbosity=0, fake_initial=True)
        _sync_missing_runtime_columns()
        _RUNTIME_DB_READY = True


@contextmanager
def workspace_file_lock(workspace_root: Path | str, name: str):
    with tradingcodex_file_lock(name):
        yield


@contextmanager
def tradingcodex_file_lock(name: str):
    lock_path = tradingcodex_state_dir() / f"tradingcodex.{sanitize_id(name)}.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a", encoding="utf-8") as lock_file:
        try:
            import fcntl

            fcntl.flock(lock_file, fcntl.LOCK_EX)
        except Exception:
            pass
        try:
            yield
        finally:
            try:
                import fcntl

                fcntl.flock(lock_file, fcntl.LOCK_UN)
            except Exception:
                pass


def _runtime_model_tables_present() -> bool:
    try:
        from django.apps import apps
        from django.db import connection

        existing = set(connection.introspection.table_names())
        required = {
            model._meta.db_table
            for model in apps.get_models()
            if model._meta.managed and not model._meta.proxy
        }
        if not bool(required) or not required.issubset(existing):
            return False
        for model in apps.get_models():
            if not model._meta.managed or model._meta.proxy:
                continue
            columns = {
                column.name
                for column in connection.introspection.get_table_description(connection.cursor(), model._meta.db_table)
            }
            expected = {field.column for field in model._meta.local_concrete_fields}
            if not expected.issubset(columns):
                return False
        return True
    except Exception:
        return False


def _sync_missing_runtime_columns() -> None:
    try:
        from django.apps import apps
        from django.db import connection

        with connection.schema_editor() as schema_editor:
            for model in apps.get_models():
                if not model._meta.managed or model._meta.proxy:
                    continue
                existing_tables = set(connection.introspection.table_names())
                if model._meta.db_table not in existing_tables:
                    continue
                columns = {
                    column.name
                    for column in connection.introspection.get_table_description(connection.cursor(), model._meta.db_table)
                }
                for field in model._meta.local_concrete_fields:
                    if field.column not in columns:
                        schema_editor.add_field(model, field)
    except Exception:
        return


def _git_dir(root: Path) -> Path | None:
    dotgit = root / ".git"
    if dotgit.is_dir():
        return dotgit
    if dotgit.is_file():
        text = _safe_read(dotgit).strip()
        match = re.match(r"gitdir:\s*(.+)", text)
        if match:
            gitdir = Path(match.group(1))
            return gitdir if gitdir.is_absolute() else (root / gitdir).resolve()
    return None


def _git_branch(root: Path) -> str:
    gitdir = _git_dir(root)
    if not gitdir:
        return ""
    head = _safe_read(gitdir / "HEAD").strip()
    match = re.match(r"ref:\s+refs/heads/(.+)", head)
    return match.group(1) if match else head[:12]


def _git_remote(root: Path) -> str:
    gitdir = _git_dir(root)
    config = _safe_read(gitdir / "config") if gitdir else _safe_read(root / ".git" / "config")
    match = re.search(r'\[remote "origin"\][^\[]*?\n\s*url\s*=\s*(.+)', config)
    return match.group(1).strip() if match else ""

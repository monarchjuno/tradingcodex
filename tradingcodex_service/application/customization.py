from __future__ import annotations

import os
import json
import re
import shutil
import tomllib
from pathlib import Path
from typing import Any

from tradingcodex_service.application.common import atomic_write_text, now_iso, read_json, write_json
from tradingcodex_service.application.operator_authority import (
    EXTERNAL_MCP_IMPORT_CODEX,
    OperatorAuthority,
    OperatorServiceAuthority,
    consume_operator_authority,
    consume_service_operator_authority,
    external_mcp_codex_import_resource,
)
from tradingcodex_service.application.runtime import ensure_runtime_database, tradingcodex_home, workspace_context_payload
from tradingcodex_service.application.runtime_mode import get_runtime_mode_status


WORKSPACE_CUSTOMIZATION_REL = Path(".tradingcodex") / "user" / "customization.json"
GLOBAL_CUSTOMIZATION_REL = Path("preferences") / "customization.json"
CODEX_MCP_BLOCK_NAME = "TradingCodex managed Codex MCP"
SAFE_MCP_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def workspace_customization_path(workspace_root: Path | str) -> Path:
    return Path(workspace_root).expanduser().resolve() / WORKSPACE_CUSTOMIZATION_REL


def global_customization_path() -> Path:
    return tradingcodex_home() / GLOBAL_CUSTOMIZATION_REL


def read_customization_settings(workspace_root: Path | str) -> dict[str, Any]:
    global_settings = _read_settings(global_customization_path())
    workspace_settings = _read_settings(workspace_customization_path(workspace_root))
    merged = _merge_settings(global_settings, workspace_settings)
    merged["paths"] = {
        "global": str(global_customization_path()),
        "workspace": str(workspace_customization_path(workspace_root)),
    }
    return merged


def update_customization_settings(workspace_root: Path | str, updates: dict[str, Any], *, scope: str = "workspace") -> dict[str, Any]:
    path = global_customization_path() if scope == "global" else workspace_customization_path(workspace_root)
    current = _read_settings(path)
    current = _merge_settings(current, updates)
    current["version"] = 1
    current["updated_at"] = now_iso()
    write_json(path, current)
    return read_customization_settings(workspace_root)


def discover_codex_mcp_servers(
    workspace_root: Path | str,
    *,
    include_global: bool = True,
    record: bool = False,
    include_launch_details: bool = False,
) -> dict[str, Any]:
    root = Path(workspace_root).expanduser().resolve()
    records: list[dict[str, Any]] = []
    for source, path in _codex_config_paths(root, include_global=include_global):
        records.extend(_servers_from_config(source, path))
    if record:
        update_customization_settings(root, {"codex_config": {"last_discovered_at": now_iso()}}, scope="workspace")
    visible_records = records if include_launch_details else [
        _public_codex_mcp_server_record(record) for record in records
    ]
    return {
        "status": "discovered",
        "servers": visible_records,
        "count": len(visible_records),
        "workspace_context": workspace_context_payload(root),
        "settings": read_customization_settings(root),
    }


def write_codex_mcp_server_config(
    workspace_root: Path | str,
    *,
    name: str,
    scope: str = "workspace",
    transport: str = "stdio",
    command: str = "",
    args: list[str] | None = None,
    url: str = "",
    env_keys: list[str] | None = None,
    credential_ref: str = "",
    dry_run: bool = False,
    full_access_detected: bool = False,
) -> dict[str, Any]:
    root = Path(workspace_root).expanduser().resolve()
    # Deprecated compatibility keyword. Codex filesystem permission is
    # advisory here; agent-origin mutation is admitted by the turn hook, while
    # this function also remains an explicit human/operator CLI surface.
    del full_access_detected
    config_path = _config_path_for_scope(root, scope)
    server_name = _validate_mcp_name(name)
    table = codex_mcp_server_table(server_name, transport=transport, command=command, args=args or [], url=url, env_keys=env_keys or [])
    existing = config_path.read_text(encoding="utf-8") if config_path.exists() else ""
    _assert_no_unmanaged_mcp_conflict(existing, server_name)
    updated = upsert_managed_mcp_server(existing, server_name, table)
    result = {
        "status": "dry_run" if dry_run else "written",
        "server_name": server_name,
        "scope": scope,
        "config_path": str(config_path),
        "backup_path": "",
        "managed_block": CODEX_MCP_BLOCK_NAME,
        "credential_ref": credential_ref,
        "required_env_keys": sorted(set(env_keys or [])),
    }
    if dry_run:
        result["preview"] = table
        return result
    backup_path = backup_file(config_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(config_path, updated)
    update_customization_settings(
        root,
        {
            "codex_config": {
                "preferred_scope": scope,
                "managed_servers": {
                    server_name: {
                        "scope": scope,
                        "config_path": str(config_path),
                        "credential_ref": credential_ref,
                        "required_env_keys": sorted(set(env_keys or [])),
                        "updated_at": now_iso(),
                    }
                },
            }
        },
        scope="workspace",
    )
    result["backup_path"] = str(backup_path) if backup_path else ""
    return result


def import_codex_mcp_server(
    workspace_root: Path | str,
    *,
    name: str,
    source: str = "workspace",
    operator_authority: OperatorAuthority | None = None,
) -> dict[str, Any]:
    """Import one Codex MCP entry after explicit operator confirmation.

    This is the user-facing service boundary. It consumes the CLI capability
    and passes only a sealed second-stage capability into the trusted
    composition helper that mutates the External MCP registry.
    """

    root = Path(workspace_root).expanduser().resolve()
    canonical_name = _validate_mcp_name(name)
    canonical_source = str(source or "workspace").strip().lower()
    if canonical_source not in {"workspace", "global", "any"}:
        raise ValueError("source must be workspace, global, or any")
    resource = external_mcp_codex_import_resource(canonical_name, canonical_source)
    service_authority = consume_operator_authority(
        operator_authority,
        root,
        action=EXTERNAL_MCP_IMPORT_CODEX,
        resource=resource,
    )
    return _import_codex_mcp_server_from_service(
        root,
        name=canonical_name,
        source=canonical_source,
        operator_service_authority=service_authority,
    )


def _import_codex_mcp_server_from_service(
    workspace_root: Path | str,
    *,
    name: str,
    source: str,
    operator_service_authority: OperatorServiceAuthority,
) -> dict[str, Any]:
    """Trusted aggregate mutation; accepts only a sealed service capability."""

    from apps.mcp.services import create_or_update_router, serialize_external_mcp_router

    root = Path(workspace_root).expanduser().resolve()
    if not isinstance(operator_service_authority, OperatorServiceAuthority):
        raise PermissionError("a sealed operator service authority is required")
    consume_service_operator_authority(
        operator_service_authority,
        root,
        action=EXTERNAL_MCP_IMPORT_CODEX,
        resource=external_mcp_codex_import_resource(name, source),
    )
    ensure_runtime_database(root)
    discovery = discover_codex_mcp_servers(
        root,
        include_global=True,
        record=False,
        include_launch_details=True,
    )
    record = next(
        (
            item
            for item in discovery["servers"]
            if item.get("name") == name and (source in {"", "any"} or item.get("source") == source)
        ),
        None,
    )
    if not record:
        raise ValueError(f"Codex MCP server not found: {source}:{name}")
    router = create_or_update_router(
        name=str(record["name"]),
        label=str(record.get("name") or ""),
        transport=str(record.get("transport") or ("http" if record.get("url") else "stdio")),
        command=str(record.get("command") or ""),
        args=[str(item) for item in record.get("args") or []],
        env={},
        url=str(record.get("url") or ""),
        credential_ref="",
        enabled=False,
        actor="local-operator",
    )
    return {
        "status": "imported",
        "source": record["source"],
        "server": record,
        "connection": serialize_external_mcp_router(router, include_tools=True),
        "note": "Imported disabled. Enable, check, discover, and review through External MCP Gate.",
    }


def build_customization_status(workspace_root: Path | str, *, full_access_detected: bool = False) -> dict[str, Any]:
    root = Path(workspace_root).expanduser().resolve()
    settings = read_customization_settings(root)
    discovery = discover_codex_mcp_servers(root, include_global=False, record=False)
    return {
        "authorization_contract": {
            "status": "exact_turn_required",
            "authority": "user_prompt_submit_hook",
            "exact_first_line": "$tcx-build",
            "root_native_turn_only": True,
            "persistent_mode": False,
            "active": False,
            "permission_is_advisory": True,
            "full_access_detected": bool(full_access_detected),
        },
        "managed_skill_authorization": {
            "status": "exact_capability_turn_required",
            "exact_first_lines": {
                "brain": "$tcx-brain",
                "strategy": "$tcx-strategy",
            },
            "recommended_profile": "trading-research",
            "lifecycle_transport": "proof_protected_mcp",
            "runtime_filesystem_access": False,
            "cross_scope": False,
            "build_wrapper_allowed": False,
        },
        # Compatibility projection for older `tcx build status --json`
        # consumers. It is inert and always has build_enabled=false.
        "mode_status": get_runtime_mode_status(root, full_access_detected=full_access_detected),
        "settings": settings,
        "codex_mcp": discovery,
        "workspace_context": workspace_context_payload(root),
    }


def codex_mcp_server_table(
    name: str,
    *,
    transport: str = "stdio",
    command: str = "",
    args: list[str] | None = None,
    url: str = "",
    env_keys: list[str] | None = None,
) -> str:
    server_name = _validate_mcp_name(name)
    lines = [f"[mcp_servers.{server_name}]"]
    transport = (transport or "stdio").replace("_", "-")
    if transport:
        lines.append(f"transport = {_toml_string(transport)}")
    if command:
        lines.append(f"command = {_toml_string(command)}")
    if args:
        lines.append("args = [" + ", ".join(_toml_string(str(item)) for item in args) + "]")
    if url:
        lines.append(f"url = {_toml_string(url)}")
    keys = sorted(set(env_keys or []))
    if keys:
        lines.append("# required_env_keys = [" + ", ".join(_toml_string(key) for key in keys) + "]")
    lines.extend(["enabled = true", 'default_tools_approval_mode = "prompt"', ""])
    return "\n".join(lines)


def upsert_managed_mcp_server(existing: str, server_name: str, table: str) -> str:
    start = f"# BEGIN {CODEX_MCP_BLOCK_NAME}"
    end = f"# END {CODEX_MCP_BLOCK_NAME}"
    content = ""
    if start in existing and end in existing:
        content = existing.split(start, 1)[1].split(end, 1)[0]
    content = _remove_mcp_server_table(content, server_name).strip()
    next_content = (content + "\n\n" if content else "") + table.rstrip()
    return replace_managed_block(existing, f"{start}\n{next_content}\n{end}\n", CODEX_MCP_BLOCK_NAME)


def replace_managed_block(existing: str, block: str, name: str) -> str:
    start = f"# BEGIN {name}"
    end = f"# END {name}"
    if start in existing and end in existing:
        before, rest = existing.split(start, 1)
        _, after = rest.split(end, 1)
        return before.rstrip() + "\n\n" + block.rstrip() + "\n" + after
    prefix = existing.rstrip() + "\n\n" if existing.strip() else ""
    return prefix + block


def _remove_mcp_server_table(text: str, server_name: str) -> str:
    pattern = re.compile(
        rf"(?ms)^\[mcp_servers\.{re.escape(server_name)}\]\n.*?(?=^\[mcp_servers\.|\Z)"
    )
    return pattern.sub("", text).strip()


def backup_file(path: Path) -> Path | None:
    if not path.exists():
        return None
    backup_dir = tradingcodex_home() / "backups" / "codex-config"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / f"{now_iso().replace(':', '').replace('-', '')}-{path.name}"
    shutil.copy2(path, backup_path)
    return backup_path


def _codex_config_paths(root: Path, *, include_global: bool) -> list[tuple[str, Path]]:
    paths = [("workspace", root / ".codex" / "config.toml")]
    if include_global:
        paths.append(("global", Path(os.environ.get("CODEX_HOME", "~/.codex")).expanduser().resolve() / "config.toml"))
    return paths


def _servers_from_config(source: str, path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    try:
        data = tomllib.loads(text) if text else {}
    except Exception as exc:
        return [{"source": source, "config_path": str(path), "status": "parse_error", "error": str(exc)}]
    servers = data.get("mcp_servers") if isinstance(data, dict) else {}
    if not isinstance(servers, dict):
        return []
    records: list[dict[str, Any]] = []
    for name, config in sorted(servers.items()):
        if not isinstance(config, dict):
            continue
        env = config.get("env") if isinstance(config.get("env"), dict) else {}
        records.append(
            {
                "source": source,
                "config_path": str(path),
                "status": "ok",
                "name": str(name),
                "transport": str(config.get("transport") or ("http" if config.get("url") else "stdio")),
                "command": str(config.get("command") or ""),
                "args": [str(item) for item in config.get("args") or []] if isinstance(config.get("args"), list) else [],
                "url": str(config.get("url") or ""),
                "enabled": bool(config.get("enabled", True)),
                "env_keys": sorted(str(key) for key in env),
                "enabled_tools": [str(item) for item in config.get("enabled_tools") or []] if isinstance(config.get("enabled_tools"), list) else [],
                "approval_mode": str(config.get("default_tools_approval_mode") or ""),
                "managed": _managed_block_contains(text, str(name)),
            }
        )
    return records


def _public_codex_mcp_server_record(record: dict[str, Any]) -> dict[str, Any]:
    """Project discovery without launch values that may embed credentials."""

    if record.get("status") == "parse_error":
        return {
            "source": str(record.get("source") or ""),
            "config_path": ".codex/config.toml" if record.get("source") == "workspace" else "<global-codex-config>",
            "status": "parse_error",
            "error": str(record.get("error") or "configuration could not be parsed"),
        }
    return {
        "source": str(record.get("source") or ""),
        "config_path": ".codex/config.toml" if record.get("source") == "workspace" else "<global-codex-config>",
        "status": str(record.get("status") or ""),
        "name": str(record.get("name") or ""),
        "transport": str(record.get("transport") or ""),
        "enabled": bool(record.get("enabled")),
        "env_keys": list(record.get("env_keys") or []),
        "enabled_tools": list(record.get("enabled_tools") or []),
        "approval_mode": str(record.get("approval_mode") or ""),
        "managed": bool(record.get("managed")),
        "has_command": bool(record.get("command")),
        "arg_count": len(record.get("args") or []),
        "has_url": bool(record.get("url")),
    }


def _managed_block_contains(text: str, server_name: str) -> bool:
    start = f"# BEGIN {CODEX_MCP_BLOCK_NAME}"
    end = f"# END {CODEX_MCP_BLOCK_NAME}"
    if start not in text or end not in text:
        return False
    block = text.split(start, 1)[1].split(end, 1)[0]
    return f"[mcp_servers.{server_name}]" in block


def _assert_no_unmanaged_mcp_conflict(existing: str, server_name: str) -> None:
    if f"[mcp_servers.{server_name}]" not in existing:
        return
    if _managed_block_contains(existing, server_name):
        return
    raise ValueError(f"Codex MCP server already exists outside TradingCodex managed block: {server_name}")


def _config_path_for_scope(root: Path, scope: str) -> Path:
    if scope == "global":
        return Path(os.environ.get("CODEX_HOME", "~/.codex")).expanduser().resolve() / "config.toml"
    if scope in {"workspace", ""}:
        return root / ".codex" / "config.toml"
    raise ValueError("scope must be workspace or global")


def _validate_mcp_name(name: str) -> str:
    text = str(name or "").strip()
    if not SAFE_MCP_NAME_RE.match(text):
        raise ValueError("MCP server name must use only letters, numbers, hyphen, or underscore")
    return text


def _read_settings(path: Path) -> dict[str, Any]:
    value = read_json(path, {})
    return value if isinstance(value, dict) else {}


def _merge_settings(base: dict[str, Any], updates: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_settings(dict(merged[key]), value)
        else:
            merged[key] = value
    return merged


def _toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)

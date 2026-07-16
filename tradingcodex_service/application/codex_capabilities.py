from __future__ import annotations

import json
import os
import shutil
import subprocess
import tomllib
from pathlib import Path
from typing import Any

from tradingcodex_service.application.common import now_iso


CODEX_COMMAND_TIMEOUT_SECONDS = 5
RESERVED_SKILL_PREFIX = "tcx-"
RESERVED_MCP_NAMES = {"tradingcodex", "tradingcodex-home"}


def list_codex_capabilities(workspace_root: Path | str) -> dict[str, Any]:
    """Return a secret-free, read-only inventory of Codex-native capabilities."""

    root = Path(workspace_root).expanduser().resolve()
    warnings: list[str] = []
    capabilities: list[dict[str, Any]] = []

    mcp_payload = _run_codex_json(("mcp", "list", "--json"), warnings, "MCP")
    if mcp_payload is not None:
        capabilities.extend(_mcp_capabilities(mcp_payload))

    plugin_payload = _run_codex_json(("plugin", "list", "--json"), warnings, "plugin")
    if plugin_payload is not None:
        capabilities.extend(_plugin_capabilities(plugin_payload, warnings))

    capabilities.extend(_standalone_skill_capabilities(root, warnings))
    capabilities = _deduplicate_capabilities(capabilities)
    capabilities.sort(key=lambda item: (item["kind"], item["scope"], item["id"]))

    command_failures = sum(1 for warning in warnings if warning.startswith("Codex "))
    if capabilities:
        status = "partial" if warnings else "complete"
    else:
        status = "unavailable" if command_failures else "complete"
    return {
        "status": status,
        "generated_at": now_iso(),
        "capabilities": capabilities,
        "warnings": warnings,
    }


def _run_codex_json(argv: tuple[str, ...], warnings: list[str], label: str) -> Any | None:
    executable = shutil.which("codex")
    if not executable:
        warnings.append(f"Codex {label} inventory is unavailable because the Codex CLI was not found")
        return None
    try:
        completed = subprocess.run(
            [executable, *argv],
            check=False,
            capture_output=True,
            text=True,
            timeout=CODEX_COMMAND_TIMEOUT_SECONDS,
            stdin=subprocess.DEVNULL,
            env=_inventory_environment(),
        )
    except subprocess.TimeoutExpired:
        warnings.append(f"Codex {label} inventory timed out")
        return None
    except OSError:
        warnings.append(f"Codex {label} inventory could not start")
        return None
    if completed.returncode != 0:
        warnings.append(f"Codex {label} inventory returned an error")
        return None
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError:
        warnings.append(f"Codex {label} inventory returned invalid JSON")
        return None


def _inventory_environment() -> dict[str, str]:
    allowed = {
        "CODEX_HOME",
        "HOME",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "LOCALAPPDATA",
        "PATH",
        "SYSTEMROOT",
        "TEMP",
        "TMP",
        "USERPROFILE",
        "WINDIR",
    }
    return {key: value for key, value in os.environ.items() if key in allowed}


def _mcp_capabilities(payload: Any) -> list[dict[str, Any]]:
    items = payload if isinstance(payload, list) else payload.get("servers", []) if isinstance(payload, dict) else []
    records: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("serverName") or item.get("id") or "").strip()
        if not name or name in RESERVED_MCP_NAMES:
            continue
        enabled = item.get("enabled") is not False
        records.append(_capability("mcp", name, name, _scope(item), "codex", enabled))
    return records


def _plugin_capabilities(payload: Any, warnings: list[str]) -> list[dict[str, Any]]:
    items = payload.get("installed", []) if isinstance(payload, dict) else payload if isinstance(payload, list) else []
    records: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict) or item.get("installed") is False:
            continue
        plugin_id = str(item.get("pluginId") or item.get("id") or item.get("name") or "").strip()
        if not plugin_id:
            continue
        enabled = item.get("enabled") is not False
        label = str(item.get("name") or plugin_id)
        records.append(_capability("plugin", plugin_id, label, "plugin", "codex", enabled))
        source = item.get("source") if isinstance(item.get("source"), dict) else {}
        source_path = source.get("path")
        if not isinstance(source_path, str) or not source_path:
            continue
        manifest_path = Path(source_path).expanduser() / ".codex-plugin" / "plugin.json"
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            warnings.append(f"Installed plugin metadata is unavailable for {plugin_id}")
            continue
        if not isinstance(manifest, dict):
            warnings.append(f"Installed plugin metadata is invalid for {plugin_id}")
            continue
        records.extend(_plugin_components(manifest_path.parent.parent, manifest, plugin_id, enabled, warnings))
    return records


def _plugin_components(
    plugin_root: Path,
    manifest: dict[str, Any],
    plugin_id: str,
    enabled: bool,
    warnings: list[str],
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    skills_path = _manifest_path(plugin_root, manifest.get("skills"))
    if skills_path is not None:
        if skills_path.is_dir():
            for skill_file in sorted(skills_path.glob("*/SKILL.md")):
                name = skill_file.parent.name
                if name:
                    records.append(_capability("skill", f"{plugin_id}:{name}", name, "plugin", "plugin", enabled, plugin_id))
        elif skills_path.is_file() and skills_path.name == "SKILL.md":
            name = _skill_name(skills_path)
            if name:
                records.append(_capability("skill", f"{plugin_id}:{name}", name, "plugin", "plugin", enabled, plugin_id))

    for field, kind in (("mcpServers", "mcp"), ("apps", "app"), ("hooks", "hook")):
        component_path = _manifest_path(plugin_root, manifest.get(field))
        if component_path is None:
            continue
        label = component_path.stem.lstrip(".") or kind
        records.append(_capability(kind, f"{plugin_id}:{kind}", label, "plugin", "plugin", enabled, plugin_id))
    hooks_dir = plugin_root / "hooks"
    if hooks_dir.is_dir():
        hook_files = sorted(path for path in hooks_dir.iterdir() if path.is_file())
        for hook_file in hook_files:
            records.append(
                _capability(
                    "hook",
                    f"{plugin_id}:hook:{hook_file.stem}",
                    hook_file.stem,
                    "plugin",
                    "plugin",
                    enabled,
                    plugin_id,
                )
            )
    return records


def _manifest_path(plugin_root: Path, value: Any) -> Path | None:
    if not isinstance(value, str) or not value.strip():
        return None
    candidate = (plugin_root / value).resolve()
    try:
        candidate.relative_to(plugin_root.resolve())
    except ValueError:
        return None
    return candidate


def _standalone_skill_capabilities(root: Path, warnings: list[str]) -> list[dict[str, Any]]:
    home = Path(os.environ.get("HOME") or Path.home()).expanduser()
    locations = [
        ("repo", root / ".agents" / "skills"),
        ("user", home / ".agents" / "skills"),
        ("admin", Path("/etc/codex/skills")),
    ]
    enabled_by_path = _skill_enabled_state(root, warnings)
    records: list[dict[str, Any]] = []
    for scope, directory in locations:
        if not directory.is_dir():
            continue
        for skill_file in sorted(directory.glob("*/SKILL.md")):
            name = skill_file.parent.name
            if not name or name.startswith(RESERVED_SKILL_PREFIX):
                continue
            enabled = enabled_by_path.get(skill_file.resolve(), True)
            records.append(_capability("skill", f"{scope}:{name}", name, scope, scope, enabled))
    return records


def _skill_enabled_state(root: Path, warnings: list[str]) -> dict[Path, bool]:
    result: dict[Path, bool] = {}
    codex_home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex").expanduser()
    for config_path in (codex_home / "config.toml", root / ".codex" / "config.toml"):
        if not config_path.is_file():
            continue
        try:
            config = tomllib.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError):
            warnings.append(f"Codex skill configuration is invalid at {_config_scope(config_path, root, codex_home)} scope")
            continue
        skills = config.get("skills") if isinstance(config.get("skills"), dict) else {}
        entries = skills.get("config") if isinstance(skills.get("config"), list) else []
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
                continue
            path = Path(entry["path"]).expanduser()
            if not path.is_absolute():
                path = config_path.parent / path
            result[path.resolve()] = entry.get("enabled") is not False
    return result


def _scope(item: dict[str, Any]) -> str:
    for key in ("scope", "source", "configScope"):
        value = item.get(key)
        if isinstance(value, str) and value:
            return value
    return "codex"


def _config_scope(path: Path, root: Path, codex_home: Path) -> str:
    if path == root / ".codex" / "config.toml":
        return "repo"
    if path == codex_home / "config.toml":
        return "user"
    return "codex"


def _capability(
    kind: str,
    capability_id: str,
    label: str,
    scope: str,
    origin: str,
    enabled: bool,
    parent_plugin: str = "",
) -> dict[str, Any]:
    return {
        "kind": kind,
        "id": capability_id,
        "label": label,
        "scope": scope,
        "origin": origin,
        "enabled": bool(enabled),
        "availability": "available" if enabled else "disabled",
        "parent_plugin": parent_plugin,
    }


def _deduplicate_capabilities(capabilities: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: dict[tuple[str, str, str], dict[str, Any]] = {}
    for capability in capabilities:
        key = (capability["kind"], capability["scope"], capability["id"])
        result[key] = capability
    return list(result.values())

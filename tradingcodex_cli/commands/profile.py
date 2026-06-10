from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tradingcodex_cli.commands.utils import print_json
from tradingcodex_service.domain import (
    active_profile_for_workspace,
    default_active_profile,
    ensure_workspace_manifest,
    normalize_active_profile,
    set_active_profile_for_workspace,
)
from tradingcodex_service.application.common import sanitize_id


PROFILES_REL = ".tradingcodex/profiles.json"


def profile(root: Path, argv: list[str]) -> None:
    sub = argv[0] if argv else "status"
    args = argv[1:]
    if sub == "status":
        manifest = ensure_workspace_manifest(root)
        print_json({
            "status": "ok",
            "workspace_id": manifest["workspace_id"],
            "active_profile": manifest["active_profile"],
            "execution_mode": manifest["execution_mode"],
        })
        return
    if sub == "list":
        active = active_profile_for_workspace(root)
        print_json({
            "active_profile_id": active["profile_id"],
            "profiles": [
                {**item, "active": item["profile_id"] == active["profile_id"]}
                for item in _list_profiles(root)
            ],
        })
        return
    if sub == "create":
        profile_id = args[0] if args else ""
        if not profile_id:
            raise ValueError("Usage: tcx profile create <profile-id>")
        created = _profile_from_id(profile_id)
        registry = _read_profiles(root)
        registry[created["profile_id"]] = created
        _write_profiles(root, registry)
        print_json({"status": "created", "profile": created})
        return
    if sub == "select":
        profile_id = args[0] if args else ""
        if not profile_id:
            raise ValueError("Usage: tcx profile select <profile-id>")
        registry = _read_profiles(root)
        if profile_id in {"default", "default-paper", "shared"}:
            selected = default_active_profile()
        else:
            selected = registry.get(sanitize_id(profile_id))
            if selected is None:
                raise ValueError(f"unknown profile: {profile_id}. Run `tcx profile create {profile_id}` first.")
        manifest = set_active_profile_for_workspace(root, selected)
        print_json({"status": "selected", "workspace_id": manifest["workspace_id"], "active_profile": manifest["active_profile"]})
        return
    raise ValueError("Usage: tcx profile status|list|create|select")


def _profile_from_id(raw_id: str) -> dict[str, Any]:
    profile_id = sanitize_id(raw_id)
    return normalize_active_profile({
        "profile_id": profile_id,
        "portfolio_id": profile_id,
        "account_id": "local-paper",
        "strategy_id": "default-strategy",
        "label": profile_id,
        "shared": False,
    })


def _profiles_path(root: Path) -> Path:
    return root / PROFILES_REL


def _read_profiles(root: Path) -> dict[str, dict[str, Any]]:
    try:
        raw = json.loads(_profiles_path(root).read_text(encoding="utf-8"))
    except Exception:
        raw = {}
    profiles = raw.get("profiles") if isinstance(raw, dict) else {}
    result: dict[str, dict[str, Any]] = {}
    if isinstance(profiles, dict):
        for key, value in profiles.items():
            if isinstance(value, dict):
                normalized = normalize_active_profile(value)
                result[normalized["profile_id"] or sanitize_id(key)] = normalized
    return result


def _write_profiles(root: Path, profiles: dict[str, dict[str, Any]]) -> None:
    path = _profiles_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"profiles": profiles}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _list_profiles(root: Path) -> list[dict[str, Any]]:
    profiles = {default_active_profile()["profile_id"]: default_active_profile()}
    profiles.update(_read_profiles(root))
    return [profiles[key] for key in sorted(profiles)]

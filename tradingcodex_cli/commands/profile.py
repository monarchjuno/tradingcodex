from __future__ import annotations

from pathlib import Path
from typing import Any

from tradingcodex_cli.commands.utils import _option_value, print_json
from tradingcodex_service.application.runtime import (
    active_profile_for_workspace,
    default_active_profile,
    ensure_workspace_manifest,
    normalize_active_profile,
    read_workspace_profiles,
    save_active_profile_for_workspace,
    set_active_profile_for_workspace,
    write_workspace_profiles,
)
from tradingcodex_service.application.common import sanitize_id


INVESTOR_PROFILE_OPTIONS = {
    "--objective": "investment_objective",
    "--horizon": "time_horizon",
    "--risk-tolerance": "risk_tolerance_and_loss_capacity",
    "--liquidity": "liquidity_needs",
    "--holdings": "current_holdings_and_concentrations",
    "--constraints": "constraints",
}


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
                for item in _workspace_profiles(root)
            ],
        })
        return
    if sub == "create":
        profile_id = args[0] if args else ""
        if not profile_id:
            raise ValueError("Usage: tcx profile create <profile-id>")
        created = _profile_from_id(profile_id)
        registry = read_workspace_profiles(root)
        registry[created["profile_id"]] = created
        write_workspace_profiles(root, registry)
        print_json({"status": "created", "profile": created})
        return
    if sub == "select":
        profile_id = args[0] if args else ""
        if not profile_id:
            raise ValueError("Usage: tcx profile select <profile-id>")
        registry = read_workspace_profiles(root)
        if profile_id in {"default", "default-paper", "shared"}:
            selected = registry.get(default_active_profile()["profile_id"], default_active_profile())
        else:
            selected = registry.get(sanitize_id(profile_id))
            if selected is None:
                raise ValueError(f"unknown profile: {profile_id}. Run `tcx profile create {profile_id}` first.")
        manifest = set_active_profile_for_workspace(root, selected)
        print_json({"status": "selected", "workspace_id": manifest["workspace_id"], "active_profile": manifest["active_profile"]})
        return
    if sub == "update":
        updated = _update_active_profile(root, args)
        print_json({"status": "updated", "active_profile": updated})
        return
    raise ValueError("Usage: tcx profile status|list|create|select|update")


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


def _workspace_profiles(root: Path) -> list[dict[str, Any]]:
    profiles = {default_active_profile()["profile_id"]: default_active_profile()}
    profiles.update(read_workspace_profiles(root))
    return [profiles[key] for key in sorted(profiles)]


def _update_active_profile(root: Path, args: list[str]) -> dict[str, Any]:
    if "--help" in args or not args:
        raise ValueError(
            "Usage: tcx profile update [--label text] [--objective text] [--horizon text] "
            "[--risk-tolerance text] [--liquidity text] [--holdings text] [--constraints text]"
        )
    active = active_profile_for_workspace(root)
    if _option_value(args, "--label"):
        active["label"] = str(_option_value(args, "--label"))
    investor_profile = dict(active.get("investor_profile") or {})
    for option, field in INVESTOR_PROFILE_OPTIONS.items():
        value = _option_value(args, option)
        if value is not None:
            investor_profile[field] = value
    active["investor_profile"] = {key: value for key, value in investor_profile.items() if value not in (None, "")}
    manifest = save_active_profile_for_workspace(root, active)
    return manifest["active_profile"]

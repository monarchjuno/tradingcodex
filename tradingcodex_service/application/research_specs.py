from __future__ import annotations

import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tradingcodex_service.application.common import (
    exclusive_file_lock,
    file_hash,
    now_iso,
    safe_workspace_path,
    sanitize_id,
)
from tradingcodex_service.application.research_objects import (
    legacy_content_hash as stable_hash,
    read_regular_json,
    research_object_path,
    store_immutable_hashed_json,
    verify_hashed_json,
)
from tradingcodex_service.application.runtime import workspace_context_payload
from tradingcodex_service.application.source_snapshots import validate_source_snapshot

SPEC_ROOT = Path("trading/research/specs")
REPLAY_ROOT = Path("trading/research/replay-manifests")
EXPERIMENT_ROOT = Path("trading/research/experiments")
GENERAL_EVIDENCE_PROFILE = "general_evidence_v1"
EVENT_RESEARCH_PROFILE = "event_research_v1"
QUANT_SIGNAL_PROFILE = "quant_signal_v1"
LISTED_EQUITY_FCFF_DCF_PROFILE = "listed_equity_fcff_dcf_v1"
RESEARCH_SPEC_SCHEMA_VERSION = 1
REPLAY_MANIFEST_SCHEMA_VERSION = 1
EXPERIMENT_RUN_SCHEMA_VERSION = 1
BUNDLED_METHOD_PROFILES = {
    GENERAL_EVIDENCE_PROFILE,
    EVENT_RESEARCH_PROFILE,
    QUANT_SIGNAL_PROFILE,
    LISTED_EQUITY_FCFF_DCF_PROFILE,
}
EVIDENCE_LANES = {
    "historical_replay",
    "historical_holdout",
    "live_forward",
}
PROFILE_SPECIFIC_FIELDS = {
    GENERAL_EVIDENCE_PROFILE: set(),
    EVENT_RESEARCH_PROFILE: set(),
    QUANT_SIGNAL_PROFILE: {
        "benchmark",
        "holding_period",
        "rebalance_rule",
        "signal_definition",
        "parameter_trial_budget",
        "cost_assumptions",
        "capacity_assumptions",
    },
    LISTED_EQUITY_FCFF_DCF_PROFILE: {
        "instrument",
        "driver_tree",
        "base_rate_cohort",
        "implied_expectations_plan",
        "scenario_plan",
        "method_reconciliation_plan",
        "independent_review_plan",
    },
}
RESEARCH_TYPE_PROFILES = {
    "general": GENERAL_EVIDENCE_PROFILE,
    "general_evidence": GENERAL_EVIDENCE_PROFILE,
    "event": EVENT_RESEARCH_PROFILE,
    "event_research": EVENT_RESEARCH_PROFILE,
    "quantitative": QUANT_SIGNAL_PROFILE,
    "quant_signal": QUANT_SIGNAL_PROFILE,
    "listed_equity_valuation": LISTED_EQUITY_FCFF_DCF_PROFILE,
}
QUANT_CONCLUSIONS = {
    "keep_researching",
    "conditionally_promising",
    "likely_overfit",
    "implementation_weak",
    "reject",
}
CHECK_STATUSES = {"pass", "fail", "not_applicable"}
QUANT_REQUIRED_VALIDATION_CHECKS = (
    "point_in_time",
    "survivorship_bias",
    "look_ahead",
    "data_snooping",
    "out_of_sample",
    "walk_forward",
    "parameter_stability",
    "costs",
    "capacity",
    "regime_sensitivity",
    "attribution",
)
def create_research_spec(workspace_root: Path | str, args: dict[str, Any]) -> dict[str, Any]:
    root = Path(workspace_root).expanduser().resolve()
    spec_id = sanitize_id(args.get("spec_id") or f"spec-{stable_hash(args)[:16]}")
    knowledge_cutoff = _iso(args.get("knowledge_cutoff"), "knowledge_cutoff")
    system_recorded_at = now_iso()
    evidence_lane = _evidence_lane(args.get("evidence_lane") or "live_forward")
    method_profile = _method_profile(args)
    _validate_explicit_method_profile(args, method_profile)
    parent_spec_ref = _holdout_parent_ref(root, args, evidence_lane, knowledge_cutoff, spec_id)
    spec = {
        "schema_version": RESEARCH_SPEC_SCHEMA_VERSION,
        "artifact_type": "research_spec",
        "spec_id": spec_id,
        "created_at": _iso(args.get("created_at") or now_iso(), "created_at"),
        "created_by": _required_text(args, "created_by"),
        "system_recorded_at": system_recorded_at,
        "knowledge_cutoff": knowledge_cutoff,
        "evidence_lane": evidence_lane,
        "parent_spec_ref": parent_spec_ref,
        "method_profile": method_profile,
        "hypothesis": _required_text(args, "hypothesis"),
        "economic_mechanism": _required_text(args, "economic_mechanism"),
        "research_type": _research_type(args, method_profile),
        "universe": _required_text(args, "universe"),
        "universe_membership_rule": _required_text(args, "universe_membership_rule"),
        "target": _required_text(args, "target"),
        "horizon": _required_text(args, "horizon"),
        "falsification_criteria": _required_list(args, "falsification_criteria"),
        "validation_plan": _required_dict(args, "validation_plan"),
        "resolution_rule": _required_text(args, "resolution_rule"),
        "authority": "evidence_only",
        "blocked_actions": ["order_drafting", "order_approval", "order_execution"],
    }
    if method_profile == QUANT_SIGNAL_PROFILE:
        trial_budget = int(args.get("parameter_trial_budget") or 1)
        if trial_budget < 1:
            raise ValueError("parameter_trial_budget must be at least 1")
        spec.update({
            "benchmark": _required_text(args, "benchmark"),
            "holding_period": str(args.get("holding_period") or ""),
            "rebalance_rule": str(args.get("rebalance_rule") or ""),
            "signal_definition": _required_dict(args, "signal_definition"),
            "parameter_trial_budget": trial_budget,
            "cost_assumptions": _required_dict(args, "cost_assumptions"),
            "capacity_assumptions": _required_dict(args, "capacity_assumptions"),
        })
    if method_profile == LISTED_EQUITY_FCFF_DCF_PROFILE:
        spec.update({
            "instrument": _required_text(args, "instrument"),
            "driver_tree": _required_dict(args, "driver_tree"),
            "base_rate_cohort": _validated_base_rate_cohort(args.get("base_rate_cohort"), knowledge_cutoff),
            "implied_expectations_plan": _required_dict(args, "implied_expectations_plan"),
            "scenario_plan": _validated_scenario_plan(args.get("scenario_plan"), require_causal_drivers=True),
            "method_reconciliation_plan": _required_dict(args, "method_reconciliation_plan"),
            "independent_review_plan": _required_dict(args, "independent_review_plan"),
        })
    if spec["created_at"] < knowledge_cutoff:
        raise ValueError("created_at must not be before knowledge_cutoff")
    spec["analysis_plan_hash"] = stable_hash(spec)
    path = _artifact_path(root, SPEC_ROOT, spec_id)
    artifact, status = _store_immutable(path, spec, "analysis_plan_hash", "research spec", spec_id)
    return _result(root, path, artifact, status)


def get_research_spec(workspace_root: Path | str, args: dict[str, Any]) -> dict[str, Any]:
    root = Path(workspace_root).expanduser().resolve()
    spec_id = _required_text(args, "spec_id")
    path = _artifact_path(root, SPEC_ROOT, spec_id)
    if not path.exists():
        raise ValueError(f"research spec not found: {spec_id}")
    artifact = _verified_artifact(path, "analysis_plan_hash", "research spec")
    if artifact.get("schema_version") != RESEARCH_SPEC_SCHEMA_VERSION:
        raise ValueError("unsupported research spec schema")
    return _result(root, path, artifact, "ok")


def create_replay_manifest(workspace_root: Path | str, args: dict[str, Any]) -> dict[str, Any]:
    root = Path(workspace_root).expanduser().resolve()
    spec = get_research_spec(root, {"spec_id": _required_text(args, "spec_id")})["artifact"]
    snapshot_ids = _required_list(args, "source_snapshot_ids")
    snapshots = []
    latest_recorded_at = spec["knowledge_cutoff"]
    for snapshot_id in snapshot_ids:
        path = _artifact_path(root, Path("trading/research/source-snapshots"), str(snapshot_id))
        if not path.exists():
            raise ValueError(f"source snapshot not found: {snapshot_id}")
        snapshot = validate_source_snapshot(
            _read_object(path),
            expected_snapshot_id=str(snapshot_id),
        )
        known_at = _iso(snapshot.get("known_at"), f"source snapshot {snapshot_id} known_at")
        if known_at > spec["knowledge_cutoff"]:
            raise ValueError(f"source snapshot is after knowledge cutoff: {snapshot_id}")
        for field in ("source_locator", "retrieved_at", "timezone", "schema_hash", "payload_hash", "snapshot_hash", "revision", "vintage"):
            if not str(snapshot.get(field) or "").strip():
                raise ValueError(f"source snapshot lacks point-in-time metadata {field}: {snapshot_id}")
        retrieved_at = _iso(snapshot.get("retrieved_at"), f"source snapshot {snapshot_id} retrieved_at")
        recorded_at = _iso(snapshot.get("recorded_at"), f"source snapshot {snapshot_id} recorded_at")
        semantic_times = {}
        for field in ("as_of", "observed_at", "effective_at", "published_at"):
            if str(snapshot.get(field) or "").strip():
                semantic_times[field] = _iso(snapshot[field], f"source snapshot {snapshot_id} {field}")
                if semantic_times[field] > spec["knowledge_cutoff"]:
                    raise ValueError(f"source snapshot {field} is after knowledge cutoff: {snapshot_id}")
        latest_recorded_at = max(latest_recorded_at, recorded_at)
        payload = snapshot["payload"]
        if any(key in payload for key in ("bars", "ohlc", "prices")):
            for field in ("corporate_action_policy", "price_adjustment_policy", "delisting_policy"):
                if str(snapshot.get(field) or "") in {"", "not_specified"}:
                    raise ValueError(f"market-data replay requires explicit {field}: {snapshot_id}")
            if not isinstance(snapshot.get("universe_membership"), dict) or not snapshot.get("universe_membership"):
                raise ValueError(f"market-data replay requires point-in-time universe_membership: {snapshot_id}")
        snapshots.append(
            {
                "snapshot_id": str(snapshot.get("snapshot_id") or snapshot_id),
                "content_hash": file_hash(path),
                "known_at": known_at,
                "retrieved_at": retrieved_at,
                "recorded_at": recorded_at,
                **semantic_times,
                "path": path.relative_to(root).as_posix(),
            }
        )
    manifest_seed = {
        "spec_id": spec["spec_id"],
        "analysis_plan_hash": spec["analysis_plan_hash"],
        "method_profile": _stored_method_profile(spec),
        "evidence_lane": _evidence_lane(spec.get("evidence_lane") or "live_forward"),
        "knowledge_cutoff": spec["knowledge_cutoff"],
        "snapshots": snapshots,
    }
    manifest_id = sanitize_id(args.get("manifest_id") or f"replay-{stable_hash(manifest_seed)[:16]}")
    created_at = _iso(args.get("created_at") or now_iso(), "created_at")
    if created_at < latest_recorded_at:
        raise ValueError("replay manifest created_at must not predate its source snapshots")
    manifest = {
        "schema_version": REPLAY_MANIFEST_SCHEMA_VERSION,
        "artifact_type": "replay_manifest",
        "manifest_id": manifest_id,
        **manifest_seed,
        "created_at": created_at,
        "created_by": _required_text(args, "created_by"),
        "system_recorded_at": now_iso(),
        "authority": "evidence_only",
        "blocked_actions": ["order_drafting", "order_approval", "order_execution"],
    }
    manifest["manifest_hash"] = stable_hash(manifest)
    path = _artifact_path(root, REPLAY_ROOT, manifest_id)
    artifact, status = _store_immutable(path, manifest, "manifest_hash", "replay manifest", manifest_id)
    return _result(root, path, artifact, status)


def record_experiment_run(workspace_root: Path | str, args: dict[str, Any]) -> dict[str, Any]:
    root = Path(workspace_root).expanduser().resolve()
    spec = get_research_spec(root, {"spec_id": _required_text(args, "spec_id")})["artifact"]
    manifest_id = _required_text(args, "replay_manifest_id")
    manifest_path = _artifact_path(root, REPLAY_ROOT, manifest_id)
    if not manifest_path.exists():
        raise ValueError(f"replay manifest not found: {manifest_id}")
    manifest = _verified_artifact(manifest_path, "manifest_hash", "replay manifest")
    if manifest.get("schema_version") != REPLAY_MANIFEST_SCHEMA_VERSION:
        raise ValueError("unsupported replay manifest schema")
    if manifest.get("spec_id") != spec.get("spec_id") or manifest.get("analysis_plan_hash") != spec.get("analysis_plan_hash"):
        raise ValueError("replay manifest does not match the immutable research spec")
    method_profile = _stored_method_profile(spec)
    checks = _validation_checks(args.get("checks"), method_profile)
    _verify_evidence_refs(root, checks)
    conclusion = _experiment_conclusion(args, method_profile)
    failed = sorted(key for key, value in checks.items() if value["status"] == "fail")
    if method_profile == QUANT_SIGNAL_PROFILE and conclusion == "conditionally_promising" and failed:
        raise ValueError("conditionally_promising is not allowed while validation checks fail")
    if method_profile == QUANT_SIGNAL_PROFILE and conclusion == "conditionally_promising" and not any(value["status"] == "pass" for value in checks.values()):
        raise ValueError("conditionally_promising requires at least one passed validation check")
    run_id = sanitize_id(args.get("run_id") or f"experiment-{stable_hash(args)[:16]}")
    run = {
        "schema_version": EXPERIMENT_RUN_SCHEMA_VERSION,
        "artifact_type": "experiment_run",
        "run_id": run_id,
        "spec_id": spec["spec_id"],
        "analysis_plan_hash": spec["analysis_plan_hash"],
        "method_profile": method_profile,
        "evidence_lane": _evidence_lane(spec.get("evidence_lane") or "live_forward"),
        "replay_manifest_id": manifest_id,
        "replay_manifest_hash": manifest["manifest_hash"],
        "created_at": _iso(args.get("created_at") or now_iso(), "created_at"),
        "created_by": _required_text(args, "created_by"),
        "system_recorded_at": now_iso(),
        "code_hash": _hash_text(args, "code_hash"),
        "data_hash": _hash_text(args, "data_hash"),
        "config_hash": _hash_text(args, "config_hash"),
        "model": str(args.get("model") or ""),
        "reasoning_effort": str(args.get("reasoning_effort") or ""),
        "prompt_hash": str(args.get("prompt_hash") or ""),
        "tool_profile_hash": str(args.get("tool_profile_hash") or ""),
        "splits": _required_dict(args, "splits"),
        "trial_count": int(args.get("trial_count") or 1),
        "metrics": _required_dict(args, "metrics"),
        "checks": checks,
        "conclusion": conclusion,
        "failed_checks": failed,
        "source_limitations": args.get("source_limitations") if isinstance(args.get("source_limitations"), list) else [],
        "authority": "evidence_only",
        "blocked_actions": ["order_drafting", "order_approval", "order_execution"],
    }
    if run["trial_count"] < 1:
        raise ValueError("trial_count must be at least 1")
    if method_profile == QUANT_SIGNAL_PROFILE and run["trial_count"] > int(spec["parameter_trial_budget"]):
        raise ValueError("trial_count exceeds the preregistered parameter_trial_budget")
    run["run_hash"] = stable_hash(run)
    path = _artifact_path(root, EXPERIMENT_ROOT, run_id)
    if method_profile != QUANT_SIGNAL_PROFILE:
        artifact, status = _store_immutable(path, run, "run_hash", "experiment run", run_id)
        return _result(root, path, artifact, status)
    budget_lock = root / EXPERIMENT_ROOT / f".{sanitize_id(spec['spec_id'])}.trial-budget"
    with exclusive_file_lock(budget_lock):
        used_trials = sum(
            int(existing.get("trial_count") or 0)
            for existing_path in (root / EXPERIMENT_ROOT).glob("*.json")
            if existing_path != path
            for existing in [_read_object(existing_path)]
            if existing.get("spec_id") == spec["spec_id"]
        )
        if used_trials + int(run["trial_count"]) > int(spec["parameter_trial_budget"]):
            raise ValueError("cumulative trial_count exceeds the preregistered parameter_trial_budget")
        artifact, status = _store_immutable(path, run, "run_hash", "experiment run", run_id)
    return _result(root, path, artifact, status)


def _holdout_parent_ref(
    root: Path,
    args: dict[str, Any],
    evidence_lane: str,
    knowledge_cutoff: str,
    spec_id: str,
) -> dict[str, Any]:
    parent_spec_id = str(args.get("parent_spec_id") or "").strip()
    if evidence_lane != "historical_holdout":
        if parent_spec_id:
            raise ValueError("parent_spec_id is only valid for historical_holdout")
        return {}
    if not parent_spec_id:
        raise ValueError("historical_holdout requires a preregistered parent_spec_id")
    parent = get_research_spec(root, {"spec_id": parent_spec_id})["artifact"]
    if parent.get("schema_version") != RESEARCH_SPEC_SCHEMA_VERSION or not parent.get("system_recorded_at"):
        raise ValueError("historical_holdout parent spec must use the current schema")
    if parent.get("evidence_lane") != "historical_replay":
        raise ValueError("historical_holdout parent spec must use historical_replay")
    holdout = (parent.get("validation_plan") or {}).get("holdout")
    if not isinstance(holdout, dict):
        raise ValueError("historical_holdout parent validation_plan must preregister holdout")
    expected_cutoff = _iso(holdout.get("knowledge_cutoff"), "validation_plan.holdout.knowledge_cutoff")
    if expected_cutoff != knowledge_cutoff:
        raise ValueError("historical_holdout knowledge_cutoff does not match its preregistration")
    expected_horizon = str(holdout.get("horizon") or "").strip()
    if not expected_horizon or expected_horizon != str(args.get("horizon") or "").strip():
        raise ValueError("historical_holdout horizon does not match its preregistration")
    holdout_id = str(holdout.get("holdout_id") or "").strip()
    if not holdout_id:
        raise ValueError("validation_plan.holdout.holdout_id is required")
    for path in (root / SPEC_ROOT).glob("*.json"):
        if path.stem == spec_id:
            continue
        existing = _read_object(path)
        existing_parent = existing.get("parent_spec_ref") if isinstance(existing.get("parent_spec_ref"), dict) else {}
        if existing_parent.get("spec_id") == parent_spec_id and existing_parent.get("holdout_id") == holdout_id:
            raise ValueError("preregistered historical holdout already has a child spec")
    return {
        "spec_id": parent["spec_id"],
        "analysis_plan_hash": parent["analysis_plan_hash"],
        "holdout_id": holdout_id,
        "system_recorded_at": parent["system_recorded_at"],
    }


def list_research_specs(workspace_root: Path | str) -> dict[str, Any]:
    root = Path(workspace_root).expanduser().resolve()
    records = []
    if (root / SPEC_ROOT).exists():
        for path in sorted((root / SPEC_ROOT).glob("*.json")):
            artifact = _verified_artifact(path, "analysis_plan_hash", "research spec")
            if artifact.get("schema_version") != RESEARCH_SPEC_SCHEMA_VERSION:
                raise ValueError(f"unsupported research spec schema: {path.stem}")
            records.append(artifact)
    return {
        "research_specs": records,
        "count": len(records),
        "authority": "evidence_only",
        "workspace_context": workspace_context_payload(root),
    }


def _validation_checks(value: Any, method_profile: str) -> dict[str, dict[str, Any]]:
    if not isinstance(value, dict) or not value:
        raise ValueError("checks must be a non-empty object")
    result: dict[str, dict[str, Any]] = {}
    keys = QUANT_REQUIRED_VALIDATION_CHECKS if method_profile == QUANT_SIGNAL_PROFILE else tuple(value)
    for key in keys:
        raw = value.get(key)
        if not isinstance(raw, dict):
            raise ValueError(f"checks.{key} must be an object")
        status = str(raw.get("status") or "")
        if status not in CHECK_STATUSES:
            raise ValueError(f"checks.{key}.status must be pass, fail, or not_applicable")
        reason = str(raw.get("reason") or "").strip()
        evidence_refs = raw.get("evidence_refs")
        if not reason or not isinstance(evidence_refs, list) or not evidence_refs:
            raise ValueError(f"checks.{key} requires reason and evidence_refs")
        result[key] = {"status": status, "reason": reason, "evidence_refs": evidence_refs}
    return result


def _experiment_conclusion(args: dict[str, Any], method_profile: str) -> str:
    conclusion = _required_text(args, "conclusion")
    if method_profile == QUANT_SIGNAL_PROFILE and conclusion not in QUANT_CONCLUSIONS:
        raise ValueError(f"conclusion must be one of: {', '.join(sorted(QUANT_CONCLUSIONS))}")
    return conclusion


def _method_profile(args: dict[str, Any]) -> str:
    profile = _required_text(args, "method_profile")
    if profile not in BUNDLED_METHOD_PROFILES:
        raise ValueError(f"method_profile must be one of: {', '.join(sorted(BUNDLED_METHOD_PROFILES))}")
    return profile


def _evidence_lane(value: Any) -> str:
    lane = str(value or "").strip()
    if lane not in EVIDENCE_LANES:
        raise ValueError(f"evidence_lane must be one of: {', '.join(sorted(EVIDENCE_LANES))}")
    return lane


def _validate_explicit_method_profile(args: dict[str, Any], method_profile: str) -> None:
    research_type = str(args.get("research_type") or "").strip().lower()
    mapped_profile = RESEARCH_TYPE_PROFILES.get(research_type)
    if mapped_profile and mapped_profile != method_profile:
        raise ValueError(
            f"research_type {research_type} conflicts with explicit method_profile {method_profile}"
        )
    causal_required = args.get("causal_analysis_required")
    if causal_required is not None:
        if not isinstance(causal_required, bool):
            raise ValueError("causal_analysis_required must be a boolean")
        if causal_required != (method_profile == LISTED_EQUITY_FCFF_DCF_PROFILE):
            raise ValueError(
                f"causal_analysis_required conflicts with explicit method_profile {method_profile}"
            )
    allowed = PROFILE_SPECIFIC_FIELDS[method_profile]
    profile_fields = set().union(*PROFILE_SPECIFIC_FIELDS.values())
    irrelevant = sorted(
        field for field in profile_fields - allowed
        if field in args and args[field] is not None
    )
    if irrelevant:
        raise ValueError(
            f"method_profile {method_profile} does not accept profile-specific fields: "
            + ", ".join(irrelevant)
        )


def _stored_method_profile(spec: dict[str, Any]) -> str:
    profile = str(spec.get("method_profile") or "")
    if profile not in BUNDLED_METHOD_PROFILES:
        raise ValueError("research spec method_profile is missing or unsupported")
    return profile


def _research_type(args: dict[str, Any], method_profile: str) -> str:
    if method_profile == LISTED_EQUITY_FCFF_DCF_PROFILE:
        return "listed_equity_valuation"
    if method_profile == QUANT_SIGNAL_PROFILE:
        return str(args.get("research_type") or "quantitative")
    if method_profile == EVENT_RESEARCH_PROFILE:
        return str(args.get("research_type") or "event_research")
    return str(args.get("research_type") or "general_evidence")


def _verify_evidence_refs(root: Path, checks: dict[str, dict[str, Any]]) -> None:
    allowed = (
        Path("trading/research"),
        Path("trading/reports"),
        Path("trading/forecasts"),
        Path("trading/decisions"),
    )
    for key, check in checks.items():
        normalized = []
        for index, raw in enumerate(check["evidence_refs"], start=1):
            if not isinstance(raw, dict):
                raise ValueError(f"checks.{key}.evidence_refs[{index}] must contain path and sha256")
            rel = str(raw.get("path") or "")
            expected_hash = str(raw.get("sha256") or "").lower()
            if len(expected_hash) != 64:
                raise ValueError(f"checks.{key}.evidence_refs[{index}].sha256 must be a 64-character digest")
            path = safe_workspace_path(root, rel, allowed_roots=allowed)
            actual_hash = file_hash(path)
            if actual_hash is None:
                raise ValueError(f"validation evidence does not exist: {rel}")
            if actual_hash != expected_hash:
                raise ValueError(f"validation evidence hash mismatch: {rel}")
            normalized.append({"path": rel, "sha256": actual_hash})
        check["evidence_refs"] = normalized


def _validated_base_rate_cohort(value: Any, knowledge_cutoff: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("base_rate_cohort must be an object")
    required = ("selection_rule", "as_of", "sample_size", "dispersion", "limitations")
    missing = [field for field in required if value.get(field) in (None, "", [], {})]
    if missing:
        raise ValueError(f"base_rate_cohort missing: {', '.join(missing)}")
    if int(value["sample_size"]) < 1:
        raise ValueError("base_rate_cohort.sample_size must be positive")
    result = dict(value)
    result["as_of"] = _iso(value["as_of"], "base_rate_cohort.as_of")
    if result["as_of"] > knowledge_cutoff:
        raise ValueError("base_rate_cohort.as_of must not be after knowledge_cutoff")
    return result


def _validated_scenario_plan(value: Any, *, require_causal_drivers: bool = False) -> dict[str, Any]:
    if not isinstance(value, dict) or not isinstance(value.get("scenarios"), list) or len(value["scenarios"]) < 2:
        raise ValueError("scenario_plan requires at least two scenarios")
    weights = []
    names: set[str] = set()
    for index, scenario in enumerate(value["scenarios"], start=1):
        if not isinstance(scenario, dict) or not scenario.get("name") or not isinstance(scenario.get("drivers"), dict):
            raise ValueError(f"scenario_plan.scenarios[{index}] requires name and drivers")
        name = str(scenario["name"]).strip()
        if name in names:
            raise ValueError(f"duplicate scenario_plan scenario name: {name}")
        names.add(name)
        if require_causal_drivers:
            required_drivers = {
                "revenue_growth",
                "operating_margin",
                "tax_rate",
                "sales_to_capital",
                "discount_rate",
                "terminal_growth",
            }
            if set(scenario["drivers"]) != required_drivers:
                raise ValueError(
                    f"scenario_plan.scenarios[{index}].drivers must contain exactly: "
                    + ", ".join(sorted(required_drivers))
                )
            assumptions = scenario.get("assumptions")
            if not isinstance(assumptions, list) or not assumptions:
                raise ValueError(f"scenario_plan.scenarios[{index}].assumptions must be a non-empty list")
            for driver, raw_driver in scenario["drivers"].items():
                raw_value = raw_driver.get("value") if isinstance(raw_driver, dict) else raw_driver
                try:
                    number = float(raw_value)
                except (TypeError, ValueError) as exc:
                    raise ValueError(f"scenario_plan.scenarios[{index}].drivers.{driver} must be numeric") from exc
                if not math.isfinite(number):
                    raise ValueError(f"scenario_plan.scenarios[{index}].drivers.{driver} must be finite")
        try:
            weight = float(scenario.get("weight"))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"scenario_plan.scenarios[{index}].weight must be numeric") from exc
        if not 0 <= weight <= 1:
            raise ValueError(f"scenario_plan.scenarios[{index}].weight must be between 0 and 1")
        weights.append(weight)
    if abs(sum(weights) - 1.0) > 1e-9:
        raise ValueError("scenario_plan scenario weights must sum to 1")
    return dict(value)


def _store_immutable(
    path: Path,
    artifact: dict[str, Any],
    hash_field: str,
    label: str,
    artifact_id: str,
) -> tuple[dict[str, Any], str]:
    return store_immutable_hashed_json(
        path,
        artifact,
        hash_field=hash_field,
        label=label,
        object_id=artifact_id,
        hash_function=stable_hash,
    )


def _artifact_path(root: Path, allowed_root: Path, artifact_id: str) -> Path:
    return research_object_path(root, allowed_root, artifact_id)


def _read_object(path: Path) -> dict[str, Any]:
    value = read_regular_json(path, label=f"research artifact {path.stem}")
    if value.get("schema_version") != 1:
        raise ValueError(f"research artifact uses an unsupported schema: {path}")
    return value


def _verified_artifact(path: Path, hash_field: str, label: str) -> dict[str, Any]:
    return verify_hashed_json(
        path,
        hash_field=hash_field,
        label=label,
        hash_function=stable_hash,
        schema_version=1,
    )


def _result(root: Path, path: Path, artifact: dict[str, Any], status: str) -> dict[str, Any]:
    return {
        "status": status,
        "artifact": artifact,
        "export_path": path.relative_to(root).as_posix(),
        "authority": "evidence_only",
        "workspace_context": workspace_context_payload(root),
    }


def _required_text(args: dict[str, Any], field: str) -> str:
    value = str(args.get(field) or "").strip()
    if not value:
        raise ValueError(f"{field} is required")
    return value


def _hash_text(args: dict[str, Any], field: str) -> str:
    value = _required_text(args, field)
    if len(value) != 64 or any(character not in "0123456789abcdefABCDEF" for character in value):
        raise ValueError(f"{field} must be a 64-character hexadecimal digest")
    return value.lower()


def _required_list(args: dict[str, Any], field: str) -> list[Any]:
    value = args.get(field)
    if not isinstance(value, list) or not value:
        raise ValueError(f"{field} must be a non-empty list")
    return value


def _required_dict(args: dict[str, Any], field: str) -> dict[str, Any]:
    value = args.get(field)
    if not isinstance(value, dict) or not value:
        raise ValueError(f"{field} must be a non-empty object")
    return value


def _iso(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field} is required")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO-8601 datetime") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

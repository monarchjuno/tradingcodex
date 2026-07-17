from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import re
import stat
from decimal import Decimal, InvalidOperation
from datetime import datetime, timezone
from importlib.resources import files
from pathlib import Path
from typing import Any

from tradingcodex_service.application.common import (
    atomic_write_text,
    exclusive_file_lock,
    now_iso,
    safe_workspace_path,
    sanitize_id,
    stable_hash,
)
from tradingcodex_service.application.runtime import workspace_context_payload


CALCULATION_SPEC_ROOT = Path("trading/research/calculations/specs")
CALCULATION_RUN_ROOT = Path("trading/research/calculations/runs")
CALCULATION_SPEC_SCHEMA_VERSION = 1
CALCULATION_RUN_SCHEMA_VERSION = 1
CALCULATION_SIDECAR_SCHEMA_VERSION = 1
CALCULATION_RESULT_SCHEMA_VERSION = 1
MAX_SCRIPT_BYTES = 1_000_000
MAX_INPUT_BYTES = 256 * 1024 * 1024
MAX_RESULT_BYTES = 1_000_000
DEFAULT_SEARCH_LIMIT = 20
MAX_SEARCH_LIMIT = 200
DIRECT_FILE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,180}")
SCRIPT_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,120}\.py")
HASH = re.compile(r"[0-9a-f]{64}")
RUN_STATUSES = {"succeeded", "failed", "reused"}
PRIVATE_INPUT_KINDS = {"private_ledger", "private_portfolio", "private_account"}
ALLOWED_INPUT_KINDS = {"dataset_slice", *PRIVATE_INPUT_KINDS}
FORBIDDEN_OUTPUT_SUFFIXES = (".pickle", ".pkl", ".joblib", ".pyc", ".pyo")


def prepare_calculation(
    workspace_root: Path | str,
    args: dict[str, Any],
    *,
    scratch_root: Path | str | None = None,
    runtime_manifest_path: Path | str | None = None,
) -> dict[str, Any]:
    """Seal one calculation specification and prepare its scratch sidecar.

    Exact successful fingerprint matches create a current-workflow reuse Run
    immediately and do not expose a stale result file as a new execution.
    """

    root = Path(workspace_root).expanduser().resolve()
    scratch = _scratch_root(scratch_root)
    manifest = _runtime_manifest(runtime_manifest_path)
    script_name = _script_name(args.get("script_name"))
    script_path = scratch / script_name
    script_bytes = _read_single_link_file(
        script_path,
        maximum=MAX_SCRIPT_BYTES,
        label="calculation script",
    )
    script_sha256 = hashlib.sha256(script_bytes).hexdigest()
    knowledge_cutoff = _iso(args.get("knowledge_cutoff"), "knowledge_cutoff")
    if knowledge_cutoff > _iso(now_iso(), "system time"):
        raise ValueError("knowledge_cutoff must not be in the future")
    workflow_run_id = _required_text(args, "workflow_run_id")
    created_by = str(args.get("principal_id") or args.get("created_by") or "").strip()
    if not created_by:
        raise ValueError("principal_id is required")
    parameters = _finite_object(args.get("parameters", {}), "parameters")
    output_schema = _validated_output_schema(args.get("output_schema"))
    sidecar_inputs, spec_inputs = _validated_inputs(
        root,
        scratch,
        args.get("inputs", []),
        knowledge_cutoff=knowledge_cutoff,
    )
    outputs = _validated_outputs(
        args.get("outputs", []),
        knowledge_cutoff=knowledge_cutoff,
        private_inputs_present=any(
            item.get("kind") in PRIVATE_INPUT_KINDS for item in spec_inputs
        ),
    )
    runtime_identity = _runtime_identity(manifest)
    fingerprint_seed = {
        "calculation_type": _required_text(args, "calculation_type"),
        "calculation_version": _required_text(args, "calculation_version"),
        "script_sha256": script_sha256,
        "inputs": spec_inputs,
        "parameters": parameters,
        "output_schema": output_schema,
        "outputs": outputs,
        "knowledge_cutoff": knowledge_cutoff,
        "runtime_identity": runtime_identity,
    }
    fingerprint = stable_hash(fingerprint_seed)
    spec_id = f"calc-spec-{fingerprint[:20]}"
    spec = {
        "schema_version": CALCULATION_SPEC_SCHEMA_VERSION,
        "artifact_type": "calculation_spec",
        "calculation_spec_id": spec_id,
        "fingerprint": fingerprint,
        **fingerprint_seed,
        "created_by": created_by,
        "system_recorded_at": now_iso(),
        "authority": "evidence_only",
        "blocked_actions": ["order_drafting", "order_approval", "order_execution"],
    }
    spec["spec_sha256"] = stable_hash(spec)
    spec_path = _artifact_path(root, CALCULATION_SPEC_ROOT, spec_id)
    stored_spec, spec_status = _store_immutable(
        spec_path,
        spec,
        hash_field="spec_sha256",
        id_field="calculation_spec_id",
        label="calculation spec",
        ignored_fields={"created_by"},
    )
    original = _successful_run_for_fingerprint(root, fingerprint)
    if original is not None:
        reuse = _record_reuse_run(
            root,
            spec=stored_spec,
            workflow_run_id=workflow_run_id,
            created_by=created_by,
            original=original,
        )
        return {
            "status": "reused",
            "execution_required": False,
            "calculation_spec_id": spec_id,
            "fingerprint": fingerprint,
            "calculation_run_id": reuse["artifact"]["calculation_run_id"],
            "original_run_id": original["calculation_run_id"],
            "spec_status": spec_status,
            "workspace_context": workspace_context_payload(root),
        }

    sidecar_name = f"{script_name}.tcx.json"
    result_name = f"{script_name}.tcx-result.json"
    sidecar_path = scratch / sidecar_name
    result_path = scratch / result_name
    if result_path.exists() or result_path.is_symlink():
        raise ValueError(f"calculation result file already exists: {result_name}")
    sidecar = {
        "schema_version": CALCULATION_SIDECAR_SCHEMA_VERSION,
        "mode": "prepared",
        "calculation_spec_id": spec_id,
        "fingerprint": fingerprint,
        "workflow_run_id": workflow_run_id,
        "script_name": script_name,
        "script_sha256": script_sha256,
        "runtime_lock_sha256": manifest["lock_sha256"],
        "runtime_requirements_sha256": manifest["requirements_sha256"],
        "runtime_manifest_sha256": manifest["manifest_sha256"],
        "inputs": sidecar_inputs,
        "outputs": outputs,
        "result_file": result_name,
    }
    sidecar["sidecar_sha256"] = stable_hash(sidecar)
    _store_sidecar(sidecar_path, sidecar)
    return {
        "status": "prepared",
        "execution_required": True,
        "calculation_spec_id": spec_id,
        "fingerprint": fingerprint,
        "script_name": script_name,
        "sidecar_file": sidecar_name,
        "result_file": result_name,
        "spec_status": spec_status,
        "workspace_context": workspace_context_payload(root),
    }


def record_calculation_run(
    workspace_root: Path | str,
    args: dict[str, Any],
    *,
    scratch_root: Path | str | None = None,
    runtime_manifest_path: Path | str | None = None,
) -> dict[str, Any]:
    """Verify a runner envelope and store one immutable CalculationRun."""

    root = Path(workspace_root).expanduser().resolve()
    scratch = _scratch_root(scratch_root)
    manifest = _runtime_manifest(runtime_manifest_path)
    result_name = _direct_name(args.get("result_file"), "result_file")
    if not result_name.endswith(".tcx-result.json"):
        raise ValueError("result_file must be a tcx-calc result envelope")
    result_path = scratch / result_name
    envelope = _read_json_object(
        result_path,
        maximum=MAX_RESULT_BYTES,
        label="calculation result envelope",
    )
    _validate_envelope(envelope, manifest)
    spec_id = str(envelope["calculation_spec_id"])
    requested_spec_id = str(args.get("calculation_spec_id") or spec_id)
    if requested_spec_id != spec_id:
        raise ValueError("result envelope calculation_spec_id mismatch")
    spec = _get_spec(root, spec_id)
    if envelope["fingerprint"] != spec["fingerprint"]:
        raise ValueError("result envelope fingerprint mismatch")
    workflow_run_id = _required_text(args, "workflow_run_id")
    if envelope["workflow_run_id"] != workflow_run_id:
        raise ValueError("result envelope workflow_run_id mismatch")
    _validate_prepared_execution(
        root,
        scratch,
        result_name=result_name,
        envelope=envelope,
        spec=spec,
        manifest=manifest,
    )
    created_by = str(args.get("principal_id") or args.get("created_by") or "").strip()
    if not created_by:
        raise ValueError("principal_id is required")
    output_hashes = _verify_envelope_outputs(scratch, envelope)
    derived_datasets = []
    if envelope["status"] == "succeeded":
        derived_datasets = _record_derived_datasets(
            root,
            scratch,
            spec,
            output_hashes,
            principal_id=created_by,
        )
    envelope_sha256 = hashlib.sha256(
        json.dumps(envelope, indent=2, ensure_ascii=False, sort_keys=True, allow_nan=False).encode("utf-8")
    ).hexdigest()
    run_seed = {
        "calculation_spec_id": spec_id,
        "fingerprint": spec["fingerprint"],
        "workflow_run_id": workflow_run_id,
        "status": envelope["status"],
        "envelope_sha256": envelope_sha256,
        "runtime_manifest_sha256": envelope["runtime_manifest_sha256"],
    }
    run_id = f"calc-run-{stable_hash(run_seed)[:20]}"
    result = envelope.get("result") if isinstance(envelope.get("result"), dict) else None
    run = {
        "schema_version": CALCULATION_RUN_SCHEMA_VERSION,
        "artifact_type": "calculation_run",
        "calculation_run_id": run_id,
        **run_seed,
        "original_run_id": "",
        "metrics": list(result.get("metrics", [])) if result else [],
        "diagnostics": dict(result.get("diagnostics", {})) if result else {},
        "assumptions": list(result.get("assumptions", [])) if result else [],
        "warnings": list(result.get("warnings", [])) if result else [],
        "outputs": output_hashes,
        "derived_dataset_ids": [item["dataset_id"] for item in derived_datasets],
        "derived_dataset_hashes": {
            item["dataset_id"]: item["manifest_hash"] for item in derived_datasets
        },
        "stdout": dict(envelope["stdout"]),
        "stderr": dict(envelope["stderr"]),
        "error_type": str(envelope.get("error_type") or ""),
        "created_by": created_by,
        "system_recorded_at": now_iso(),
        "authority": "evidence_only",
        "blocked_actions": ["order_drafting", "order_approval", "order_execution"],
    }
    run["run_sha256"] = stable_hash(run)
    path = _artifact_path(root, CALCULATION_RUN_ROOT, run_id)
    artifact, status = _store_immutable(
        path,
        run,
        hash_field="run_sha256",
        id_field="calculation_run_id",
        label="calculation run",
    )
    return _result(root, path, artifact, status)


def lookup_calculation_fingerprint(
    workspace_root: Path | str,
    args: dict[str, Any],
) -> dict[str, Any]:
    root = Path(workspace_root).expanduser().resolve()
    fingerprint = _hash_text(args.get("fingerprint"), "fingerprint")
    run = _successful_run_for_fingerprint(root, fingerprint)
    return {
        "found": run is not None,
        "card": _run_card(run) if run else None,
        "workspace_context": workspace_context_payload(root),
    }


def search_calculations(
    workspace_root: Path | str,
    args: dict[str, Any],
) -> dict[str, Any]:
    root = Path(workspace_root).expanduser().resolve()
    query = str(args.get("query") or "").strip()
    calculation_type = str(args.get("calculation_type") or "").strip()
    status_filter = str(args.get("status") or "").strip()
    if status_filter and status_filter not in RUN_STATUSES:
        raise ValueError(f"status must be one of: {', '.join(sorted(RUN_STATUSES))}")
    cutoff = _optional_iso(args.get("knowledge_cutoff"), "knowledge_cutoff")
    limit = int(args.get("limit") or DEFAULT_SEARCH_LIMIT)
    if limit < 1 or limit > MAX_SEARCH_LIMIT:
        raise ValueError(f"limit must be between 1 and {MAX_SEARCH_LIMIT}")
    from tradingcodex_service.application.research_object_catalog import (
        search_calculation_objects,
    )

    indexed = search_calculation_objects(
        root,
        {
            "query": query,
            "calculation_type": calculation_type,
            "status": status_filter,
            "knowledge_cutoff": cutoff,
            "limit": limit,
        },
    )
    cards = indexed["calculations"]
    return {
        "calculations": cards,
        "count": len(cards),
        "limit": limit,
        "payload_included": False,
        "index_path": indexed["index_path"],
        "fts_enabled": indexed["fts_enabled"],
        "workspace_context": workspace_context_payload(root),
    }


def get_calculation_run(
    workspace_root: Path | str,
    args: dict[str, Any],
) -> dict[str, Any]:
    root = Path(workspace_root).expanduser().resolve()
    run_id = _required_text(args, "calculation_run_id")
    path = _artifact_path(root, CALCULATION_RUN_ROOT, run_id)
    run = _verified_artifact(
        path,
        hash_field="run_sha256",
        id_field="calculation_run_id",
        expected_id=run_id,
        label="calculation run",
    )
    spec = _get_spec(root, str(run["calculation_spec_id"]))
    return {
        "status": "ok",
        "run": run,
        "spec": spec,
        "export_path": path.relative_to(root).as_posix(),
        "authority": "evidence_only",
        "workspace_context": workspace_context_payload(root),
    }


def compare_calculation_runs(
    workspace_root: Path | str,
    args: dict[str, Any],
) -> dict[str, Any]:
    root = Path(workspace_root).expanduser().resolve()
    run_ids = args.get("calculation_run_ids")
    metrics = args.get("metrics")
    if not isinstance(run_ids, list) or not 2 <= len(run_ids) <= 20:
        raise ValueError("calculation_run_ids must contain between 2 and 20 ids")
    if not isinstance(metrics, list) or not metrics or len(metrics) > 50 or not all(isinstance(item, str) and item.strip() for item in metrics):
        raise ValueError("metrics must contain between 1 and 50 metric names")
    wanted = list(dict.fromkeys(item.strip() for item in metrics))
    rows = []
    for run_id in run_ids:
        run = get_calculation_run(root, {"calculation_run_id": str(run_id)})["run"]
        values = {
            str(metric.get("name")): {
                "value": metric.get("value"),
                "value_type": metric.get("value_type"),
                "unit": metric.get("unit"),
                "currency": metric.get("currency"),
                "precision": metric.get("precision"),
            }
            for metric in run.get("metrics", [])
            if isinstance(metric, dict) and str(metric.get("name") or "") in wanted
        }
        rows.append(
            {
                "calculation_run_id": run["calculation_run_id"],
                "status": run["status"],
                "original_run_id": run.get("original_run_id") or "",
                "metrics": {name: values.get(name) for name in wanted},
            }
        )
    return {
        "metrics": wanted,
        "rows": rows,
        "workspace_context": workspace_context_payload(root),
    }


def verify_calculation_run_binding(
    workspace_root: Path | str,
    calculation_run_id: str,
    *,
    workflow_run_id: str,
    knowledge_cutoff: str,
) -> dict[str, Any]:
    """Verify one conclusion-grade run for a current research artifact.

    The returned hashes are service-derived binding material. Callers must not
    accept caller-authored hashes in their place.
    """

    root = Path(workspace_root).expanduser().resolve()
    run_id = str(calculation_run_id or "").strip()
    if not run_id:
        raise ValueError("calculation_run_id is required")
    path = _artifact_path(root, CALCULATION_RUN_ROOT, run_id)
    run = _verified_artifact(
        path,
        hash_field="run_sha256",
        id_field="calculation_run_id",
        expected_id=run_id,
        label="calculation run",
    )
    if run.get("status") not in {"succeeded", "reused"}:
        raise ValueError(f"calculation run is not conclusion-grade: {run_id}")
    if str(run.get("workflow_run_id") or "") != str(workflow_run_id or ""):
        raise ValueError(f"calculation run belongs to another workflow: {run_id}")
    artifact_cutoff = _iso(knowledge_cutoff, "knowledge_cutoff")
    spec = _get_spec(root, str(run["calculation_spec_id"]))
    spec_cutoff = _iso(spec.get("knowledge_cutoff"), "calculation spec knowledge_cutoff")
    if spec_cutoff > artifact_cutoff:
        raise ValueError(
            f"calculation run is after artifact knowledge cutoff: {run_id} ({spec_cutoff})"
        )
    original_id = str(run.get("original_run_id") or "")
    original_hash = ""
    if run["status"] == "reused":
        if not original_id:
            raise ValueError(f"reused calculation run lacks an origin: {run_id}")
        original_path = _artifact_path(root, CALCULATION_RUN_ROOT, original_id)
        original = _verified_artifact(
            original_path,
            hash_field="run_sha256",
            id_field="calculation_run_id",
            expected_id=original_id,
            label="original calculation run",
        )
        if (
            original.get("status") != "succeeded"
            or original.get("fingerprint") != run.get("fingerprint")
            or original.get("calculation_spec_id") != run.get("calculation_spec_id")
        ):
            raise ValueError(f"reused calculation run origin is invalid: {run_id}")
        original_hash = str(original["run_sha256"])
    elif original_id:
        raise ValueError(f"successful calculation run must not name an origin: {run_id}")
    return {
        "calculation_run_id": run_id,
        "run_sha256": run["run_sha256"],
        "status": run["status"],
        "calculation_spec_id": run["calculation_spec_id"],
        "fingerprint": run["fingerprint"],
        "knowledge_cutoff": spec_cutoff,
        "original_run_id": original_id,
        "original_run_sha256": original_hash,
    }


def validated_calculation_run_hashes(
    workspace_root: Path | str,
    calculation_run_ids: list[str],
    *,
    workflow_run_id: str,
    knowledge_cutoff: str,
) -> dict[str, str]:
    """Return exact CalculationRun hashes after artifact-binding validation."""

    if not isinstance(calculation_run_ids, list):
        raise ValueError("calculation_run_ids must be an array")
    if len(calculation_run_ids) != len(set(calculation_run_ids)):
        raise ValueError("calculation_run_ids must not contain duplicates")
    bindings = [
        verify_calculation_run_binding(
            workspace_root,
            run_id,
            workflow_run_id=workflow_run_id,
            knowledge_cutoff=knowledge_cutoff,
        )
        for run_id in calculation_run_ids
    ]
    return {item["calculation_run_id"]: item["run_sha256"] for item in bindings}


def _record_reuse_run(
    root: Path,
    *,
    spec: dict[str, Any],
    workflow_run_id: str,
    created_by: str,
    original: dict[str, Any],
) -> dict[str, Any]:
    seed = {
        "calculation_spec_id": spec["calculation_spec_id"],
        "fingerprint": spec["fingerprint"],
        "workflow_run_id": workflow_run_id,
        "status": "reused",
        "original_run_id": original["calculation_run_id"],
    }
    run_id = f"calc-run-{stable_hash(seed)[:20]}"
    run = {
        "schema_version": CALCULATION_RUN_SCHEMA_VERSION,
        "artifact_type": "calculation_run",
        "calculation_run_id": run_id,
        **seed,
        "envelope_sha256": original["envelope_sha256"],
        "runtime_manifest_sha256": original["runtime_manifest_sha256"],
        "metrics": list(original.get("metrics", [])),
        "diagnostics": dict(original.get("diagnostics", {})),
        "assumptions": list(original.get("assumptions", [])),
        "warnings": [*list(original.get("warnings", [])), "exact fingerprint reused"],
        "outputs": list(original.get("outputs", [])),
        "derived_dataset_ids": list(original.get("derived_dataset_ids", [])),
        "derived_dataset_hashes": dict(original.get("derived_dataset_hashes", {})),
        "stdout": dict(original.get("stdout", {})),
        "stderr": dict(original.get("stderr", {})),
        "error_type": "",
        "created_by": created_by,
        "system_recorded_at": now_iso(),
        "authority": "evidence_only",
        "blocked_actions": ["order_drafting", "order_approval", "order_execution"],
    }
    run["run_sha256"] = stable_hash(run)
    path = _artifact_path(root, CALCULATION_RUN_ROOT, run_id)
    artifact, status = _store_immutable(
        path,
        run,
        hash_field="run_sha256",
        id_field="calculation_run_id",
        label="calculation reuse run",
    )
    return _result(root, path, artifact, status)


def _successful_run_for_fingerprint(root: Path, fingerprint: str) -> dict[str, Any] | None:
    from tradingcodex_service.application.research_object_catalog import (
        search_calculation_objects,
    )

    indexed = search_calculation_objects(
        root,
        {"query": fingerprint, "status": "succeeded", "limit": 20},
    )
    for card in indexed["calculations"]:
        details = card.get("details") if isinstance(card.get("details"), dict) else {}
        if details.get("fingerprint") != fingerprint:
            continue
        run_id = str(card.get("object_id") or "")
        path = _artifact_path(root, CALCULATION_RUN_ROOT, run_id)
        return _verified_artifact(
            path,
            hash_field="run_sha256",
            id_field="calculation_run_id",
            expected_id=run_id,
            label="calculation run",
        )
    return None


def _all_runs(root: Path) -> list[dict[str, Any]]:
    directory = root / CALCULATION_RUN_ROOT
    if not directory.is_dir():
        return []
    runs = []
    for path in sorted(directory.glob("*.json")):
        try:
            run = _verified_artifact(
                path,
                hash_field="run_sha256",
                id_field="calculation_run_id",
                expected_id=path.stem,
                label="calculation run",
            )
        except ValueError:
            continue
        runs.append(run)
    runs.sort(key=lambda item: (str(item.get("system_recorded_at") or ""), str(item.get("calculation_run_id") or "")))
    return runs


def _run_card(run: dict[str, Any], *, spec: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "calculation_run_id": run["calculation_run_id"],
        "calculation_spec_id": run["calculation_spec_id"],
        "calculation_type": str((spec or {}).get("calculation_type") or ""),
        "calculation_version": str((spec or {}).get("calculation_version") or ""),
        "knowledge_cutoff": str((spec or {}).get("knowledge_cutoff") or ""),
        "status": run["status"],
        "original_run_id": run.get("original_run_id") or "",
        "metrics": [
            {
                "name": metric.get("name"),
                "value": metric.get("value"),
                "unit": metric.get("unit"),
                "currency": metric.get("currency"),
            }
            for metric in run.get("metrics", [])[:20]
            if isinstance(metric, dict)
        ],
        "warnings": list(run.get("warnings", []))[:10],
        "system_recorded_at": run.get("system_recorded_at"),
    }


def _runtime_manifest(path: Path | str | None) -> dict[str, Any]:
    raw_path = path
    if raw_path is None:
        configured = str(os.environ.get("TRADINGCODEX_CALCULATION_RUNTIME_ROOT") or "").strip()
        if not configured:
            raise ValueError("calculation runtime manifest is unavailable")
        raw_path = Path(configured) / "runtime-manifest.json"
    manifest_path = Path(raw_path).expanduser().absolute()
    manifest = _read_json_object(manifest_path, maximum=MAX_RESULT_BYTES, label="calculation runtime manifest")
    payload = dict(manifest)
    expected = str(payload.pop("manifest_sha256", ""))
    if manifest.get("schema_version") != 2 or HASH.fullmatch(expected) is None or stable_hash(payload) != expected:
        raise ValueError("calculation runtime manifest is invalid")
    if HASH.fullmatch(str(manifest.get("lock_sha256") or "")) is None:
        raise ValueError("calculation runtime lock identity is invalid")
    if HASH.fullmatch(str(manifest.get("requirements_sha256") or "")) is None:
        raise ValueError("calculation runtime requirements identity is invalid")
    expected = _packaged_runtime_identity()
    for field in ("lock_sha256", "requirements_sha256", "runner_sha256"):
        if manifest.get(field) != expected[field]:
            raise ValueError(f"calculation runtime {field} does not match this release")
    if manifest.get("packages") != expected["packages"]:
        raise ValueError("calculation runtime packages do not match this release")
    return manifest


def _packaged_runtime_identity() -> dict[str, Any]:
    package = files("tradingcodex_cli")
    try:
        lock_bytes = package.joinpath("calculation-runtime-lock.json").read_bytes()
        requirements_bytes = package.joinpath(
            "calculation-runtime-requirements.txt"
        ).read_bytes()
        runner_bytes = package.joinpath("calculation_runner.py").read_bytes()
        lock = json.loads(lock_bytes)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("packaged calculation runtime identity is unavailable") from exc
    if not isinstance(lock, dict):
        raise ValueError("packaged calculation runtime identity is invalid")
    packages = lock.get("packages")
    requirements_sha256 = hashlib.sha256(requirements_bytes).hexdigest()
    if (
        lock.get("schema_version") != 1
        or not isinstance(packages, dict)
        or not packages
        or lock.get("requirements_sha256") != requirements_sha256
    ):
        raise ValueError("packaged calculation runtime identity is invalid")
    return {
        "lock_sha256": hashlib.sha256(lock_bytes).hexdigest(),
        "requirements_sha256": requirements_sha256,
        "runner_sha256": hashlib.sha256(runner_bytes).hexdigest(),
        "packages": packages,
    }


def _runtime_identity(manifest: dict[str, Any]) -> dict[str, Any]:
    return {
        "runtime_manifest_sha256": manifest["manifest_sha256"],
        "runtime_lock_sha256": manifest["lock_sha256"],
        "runtime_requirements_sha256": manifest["requirements_sha256"],
        "python_version": manifest.get("python_version"),
        "python_implementation": manifest.get("python_implementation"),
        "platform_system": manifest.get("platform_system"),
        "platform_machine": manifest.get("platform_machine"),
        "numpy_config": manifest.get("numpy_config", {}),
        "packages": manifest.get("packages", {}),
    }


def _validated_inputs(
    root: Path,
    scratch: Path,
    raw_inputs: Any,
    *,
    knowledge_cutoff: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not isinstance(raw_inputs, list):
        raise ValueError("inputs must be an array")
    sidecar_inputs = []
    spec_inputs = []
    names: set[str] = set()
    filenames: set[str] = set()
    for index, raw in enumerate(raw_inputs):
        if not isinstance(raw, dict):
            raise ValueError(f"inputs[{index}] must be an object")
        name = str(raw.get("name") or "").strip()
        if not name or name in names:
            raise ValueError(f"inputs[{index}].name must be unique and non-empty")
        names.add(name)
        filename = _direct_name(raw.get("filename"), f"inputs[{index}].filename")
        if filename in filenames:
            raise ValueError(f"duplicate calculation input filename: {filename}")
        filenames.add(filename)
        payload = _read_single_link_file(
            scratch / filename,
            maximum=MAX_INPUT_BYTES,
            label=f"calculation input {index}",
        )
        digest = hashlib.sha256(payload).hexdigest()
        supplied = str(raw.get("sha256") or "")
        if supplied and supplied != digest:
            raise ValueError(f"inputs[{index}].sha256 does not match the file")
        kind = str(raw.get("kind") or "dataset_slice").strip()
        if kind not in ALLOWED_INPUT_KINDS:
            raise ValueError(
                f"inputs[{index}].kind must be one of: "
                + ", ".join(sorted(ALLOWED_INPUT_KINDS))
            )
        sidecar_inputs.append({"name": name, "filename": filename, "sha256": digest})
        spec_input = {
            "name": name,
            "kind": kind,
            "sha256": digest,
            "dataset_id": str(raw.get("dataset_id") or ""),
            "materialization_id": str(raw.get("materialization_id") or ""),
            "ledger_snapshot_id": str(raw.get("ledger_snapshot_id") or ""),
            "ledger_snapshot_hash": str(raw.get("ledger_snapshot_hash") or ""),
        }
        dataset_id = spec_input["dataset_id"]
        if kind in PRIVATE_INPUT_KINDS:
            if dataset_id:
                raise ValueError(f"inputs[{index}] private input must not reference a dataset")
            if not spec_input["ledger_snapshot_id"] or HASH.fullmatch(
                spec_input["ledger_snapshot_hash"]
            ) is None:
                raise ValueError(
                    f"inputs[{index}] private input requires ledger_snapshot_id and ledger_snapshot_hash"
                )
            _validate_private_input_binding(root, spec_input, index=index)
        if dataset_id:
            from tradingcodex_service.application.datasets import get_dataset_manifest

            dataset_response = get_dataset_manifest(root, {"dataset_id": dataset_id})
            if dataset_response["withdrawn"] or not dataset_response["payload_available"]:
                raise ValueError(f"calculation input dataset is unavailable: {dataset_id}")
            dataset_manifest = dataset_response["dataset"]
            if str(dataset_manifest["knowledge_cutoff"]) > knowledge_cutoff:
                raise ValueError(
                    f"calculation input dataset is after knowledge cutoff: {dataset_id}"
                )
            spec_input["dataset_manifest_hash"] = str(
                dataset_manifest["manifest_hash"]
            )
            _validate_dataset_materialization(
                root,
                scratch,
                raw=raw,
                spec_input=spec_input,
                dataset_response=dataset_response,
                filename=filename,
                payload_size=len(payload),
                payload_sha256=digest,
                index=index,
            )
        elif kind == "dataset_slice":
            raise ValueError(f"inputs[{index}].dataset_id is required for a dataset slice")
        if kind not in PRIVATE_INPUT_KINDS:
            spec_input["filename"] = filename
        spec_inputs.append(spec_input)
    return sidecar_inputs, spec_inputs


def _validate_private_input_binding(
    root: Path,
    spec_input: dict[str, Any],
    *,
    index: int,
) -> None:
    from tradingcodex_service.application.portfolio import (
        paper_portfolio_state_binding,
    )

    try:
        binding = paper_portfolio_state_binding(
            root,
            expected_snapshot_id=str(spec_input.get("ledger_snapshot_id") or ""),
            expected_snapshot_hash=str(spec_input.get("ledger_snapshot_hash") or ""),
        )
    except ValueError as exc:
        raise ValueError(
            f"inputs[{index}] private input binding is not a current "
            f"DB-canonical portfolio snapshot: {exc}"
        ) from exc
    if (
        binding["ledger_snapshot_id"] != spec_input.get("ledger_snapshot_id")
        or binding["ledger_snapshot_hash"] != spec_input.get("ledger_snapshot_hash")
    ):
        raise ValueError(f"inputs[{index}] private input binding mismatch")


def _validate_dataset_materialization(
    root: Path,
    scratch: Path,
    *,
    raw: dict[str, Any],
    spec_input: dict[str, Any],
    dataset_response: dict[str, Any],
    filename: str,
    payload_size: int,
    payload_sha256: str,
    index: int,
) -> None:
    from tradingcodex_service.application.artifact_bindings import (
        _receipt_signature,
    )
    from tradingcodex_service.application.research_objects import derive_content_id

    materialization_id = str(raw.get("materialization_id") or "").strip()
    if re.fullmatch(r"materialization-[0-9a-f]{24}", materialization_id) is None:
        raise ValueError(
            f"inputs[{index}].materialization_id must reference a service-issued Dataset slice"
        )
    expected_filename = f"{materialization_id}.parquet"
    if filename != expected_filename:
        raise ValueError(
            f"inputs[{index}].filename must match its Dataset materialization"
        )
    proof = _read_json_object(
        scratch / f"{materialization_id}.materialization.json",
        maximum=MAX_RESULT_BYTES,
        label=f"Dataset materialization proof {index}",
    )
    expected_fields = {
        "schema_version",
        "artifact_type",
        "materialization_id",
        "dataset_id",
        "manifest_hash",
        "payload_hash",
        "selector",
        "filename",
        "row_count",
        "size_bytes",
        "content_hash",
        "signature_algorithm",
        "proof_hash",
    }
    material = dict(proof)
    proof_hash = str(material.pop("proof_hash", ""))
    if (
        set(proof) != expected_fields
        or proof.get("schema_version") != 1
        or proof.get("artifact_type") != "dataset_materialization"
        or proof.get("signature_algorithm") != "hmac-sha256"
        or HASH.fullmatch(proof_hash) is None
    ):
        raise ValueError(f"inputs[{index}] Dataset materialization proof is invalid")
    expected_signature = _receipt_signature(
        root,
        material,
        create_signing_key=False,
    )
    if not hmac.compare_digest(proof_hash, expected_signature):
        raise ValueError(f"inputs[{index}] Dataset materialization proof is invalid")
    manifest = dataset_response["dataset"]
    selector = proof.get("selector")
    if not isinstance(selector, dict):
        raise ValueError(f"inputs[{index}] Dataset materialization selector is invalid")
    expected_id = derive_content_id(
        "materialization",
        {
            "dataset_id": spec_input["dataset_id"],
            "payload_hash": manifest["payload"]["sha256"],
            "selector": selector,
        },
    )
    if (
        proof.get("materialization_id") != materialization_id
        or expected_id != materialization_id
        or proof.get("dataset_id") != spec_input["dataset_id"]
        or proof.get("manifest_hash") != manifest["manifest_hash"]
        or proof.get("payload_hash") != manifest["payload"]["sha256"]
        or proof.get("filename") != filename
        or proof.get("content_hash") != payload_sha256
        or proof.get("size_bytes") != payload_size
        or type(proof.get("row_count")) is not int
        or proof["row_count"] < 0
    ):
        raise ValueError(
            f"inputs[{index}] Dataset materialization no longer matches its Dataset or file"
        )
    spec_input["materialization_id"] = materialization_id


def _validated_outputs(
    raw_outputs: Any,
    *,
    knowledge_cutoff: str,
    private_inputs_present: bool,
) -> list[dict[str, Any]]:
    if raw_outputs is None:
        return []
    if not isinstance(raw_outputs, list):
        raise ValueError("outputs must be an array")
    outputs = []
    filenames: set[str] = set()
    for index, raw in enumerate(raw_outputs):
        if not isinstance(raw, dict):
            raise ValueError(f"outputs[{index}] must be an object")
        name = str(raw.get("name") or "").strip()
        filename = _direct_name(raw.get("filename"), f"outputs[{index}].filename")
        if not name or filename in filenames:
            raise ValueError(f"outputs[{index}] must have a name and unique filename")
        if filename.casefold().endswith(FORBIDDEN_OUTPUT_SUFFIXES):
            raise ValueError("executable calculation output serialization is forbidden")
        filenames.add(filename)
        output: dict[str, Any] = {
            "name": name,
            "filename": filename,
            "media_type": str(raw.get("media_type") or "application/octet-stream"),
        }
        dataset = raw.get("dataset")
        if dataset not in (None, {}):
            if private_inputs_present:
                raise ValueError(
                    "calculation outputs derived from private ledger inputs cannot be promoted to datasets"
                )
            if not isinstance(dataset, dict):
                raise ValueError(f"outputs[{index}].dataset must be an object")
            forbidden = {
                "source_filename",
                "parent_dataset_ids",
                "transformation_code_hash",
                "principal_id",
                "created_by",
                "dataset_id",
                "manifest_hash",
            }
            if forbidden & set(dataset):
                raise ValueError(
                    f"outputs[{index}].dataset contains service-derived fields"
                )
            required = {
                "title",
                "provider",
                "provider_query",
                "as_of",
                "vintage",
                "period_start",
                "period_end",
                "timezone",
                "frequency",
                "universe_membership_policy",
                "universe_membership",
                "columns",
            }
            missing = sorted(
                field for field in required if dataset.get(field) in (None, "", [], {})
            )
            if missing:
                raise ValueError(
                    f"outputs[{index}].dataset is missing: {', '.join(missing)}"
                )
            if not dataset.get("instrument_ids") and not dataset.get("symbols"):
                raise ValueError(
                    f"outputs[{index}].dataset requires instrument_ids or symbols"
                )
            supplied_cutoff = _optional_iso(
                dataset.get("knowledge_cutoff"),
                f"outputs[{index}].dataset.knowledge_cutoff",
            )
            if supplied_cutoff and supplied_cutoff != knowledge_cutoff:
                raise ValueError(
                    f"outputs[{index}].dataset knowledge_cutoff must match the calculation spec"
                )
            normalized_dataset = dict(dataset)
            normalized_dataset["knowledge_cutoff"] = knowledge_cutoff
            _finite_value(normalized_dataset, f"outputs[{index}].dataset")
            output["dataset"] = normalized_dataset
        outputs.append(output)
    return outputs


def _validated_output_schema(value: Any) -> dict[str, Any]:
    schema = _finite_object(value, "output_schema", required=True)
    metrics = schema.get("metrics")
    if not isinstance(metrics, list) or not metrics or len(metrics) > 200:
        raise ValueError("output_schema.metrics must contain between 1 and 200 metrics")
    normalized: list[str | dict[str, Any]] = []
    names: set[str] = set()
    for index, raw in enumerate(metrics):
        if isinstance(raw, str):
            name = raw.strip()
            record: str | dict[str, Any] = name
        elif isinstance(raw, dict):
            allowed = {"name", "value_type", "unit", "currency", "precision"}
            if set(raw) - allowed:
                raise ValueError(f"output_schema.metrics[{index}] has unknown fields")
            name = str(raw.get("name") or "").strip()
            record = {"name": name}
            value_type = raw.get("value_type")
            if value_type is not None:
                value_type = str(value_type)
                if value_type not in {"number", "integer", "decimal", "string", "boolean"}:
                    raise ValueError(
                        f"output_schema.metrics[{index}].value_type is invalid"
                    )
                record["value_type"] = value_type
            for field in ("unit", "currency"):
                if field in raw:
                    field_value = raw[field]
                    if field_value is not None and not isinstance(field_value, str):
                        raise ValueError(
                            f"output_schema.metrics[{index}].{field} must be a string or null"
                        )
                    record[field] = field_value
            if "precision" in raw:
                precision = raw["precision"]
                if precision is not None and (
                    isinstance(precision, bool)
                    or not isinstance(precision, int)
                    or precision < 0
                ):
                    raise ValueError(
                        f"output_schema.metrics[{index}].precision must be a non-negative integer or null"
                    )
                record["precision"] = precision
        else:
            raise ValueError(f"output_schema.metrics[{index}] must be a name or object")
        if not name or name in names:
            raise ValueError("output_schema metric names must be unique and non-empty")
        names.add(name)
        normalized.append(record)
    return {**schema, "metrics": normalized}


def _validate_prepared_execution(
    root: Path,
    scratch: Path,
    *,
    result_name: str,
    envelope: dict[str, Any],
    spec: dict[str, Any],
    manifest: dict[str, Any],
) -> None:
    suffix = ".tcx-result.json"
    if not result_name.endswith(suffix):
        raise ValueError("calculation result filename is invalid")
    script_name = _script_name(result_name[: -len(suffix)])
    sidecar = _read_json_object(
        scratch / f"{script_name}.tcx.json",
        maximum=MAX_RESULT_BYTES,
        label="calculation sidecar",
    )
    sidecar_payload = dict(sidecar)
    sidecar_hash = str(sidecar_payload.pop("sidecar_sha256", ""))
    expected_fields = {
        "schema_version",
        "mode",
        "calculation_spec_id",
        "fingerprint",
        "workflow_run_id",
        "script_name",
        "script_sha256",
        "runtime_lock_sha256",
        "runtime_requirements_sha256",
        "runtime_manifest_sha256",
        "inputs",
        "outputs",
        "result_file",
        "sidecar_sha256",
    }
    if (
        set(sidecar) != expected_fields
        or sidecar.get("schema_version") != CALCULATION_SIDECAR_SCHEMA_VERSION
        or sidecar.get("mode") != "prepared"
        or HASH.fullmatch(sidecar_hash) is None
        or stable_hash(sidecar_payload) != sidecar_hash
    ):
        raise ValueError("calculation sidecar is invalid")
    expected = {
        "calculation_spec_id": spec["calculation_spec_id"],
        "fingerprint": spec["fingerprint"],
        "workflow_run_id": envelope["workflow_run_id"],
        "script_name": script_name,
        "script_sha256": spec["script_sha256"],
        "runtime_lock_sha256": manifest["lock_sha256"],
        "runtime_requirements_sha256": manifest["requirements_sha256"],
        "runtime_manifest_sha256": manifest["manifest_sha256"],
        "outputs": spec["outputs"],
        "result_file": result_name,
    }
    for field, value in expected.items():
        if sidecar.get(field) != value:
            raise ValueError(f"calculation sidecar {field} mismatch")
    script_payload = _read_single_link_file(
        scratch / script_name,
        maximum=MAX_SCRIPT_BYTES,
        label="calculation script",
    )
    if hashlib.sha256(script_payload).hexdigest() != spec["script_sha256"]:
        raise ValueError("calculation script changed after execution")
    sidecar_inputs = sidecar.get("inputs")
    spec_inputs = spec.get("inputs")
    if not isinstance(sidecar_inputs, list) or not isinstance(spec_inputs, list):
        raise ValueError("calculation sidecar inputs are invalid")
    spec_by_name = {
        str(item.get("name") or ""): item
        for item in spec_inputs
        if isinstance(item, dict)
    }
    if len(sidecar_inputs) != len(spec_by_name):
        raise ValueError("calculation sidecar inputs do not match the spec")
    for index, item in enumerate(sidecar_inputs):
        if not isinstance(item, dict) or set(item) != {"name", "filename", "sha256"}:
            raise ValueError(f"calculation sidecar input {index} is invalid")
        name = str(item.get("name") or "")
        filename = _direct_name(item.get("filename"), f"calculation sidecar input {index}")
        expected_input = spec_by_name.get(name)
        if (
            expected_input is None
            or item.get("sha256") != expected_input.get("sha256")
            or (
                expected_input.get("kind") not in PRIVATE_INPUT_KINDS
                and filename != expected_input.get("filename")
            )
        ):
            raise ValueError("calculation sidecar inputs do not match the spec")
        payload = _read_single_link_file(
            scratch / filename,
            maximum=MAX_INPUT_BYTES,
            label=f"calculation input {index}",
        )
        if hashlib.sha256(payload).hexdigest() != item["sha256"]:
            raise ValueError(f"calculation input changed after execution: {filename}")
        input_kind = expected_input.get("kind")
        if input_kind == "dataset_slice":
            from tradingcodex_service.application.datasets import get_dataset_manifest

            dataset_id = str(expected_input.get("dataset_id") or "")
            dataset_response = get_dataset_manifest(root, {"dataset_id": dataset_id})
            if dataset_response["withdrawn"] or not dataset_response["payload_available"]:
                raise ValueError(
                    f"calculation input dataset is no longer available: {dataset_id}"
                )
            if (
                dataset_response["dataset"].get("manifest_hash")
                != expected_input.get("dataset_manifest_hash")
            ):
                raise ValueError(
                    f"calculation input dataset manifest changed after preparation: {dataset_id}"
                )
            _validate_dataset_materialization(
                root,
                scratch,
                raw=expected_input,
                spec_input=expected_input,
                dataset_response=dataset_response,
                filename=filename,
                payload_size=len(payload),
                payload_sha256=str(item["sha256"]),
                index=index,
            )
        elif input_kind in PRIVATE_INPUT_KINDS:
            _validate_private_input_binding(root, expected_input, index=index)
    _validate_recorded_result(envelope, spec)


def _validate_recorded_result(envelope: dict[str, Any], spec: dict[str, Any]) -> None:
    if envelope["status"] != "succeeded":
        return
    result = envelope.get("result")
    if not isinstance(result, dict) or set(result) != {
        "metrics",
        "diagnostics",
        "assumptions",
        "warnings",
        "output_files",
    }:
        raise ValueError("calculation result does not match the typed result schema")
    metrics = result.get("metrics")
    if not isinstance(metrics, list) or not metrics:
        raise ValueError("calculation result metrics must be a non-empty array")
    normalized: list[dict[str, Any]] = []
    names: set[str] = set()
    for index, metric in enumerate(metrics):
        required = {"name", "value", "value_type", "unit", "currency", "precision"}
        if not isinstance(metric, dict) or set(metric) != required:
            raise ValueError(f"calculation result metric {index} is invalid")
        name = str(metric.get("name") or "").strip()
        value_type = str(metric.get("value_type") or "")
        if not name or name in names or value_type not in {
            "number",
            "integer",
            "decimal",
            "string",
            "boolean",
        }:
            raise ValueError(f"calculation result metric {index} is invalid")
        names.add(name)
        value = metric.get("value")
        if value_type == "number" and (
            isinstance(value, bool) or not isinstance(value, (int, float))
        ):
            raise ValueError(f"calculation result metric {name} must be numeric")
        if value_type == "integer" and (
            isinstance(value, bool) or not isinstance(value, int)
        ):
            raise ValueError(f"calculation result metric {name} must be an integer")
        if value_type in {"decimal", "string"} and not isinstance(value, str):
            raise ValueError(f"calculation result metric {name} must be a string")
        if value_type == "decimal":
            try:
                decimal_value = Decimal(value)
            except (InvalidOperation, ValueError) as exc:
                raise ValueError(
                    f"calculation result metric {name} must be a finite decimal string"
                ) from exc
            if not decimal_value.is_finite():
                raise ValueError(
                    f"calculation result metric {name} must be a finite decimal string"
                )
        if value_type == "boolean" and not isinstance(value, bool):
            raise ValueError(f"calculation result metric {name} must be boolean")
        precision = metric.get("precision")
        if precision is not None and (
            isinstance(precision, bool)
            or not isinstance(precision, int)
            or precision < 0
        ):
            raise ValueError(f"calculation result metric {name} precision is invalid")
        for field in ("unit", "currency"):
            if metric[field] is not None and not isinstance(metric[field], str):
                raise ValueError(f"calculation result metric {name} {field} is invalid")
        normalized.append(metric)
    for field in ("assumptions", "warnings"):
        values = result.get(field)
        if not isinstance(values, list) or not all(isinstance(item, str) for item in values):
            raise ValueError(f"calculation result {field} must be an array of strings")
    if not isinstance(result.get("diagnostics"), dict):
        raise ValueError("calculation result diagnostics must be an object")
    output_files = result.get("output_files")
    if not isinstance(output_files, list):
        raise ValueError("calculation result output_files must be an array")
    emitted_files = [_direct_name(item, "calculation result output file") for item in output_files]
    if len(emitted_files) != len(set(emitted_files)):
        raise ValueError("calculation result output_files must be unique")
    declared_files = {str(item["filename"]) for item in spec.get("outputs", [])}
    envelope_files = {
        str(item.get("filename") or "")
        for item in envelope.get("outputs", [])
        if isinstance(item, dict)
    }
    if not set(emitted_files).issubset(declared_files) or set(emitted_files) != envelope_files:
        raise ValueError("calculation result outputs do not match declared outputs")
    schema_metrics = spec["output_schema"]["metrics"]
    expected_names = [item if isinstance(item, str) else str(item["name"]) for item in schema_metrics]
    if [item["name"] for item in normalized] != expected_names:
        raise ValueError("calculation result metric names do not match output_schema")
    for expected_metric, actual_metric in zip(schema_metrics, normalized, strict=True):
        if not isinstance(expected_metric, dict):
            continue
        for field in ("value_type", "unit", "currency", "precision"):
            if field in expected_metric and actual_metric[field] != expected_metric[field]:
                raise ValueError(
                    f"calculation result metric {actual_metric['name']} {field} does not match output_schema"
                )
    _finite_value(result, "calculation result")


def _record_derived_datasets(
    root: Path,
    scratch: Path,
    spec: dict[str, Any],
    output_hashes: list[dict[str, Any]],
    *,
    principal_id: str,
) -> list[dict[str, str]]:
    output_by_name = {
        str(item.get("filename") or ""): item for item in output_hashes
    }
    dataset_outputs = [
        item
        for item in spec.get("outputs", [])
        if isinstance(item, dict) and isinstance(item.get("dataset"), dict)
    ]
    if not dataset_outputs:
        return []
    if any(
        item.get("kind") in PRIVATE_INPUT_KINDS
        for item in spec.get("inputs", [])
        if isinstance(item, dict)
    ):
        raise ValueError(
            "calculation outputs derived from private ledger inputs cannot be promoted to datasets"
        )
    parent_dataset_ids = sorted(
        {
            str(item.get("dataset_id") or "")
            for item in spec.get("inputs", [])
            if isinstance(item, dict) and str(item.get("dataset_id") or "")
        }
    )
    from tradingcodex_service.application.datasets import (
        get_dataset_manifest,
        record_dataset_snapshot,
    )

    recorded = []
    for output in dataset_outputs:
        filename = str(output["filename"])
        if filename not in output_by_name:
            raise ValueError(
                f"declared derived dataset output was not emitted: {filename}"
            )
        metadata = dict(output["dataset"])
        requested_parent_ids = metadata.pop("parent_dataset_ids", None)
        if requested_parent_ids not in (None, parent_dataset_ids):
            raise ValueError("derived dataset parent_dataset_ids must match calculation inputs")
        dataset_result = record_dataset_snapshot(
            root,
            {
                **metadata,
                "source_filename": filename,
                "parent_dataset_ids": parent_dataset_ids,
                "transformation_code_hash": spec["script_sha256"],
                "principal_id": principal_id,
            },
            scratch_root=scratch,
        )
        dataset_id = str(dataset_result["dataset_id"])
        manifest = get_dataset_manifest(root, {"dataset_id": dataset_id})["dataset"]
        recorded.append(
            {
                "dataset_id": dataset_id,
                "manifest_hash": str(manifest["manifest_hash"]),
            }
        )
    return recorded


def _validate_envelope(envelope: dict[str, Any], manifest: dict[str, Any]) -> None:
    expected_fields = {
        "schema_version",
        "calculation_spec_id",
        "fingerprint",
        "workflow_run_id",
        "status",
        "error_type",
        "result",
        "outputs",
        "stdout",
        "stderr",
        "runtime_manifest_sha256",
        "envelope_sha256",
    }
    payload = dict(envelope)
    expected = str(payload.pop("envelope_sha256", ""))
    if (
        set(envelope) != expected_fields
        or envelope.get("schema_version") != CALCULATION_RESULT_SCHEMA_VERSION
        or HASH.fullmatch(expected) is None
        or stable_hash(payload) != expected
    ):
        raise ValueError("calculation result envelope hash mismatch")
    if envelope.get("status") not in {"succeeded", "failed"}:
        raise ValueError("calculation result envelope status is invalid")
    if envelope.get("runtime_manifest_sha256") != manifest.get("manifest_sha256"):
        raise ValueError("calculation result runtime identity mismatch")
    for field in ("calculation_spec_id", "workflow_run_id"):
        if not str(envelope.get(field) or "").strip():
            raise ValueError(f"calculation result {field} is required")
    if HASH.fullmatch(str(envelope.get("fingerprint") or "")) is None:
        raise ValueError("calculation result fingerprint is invalid")
    for field in ("stdout", "stderr"):
        summary = envelope.get(field)
        if (
            not isinstance(summary, dict)
            or set(summary) != {"bytes", "sha256"}
            or HASH.fullmatch(str(summary.get("sha256") or "")) is None
        ):
            raise ValueError(f"calculation result {field} summary is invalid")
        if not isinstance(summary.get("bytes"), int) or not 0 <= summary["bytes"] <= MAX_RESULT_BYTES:
            raise ValueError(f"calculation result {field} byte count is invalid")
    if envelope["status"] == "succeeded":
        if envelope.get("error_type") != "" or not isinstance(envelope.get("result"), dict):
            raise ValueError("successful calculation result payload is invalid")
    elif not str(envelope.get("error_type") or "").strip():
        raise ValueError("failed calculation result error_type is required")
    _finite_value(envelope, "calculation result")


def _verify_envelope_outputs(scratch: Path, envelope: dict[str, Any]) -> list[dict[str, Any]]:
    outputs = envelope.get("outputs")
    if not isinstance(outputs, list):
        raise ValueError("calculation result outputs must be an array")
    verified = []
    filenames: set[str] = set()
    for index, output in enumerate(outputs):
        if not isinstance(output, dict) or set(output) != {"filename", "bytes", "sha256"}:
            raise ValueError(f"calculation result output {index} is invalid")
        filename = _direct_name(output.get("filename"), f"calculation result output {index}")
        if filename in filenames:
            raise ValueError("calculation result output filenames must be unique")
        filenames.add(filename)
        payload = _read_single_link_file(
            scratch / filename,
            maximum=MAX_INPUT_BYTES,
            label=f"calculation result output {index}",
        )
        digest = hashlib.sha256(payload).hexdigest()
        if digest != output.get("sha256") or len(payload) != output.get("bytes"):
            raise ValueError(f"calculation result output changed: {filename}")
        verified.append({"filename": filename, "bytes": len(payload), "sha256": digest})
    return verified


def _scratch_root(raw: Path | str | None) -> Path:
    configured = raw if raw is not None else os.environ.get("TRADINGCODEX_SCRATCH")
    if not str(configured or "").strip():
        raise ValueError("TRADINGCODEX_SCRATCH is required")
    path = Path(str(configured)).expanduser()
    if not path.is_absolute():
        raise ValueError("calculation scratch_root must be absolute")
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise ValueError("calculation scratch_root is unavailable") from exc
    if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise ValueError("calculation scratch_root must be a real directory")
    return path.absolute()


def _script_name(value: Any) -> str:
    name = str(value or "")
    if SCRIPT_NAME.fullmatch(name) is None:
        raise ValueError("script_name must be one direct scratch-local .py filename")
    return name


def _direct_name(value: Any, label: str) -> str:
    name = str(value or "")
    if DIRECT_FILE_NAME.fullmatch(name) is None or "/" in name or "\\" in name:
        raise ValueError(f"{label} must be one direct scratch-local filename")
    return name


def _read_single_link_file(path: Path, *, maximum: int, label: str) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ValueError(f"{label} must be a real readable file") from exc
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1:
            raise ValueError(f"{label} must be a single-link regular file")
        if opened.st_size <= 0 or opened.st_size > maximum:
            raise ValueError(f"{label} size is outside the supported bound")
        chunks = []
        remaining = maximum + 1
        while remaining > 0:
            chunk = os.read(descriptor, min(64 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
    finally:
        os.close(descriptor)
    payload = b"".join(chunks)
    if len(payload) > maximum:
        raise ValueError(f"{label} exceeds the supported bound")
    return payload


def _read_json_object(path: Path, *, maximum: int, label: str) -> dict[str, Any]:
    raw = _read_single_link_file(path, maximum=maximum, label=label)
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} must be valid JSON") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def _store_sidecar(path: Path, sidecar: dict[str, Any]) -> None:
    serialized = json.dumps(sidecar, indent=2, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n"
    with exclusive_file_lock(path):
        if path.exists() or path.is_symlink():
            existing = _read_json_object(path, maximum=MAX_RESULT_BYTES, label="calculation sidecar")
            if existing == sidecar:
                return
            raise ValueError(f"calculation sidecar already exists: {path.name}")
        atomic_write_text(path, serialized)
        if os.name != "nt":
            path.chmod(0o600)


def _artifact_path(root: Path, allowed_root: Path, artifact_id: str) -> Path:
    return safe_workspace_path(
        root,
        allowed_root / f"{sanitize_id(artifact_id)}.json",
        allowed_roots=(allowed_root,),
    )


def _store_immutable(
    path: Path,
    artifact: dict[str, Any],
    *,
    hash_field: str,
    id_field: str,
    label: str,
    ignored_fields: set[str] | None = None,
) -> tuple[dict[str, Any], str]:
    with exclusive_file_lock(path):
        if path.exists():
            existing = _verified_artifact(
                path,
                hash_field=hash_field,
                id_field=id_field,
                expected_id=str(artifact[id_field]),
                label=label,
            )
            if existing.get(hash_field) == artifact.get(hash_field):
                return existing, "existing"
            ignored = {hash_field, "system_recorded_at", *(ignored_fields or set())}
            if stable_hash({key: value for key, value in existing.items() if key not in ignored}) == stable_hash({key: value for key, value in artifact.items() if key not in ignored}):
                return existing, "existing"
            raise ValueError(f"{label} is immutable and already exists: {artifact[id_field]}")
        atomic_write_text(
            path,
            json.dumps(artifact, indent=2, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n",
        )
    return artifact, "recorded"


def _verified_artifact(
    path: Path,
    *,
    hash_field: str,
    id_field: str,
    expected_id: str,
    label: str,
) -> dict[str, Any]:
    if not path.is_file():
        raise ValueError(f"{label} not found: {expected_id}")
    artifact = _read_json_object(path, maximum=MAX_RESULT_BYTES, label=label)
    if artifact.get(id_field) != expected_id:
        raise ValueError(f"{label} id mismatch: {expected_id}")
    payload = dict(artifact)
    expected = str(payload.pop(hash_field, ""))
    if HASH.fullmatch(expected) is None or stable_hash(payload) != expected:
        raise ValueError(f"{label} hash mismatch: {expected_id}")
    return artifact


def _get_spec(root: Path, spec_id: str) -> dict[str, Any]:
    return _verified_artifact(
        _artifact_path(root, CALCULATION_SPEC_ROOT, spec_id),
        hash_field="spec_sha256",
        id_field="calculation_spec_id",
        expected_id=spec_id,
        label="calculation spec",
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


def _hash_text(value: Any, field: str) -> str:
    text = str(value or "").strip().lower()
    if HASH.fullmatch(text) is None:
        raise ValueError(f"{field} must be a lowercase SHA-256 hash")
    return text


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


def _optional_iso(value: Any, field: str) -> str:
    return _iso(value, field) if str(value or "").strip() else ""


def _finite_object(value: Any, field: str, *, required: bool = False) -> dict[str, Any]:
    if not isinstance(value, dict) or (required and not value):
        raise ValueError(f"{field} must be {'a non-empty ' if required else 'an '}object")
    _finite_value(value, field)
    return dict(value)


def _finite_value(value: Any, field: str) -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{field} must not contain NaN or Infinity")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _finite_value(item, f"{field}[{index}]")
        return
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        for key, item in value.items():
            _finite_value(item, f"{field}.{key}")
        return
    raise ValueError(f"{field} must contain only finite JSON values")

from __future__ import annotations

import os
import hashlib
import json
import platform
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tradingcodex_service.application.calculations import (
    _validate_recorded_result,
    compare_calculation_runs,
    get_calculation_run,
    prepare_calculation,
    record_calculation_run,
    search_calculations,
    verify_calculation_run_binding,
)
from tradingcodex_service.application.analysis_runs import begin_analysis_run
from tradingcodex_service.application.artifact_bindings import (
    verify_authenticated_artifact_binding,
)
from tradingcodex_service.application.common import stable_hash
from tradingcodex_service.application.runtime import ensure_workspace_manifest
from tradingcodex_service.application.datasets import (
    get_dataset_manifest,
    materialize_dataset_slice,
    record_dataset_snapshot,
    withdraw_dataset_snapshot,
)
from tradingcodex_service.application.research import record_source_snapshot
from tradingcodex_service.application.research import get_research_artifact
from tradingcodex_service.application.portfolio import list_positions
from tradingcodex_service.mcp_runtime import TOOL_REGISTRY, call_mcp_tool
from tradingcodex_cli.generator import (
    CALCULATION_RUNTIME_LOCK,
    CALCULATION_RUNTIME_REQUIREMENTS,
    _load_calculation_runtime_lock,
    _validate_calculation_runtime_requirements,
)


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "tradingcodex_cli/calculation_runner.py"


def _run(workspace: Path, scratch: Path, script: str, *, environment: dict[str, str] | None = None):
    return subprocess.run(
        [
            sys.executable,
            "-I",
            "-B",
            "-S",
            str(RUNNER),
            "--workspace",
            str(workspace),
            "--scratch",
            str(scratch),
            "--",
            script,
        ],
        cwd=workspace,
        env=environment,
        stdin=subprocess.DEVNULL,
        text=True,
        capture_output=True,
        timeout=10,
    )


def test_calculation_runner_is_deterministic_package_free_and_sanitized(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    scratch = tmp_path / "scratch"
    workspace.mkdir()
    scratch.mkdir()
    (scratch / "calc.py").write_text(
        """from decimal import Decimal
import importlib.util
import os
print(Decimal('121') / Decimal('1.1') ** 2)
print(importlib.util.find_spec('django'))
print(importlib.util.find_spec('tradingcodex_service'))
print(os.environ.get('BROKER_API_SECRET'))
print(os.environ['TMPDIR'])
""",
        encoding="utf-8",
    )
    environment = {**os.environ, "BROKER_API_SECRET": "must-not-leak"}

    result = _run(workspace, scratch, "calc.py", environment=environment)

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["1E+2", "None", "None", "None", str(scratch)]
    assert "must-not-leak" not in result.stdout + result.stderr


def test_calculation_runner_rejects_paths_symlinks_and_hardlinks(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    scratch = tmp_path / "scratch"
    workspace.mkdir()
    scratch.mkdir()
    source = scratch / "source.py"
    source.write_text("print('no')\n", encoding="utf-8")
    linked = scratch / "linked.py"
    os.link(source, linked)
    symlink = scratch / "symlink.py"
    symlink.symlink_to(source)

    for script in ("../source.py", "source.py", "linked.py", "symlink.py", "-c"):
        result = _run(workspace, scratch, script)
        assert result.returncode == 2


def test_calculation_runner_caps_normal_text_output(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    scratch = tmp_path / "scratch"
    workspace.mkdir()
    scratch.mkdir()
    (scratch / "loud.py").write_text("print('x' * 1_000_001)\n", encoding="utf-8")

    result = _run(workspace, scratch, "loud.py")

    assert result.returncode == 2
    assert "output exceeded" in result.stderr


@pytest.mark.parametrize(
    "source",
    [
        "import subprocess\nsubprocess.run(['ignored'])\n",
        "import importlib\nsubprocess = importlib.import_module('subprocess')\nsubprocess.run(['ignored'])\n",
        "import importlib\nsocket = importlib.import_module('socket')\nsocket.socket()\n",
        "import importlib\nensurepip = importlib.import_module('ensurepip')\nensurepip.bootstrap()\n",
        "import importlib\nctypes = importlib.import_module('ctypes')\nctypes.CDLL(None).getpid()\n",
    ],
)
def test_calculation_runner_denies_process_network_install_and_ffi_escapes(
    tmp_path: Path,
    source: str,
) -> None:
    workspace = tmp_path / "workspace"
    scratch = tmp_path / "scratch"
    workspace.mkdir()
    scratch.mkdir()
    (scratch / "escape.py").write_text(source, encoding="utf-8")

    result = _run(workspace, scratch, "escape.py")

    assert result.returncode == 2
    assert "denied" in result.stderr


def test_calculation_runtime_requirements_are_fully_hash_locked_and_tamper_evident() -> None:
    lock = _load_calculation_runtime_lock(CALCULATION_RUNTIME_LOCK.read_bytes())
    hashes = _validate_calculation_runtime_requirements(
        CALCULATION_RUNTIME_REQUIREMENTS.read_bytes(),
        lock,
    )

    assert set(hashes) == set(lock["packages"])
    assert all(package_hashes for package_hashes in hashes.values())
    tampered = CALCULATION_RUNTIME_REQUIREMENTS.read_bytes().replace(
        b"00dc4e846108a382",
        b"10dc4e846108a382",
        1,
    )
    with pytest.raises(ValueError, match="requirements hash mismatch"):
        _validate_calculation_runtime_requirements(tampered, lock)

    original_lock = json.loads(CALCULATION_RUNTIME_LOCK.read_text(encoding="utf-8"))
    invalid_locks = [
        {**original_lock, "python_requires": ">=3.11"},
        {
            **original_lock,
            "installer": {"dependencies": True, "source_distributions": False},
        },
        {**original_lock, "direct_packages": ["numpy"]},
        {**original_lock, "unexpected": True},
    ]
    for invalid in invalid_locks:
        with pytest.raises(ValueError, match="TradingCodex calculation runtime"):
            _load_calculation_runtime_lock(json.dumps(invalid).encode("utf-8"))


def test_prepare_calculation_mcp_schema_accepts_derived_dataset_metadata() -> None:
    output_item = TOOL_REGISTRY["prepare_calculation"].input_schema["properties"][
        "outputs"
    ]["items"]

    assert "dataset" in output_item["properties"]
    assert output_item["properties"]["dataset"]["type"] == "object"


def test_calculation_runner_rejects_tampered_runtime_manifest(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    scratch = tmp_path / "scratch"
    workspace.mkdir()
    scratch.mkdir()
    runner, manifest_path = _prepared_runtime(tmp_path)
    (scratch / "calc.py").write_text("print('must not run')\n", encoding="utf-8")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["packages"] = {"numpy": "0.0.0"}
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    result = _run_prepared(runner, workspace, scratch, "calc.py")

    assert result.returncode == 2
    assert "manifest hash mismatch" in result.stderr
    assert "must not run" not in result.stdout


def test_calculation_service_rejects_runtime_identity_not_packaged_with_release(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    scratch = tmp_path / "scratch"
    workspace.mkdir()
    scratch.mkdir()
    ensure_workspace_manifest(workspace)
    _runner, manifest_path = _prepared_runtime(tmp_path)
    (scratch / "calc.py").write_text("print('must not prepare')\n", encoding="utf-8")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["lock_sha256"] = "0" * 64
    manifest["manifest_sha256"] = stable_hash(
        {key: value for key, value in manifest.items() if key != "manifest_sha256"}
    )
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="does not match this release"):
        prepare_calculation(
            workspace,
            _prepare_args("calc.py"),
            scratch_root=scratch,
            runtime_manifest_path=manifest_path,
        )


def test_calculation_runner_rejects_rehashed_wrong_runtime_self_contract(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    scratch = tmp_path / "scratch"
    workspace.mkdir()
    scratch.mkdir()
    runner, manifest_path = _prepared_runtime(tmp_path)
    (scratch / "calc.py").write_text("print('must not run')\n", encoding="utf-8")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["runner_sha256"] = "0" * 64
    manifest.pop("manifest_sha256")
    manifest["manifest_sha256"] = stable_hash(manifest)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    result = _run_prepared(runner, workspace, scratch, "calc.py")

    assert result.returncode == 2
    assert "runner hash mismatch" in result.stderr
    assert "must not run" not in result.stdout


def _prepared_runtime(tmp_path: Path) -> tuple[Path, Path]:
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    runner = runtime / "calculation_runner.py"
    shutil.copy2(RUNNER, runner)
    lock_bytes = CALCULATION_RUNTIME_LOCK.read_bytes()
    requirements_bytes = CALCULATION_RUNTIME_REQUIREMENTS.read_bytes()
    lock = _load_calculation_runtime_lock(lock_bytes)
    for package_name, package_version in lock["packages"].items():
        metadata = runtime / (
            f"{package_name.replace('-', '_')}-{package_version}.dist-info"
        )
        metadata.mkdir()
        (metadata / "METADATA").write_text(
            "Metadata-Version: 2.1\n"
            f"Name: {package_name}\n"
            f"Version: {package_version}\n",
            encoding="utf-8",
        )
    manifest = {
        "schema_version": 2,
        "lock_sha256": hashlib.sha256(lock_bytes).hexdigest(),
        "requirements_sha256": hashlib.sha256(requirements_bytes).hexdigest(),
        "runner_sha256": hashlib.sha256(runner.read_bytes()).hexdigest(),
        "site_packages": ".",
        "python_version": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "platform_system": platform.system(),
        "platform_machine": platform.machine(),
        "packages": lock["packages"],
        "numpy_config": {},
    }
    manifest["manifest_sha256"] = stable_hash(manifest)
    manifest_path = runtime / "runtime-manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return runner, manifest_path


def _run_prepared(runner: Path, workspace: Path, scratch: Path, script: str):
    return subprocess.run(
        [
            sys.executable,
            "-I",
            "-B",
            "-S",
            str(runner),
            "--workspace",
            str(workspace),
            "--scratch",
            str(scratch),
            "--",
            script,
        ],
        cwd=workspace,
        stdin=subprocess.DEVNULL,
        text=True,
        capture_output=True,
        timeout=10,
    )


def _write_runner_sidecar(
    scratch: Path,
    manifest_path: Path,
    script_name: str,
    *,
    result_file: str = "calculation-result.json",
) -> Path:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    source = (scratch / script_name).read_bytes()
    sidecar = {
        "schema_version": 1,
        "mode": "prepared",
        "calculation_spec_id": "calc-spec-runner-test",
        "fingerprint": "f" * 64,
        "workflow_run_id": "analysis-runner-test",
        "script_name": script_name,
        "script_sha256": hashlib.sha256(source).hexdigest(),
        "runtime_lock_sha256": manifest["lock_sha256"],
        "runtime_requirements_sha256": manifest["requirements_sha256"],
        "runtime_manifest_sha256": manifest["manifest_sha256"],
        "inputs": [],
        "outputs": [],
        "result_file": result_file,
    }
    sidecar["sidecar_sha256"] = stable_hash(sidecar)
    (scratch / f"{script_name}.tcx.json").write_text(
        json.dumps(sidecar),
        encoding="utf-8",
    )
    return scratch / result_file


def _prepare_args(script_name: str, workflow_run_id: str = "analysis-one") -> dict[str, object]:
    return {
        "calculation_type": "unit_return",
        "calculation_version": "1",
        "script_name": script_name,
        "workflow_run_id": workflow_run_id,
        "knowledge_cutoff": "2025-01-01T00:00:00Z",
        "principal_id": "technical-analyst",
        "inputs": [],
        "parameters": {"periods": 1},
        "output_schema": {"metrics": ["return"]},
        "outputs": [],
    }


def _materialized_price_input(workspace: Path, scratch: Path) -> dict[str, str]:
    snapshot = record_source_snapshot(
        workspace,
        {
            "provider": "test-provider",
            "source_category": "market_data",
            "source_locator": "test://calculation-prices",
            "provider_query": {"symbol": "BTCUSD"},
            "known_at": "2024-12-31T00:00:00Z",
            "as_of": "2024-12-31T00:00:00Z",
            "payload": {"request_hash": "calculation-prices"},
            "principal_id": "technical-analyst",
        },
    )
    (scratch / "calculation-prices.csv").write_text(
        "timestamp,symbol,close\n"
        "2024-12-30T00:00:00Z,BTCUSD,100\n"
        "2024-12-31T00:00:00Z,BTCUSD,110\n",
        encoding="utf-8",
    )
    dataset = record_dataset_snapshot(
        workspace,
        {
            "source_filename": "calculation-prices.csv",
            "title": "Calculation price input",
            "provider": "test-provider",
            "provider_query": {"symbol": "BTCUSD"},
            "source_snapshot_ids": [snapshot["snapshot_id"]],
            "known_at": "2024-12-31T00:00:00Z",
            "knowledge_cutoff": "2025-01-01T00:00:00Z",
            "as_of": "2024-12-31T00:00:00Z",
            "vintage": "2024-12-31-final",
            "period_start": "2024-12-30T00:00:00Z",
            "period_end": "2024-12-31T00:00:00Z",
            "timezone": "UTC",
            "frequency": "1d",
            "symbols": ["BTCUSD"],
            "universe_membership_policy": "single_instrument",
            "universe_membership": {"BTCUSD": {"included": True}},
            "columns": [
                {"name": "timestamp", "type": "timestamp", "nullable": False},
                {"name": "symbol", "type": "string", "nullable": False},
                {"name": "close", "type": "float64", "nullable": False},
            ],
            "principal_id": "technical-analyst",
        },
        scratch_root=scratch,
    )
    materialized = materialize_dataset_slice(
        workspace,
        {
            "dataset_id": dataset["dataset_id"],
            "columns": ["timestamp", "symbol", "close"],
        },
        scratch_root=scratch,
    )
    return {
        "name": "prices",
        "filename": str(materialized["filename"]),
        "kind": "dataset_slice",
        "dataset_id": str(dataset["dataset_id"]),
        "materialization_id": str(materialized["materialization_id"]),
        "sha256": str(materialized["content_hash"]),
    }


def test_prepared_calculation_records_searches_compares_and_reuses(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    scratch = tmp_path / "scratch"
    workspace.mkdir()
    scratch.mkdir()
    ensure_workspace_manifest(workspace)
    runner, manifest = _prepared_runtime(tmp_path)
    calculation_input = _materialized_price_input(workspace, scratch)
    (scratch / "calc.py").write_text(
        """tcx_emit_result({
    'metrics': [{'name': 'return', 'value': 0.1, 'value_type': 'number', 'unit': 'ratio', 'currency': None, 'precision': 6}],
    'diagnostics': {'observations': 2},
    'assumptions': [],
    'warnings': [],
    'output_files': [],
})
""",
        encoding="utf-8",
    )
    args = _prepare_args("calc.py")
    args["inputs"] = [calculation_input]

    prepared = prepare_calculation(
        workspace,
        args,
        scratch_root=scratch,
        runtime_manifest_path=manifest,
    )
    assert prepared["status"] == "prepared"
    executed = _run_prepared(runner, workspace, scratch, "calc.py")
    assert executed.returncode == 0, executed.stderr
    recorded = record_calculation_run(
        workspace,
        {
            "calculation_spec_id": prepared["calculation_spec_id"],
            "workflow_run_id": "analysis-one",
            "result_file": prepared["result_file"],
            "principal_id": "technical-analyst",
        },
        scratch_root=scratch,
        runtime_manifest_path=manifest,
    )
    run = recorded["artifact"]
    assert run["status"] == "succeeded"
    assert run["metrics"][0]["value"] == pytest.approx(0.1)

    reuse_args = _prepare_args("calc.py", "analysis-two")
    reuse_args["inputs"] = [calculation_input]
    reused = prepare_calculation(
        workspace,
        reuse_args,
        scratch_root=scratch,
        runtime_manifest_path=manifest,
    )
    assert reused["status"] == "reused"
    assert reused["execution_required"] is False
    assert reused["original_run_id"] == run["calculation_run_id"]
    cross_role_args = _prepare_args("calc.py", "analysis-three")
    cross_role_args["inputs"] = [calculation_input]
    cross_role_args["principal_id"] = "macro-analyst"
    cross_role_reuse = prepare_calculation(
        workspace,
        cross_role_args,
        scratch_root=scratch,
        runtime_manifest_path=manifest,
    )
    assert cross_role_reuse["status"] == "reused"
    assert cross_role_reuse["original_run_id"] == run["calculation_run_id"]
    binding = verify_calculation_run_binding(
        workspace,
        reused["calculation_run_id"],
        workflow_run_id="analysis-two",
        knowledge_cutoff="2025-01-02T00:00:00Z",
    )
    assert binding["original_run_sha256"] == run["run_sha256"]
    with pytest.raises(ValueError, match="another workflow"):
        verify_calculation_run_binding(
            workspace,
            run["calculation_run_id"],
            workflow_run_id="analysis-two",
            knowledge_cutoff="2025-01-02T00:00:00Z",
        )

    begin_analysis_run(
        workspace,
        "Seal a reused calculation into the current workflow artifact.",
        run_id="analysis-two",
        apply_investor_context=False,
    )
    call_mcp_tool(
        workspace,
        "create_research_artifact",
        {
            "artifact_id": "calculation-backed-technical-note",
            "artifact_type": "research_memo",
            "universe": "digital_assets",
            "workflow_type": "calculation_memory_test",
            "title": "Calculation-backed technical note",
            "markdown": "# Calculation-backed technical note\n\n[factual] The sealed return is 10%.\n",
            "source_as_of": "2025-01-01",
            "knowledge_cutoff": "2025-01-02T00:00:00Z",
            "evidence_lane": "live_forward",
            "readiness_label": "accepted",
            "context_summary": "Exact calculation reuse binding test.",
            "reader_summary": "The current workflow reuses an exact prior return calculation.",
            "handoff_state": "accepted",
            "confidence": "high",
            "missing_evidence": [],
            "next_recipient": "head-manager",
            "next_action": "Use the sealed calculation lineage.",
            "blocked_actions": ["order", "execution"],
            "source_snapshot_ids": [],
            "calculation_run_ids": [reused["calculation_run_id"]],
            "workflow_run_id": "analysis-two",
            "input_artifact_ids": [],
        },
        transport_principal="technical-analyst",
    )
    artifact = get_research_artifact(
        workspace,
        {
            "artifact_id": "calculation-backed-technical-note",
            "include_markdown": False,
        },
    )
    assert artifact["calculation_run_hashes"] == {
        reused["calculation_run_id"]: binding["run_sha256"]
    }
    assert artifact["calculation_reuse_origins"] == {
        reused["calculation_run_id"]: {
            "original_run_id": run["calculation_run_id"],
            "original_run_sha256": run["run_sha256"],
        }
    }
    verification = verify_authenticated_artifact_binding(workspace, artifact)
    receipt = json.loads((workspace / verification["path"]).read_text(encoding="utf-8"))
    assert receipt["schema_version"] == 2
    assert receipt["calculation_run_ids"] == [reused["calculation_run_id"]]

    search = search_calculations(workspace, {"query": "return"})
    assert search["count"] == 3
    assert search["payload_included"] is False
    fetched = get_calculation_run(
        workspace,
        {"calculation_run_id": reused["calculation_run_id"]},
    )
    assert fetched["run"]["status"] == "reused"
    comparison = compare_calculation_runs(
        workspace,
        {
            "calculation_run_ids": [run["calculation_run_id"], reused["calculation_run_id"]],
            "metrics": ["return"],
        },
    )
    assert comparison["rows"][0]["metrics"]["return"]["value"] == pytest.approx(0.1)
    assert comparison["rows"][1]["metrics"]["return"]["value"] == pytest.approx(0.1)


def test_prepared_calculation_denies_undeclared_input_and_records_failure(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    scratch = tmp_path / "scratch"
    workspace.mkdir()
    scratch.mkdir()
    ensure_workspace_manifest(workspace)
    runner, manifest = _prepared_runtime(tmp_path)
    (scratch / "secret.txt").write_text("hidden\n", encoding="utf-8")
    (scratch / "blocked.py").write_text(
        """open('secret.txt', encoding='utf-8').read()
tcx_emit_result({'metrics': [{'name': 'x', 'value': 1, 'value_type': 'integer', 'unit': None, 'currency': None, 'precision': 0}]})
""",
        encoding="utf-8",
    )
    args = _prepare_args("blocked.py")
    args["inputs"] = []
    prepared = prepare_calculation(
        workspace,
        args,
        scratch_root=scratch,
        runtime_manifest_path=manifest,
    )

    executed = _run_prepared(runner, workspace, scratch, "blocked.py")

    assert executed.returncode == 1
    envelope = json.loads((scratch / str(prepared["result_file"])).read_text(encoding="utf-8"))
    assert envelope["status"] == "failed"
    assert envelope["error_type"] == "PermissionError"
    recorded = record_calculation_run(
        workspace,
        {
            "calculation_spec_id": prepared["calculation_spec_id"],
            "workflow_run_id": "analysis-one",
            "result_file": prepared["result_file"],
            "principal_id": "technical-analyst",
        },
        scratch_root=scratch,
        runtime_manifest_path=manifest,
    )
    assert recorded["artifact"]["status"] == "failed"
    with pytest.raises(ValueError, match="not conclusion-grade"):
        verify_calculation_run_binding(
            workspace,
            recorded["artifact"]["calculation_run_id"],
            workflow_run_id="analysis-one",
            knowledge_cutoff="2025-01-02T00:00:00Z",
        )


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity", " 1.25 "])
def test_prepared_calculation_rejects_nonfinite_or_inexact_decimal_metrics(
    tmp_path: Path,
    value: str,
) -> None:
    workspace = tmp_path / "workspace"
    scratch = tmp_path / "scratch"
    workspace.mkdir()
    scratch.mkdir()
    runner, manifest = _prepared_runtime(tmp_path)
    (scratch / "decimal.py").write_text(
        f"""tcx_emit_result({{
    'metrics': [{{'name': 'return', 'value': {value!r}, 'value_type': 'decimal', 'unit': 'ratio', 'currency': None, 'precision': 4}}],
    'diagnostics': {{}},
    'assumptions': [],
    'warnings': [],
    'output_files': [],
}})
""",
        encoding="utf-8",
    )
    result_path = _write_runner_sidecar(scratch, manifest, "decimal.py")

    executed = _run_prepared(runner, workspace, scratch, "decimal.py")

    assert executed.returncode == 1
    envelope = json.loads(result_path.read_text(encoding="utf-8"))
    assert envelope["status"] == "failed"
    assert envelope["error_type"] == "ValueError"


def test_prepared_calculation_preserves_exact_finite_decimal_metric(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    scratch = tmp_path / "scratch"
    workspace.mkdir()
    scratch.mkdir()
    runner, manifest = _prepared_runtime(tmp_path)
    (scratch / "decimal.py").write_text(
        """tcx_emit_result({
    'metrics': [{'name': 'return', 'value': '-123.4500', 'value_type': 'decimal', 'unit': 'ratio', 'currency': None, 'precision': 4}],
    'diagnostics': {},
    'assumptions': [],
    'warnings': [],
    'output_files': [],
})
""",
        encoding="utf-8",
    )
    result_path = _write_runner_sidecar(scratch, manifest, "decimal.py")

    executed = _run_prepared(runner, workspace, scratch, "decimal.py")

    assert executed.returncode == 0, executed.stderr
    envelope = json.loads(result_path.read_text(encoding="utf-8"))
    assert envelope["result"]["metrics"][0]["value"] == "-123.4500"


def test_record_revalidates_sidecar_script_inputs_and_output_schema(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    scratch = tmp_path / "scratch"
    workspace.mkdir()
    scratch.mkdir()
    ensure_workspace_manifest(workspace)
    runner, manifest = _prepared_runtime(tmp_path)
    calculation_input = _materialized_price_input(workspace, scratch)
    materialized_path = scratch / calculation_input["filename"]
    original_materialization = materialized_path.read_bytes()
    (scratch / "wrong-metric.py").write_text(
        """tcx_emit_result({
    'metrics': [{'name': 'unexpected', 'value': 1, 'value_type': 'integer', 'unit': None, 'currency': None, 'precision': 0}],
    'diagnostics': {},
    'assumptions': [],
    'warnings': [],
    'output_files': [],
})
""",
        encoding="utf-8",
    )
    args = _prepare_args("wrong-metric.py")
    args["inputs"] = [calculation_input]
    prepared = prepare_calculation(
        workspace,
        args,
        scratch_root=scratch,
        runtime_manifest_path=manifest,
    )
    executed = _run_prepared(runner, workspace, scratch, "wrong-metric.py")
    assert executed.returncode == 0, executed.stderr

    with pytest.raises(ValueError, match="metric names do not match output_schema"):
        record_calculation_run(
            workspace,
            {
                "calculation_spec_id": prepared["calculation_spec_id"],
                "workflow_run_id": "analysis-one",
                "result_file": prepared["result_file"],
                "principal_id": "technical-analyst",
            },
            scratch_root=scratch,
            runtime_manifest_path=manifest,
        )

    duplicate_schema = _prepare_args("wrong-metric.py")
    duplicate_schema["inputs"] = [calculation_input]
    duplicate_schema["output_schema"] = {"metrics": ["return", "return"]}
    with pytest.raises(ValueError, match="metric names must be unique"):
        prepare_calculation(
            workspace,
            duplicate_schema,
            scratch_root=scratch,
            runtime_manifest_path=manifest,
        )

    materialized_path.write_bytes(original_materialization + b"changed")
    with pytest.raises(ValueError, match="input changed after execution"):
        record_calculation_run(
            workspace,
            {
                "calculation_spec_id": prepared["calculation_spec_id"],
                "workflow_run_id": "analysis-one",
                "result_file": prepared["result_file"],
                "principal_id": "technical-analyst",
            },
            scratch_root=scratch,
            runtime_manifest_path=manifest,
        )

    materialized_path.write_bytes(original_materialization)
    proof_path = scratch / f"{calculation_input['materialization_id']}.materialization.json"
    original_proof = proof_path.read_bytes()
    proof = json.loads(original_proof)
    proof["selector"] = {**proof["selector"], "symbols": ["FORGED"]}
    proof_path.write_text(json.dumps(proof), encoding="utf-8")
    with pytest.raises(ValueError, match="materialization proof is invalid"):
        record_calculation_run(
            workspace,
            {
                "calculation_spec_id": prepared["calculation_spec_id"],
                "workflow_run_id": "analysis-one",
                "result_file": prepared["result_file"],
                "principal_id": "technical-analyst",
            },
            scratch_root=scratch,
            runtime_manifest_path=manifest,
        )

    proof_path.write_bytes(original_proof)
    (scratch / "wrong-metric.py").write_text("print('changed')\n", encoding="utf-8")
    with pytest.raises(ValueError, match="script changed after execution"):
        record_calculation_run(
            workspace,
            {
                "calculation_spec_id": prepared["calculation_spec_id"],
                "workflow_run_id": "analysis-one",
                "result_file": prepared["result_file"],
                "principal_id": "technical-analyst",
            },
            scratch_root=scratch,
            runtime_manifest_path=manifest,
        )


def test_record_rejects_dataset_withdrawn_after_preparation(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    scratch = tmp_path / "scratch"
    workspace.mkdir()
    scratch.mkdir()
    ensure_workspace_manifest(workspace)
    runner, manifest = _prepared_runtime(tmp_path)
    calculation_input = _materialized_price_input(workspace, scratch)
    (scratch / "withdrawn.py").write_text(
        """tcx_emit_result({
    'metrics': [{'name': 'return', 'value': 0.1, 'value_type': 'number', 'unit': None, 'currency': None, 'precision': None}],
    'diagnostics': {},
    'assumptions': [],
    'warnings': [],
    'output_files': [],
})
""",
        encoding="utf-8",
    )
    args = _prepare_args("withdrawn.py", "analysis-withdrawn")
    args["inputs"] = [calculation_input]
    prepared = prepare_calculation(
        workspace,
        args,
        scratch_root=scratch,
        runtime_manifest_path=manifest,
    )
    executed = _run_prepared(runner, workspace, scratch, "withdrawn.py")
    assert executed.returncode == 0, executed.stderr

    withdraw_dataset_snapshot(
        workspace,
        {
            "dataset_id": calculation_input["dataset_id"],
            "reason_code": "legal",
            "reason": "record-time withdrawal regression",
            "confirmed_by_user": True,
            "principal_id": "test-user",
        },
    )
    with pytest.raises(ValueError, match="dataset is no longer available"):
        record_calculation_run(
            workspace,
            {
                "calculation_spec_id": prepared["calculation_spec_id"],
                "workflow_run_id": "analysis-withdrawn",
                "result_file": prepared["result_file"],
                "principal_id": "technical-analyst",
            },
            scratch_root=scratch,
            runtime_manifest_path=manifest,
        )


def test_successful_tabular_output_is_promoted_to_derived_dataset(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    scratch = tmp_path / "scratch"
    workspace.mkdir()
    scratch.mkdir()
    ensure_workspace_manifest(workspace)
    runner, manifest = _prepared_runtime(tmp_path)
    snapshot = record_source_snapshot(
        workspace,
        {
            "provider": "test-provider",
            "source_category": "market_data",
            "source_locator": "test://prices",
            "provider_query": {"symbol": "BTCUSD"},
            "known_at": "2024-12-31T00:00:00Z",
            "as_of": "2024-12-31T00:00:00Z",
            "payload": {"request_hash": "prices"},
            "principal_id": "technical-analyst",
        },
    )
    (scratch / "parent.csv").write_text(
        "timestamp,symbol,close\n"
        "2024-12-30T00:00:00Z,BTCUSD,100\n"
        "2024-12-31T00:00:00Z,BTCUSD,110\n",
        encoding="utf-8",
    )
    parent = record_dataset_snapshot(
        workspace,
        {
            "source_filename": "parent.csv",
            "title": "Parent prices",
            "description": "Parent calculation input.",
            "tags": ["prices"],
            "provider": "test-provider",
            "provider_query": {"symbol": "BTCUSD"},
            "source_snapshot_ids": [snapshot["snapshot_id"]],
            "known_at": "2024-12-31T00:00:00Z",
            "knowledge_cutoff": "2025-01-01T00:00:00Z",
            "as_of": "2024-12-31T00:00:00Z",
            "vintage": "2024-12-31-final",
            "period_start": "2024-12-30T00:00:00Z",
            "period_end": "2024-12-31T00:00:00Z",
            "timezone": "UTC",
            "frequency": "1d",
            "symbols": ["BTCUSD"],
            "universe_membership_policy": "single_instrument",
            "universe_membership": {"BTCUSD": {"included": True}},
            "adjustment_policy": "unadjusted",
            "corporate_action_policy": "not_applicable",
            "delisting_policy": "retain_history",
            "columns": [
                {"name": "timestamp", "type": "timestamp", "nullable": False},
                {"name": "symbol", "type": "string", "nullable": False},
                {"name": "close", "type": "float64", "nullable": False, "unit": "price", "currency": "USD"},
            ],
            "principal_id": "technical-analyst",
        },
        scratch_root=scratch,
    )
    parent_materialization = materialize_dataset_slice(
        workspace,
        {
            "dataset_id": parent["dataset_id"],
            "columns": ["timestamp", "symbol", "close"],
        },
        scratch_root=scratch,
    )
    (scratch / "derived.py").write_text(
        """with open('returns.csv', 'w', encoding='utf-8') as output:
    output.write('timestamp,symbol,return\\n')
    output.write('2024-12-31T00:00:00Z,BTCUSD,0.1\\n')
tcx_emit_result({
    'metrics': [{'name': 'return', 'value': 0.1, 'value_type': 'number', 'unit': 'ratio', 'currency': None, 'precision': 6}],
    'diagnostics': {'observations': 2},
    'assumptions': [],
    'warnings': [],
    'output_files': ['returns.csv'],
})
""",
        encoding="utf-8",
    )
    args = _prepare_args("derived.py", "analysis-derived")
    args["inputs"] = [
        {
            "name": "prices",
            "filename": parent_materialization["filename"],
            "kind": "dataset_slice",
            "dataset_id": parent["dataset_id"],
            "materialization_id": parent_materialization["materialization_id"],
            "sha256": parent_materialization["content_hash"],
        }
    ]
    args["outputs"] = [
        {
            "name": "returns",
            "filename": "returns.csv",
            "media_type": "text/csv",
            "dataset": {
                "title": "Calculated BTC returns",
                "description": "Return series derived by the sealed calculation.",
                "tags": ["returns"],
                "provider": "TradingCodex calculation",
                "provider_query": {"calculation": "unit_return"},
                "known_at": "2025-01-01T00:00:00Z",
                "knowledge_cutoff": "2025-01-01T00:00:00Z",
                "as_of": "2024-12-31T00:00:00Z",
                "vintage": "calculated-v1",
                "period_start": "2024-12-31T00:00:00Z",
                "period_end": "2024-12-31T00:00:00Z",
                "timezone": "UTC",
                "frequency": "1d",
                "symbols": ["BTCUSD"],
                "universe_membership_policy": "inherits_parent",
                "universe_membership": {"BTCUSD": {"included": True}},
                "adjustment_policy": "inherits_parent",
                "corporate_action_policy": "inherits_parent",
                "delisting_policy": "inherits_parent",
                "columns": [
                    {"name": "timestamp", "type": "timestamp", "nullable": False},
                    {"name": "symbol", "type": "string", "nullable": False},
                    {"name": "return", "type": "float64", "nullable": False, "unit": "ratio"},
                ],
            },
        }
    ]
    prepared = prepare_calculation(
        workspace,
        args,
        scratch_root=scratch,
        runtime_manifest_path=manifest,
    )
    executed = _run_prepared(runner, workspace, scratch, "derived.py")
    assert executed.returncode == 0, executed.stderr
    recorded = record_calculation_run(
        workspace,
        {
            "calculation_spec_id": prepared["calculation_spec_id"],
            "workflow_run_id": "analysis-derived",
            "result_file": prepared["result_file"],
            "principal_id": "technical-analyst",
        },
        scratch_root=scratch,
        runtime_manifest_path=manifest,
    )["artifact"]

    assert len(recorded["derived_dataset_ids"]) == 1
    derived_id = recorded["derived_dataset_ids"][0]
    derived = get_dataset_manifest(workspace, {"dataset_id": derived_id})["dataset"]
    assert recorded["derived_dataset_hashes"][derived_id] == derived["manifest_hash"]
    assert derived["lineage"]["parent_dataset_ids"] == [parent["dataset_id"]]
    assert derived["lineage"]["transformation_code_hash"] == recorded_calculation_script_hash(workspace, recorded)
    symbol_search = search_calculations(
        workspace,
        {"query": "BTCUSD", "calculation_type": "unit_return"},
    )
    assert symbol_search["count"] == 1
    assert set(symbol_search["calculations"][0]["details"]["dataset_ids"]) == {
        parent["dataset_id"],
        derived_id,
    }


def test_private_ledger_input_requires_a_service_issued_binding(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    scratch = tmp_path / "scratch"
    workspace.mkdir()
    scratch.mkdir()
    ensure_workspace_manifest(workspace)
    runner, manifest = _prepared_runtime(tmp_path)
    (scratch / "private.txt").write_text("sensitive\n", encoding="utf-8")
    (scratch / "private.py").write_text(
        """tcx_emit_result({
    'metrics': [{'name': 'return', 'value': 0.1, 'value_type': 'number', 'unit': None, 'currency': None, 'precision': None}],
    'diagnostics': {},
    'assumptions': [],
    'warnings': [],
    'output_files': [],
})
""",
        encoding="utf-8",
    )
    args = _prepare_args("private.py", "analysis-private")
    args["inputs"] = [
        {
            "name": "ledger",
            "filename": "private.txt",
            "kind": "private_ledger",
            "ledger_snapshot_id": "ledger-one",
            "ledger_snapshot_hash": "a" * 64,
        }
    ]
    args["outputs"] = [
        {
            "name": "leak",
            "filename": "leak.csv",
            "dataset": {"title": "must not be recorded"},
        }
    ]

    with pytest.raises(ValueError, match="DB-canonical portfolio snapshot"):
        prepare_calculation(
            workspace,
            args,
            scratch_root=scratch,
            runtime_manifest_path=manifest,
        )

    binding = list_positions(workspace)["private_calculation_binding"]
    args["inputs"][0]["ledger_snapshot_id"] = binding["ledger_snapshot_id"]
    args["inputs"][0]["ledger_snapshot_hash"] = binding["ledger_snapshot_hash"]
    with pytest.raises(ValueError, match="private ledger inputs"):
        prepare_calculation(
            workspace,
            args,
            scratch_root=scratch,
            runtime_manifest_path=manifest,
        )

    args["outputs"] = []
    prepared = prepare_calculation(
        workspace,
        args,
        scratch_root=scratch,
        runtime_manifest_path=manifest,
    )
    assert prepared["status"] == "prepared"
    spec = json.loads(
        (
            workspace
            / "trading/research/calculations/specs"
            / f"{prepared['calculation_spec_id']}.json"
        ).read_text(encoding="utf-8")
    )
    assert "filename" not in spec["inputs"][0]

    executed = _run_prepared(runner, workspace, scratch, "private.py")
    assert executed.returncode == 0, executed.stderr
    record_args = {
        "calculation_spec_id": prepared["calculation_spec_id"],
        "workflow_run_id": "analysis-private",
        "result_file": prepared["result_file"],
        "principal_id": "technical-analyst",
    }
    recorded = record_calculation_run(
        workspace,
        record_args,
        scratch_root=scratch,
        runtime_manifest_path=manifest,
    )
    assert recorded["artifact"]["status"] == "succeeded"

    from apps.portfolio.models import PaperPortfolioState

    state_pk = int(str(binding["ledger_snapshot_id"]).split(":")[1])
    state = PaperPortfolioState.objects.get(pk=state_pk)
    state.version += 1
    state.save(update_fields=["version", "updated_at"])
    with pytest.raises(ValueError, match="version is no longer current"):
        record_calculation_run(
            workspace,
            record_args,
            scratch_root=scratch,
            runtime_manifest_path=manifest,
        )


def test_prepared_calculation_rejects_unsupported_input_kinds_and_forged_materialization_proofs(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    scratch = tmp_path / "scratch"
    workspace.mkdir()
    scratch.mkdir()
    ensure_workspace_manifest(workspace)
    _runner, manifest = _prepared_runtime(tmp_path)
    (scratch / "strict.py").write_text("print('sealed')\n", encoding="utf-8")
    (scratch / "raw.txt").write_text("not governed\n", encoding="utf-8")
    unsupported = _prepare_args("strict.py")
    unsupported["inputs"] = [
        {"name": "raw", "filename": "raw.txt", "kind": "file"}
    ]
    with pytest.raises(ValueError, match="kind must be one of"):
        prepare_calculation(
            workspace,
            unsupported,
            scratch_root=scratch,
            runtime_manifest_path=manifest,
        )

    calculation_input = _materialized_price_input(workspace, scratch)
    proof_path = scratch / (
        calculation_input["materialization_id"] + ".materialization.json"
    )
    proof = json.loads(proof_path.read_text(encoding="utf-8"))
    proof["selector"] = {**proof["selector"], "symbols": ["FORGED"]}
    proof_path.write_text(json.dumps(proof), encoding="utf-8")
    forged = _prepare_args("strict.py")
    forged["inputs"] = [calculation_input]
    with pytest.raises(ValueError, match="materialization proof is invalid"):
        prepare_calculation(
            workspace,
            forged,
            scratch_root=scratch,
            runtime_manifest_path=manifest,
        )


@pytest.mark.parametrize("value", ["NaN", "Infinity", "not-a-decimal"])
def test_service_validation_rejects_invalid_and_nonfinite_decimal_strings(
    value: str,
) -> None:
    output_schema = {
        "metrics": [
            {
                "name": "present_value",
                "value_type": "decimal",
                "unit": "currency",
                "currency": "USD",
                "precision": 2,
            }
        ]
    }
    with pytest.raises(ValueError, match="finite decimal string"):
        _validate_recorded_result(
            {
                "status": "succeeded",
                "result": {
                    "metrics": [
                        {
                            "name": "present_value",
                            "value": value,
                            "value_type": "decimal",
                            "unit": "currency",
                            "currency": "USD",
                            "precision": 2,
                        }
                    ],
                    "diagnostics": {},
                    "assumptions": [],
                    "warnings": [],
                    "output_files": [],
                },
                "outputs": [],
            },
            {"output_schema": output_schema, "outputs": []},
        )


def recorded_calculation_script_hash(workspace: Path, run: dict[str, object]) -> str:
    spec = get_calculation_run(
        workspace,
        {"calculation_run_id": str(run["calculation_run_id"])},
    )["spec"]
    return str(spec["script_sha256"])

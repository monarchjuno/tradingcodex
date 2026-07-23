from __future__ import annotations

import hashlib
import json
import os
import shutil
from contextlib import contextmanager
from pathlib import Path

import pytest
import yaml

from tradingcodex_service.application import artifact_bindings as artifact_bindings_module
from tradingcodex_service.application import research as research_module
from tradingcodex_service.application.analysis_runs import begin_analysis_run
from tradingcodex_service.application.artifact_catalog import list_artifact_catalog
from tradingcodex_service.application.artifact_bindings import (
    ARTIFACT_BINDING_SIGNING_KEY_FILE,
    record_authenticated_artifact_binding,
    verify_authenticated_artifact_binding,
)
from tradingcodex_service.application.common import stable_hash
from tradingcodex_service.application.forecasting import _origin_artifact_ref
from tradingcodex_service.application.research import (
    append_research_artifact_version,
    create_research_artifact,
    export_research_artifact_md,
    get_research_artifact,
    list_workflow_artifacts,
    record_source_snapshot,
    research_artifact_version_archive_path,
)
from tradingcodex_service.application.runtime import (
    ensure_workspace_manifest,
    persist_workspace_context_if_available,
)
from tradingcodex_service.mcp_runtime import call_mcp_tool as _runtime_call_mcp_tool, handle_mcp_rpc


RUN_ID = "analysis-authenticated-artifacts"


@pytest.fixture(autouse=True)
def attached_run(tmp_path: Path) -> None:
    ensure_workspace_manifest(tmp_path)
    begin_analysis_run(
        tmp_path,
        "Analyze ACME with authenticated evidence only.",
        run_id=RUN_ID,
        apply_investor_context=False,
    )


def _artifact_args(
    artifact_id: str,
    artifact_type: str = "research_memo",
    *,
    inputs: list[str] | None = None,
    markdown: str = "",
) -> dict[str, object]:
    payload = {
        "artifact_id": artifact_id,
        "artifact_type": artifact_type,
        "universe": "public_equity",
        "title": artifact_id,
        "markdown": markdown or f"# {artifact_id}\n\n[factual] Authenticated fixture evidence.\n",
        "summary": "Authentication fixture.",
        "status": {
            "handoff": "accepted",
            "evidence_readiness": "decision-grade",
            "action_readiness": "research-only",
            "confidence": "high",
            "confidence_basis": "Authenticated fixture evidence.",
            "missing_evidence": [],
            "blocked_actions": ["order", "execution"],
        },
        "lineage": {
            "knowledge_cutoff": "2026-07-12T00:00:00Z",
            "evidence_lane": "live_forward",
            "source_snapshot_ids": [],
            "workflow_run_id": RUN_ID,
            "input_artifact_ids": inputs or [],
            "dataset_ids": [],
            "calculation_run_ids": [],
        },
        "requirements": [],
    }
    if artifact_type == "synthesis_report":
        payload.pop("artifact_id")
    return payload


def call_mcp_tool(
    workspace_root: Path,
    name: str,
    args: dict[str, object],
    **kwargs: object,
) -> dict[str, object]:
    """Keep legacy append test scenarios while exercising the full v2 wire shape."""

    if name == "append_research_artifact_version" and "status" not in args:
        artifact_id = str(args.get("artifact_id") or "")
        existing = get_research_artifact(
            workspace_root,
            {"artifact_id": artifact_id, "include_markdown": False},
        )
        artifact_type = str(existing.get("artifact_type") or "research_memo")
        enriched: dict[str, object] = {
            "artifact_type": artifact_type,
            "title": str(existing.get("title") or artifact_id),
            "universe": str(existing.get("universe") or "public_equity"),
            "markdown": str(args.get("markdown") or ""),
            "summary": str(existing.get("summary") or "Authentication fixture."),
            "status": {
                "handoff": str(args.get("handoff_state") or existing.get("handoff_state") or "accepted"),
                "evidence_readiness": str(existing.get("evidence_readiness") or "decision-grade"),
                "action_readiness": str(existing.get("action_readiness") or "research-only"),
                "confidence": str(args.get("confidence") or existing.get("confidence") or "high"),
                "confidence_basis": str(existing.get("confidence_basis") or "Authenticated fixture evidence."),
                "missing_evidence": list(args.get("missing_evidence") or existing.get("missing_evidence") or []),
                "blocked_actions": list(args.get("blocked_actions") or existing.get("blocked_actions") or []),
            },
            "lineage": {
                "workflow_run_id": str(args.get("workflow_run_id") or existing.get("workflow_run_id") or ""),
                "knowledge_cutoff": str(args.get("knowledge_cutoff") or existing.get("knowledge_cutoff") or ""),
                "input_artifact_ids": list(args.get("input_artifact_ids") or existing.get("input_artifact_ids") or []),
                "source_snapshot_ids": list(args.get("source_snapshot_ids") or existing.get("source_snapshot_ids") or []),
                "dataset_ids": list(args.get("dataset_ids") or existing.get("dataset_ids") or []),
                "calculation_run_ids": list(args.get("calculation_run_ids") or existing.get("calculation_run_ids") or []),
                "evidence_lane": str(args.get("evidence_lane") or existing.get("evidence_lane") or "live_forward"),
            },
            "requirements": list(args.get("requirements") or existing.get("requirements") or []),
        }
        if artifact_type != "synthesis_report":
            enriched["artifact_id"] = artifact_id
        for field in ("export_path", "path", "metadata"):
            if field in args:
                enriched[field] = args[field]
        args = enriched
    return _runtime_call_mcp_tool(workspace_root, name, args, **kwargs)


def test_accepted_run_bound_artifact_requires_strict_quality_before_publication(
    tmp_path: Path,
) -> None:
    malformed_follow_up = _artifact_args("invalid-follow-up")
    malformed_follow_up["follow_up_requests"] = ["Retrieve the missing filing."]

    with pytest.raises(ValueError, match=r"follow_up_requests\[0\] must be object"):
        call_mcp_tool(
            tmp_path,
            "create_research_artifact",
            malformed_follow_up,
            transport_principal="fundamental-analyst",
        )

    invalid_quality = _artifact_args("missing-confidence-basis")
    invalid_quality["status"].pop("confidence_basis")

    with pytest.raises(
        ValueError,
        match="requires confidence_basis",
    ):
        call_mcp_tool(
            tmp_path,
            "create_research_artifact",
            invalid_quality,
            transport_principal="fundamental-analyst",
        )

    assert not (
        tmp_path / "trading/reports/fundamental/invalid-follow-up.md"
    ).exists()
    assert not (
        tmp_path / "trading/reports/fundamental/missing-confidence-basis.md"
    ).exists()
    binding_dir = tmp_path / ".tradingcodex/mainagent/runs" / RUN_ID / "artifact-bindings"
    assert not binding_dir.exists() or not list(binding_dir.glob("*.json"))


def test_synthesis_rejects_authenticated_input_without_accepted_handoff(
    tmp_path: Path,
) -> None:
    revise = _artifact_args("needs-revision")
    revise["status"]["handoff"] = "revise"
    revise["status"]["missing_evidence"] = ["A primary filing is still required."]
    call_mcp_tool(
        tmp_path,
        "create_research_artifact",
        revise,
        transport_principal="fundamental-analyst",
    )

    with pytest.raises(ValueError, match="is not an accepted handoff"):
        call_mcp_tool(
            tmp_path,
            "create_research_artifact",
            _artifact_args(
                "invalid-synthesis-input",
                "synthesis_report",
                inputs=["needs-revision"],
            ),
            transport_principal="head-manager",
        )


def _store_role_artifact(root: Path, artifact_id: str = "authenticated-source") -> dict[str, object]:
    call_mcp_tool(
        root,
        "create_research_artifact",
        _artifact_args(artifact_id),
        transport_principal="fundamental-analyst",
    )
    return get_research_artifact(root, {"artifact_id": artifact_id, "include_markdown": False})


def test_full_artifact_mcp_response_is_json_serializable(tmp_path: Path) -> None:
    call_mcp_tool(
        tmp_path,
        "create_research_artifact",
        _artifact_args("full-detail-json"),
        transport_principal="fundamental-analyst",
    )

    response = handle_mcp_rpc(
        tmp_path,
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "get_research_artifact",
                "arguments": {
                    "artifact_id": "full-detail-json",
                    "detail_level": "full",
                    "include_markdown": True,
                },
            },
        },
        transport_principal="fundamental-analyst",
    )

    assert response["result"]["isError"] is False
    artifact = json.loads(response["result"]["content"][0]["text"])
    assert artifact["artifact"]["id"] == "full-detail-json"
    assert artifact["markdown"]
    assert isinstance(artifact["artifact"]["lineage"]["recorded_at"], str)


def _workspace_file_snapshot(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _durable_workspace_file_snapshot(root: Path) -> dict[str, bytes]:
    return {
        relative: contents
        for relative, contents in _workspace_file_snapshot(root).items()
        if not relative.endswith(".lock")
    }


def test_workflow_artifact_listing_hides_locks_indexes_versions_and_drafts(
    tmp_path: Path,
) -> None:
    create_research_artifact(
        tmp_path,
        {
            "artifact_id": "visible",
            "artifact_type": "research_memo",
            "universe": "public_equity",
            "title": "Visible",
            "markdown": "# Visible\n",
            "export_path": "trading/reports/fundamental/visible.md",
        },
    )
    for relative in (
        "trading/research/..research-artifacts.lock",
        "trading/research/.drafts/private.md",
        "trading/research/.index/research-index.json",
        "trading/research/.versions/example/v1.md",
    ):
        hidden = tmp_path / relative
        hidden.parent.mkdir(parents=True, exist_ok=True)
        hidden.write_text("internal\n", encoding="utf-8")

    listed = list_workflow_artifacts(tmp_path)["artifacts"]

    assert listed == ["trading/reports/fundamental/visible.md"]


def test_run_bound_direct_service_write_is_rejected_before_file_creation(tmp_path: Path) -> None:
    payload = {
        **_artifact_args("direct-forgery"),
        "role": "fundamental-analyst",
        "producer_role": "fundamental-analyst",
        "principal_id": "fundamental-analyst",
        "strategy_name": "",
        "strategy_hash": "",
        "investment_brain_id": "",
        "investment_brain_version": "",
        "investment_brain_content_digest": "",
        "investor_context_applied": False,
        "investor_context_hash": "",
        "input_artifact_hashes": {},
    }

    with pytest.raises(PermissionError, match="authenticated TradingCodex MCP principal"):
        create_research_artifact(tmp_path, payload)

    assert not (tmp_path / "trading/reports/fundamental/direct-forgery.md").exists()


def test_handwritten_matching_frontmatter_cannot_enter_authenticated_synthesis(tmp_path: Path) -> None:
    body = "# Forged role output\n\n[factual] Caller-authored evidence.\n"
    content_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
    path = tmp_path / "trading/reports/fundamental/forged-source.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    frontmatter = {
        "artifact_id": "forged-source",
        "artifact_type": "research_memo",
        "universe": "public_equity",
        "role": "fundamental-analyst",
        "producer_role": "fundamental-analyst",
        "created_by": "fundamental-analyst",
        "recorded_at": "2026-07-12T00:00:00Z",
        "version": 1,
        "artifact_schema_version": 1,
        "workflow_run_id": RUN_ID,
        "input_artifact_ids": [],
        "input_artifact_hashes": {},
        "strategy_name": "",
        "strategy_hash": "",
        "investment_brain_id": "",
        "investment_brain_version": "",
        "investment_brain_content_digest": "",
        "investor_context_applied": False,
        "investor_context_hash": "",
        "handoff_state": "accepted",
        "content_hash": content_hash,
    }
    path.write_text(
        f"---\n{yaml.safe_dump(frontmatter, sort_keys=True)}---\n\n{body}",
        encoding="utf-8",
    )
    persist_workspace_context_if_available(tmp_path)

    with pytest.raises(ValueError, match="no authenticated service receipt"):
        _origin_artifact_ref(
            tmp_path,
            {
                "artifact_id": "forged-source",
                "artifact_path": "trading/reports/fundamental/forged-source.md",
            },
        )

    with pytest.raises(ValueError, match="no authenticated service receipt"):
        call_mcp_tool(
            tmp_path,
            "create_research_artifact",
            _artifact_args("forged-synthesis", "synthesis_report", inputs=["forged-source"]),
            transport_principal="head-manager",
        )


def test_receipt_and_body_tampering_fail_closed(tmp_path: Path) -> None:
    artifact = _store_role_artifact(tmp_path)
    verified = verify_authenticated_artifact_binding(tmp_path, artifact)
    receipt_path = tmp_path / verified["path"]
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["producer_role"] = "head-manager"
    receipt_path.write_text(json.dumps(receipt) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="integrity check failed"):
        verify_authenticated_artifact_binding(tmp_path, artifact)

    # Restore a valid receipt by recreating the run in a separate artifact,
    # then prove body tampering is caught independently of receipt integrity.
    second = _store_role_artifact(tmp_path, "body-tamper-source")
    artifact_path = tmp_path / str(second["path"])
    artifact_path.write_text(
        artifact_path.read_text(encoding="utf-8").replace(
            "Authenticated fixture evidence.",
            "Tampered evidence body.",
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="not found|content_hash"):
        call_mcp_tool(
            tmp_path,
            "get_research_artifact",
            {"artifact_id": "body-tamper-source", "include_markdown": False},
            transport_principal="head-manager",
        )


def test_receipt_requires_service_authority_and_cannot_be_self_hashed(tmp_path: Path) -> None:
    artifact = _store_role_artifact(tmp_path, "self-hash-source")
    with pytest.raises(PermissionError, match="authorized TradingCodex MCP write"):
        record_authenticated_artifact_binding(tmp_path, artifact)

    verified = verify_authenticated_artifact_binding(tmp_path, artifact)
    receipt_path = tmp_path / verified["path"]
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))

    run_path = tmp_path / f".tradingcodex/mainagent/runs/{RUN_ID}/run.json"
    run = json.loads(run_path.read_text(encoding="utf-8"))
    run["request_bytes"] = int(run["request_bytes"]) + 1
    run["record_hash"] = stable_hash(
        {key: value for key, value in run.items() if key != "record_hash"}
    )
    run_path.write_text(json.dumps(run, sort_keys=True) + "\n", encoding="utf-8")

    receipt["run_record_hash"] = run["record_hash"]
    receipt["receipt_hash"] = stable_hash(
        {key: value for key, value in receipt.items() if key != "receipt_hash"}
    )
    receipt_path.write_text(json.dumps(receipt, sort_keys=True) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="integrity check failed"):
        verify_authenticated_artifact_binding(tmp_path, artifact)


def test_receipt_failure_before_stable_publish_leaves_no_artifact(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def failing_prepare(
        root: Path | str,
        artifact: dict[str, object],
    ) -> dict[str, object]:
        path = Path(root) / str(artifact["path"])
        assert not path.exists()
        raise ValueError("injected receipt precommit failure")

    monkeypatch.setattr(
        artifact_bindings_module,
        "prepare_authenticated_artifact_binding_receipt",
        failing_prepare,
    )

    with pytest.raises(ValueError, match="injected receipt precommit failure"):
        call_mcp_tool(
            tmp_path,
            "create_research_artifact",
            _artifact_args("receipt-race-source"),
            transport_principal="fundamental-analyst",
        )

    assert not (
        tmp_path / "trading/reports/fundamental/receipt-race-source.md"
    ).exists()
    assert not (tmp_path / "trading/research/.versions").exists()
    assert not (tmp_path / "trading/research/.index/research-index.json").exists()
    receipt_directory = (
        tmp_path
        / f".tradingcodex/mainagent/runs/{RUN_ID}/artifact-bindings"
    )
    assert not list(receipt_directory.glob("*.json"))


def test_authenticated_mcp_read_returns_the_verified_snapshot(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    artifact = _store_role_artifact(tmp_path, "single-read-source")
    original_get = research_module.get_research_artifact
    calls = 0

    def tamper_on_second_read(root: Path | str, args: dict[str, object]) -> dict[str, object]:
        nonlocal calls
        calls += 1
        if calls == 2:
            path = Path(root) / str(artifact["path"])
            path.write_text(
                path.read_text(encoding="utf-8").replace(
                    "\n---\n\n",
                    "\ntampered_metadata: true\n---\n\n",
                    1,
                ),
                encoding="utf-8",
            )
        return original_get(root, args)

    monkeypatch.setattr(research_module, "get_research_artifact", tamper_on_second_read)

    result = call_mcp_tool(
        tmp_path,
        "get_research_artifact",
        {"artifact_id": "single-read-source", "include_markdown": True},
        transport_principal="head-manager",
    )

    assert calls == 1
    assert "Authenticated fixture evidence." in result["markdown"]
    assert "tampered_metadata" not in (tmp_path / str(artifact["path"])).read_text(
        encoding="utf-8"
    )


def test_receipt_binds_exact_input_ids_as_well_as_hashes(tmp_path: Path) -> None:
    artifact = _store_role_artifact(tmp_path, "input-binding-source")
    altered = {**artifact, "input_artifact_ids": ["invented-input"]}

    with pytest.raises(ValueError, match="input ids and hashes must match exactly"):
        verify_authenticated_artifact_binding(tmp_path, altered)


def test_recursive_receipt_verification_resolves_exact_historical_inputs(
    tmp_path: Path,
) -> None:
    source = _store_role_artifact(tmp_path, "historical-lineage-source")
    call_mcp_tool(
        tmp_path,
        "create_research_artifact",
        _artifact_args(
            "historical-lineage-intermediate",
            inputs=["historical-lineage-source"],
        ),
        transport_principal="fundamental-analyst",
    )
    intermediate = get_research_artifact(
        tmp_path,
        {
            "artifact_id": "historical-lineage-intermediate",
            "include_markdown": False,
        },
    )
    intermediate_receipt = verify_authenticated_artifact_binding(
        tmp_path,
        intermediate,
    )
    intermediate_receipt_document = json.loads(
        (tmp_path / intermediate_receipt["path"]).read_text(encoding="utf-8")
    )
    assert intermediate_receipt_document["input_artifact_versions"] == {
        "historical-lineage-source": 1,
    }

    call_mcp_tool(
        tmp_path,
        "create_research_artifact",
        _artifact_args(
            "historical-lineage-synthesis",
            "synthesis_report",
            inputs=["historical-lineage-intermediate"],
        ),
        transport_principal="head-manager",
    )
    synthesis = get_research_artifact(
        tmp_path,
        {
            "artifact_id": f"synthesis-{RUN_ID}",
            "include_markdown": False,
        },
    )

    call_mcp_tool(
        tmp_path,
        "append_research_artifact_version",
        {
            "artifact_id": "historical-lineage-source",
            "markdown": (
                "# historical-lineage-source\n\n"
                "[factual] Authenticated evidence advanced to version two.\n"
            ),
        },
        transport_principal="fundamental-analyst",
    )
    current_source = get_research_artifact(
        tmp_path,
        {
            "artifact_id": "historical-lineage-source",
            "include_markdown": False,
        },
    )
    assert current_source["version"] == 2
    assert verify_authenticated_artifact_binding(tmp_path, current_source)["status"] == (
        "verified"
    )
    assert verify_authenticated_artifact_binding(tmp_path, synthesis)["status"] == (
        "verified"
    )

    archive = research_artifact_version_archive_path(
        tmp_path,
        str(source["artifact_id"]),
        int(source["version"]),
        str(source["content_hash"]),
    )
    archive.write_text(
        archive.read_text(encoding="utf-8").replace(
            "Authenticated fixture evidence.",
            "Tampered archived evidence.",
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="content_hash"):
        verify_authenticated_artifact_binding(tmp_path, synthesis)
    assert verify_authenticated_artifact_binding(tmp_path, current_source)["status"] == (
        "verified"
    )


def test_receipt_binds_exact_source_snapshot_hashes_and_revalidates_files(
    tmp_path: Path,
) -> None:
    snapshot = record_source_snapshot(
        tmp_path,
        {
            "provider": "test-provider",
            "source_category": "issuer-release",
            "source_locator": "https://example.test/acme-release",
            "known_at": "2026-07-11T00:00:00Z",
            "retrieved_at": "2026-07-11T00:00:00Z",
            "recorded_at": "2026-07-11T00:00:00Z",
            "coverage_note": "Deterministic receipt fixture.",
            "payload": {"claim": "original"},
            "principal_id": "fundamental-analyst",
        },
    )
    payload = _artifact_args("snapshot-bound-source")
    payload["lineage"]["source_snapshot_ids"] = [snapshot["snapshot_id"]]
    call_mcp_tool(
        tmp_path,
        "create_research_artifact",
        payload,
        transport_principal="fundamental-analyst",
    )
    artifact = get_research_artifact(
        tmp_path,
        {"artifact_id": "snapshot-bound-source", "include_markdown": False},
    )
    snapshot_path = tmp_path / snapshot["export_path"]
    snapshot_document = json.loads(snapshot_path.read_text(encoding="utf-8"))
    verified = verify_authenticated_artifact_binding(tmp_path, artifact)
    receipt = json.loads((tmp_path / verified["path"]).read_text(encoding="utf-8"))
    assert receipt["source_snapshot_hashes"] == {
        snapshot["snapshot_id"]: snapshot_document["snapshot_hash"]
    }
    synthesis_payload = _artifact_args(
        "snapshot-bound-synthesis",
        "synthesis_report",
        inputs=["snapshot-bound-source"],
    )
    call_mcp_tool(
        tmp_path,
        "create_research_artifact",
        synthesis_payload,
        transport_principal="head-manager",
    )
    synthesis = get_research_artifact(
        tmp_path,
        {"artifact_id": f"synthesis-{RUN_ID}", "include_markdown": False},
    )
    assert verify_authenticated_artifact_binding(tmp_path, synthesis)["status"] == "verified"

    snapshot_document["payload"] = {"claim": "rewritten"}
    snapshot_document["payload_hash"] = stable_hash(snapshot_document["payload"])
    snapshot_document["snapshot_hash"] = stable_hash({
        key: value
        for key, value in snapshot_document.items()
        if key not in {"snapshot_id", "snapshot_hash"}
    })
    snapshot_path.write_text(
        json.dumps(snapshot_document, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="snapshot_id does not match its content"):
        verify_authenticated_artifact_binding(tmp_path, artifact)
    with pytest.raises(ValueError, match="snapshot_id does not match its content"):
        verify_authenticated_artifact_binding(tmp_path, synthesis)


def test_receipt_cannot_be_replayed_into_another_workspace(tmp_path: Path) -> None:
    artifact = _store_role_artifact(tmp_path, "workspace-bound-source")
    other = tmp_path / "other-workspace"
    ensure_workspace_manifest(other)
    persist_workspace_context_if_available(other)
    shutil.copytree(tmp_path / "trading", other / "trading")
    shutil.copytree(
        tmp_path / ".tradingcodex/mainagent/runs",
        other / ".tradingcodex/mainagent/runs",
    )

    with pytest.raises(ValueError, match="does not match the current artifact"):
        verify_authenticated_artifact_binding(other, artifact)


def test_missing_or_replaced_global_key_never_resigns_historical_receipt(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    service_home = tmp_path.parent / f"{tmp_path.name}-service-home"
    monkeypatch.setenv("TRADINGCODEX_HOME", str(service_home))
    artifact = _store_role_artifact(tmp_path, "key-lifecycle-source")
    verified = verify_authenticated_artifact_binding(tmp_path, artifact)
    receipt_path = tmp_path / verified["path"]
    original_receipt = receipt_path.read_bytes()
    key_path = service_home / "state" / ARTIFACT_BINDING_SIGNING_KEY_FILE
    assert key_path.is_file()

    key_path.unlink()
    with pytest.raises(ValueError, match="signing key is unavailable"):
        verify_authenticated_artifact_binding(tmp_path, artifact)
    assert not key_path.exists()
    assert receipt_path.read_bytes() == original_receipt

    key_path.write_text("00" * 32 + "\n", encoding="ascii")
    if os.name != "nt":
        key_path.chmod(0o600)
    with pytest.raises(ValueError, match="integrity check failed"):
        verify_authenticated_artifact_binding(tmp_path, artifact)
    assert receipt_path.read_bytes() == original_receipt


def test_forecast_derives_and_verifies_run_binding_when_caller_omits_it(tmp_path: Path) -> None:
    artifact = _store_role_artifact(tmp_path, "forecast-origin")

    origin = _origin_artifact_ref(
        tmp_path,
        {
            "artifact_id": "forecast-origin",
            "artifact_path": artifact["path"],
        },
    )

    assert origin["workflow_run_id"] == RUN_ID
    assert origin["authentication"]["status"] == "verified"


def test_artifact_receipt_and_run_record_symlinks_fail_closed(tmp_path: Path) -> None:
    artifact_link = _store_role_artifact(tmp_path, "artifact-symlink-source")
    artifact_path = tmp_path / str(artifact_link["path"])
    moved_artifact = artifact_path.with_name(f"moved-{artifact_path.name}")
    artifact_path.rename(moved_artifact)
    try:
        artifact_path.symlink_to(moved_artifact.name)
    except OSError:
        moved_artifact.rename(artifact_path)
        pytest.skip("symlink creation is unavailable on this platform")

    with pytest.raises(ValueError, match="must not contain symlinks"):
        verify_authenticated_artifact_binding(tmp_path, artifact_link)
    artifact_path.unlink()
    moved_artifact.rename(artifact_path)

    receipt_artifact = _store_role_artifact(tmp_path, "receipt-symlink-source")
    receipt = verify_authenticated_artifact_binding(tmp_path, receipt_artifact)
    receipt_path = tmp_path / receipt["path"]
    moved_receipt = receipt_path.with_name(f"moved-{receipt_path.name}")
    receipt_path.rename(moved_receipt)
    try:
        receipt_path.symlink_to(moved_receipt.name)
    except OSError:
        moved_receipt.rename(receipt_path)
        pytest.skip("symlink creation is unavailable on this platform")

    with pytest.raises(ValueError, match="must not contain symlinks"):
        verify_authenticated_artifact_binding(tmp_path, receipt_artifact)

    run_artifact = _store_role_artifact(tmp_path, "run-symlink-source")
    run_path = tmp_path / f".tradingcodex/mainagent/runs/{RUN_ID}/run.json"
    moved_run = run_path.with_name("moved-run.json")
    run_path.rename(moved_run)
    run_path.symlink_to(moved_run.name)

    with pytest.raises(ValueError, match="must not contain symlinks"):
        verify_authenticated_artifact_binding(tmp_path, run_artifact)


def test_append_creates_new_receipt_and_direct_append_cannot_bypass_it(tmp_path: Path) -> None:
    original = _store_role_artifact(tmp_path, "versioned-source")
    first_receipt = verify_authenticated_artifact_binding(tmp_path, original)

    result = call_mcp_tool(
        tmp_path,
        "append_research_artifact_version",
        {
            "artifact_id": "versioned-source",
            "markdown": "# versioned-source\n\n[factual] Authenticated second version.\n",
            "workflow_run_id": RUN_ID,
            "input_artifact_ids": [],
        },
        transport_principal="fundamental-analyst",
    )
    updated = get_research_artifact(
        tmp_path,
        {"artifact_id": "versioned-source", "include_markdown": False},
    )
    second_receipt = verify_authenticated_artifact_binding(tmp_path, updated)
    assert result["artifact"]["lineage"]["version"] == 2
    assert first_receipt["path"] != second_receipt["path"]
    assert (tmp_path / first_receipt["path"]).is_file()
    assert (tmp_path / second_receipt["path"]).is_file()

    with pytest.raises(PermissionError, match="authenticated TradingCodex MCP principal"):
        append_research_artifact_version(
            tmp_path,
            {
                "artifact_id": "versioned-source",
                "markdown": "# versioned-source\n\n[factual] Direct third version.\n",
            },
        )


def test_append_may_bind_new_run_local_input_artifacts(tmp_path: Path) -> None:
    trigger = _store_role_artifact(tmp_path, "followup-trigger")
    target = _store_role_artifact(tmp_path, "followup-target")

    result = call_mcp_tool(
        tmp_path,
        "append_research_artifact_version",
        {
            "artifact_id": target["artifact_id"],
            "markdown": "# followup-target\n\n[factual] Authenticated cross-role follow-up.\n",
            "workflow_run_id": RUN_ID,
            "input_artifact_ids": [trigger["artifact_id"]],
        },
        transport_principal="fundamental-analyst",
    )

    updated = get_research_artifact(
        tmp_path,
        {"artifact_id": target["artifact_id"], "include_markdown": False},
    )
    assert result["artifact"]["lineage"]["version"] == 2
    assert updated["input_artifact_ids"] == [trigger["artifact_id"]]
    receipt = json.loads(
        (tmp_path / verify_authenticated_artifact_binding(tmp_path, updated)["path"]).read_text(encoding="utf-8")
    )
    assert receipt["input_artifact_hashes"] == {
        trigger["artifact_id"]: trigger["content_hash"]
    }
    assert verify_authenticated_artifact_binding(tmp_path, updated)["status"] == (
        "verified"
    )


@pytest.mark.parametrize("declaration_surface", ["arguments"])
def test_append_recomputes_source_snapshot_hashes_for_new_version(
    declaration_surface: str,
    tmp_path: Path,
) -> None:
    first = record_source_snapshot(
        tmp_path,
        {
            "provider": "test-provider",
            "source_category": "issuer-release",
            "source_locator": "https://example.test/first",
            "known_at": "2026-07-10T00:00:00Z",
            "retrieved_at": "2026-07-10T00:00:00Z",
            "recorded_at": "2026-07-10T00:00:00Z",
            "coverage_note": "Initial version evidence.",
            "payload": {"version": 1},
            "principal_id": "fundamental-analyst",
        },
    )
    second = record_source_snapshot(
        tmp_path,
        {
            "provider": "test-provider",
            "source_category": "issuer-release",
            "source_locator": "https://example.test/second",
            "known_at": "2026-07-11T00:00:00Z",
            "retrieved_at": "2026-07-11T00:00:00Z",
            "recorded_at": "2026-07-11T00:00:00Z",
            "coverage_note": "Follow-up version evidence.",
            "payload": {"version": 2},
            "principal_id": "fundamental-analyst",
        },
    )
    payload = _artifact_args("snapshot-followup-target")
    payload["lineage"]["source_snapshot_ids"] = [first["snapshot_id"]]
    target = call_mcp_tool(
        tmp_path,
        "create_research_artifact",
        payload,
        transport_principal="fundamental-analyst",
    )
    target_id = target["artifact"]["id"]

    lineage = {
        "source_snapshot_ids": [first["snapshot_id"], second["snapshot_id"]],
        "knowledge_cutoff": "2026-07-12T00:00:00Z",
    }
    append_payload: dict[str, object] = {
        "artifact_id": target_id,
        "markdown": "# snapshot-followup-target\n\n[factual] Updated evidence.\n",
        "workflow_run_id": RUN_ID,
    }
    if declaration_surface == "arguments":
        append_payload.update(lineage)
    else:
        append_payload["markdown"] = (
            "---\n"
            + yaml.safe_dump(lineage, sort_keys=False)
            + "---\n\n"
            + "# snapshot-followup-target\n\n[factual] Updated evidence.\n"
        )

    result = call_mcp_tool(
        tmp_path,
        "append_research_artifact_version",
        append_payload,
        transport_principal="fundamental-analyst",
    )

    updated = get_research_artifact(
        tmp_path,
        {"artifact_id": target_id, "include_markdown": False},
    )
    assert result["artifact"]["lineage"]["version"] == 2
    assert updated["source_snapshot_ids"] == lineage["source_snapshot_ids"]
    receipt = json.loads(
        (tmp_path / verify_authenticated_artifact_binding(tmp_path, updated)["path"]).read_text(encoding="utf-8")
    )
    assert set(receipt["source_snapshot_hashes"]) == {
        first["snapshot_id"],
        second["snapshot_id"],
    }
    assert verify_authenticated_artifact_binding(tmp_path, updated)["status"] == (
        "verified"
    )


@pytest.mark.parametrize(
    "path_source",
    ["argument-export", "argument-path", "metadata", "frontmatter"],
)
def test_append_rejects_every_noncanonical_path_declaration_without_mutation(
    path_source: str,
    tmp_path: Path,
) -> None:
    artifact = _store_role_artifact(tmp_path, "path-pinned-source")
    stable_path = tmp_path / str(artifact["path"])
    stable_bytes = stable_path.read_bytes()
    relocated = "trading/reports/fundamental/path-pinned-relocated.md"
    payload: dict[str, object] = {
        "artifact_id": "path-pinned-source",
        "markdown": "# path-pinned-source\n\n[factual] Must stay pinned.\n",
    }
    if path_source == "argument-export":
        payload["export_path"] = relocated
    elif path_source == "argument-path":
        payload["path"] = relocated
    elif path_source == "metadata":
        payload["metadata"] = {"export_path": relocated}
    else:
        payload["markdown"] = (
            "---\n"
            f'export_path: "{relocated}"\n'
            "---\n\n"
            "# path-pinned-source\n\n"
            "[factual] Must stay pinned.\n"
        )
    before = _durable_workspace_file_snapshot(tmp_path)

    with pytest.raises(ValueError, match="does not allow additional properties|must match its canonical path"):
        call_mcp_tool(
            tmp_path,
            "append_research_artifact_version",
            payload,
            transport_principal="fundamental-analyst",
        )

    assert _durable_workspace_file_snapshot(tmp_path) == before
    assert stable_path.read_bytes() == stable_bytes
    assert not (tmp_path / relocated).exists()
    assert verify_authenticated_artifact_binding(tmp_path, artifact)["status"] == (
        "verified"
    )


def test_create_cannot_overwrite_another_artifacts_canonical_path(
    tmp_path: Path,
) -> None:
    victim = _store_role_artifact(tmp_path, "path-collision-victim")
    victim_path = tmp_path / str(victim["path"])
    victim_bytes = victim_path.read_bytes()
    before = _durable_workspace_file_snapshot(tmp_path)
    payload = _artifact_args("path-collision-attacker")
    payload["export_path"] = str(victim["path"])

    with pytest.raises(ValueError, match="does not allow additional properties"):
        call_mcp_tool(
            tmp_path,
            "create_research_artifact",
            payload,
            transport_principal="fundamental-analyst",
        )

    assert _durable_workspace_file_snapshot(tmp_path) == before
    assert victim_path.read_bytes() == victim_bytes
    assert research_module.find_workspace_research_artifact_read_only(
        tmp_path,
        "path-collision-attacker",
    ) is None
    assert verify_authenticated_artifact_binding(tmp_path, victim)["status"] == (
        "verified"
    )


def test_create_cannot_relocate_an_existing_artifact_identity(tmp_path: Path) -> None:
    artifact = _store_role_artifact(tmp_path, "create-path-pinned-source")
    relocated = "trading/reports/fundamental/create-path-relocated.md"
    before = _durable_workspace_file_snapshot(tmp_path)
    payload = _artifact_args("create-path-pinned-source")
    payload["export_path"] = relocated

    with pytest.raises(ValueError, match="does not allow additional properties|must match its canonical path"):
        call_mcp_tool(
            tmp_path,
            "create_research_artifact",
            payload,
            transport_principal="fundamental-analyst",
        )

    assert _durable_workspace_file_snapshot(tmp_path) == before
    assert not (tmp_path / relocated).exists()
    assert verify_authenticated_artifact_binding(tmp_path, artifact)["status"] == (
        "verified"
    )


def test_append_receipt_commit_failure_rolls_back_all_durable_artifact_state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    artifact = _store_role_artifact(tmp_path, "receipt-rollback-source")
    before = _durable_workspace_file_snapshot(tmp_path)
    original_record = artifact_bindings_module.record_authenticated_artifact_binding

    def record_then_fail(
        root: Path | str,
        pending: dict[str, object],
    ) -> dict[str, str]:
        original_record(root, pending)
        raise RuntimeError("injected receipt commit failure")

    monkeypatch.setattr(
        artifact_bindings_module,
        "record_authenticated_artifact_binding",
        record_then_fail,
    )

    with pytest.raises(RuntimeError, match="injected receipt commit failure"):
        call_mcp_tool(
            tmp_path,
            "append_research_artifact_version",
            {
                "artifact_id": "receipt-rollback-source",
                "markdown": (
                    "# receipt-rollback-source\n\n"
                    "[factual] This version must be rolled back.\n"
                ),
            },
            transport_principal="fundamental-analyst",
        )

    assert _durable_workspace_file_snapshot(tmp_path) == before
    current = research_module.find_workspace_research_artifact_read_only(
        tmp_path,
        "receipt-rollback-source",
    )
    assert current is not None
    assert current["version"] == artifact["version"]
    assert current["content_hash"] == artifact["content_hash"]


@pytest.mark.parametrize("failure_source", ["run", "input", "snapshot"])
def test_append_precommit_validation_race_rolls_back_archive_and_stable(
    failure_source: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    artifact_id = f"precommit-{failure_source}-source"
    payload = _artifact_args(artifact_id)
    mutation_path: Path
    mutation_bytes: bytes

    if failure_source == "input":
        input_artifact = _store_role_artifact(tmp_path, "precommit-race-input")
        payload = _artifact_args(artifact_id, inputs=["precommit-race-input"])
        mutation_path = tmp_path / str(input_artifact["path"])
        mutation_bytes = mutation_path.read_bytes()
    elif failure_source == "snapshot":
        snapshot = record_source_snapshot(
            tmp_path,
            {
                "provider": "test-provider",
                "source_category": "issuer-release",
                "source_locator": "https://example.test/precommit-race",
                "known_at": "2026-07-11T00:00:00Z",
                "retrieved_at": "2026-07-11T00:00:00Z",
                "recorded_at": "2026-07-11T00:00:00Z",
                "coverage_note": "Precommit race fixture.",
                "payload": {"claim": "original"},
                "principal_id": "fundamental-analyst",
            },
        )
        payload["lineage"]["source_snapshot_ids"] = [snapshot["snapshot_id"]]
        mutation_path = tmp_path / str(snapshot["export_path"])
        mutation_bytes = mutation_path.read_bytes()
    else:
        mutation_path = (
            tmp_path / f".tradingcodex/mainagent/runs/{RUN_ID}/run.json"
        )
        mutation_bytes = mutation_path.read_bytes()

    call_mcp_tool(
        tmp_path,
        "create_research_artifact",
        payload,
        transport_principal="fundamental-analyst",
    )
    before = _durable_workspace_file_snapshot(tmp_path)
    original_prepare = (
        artifact_bindings_module.prepare_authenticated_artifact_binding_receipt
    )

    def mutate_during_prepare(
        root: Path | str,
        pending: dict[str, object],
    ) -> dict[str, object]:
        if failure_source == "input":
            mutated = mutation_bytes.decode("utf-8").replace(
                "Authenticated fixture evidence.",
                "Tampered input evidence.",
            )
        else:
            document = json.loads(mutation_bytes.decode("utf-8"))
            if failure_source == "run":
                document["request_bytes"] = int(document["request_bytes"]) + 1
            else:
                document["payload"] = {"claim": "tampered"}
            mutated = json.dumps(document, indent=2, sort_keys=True) + "\n"
        mutation_path.write_text(mutated, encoding="utf-8")
        try:
            return original_prepare(root, pending)
        finally:
            mutation_path.write_bytes(mutation_bytes)

    monkeypatch.setattr(
        artifact_bindings_module,
        "prepare_authenticated_artifact_binding_receipt",
        mutate_during_prepare,
    )

    with pytest.raises(ValueError):
        call_mcp_tool(
            tmp_path,
            "append_research_artifact_version",
            {
                "artifact_id": artifact_id,
                "markdown": (
                    f"# {artifact_id}\n\n"
                    "[factual] Must not publish after a precommit race.\n"
                ),
            },
            transport_principal="fundamental-analyst",
        )

    assert _durable_workspace_file_snapshot(tmp_path) == before


def test_append_publishes_receipt_before_stable_pointer_and_recovers_from_crash(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    original = _store_role_artifact(tmp_path, "crash-safe-source")
    stable_path = tmp_path / str(original["path"])
    original_stable_bytes = stable_path.read_bytes()
    original_atomic_write = research_module.atomic_write_text
    second_markdown = (
        "# crash-safe-source\n\n"
        "[factual] Receipt exists before stable publication.\n"
    )

    def crash_before_stable_publish(path: Path, text: str) -> None:
        if path == stable_path and "Receipt exists before stable publication." in text:
            receipts = list(
                (
                    tmp_path
                    / f".tradingcodex/mainagent/runs/{RUN_ID}/artifact-bindings"
                ).glob("*.json")
            )
            assert any(
                json.loads(receipt.read_text(encoding="utf-8"))["artifact_version"]
                == 2
                for receipt in receipts
            )
            raise KeyboardInterrupt("simulated process boundary")
        original_atomic_write(path, text)

    monkeypatch.setattr(
        research_module,
        "atomic_write_text",
        crash_before_stable_publish,
    )

    with pytest.raises(KeyboardInterrupt, match="simulated process boundary"):
        call_mcp_tool(
            tmp_path,
            "append_research_artifact_version",
            {
                "artifact_id": "crash-safe-source",
                "markdown": second_markdown,
            },
            transport_principal="fundamental-analyst",
        )

    assert stable_path.read_bytes() == original_stable_bytes
    current = research_module.find_workspace_research_artifact_read_only(
        tmp_path,
        "crash-safe-source",
    )
    assert current is not None
    assert current["version"] == 1
    assert verify_authenticated_artifact_binding(tmp_path, current)["status"] == "verified"

    monkeypatch.setattr(
        research_module,
        "atomic_write_text",
        original_atomic_write,
    )
    retried = call_mcp_tool(
        tmp_path,
        "append_research_artifact_version",
        {
            "artifact_id": "crash-safe-source",
            "markdown": second_markdown,
        },
        transport_principal="fundamental-analyst",
    )
    assert retried["artifact"]["lineage"]["version"] == 2
    published = research_module.find_workspace_research_artifact_read_only(
        tmp_path,
        "crash-safe-source",
    )
    assert published is not None
    assert verify_authenticated_artifact_binding(tmp_path, published)["status"] == "verified"


def test_append_reverifies_current_receipt_inside_research_lock(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    artifact = _store_role_artifact(tmp_path, "receipt-lock-race-source")
    verified = verify_authenticated_artifact_binding(tmp_path, artifact)
    receipt_path = tmp_path / verified["path"]
    receipt_bytes = receipt_path.read_bytes()
    before = _durable_workspace_file_snapshot(tmp_path)
    original_lock = research_module.exclusive_file_lock

    @contextmanager
    def remove_receipt_after_outer_check(
        path: Path,
        *,
        timeout_seconds: float = 5.0,
    ):
        with original_lock(path, timeout_seconds=timeout_seconds):
            if path.name == ".research-artifacts":
                receipt_path.unlink()
                try:
                    yield
                finally:
                    receipt_path.write_bytes(receipt_bytes)
            else:
                yield

    monkeypatch.setattr(
        research_module,
        "exclusive_file_lock",
        remove_receipt_after_outer_check,
    )

    with pytest.raises(ValueError, match="no authenticated service receipt"):
        call_mcp_tool(
            tmp_path,
            "append_research_artifact_version",
            {
                "artifact_id": "receipt-lock-race-source",
                "markdown": "# race\n\n[factual] Must never publish.\n",
            },
            transport_principal="fundamental-analyst",
        )

    assert _durable_workspace_file_snapshot(tmp_path) == before


def test_append_rejects_preplanted_mismatched_version_archive(tmp_path: Path) -> None:
    artifact = _store_role_artifact(tmp_path, "archive-poison-source")
    stable_path = tmp_path / str(artifact["path"])
    original_bytes = stable_path.read_bytes()
    archive = research_artifact_version_archive_path(
        tmp_path,
        str(artifact["artifact_id"]),
        int(artifact["version"]),
        str(artifact["content_hash"]),
    )
    archive.parent.mkdir(parents=True, exist_ok=True)
    archive.write_text("preplanted archive poison\n", encoding="utf-8")
    before = _durable_workspace_file_snapshot(tmp_path)

    with pytest.raises(ValueError, match="does not match current stable bytes"):
        call_mcp_tool(
            tmp_path,
            "append_research_artifact_version",
            {
                "artifact_id": "archive-poison-source",
                "markdown": "# next\n\n[factual] Must not publish.\n",
            },
            transport_principal="fundamental-analyst",
        )

    assert _durable_workspace_file_snapshot(tmp_path) == before
    assert stable_path.read_bytes() == original_bytes


def test_append_accepts_preexisting_exact_version_archive(tmp_path: Path) -> None:
    artifact = _store_role_artifact(tmp_path, "archive-idempotent-source")
    stable_path = tmp_path / str(artifact["path"])
    original_bytes = stable_path.read_bytes()
    archive = research_artifact_version_archive_path(
        tmp_path,
        str(artifact["artifact_id"]),
        int(artifact["version"]),
        str(artifact["content_hash"]),
    )
    archive.parent.mkdir(parents=True, exist_ok=True)
    archive.write_bytes(original_bytes)

    result = call_mcp_tool(
        tmp_path,
        "append_research_artifact_version",
        {
            "artifact_id": "archive-idempotent-source",
            "markdown": "# next\n\n[factual] Exact archive is idempotent.\n",
        },
        transport_principal="fundamental-analyst",
    )

    assert result["artifact"]["lineage"]["version"] == 2
    assert archive.read_bytes() == original_bytes


@pytest.mark.parametrize("symlink_kind", ["ancestor", "destination"])
def test_append_rejects_symlinked_version_archive_path(
    symlink_kind: str,
    tmp_path: Path,
) -> None:
    artifact = _store_role_artifact(tmp_path, f"archive-symlink-{symlink_kind}")
    stable_path = tmp_path / str(artifact["path"])
    original_bytes = stable_path.read_bytes()
    archive = research_artifact_version_archive_path(
        tmp_path,
        str(artifact["artifact_id"]),
        int(artifact["version"]),
        str(artifact["content_hash"]),
    )
    try:
        if symlink_kind == "ancestor":
            target = tmp_path / "trading/research/.archive-symlink-target"
            target.mkdir(parents=True)
            archive.parent.parent.symlink_to(target, target_is_directory=True)
        else:
            archive.parent.mkdir(parents=True, exist_ok=True)
            target = tmp_path / "trading/research/.archive-symlink-target.md"
            target.write_bytes(original_bytes)
            archive.symlink_to(target)
    except OSError:
        pytest.skip("symlink creation is unavailable on this platform")

    with pytest.raises(ValueError, match="symlink"):
        call_mcp_tool(
            tmp_path,
            "append_research_artifact_version",
            {
                "artifact_id": str(artifact["artifact_id"]),
                "markdown": "# next\n\n[factual] Must not follow symlinks.\n",
            },
            transport_principal="fundamental-analyst",
        )

    assert stable_path.read_bytes() == original_bytes


def test_tampered_current_artifact_cannot_be_laundered_by_append(
    tmp_path: Path,
) -> None:
    artifact = _store_role_artifact(tmp_path, "append-laundering-source")
    artifact_path = tmp_path / str(artifact["path"])
    original_file = artifact_path.read_bytes()
    original_text = original_file.decode("utf-8")
    original_body = get_research_artifact(
        tmp_path,
        {"artifact_id": "append-laundering-source", "include_markdown": True},
    )["markdown"]
    tampered_body = str(original_body).replace(
        "Authenticated fixture evidence.",
        "Tampered evidence prepared for laundering.",
    )
    tampered_hash = hashlib.sha256(tampered_body.encode("utf-8")).hexdigest()
    tampered_text = original_text.replace(
        "Authenticated fixture evidence.",
        "Tampered evidence prepared for laundering.",
    ).replace(str(artifact["content_hash"]), tampered_hash, 1)
    artifact_path.write_text(tampered_text, encoding="utf-8")
    before = _workspace_file_snapshot(tmp_path)

    with pytest.raises(
        ValueError,
        match="no authenticated service receipt|authenticated artifact receipt does not match",
    ):
        call_mcp_tool(
            tmp_path,
            "append_research_artifact_version",
            {
                "artifact_id": "append-laundering-source",
                "markdown": "# append-laundering-source\n\n[factual] Attempted repair.\n",
            },
            transport_principal="fundamental-analyst",
        )

    assert _workspace_file_snapshot(tmp_path) == before
    assert not (
        tmp_path / "trading/research/.versions/append-laundering-source"
    ).exists()

    artifact_path.write_bytes(original_file)
    valid = call_mcp_tool(
        tmp_path,
        "append_research_artifact_version",
        {
            "artifact_id": "append-laundering-source",
            "markdown": (
                "# append-laundering-source\n\n"
                "[factual] Authenticated second version after restoring the source.\n"
            ),
        },
        transport_principal="fundamental-analyst",
    )
    current = get_research_artifact(
        tmp_path,
        {"artifact_id": "append-laundering-source", "include_markdown": False},
    )
    assert valid["artifact"]["lineage"]["version"] == 2
    assert verify_authenticated_artifact_binding(tmp_path, current)["status"] == "verified"


def test_removing_run_binding_cannot_downgrade_authenticated_append(
    tmp_path: Path,
) -> None:
    artifact = _store_role_artifact(tmp_path, "append-binding-removal")
    artifact_path = tmp_path / str(artifact["path"])
    text = artifact_path.read_text(encoding="utf-8")
    assert f'workflow_run_id: "{RUN_ID}"' in text
    artifact_path.write_text(
        text.replace(f'workflow_run_id: "{RUN_ID}"', 'workflow_run_id: ""', 1),
        encoding="utf-8",
    )
    before = _workspace_file_snapshot(tmp_path)

    with pytest.raises(ValueError, match="lost its authenticated workflow binding|workflow_run_id is too short"):
        call_mcp_tool(
            tmp_path,
            "append_research_artifact_version",
            {
                "artifact_id": "append-binding-removal",
                "markdown": "# append-binding-removal\n\n[factual] Attempted downgrade.\n",
            },
            transport_principal="fundamental-analyst",
        )

    assert _workspace_file_snapshot(tmp_path) == before


def test_duplicate_artifact_ids_are_ambiguous_and_rejected(tmp_path: Path) -> None:
    _store_role_artifact(tmp_path, "duplicate-source")
    original = get_research_artifact(
        tmp_path,
        {"artifact_id": "duplicate-source", "include_markdown": True},
    )
    duplicate = tmp_path / "trading/research/duplicate-source-copy.md"
    duplicate.parent.mkdir(parents=True, exist_ok=True)
    source_path = tmp_path / str(original["path"])
    duplicate.write_text(source_path.read_text(encoding="utf-8"), encoding="utf-8")

    with pytest.raises(ValueError, match="duplicate research artifact_id"):
        get_research_artifact(
            tmp_path,
            {"artifact_id": "duplicate-source", "include_markdown": False},
        )


def _store_canonical_research_artifact(
    root: Path,
    artifact_id: str,
) -> dict[str, object]:
    args = _artifact_args(artifact_id)
    call_mcp_tool(
        root,
        "create_research_artifact",
        args,
        transport_principal="fundamental-analyst",
    )
    return get_research_artifact(
        root,
        {"artifact_id": artifact_id, "include_markdown": False},
    )


def test_migrates_exact_legacy_report_export_copy(tmp_path: Path) -> None:
    source = _store_canonical_research_artifact(tmp_path, "legacy-export-source")
    source_path = tmp_path / str(source["path"])
    target = tmp_path / "trading/reports/news/legacy-export-source.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(source_path.read_bytes())

    with pytest.raises(ValueError, match="duplicate research artifact_id"):
        get_research_artifact(
            tmp_path,
            {"artifact_id": "legacy-export-source", "include_markdown": False},
        )

    migrated = research_module.migrate_legacy_research_artifact_exports(tmp_path)

    assert migrated == {
        "status": "migrated",
        "migrated_paths": ["trading/reports/news/legacy-export-source.md"],
    }
    assert research_module.is_research_artifact_export_copy(tmp_path, target)
    assert get_research_artifact(
        tmp_path,
        {"artifact_id": "legacy-export-source", "include_markdown": False},
    )["path"] == source["path"]
    assert [
        entry["path"]
        for entry in list_artifact_catalog(tmp_path)["items"]
        if entry["artifact_id"] == "legacy-export-source"
    ] == [source["path"]]


@pytest.mark.parametrize(
    "target_relative",
    (
        "trading/reports/news/legacy-report-origin-copy.md",
        "trading/research/legacy-report-origin-copy.md",
    ),
    ids=("report-to-report", "report-to-research"),
)
def test_migrates_receipt_proven_legacy_exports_from_report_roots(
    tmp_path: Path,
    target_relative: str,
) -> None:
    source = _store_role_artifact(tmp_path, "legacy-report-origin")
    source_path = tmp_path / str(source["path"])
    assert str(source["path"]).startswith("trading/reports/")
    target = tmp_path / target_relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(source_path.read_bytes())

    migrated = research_module.migrate_legacy_research_artifact_exports(tmp_path)

    assert migrated["migrated_paths"] == [target_relative]
    assert research_module.is_research_artifact_export_copy(tmp_path, target)
    assert get_research_artifact(
        tmp_path,
        {"artifact_id": "legacy-report-origin", "include_markdown": False},
    )["path"] == source["path"]


def test_migrates_a_receipt_proven_legacy_export_after_source_version_append(
    tmp_path: Path,
) -> None:
    source = _store_role_artifact(tmp_path, "legacy-appended-export")
    source_path = tmp_path / str(source["path"])
    first_version = source_path.read_bytes()
    call_mcp_tool(
        tmp_path,
        "append_research_artifact_version",
        {
            "artifact_id": "legacy-appended-export",
            "markdown": (
                "# legacy-appended-export\n\n"
                "[factual] Authenticated second-version evidence.\n"
            ),
            "workflow_run_id": RUN_ID,
            "input_artifact_ids": [],
        },
        transport_principal="fundamental-analyst",
    )
    target = tmp_path / "trading/research/legacy-appended-export-copy.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(first_version)

    migrated = research_module.migrate_legacy_research_artifact_exports(tmp_path)

    assert migrated["migrated_paths"] == [
        "trading/research/legacy-appended-export-copy.md"
    ]
    assert research_module.is_research_artifact_export_copy(tmp_path, target)
    current = get_research_artifact(
        tmp_path,
        {"artifact_id": "legacy-appended-export", "include_markdown": False},
    )
    assert current["path"] == source["path"]
    assert current["version"] == 2


def test_legacy_migration_rejects_an_exact_copy_without_a_verified_receipt(
    tmp_path: Path,
) -> None:
    source = create_research_artifact(
        tmp_path,
        {
            "artifact_id": "legacy-unproven-copy",
            "artifact_type": "research_memo",
            "universe": "public_equity",
            "title": "Legacy unproven copy",
            "markdown": "# Legacy unproven copy\n\nExact but unbound.\n",
            "export_path": "trading/research/legacy-unproven-copy.md",
        },
    )
    source_path = tmp_path / str(source["path"])
    target = tmp_path / "trading/reports/news/legacy-unproven-copy.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(source_path.read_bytes())
    before = _workspace_file_snapshot(tmp_path)

    with pytest.raises(ValueError, match="without a verified receipt"):
        research_module.migrate_legacy_research_artifact_exports(tmp_path)

    assert _workspace_file_snapshot(tmp_path) == before


def test_legacy_migration_rejects_a_copy_when_the_receipt_key_is_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    service_home = tmp_path.parent / f"{tmp_path.name}-legacy-export-home"
    monkeypatch.setenv("TRADINGCODEX_HOME", str(service_home))
    source = _store_role_artifact(tmp_path, "legacy-missing-key")
    source_path = tmp_path / str(source["path"])
    target = tmp_path / "trading/research/legacy-missing-key-copy.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(source_path.read_bytes())
    key_path = service_home / "state" / ARTIFACT_BINDING_SIGNING_KEY_FILE
    assert key_path.is_file()
    key_path.unlink()
    before = _workspace_file_snapshot(tmp_path)

    with pytest.raises(ValueError, match="without a verified receipt"):
        research_module.migrate_legacy_research_artifact_exports(tmp_path)

    assert _workspace_file_snapshot(tmp_path) == before


def test_legacy_migration_rejects_a_nonidentical_report_duplicate(
    tmp_path: Path,
) -> None:
    source = _store_canonical_research_artifact(tmp_path, "legacy-altered-source")
    source_path = tmp_path / str(source["path"])
    source_markdown = get_research_artifact(
        tmp_path,
        {"artifact_id": "legacy-altered-source", "include_markdown": True},
    )["markdown"]
    altered_markdown = str(source_markdown).replace(
        "Authenticated fixture evidence.",
        "Altered manual evidence.",
    )
    target = tmp_path / "trading/reports/news/legacy-altered-source.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        source_path.read_text(encoding="utf-8")
        .replace("Authenticated fixture evidence.", "Altered manual evidence.")
        .replace(
            str(source["content_hash"]),
            hashlib.sha256(altered_markdown.encode("utf-8")).hexdigest(),
            1,
        ),
        encoding="utf-8",
    )

    before = _workspace_file_snapshot(tmp_path)
    with pytest.raises(ValueError, match="without a verified receipt"):
        research_module.migrate_legacy_research_artifact_exports(tmp_path)

    assert _workspace_file_snapshot(tmp_path) == before


def test_verified_export_copy_does_not_shadow_its_canonical_artifact(
    tmp_path: Path,
) -> None:
    source = _store_role_artifact(tmp_path, "exported-source")
    exported = export_research_artifact_md(
        tmp_path,
        {
            "artifact_id": "exported-source",
            "export_path": "trading/reports/news/exported-source.md",
        },
    )
    target = tmp_path / str(exported["export_path"])
    source_path = tmp_path / str(source["path"])
    original_markdown = get_research_artifact(
        tmp_path,
        {"artifact_id": "exported-source", "include_markdown": True},
    )["markdown"]

    assert target.read_bytes() == source_path.read_bytes()
    assert get_research_artifact(
        tmp_path,
        {"artifact_id": "exported-source", "include_markdown": False},
    )["path"] == source["path"]
    assert research_module.find_workspace_research_artifact(
        tmp_path,
        "exported-source",
    )["path"] == source["path"]
    assert [
        entry["path"]
        for entry in list_artifact_catalog(tmp_path)["items"]
        if entry["artifact_id"] == "exported-source"
    ] == [source["path"]]

    call_mcp_tool(
        tmp_path,
        "append_research_artifact_version",
        {
            "artifact_id": "exported-source",
            "markdown": "# exported-source\n\n[factual] Updated authenticated evidence.\n",
            "workflow_run_id": RUN_ID,
            "input_artifact_ids": [],
        },
        transport_principal="fundamental-analyst",
    )
    assert get_research_artifact(
        tmp_path,
        {"artifact_id": "exported-source", "include_markdown": False},
    )["path"] == source["path"]

    original_text = target.read_text(encoding="utf-8")
    tampered_body = original_text.replace(
        "Authenticated fixture evidence.",
        "Tampered exported evidence.",
    )
    tampered_hash = hashlib.sha256(
        original_markdown.replace(
            "Authenticated fixture evidence.",
            "Tampered exported evidence.",
        ).encode("utf-8")
    ).hexdigest()
    target.write_text(
        tampered_body.replace(str(source["content_hash"]), tampered_hash, 1),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="duplicate research artifact_id"):
        get_research_artifact(
            tmp_path,
            {"artifact_id": "exported-source", "include_markdown": False},
        )


def test_fabricated_export_sidecar_with_source_receipt_is_exposed(
    tmp_path: Path,
) -> None:
    source = _store_role_artifact(tmp_path, "fabricated-export-source")
    source_path = tmp_path / str(source["path"])
    target = tmp_path / "trading/reports/news/fabricated-export-source.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(source_path.read_bytes())
    source_hash = hashlib.sha256(source_path.read_bytes()).hexdigest()
    target_relative = target.relative_to(tmp_path).as_posix()
    target.with_name(f".{target.name}.tcx-export.json").write_text(
        json.dumps(
            {
                "schema_version": 2,
                "marker": "tradingcodex-research-artifact-export",
                "workflow_run_id": RUN_ID,
                "artifact_id": source["artifact_id"],
                "source_path": source["path"],
                "export_path": target_relative,
                "version": source["version"],
                "content_hash": source["content_hash"],
                "source_file_sha256": source_hash,
                "export_file_sha256": source_hash,
                "sidecar_signature": "0" * 64,
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    assert not research_module.is_research_artifact_export_copy(tmp_path, target)
    with pytest.raises(ValueError, match="duplicate research artifact_id"):
        get_research_artifact(
            tmp_path,
            {"artifact_id": "fabricated-export-source", "include_markdown": False},
        )


def test_unbound_export_sidecar_cannot_replay_across_workspaces(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("TRADINGCODEX_HOME", str(tmp_path / "shared-service-home"))
    source_root = tmp_path / "workspace-a"
    replay_root = tmp_path / "workspace-b"
    ensure_workspace_manifest(source_root)
    ensure_workspace_manifest(replay_root)
    source = create_research_artifact(
        source_root,
        {
            "artifact_id": "unbound-export-source",
            "artifact_type": "research_memo",
            "universe": "public_equity",
            "title": "Unbound export source",
            "markdown": "# Unbound export source\n\nService-created export.\n",
            "export_path": "trading/research/unbound-export-source.md",
        },
    )
    exported = export_research_artifact_md(
        source_root,
        {
            "artifact_id": "unbound-export-source",
            "export_path": "trading/reports/news/unbound-export-source.md",
        },
    )
    source_path = source_root / str(source["path"])
    export_path = source_root / str(exported["export_path"])
    export_sidecar = export_path.with_name(f".{export_path.name}.tcx-export.json")

    assert research_module.is_research_artifact_export_copy(source_root, export_path)
    assert get_research_artifact(
        source_root,
        {"artifact_id": "unbound-export-source", "include_markdown": False},
    )["path"] == source["path"]

    replay_source = replay_root / str(source["path"])
    replay_export = replay_root / str(exported["export_path"])
    replay_source.parent.mkdir(parents=True, exist_ok=True)
    replay_export.parent.mkdir(parents=True, exist_ok=True)
    replay_source.write_bytes(source_path.read_bytes())
    replay_export.write_bytes(export_path.read_bytes())
    replay_export.with_name(f".{replay_export.name}.tcx-export.json").write_bytes(
        export_sidecar.read_bytes()
    )

    assert not research_module.is_research_artifact_export_copy(replay_root, replay_export)
    with pytest.raises(ValueError, match="duplicate research artifact_id"):
        get_research_artifact(
            replay_root,
            {"artifact_id": "unbound-export-source", "include_markdown": False},
        )


def test_run_bound_receipt_cannot_authorize_another_bound_workspace_export(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("TRADINGCODEX_HOME", str(tmp_path / "shared-service-home"))
    source_root = tmp_path / "workspace-a"
    replay_root = tmp_path / "workspace-b"
    ensure_workspace_manifest(source_root)
    begin_analysis_run(
        source_root,
        "Analyze a receipt replay boundary.",
        run_id=RUN_ID,
        apply_investor_context=False,
    )
    call_mcp_tool(
        source_root,
        "create_research_artifact",
        _artifact_args("bound-replay-source"),
        transport_principal="fundamental-analyst",
    )
    source = get_research_artifact(
        source_root,
        {"artifact_id": "bound-replay-source", "include_markdown": False},
    )
    receipt = verify_authenticated_artifact_binding(source_root, source)

    ensure_workspace_manifest(replay_root)
    persist_workspace_context_if_available(replay_root)
    replay_source = replay_root / str(source["path"])
    replay_receipt = replay_root / str(receipt["path"])
    replay_source.parent.mkdir(parents=True, exist_ok=True)
    replay_receipt.parent.mkdir(parents=True, exist_ok=True)
    replay_source.write_bytes((source_root / str(source["path"])).read_bytes())
    replay_receipt.write_bytes((source_root / str(receipt["path"])).read_bytes())
    replay_export = replay_root / "trading/reports/news/bound-replay-source.md"
    replay_sidecar = replay_export.with_name(f".{replay_export.name}.tcx-export.json")

    with pytest.raises(ValueError, match="authenticated receipt"):
        export_research_artifact_md(
            replay_root,
            {
                "artifact_id": "bound-replay-source",
                "export_path": replay_export.relative_to(replay_root).as_posix(),
            },
        )

    assert not replay_export.exists()
    assert not replay_sidecar.exists()


def test_workspace_context_rejects_a_copied_workspace_identity(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("TRADINGCODEX_HOME", str(tmp_path / "shared-service-home"))
    source_root = tmp_path / "workspace-a"
    copied_root = tmp_path / "workspace-b"
    ensure_workspace_manifest(source_root)
    persist_workspace_context_if_available(source_root)
    shutil.copytree(source_root, copied_root)

    with pytest.raises(ValueError, match="workspace_id is already bound"):
        persist_workspace_context_if_available(copied_root)


@pytest.mark.parametrize("detail_level", ("review", "card"))
def test_run_bound_receipts_reject_a_copied_workspace_identity_on_every_read_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    detail_level: str,
) -> None:
    monkeypatch.setenv("TRADINGCODEX_HOME", str(tmp_path / "shared-service-home"))
    source_root = tmp_path / "workspace-a"
    copied_root = tmp_path / "workspace-b"
    ensure_workspace_manifest(source_root)
    begin_analysis_run(
        source_root,
        "Verify copied workspace receipt protection.",
        run_id=RUN_ID,
        apply_investor_context=False,
    )
    call_mcp_tool(
        source_root,
        "create_research_artifact",
        _artifact_args("copied-identity-source"),
        transport_principal="fundamental-analyst",
    )
    artifact = get_research_artifact(
        source_root,
        {"artifact_id": "copied-identity-source", "include_markdown": False},
    )
    assert verify_authenticated_artifact_binding(source_root, artifact)["status"] == "verified"
    shutil.copytree(source_root, copied_root)

    with pytest.raises(ValueError, match="workspace_id is already bound"):
        verify_authenticated_artifact_binding(copied_root, artifact)
    with pytest.raises(ValueError, match="workspace_id is already bound"):
        call_mcp_tool(
            copied_root,
            "get_research_artifact",
            {
                "artifact_id": "copied-identity-source",
                "detail_level": detail_level,
            },
            transport_principal="head-manager",
        )


def test_export_refreshes_a_verified_copy_after_the_source_version_changes(
    tmp_path: Path,
) -> None:
    source = _store_role_artifact(tmp_path, "refresh-export-source")
    destination = "trading/reports/news/refresh-export-source.md"
    export_research_artifact_md(
        tmp_path,
        {"artifact_id": "refresh-export-source", "export_path": destination},
    )
    call_mcp_tool(
        tmp_path,
        "append_research_artifact_version",
        {
            "artifact_id": "refresh-export-source",
            "markdown": "# refresh-export-source\n\n[factual] Updated authenticated evidence.\n",
            "workflow_run_id": RUN_ID,
            "input_artifact_ids": [],
        },
        transport_principal="fundamental-analyst",
    )

    refreshed = export_research_artifact_md(
        tmp_path,
        {"artifact_id": "refresh-export-source", "export_path": destination},
    )
    current = get_research_artifact(
        tmp_path,
        {"artifact_id": "refresh-export-source", "include_markdown": False},
    )
    target = tmp_path / str(refreshed["export_path"])
    assert target.read_bytes() == (tmp_path / str(current["path"])).read_bytes()
    assert research_module.is_research_artifact_export_copy(tmp_path, target)
    assert current["path"] == source["path"]


def test_export_rolls_back_when_manifest_publication_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _store_role_artifact(tmp_path, "export-rollback-source")
    target = tmp_path / "trading/reports/news/export-rollback-source.md"
    manifest = target.with_name(f".{target.name}.tcx-export.json")

    def fail_manifest(*_args: object, **_kwargs: object) -> None:
        raise OSError("manifest write failed")

    monkeypatch.setattr(
        research_module,
        "_write_research_artifact_export_manifest",
        fail_manifest,
    )

    with pytest.raises(OSError, match="manifest write failed"):
        export_research_artifact_md(
            tmp_path,
            {
                "artifact_id": "export-rollback-source",
                "export_path": target.relative_to(tmp_path).as_posix(),
            },
        )

    assert not target.exists()
    assert not manifest.exists()
    assert get_research_artifact(
        tmp_path,
        {"artifact_id": "export-rollback-source", "include_markdown": False},
    )["path"] == source["path"]


def test_export_rejects_an_occupied_canonical_destination(tmp_path: Path) -> None:
    source = _store_role_artifact(tmp_path, "export-collision-source")
    victim = _store_role_artifact(tmp_path, "export-collision-victim")
    victim_path = tmp_path / str(victim["path"])
    victim_bytes = victim_path.read_bytes()

    with pytest.raises(ValueError, match="export destination is occupied"):
        export_research_artifact_md(
            tmp_path,
            {
                "artifact_id": "export-collision-source",
                "export_path": victim["path"],
            },
        )

    assert victim_path.read_bytes() == victim_bytes
    assert get_research_artifact(
        tmp_path,
        {"artifact_id": "export-collision-source", "include_markdown": False},
    )["path"] == source["path"]


def test_export_rejects_a_version_archive_destination(tmp_path: Path) -> None:
    source = _store_role_artifact(tmp_path, "export-archive-source")
    call_mcp_tool(
        tmp_path,
        "append_research_artifact_version",
        {
            "artifact_id": "export-archive-source",
            "markdown": "# export-archive-source\n\n[factual] Updated authenticated evidence.\n",
            "workflow_run_id": RUN_ID,
            "input_artifact_ids": [],
        },
        transport_principal="fundamental-analyst",
    )
    archive = research_artifact_version_archive_path(
        tmp_path,
        "export-archive-source",
        1,
        str(source["content_hash"]),
    )
    archive_bytes = archive.read_bytes()

    with pytest.raises(ValueError, match="reserved directory"):
        export_research_artifact_md(
            tmp_path,
            {
                "artifact_id": "export-archive-source",
                "export_path": archive.relative_to(tmp_path).as_posix(),
            },
        )

    assert archive.read_bytes() == archive_bytes

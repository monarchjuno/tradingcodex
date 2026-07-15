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
    get_research_artifact,
    list_workflow_artifacts,
    record_source_snapshot,
    research_artifact_version_archive_path,
)
from tradingcodex_service.application.runtime import ensure_workspace_manifest
from tradingcodex_service.mcp_runtime import call_mcp_tool


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
    return {
        "artifact_id": artifact_id,
        "artifact_type": artifact_type,
        "universe": "public_equity",
        "workflow_type": "authentication_test",
        "title": artifact_id,
        "markdown": markdown or f"# {artifact_id}\n\n[factual] Authenticated fixture evidence.\n",
        "source_as_of": "2026-07-12",
        "knowledge_cutoff": "2026-07-12T00:00:00Z",
        "evidence_lane": "live_forward",
        "readiness_label": "accepted",
        "context_summary": "Authentication fixture.",
        "reader_summary": "Authentication fixture.",
        "handoff_state": "accepted",
        "confidence": "high",
        "missing_evidence": [],
        "next_recipient": "head-manager",
        "next_action": "Verify the receipt.",
        "blocked_actions": ["order", "execution"],
        "source_snapshot_ids": [],
        "workflow_run_id": RUN_ID,
        "input_artifact_ids": inputs or [],
    }


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

    invalid_quality = _artifact_args("missing-next-recipient")
    invalid_quality.pop("next_recipient")

    with pytest.raises(
        ValueError,
        match="accepted run-bound research artifact failed strict quality",
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
        tmp_path / "trading/reports/fundamental/missing-next-recipient.md"
    ).exists()
    binding_dir = tmp_path / ".tradingcodex/mainagent/runs" / RUN_ID / "artifact-bindings"
    assert not binding_dir.exists() or not list(binding_dir.glob("*.json"))


def test_synthesis_rejects_authenticated_input_without_accepted_handoff(
    tmp_path: Path,
) -> None:
    revise = _artifact_args("needs-revision")
    revise["handoff_state"] = "revise"
    revise["missing_evidence"] = ["A primary filing is still required."]
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
            "artifact_id": "historical-lineage-synthesis",
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
    payload["source_snapshot_ids"] = [snapshot["snapshot_id"]]
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
    assert artifact["source_snapshot_hashes"] == {
        snapshot["snapshot_id"]: snapshot_document["snapshot_hash"]
    }
    verified = verify_authenticated_artifact_binding(tmp_path, artifact)
    receipt = json.loads((tmp_path / verified["path"]).read_text(encoding="utf-8"))
    assert receipt["source_snapshot_hashes"] == artifact["source_snapshot_hashes"]
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
        {"artifact_id": "snapshot-bound-synthesis", "include_markdown": False},
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
    assert result["version"] == 2
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

    with pytest.raises(ValueError, match="must match its canonical path"):
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

    with pytest.raises(ValueError, match="belongs to another artifact"):
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

    with pytest.raises(ValueError, match="must match its canonical path"):
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
        payload["source_snapshot_ids"] = [snapshot["snapshot_id"]]
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
    assert retried["version"] == 2
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

    assert result["version"] == 2
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
            {"artifact_id": "append-laundering-source"},
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
    assert valid["version"] == 2
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

    with pytest.raises(ValueError, match="lost its authenticated workflow binding"):
        call_mcp_tool(
            tmp_path,
            "append_research_artifact_version",
            {"artifact_id": "append-binding-removal"},
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

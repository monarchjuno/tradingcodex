from __future__ import annotations

import json
import hmac
import os
from pathlib import Path

import pytest

from tradingcodex_service.application.datasets import (
    DATASET_MANIFEST_ROOT,
    DATASET_OBJECT_ROOT,
    get_dataset_manifest,
    materialize_dataset_slice,
    profile_dataset,
    record_dataset_snapshot,
    search_datasets,
    validate_dataset_manifest,
    withdraw_dataset_snapshot,
)
from tradingcodex_service.application.research import record_source_snapshot
from tradingcodex_service.application.runtime import ensure_workspace_manifest


pytest.importorskip("pyarrow", reason="dataset tests require the pinned PyArrow runtime")


def _snapshot(root: Path) -> dict[str, object]:
    return record_source_snapshot(
        root,
        {
            "provider": "test-provider",
            "source_category": "market_data",
            "source_locator": "test://prices",
            "provider_query": {"symbol": "BTCUSD"},
            "known_at": "2026-01-03T00:00:00Z",
            "as_of": "2026-01-03T00:00:00Z",
            "payload": {"request_hash": "abc"},
            "principal_id": "technical-analyst",
        },
    )


def _args(snapshot_id: str, *, title: str = "BTC daily prices") -> dict[str, object]:
    return {
        "source_filename": "prices.csv",
        "title": title,
        "description": "Point-in-time BTC closes used for volatility analysis.",
        "tags": ["crypto", "daily"],
        "provider": "test-provider",
        "provider_query": {"symbol": "BTCUSD", "interval": "1d"},
        "source_snapshot_ids": [snapshot_id],
        "knowledge_cutoff": "2026-01-04T00:00:00Z",
        "as_of": "2026-01-03T00:00:00Z",
        "vintage": "2026-01-03-final",
        "period_start": "2026-01-01T00:00:00Z",
        "period_end": "2026-01-03T00:00:00Z",
        "timezone": "UTC",
        "frequency": "1d",
        "symbols": ["BTCUSD"],
        "universe_membership_policy": "single_requested_instrument_point_in_time",
        "universe_membership": {"BTCUSD": {"included": True, "known_at": "2026-01-03T00:00:00Z"}},
        "adjustment_policy": "unadjusted",
        "columns": [
            {"name": "timestamp", "type": "timestamp", "nullable": False},
            {"name": "symbol", "type": "string", "nullable": False},
            {"name": "close", "type": "float64", "nullable": False, "unit": "price", "currency": "USD"},
        ],
        "principal_id": "technical-analyst",
    }


@pytest.fixture
def workspace_and_scratch(tmp_path: Path) -> tuple[Path, Path, str]:
    root = (tmp_path / "workspace").resolve()
    scratch = (tmp_path / "scratch").resolve()
    scratch.mkdir(parents=True)
    ensure_workspace_manifest(root)
    snapshot = _snapshot(root)
    (scratch / "prices.csv").write_text(
        "timestamp,symbol,close\n"
        "2026-01-01T00:00:00Z,BTCUSD,100\n"
        "2026-01-02T00:00:00Z,BTCUSD,110\n"
        "2026-01-03T00:00:00Z,BTCUSD,121\n",
        encoding="utf-8",
    )
    return root, scratch, str(snapshot["snapshot_id"])


def test_dataset_ingest_is_immutable_idempotent_searchable_and_sliceable(
    workspace_and_scratch: tuple[Path, Path, str],
) -> None:
    root, scratch, snapshot_id = workspace_and_scratch
    created = record_dataset_snapshot(root, _args(snapshot_id), scratch_root=scratch)
    repeated = record_dataset_snapshot(root, _args(snapshot_id), scratch_root=scratch)
    assert created["status"] == "recorded"
    assert repeated["status"] == "existing"
    assert repeated["dataset_id"] == created["dataset_id"]

    manifest_response = get_dataset_manifest(root, {"dataset_id": created["dataset_id"]})
    manifest = manifest_response["dataset"]
    assert validate_dataset_manifest(manifest) is manifest
    assert manifest_response["payload_available"] is True
    assert manifest["payload"]["row_count"] == 3
    assert manifest["source_snapshot_ids"] == [snapshot_id]
    assert (root / DATASET_MANIFEST_ROOT / f"{created['dataset_id']}.json").is_file()
    assert (root / DATASET_OBJECT_ROOT / f"{created['payload_hash']}.parquet").is_file()

    search = search_datasets(root, {"query": "volatility", "limit": 20})
    assert [item["object_id"] for item in search["datasets"]] == [created["dataset_id"]]
    assert search["datasets"][0]["details"]["column_names"] == ["timestamp", "symbol", "close"]
    assert "payload" not in search["datasets"][0]
    structured = search_datasets(
        root,
        {
            "provider": "test-provider",
            "symbol": "BTCUSD",
            "column": "close",
            "frequency": "1d",
            "period_start": "2026-01-02T00:00:00Z",
            "period_end": "2026-01-02T00:00:00Z",
        },
    )
    assert [item["object_id"] for item in structured["datasets"]] == [created["dataset_id"]]

    profile = profile_dataset(root, {"dataset_id": created["dataset_id"], "columns": ["close"], "sample_rows": 50})
    assert profile["columns"][0]["min"] == 100.0
    assert profile["columns"][0]["max"] == 121.0
    assert len(profile["sample"]) == 3

    sliced = materialize_dataset_slice(
        root,
        {
            "dataset_id": created["dataset_id"],
            "columns": ["timestamp", "close"],
            "time_column": "timestamp",
            "start": "2026-01-02T00:00:00Z",
            "max_rows": 2,
        },
        scratch_root=scratch,
    )
    assert sliced["row_count"] == 2
    assert Path(sliced["filename"]).name == sliced["filename"]
    assert (scratch / sliced["filename"]).is_file()
    sidecar = json.loads((scratch / sliced["sidecar_filename"]).read_text(encoding="utf-8"))
    assert sidecar == {
        "schema_version": 1,
        "artifact_type": "dataset_materialization",
        "materialization_id": sliced["materialization_id"],
        "dataset_id": created["dataset_id"],
        "manifest_hash": manifest["manifest_hash"],
        "payload_hash": created["payload_hash"],
        "selector": sliced["selector"],
        "filename": sliced["filename"],
        "row_count": 2,
        "size_bytes": sliced["size_bytes"],
        "content_hash": sliced["content_hash"],
        "signature_algorithm": "hmac-sha256",
        "proof_hash": sidecar["proof_hash"],
    }
    from tradingcodex_service.application.artifact_bindings import _receipt_signature

    claimed_proof = sidecar.pop("proof_hash")
    assert hmac.compare_digest(
        claimed_proof,
        _receipt_signature(root, sidecar, create_signing_key=False),
    )


def test_dataset_rejects_unsafe_sources_and_withdrawal_is_explicit(
    workspace_and_scratch: tuple[Path, Path, str],
) -> None:
    root, scratch, snapshot_id = workspace_and_scratch
    with pytest.raises(ValueError, match="basename-only"):
        record_dataset_snapshot(root, {**_args(snapshot_id), "source_filename": "../prices.csv"}, scratch_root=scratch)

    hardlink = scratch / "linked.csv"
    try:
        os.link(scratch / "prices.csv", hardlink)
    except OSError:
        hardlink = None
    if hardlink is not None:
        with pytest.raises(ValueError, match="single-link"):
            record_dataset_snapshot(root, {**_args(snapshot_id), "source_filename": "linked.csv"}, scratch_root=scratch)
        hardlink.unlink()

    created = record_dataset_snapshot(root, _args(snapshot_id), scratch_root=scratch)
    with pytest.raises(ValueError, match="confirmed_by_user"):
        withdraw_dataset_snapshot(root, {"dataset_id": created["dataset_id"], "reason": "license"})
    with pytest.raises(ValueError, match="reason_code"):
        withdraw_dataset_snapshot(
            root,
            {
                "dataset_id": created["dataset_id"],
                "reason_code": "cleanup",
                "reason": "routine cleanup",
                "confirmed_by_user": True,
            },
        )
    assert (root / DATASET_OBJECT_ROOT / f"{created['payload_hash']}.parquet").is_file()
    withdrawn = withdraw_dataset_snapshot(
        root,
        {
            "dataset_id": created["dataset_id"],
            "reason_code": "license",
            "reason": "provider license requires deletion",
            "confirmed_by_user": True,
            "principal_id": "user",
        },
    )
    assert withdrawn["payload_removed"] is True
    assert get_dataset_manifest(root, {"dataset_id": created["dataset_id"]})["withdrawn"] is True
    assert get_dataset_manifest(root, {"dataset_id": created["dataset_id"]})["payload_available"] is False
    assert search_datasets(root)["datasets"] == []
    with pytest.raises(ValueError, match="withdrawn"):
        materialize_dataset_slice(root, {"dataset_id": created["dataset_id"]}, scratch_root=scratch)


def test_dataset_manifest_tampering_and_nonfinite_values_fail_closed(
    workspace_and_scratch: tuple[Path, Path, str],
) -> None:
    root, scratch, snapshot_id = workspace_and_scratch
    created = record_dataset_snapshot(root, _args(snapshot_id), scratch_root=scratch)
    path = root / created["manifest_path"]
    document = json.loads(path.read_text(encoding="utf-8"))
    document["title"] = "tampered"
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ValueError, match="dataset_id does not match|manifest hash mismatch"):
        get_dataset_manifest(root, {"dataset_id": created["dataset_id"]})

    (scratch / "bad.csv").write_text("value\nNaN\n", encoding="utf-8")
    with pytest.raises(ValueError, match="NaN or Infinity"):
        record_dataset_snapshot(
            root,
            {
                **_args(snapshot_id, title="Bad values"),
                "source_filename": "bad.csv",
                "columns": [{"name": "value", "type": "float64", "nullable": False}],
            },
            scratch_root=scratch,
        )


def test_dataset_catalog_revalidates_changed_source_lineage(
    workspace_and_scratch: tuple[Path, Path, str],
) -> None:
    root, scratch, snapshot_id = workspace_and_scratch
    created = record_dataset_snapshot(root, _args(snapshot_id), scratch_root=scratch)
    assert [
        card["object_id"]
        for card in search_datasets(root, {"query": "volatility"})["datasets"]
    ] == [created["dataset_id"]]

    source_path = root / "trading/research/source-snapshots" / f"{snapshot_id}.json"
    original = source_path.read_bytes()
    document = json.loads(original)
    document["provider"] = "tampered-provider"
    source_path.write_text(json.dumps(document), encoding="utf-8")
    assert search_datasets(root, {"query": "volatility"})["datasets"] == []

    source_path.write_bytes(original)
    recovered = search_datasets(root, {"query": "volatility"})
    assert [card["object_id"] for card in recovered["datasets"]] == [created["dataset_id"]]


def test_withdrawal_preserves_a_shared_content_addressed_payload(
    workspace_and_scratch: tuple[Path, Path, str],
) -> None:
    root, scratch, snapshot_id = workspace_and_scratch
    first = record_dataset_snapshot(root, _args(snapshot_id, title="BTC prices primary"), scratch_root=scratch)
    second = record_dataset_snapshot(root, _args(snapshot_id, title="BTC prices alternate view"), scratch_root=scratch)
    assert first["dataset_id"] != second["dataset_id"]
    assert first["payload_hash"] == second["payload_hash"]
    payload = root / DATASET_OBJECT_ROOT / f"{first['payload_hash']}.parquet"

    first_withdrawal = withdraw_dataset_snapshot(
        root,
        {
            "dataset_id": first["dataset_id"],
            "reason_code": "legal",
            "reason": "withdraw first manifest",
            "confirmed_by_user": True,
            "principal_id": "user",
        },
    )
    assert first_withdrawal["payload_removed"] is False
    assert payload.is_file()
    with pytest.raises(ValueError, match="withdrawn datasets cannot be profiled"):
        profile_dataset(root, {"dataset_id": first["dataset_id"]})
    limited = search_datasets(root, {"limit": 1})
    assert [card["object_id"] for card in limited["datasets"]] == [second["dataset_id"]]

    second_withdrawal = withdraw_dataset_snapshot(
        root,
        {
            "dataset_id": second["dataset_id"],
            "reason_code": "license",
            "reason": "withdraw final manifest",
            "confirmed_by_user": True,
            "principal_id": "user",
        },
    )
    assert second_withdrawal["payload_removed"] is True
    assert not payload.exists()


def test_dataset_rejects_backdated_source_and_parent_lineage(
    workspace_and_scratch: tuple[Path, Path, str],
) -> None:
    root, scratch, snapshot_id = workspace_and_scratch
    with pytest.raises(ValueError, match="before source snapshot known_at"):
        record_dataset_snapshot(
            root,
            {
                **_args(snapshot_id, title="Backdated source lineage"),
                "known_at": "2026-01-02T00:00:00Z",
            },
            scratch_root=scratch,
        )

    parent = record_dataset_snapshot(root, _args(snapshot_id), scratch_root=scratch)
    child_args = _args(snapshot_id, title="Backdated parent lineage")
    child_args.pop("source_snapshot_ids")
    child_args.update(
        {
            "parent_dataset_ids": [parent["dataset_id"]],
            "known_at": "2026-01-02T00:00:00Z",
            "knowledge_cutoff": "2026-01-02T00:00:00Z",
            "as_of": "2026-01-02T00:00:00Z",
            "period_end": "2026-01-02T00:00:00Z",
        }
    )
    with pytest.raises(ValueError, match="parent dataset .* later than dataset known_at"):
        record_dataset_snapshot(root, child_args, scratch_root=scratch)


def test_dataset_search_cards_bound_user_controlled_context(
    workspace_and_scratch: tuple[Path, Path, str],
) -> None:
    root, scratch, snapshot_id = workspace_and_scratch
    created = record_dataset_snapshot(
        root,
        {
            **_args(snapshot_id, title="T" * 1000),
            "description": "D" * 5000,
            "tags": [f"tag-{index}-" + "x" * 200 for index in range(40)],
        },
        scratch_root=scratch,
    )
    card = next(
        item
        for item in search_datasets(root, {"limit": 200})["datasets"]
        if item["object_id"] == created["dataset_id"]
    )
    assert len(card["title"]) <= 240
    assert len(card["summary"]) <= 800
    assert len(card["tags"]) <= 20
    assert all(len(tag) <= 100 for tag in card["tags"])

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tradingcodex_service.application.research_objects import (
    canonical_json_bytes,
    content_hash,
    derive_content_id,
    normalize_timestamp,
    read_regular_json,
    write_immutable_json,
)


def test_research_object_json_is_strict_stable_and_immutable(tmp_path: Path) -> None:
    left = {"b": [2, 1], "a": "한글"}
    right = {"a": "한글", "b": [2, 1]}
    assert canonical_json_bytes(left) == canonical_json_bytes(right)
    assert content_hash(left) == content_hash(right)
    assert derive_content_id("Dataset", left).startswith("dataset-")
    with pytest.raises(ValueError, match="non-JSON or non-finite"):
        canonical_json_bytes({"value": float("nan")})

    path = tmp_path / "object.json"
    assert write_immutable_json(path, left) is True
    assert write_immutable_json(path, right) is False
    with pytest.raises(ValueError, match="collision"):
        write_immutable_json(path, {"different": True})
    assert read_regular_json(path, label="object") == left


def test_research_object_paths_and_timestamps_fail_closed(tmp_path: Path) -> None:
    assert normalize_timestamp("2026-01-01T09:00:00+09:00", "known_at") == "2026-01-01T00:00:00Z"
    with pytest.raises(ValueError, match="timezone"):
        normalize_timestamp("2026-01-01", "known_at")

    target = tmp_path / "target.json"
    target.write_text(json.dumps({"ok": True}), encoding="utf-8")
    link = tmp_path / "link.json"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    with pytest.raises(ValueError, match="regular non-symlink"):
        read_regular_json(link, label="linked object")

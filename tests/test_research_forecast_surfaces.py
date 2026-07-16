from __future__ import annotations

import json
import tomllib
import uuid
from pathlib import Path

import pytest
from django.test import Client

from tradingcodex_cli.__main__ import WORKSPACE_COMMANDS
from tradingcodex_cli.commands.forecast import forecast as forecast_command
from tradingcodex_cli.commands.research import research as research_command
from tradingcodex_cli.generator import bootstrap_workspace
from tradingcodex_service.application import forecasting as forecasting_module
from tradingcodex_service.application import research as research_module
from tradingcodex_service.application.research import record_source_snapshot
from tradingcodex_service.application.runtime import ensure_runtime_database, ensure_workspace_manifest
from tradingcodex_service.mcp_runtime import SAFE_HOME_TOOL_NAMES, TOOL_REGISTRY, call_mcp_tool


@pytest.fixture(autouse=True)
def attached_workspace(tmp_path: Path) -> None:
    ensure_workspace_manifest(tmp_path)
    origin = tmp_path / "trading/research/research-artifact-1.md"
    origin.parent.mkdir(parents=True, exist_ok=True)
    origin.write_text(
        "---\nartifact_id: research-artifact-1\nartifact_type: research_memo\nuniverse: public_equity\n---\n# Research artifact\n\nCurrent-schema forecast origin.\n",
        encoding="utf-8",
    )


def _snapshot(
    root: Path,
    category: str,
    value: object,
    *,
    known_at: str = "2025-12-31T00:00:00Z",
    recorded_at: str = "2025-12-31T00:00:00Z",
) -> str:
    return record_source_snapshot(root, {
        "provider": "surface-test",
        "source_category": category,
        "known_at": known_at,
        "retrieved_at": recorded_at,
        "recorded_at": recorded_at,
        "payload": {"value": value},
        "principal_id": "fundamental-analyst",
    })["snapshot_id"]


def _research_spec_payload(spec_id: str) -> dict[str, object]:
    return {
        "spec_id": spec_id,
        "knowledge_cutoff": "2026-01-01T00:00:00Z",
        "method_profile": "quant_signal_v1",
        "hypothesis": "A preregistered signal has positive benchmark-relative value after costs.",
        "economic_mechanism": "The signal captures a delayed information response.",
        "research_type": "quantitative",
        "universe": "point-in-time listed equities",
        "universe_membership_rule": "Use only constituents known at the cutoff.",
        "target": "benchmark-relative return",
        "horizon": "90 days",
        "benchmark": "frozen market index snapshot",
        "holding_period": "90 days",
        "rebalance_rule": "monthly",
        "signal_definition": {"field": "signal", "lag_days": 1},
        "falsification_criteria": ["non-positive untouched out-of-sample return after costs"],
        "validation_plan": {"out_of_sample": "untouched", "walk_forward": True},
        "parameter_trial_budget": 2,
        "cost_assumptions": {"source": "frozen cost snapshot", "bps": 10},
        "capacity_assumptions": {"source": "frozen liquidity snapshot", "participation": 0.01},
        "resolution_rule": "Resolve from the frozen benchmark-relative return series.",
    }


def _forecast_payload(base_snapshot_id: str, forecast_id: str) -> dict[str, object]:
    return {
        "forecast_id": forecast_id,
        "artifact_id": "research-artifact-1",
        "artifact_path": "trading/research/research-artifact-1.md",
        "role": "valuation-analyst",
        "forecast_target": "The issuer reports positive year-over-year revenue growth.",
        "target_type": "binary",
        "horizon": "2026-06-30T00:00:00Z",
        "issued_at": "2026-01-02T00:00:00Z",
        "knowledge_cutoff": "2026-01-01T00:00:00Z",
        "probability": 0.6,
        "base_rate": {
            "cohort": "same-sector issuers",
            "source_snapshot_id": base_snapshot_id,
            "sample_size": 40,
            "selection_rule": "same sector and reporting regime",
            "value": 0.45,
        },
        "evidence_ids": ["research-artifact-1"],
        "contrary_evidence": ["demand may weaken"],
        "invalidation_conditions": ["reported revenue growth is non-positive"],
        "update_triggers": ["guidance revision"],
        "resolution_rule": "Resolve from the next audited filing.",
        "resolution_source": "audited filing snapshot",
        "review_date": "2026-04-30T00:00:00Z",
    }


def test_research_and_forecast_lists_filter_run_before_limit(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        research_module,
        "list_workspace_research_artifacts",
        lambda root, include_markdown=False: [
            {"artifact_id": "newer-other", "workflow_run_id": "other"},
            {"artifact_id": "older-target", "workflow_run_id": "target"},
        ],
    )
    artifacts = research_module.list_research_artifacts(
        tmp_path,
        {"workflow_run_id": "target", "limit": 1},
    )["artifacts"]
    assert [item["artifact_id"] for item in artifacts] == ["older-target"]

    monkeypatch.setattr(forecasting_module, "_read_events", lambda path: [])
    monkeypatch.setattr(
        forecasting_module,
        "_latest_records",
        lambda events: [
            {"forecast_id": "older-target", "workflow_run_id": "target"},
            {"forecast_id": "newer-other", "workflow_run_id": "other"},
        ],
    )
    forecasts = forecasting_module.list_forecasts(
        tmp_path,
        {"workflow_run_id": "target", "limit": 1},
    )["forecasts"]
    assert [item["forecast_id"] for item in forecasts] == ["older-target"]


def test_omitted_forecast_event_times_use_the_same_system_receipt_time(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    base_snapshot_id = _snapshot(tmp_path, "base-rate", 0.45)
    forecast_id = f"receipt-time-{uuid.uuid4().hex[:10]}"
    issue_times = iter(
        [
            "2026-01-02T00:00:00.000000Z",
            "2026-01-02T00:00:00.000001Z",
        ]
    )
    monkeypatch.setattr(forecasting_module, "now_iso", lambda: next(issue_times))
    payload = _forecast_payload(base_snapshot_id, forecast_id)
    payload.pop("issued_at")

    issued = call_mcp_tool(
        tmp_path,
        "issue_forecast",
        {"principal_id": "fundamental-analyst", **payload},
    )["forecast"]

    assert issued["issued_at"] == issued["recorded_at"]

    revision_times = iter(
        [
            "2026-01-03T00:00:00.000000Z",
            "2026-01-03T00:00:00.000001Z",
        ]
    )
    monkeypatch.setattr(forecasting_module, "now_iso", lambda: next(revision_times))
    revised = call_mcp_tool(
        tmp_path,
        "revise_forecast",
        {
            "principal_id": "fundamental-analyst",
            "forecast_id": forecast_id,
            "revision_reason": "receipt-time regression check",
            "probability": 0.61,
        },
    )["forecast"]

    assert revised["revised_at"] == revised["recorded_at"]


def test_mcp_research_and_forecast_lifecycle_enforces_role_separation(monkeypatch, tmp_path: Path) -> None:
    clock = ["2026-01-03T00:00:00Z"]
    monkeypatch.setattr(forecasting_module, "now_iso", lambda: clock[0])
    monkeypatch.setattr(research_module, "now_iso", lambda: clock[0])
    ensure_runtime_database(tmp_path)
    spec_id = f"surface-spec-{uuid.uuid4().hex[:10]}"
    created = call_mcp_tool(tmp_path, "create_research_spec", {
        "principal_id": "technical-analyst",
        **_research_spec_payload(spec_id),
    })
    assert created["artifact"]["created_by"] == "technical-analyst"
    assert call_mcp_tool(tmp_path, "get_research_spec", {"principal_id": "judgment-reviewer", "spec_id": spec_id})["artifact"]["spec_id"] == spec_id

    base_snapshot_id = _snapshot(tmp_path, "base-rate", 0.45)
    forecast_id = f"surface-forecast-{uuid.uuid4().hex[:10]}"
    issued = call_mcp_tool(tmp_path, "issue_forecast", {
        "principal_id": "fundamental-analyst",
        **_forecast_payload(base_snapshot_id, forecast_id),
    })
    assert issued["forecast"]["author"] == "fundamental-analyst"
    assert issued["forecast"]["role"] == "fundamental-analyst"
    assert issued["forecast"]["authority"] == "evidence_only"

    with pytest.raises(PermissionError, match="only the forecast author"):
        call_mcp_tool(tmp_path, "revise_forecast", {
            "principal_id": "technical-analyst",
            "forecast_id": forecast_id,
            "revision_reason": "cross-role overwrite attempt",
            "probability": 0.55,
        })
    revised = call_mcp_tool(tmp_path, "revise_forecast", {
        "principal_id": "fundamental-analyst",
        "forecast_id": forecast_id,
        "revision_reason": "new filing evidence",
        "probability": 0.65,
    })
    assert revised["forecast"]["prior_version"] == 1

    clock[0] = "2026-06-30T00:00:00Z"
    resolution_snapshot_id = _snapshot(
        tmp_path,
        "resolution",
        1,
        known_at=clock[0],
        recorded_at=clock[0],
    )
    with pytest.raises(PermissionError, match="not allowed"):
        call_mcp_tool(tmp_path, "resolve_forecast", {
            "principal_id": "fundamental-analyst",
            "forecast_id": forecast_id,
            "outcome": 1,
            "resolution_source_snapshot_id": resolution_snapshot_id,
        })
    resolved = call_mcp_tool(tmp_path, "resolve_forecast", {
        "principal_id": "judgment-reviewer",
        "forecast_id": forecast_id,
        "outcome": 1,
        "resolution_source_snapshot_id": resolution_snapshot_id,
        "observed_at": "2026-06-30T00:00:00Z",
        "resolved_at": "2026-06-30T00:00:00Z",
    })
    assert resolved["forecast"]["resolver"] == "judgment-reviewer"
    scored = call_mcp_tool(tmp_path, "score_forecast", {
        "principal_id": "judgment-reviewer",
        "forecast_id": forecast_id,
    })
    assert scored["forecast"]["original_scores"]["brier"] == pytest.approx(0.16)
    assert len(scored["forecast"]["scores_by_event"]) == 2
    report = call_mcp_tool(tmp_path, "get_forecast_calibration_report", {
        "principal_id": "head-manager",
        "minimum_sample": 2,
    })
    assert report["status"] == "insufficient_sample"

    from apps.mcp.models import McpToolCall

    assert not McpToolCall.objects.filter(tool_name__in={"create_research_spec", "issue_forecast", "resolve_forecast"}).exists()


def test_generated_projection_and_registry_keep_evidence_roles_narrow(tmp_path: Path) -> None:
    expected = {
        "create_research_spec",
        "create_causal_equity_analysis",
        "record_blind_judgment_prior",
        "complete_judgment_review",
        "issue_forecast",
        "resolve_forecast",
        "score_forecast",
        "promote_lesson",
        "create_evaluation_corpus",
        "record_evaluation_run",
        "record_blind_human_review",
        "compare_evaluation_runs",
        "list_artifact_catalog",
        "search_artifact_catalog",
        "rebuild_artifact_catalog",
    }
    assert expected.issubset(TOOL_REGISTRY)
    assert TOOL_REGISTRY["create_causal_equity_analysis"].allowed_roles == {"valuation-analyst"}
    assert TOOL_REGISTRY["resolve_forecast"].allowed_roles == {"judgment-reviewer"}
    assert TOOL_REGISTRY["promote_lesson"].allowed_roles == {"judgment-reviewer"}
    assert TOOL_REGISTRY["promote_lesson"].capability_required == "judgment_review.write"
    assert TOOL_REGISTRY["create_evaluation_corpus"].allowed_roles == {"head-manager"}
    assert TOOL_REGISTRY["record_blind_human_review"].allowed_roles == {"judgment-reviewer"}
    assert TOOL_REGISTRY["rebuild_artifact_catalog"].allowed_roles == {"head-manager"}
    assert all("execution-operator" not in TOOL_REGISTRY[name].allowed_roles for name in expected)
    assert {"get_research_spec", "list_research_specs", "get_forecast", "list_forecasts", "get_forecast_calibration_report", "list_artifact_catalog", "search_artifact_catalog"}.issubset(SAFE_HOME_TOOL_NAMES)
    snapshot_schema = TOOL_REGISTRY["record_source_snapshot"].input_schema
    assert {"source_locator", "provider_query", "known_at", "retrieved_at", "revision", "vintage", "timezone", "universe_membership"}.issubset(snapshot_schema["properties"])
    assert snapshot_schema["additionalProperties"] is False
    assert "resolve_dispute" in TOOL_REGISTRY["resolve_forecast"].input_schema["properties"]

    workspace = tmp_path / "workspace"
    bootstrap_workspace(workspace)
    root_tools = set(tomllib.loads((workspace / ".codex/config.toml").read_text(encoding="utf-8"))["mcp_servers"]["tradingcodex"]["enabled_tools"])
    valuation_tools = set(tomllib.loads((workspace / ".codex/agents/valuation-analyst.toml").read_text(encoding="utf-8"))["mcp_servers"]["tradingcodex"]["enabled_tools"])
    judgment_tools = set(tomllib.loads((workspace / ".codex/agents/judgment-reviewer.toml").read_text(encoding="utf-8"))["mcp_servers"]["tradingcodex"]["enabled_tools"])
    assert not (workspace / ".codex/agents/execution-operator.toml").exists()
    assert {"create_research_spec", "create_evaluation_corpus", "score_forecast", "list_artifact_catalog", "search_artifact_catalog", "rebuild_artifact_catalog"}.issubset(root_tools)
    assert {"create_causal_equity_analysis", "issue_forecast", "search_artifact_catalog"}.issubset(valuation_tools)
    assert {"record_blind_judgment_prior", "complete_judgment_review", "resolve_forecast", "promote_lesson", "record_blind_human_review", "search_artifact_catalog"}.issubset(judgment_tools)


def test_api_and_cli_expose_frozen_research_and_forecast_operations(monkeypatch, tmp_path: Path, capsys) -> None:
    clock = ["2026-01-03T00:00:00Z"]
    monkeypatch.setattr(forecasting_module, "now_iso", lambda: clock[0])
    monkeypatch.setattr(research_module, "now_iso", lambda: clock[0])
    ensure_runtime_database(tmp_path)
    monkeypatch.setenv("TRADINGCODEX_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("TRADINGCODEX_API_KEY", "surface-key")
    monkeypatch.setenv("TRADINGCODEX_API_PRINCIPAL", "fundamental-analyst")
    client = Client(REMOTE_ADDR="127.0.0.1", HTTP_X_TRADINGCODEX_KEY="surface-key")

    spec_id = f"api-spec-{uuid.uuid4().hex[:10]}"
    response = client.post("/api/research/specs", data=json.dumps(_research_spec_payload(spec_id)), content_type="application/json")
    assert response.status_code == 200, response.content
    assert response.json()["artifact"]["created_by"] == "fundamental-analyst"
    assert client.get(f"/api/research/specs/{spec_id}").json()["artifact"]["spec_id"] == spec_id
    snapshot_response = client.post(
        "/api/research/source-snapshots",
        data=json.dumps({
            "provider": "api-point-in-time",
            "source_category": "fundamental",
            "source_locator": "provider:test:fundamental",
            "known_at": "2025-12-31T00:00:00Z",
            "retrieved_at": "2025-12-31T00:00:00Z",
            "recorded_at": "2025-12-31T00:00:00Z",
            "revision": "original",
            "vintage": "2025Q4",
            "timezone": "UTC",
            "coverage_note": "test coverage",
            "payload": {"revenue": 100},
        }),
        content_type="application/json",
    )
    assert snapshot_response.status_code == 200, snapshot_response.content
    replay_response = client.post(
        "/api/research/replay-manifests",
        data=json.dumps({"spec_id": spec_id, "source_snapshot_ids": [snapshot_response.json()["snapshot_id"]]}),
        content_type="application/json",
    )
    assert replay_response.status_code == 200, replay_response.content
    assert replay_response.json()["artifact"]["knowledge_cutoff"] == "2026-01-01T00:00:00Z"

    base_snapshot_id = _snapshot(tmp_path, "api-base-rate", 0.45)
    forecast_id = f"api-forecast-{uuid.uuid4().hex[:10]}"
    response = client.post("/api/research/forecasts", data=json.dumps(_forecast_payload(base_snapshot_id, forecast_id)), content_type="application/json")
    assert response.status_code == 200, response.content
    assert response.json()["forecast"]["author"] == "fundamental-analyst"
    assert client.get(f"/api/research/forecasts/{forecast_id}").json()["forecast"]["forecast_id"] == forecast_id
    catalog = client.get("/api/research/catalog").json()
    assert {"research_spec", "source_snapshot", "forecast"}.issubset(
        {entry["artifact_type"] for entry in catalog["entries"]}
    )
    catalog_search = client.post(
        "/api/research/catalog/search",
        data=json.dumps({"query": "positive benchmark-relative value"}),
        content_type="application/json",
    )
    assert catalog_search.status_code == 200, catalog_search.content
    assert catalog_search.json()["entries"][0]["artifact_type"] == "research_spec"

    cli_forecast_id = f"cli-forecast-{uuid.uuid4().hex[:10]}"
    cli_forecast_file = tmp_path / "cli-forecast.json"
    cli_forecast_file.write_text(json.dumps(_forecast_payload(base_snapshot_id, cli_forecast_id)), encoding="utf-8")
    with pytest.raises(ValueError, match="--principal"):
        forecast_command(tmp_path, ["issue", str(cli_forecast_file)])
    forecast_command(tmp_path, ["issue", str(cli_forecast_file), "--principal", "fundamental-analyst"])
    cli_issued = json.loads(capsys.readouterr().out)["forecast"]
    assert cli_issued["author"] == "fundamental-analyst"
    assert cli_issued["role"] == "fundamental-analyst"

    clock[0] = "2026-05-01T00:00:00Z"
    cli_revision_file = tmp_path / "cli-forecast-revision.json"
    cli_revision_file.write_text(json.dumps({
        "forecast_id": cli_forecast_id,
        "revision_reason": "CLI transport-bound revision.",
        "probability": 0.65,
        "revised_at": clock[0],
    }), encoding="utf-8")
    with pytest.raises(ValueError, match="--principal"):
        forecast_command(tmp_path, ["revise", str(cli_revision_file)])
    forecast_command(tmp_path, ["revise", str(cli_revision_file), "--principal", "fundamental-analyst"])
    assert json.loads(capsys.readouterr().out)["forecast"]["author"] == "fundamental-analyst"

    clock[0] = "2026-06-30T00:00:00Z"
    resolution_snapshot_id = _snapshot(
        tmp_path,
        "api-resolution",
        1,
        known_at=clock[0],
        recorded_at=clock[0],
    )
    resolution_payload = {
        "outcome": 1,
        "resolution_source_snapshot_id": resolution_snapshot_id,
        "observed_at": "2026-06-30T00:00:00Z",
        "resolved_at": "2026-06-30T00:00:00Z",
    }
    denied = client.post(
        f"/api/research/forecasts/{forecast_id}/resolution",
        data=json.dumps(resolution_payload),
        content_type="application/json",
    )
    assert denied.status_code == 403
    denied_evaluation = client.post(
        "/api/evaluations/corpora",
        data=json.dumps({"cases": [{}], "promotion_criteria": [{}]}),
        content_type="application/json",
    )
    assert denied_evaluation.status_code == 403
    monkeypatch.setenv("TRADINGCODEX_API_PRINCIPAL", "judgment-reviewer")
    resolved = client.post(
        f"/api/research/forecasts/{forecast_id}/resolution",
        data=json.dumps(resolution_payload),
        content_type="application/json",
    )
    assert resolved.status_code == 200, resolved.content
    assert resolved.json()["forecast"]["resolver"] == "judgment-reviewer"
    assert client.post(f"/api/research/forecasts/{forecast_id}/score").status_code == 200

    spec_file = tmp_path / "cli-spec.json"
    cli_spec_id = f"cli-spec-{uuid.uuid4().hex[:10]}"
    spec_file.write_text(json.dumps(_research_spec_payload(cli_spec_id)), encoding="utf-8")
    with pytest.raises(ValueError, match="--principal"):
        research_command(tmp_path, ["spec", "create", str(spec_file)])
    research_command(tmp_path, ["spec", "create", str(spec_file), "--principal", "head-manager"])
    cli_spec = json.loads(capsys.readouterr().out)["artifact"]
    assert cli_spec["spec_id"] == cli_spec_id
    assert cli_spec["created_by"] == "head-manager"
    research_command(tmp_path, ["index", "rebuild"])
    assert json.loads(capsys.readouterr().out)["status"] == "rebuilt"
    forecast_command(tmp_path, ["list", "--role", "fundamental-analyst"])
    assert json.loads(capsys.readouterr().out)["forecasts"][0]["forecast_id"] == forecast_id
    with pytest.raises(ValueError, match="--principal"):
        forecast_command(tmp_path, ["score", forecast_id])
    forecast_command(tmp_path, ["score", forecast_id, "--principal", "judgment-reviewer"])
    assert json.loads(capsys.readouterr().out)["forecast"]["event_type"] == "scored"

    cli_resolution_file = tmp_path / "cli-forecast-resolution.json"
    cli_resolution_file.write_text(json.dumps({
        "forecast_id": cli_forecast_id,
        "outcome": 1,
        "resolution_source_snapshot_id": resolution_snapshot_id,
        "observed_at": "2026-06-30T00:00:00Z",
        "resolved_at": "2026-06-30T00:00:00Z",
    }), encoding="utf-8")
    with pytest.raises(ValueError, match="--principal"):
        forecast_command(tmp_path, ["resolve", str(cli_resolution_file)])
    forecast_command(tmp_path, ["resolve", str(cli_resolution_file), "--principal", "judgment-reviewer"])
    assert json.loads(capsys.readouterr().out)["forecast"]["resolver"] == "judgment-reviewer"
    assert {"research", "forecast", "evaluation"}.issubset(WORKSPACE_COMMANDS)

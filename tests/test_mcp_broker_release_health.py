from __future__ import annotations

import hashlib
import json
import logging
import sys
import tomllib
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.test import Client, RequestFactory

from apps.mcp.services import redact_sensitive_data

from tradingcodex_service.application import health as health_service
from tradingcodex_service.application.brokers import (
    BrokerAdapter,
    BrokerAdapterProvider,
    BrokerHealth,
    _BROKER_ADAPTER_PROVIDERS,
    adapter_for_connection,
    connect_broker_connector,
    ensure_paper_broker_connection,
    get_broker_connection_status,
    list_broker_connections,
    render_broker_connector_scaffold,
    register_broker_adapter_provider,
    register_broker_connector,
    validate_broker_connector_build,
)
from tradingcodex_service.application.runtime import (
    ensure_runtime_database,
    ensure_workspace_manifest,
    tradingcodex_state_dir,
)
from tradingcodex_service.application.policy import PolicyConfigurationError, read_runtime_policy
from tradingcodex_service.mcp_runtime import TOOL_REGISTRY, call_mcp_tool, validate_input_schema
from tradingcodex_service.log_safety import RedactingFormatter, redact_log_text


ROOT = Path(__file__).resolve().parents[1]


def test_broker_mcp_inputs_expose_only_v1_canonical_identity_fields() -> None:
    connector_tools = {
        "register_broker_connector",
        "render_broker_connector_scaffold",
        "validate_broker_connector_build",
        "get_broker_connection_status",
        "get_broker_capability_profile",
        "get_broker_instrument_constraints",
        "sync_broker_account",
    }
    removed_aliases = {"broker", "broker_connection_id", "label", "provider", "template"}

    for name in connector_tools:
        properties = set(TOOL_REGISTRY[name].input_schema["properties"])
        assert properties.isdisjoint(removed_aliases), name
        assert "broker_id" in TOOL_REGISTRY[name].input_schema["required"], name

    for name in {"register_broker_connector", "render_broker_connector_scaffold"}:
        assert {"provider_id", "broker_id"}.issubset(TOOL_REGISTRY[name].input_schema["required"])

    assert "connect_broker_connector" not in TOOL_REGISTRY
    assert "scaffold_broker_connector" not in TOOL_REGISTRY

    constraints_schema = TOOL_REGISTRY["get_broker_instrument_constraints"].input_schema
    assert set(constraints_schema["required"]) == {"broker_id", "symbol"}
    assert "instrument" not in constraints_schema["properties"]

    with pytest.raises(ValueError, match="does not allow additional properties: provider"):
        validate_input_schema(
            TOOL_REGISTRY["register_broker_connector"],
            {"provider_id": "paper", "broker_id": "paper", "provider": "paper"},
        )
    with pytest.raises(ValueError, match="broker_id is required"):
        get_broker_connection_status(Path.cwd(), {"broker_connection_id": "paper-trading"})
    with pytest.raises(ValueError, match="provider_id is required"):
        connect_broker_connector(Path.cwd(), {"broker": "paper", "broker_id": "paper-trading"})


def test_connector_scaffold_render_is_content_addressed_and_read_only(tmp_path: Path) -> None:
    ensure_workspace_manifest(tmp_path)
    target_root = tmp_path / "trading" / "connectors" / "render-test"
    arguments = {
        "provider_id": "missing-provider",
        "broker_id": "render-test",
        "credential_ref": "env:RENDER_TEST",
    }

    first = render_broker_connector_scaffold(tmp_path, arguments)

    assert first["status"] == "rendered"
    assert first["writes_performed"] is False
    assert first["db_canonical"] is False
    assert not target_root.exists()
    for rendered_file in first["files"].values():
        assert rendered_file["content_sha256"] == hashlib.sha256(
            rendered_file["content"].encode("utf-8")
        ).hexdigest()
        assert rendered_file["preimage_exists"] is False
        assert rendered_file["preimage_sha256"] is None
        assert rendered_file["preimage_size"] is None
        assert "preimage_content" not in rendered_file

    target_root.mkdir(parents=True)
    profile_path = target_root / "connector-profile.json"
    raw_preimage = "api_key=raw-secret-never-return\n"
    profile_path.write_text(raw_preimage, encoding="utf-8")
    before = profile_path.read_bytes()
    second = render_broker_connector_scaffold(tmp_path, arguments)
    profile = second["files"]["profile"]

    assert profile["preimage_exists"] is True
    assert profile["preimage_sha256"] == hashlib.sha256(before).hexdigest()
    assert profile["preimage_size"] == len(before)
    assert "preimage_content" not in profile
    assert "raw-secret-never-return" not in json.dumps(second, sort_keys=True)
    assert profile_path.read_bytes() == before
    assert not (target_root / "secret-schema.json").exists()
    assert not (target_root / "README.md").exists()
    assert second["render_sha256"] != first["render_sha256"]


def test_broker_connection_identity_is_first_class_and_fail_closed(tmp_path: Path) -> None:
    ensure_runtime_database(tmp_path)
    ensure_workspace_manifest(tmp_path)
    from apps.integrations.models import BrokerConnection

    paper = ensure_paper_broker_connection(tmp_path)
    fields = {field.name for field in BrokerConnection._meta.fields}
    serialized = next(
        item
        for item in list_broker_connections(tmp_path)["connections"]
        if item["broker_id"] == paper.broker_id
    )

    assert "provider_id" in fields
    assert "adapter_type" not in fields
    assert serialized["provider_id"] == paper.provider_id == "paper"
    assert serialized["transport"] == paper.transport == "paper"
    assert "adapter_type" not in serialized
    assert "provider_id" not in serialized["metadata"]

    cases = (
        ("paper", "api", "not valid for api transport"),
        ("unknown-transport", "mcp", "unsupported broker transport"),
        ("unknown-api-provider", "api", "unknown broker provider"),
    )
    for provider_id, transport, message in cases:
        connection = SimpleNamespace(
            broker_id=f"identity-{uuid.uuid4().hex}",
            provider_id=provider_id,
            display_name="Invalid identity",
            transport=transport,
            metadata={},
        )
        with pytest.raises(ValueError, match=message):
            adapter_for_connection(connection, tmp_path)


def test_execution_postures_reject_pre_v1_aliases(tmp_path: Path) -> None:
    config = tmp_path / ".tradingcodex" / "config.yaml"
    config.parent.mkdir(parents=True)
    config.write_text(
        "execution:\n"
        "  live_enabled: false\n"
        "  max_single_order_base: 100000\n"
        "  enabled_adapters:\n"
        "    - paper-trading\n"
        "  enabled_execution_postures:\n"
        "    - testnet_order_test\n",
        encoding="utf-8",
    )

    with pytest.raises(PolicyConfigurationError, match="unsupported execution posture"):
        read_runtime_policy(tmp_path)

    provider_id = f"legacy-posture-{uuid.uuid4().hex}"
    with pytest.raises(ValueError, match="unsupported broker execution posture"):
        register_broker_adapter_provider(
            BrokerAdapterProvider(
                provider_id=provider_id,
                display_name="Legacy Posture",
                execution_posture="testnet_order_test",
            )
        )
    assert provider_id not in _BROKER_ADAPTER_PROVIDERS

    safe_provider_id = f"safe-default-{uuid.uuid4().hex}"
    safe_provider = BrokerAdapterProvider(provider_id=safe_provider_id, display_name="Safe Default")
    register_broker_adapter_provider(safe_provider)
    try:
        profile = safe_provider.as_profile(broker_id=safe_provider_id)
        assert profile["execution_posture"] == "service_adapter_required"
        assert "execution_service_adapter_required" in profile["blockers"]
    finally:
        _BROKER_ADAPTER_PROVIDERS.pop(safe_provider_id, None)


def test_runtime_policy_requires_the_exact_v1_execution_config(tmp_path: Path) -> None:
    config = tmp_path / ".tradingcodex" / "config.yaml"
    config.parent.mkdir(parents=True)
    config.write_text(
        "execution:\n"
        "  live_enabled: false\n"
        "  max_single_order_base: 25000\n"
        "  enabled_adapters: [paper-trading]\n"
        "  enabled_execution_postures: [paper_only, broker_validation_only]\n",
        encoding="utf-8",
    )

    policy = read_runtime_policy(tmp_path)
    assert policy.max_single_order_base == 25000
    assert policy.allowed_adapters == frozenset({"paper-trading"})
    assert policy.source == (".tradingcodex/config.yaml",)

    config.write_text(config.read_text(encoding="utf-8") + "  default_adapter: paper-trading\n", encoding="utf-8")
    with pytest.raises(PolicyConfigurationError, match="unsupported field.*default_adapter"):
        read_runtime_policy(tmp_path)


def test_package_metadata_uses_the_runtime_version_source() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert project["project"]["dynamic"] == ["version"]
    assert "version" not in project["project"]
    assert project["tool"]["setuptools"]["dynamic"]["version"] == {
        "attr": "tradingcodex_service.version.TRADINGCODEX_VERSION"
    }


class _ValidationAdapter(BrokerAdapter):
    def health_check(self) -> BrokerHealth:
        return BrokerHealth("ok", "validation health ok")


def test_broker_status_is_read_only_and_explicit_validation_promotes(tmp_path: Path) -> None:
    ensure_runtime_database(tmp_path)
    ensure_workspace_manifest(tmp_path)
    provider_id = f"validation-{uuid.uuid4().hex}"
    register_broker_adapter_provider(
        BrokerAdapterProvider(
            provider_id=provider_id,
            display_name="Validation Test",
            execution_posture="broker_validation_only",
            auth_model={"type": "credential_ref", "credential_ref_required": True},
            factory=lambda connection, workspace_root: _ValidationAdapter(),
        )
    )
    try:
        register_broker_connector(
            tmp_path,
            {"provider_id": provider_id, "broker_id": provider_id, "credential_ref": "env:VALIDATION_TEST"},
        )
        from apps.integrations.models import BrokerConnection

        connection = BrokerConnection.objects.get(broker_id=provider_id)
        before = {
            "status": connection.status,
            "trade_scopes": connection.enabled_trade_scopes,
            "health": connection.last_health_status,
            "metadata": connection.metadata,
            "updated_at": connection.updated_at,
        }
        observed = get_broker_connection_status(tmp_path, {"broker_id": provider_id, "promote_execution": True})
        connection.refresh_from_db()
        after = {
            "status": connection.status,
            "trade_scopes": connection.enabled_trade_scopes,
            "health": connection.last_health_status,
            "metadata": connection.metadata,
            "updated_at": connection.updated_at,
        }
        assert observed["health"]["status"] == "ok"
        assert observed["read_only"] is True
        assert after == before

        validated = validate_broker_connector_build(tmp_path, {"broker_id": provider_id})
        connection.refresh_from_db()
        assert validated["connection"]["read_only"] is False
        assert connection.status == "trading_enabled"
        assert connection.enabled_trade_scopes == ["order.submit.validation"]
    finally:
        _BROKER_ADAPTER_PROVIDERS.pop(provider_id, None)


def test_liveness_and_readiness_are_distinct(monkeypatch, tmp_path: Path) -> None:
    ensure_runtime_database(tmp_path)
    client = Client(REMOTE_ADDR="127.0.0.1")
    live = client.get("/api/health/live")
    ready = client.get("/api/health/ready")
    assert live.status_code == 200
    assert live.json()["status"] == "ok"
    assert "checks" not in live.json()
    assert ready.status_code == 200
    assert ready.json()["ready"] is True
    assert {check["name"] for check in ready.json()["checks"]} == {"database", "migrations", "state_directory"}

    monkeypatch.setattr(
        health_service,
        "_state_directory_check",
        lambda: {"name": "state_directory", "status": "failed", "code": "state_directory_unwritable"},
    )
    not_ready = client.get("/api/health/ready")
    assert not_ready.status_code == 503
    assert not_ready.json()["ready"] is False
    assert not_ready.json()["reason_codes"] == ["state_directory_unwritable"]


def test_service_logging_uses_bounded_rotating_handler() -> None:
    from tradingcodex_service.settings import LOGGING

    handler = LOGGING["handlers"]["service_file"]
    assert handler["class"] == "logging.handlers.RotatingFileHandler"
    assert handler["maxBytes"] > 0
    assert handler["backupCount"] > 0
    assert handler["formatter"] == "redacted"


def test_service_log_formatter_redacts_environment_secrets(monkeypatch) -> None:
    canary = f"tcx-log-secret-{uuid.uuid4().hex}"
    monkeypatch.setenv("PROVIDER_API_KEY", canary)
    record = logging.LogRecord("test", logging.ERROR, __file__, 1, "provider failed: %s", (canary,), None)
    formatted = RedactingFormatter("{levelname} {message}", style="{").format(record)
    assert canary not in formatted
    assert "<redacted>" in formatted
    username_url = "https://legacy-user@example.test/mcp"
    assert "legacy-user" not in redact_log_text(username_url)
    assert "legacy-user" not in json.dumps(redact_sensitive_data({"url": username_url}))

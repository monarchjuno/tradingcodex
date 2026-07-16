from django.apps import apps
from django.db import connection
from django.db import migrations
from django.db.migrations.autodetector import MigrationAutodetector
from django.db.migrations.loader import MigrationLoader
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.state import ProjectState


PROJECT_APPS = {
    "audit",
    "harness",
    "integrations",
    "mcp",
    "orders",
    "policy",
    "portfolio",
}
V1_INITIAL = "0001_v1_initial"


def test_public_v1_initial_migrations_are_preserved_with_forward_gate_removal() -> None:
    loader = MigrationLoader(None, ignore_no_migrations=True)
    project_nodes = {node for node in loader.graph.nodes if node[0] in PROJECT_APPS}

    assert project_nodes == {
        *((app_label, V1_INITIAL) for app_label in PROJECT_APPS),
        ("mcp", "0002_remove_external_mcp_gate"),
    }
    for app_label, migration_name in project_nodes:
        if migration_name != V1_INITIAL:
            continue
        migration = loader.get_migration(app_label, migration_name)
        assert migration.initial is True
        assert not migration.replaces
        assert not any(isinstance(operation, migrations.RunPython) for operation in migration.operations)
        assert all(
            dependency[1] == V1_INITIAL
            for dependency in migration.dependencies
            if dependency[0] in PROJECT_APPS
        )

    assert ("orders", "orderintent") not in loader.project_state().models
    final_models = loader.project_state().models
    assert ("mcp", "mcptooldefinition") in final_models
    assert ("mcp", "mcptoolcall") in final_models
    assert not any(
        app_label == "mcp" and (model_name == "mcprouter" or model_name.startswith("mcpexternal"))
        for app_label, model_name in final_models
    )


def test_public_v1_database_upgrade_removes_gate_state_and_preserves_ledgers() -> None:
    executor = MigrationExecutor(connection)
    latest = executor.loader.graph.leaf_nodes()
    try:
        v1_targets = [(app_label, V1_INITIAL) for app_label in PROJECT_APPS]
        executor.migrate(v1_targets)
        old_apps = executor.loader.project_state(v1_targets).apps
        BrokerConnection = old_apps.get_model("integrations", "BrokerConnection")
        Router = old_apps.get_model("mcp", "McpRouter")
        ToolCall = old_apps.get_model("mcp", "McpToolCall")
        AuditEvent = old_apps.get_model("audit", "AuditEvent")

        BrokerConnection.objects.create(
            broker_id="legacy-gate",
            provider_id="external-mcp",
            display_name="Legacy Gate",
            transport="mcp",
        )
        BrokerConnection.objects.create(
            broker_id="paper-preserved",
            provider_id="paper",
            display_name="Paper",
            transport="paper",
        )
        Router.objects.create(name="legacy-router")
        ToolCall.objects.create(tool_name="historical-call", request={"redacted": True})
        AuditEvent.objects.create(action="historical.external-capability", payload={"kept": True})

        executor = MigrationExecutor(connection)
        executor.migrate(latest)

        from apps.audit.models import AuditEvent as CurrentAuditEvent
        from apps.integrations.models import BrokerConnection as CurrentBrokerConnection
        from apps.mcp.models import McpToolCall as CurrentToolCall

        assert not CurrentBrokerConnection.objects.filter(broker_id="legacy-gate").exists()
        assert CurrentBrokerConnection.objects.filter(broker_id="paper-preserved").exists()
        assert CurrentToolCall.objects.filter(tool_name="historical-call").exists()
        assert CurrentAuditEvent.objects.filter(action="historical.external-capability").exists()
        tables = set(connection.introspection.table_names())
        assert "mcp_mcptoolcall" in tables
        assert not any(name == "mcp_mcprouter" or name.startswith("mcp_mcpexternal") for name in tables)
    finally:
        MigrationExecutor(connection).migrate(latest)


def test_v1_migration_graph_matches_current_models() -> None:
    loader = MigrationLoader(None, ignore_no_migrations=True)
    changes = MigrationAutodetector(
        loader.project_state(),
        ProjectState.from_apps(apps),
    ).changes(graph=loader.graph)

    assert not {app_label: changes[app_label] for app_label in PROJECT_APPS if app_label in changes}

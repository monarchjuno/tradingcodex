from django.db import migrations


def remove_gate_broker_connections(apps, schema_editor):
    BrokerConnection = apps.get_model("integrations", "BrokerConnection")
    BrokerConnection.objects.filter(provider_id="external-mcp", transport="mcp").delete()


class Migration(migrations.Migration):
    dependencies = [
        ("integrations", "0001_v1_initial"),
        ("mcp", "0001_v1_initial"),
    ]

    operations = [
        migrations.RunPython(remove_gate_broker_connections, migrations.RunPython.noop),
        migrations.DeleteModel(name="McpExternalPermissionRequest"),
        migrations.DeleteModel(name="McpExternalToolCall"),
        migrations.DeleteModel(name="McpExternalToolPermission"),
        migrations.DeleteModel(name="McpExternalTool"),
        migrations.DeleteModel(name="McpRouter"),
    ]

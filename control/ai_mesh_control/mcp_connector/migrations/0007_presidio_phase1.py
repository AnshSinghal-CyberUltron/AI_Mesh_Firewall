"""DECISION-D Phase 1: Presidio + ComplianceTag wiring on MCP models."""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("mcp_connector", "0006_drop_contextforge_server_id"),
    ]

    operations = [
        migrations.AddField(
            model_name="mcpserverregistration",
            name="default_presidio_action",
            field=models.CharField(
                max_length=8,
                choices=[("tag", "Tag only"), ("redact", "Redact"), ("block", "Block")],
                default="tag",
            ),
        ),
        migrations.AddField(
            model_name="mcptoolregistration",
            name="presidio_action",
            field=models.CharField(
                max_length=8,
                choices=[
                    ("inherit", "Inherit from server"),
                    ("tag", "Tag only"),
                    ("redact", "Redact"),
                    ("block", "Block"),
                ],
                default="inherit",
            ),
        ),
        migrations.AddField(
            model_name="mcpevent",
            name="compliance_tags",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name="mcpevent",
            name="presidio_findings",
            field=models.JSONField(blank=True, default=list),
        ),
    ]

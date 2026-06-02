# Generated manually for MCP two-tier scan control matrix.

import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("auth_api", "0006_unique_user_email"),
        ("mcp_connector", "0010_unify_guardrails_remove_enkrypt"),
    ]

    operations = [
        migrations.CreateModel(
            name="MCPScanControl",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("tool_name", models.CharField(blank=True, default="", max_length=255)),
                (
                    "tier",
                    models.CharField(
                        choices=[("tier1", "Tier 1 (static)"), ("tier2", "Tier 2 (Bedrock)")],
                        max_length=8,
                    ),
                ),
                ("enabled", models.BooleanField(default=True)),
                (
                    "direction",
                    models.CharField(
                        choices=[
                            ("input", "Input"),
                            ("output", "Output"),
                            ("both", "Both"),
                        ],
                        default="both",
                        max_length=8,
                    ),
                ),
                (
                    "scope_type",
                    models.CharField(
                        choices=[
                            ("org", "Organization"),
                            ("server", "Server"),
                            ("tool", "Tool"),
                        ],
                        default="org",
                        max_length=8,
                    ),
                ),
                (
                    "target_mode",
                    models.CharField(
                        choices=[
                            ("entire", "Entire payload"),
                            ("key_path", "Key path"),
                        ],
                        default="entire",
                        max_length=16,
                    ),
                ),
                ("key_path", models.CharField(blank=True, default="", max_length=512)),
                (
                    "strict_mode",
                    models.CharField(
                        choices=[
                            ("strict", "Strict (fail closed)"),
                            ("fail_open", "Fail open (degraded pass)"),
                        ],
                        default="fail_open",
                        max_length=16,
                    ),
                ),
                ("priority", models.IntegerField(default=100)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "organization",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="mcp_scan_controls",
                        to="auth_api.organization",
                    ),
                ),
                (
                    "server",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="scan_controls",
                        to="mcp_connector.mcpserverregistration",
                    ),
                ),
            ],
            options={
                "ordering": ["-priority", "tier", "direction"],
            },
        ),
        migrations.AddIndex(
            model_name="mcpscancontrol",
            index=models.Index(
                fields=["organization", "tier", "enabled"],
                name="mcp_scan_org_tier_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="mcpscancontrol",
            index=models.Index(
                fields=["server", "tool_name"],
                name="mcp_scan_srv_tool_idx",
            ),
        ),
    ]

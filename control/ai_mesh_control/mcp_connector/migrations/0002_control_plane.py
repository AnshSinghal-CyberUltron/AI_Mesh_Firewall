# Generated migration for MCP control-plane models

import django.db.models.deletion
import uuid
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("auth_api", "0006_unique_user_email"),
        ("mcp_connector", "0001_initial"),
    ]

    operations = [
        # ── MCPServerRegistration new fields ──
        migrations.AddField(
            model_name="mcpserverregistration",
            name="server_slug",
            field=models.SlugField(
                blank=True,
                default="",
                help_text="URL-safe identifier for gateway endpoint; auto-generated from name",
                max_length=128,
            ),
        ),
        migrations.AddField(
            model_name="mcpserverregistration",
            name="is_exposed_to_agents",
            field=models.BooleanField(
                default=True,
                help_text="Whether this server is accessible via the external gateway endpoint",
            ),
        ),
        migrations.AddField(
            model_name="mcpserverregistration",
            name="connection_status",
            field=models.CharField(
                choices=[
                    ("connected", "Connected"),
                    ("failed", "Failed"),
                    ("syncing", "Syncing"),
                    ("unknown", "Unknown"),
                ],
                default="unknown",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="mcpserverregistration",
            name="tools_count",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="mcpserverregistration",
            name="last_sync_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="mcpserverregistration",
            name="last_health_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="mcpserverregistration",
            name="last_health_status",
            field=models.CharField(
                choices=[
                    ("healthy", "Healthy"),
                    ("unhealthy", "Unhealthy"),
                    ("unreachable", "Unreachable"),
                ],
                default="unreachable",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="mcpserverregistration",
            name="risk_level",
            field=models.CharField(
                choices=[
                    ("low", "Low"),
                    ("medium", "Medium"),
                    ("high", "High"),
                    ("critical", "Critical"),
                ],
                default="low",
                max_length=16,
            ),
        ),
        migrations.AddConstraint(
            model_name="mcpserverregistration",
            constraint=models.UniqueConstraint(
                fields=("organization", "server_slug"),
                name="unique_org_server_slug",
            ),
        ),
        # ── MCPToolRegistration ──
        migrations.CreateModel(
            name="MCPToolRegistration",
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
                ("tool_name", models.CharField(max_length=255)),
                ("description", models.TextField(blank=True, default="")),
                ("enabled", models.BooleanField(default=True)),
                (
                    "sensitivity",
                    models.CharField(
                        choices=[
                            ("low", "Low"),
                            ("medium", "Medium"),
                            ("high", "High"),
                            ("critical", "Critical"),
                        ],
                        default="low",
                        max_length=16,
                    ),
                ),
                ("input_schema", models.JSONField(blank=True, default=dict)),
                ("last_seen_at", models.DateTimeField(auto_now=True)),
                (
                    "server",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="tool_registrations",
                        to="mcp_connector.mcpserverregistration",
                    ),
                ),
                (
                    "organization",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="mcp_tools",
                        to="auth_api.organization",
                    ),
                ),
            ],
            options={
                "ordering": ["tool_name"],
            },
        ),
        migrations.AddConstraint(
            model_name="mcptoolregistration",
            constraint=models.UniqueConstraint(
                fields=("server", "tool_name"),
                name="unique_server_tool",
            ),
        ),
        # ── MCPEvent ──
        migrations.CreateModel(
            name="MCPEvent",
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
                ("user_id", models.IntegerField(blank=True, null=True)),
                ("username", models.CharField(blank=True, default="", max_length=255)),
                (
                    "server_slug",
                    models.CharField(blank=True, default="", max_length=128),
                ),
                (
                    "server_name",
                    models.CharField(blank=True, default="", max_length=255),
                ),
                ("tool_name", models.CharField(max_length=255)),
                (
                    "decision",
                    models.CharField(
                        choices=[
                            ("allow", "Allow"),
                            ("block", "Block"),
                            ("redact", "Redact"),
                            ("error", "Error"),
                        ],
                        default="allow",
                        max_length=16,
                    ),
                ),
                ("policy_ids", models.JSONField(blank=True, default=list)),
                ("policy_reason", models.TextField(blank=True, default="")),
                ("latency_ms", models.IntegerField(default=0)),
                (
                    "request_id",
                    models.CharField(blank=True, default="", max_length=64),
                ),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("timestamp", models.DateTimeField(auto_now_add=True)),
                (
                    "organization",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="mcp_events",
                        to="auth_api.organization",
                    ),
                ),
            ],
            options={
                "ordering": ["-timestamp"],
            },
        ),
        migrations.AddIndex(
            model_name="mcpevent",
            index=models.Index(
                fields=["organization", "-timestamp"],
                name="mcp_connect_organiz_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="mcpevent",
            index=models.Index(
                fields=["decision"],
                name="mcp_connect_decisio_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="mcpevent",
            index=models.Index(
                fields=["tool_name"],
                name="mcp_connect_tool_na_idx",
            ),
        ),
    ]

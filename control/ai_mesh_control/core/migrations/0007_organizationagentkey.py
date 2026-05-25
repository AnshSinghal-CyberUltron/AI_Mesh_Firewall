# OrganizationAgentKey for per-organization agent registration

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("auth_api", "0004_organization_and_userprofile_organization"),
        ("core", "0006_backfill_endpoint_organization"),
    ]

    operations = [
        migrations.CreateModel(
            name="OrganizationAgentKey",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "prefix",
                    models.CharField(
                        editable=False,
                        help_text="First 8 characters of the plaintext key (for identification in logs).",
                        max_length=8,
                        db_index=True,
                    ),
                ),
                (
                    "key_hash",
                    models.CharField(
                        editable=False,
                        help_text="SHA-256 hex digest of the full API key.",
                        max_length=64,
                        unique=True,
                        db_index=True,
                    ),
                ),
                ("name", models.CharField(default="Default registration key", max_length=128)),
                ("is_active", models.BooleanField(db_index=True, default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "organization",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="agent_keys",
                        to="auth_api.organization",
                    ),
                ),
            ],
            options={
                "ordering": ["-created_at"],
                "verbose_name": "Organization Agent Key",
                "verbose_name_plural": "Organization Agent Keys",
            },
        ),
    ]

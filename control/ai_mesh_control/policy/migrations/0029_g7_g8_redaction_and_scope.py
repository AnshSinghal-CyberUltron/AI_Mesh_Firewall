"""G7 + G8: response field redaction + per-user/agent/role allowlists.

Adds four ArrayField columns to ``policy.Policy``:

* ``redaction_fields`` — keys to scrub in tool responses (G7).
* ``allowed_user_ids`` / ``allowed_agent_ids`` / ``allowed_roles`` — actor
  allowlists that gate which policies apply to a given request (G8).

All four default to ``[]`` (wildcard / disabled) so the migration is a
no-op for existing rows. No indexes are created up front; the Policy
table is small (<100 rows in typical deployments) and the queries used
in ``mcp_connector.views`` are equality checks against ``[]`` plus
``__contains`` / ``__overlap`` containment, which planner picks up
without GIN. Indexes can be added in a follow-up migration if EXPLAIN
ANALYZE shows seq-scan pressure.
"""
from django.contrib.postgres.fields import ArrayField
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("policy", "0028_encrypt_vector_provider_api_key"),
    ]

    operations = [
        migrations.AddField(
            model_name="policy",
            name="redaction_fields",
            field=ArrayField(
                base_field=models.CharField(max_length=128),
                blank=True,
                default=list,
                help_text="Top-level / nested dict keys to redact in tool responses (case-insensitive exact match).",
            ),
        ),
        migrations.AddField(
            model_name="policy",
            name="allowed_user_ids",
            field=ArrayField(
                base_field=models.IntegerField(),
                blank=True,
                default=list,
                help_text="Empty = any user. Non-empty = only these user IDs are subject to this policy.",
            ),
        ),
        migrations.AddField(
            model_name="policy",
            name="allowed_agent_ids",
            field=ArrayField(
                base_field=models.CharField(max_length=128),
                blank=True,
                default=list,
                help_text="Empty = any agent. Non-empty = only these agent IDs (API key prefix) are subject to this policy.",
            ),
        ),
        migrations.AddField(
            model_name="policy",
            name="allowed_roles",
            field=ArrayField(
                base_field=models.CharField(max_length=64),
                blank=True,
                default=list,
                help_text="Empty = any role. Non-empty = only users with at least one of these role names are subject to this policy.",
            ),
        ),
    ]

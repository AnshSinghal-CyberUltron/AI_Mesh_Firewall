"""Migrate global policy_domain to pipeline and remove global from choices."""

from django.db import migrations, models


def migrate_global_to_pipeline(apps, schema_editor):
    Policy = apps.get_model("policy", "Policy")
    Policy.objects.filter(policy_domain__in=["global", ""]).update(policy_domain="pipeline")


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("policy", "0030_compliance_tag_catalog"),
    ]

    operations = [
        migrations.RunPython(migrate_global_to_pipeline, noop_reverse),
        migrations.AlterField(
            model_name="policy",
            name="policy_domain",
            field=models.CharField(
                choices=[
                    ("pipeline", "Pipeline"),
                    ("rag", "RAG"),
                    ("mcp", "MCP"),
                ],
                db_index=True,
                default="pipeline",
                help_text="Enforcement domain: pipeline, rag, or mcp",
                max_length=16,
            ),
        ),
    ]

"""Seed ComplianceTag catalog from policy.compliance_tags.COMPLIANCE_TAG_METADATA.

DECISION-D Phase 1.
"""
from django.db import migrations, models


def seed_tags(apps, schema_editor):
    ComplianceTag = apps.get_model("policy", "ComplianceTag")
    from policy.compliance_tags import COMPLIANCE_TAG_CODES, COMPLIANCE_TAG_METADATA
    for code in COMPLIANCE_TAG_CODES:
        meta = COMPLIANCE_TAG_METADATA[code]
        ComplianceTag.objects.update_or_create(
            code=code,
            defaults={
                "label": meta["label"],
                "regulation": meta.get("regulation", ""),
                "description": meta.get("description", ""),
                "severity": meta.get("severity", "medium"),
                "is_seeded": True,
                "is_active": True,
            },
        )


def unseed_tags(apps, schema_editor):
    ComplianceTag = apps.get_model("policy", "ComplianceTag")
    from policy.compliance_tags import COMPLIANCE_TAG_CODES
    ComplianceTag.objects.filter(code__in=COMPLIANCE_TAG_CODES, is_seeded=True).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("policy", "0029_g7_g8_redaction_and_scope"),
    ]

    operations = [
        migrations.CreateModel(
            name="ComplianceTag",
            fields=[
                ("code", models.CharField(
                    max_length=32, primary_key=True, serialize=False,
                    help_text="Stable upper-case code (e.g. 'GDPR-PII'). Used as the join key.",
                )),
                ("label", models.CharField(max_length=128)),
                ("regulation", models.CharField(
                    max_length=128, blank=True, default="",
                    help_text="Citation (e.g. 'EU GDPR Art. 4(1)').",
                )),
                ("description", models.TextField(blank=True, default="")),
                ("severity", models.CharField(
                    max_length=16,
                    choices=[("low", "Low"), ("medium", "Medium"), ("high", "High"), ("critical", "Critical")],
                    default="medium",
                )),
                ("is_seeded", models.BooleanField(
                    default=False,
                    help_text="True for tags created by the seed migration; admins may still edit them.",
                )),
                ("is_active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "Compliance Tag",
                "verbose_name_plural": "Compliance Tags",
                "ordering": ["code"],
            },
        ),
        migrations.RunPython(seed_tags, reverse_code=unseed_tags),
    ]

import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("policy", "0007_alter_notification_id"),
    ]

    operations = [
        migrations.CreateModel(
            name="ComplianceViolation",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "enforcement_event",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="compliance_violations",
                        to="policy.enforcementevent",
                    ),
                ),
                (
                    "framework",
                    models.CharField(
                        choices=[
                            ("GDPR", "GDPR"),
                            ("SOC2", "SOC 2"),
                            ("HIPAA", "HIPAA"),
                            ("ISO27001", "ISO 27001"),
                            ("PCIDSS", "PCI DSS"),
                            ("CCPA", "CCPA"),
                        ],
                        db_index=True,
                        max_length=16,
                    ),
                ),
                ("violation_type", models.CharField(blank=True, max_length=64)),
                (
                    "severity",
                    models.CharField(
                        choices=[
                            ("low", "Low"),
                            ("medium", "Medium"),
                            ("high", "High"),
                            ("critical", "Critical"),
                        ],
                        default="medium",
                        max_length=16,
                    ),
                ),
                ("description", models.TextField(blank=True)),
                (
                    "status",
                    models.CharField(
                        choices=[("open", "Open"), ("resolved", "Resolved")],
                        db_index=True,
                        default="open",
                        max_length=16,
                    ),
                ),
                (
                    "created_at",
                    models.DateTimeField(default=django.utils.timezone.now),
                ),
                ("resolved_at", models.DateTimeField(blank=True, null=True)),
            ],
            options={
                "ordering": ["-created_at"],
            },
        ),
    ]

# Generated manually for OrganizationUpstreamCa

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("auth_api", "0006_unique_user_email"),
        ("core", "0015_model_state_and_audit_log"),
    ]

    operations = [
        migrations.CreateModel(
            name="OrganizationUpstreamCa",
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
                ("cert_bytes", models.BinaryField()),
                ("sha256", models.CharField(db_index=True, max_length=64)),
                ("size_bytes", models.PositiveIntegerField()),
                ("original_filename", models.CharField(blank=True, max_length=255)),
                ("uploaded_at", models.DateTimeField(auto_now_add=True)),
                (
                    "organization",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="upstream_ca",
                        to="auth_api.organization",
                    ),
                ),
                (
                    "uploaded_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="uploaded_org_upstream_cas",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name": "Organization upstream CA",
                "verbose_name_plural": "Organization upstream CAs",
            },
        ),
    ]

# Add organization FK to Endpoint for multi-tenant AIGuardX

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("auth_api", "0004_organization_and_userprofile_organization"),
        ("core", "0004_alter_gatewayapikey_permissions_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="endpoint",
            name="organization",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="endpoints",
                to="auth_api.organization",
            ),
        ),
    ]

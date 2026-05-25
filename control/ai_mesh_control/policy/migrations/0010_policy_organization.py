# Add organization FK to Policy for multi-tenant AIGuardX

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("auth_api", "0004_organization_and_userprofile_organization"),
        ("policy", "0009_policy_is_system"),
    ]

    operations = [
        migrations.AddField(
            model_name="policy",
            name="organization",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="policies",
                to="auth_api.organization",
            ),
        ),
    ]

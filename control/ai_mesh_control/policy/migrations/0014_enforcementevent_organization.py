# Generated manually for EnforcementEvent organization FK

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('auth_api', '0005_backfill_default_organization'),
        ('policy', '0013_vectorcollectionpolicy_organization_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='enforcementevent',
            name='organization',
            field=models.ForeignKey(
                blank=True,
                db_index=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='enforcement_events',
                to='auth_api.organization',
            ),
        ),
    ]

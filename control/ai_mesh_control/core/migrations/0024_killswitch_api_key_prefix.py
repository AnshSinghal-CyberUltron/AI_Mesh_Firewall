"""Migration: optional credential-scoped kill-switch prefix."""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0023_remove_aiguardx_source_choice"),
    ]

    operations = [
        migrations.AddField(
            model_name="killswitch",
            name="api_key_prefix",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Optional API key prefix for credential-scoped kill-switch. Blank = org-wide.",
                max_length=64,
            ),
        ),
        migrations.AlterUniqueTogether(
            name="killswitch",
            unique_together={("organization", "model_name", "api_key_prefix")},
        ),
    ]

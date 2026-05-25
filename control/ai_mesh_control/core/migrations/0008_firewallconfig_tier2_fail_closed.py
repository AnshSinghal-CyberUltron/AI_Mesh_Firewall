from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0007_firewallconfig"),
    ]

    operations = [
        migrations.AddField(
            model_name="firewallconfig",
            name="tier2_fail_closed_enabled",
            field=models.BooleanField(
                default=True,
                help_text="If Tier-2 scanning degrades (timeout/parse issues), fail closed in block mode.",
            ),
        ),
    ]

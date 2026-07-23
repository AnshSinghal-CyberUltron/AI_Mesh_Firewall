"""Phase 2 (2026-07-23): add the ``detector`` Rule type (state-only choices change).

CharField ``choices`` are not enforced at the DB level, so this AlterField only updates the
migration state / model validation — no data change, no downtime.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("policy", "0036_ev_created_at_brin"),
    ]

    operations = [
        migrations.AlterField(
            model_name="rule",
            name="rule_type",
            field=models.CharField(
                choices=[
                    ("regex", "Regex"),
                    ("keywords", "Keywords"),
                    ("pattern", "Pattern"),
                    ("detector", "Detector class"),
                ],
                default="keywords",
                max_length=32,
            ),
        ),
    ]

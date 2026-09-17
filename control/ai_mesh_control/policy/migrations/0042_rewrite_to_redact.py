"""T01: remap stored Rule.action rewrite to redact (unsupported capability)."""

from django.db import migrations


def _rewrite_to_redact(apps, schema_editor):
    Rule = apps.get_model("policy", "Rule")
    Rule.objects.filter(action="rewrite").update(action="redact")


def _noop(apps, schema_editor):
    return


class Migration(migrations.Migration):
    dependencies = [
        ("policy", "0041_analytics_hourly_facts"),
    ]

    operations = [
        migrations.RunPython(_rewrite_to_redact, _noop),
    ]

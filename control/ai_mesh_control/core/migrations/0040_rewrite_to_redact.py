"""T01: remap stored operator REWRITE to redact (unsupported capability)."""

from django.db import migrations

_OUTPUT_FIELDS = (
    "output_pii_action",
    "output_credential_action",
    "output_ip_leakage_action",
    "output_policy_action",
    "output_hallucination_action",
)


def _rewrite_to_redact(apps, schema_editor):
    FirewallConfig = apps.get_model("core", "FirewallConfig")
    for field in _OUTPUT_FIELDS:
        FirewallConfig.objects.filter(**{field: "rewrite"}).update(**{field: "redact"})


def _noop(apps, schema_editor):
    return


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0039_routing_sensitivity_default_public"),
    ]

    operations = [
        migrations.RunPython(_rewrite_to_redact, _noop),
    ]

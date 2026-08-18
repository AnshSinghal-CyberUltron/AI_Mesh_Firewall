"""D3/R1 — make the data-sensitivity floor safe to enforce.

The gateway now READS ``FirewallConfig.default_data_sensitivity`` and applies it as a
HARD floor when a request does not state its own sensitivity, matching the Routing
Governance copy ("Models below this level are excluded from routing").

That floor cannot be switched on against the old defaults. Two Django defaults collided:

    FirewallConfig.default_data_sensitivity  default = "internal"
    LLMModelConfig.data_sensitivity_level    default = "public"

so every org that never hand-set per-model sensitivity would have had its ENTIRE default
traffic 403 with ``compliance_routing_unsatisfiable`` — verified by running
``build_compliant_fallback_chains`` over an all-public catalogue, which returns an empty
``internal|`` chain.

R1 (chosen over backfilling models to "internal"): flip the field default to "public" and
rewrite the legacy rows that carry "internal" purely because it was the default. This
preserves today's behaviour for every existing org — the floor only bites once an
operator deliberately raises it — and, unlike the alternative, it does not silently
assert on the operator's behalf that their models are approved for internal data.

Reverse restores the old default so the migration is not one-way.
"""
from django.db import migrations, models


def _relax_legacy_internal_floor(apps, schema_editor):
    FirewallConfig = apps.get_model("core", "FirewallConfig")
    # Only rows still sitting on the OLD default are relaxed. A row an operator
    # deliberately raised to confidential/restricted is left exactly as-is.
    changed = list(
        FirewallConfig.objects.filter(default_data_sensitivity="internal")
        .values_list("pk", flat=True)
    )
    if not changed:
        return
    FirewallConfig.objects.filter(pk__in=changed).update(default_data_sensitivity="public")

    # A bulk .update() deliberately bypasses post_save, so the Redis config the GATEWAY
    # actually reads would still advertise the old 'internal' floor. Left unsynced, every
    # request for an org whose models are 'public' 403s with
    # compliance_routing_unsatisfiable until the 120s reconcile happens to run — and
    # forever if that periodic task is not running. Push the corrected config now.
    #
    # Best-effort: a Redis outage must not fail or roll back the schema migration. The
    # periodic reconcile remains the backstop.
    try:
        from core.models import FirewallConfig as LiveFirewallConfig
        from core.signals import sync_firewall_config_to_redis
    except Exception:  # pragma: no cover - app registry not ready
        return
    for cfg in LiveFirewallConfig.objects.filter(pk__in=changed):
        try:
            sync_firewall_config_to_redis(sender=LiveFirewallConfig, instance=cfg, created=False)
        except Exception:  # pragma: no cover - Redis unavailable during migrate
            pass


def _noop_reverse(apps, schema_editor):
    # Deliberately not re-raising rows to "internal": we cannot distinguish a row that
    # was relaxed by this migration from one an operator set to "public" themselves,
    # and guessing wrong would re-introduce the 403 outage this migration exists to
    # prevent. The field default is restored by the AlterField below.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0038_drop_gatewayapikey_ueba_fields"),
    ]

    operations = [
        migrations.AlterField(
            model_name="firewallconfig",
            name="default_data_sensitivity",
            field=models.CharField(
                choices=[
                    ("public", "Public"),
                    ("internal", "Internal"),
                    ("confidential", "Confidential"),
                    ("restricted", "Restricted"),
                ],
                default="public",
                help_text=(
                    "Default data sensitivity applied when a request does not specify one. "
                    "Enforced as a HARD floor: models approved below this level are excluded "
                    "from routing, and a request with no eligible model is refused rather than "
                    "downgraded onto an under-approved model."
                ),
                max_length=16,
            ),
        ),
        migrations.RunPython(_relax_legacy_internal_floor, _noop_reverse),
    ]

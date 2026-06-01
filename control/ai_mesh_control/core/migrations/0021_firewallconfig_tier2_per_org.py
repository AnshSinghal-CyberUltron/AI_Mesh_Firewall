"""Phase 0 D-G2-v3 / D-G3-v3: per-org Tier-2 override + strict mode.

Adds two fields to ``FirewallConfig``:

* ``tier2_enabled`` (BooleanField, null=True, default=None) — tri-state per-org
  override over the gateway-wide TIER2_ENABLED default. ``None`` means "no
  per-org opinion; inherit gateway default". The gateway MUST use ``is None``
  identity checks (not truthiness) to distinguish "no override" from
  "explicit False".
* ``tier2_strict`` (BooleanField, default=True) — when Tier-2 is configured
  but unavailable (breaker OPEN, Bedrock degraded), refuse the request with
  HTTP 451 ``tier2_unavailable_strict`` rather than silently passing through
  Tier-1-only. Default ``True`` per Security Hawk F5 override; operators can
  opt into degraded-pass per-org by setting ``False`` (gateway then emits a
  ``tier2_degraded_pass`` event for each affected request).

This migration is reversible: ``RemoveField`` is the default reverse for
``AddField``, and neither column has data-bearing dependencies elsewhere.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0020_llmmodelconfig_encrypted_api_key"),
    ]

    operations = [
        migrations.AddField(
            model_name="firewallconfig",
            name="tier2_enabled",
            field=models.BooleanField(
                null=True,
                blank=True,
                default=None,
                help_text=(
                    "Per-org override for Tier-2 (Bedrock LLM scan). "
                    "None = inherit gateway default; True/False = explicit override."
                ),
            ),
        ),
        migrations.AddField(
            model_name="firewallconfig",
            name="tier2_strict",
            field=models.BooleanField(
                default=True,
                help_text=(
                    "When Tier-2 is configured but unavailable, refuse the request "
                    "(HTTP 451) rather than passing through Tier-1-only. "
                    "Default True per Phase 0 D-G3-v3."
                ),
            ),
        ),
    ]

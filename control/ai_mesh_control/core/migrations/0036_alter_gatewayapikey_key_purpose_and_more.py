"""UEBA risk_score alignment on GatewayAPIKey (Module 2).

What this migration does
------------------------
Keeps the migration graph node named ``0036_alter_gatewayapikey_key_purpose_and_more``
(merge parents already depend on this filename) but only changes ``risk_score``:

  * help_text documents the unified UEBA final score (0.0 trusted → 1.0 highest risk)
  * Min/Max validators lock the ORM range to [0.0, 1.0]

What it deliberately does NOT do
--------------------------------
``key_purpose`` is NOT altered here (and must not be re-added). Simulator /
scanner identity uses ``project_id`` / name (``simulator-{org-slug}``), not a
purpose label. Older branches that created ``key_purpose`` are cleaned in
``core.0038`` (DROP COLUMN IF EXISTS) — no extra migration files.
"""

import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0035_merge_20260629_0959"),
    ]

    operations = [
        # key_purpose AlterField intentionally omitted — see module docstring.
        # Filename retained so 0037_merge / later leaves stay valid.

        # --- risk_score: document + clamp to UEBA 0.0..1.0 fraction ---
        migrations.AlterField(
            model_name="gatewayapikey",
            name="risk_score",
            field=models.FloatField(
                default=0.0,
                help_text=(
                    "Unified UEBA final risk score "
                    "(0.0 = trusted, 1.0 = highest risk). Written by scoring engine."
                ),
                validators=[
                    django.core.validators.MinValueValidator(0.0),
                    django.core.validators.MaxValueValidator(1.0),
                ],
            ),
        ),
    ]

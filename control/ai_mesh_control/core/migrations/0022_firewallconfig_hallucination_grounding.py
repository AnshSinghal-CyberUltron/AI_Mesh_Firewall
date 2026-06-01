"""
D_G10 / Semantic Hallucination Grounding — per-org FirewallConfig fields.

Adds three fields to `FirewallConfig` so an operator can opt into
semantic (Bedrock Titan v2) or hybrid grounding without changing
gateway env vars:

  * hallucination_grounding_mode        — "lexical" | "semantic" | "hybrid"
  * hallucination_grounding_threshold   — float, default 0.2
  * hallucination_grounding_model       — embedding model id override (default "")

Default "lexical" + 0.2 preserves legacy behaviour exactly, so this
migration is byte-equivalent for any existing tenant until they flip
the mode (D_G10 §1.6).

`dependencies` lists only the latest `core` migration on `main`. It is
explicitly NOT chained to `policy/0024` (D_G5) or `policy/0023` (D_G6) —
see D_G10 A20.
"""

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0021_firewallconfig_tier2_per_org"),
    ]

    operations = [
        migrations.AddField(
            model_name="firewallconfig",
            name="hallucination_grounding_mode",
            field=models.CharField(
                choices=[
                    ("lexical", "Lexical (Jaccard)"),
                    ("semantic", "Semantic (Bedrock Titan v2)"),
                    ("hybrid", "Hybrid (avg lex+sem)"),
                ],
                default="lexical",
                help_text=(
                    "Hallucination grounding scoring mode. 'lexical' = legacy "
                    "Jaccard; 'semantic' = Bedrock Titan v2; 'hybrid' = average."
                ),
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="firewallconfig",
            name="hallucination_grounding_threshold",
            field=models.FloatField(
                default=0.2,
                help_text=(
                    "Minimum risk_score that flags a response as hallucinated. "
                    "Default 0.2 preserves the legacy guard threshold."
                ),
                validators=[MinValueValidator(0.0), MaxValueValidator(1.0)],
            ),
        ),
        migrations.AddField(
            model_name="firewallconfig",
            name="hallucination_grounding_model",
            field=models.CharField(
                blank=True,
                default="",
                help_text=(
                    "Override embedding model id. Empty = use gateway default "
                    "(env BEDROCK_MODEL, typically 'amazon.titan-embed-text-v2:0')."
                ),
                max_length=128,
            ),
        ),
    ]

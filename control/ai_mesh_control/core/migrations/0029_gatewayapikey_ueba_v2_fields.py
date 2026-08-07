"""UEBA v2 columns on GatewayAPIKey (Module 2 identity risk).

What this migration does
------------------------
Adds per-API-key fields the UEBA engine needs so each gateway key can:
  1. start in "learning" mode (build a baseline of normal behavior),
  2. later "graduate" into "active" mode (full risk scoring / kill-switch),
  3. track how much traffic it has seen over its lifetime.

Simulator identity is NOT stored here — use project_id/name
(``simulator-{org-slug}``), not a purpose label.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0028_firewallconfig_mcp_tier2_enabled"),
    ]

    operations = [
        # key_purpose intentionally omitted: simulator keys are identified by
        # project_id/name (simulator-{slug}), not a user-facing purpose label.

        # --- Mode: is this key still learning, or fully scored? ---
        migrations.AddField(
            model_name="gatewayapikey",
            name="ueba_mode",
            field=models.CharField(
                # learning = observe + soft score; active = full UEBA posture
                choices=[("learning", "Learning"), ("active", "Active")],
                db_index=True,  # fleet filters by mode often
                default="learning",  # new keys always start learning
                max_length=16,
            ),
        ),

        # --- Graduation gate: optional per-key override ---
        # learning → active uses REQUEST count only (no days).
        # NULL → org behavior_profile_prompt_target (UI default 50).
        # That same UI knob also drives behavior-profile baseline building.
        migrations.AddField(
            model_name="gatewayapikey",
            name="ueba_graduation_requests",
            field=models.PositiveIntegerField(
                blank=True,
                help_text="Override org prompt-target before active mode.",
                null=True,
            ),
        ),

        # ueba_graduation_days intentionally omitted: days were never in the UI
        # and must not graduate a key without enough requests.
        # Shared DBs that already have that column: core.0039 drops it.

        # --- Lifetime counter: how many requests this key has ever made ---
        # Incremented when telemetry is ingested; compared against
        # ueba_graduation_requests / org behavior_profile_prompt_target.
        migrations.AddField(
            model_name="gatewayapikey",
            name="ueba_lifetime_request_count",
            field=models.PositiveIntegerField(default=0),
        ),

        # --- When the learning baseline was locked (entered active) ---
        # NULL while still learning; set once when the key graduates.
        migrations.AddField(
            model_name="gatewayapikey",
            name="ueba_baseline_locked_at",
            field=models.DateTimeField(blank=True, null=True),
        ),

        # Keep risk_score semantics aligned with UEBA scoring (0.0..1.0 fraction).
        migrations.AlterField(
            model_name="gatewayapikey",
            name="risk_score",
            field=models.FloatField(
                default=0.0,
                help_text=(
                    "Unified UEBA final risk score "
                    "(0.0 = trusted, 1.0 = highest risk). Written by scoring engine."
                ),
                validators=[],
            ),
        ),
    ]

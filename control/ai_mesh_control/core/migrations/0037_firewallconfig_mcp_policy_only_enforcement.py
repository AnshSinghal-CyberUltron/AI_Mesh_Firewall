"""Phase 3 (collapse-to-one-surface): per-org cutover flag.

``mcp_policy_only_enforcement`` — when True the gateway retires the MCP server posture /
scan-control ACTION as a Tier-1 enforcement input (presets observe-only; the seeded detector
policies enforce). Default False; only flip after the org's detector policies are seeded
(policy migration 0038 backfills existing orgs, the org-create signal seeds new ones).
"""
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0036_firewallconfig_mcp_ext_scan_action"),
    ]

    operations = [
        migrations.AddField(
            model_name="firewallconfig",
            name="mcp_policy_only_enforcement",
            field=models.BooleanField(
                default=False,
                help_text=(
                    "Phase 3: retire the MCP server posture / scan-control ACTION as a Tier-1 "
                    "enforcement input — presets observe-only, seeded detector policies enforce. "
                    "Only flip AFTER the org's detector policies are seeded. Tier-2 (LLM judge) "
                    "is unaffected."
                ),
            ),
        ),
    ]

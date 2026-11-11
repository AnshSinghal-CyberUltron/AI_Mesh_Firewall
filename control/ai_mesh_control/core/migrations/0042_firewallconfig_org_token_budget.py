"""GW36 S4 (Req 8): per-org token budget on FirewallConfig.

A non-negative integer token count (0–1,000,000,000, default 100,000) the
projector publishes as a signed StateKind.BUDGET record and the gateway's
per-org BudgetLease spends down. A numeric token count only, never a credential.
"""

import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0041_v2plandocument"),
    ]

    operations = [
        migrations.AddField(
            model_name="firewallconfig",
            name="org_token_budget",
            field=models.PositiveIntegerField(
                default=100000,
                validators=[
                    django.core.validators.MinValueValidator(0),
                    django.core.validators.MaxValueValidator(1000000000),
                ],
                help_text=(
                    "Per-org token budget (0–1,000,000,000 tokens). The gateway's "
                    "per-org lease spends this pool down; refills are generation-gated "
                    "by the projector. Default 100,000. A numeric token count only, "
                    "never a credential."
                ),
            ),
        ),
    ]

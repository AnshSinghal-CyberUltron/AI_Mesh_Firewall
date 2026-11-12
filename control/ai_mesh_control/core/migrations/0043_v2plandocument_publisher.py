"""F4 (PR #115 round 3): stamp each v2 plan document with the publisher fingerprint that built it.

``db_default`` keeps a database default, so an older control image (a rollback) that does not know
the column still inserts rows; ``""`` is always stale, and the next start-up rollout republishes it.
On PostgreSQL 11+ adding a column with a constant default does not rewrite the table.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0042_firewallconfig_org_token_budget"),
    ]

    operations = [
        migrations.AddField(
            model_name="v2plandocument",
            name="publisher",
            field=models.CharField(blank=True, db_default="", default="", max_length=64),
        ),
    ]

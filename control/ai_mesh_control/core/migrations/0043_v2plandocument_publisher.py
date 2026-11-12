"""F4 (PR #115 round 3): stamp each v2 plan document with the publisher that built it.

``publisher`` is the fingerprint, ``publisher_revision`` the revision it was stamped at. An older
control image (a rollback) does not know these columns: ``db_default`` lets it still insert rows
(stamp ``""``, always stale), and when it changes a body it raises ``revision`` past
``publisher_revision``, so the next start-up rollout of this release republishes that org.
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
        migrations.AddField(
            model_name="v2plandocument",
            name="publisher_revision",
            field=models.BigIntegerField(db_default=0, default=0),
        ),
    ]

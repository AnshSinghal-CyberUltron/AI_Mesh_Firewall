"""perf item 18 — standalone BRIN index on EnforcementEvent.created_at.

The soc-kpis view filters `created_at >= since` (a time window). The only prior
index whose tuple includes created_at is the composite
`(organization, event_class, created_at DESC)`, which cannot serve a
created_at-only predicate (its leading column is organization), so the window
degraded to a scan. A BRIN index on created_at gives block-range pruning that
seeks the recent window; BRIN because the table is an append-only event log
(rows inserted in created_at order → the index stays tiny as the table grows).

Built with CREATE INDEX CONCURRENTLY (AddIndexConcurrently + atomic=False) so it
does NOT lock the shared 472 MB table while other sessions read/write it.
"""

from django.contrib.postgres.indexes import BrinIndex
from django.contrib.postgres.operations import AddIndexConcurrently
from django.db import migrations


class Migration(migrations.Migration):
    # CREATE INDEX CONCURRENTLY cannot run inside a transaction.
    atomic = False

    dependencies = [
        ("policy", "0035_vector_provider_reranker_chroma"),
    ]

    operations = [
        AddIndexConcurrently(
            model_name="enforcementevent",
            index=BrinIndex(
                fields=["created_at"],
                name="ev_created_at_brin",
                pages_per_range=64,
                autosummarize=True,
            ),
        ),
    ]

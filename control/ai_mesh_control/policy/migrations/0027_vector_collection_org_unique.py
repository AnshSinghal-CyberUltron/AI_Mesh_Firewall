"""
Bundle X1 — multi-tenant collision fix on VectorCollectionPolicy.

Replaces the unique constraint
    (project_id, collection_name, vector_db_type)
with
    (organization, project_id, collection_name, vector_db_type)

Why this matters:
  * The old constraint is global, not per-org. Two organizations cannot
    independently configure a policy for the same logical triplet (e.g.
    project=default, collection=docs, vdb=pinecone). The second org gets
    an IntegrityError that leaks the existence of the first org's policy.
  * All read paths already filter by ``organization`` via DRF queryset
    scoping, so the only remaining cross-tenant coupling lived in this
    constraint.

Safety:
  * Pure schema migration. No data migration is required because the new
    constraint is strictly more permissive than the old one — any rowset
    that satisfied the old constraint also satisfies the new one.
  * Forward-only migrations follow the standard Django pattern of
    RemoveConstraint -> AddConstraint, which translates to two DDL
    statements on PostgreSQL.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("policy", "0026_merge_g5_g10_event_classes"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="vectorcollectionpolicy",
            name="unique_project_collection_vdb",
        ),
        migrations.AddConstraint(
            model_name="vectorcollectionpolicy",
            constraint=models.UniqueConstraint(
                fields=[
                    "organization",
                    "project_id",
                    "collection_name",
                    "vector_db_type",
                ],
                name="unique_org_project_collection_vdb",
            ),
        ),
    ]

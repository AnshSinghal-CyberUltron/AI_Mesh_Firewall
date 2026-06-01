"""Add tenant-scoping ``organization`` FK to AuditLog + best-effort backfill.

Fixes cross-tenant audit-log destruction: ``cleanup_old_audit_logs`` previously
deleted AuditLog rows by ``created_at`` alone inside a per-org loop, so the org
with the shortest ``retention_days`` wiped every tenant's audit trail. The FK
lets retention be scoped per organization.
"""

from __future__ import annotations

import json

from django.db import migrations, models
import django.db.models.deletion


def backfill_organization(apps, schema_editor):
    """Populate ``organization`` from the ``details`` JSON where present,
    else from the writing user's profile organization."""
    AuditLog = apps.get_model("core", "AuditLog")
    Organization = apps.get_model("auth_api", "Organization")

    # Cache slug/id lookups to avoid per-row queries.
    by_id = {o.id: o.id for o in Organization.objects.all()}
    by_slug = {o.slug: o.id for o in Organization.objects.all()}

    to_update = []
    qs = AuditLog.objects.filter(organization__isnull=True).only(
        "id", "details", "user_id"
    )
    for log in qs.iterator(chunk_size=500):
        org_id = None
        raw = (log.details or "").strip()
        if raw.startswith("{"):
            try:
                data = json.loads(raw)
                cand_id = data.get("organization_id")
                cand_slug = data.get("organization_slug")
                if cand_id in by_id:
                    org_id = by_id[cand_id]
                elif cand_slug in by_slug:
                    org_id = by_slug[cand_slug]
            except (ValueError, TypeError):
                pass
        if org_id is None and log.user_id:
            # UserProfile PK is the user; organization_id lives on the profile.
            UserProfile = apps.get_model("auth_api", "UserProfile")
            prof = UserProfile.objects.filter(user_id=log.user_id).only(
                "organization_id"
            ).first()
            if prof and prof.organization_id in by_id:
                org_id = prof.organization_id
        if org_id is not None:
            log.organization_id = org_id
            to_update.append(log)
        if len(to_update) >= 500:
            AuditLog.objects.bulk_update(to_update, ["organization_id"])
            to_update = []
    if to_update:
        AuditLog.objects.bulk_update(to_update, ["organization_id"])


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0026_firewallconfig_output_guardrails"),
        ("auth_api", "0008_remove_aiguardx_roles"),
    ]

    operations = [
        migrations.AddField(
            model_name="auditlog",
            name="organization",
            field=models.ForeignKey(
                blank=True,
                db_index=True,
                help_text="Owning org for tenant-scoped retention/isolation. Null = system/orphan.",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="audit_logs",
                to="auth_api.organization",
            ),
        ),
        migrations.RunPython(backfill_organization, noop_reverse),
    ]

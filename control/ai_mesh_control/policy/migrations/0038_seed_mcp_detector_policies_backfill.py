"""Phase-2b BACKFILL — seed the MCP detector policies for every EXISTING organization.

Each active org's detector policies replicate its current effective MCP enforcement (server
``default_scan_action`` posture + Tier-1 scan-control actions) as system ``detector`` policies, so
that when an operator flips the Phase-3 ``mcp_policy_only_enforcement`` cutover flag the seeded
policies carry the coverage the retired posture used to provide — no org loses enforcement.

New orgs are seeded by the ``Organization`` post_save signal (``policy.mcp_seed_signals``); this
migration backfills the orgs that already exist. Seeding is IDEMPOTENT (it reconciles to the
current config), so re-running is safe.

Resilience: each org is seeded in its OWN try/except — a failure for one org is logged and skipped,
never aborting the deploy. The seeding reuses the live ``seed_mcp_detector_policies`` (the logic is
too involved to reimplement against historical model state); on a fresh-DB replay where a later
schema field is absent the per-org guard simply skips, and the post_save signal / the
``seed_mcp_detector_policies`` management command remain as backstops.

Reverse is a no-op: the seeded policies are harmless system policies; a rollback leaves them in
place (operators disable/delete via the normal policy UI if desired).
"""
from __future__ import annotations

import logging

from django.db import migrations

logger = logging.getLogger(__name__)


def _backfill_detector_policies(apps, schema_editor):
    Organization = apps.get_model("auth_api", "Organization")
    # Live seeding logic (uses current model classes); imported lazily so a stale import never
    # breaks migration discovery.
    try:
        from policy.mcp_seed import seed_mcp_detector_policies
    except Exception:  # pragma: no cover - if the seeder can't import, skip the backfill entirely
        logger.warning("0038 backfill: seed_mcp_detector_policies unavailable; skipping", exc_info=True)
        return

    seeded = 0
    for org_id in Organization.objects.filter(is_active=True).values_list("id", flat=True):
        try:
            org = Organization.objects.get(id=org_id)
            seed_mcp_detector_policies(org)
            seeded += 1
        except Exception:  # pragma: no cover - never abort the deploy on one org's seeding
            logger.warning("0038 backfill: failed to seed MCP detector policies for org=%s",
                           org_id, exc_info=True)
    logger.info("0038 backfill: seeded MCP detector policies for %d active org(s)", seeded)


def _noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("policy", "0037_rule_detector_type"),
        ("mcp_connector", "0017_alter_mcpserverregistration_default_scan_action"),
        ("core", "0037_firewallconfig_mcp_policy_only_enforcement"),
    ]

    operations = [
        migrations.RunPython(_backfill_detector_policies, reverse_code=_noop_reverse),
    ]

"""
Auto-seed the baseline MCP guardrail policies when an Organization is created:
the PII policy (``seed_mcp_pii_policy``) AND the Phase-2b detector policies that
replicate each active server's posture + scan-control enforcement
(``seed_mcp_detector_policies``). Together with the 0038 backfill data migration
(which seeds every EXISTING org), this guarantees NO org is ever un-seeded — so
the Phase-3 ``mcp_policy_only_enforcement`` cutover flag is always safe to flip
(the seeded detector policies carry the coverage the retired posture provided).
"""

from __future__ import annotations

import logging

from django.db.models.signals import post_save
from django.dispatch import receiver

from auth.models import Organization

logger = logging.getLogger(__name__)


@receiver(post_save, sender=Organization, dispatch_uid="seed_mcp_policies_on_org_create")
def _seed_mcp_policies_on_org_create(sender, instance, created, **kwargs):
    if not created:
        return
    # Never block org creation on seeding — each guardrail is best-effort and logged.
    try:
        from policy.mcp_seed import seed_mcp_pii_policy

        seed_mcp_pii_policy(instance)
    except Exception:  # pragma: no cover
        logger.warning(
            "Failed to auto-seed MCP PII policy for org=%s", instance.id, exc_info=True
        )
    try:
        from policy.mcp_seed import seed_mcp_detector_policies

        seed_mcp_detector_policies(instance)
    except Exception:  # pragma: no cover
        logger.warning(
            "Failed to auto-seed MCP detector policies for org=%s", instance.id, exc_info=True
        )

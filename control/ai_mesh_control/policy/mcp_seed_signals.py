"""
Auto-seed the baseline MCP PII guardrail policy when an Organization is
created. Mirrors the ``seed_mcp_pii_policy`` management command so brand-new
tenants get PII protection on MCP tool calls without a manual step.
"""

from __future__ import annotations

import logging

from django.db.models.signals import post_save
from django.dispatch import receiver

from auth.models import Organization

logger = logging.getLogger(__name__)


@receiver(post_save, sender=Organization, dispatch_uid="seed_mcp_pii_policy_on_org_create")
def _seed_mcp_pii_policy_on_org_create(sender, instance, created, **kwargs):
    if not created:
        return
    try:
        from policy.mcp_seed import seed_mcp_pii_policy

        seed_mcp_pii_policy(instance)
    except Exception:  # pragma: no cover - never block org creation on seeding
        logger.warning(
            "Failed to auto-seed MCP PII policy for org=%s", instance.id, exc_info=True
        )

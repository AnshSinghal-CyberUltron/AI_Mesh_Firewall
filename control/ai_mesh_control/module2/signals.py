"""Signals for Module 2 — threat intel Redis sync on save/delete."""

import logging

from django.db import transaction
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from module2.models import ThreatIntelEntry

logger = logging.getLogger(__name__)


def _schedule_threat_intel_sync(org_id: int) -> None:
    """Defer Redis sync until after commit; never fail the HTTP request on Redis errors."""

    def _do_sync() -> None:
        from module2.tasks import safe_sync_threat_intel_to_redis

        ok, err = safe_sync_threat_intel_to_redis(org_id)
        if not ok:
            logger.warning(
                "Threat intel Redis sync failed for org_id=%s after DB change: %s",
                org_id,
                err,
            )

    transaction.on_commit(_do_sync)


@receiver(post_save, sender=ThreatIntelEntry)
@receiver(post_delete, sender=ThreatIntelEntry)
def sync_threat_intel_on_change(sender, instance, **kwargs):
    if instance.organization_id:
        _schedule_threat_intel_sync(instance.organization_id)

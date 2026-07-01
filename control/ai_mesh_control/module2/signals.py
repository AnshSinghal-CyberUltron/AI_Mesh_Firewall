"""Signals for Module 2 — threat intel Redis sync on save/delete."""

from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from module2.models import ThreatIntelEntry


@receiver(post_save, sender=ThreatIntelEntry)
@receiver(post_delete, sender=ThreatIntelEntry)
def sync_threat_intel_on_change(sender, instance, **kwargs):
    if instance.organization_id:
        from module2.tasks import sync_threat_intel_to_redis
        sync_threat_intel_to_redis.delay(instance.organization_id)

"""Celery tasks for Module 3 — incident emission on critical events."""

import logging

from celery import shared_task
from django.utils import timezone

from policy.constants import ACTION_BLOCK
from policy.models import EnforcementEvent, SecurityIncident

logger = logging.getLogger(__name__)


def _create_module3_incident(org, *, title: str, severity: str, event_type: str, notes: str, extra_meta: dict):
    """Create EnforcementEvent + SecurityIncident for Module 3 telemetry."""
    since = timezone.now() - timezone.timedelta(hours=24)
    if SecurityIncident.objects.filter(
        organization=org,
        title=title,
        status__in=["open", "investigating", "escalated"],
        created_at__gte=since,
    ).exists():
        logger.info("Skipping duplicate Module 3 incident: %s", title)
        return None

    meta = {"event_type": event_type, "source": "module3", **extra_meta}
    event = EnforcementEvent.objects.create(
        organization=org,
        action=ACTION_BLOCK,
        metadata=meta,
    )
    incident = SecurityIncident.objects.create(
        organization=org,
        enforcement_event=event,
        title=title,
        severity=severity,
        status="open",
        notes=notes,
    )
    try:
        from module2.analytics import invalidate_incident_summary_cache

        invalidate_incident_summary_cache(org.id)
    except Exception:
        pass
    return incident


@shared_task(queue="compute.heavy", bind=True, max_retries=2)
def emit_admission_deny_incident(self, org_id: int, artifact_id: int, reason: str = ""):
    from auth.models import Organization
    from module3.models import ModelArtifact

    try:
        org = Organization.objects.get(pk=org_id)
        artifact = ModelArtifact.objects.get(pk=artifact_id, organization=org)
    except (Organization.DoesNotExist, ModelArtifact.DoesNotExist):
        return

    title = f"LLMOps admission denied: {artifact.name}:{artifact.version}"
    notes = reason or f"Deployment blocked for image {artifact.image_ref}"
    _create_module3_incident(
        org,
        title=title,
        severity="high",
        event_type="module3_llmops",
        notes=notes,
        extra_meta={
            "artifact_id": artifact.id,
            "image_ref": artifact.image_ref,
            "signature_status": artifact.signature_status,
        },
    )


@shared_task(queue="compute.heavy", bind=True, max_retries=2)
def emit_embedding_quarantine_incident(
    self, org_id: int, job_id: int, collection: str = "", reason: str = ""
):
    from auth.models import Organization
    from module3.models import EmbeddingInspectionJob

    try:
        org = Organization.objects.get(pk=org_id)
        job = EmbeddingInspectionJob.objects.get(pk=job_id, organization=org)
    except (Organization.DoesNotExist, EmbeddingInspectionJob.DoesNotExist):
        return

    coll = collection or job.collection or "unknown"
    title = f"Embedding quarantined: {coll}"
    notes = reason or job.quarantine_reason or "Poisoned embedding detected by River inspection"
    _create_module3_incident(
        org,
        title=title,
        severity="critical",
        event_type="module3_embedding_poison",
        notes=notes,
        extra_meta={
            "job_id": job.id,
            "collection": coll,
            "anomaly_score": job.anomaly_score,
        },
    )

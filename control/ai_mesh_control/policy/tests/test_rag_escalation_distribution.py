"""T-C9 / X-2: RAG escalation_distribution must be non-zero when source rows exist."""

from __future__ import annotations

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APIClient

from policy.constants import ACTION_BLOCK
from policy.models import EnforcementEvent
from policy.tests.analytics_api_testcase import AnalyticsAPITestCase

User = get_user_model()


class RagEscalationDistributionTests(AnalyticsAPITestCase):
    def setUp(self):
        from auth.models import Organization, UserProfile

        self.org = Organization.objects.create(name="RAG Org", slug="rag-org")
        self.user = User.objects.create_user(username="rag_user", password="pass")
        profile, _ = UserProfile.objects.get_or_create(user=self.user)
        profile.organization = self.org
        profile.save(update_fields=["organization"])
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def _seed(self, *, stage: str, escalation_level: int, extra: bool = True):
        meta = {
            "event_type": "rag_pipeline",
            "pipeline_stage": stage,
            "latency_ms": 12,
        }
        if extra:
            meta["extra"] = {"escalation_level": escalation_level}
        else:
            meta["escalation_level"] = escalation_level
        ev = EnforcementEvent.objects.create(
            organization=self.org,
            action=ACTION_BLOCK,
            metadata=meta,
        )
        EnforcementEvent.objects.filter(pk=ev.pk).update(
            created_at=timezone.now() - timedelta(minutes=5)
        )

    def test_escalation_distribution_counts_when_rows_exist(self):
        self._seed(stage="query", escalation_level=0)
        self._seed(stage="retriever", escalation_level=1)
        self._seed(stage="ranker", escalation_level=2)

        resp = self.client.get("/api/security/rag-pipeline-kpis/?period=24h")
        self.assertEqual(resp.status_code, 200)
        dist = resp.json().get("escalation_distribution") or {}
        self.assertEqual(dist.get("normal"), 1)
        self.assertEqual(dist.get("elevated"), 1)
        self.assertEqual(dist.get("strict"), 1)

    def test_unknown_period_is_400(self):
        resp = self.client.get("/api/security/rag-pipeline-kpis/?period=90d")
        self.assertEqual(resp.status_code, 400)

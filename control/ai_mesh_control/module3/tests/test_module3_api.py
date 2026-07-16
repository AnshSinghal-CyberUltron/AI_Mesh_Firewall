"""API tests for Module 3 LLMOps and K8s firewall endpoints."""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from module3.models import (
    AdmissionDecision,
    ApiGovernanceEvent,
    ApiQuotaPolicy,
    EmbeddingInspectionJob,
    ModelArtifact,
    NetworkPolicyEvent,
)
from policy.models import SecurityIncident

User = get_user_model()


@override_settings(CELERY_TASK_ALWAYS_EAGER=True)
class Module3ApiTests(TestCase):
    def setUp(self):
        from auth.models import Organization, UserProfile

        self.org = Organization.objects.create(name="M3 Org", slug="m3-org")
        self.other_org = Organization.objects.create(name="Other", slug="other-m3")
        self.user = User.objects.create_user(username="m3user", password="pass-m3")
        profile, _ = UserProfile.objects.get_or_create(user=self.user)
        profile.organization = self.org
        profile.save(update_fields=["organization"])

        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

        self.artifact = ModelArtifact.objects.create(
            organization=self.org,
            name="test-model",
            version="1.0.0",
            image_ref="registry.example.com/test:1.0.0",
            data_sha256="a" * 64,
            model_sha256="b" * 64,
            signature_digest="sha256:valid",
            signature_status="pending",
        )

    def test_llmops_summary_returns_kpis(self):
        resp = self.client.get("/api/module3/llmops/summary/?period=24h")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("signed_artifacts_pct", data)
        self.assertGreaterEqual(data["total_artifacts"], 1)

    def test_artifacts_list_org_scoped(self):
        ModelArtifact.objects.create(
            organization=self.other_org,
            name="other",
            version="1",
            image_ref="other:1",
        )
        resp = self.client.get("/api/module3/llmops/artifacts/")
        self.assertEqual(resp.status_code, 200)
        names = [r["name"] for r in resp.json()["results"]]
        self.assertIn("test-model", names)
        self.assertNotIn("other", names)

    @patch("module3.views.verify_admission")
    def test_verify_creates_admission_decision(self, mock_verify):
        mock_verify.return_value = {
            "allowed": True,
            "reason": "ok",
            "latency_ms": 10,
        }
        resp = self.client.post(
            "/api/module3/llmops/verify/",
            {"artifact_id": self.artifact.id},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["decision"]["result"], "allow")
        self.assertTrue(AdmissionDecision.objects.filter(artifact=self.artifact, result="allow").exists())

    @patch("module3.views.verify_admission")
    def test_verify_deny_emits_incident(self, mock_verify):
        mock_verify.return_value = {
            "allowed": False,
            "reason": "invalid signature",
            "latency_ms": 5,
        }
        resp = self.client.post(
            "/api/module3/llmops/verify/",
            {"artifact_id": self.artifact.id, "signature_digest": "invalid"},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["decision"]["result"], "deny")
        self.assertTrue(
            SecurityIncident.objects.filter(
                organization=self.org,
                title__icontains="admission denied",
            ).exists()
        )

    def test_k8s_topology_returns_clusters(self):
        from module3.management.commands.seed_module3 import Command

        Command().handle(org_slug=self.org.slug)
        resp = self.client.get("/api/module3/k8s-firewall/topology/")
        self.assertEqual(resp.status_code, 200)
        self.assertGreaterEqual(len(resp.json()["clusters"]), 1)

    def test_simulator_network_drop(self):
        resp = self.client.post(
            "/api/module3/simulator/ingest/",
            {
                "event_type": "network_drop",
                "layer": "ebpf",
                "source_ref": "bad/pod",
                "dest_ref": "vector/db",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(NetworkPolicyEvent.objects.filter(organization=self.org, action="drop").exists())
        self.assertTrue(
            SecurityIncident.objects.filter(
                organization=self.org,
                title__icontains="K8s network drop",
            ).exists()
        )

    def test_simulator_embedding_poison_creates_incident(self):
        resp = self.client.post(
            "/api/module3/simulator/ingest/",
            {"event_type": "embedding_poison", "collection": "test-coll"},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(
            EmbeddingInspectionJob.objects.filter(organization=self.org, status="quarantined").exists()
        )
        self.assertTrue(
            SecurityIncident.objects.filter(
                organization=self.org,
                title__icontains="Embedding quarantined",
            ).exists()
        )

    @override_settings(AGENT_API_KEY="test-agent-key")
    def test_ingest_rejects_bad_api_key(self):
        anon = APIClient()
        resp = anon.post(
            "/api/module3/ingest/network-event/",
            {
                "layer": "ebpf",
                "action": "drop",
                "source_ref": "a",
                "dest_ref": "b",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 401)

    @override_settings(AGENT_API_KEY="test-agent-key", CELERY_TASK_ALWAYS_EAGER=True)
    def test_agent_network_drop_creates_incident(self):
        anon = APIClient()
        resp = anon.post(
            "/api/module3/ingest/network-event/",
            {
                "organization_slug": self.org.slug,
                "cluster_name": "agent-cluster",
                "layer": "ebpf",
                "action": "drop",
                "source_ref": "bad/agent",
                "dest_ref": "vector/db",
                "reason": "unit test drop",
            },
            format="json",
            HTTP_AUTHORIZATION="Bearer test-agent-key",
        )
        self.assertEqual(resp.status_code, 201)
        self.assertTrue(
            NetworkPolicyEvent.objects.filter(organization=self.org, action="drop").exists()
        )
        self.assertTrue(
            SecurityIncident.objects.filter(
                organization=self.org,
                title__icontains="K8s network drop",
            ).exists()
        )

    def test_api_governance_policy_crud(self):
        self.user.is_staff = True
        self.user.save(update_fields=["is_staff"])
        resp = self.client.post(
            "/api/module3/api-governance/policies/",
            {
                "tenant_id": "acme",
                "environment": "prod",
                "tokens_per_minute": 1000,
                "tokens_per_day": 50000,
                "enabled": True,
            },
            format="json",
        )
        self.assertIn(resp.status_code, (200, 201))
        self.assertTrue(
            ApiQuotaPolicy.objects.filter(
                organization=self.org, tenant_id="acme", environment="prod"
            ).exists()
        )
        listing = self.client.get("/api/module3/api-governance/policies/")
        self.assertEqual(listing.status_code, 200)
        self.assertGreaterEqual(listing.json()["count"], 1)
        summary = self.client.get("/api/module3/api-governance/summary/")
        self.assertEqual(summary.status_code, 200)
        self.assertGreaterEqual(summary.json()["policy_count"], 1)

    @override_settings(AGENT_API_KEY="test-agent-key", CELERY_TASK_ALWAYS_EAGER=True)
    def test_governance_deny_ingest_creates_incident(self):
        ApiQuotaPolicy.objects.create(
            organization=self.org,
            tenant_id="acme",
            environment="prod",
            tokens_per_minute=10,
            tokens_per_day=100,
        )
        anon = APIClient()
        snap = anon.get(
            "/api/module3/ingest/quota-snapshot/?organization_slug=m3-org",
            HTTP_AUTHORIZATION="Bearer test-agent-key",
        )
        self.assertEqual(snap.status_code, 200)
        self.assertIn("acme", snap.json()["quotas"])

        resp = anon.post(
            "/api/module3/ingest/governance-event/",
            {
                "organization_slug": self.org.slug,
                "action": "deny",
                "tenant_id": "acme",
                "environment": "prod",
                "estimated_tokens": 50,
                "path": "/v1/chat",
                "reason": "token quota exceeded",
            },
            format="json",
            HTTP_AUTHORIZATION="Bearer test-agent-key",
        )
        self.assertEqual(resp.status_code, 201)
        self.assertTrue(
            ApiGovernanceEvent.objects.filter(organization=self.org, action="deny").exists()
        )
        self.assertTrue(
            SecurityIncident.objects.filter(
                organization=self.org,
                title__icontains="API governance kill-switch",
            ).exists()
        )

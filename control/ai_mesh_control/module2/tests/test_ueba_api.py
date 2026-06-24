"""API tests for UEBA v2 settings and learning endpoints."""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from core.models import GatewayAPIKey
from module2.models import ApiKeyRiskAssessment, OrgUebaSettings
from policy.constants import ACTION_BLOCK
from policy.models import EnforcementEvent

User = get_user_model()


class UebaV2ApiTests(TestCase):
    def setUp(self):
        from auth.models import Organization, UserProfile

        self.org = Organization.objects.create(name="UEBA Org", slug="ueba-org")
        self.admin = User.objects.create_user(username="uebaadmin", password="pass", is_superuser=True)
        profile, _ = UserProfile.objects.get_or_create(user=self.admin)
        profile.organization = self.org
        profile.save(update_fields=["organization"])

        self.user = User.objects.create_user(username="uebauser", password="pass")
        profile2, _ = UserProfile.objects.get_or_create(user=self.user)
        profile2.organization = self.org
        profile2.save(update_fields=["organization"])

        self.key, _ = GatewayAPIKey.generate_key(
            name="test-key",
            owner=self.user,
            project_id="ueba-test",
        )
        self.key.organization = self.org
        self.key.key_purpose = "production"
        self.key.ueba_mode = "learning"
        self.key.save(update_fields=["organization", "key_purpose", "ueba_mode"])

        self.sim_key, _ = GatewayAPIKey.generate_key(
            name="simulator-default",
            owner=self.user,
            project_id="simulator-default",
        )
        self.sim_key.organization = self.org
        self.sim_key.key_purpose = "simulator"
        self.sim_key.ueba_mode = "learning"
        self.sim_key.risk_score = 0.95
        self.sim_key.save(update_fields=["organization", "key_purpose", "ueba_mode", "risk_score"])
        ApiKeyRiskAssessment.objects.create(
            gateway_api_key=self.sim_key,
            ueba_mode="learning",
            traditional_score=0.95,
            final_score=0.95,
            risk_band="high",
        )
        ApiKeyRiskAssessment.objects.create(
            gateway_api_key=self.key,
            ueba_mode="learning",
            traditional_score=0.2,
            final_score=0.2,
            risk_band="low",
        )

        self.client = APIClient()

    def test_org_settings_get_and_patch(self):
        self.client.force_authenticate(user=self.admin)
        resp = self.client.get("/api/module2/ueba/settings/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["graduation_min_requests"], 50)

        resp = self.client.patch(
            "/api/module2/ueba/settings/",
            {"graduation_min_requests": 40, "graduation_min_days": 5},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["graduation_min_requests"], 40)
        settings = OrgUebaSettings.objects.get(organization=self.org)
        self.assertEqual(settings.graduation_min_days, 5.0)

    def test_per_key_settings_patch(self):
        self.client.force_authenticate(user=self.admin)
        resp = self.client.patch(
            f"/api/module2/ueba/api-keys/{self.key.id}/settings/",
            {"key_purpose": "test", "ueba_graduation_requests": 30},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.key.refresh_from_db()
        self.assertEqual(self.key.key_purpose, "test")
        self.assertEqual(self.key.ueba_graduation_requests, 30)

    def test_learning_keys_list(self):
        self.client.force_authenticate(user=self.user)
        resp = self.client.get("/api/module2/ueba/api-keys/learning/")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertGreaterEqual(data["count"], 2)
        prefixes = {r["prefix"] for r in data["results"]}
        self.assertIn(self.key.prefix, prefixes)

    def test_summary_excludes_simulator_from_high_risk_kpi(self):
        since = timezone.now() - timedelta(hours=1)
        EnforcementEvent.objects.create(
            organization=self.org,
            action=ACTION_BLOCK,
            metadata={"key_prefix": self.sim_key.prefix, "threat_type": "prompt_injection"},
            created_at=since,
        )
        EnforcementEvent.objects.create(
            organization=self.org,
            action=ACTION_BLOCK,
            metadata={"key_prefix": self.key.prefix, "threat_type": "prompt_injection"},
            created_at=since,
        )
        self.client.force_authenticate(user=self.user)
        resp = self.client.get("/api/module2/ueba/api-keys/summary/?period=24h")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["summary"]["high_risk_keys"], 0)

    def test_behavior_without_snapshot_computes_risk_band(self):
        fresh_key, _ = GatewayAPIKey.generate_key(
            name="no-snapshot-key",
            owner=self.user,
            project_id="nosnap",
        )
        fresh_key.organization = self.org
        fresh_key.ueba_mode = "learning"
        fresh_key.save(update_fields=["organization", "ueba_mode"])
        since = timezone.now() - timedelta(hours=1)
        for _ in range(100):
            EnforcementEvent.objects.create(
                organization=self.org,
                action=ACTION_BLOCK,
                metadata={"key_prefix": fresh_key.prefix, "threat_type": "prompt_injection"},
                created_at=since,
            )
        self.client.force_authenticate(user=self.user)
        resp = self.client.get(f"/api/module2/ueba/api-keys/{fresh_key.id}/behavior/?period=24h")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertGreaterEqual(body["risk_score"], 0.5)
        self.assertIn(body["risk_band"], ("medium", "high"))

    def test_org_settings_rejects_invalid_thresholds(self):
        self.client.force_authenticate(user=self.admin)
        resp = self.client.patch(
            "/api/module2/ueba/settings/",
            {"medium_risk_threshold": 0.8, "high_risk_threshold": 0.5},
            format="json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("medium", resp.json()["detail"].lower())

    def test_reassess_creates_snapshot(self):
        since = timezone.now() - timedelta(hours=1)
        for _ in range(5):
            EnforcementEvent.objects.create(
                organization=self.org,
                action=ACTION_BLOCK,
                metadata={"key_prefix": self.key.prefix, "threat_type": "prompt_injection"},
                created_at=since,
            )
        before = ApiKeyRiskAssessment.objects.filter(gateway_api_key=self.key).count()
        self.client.force_authenticate(user=self.admin)
        resp = self.client.post(
            f"/api/module2/ueba/api-keys/{self.key.id}/reassess/",
            {"run_llm": False},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertIn(body["risk_band"], ("low", "medium", "high"))
        self.assertGreaterEqual(body["risk_score"], 0)
        after = ApiKeyRiskAssessment.objects.filter(gateway_api_key=self.key).count()
        self.assertGreater(after, before)
        self.assertIn("computed_at", body)

    def test_baseline_excludes_recent_scoring_window(self):
        from module2.ueba_service import refresh_baseline_for_key

        from policy.constants import ACTION_ALLOW

        now = timezone.now()
        old_time = now - timedelta(hours=48)
        recent_time = now - timedelta(hours=2)
        for _ in range(25):
            EnforcementEvent.objects.create(
                organization=self.org,
                action=ACTION_ALLOW,
                metadata={"key_prefix": self.key.prefix, "threat_type": "none", "model": "gpt-4o"},
                created_at=old_time,
            )
        for _ in range(5):
            EnforcementEvent.objects.create(
                organization=self.org,
                action=ACTION_BLOCK,
                metadata={"key_prefix": self.key.prefix, "threat_type": "none", "model": "gpt-4o"},
                created_at=old_time,
            )
        for _ in range(10):
            EnforcementEvent.objects.create(
                organization=self.org,
                action=ACTION_BLOCK,
                metadata={"key_prefix": self.key.prefix, "threat_type": "prompt_injection", "model": "gpt-4o"},
                created_at=recent_time,
            )
        events = EnforcementEvent.objects.filter(organization=self.org)
        baseline = refresh_baseline_for_key(self.key, events)
        self.assertIsNotNone(baseline)
        self.assertEqual(baseline.sample_count, 30)
        self.assertLess(baseline.avg_block_rate, 0.5)

    def test_assessment_retention_prunes_old_snapshots(self):
        from module2.ueba_service import persist_assessment, prune_assessment_history

        before = ApiKeyRiskAssessment.objects.filter(gateway_api_key=self.key).count()
        for i in range(5):
            persist_assessment(
                self.key,
                {
                    "computed_at": timezone.now(),
                    "ueba_mode": "learning",
                    "traditional_score": 0.1 * i,
                    "llm_score": None,
                    "final_score": 0.1 * i,
                    "risk_band": "low",
                    "score_breakdown": {"mode": "learning"},
                    "llm_verdict": "skipped",
                    "llm_confidence": None,
                    "llm_reasoning": "",
                    "llm_recommended_action": "",
                    "graduation_progress": {},
                },
            )
        self.assertEqual(ApiKeyRiskAssessment.objects.filter(gateway_api_key=self.key).count(), before + 5)
        deleted = prune_assessment_history(self.key, keep=2)
        self.assertEqual(deleted, before + 3)
        self.assertEqual(ApiKeyRiskAssessment.objects.filter(gateway_api_key=self.key).count(), 2)

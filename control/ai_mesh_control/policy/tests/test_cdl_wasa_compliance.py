"""CDL WASA compliance regression tests (control plane)."""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from policy.views import PolicyWriteThrottle

User = get_user_model()

_POLICY_PAYLOAD = {
    "name": "Throttle Test",
    "code": "THR_TEST",
    "severity": "LOW",
    "policy_domain": "pipeline",
    "enabled": True,
}


@override_settings(
    CACHES={
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "cdl-policy-throttle-test",
        }
    }
)
class PolicyWriteThrottleTests(TestCase):
    def setUp(self):
        from auth.models import Organization, UserProfile

        self.org = Organization.objects.create(name="CDL Org", slug="cdl-org")
        self.user = User.objects.create_user(username="cdl_user", password="CdlTest!Pass123")
        profile, _ = UserProfile.objects.get_or_create(user=self.user)
        profile.organization = self.org
        profile.save(update_fields=["organization"])
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        # Tight rate for deterministic 429 on the third write.
        PolicyWriteThrottle.rate = "2/min"

    def test_policy_create_burst_returns_429(self):
        for i in range(2):
            payload = {**_POLICY_PAYLOAD, "code": f"THR_{i}"}
            resp = self.client.post("/api/policies/", payload, format="json")
            self.assertIn(resp.status_code, (201, 200), resp.content)
        resp = self.client.post(
            "/api/policies/",
            {**_POLICY_PAYLOAD, "code": "THR_OVERFLOW"},
            format="json",
        )
        self.assertEqual(resp.status_code, 429)


class SchemaProtectionTests(TestCase):
    def setUp(self):
        from auth.models import Organization, UserProfile

        self.org = Organization.objects.create(name="Schema Org", slug="schema-org")
        self.user = User.objects.create_user(username="schema_user", password="Schema!Pass12345")
        profile, _ = UserProfile.objects.get_or_create(user=self.user)
        profile.organization = self.org
        profile.save(update_fields=["organization"])
        self.admin = User.objects.create_user(
            username="schema_admin", password="Schema!Pass12345", is_staff=True
        )
        admin_profile, _ = UserProfile.objects.get_or_create(user=self.admin)
        admin_profile.organization = self.org
        admin_profile.save(update_fields=["organization"])
        self.client = APIClient()

    def test_schema_unauthenticated_returns_401_or_403(self):
        resp = self.client.get("/api/schema/")
        self.assertIn(resp.status_code, (401, 403))

    def test_schema_authenticated_non_admin_forbidden(self):
        self.client.force_authenticate(user=self.user)
        resp = self.client.get("/api/schema/")
        self.assertEqual(resp.status_code, 403)

    def test_schema_admin_returns_200(self):
        self.client.force_authenticate(user=self.admin)
        resp = self.client.get("/api/schema/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"openapi", resp.content[:200])


class ThreatFeedValidationTests(TestCase):
    def setUp(self):
        from auth.models import Organization, UserProfile

        self.org = Organization.objects.create(name="TF Org", slug="tf-org")
        self.user = User.objects.create_user(username="tf_user", password="TfTest!Pass12345", is_staff=True)
        profile, _ = UserProfile.objects.get_or_create(user=self.user)
        profile.organization = self.org
        profile.save(update_fields=["organization"])
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_malicious_hours_returns_400_not_500(self):
        resp = self.client.get("/api/security/threat-feed/?hours=48'--")
        self.assertEqual(resp.status_code, 400)
        self.assertNotIn(b"Traceback", resp.content)
        self.assertNotIn(b"Internal server error", resp.content)


class PasswordPolicyTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="pw_user", password="OldPass!123456")
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_weak_password_rejected_on_change(self):
        resp = self.client.post(
            "/api/auth/change-password/",
            {
                "old_password": "OldPass!123456",
                "new_password": "short",
                "confirm_password": "short",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_strong_password_accepted(self):
        resp = self.client.post(
            "/api/auth/change-password/",
            {
                "old_password": "OldPass!123456",
                "new_password": "NewStrong!Pass99",
                "confirm_password": "NewStrong!Pass99",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 200)


@override_settings(DEBUG=False, CORS_ALLOW_ALL_ORIGINS=False)
class CorsRegressionTests(TestCase):
    def test_cors_not_wildcard_when_debug_false(self):
        resp = self.client.options(
            "/api/policies/",
            HTTP_ORIGIN="http://evil.example.com",
            HTTP_ACCESS_CONTROL_REQUEST_METHOD="GET",
        )
        acao = resp.get("Access-Control-Allow-Origin", "")
        self.assertNotEqual(acao, "*")

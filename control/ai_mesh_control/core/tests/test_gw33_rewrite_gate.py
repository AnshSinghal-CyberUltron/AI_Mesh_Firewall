"""GW33: REWRITE is saveable only for orgs the deployment enables for the v2 gateway.

``AMF_V2_REWRITE_ORGS`` (comma-separated org slugs or ``*``) defaults to empty, so every org keeps
the T01 L01-2 behaviour (``test_t01_rewrite_reject.py``) until an operator names it. A served org
may save ``rewrite`` on the mapped action fields; ``output_policy_action`` stays unsupported.
"""
from __future__ import annotations

import os
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from auth.models import Organization, UserProfile
from core.firewall_v2_map import REWRITE_TEMPLATES, rewrite_served_by_v2
from core.models import FirewallConfig

User = get_user_model()


class Gw33RewriteGateTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="GW33 Rewrite Org", slug="gw33-rewrite-org")
        self.user = User.objects.create_user(
            username="gw33-rewrite-admin",
            email="gw33-rewrite@example.invalid",
            password="Password123!",
            is_staff=True,
        )
        profile, _ = UserProfile.objects.get_or_create(user=self.user)
        profile.organization = self.org
        profile.save(update_fields=["organization"])
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_default_is_not_served(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AMF_V2_REWRITE_ORGS", None)
            os.environ.pop("V2_REWRITE_ORGS", None)
            self.assertFalse(rewrite_served_by_v2(self.org.slug))
            res = self.client.put(
                reverse("firewall-config"), {"output_pii_action": "rewrite"}, format="json",
            )
        self.assertEqual(res.status_code, 400, res.data)
        self.assertIn("output_pii_action", res.data)

    def test_a_listed_org_can_save_rewrite_on_the_mapped_fields(self):
        with mock.patch.dict(os.environ, {"AMF_V2_REWRITE_ORGS": self.org.slug}):
            self.assertTrue(rewrite_served_by_v2(self.org.slug))
            for field in (
                "output_pii_action",
                "output_credential_action",
                "output_ip_leakage_action",
                "output_hallucination_action",
            ):
                res = self.client.put(reverse("firewall-config"), {field: "rewrite"}, format="json")
                self.assertEqual(res.status_code, 200, (field, res.data))
                self.assertEqual(getattr(FirewallConfig.load(self.org), field), "rewrite")

    def test_an_unlisted_org_is_still_refused_when_another_is_listed(self):
        with mock.patch.dict(os.environ, {"AMF_V2_REWRITE_ORGS": "some-other-org"}):
            res = self.client.put(
                reverse("firewall-config"), {"output_pii_action": "rewrite"}, format="json",
            )
        self.assertEqual(res.status_code, 400, res.data)

    def test_the_platform_template_table_has_an_entry_per_rewritable_category(self):
        for category in ("pii", "credential", "secret", "ip_leak", "exfil", "hallucination", "canary"):
            self.assertTrue(REWRITE_TEMPLATES[category].strip())

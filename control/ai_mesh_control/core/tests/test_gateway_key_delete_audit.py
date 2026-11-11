"""Gateway API key DELETE and purge-revoked write AuditLog rows (#113 review note N3).

Both paths hard-delete keys and were recorded only by logger.info.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

User = get_user_model()


class GatewayKeyDeleteAuditTests(TestCase):
    def setUp(self):
        from auth.models import Organization, Role, UserProfile

        self.org = Organization.objects.create(name="Key Audit Org", slug="key-audit-org")
        self.user = User.objects.create_user(username="key_audit_admin", password="pw")
        profile, _ = UserProfile.objects.get_or_create(user=self.user)
        profile.organization = self.org
        profile.save(update_fields=["organization"])
        role, _ = Role.objects.get_or_create(name="org_admin")
        profile.roles.add(role)
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def _key(self, n, *, active=True):
        from core.models import GatewayAPIKey

        return GatewayAPIKey.objects.create(
            organization=self.org, prefix=f"zs_aud{n}", key_hash=f"{n}" * 64,
            name=f"audit-key-{n}", owner=self.user, project_id="proj", is_active=active,
        )

    def test_delete_writes_audit_row(self):
        from core.models import AuditLog

        key = self._key(1)
        key_id = key.id
        resp = self.client.delete(f"/api/gateways/keys/{key_id}/", REMOTE_ADDR="203.0.113.7")
        self.assertEqual(resp.status_code, 204, resp.content)
        row = AuditLog.objects.get(action="gateway_key_deleted")
        self.assertEqual(row.user, self.user)
        self.assertEqual(row.organization, self.org)
        self.assertEqual(row.resource, f"gateway_api_key:{key_id}")
        self.assertIn("zs_aud1", row.details)
        self.assertEqual(row.ip_address, "203.0.113.7")

    def test_purge_writes_one_audit_row_per_key(self):
        from core.models import AuditLog

        revoked = [self._key(2, active=False), self._key(3, active=False)]
        self._key(4)  # active keys are never purged
        resp = self.client.post(
            "/api/gateways/keys/purge-revoked/", HTTP_X_FORWARDED_FOR="not-an-ip, 10.0.0.1"
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.json()["deleted"], 2)
        rows = AuditLog.objects.filter(action="gateway_key_purged")
        self.assertEqual(
            {r.resource for r in rows}, {f"gateway_api_key:{k.id}" for k in revoked}
        )
        self.assertTrue(all(r.organization_id == self.org.id for r in rows))
        # A forged X-Forwarded-For that is not an address falls back to REMOTE_ADDR.
        self.assertTrue(all(r.ip_address == "127.0.0.1" for r in rows))

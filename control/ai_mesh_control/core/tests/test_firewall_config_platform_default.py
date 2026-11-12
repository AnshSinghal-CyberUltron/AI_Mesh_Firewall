"""#130: only a platform operator may read or change the platform-default FirewallConfig.

The row with ``organization IS NULL`` is the platform default. The v1 gateway serves it to every
org that has no row of its own (``gateway/ai_mesh_gateway/config_sync.py`` ``ConfigSync.get_config``),
so a write to it changes other tenants' posture. Before the fix:

* any authenticated caller without an organization (org-less superuser, a ``createsuperuser``
  account with no profile, an org-less plain user) could PUT it, unaudited;
* GET (and ``/api/security/soc-kpis/``) created it through ``FirewallConfig.load()``, which
  publishes it to Redis;
* any org member could PUT ``{"organization": null}`` and turn their own org's row into a second
  platform-default row (also served to every org), or ``{"organization": <id>}`` to move it onto
  another org.

Now: a caller without an organization who is not a platform operator gets 403 and nothing is
written or published; ``organization`` is read-only in the serializer; an org-less platform
operator's write is audited in the same transaction; reads never create the platform default.
"""

import json
from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from auth.models import Organization, Role, UserProfile
from core.firewall_config_views import PLATFORM_DEFAULT_AUDIT_ACTION, PLATFORM_DEFAULT_RESOURCE
from core.models import AuditLog, FirewallConfig
from policy.tests.analytics_api_testcase import AnalyticsAPITestCase

User = get_user_model()
OFF = {"firewall_enabled": False}
XFF = "203.0.113.9, 10.0.0.1"


def _user(name, org=None, *, profile=True, staff=False, superuser=False, operator=False, admin=False):
    user = User.objects.create_user(
        username=name,
        email=f"{name}@t130.test",
        password="Password123!",
        is_staff=staff or superuser,
        is_superuser=superuser,
    )
    if profile:
        prof, _ = UserProfile.objects.get_or_create(user=user)
        prof.organization = org
        prof.is_platform_operator = operator
        prof.save(update_fields=["organization", "is_platform_operator"])
        if admin:
            prof.roles.add(Role.objects.get_or_create(name="platform_admin")[0])
    return user


class PlatformDefaultFirewallConfigTests(TestCase):
    def setUp(self):
        self.org_a = Organization.objects.create(name="T130 Org A", slug="t130-org-a")
        self.org_b = Organization.objects.create(name="T130 Org B", slug="t130-org-b")
        self.org_c = Organization.objects.create(name="T130 Org C", slug="t130-org-c")  # no row
        self.default = FirewallConfig.objects.create(organization=None)
        self.cfg_a = FirewallConfig.objects.create(organization=self.org_a)
        self.cfg_b = FirewallConfig.objects.create(organization=self.org_b)
        self.operator = _user("op0", staff=True, operator=True)
        self.client = APIClient()

    # -- helpers --

    def _rows(self):
        return list(FirewallConfig.objects.order_by("pk").values())

    def _call(self, user, method, body=None, **extra):
        self.client.force_authenticate(user=user)
        url = reverse("firewall-config")
        if method == "GET":
            return self.client.get(url, **extra)
        return self.client.put(url, body, format="json", **extra)

    def _platform_audit(self):
        return AuditLog.objects.filter(action=PLATFORM_DEFAULT_AUDIT_ACTION)

    def _orgless_non_operators(self):
        return {
            "org-less superuser": _user("s0", superuser=True),
            "superuser without a profile (createsuperuser)": _user("ns0", profile=False, superuser=True),
            "org-less plain user": _user("u0"),
            "org-less staff without the operator flag": _user("st0", staff=True),
            "org-less platform_admin": _user("pa0", admin=True),
            "operator flag without is_staff": _user("fs0", operator=True),
        }

    # -- org-less callers who are not platform operators --

    def test_orgless_non_operator_put_is_refused_and_nothing_is_written_or_published(self):
        for label, user in self._orgless_non_operators().items():
            with self.subTest(label):
                before = self._rows()
                redis_client = mock.MagicMock()
                with (
                    mock.patch("core.signals._get_redis_client", return_value=redis_client),
                    # A plain return value: if a regression lets the write through, the view puts
                    # it in the JSON response, and a bare MagicMock there makes DRF's encoder chase
                    # .tolist() child mocks until the process runs out of memory.
                    mock.patch(
                        "core.firewall_v2_publish.publish_for_org", return_value={"status": "mocked"}
                    ) as publish,
                    self.captureOnCommitCallbacks(execute=True) as callbacks,
                ):
                    res = self._call(user, "PUT", OFF)
                self.assertEqual(res.status_code, 403, res.data)
                self.assertEqual(self._rows(), before)
                self.assertFalse(self._platform_audit().exists())
                self.assertEqual(callbacks, [])
                publish.assert_not_called()
                redis_client.set.assert_not_called()
                redis_client.publish.assert_not_called()

    def test_orgless_non_operator_put_does_not_create_a_missing_default(self):
        self.default.delete()
        for label, user in self._orgless_non_operators().items():
            with self.subTest(label):
                res = self._call(user, "PUT", OFF)
                self.assertEqual(res.status_code, 403, res.data)
                self.assertFalse(FirewallConfig.objects.filter(organization__isnull=True).exists())

    def test_orgless_non_operator_get_is_refused_and_creates_nothing(self):
        self.default.delete()
        for label, user in self._orgless_non_operators().items():
            with self.subTest(label):
                with self.captureOnCommitCallbacks(execute=False) as callbacks:
                    res = self._call(user, "GET")
                self.assertEqual(res.status_code, 403, res.data)
                self.assertNotIn("firewall_enabled", res.data)
                self.assertFalse(FirewallConfig.objects.filter(organization__isnull=True).exists())
                self.assertEqual(callbacks, [])

    # -- org users: unchanged, own org only --

    def test_org_admin_put_changes_only_their_own_org(self):
        admin = _user("a1", self.org_a, admin=True)
        res = self._call(admin, "PUT", OFF)
        self.assertEqual(res.status_code, 200, res.data)
        self.cfg_a.refresh_from_db()
        self.assertFalse(self.cfg_a.firewall_enabled)
        self.assertEqual(self.cfg_a.updated_by, admin)
        for other in (self.default, self.cfg_b):
            other.refresh_from_db()
            self.assertTrue(other.firewall_enabled)
            self.assertIsNone(other.updated_by)
        self.assertFalse(FirewallConfig.objects.filter(organization=self.org_c).exists())
        self.assertFalse(self._platform_audit().exists())

    def test_org_superuser_cannot_change_the_platform_default(self):
        superuser_a = _user("s1", self.org_a, superuser=True)
        default_before = FirewallConfig.objects.filter(pk=self.default.pk).values().get()
        res = self._call(superuser_a, "PUT", {**OFF, "organization": None, "requests_per_minute": 5})
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data["organization"], self.org_a.pk)
        self.assertEqual(FirewallConfig.objects.filter(pk=self.default.pk).values().get(), default_before)
        self.assertEqual(FirewallConfig.objects.filter(organization__isnull=True).count(), 1)
        self.cfg_a.refresh_from_db()
        self.assertEqual((self.cfg_a.organization_id, self.cfg_a.requests_per_minute), (self.org_a.pk, 5))
        self.assertFalse(self._platform_audit().exists())
        # GET answers with org A's row, never the platform default.
        res = self._call(superuser_a, "GET")
        self.assertEqual((res.data["organization"], res.data["requests_per_minute"]), (self.org_a.pk, 5))

    def test_member_cannot_rehome_their_row_into_a_platform_default(self):
        member = _user("m1", self.org_a)
        redis_client = mock.MagicMock()
        with (
            mock.patch("core.signals._get_redis_client", return_value=redis_client),
            self.captureOnCommitCallbacks(execute=True),
        ):
            res = self._call(member, "PUT", {"organization": None, **OFF})
        self.assertEqual(res.status_code, 200, res.data)
        self.cfg_a.refresh_from_db()
        self.assertEqual(self.cfg_a.organization_id, self.org_a.pk)
        self.assertFalse(self.cfg_a.firewall_enabled)
        self.assertEqual(list(FirewallConfig.objects.filter(organization__isnull=True)), [self.default])
        self.default.refresh_from_db()
        self.assertTrue(self.default.firewall_enabled)
        written = [c.args[0] for c in redis_client.set.call_args_list]
        self.assertEqual(written, ["firewall:config:t130-org-a"])

    def test_member_cannot_move_their_row_onto_another_org(self):
        member = _user("m1", self.org_a)
        res = self._call(member, "PUT", {"organization": self.org_c.pk, **OFF})
        self.assertEqual(res.status_code, 200, res.data)
        self.cfg_a.refresh_from_db()
        self.assertEqual(self.cfg_a.organization_id, self.org_a.pk)
        self.assertFalse(FirewallConfig.objects.filter(organization=self.org_c).exists())

    def test_operator_with_an_org_writes_their_own_org_row(self):
        operator_b = _user("op", self.org_b, staff=True, operator=True)
        res = self._call(operator_b, "PUT", OFF)
        self.assertEqual(res.status_code, 200, res.data)
        self.cfg_b.refresh_from_db()
        self.default.refresh_from_db()
        self.assertFalse(self.cfg_b.firewall_enabled)
        self.assertTrue(self.default.firewall_enabled)
        self.assertFalse(self._platform_audit().exists())

    # -- the platform operator without an org --

    def test_platform_operator_put_changes_the_default_publishes_it_and_audits(self):
        redis_client = mock.MagicMock()
        with (
            mock.patch("core.signals._get_redis_client", return_value=redis_client),
            self.captureOnCommitCallbacks(execute=True),
        ):
            # The test client's REMOTE_ADDR is 127.0.0.1. The audit must not take the address from
            # a client-supplied X-Forwarded-For (#122 N2). In production uvicorn's proxy trust still
            # decides REMOTE_ADDR (#154); see _remote_addr.
            res = self._call(
                self.operator, "PUT", {**OFF, "requests_per_minute": 500}, HTTP_X_FORWARDED_FOR=XFF
            )
        self.assertEqual(res.status_code, 200, res.data)
        self.assertIsNone(res.data["organization"])
        self.assertEqual(res.data["gateway_v2_plan"], {"status": "skipped", "reason": "no_org"})
        self.default.refresh_from_db()
        self.assertEqual((self.default.firewall_enabled, self.default.requests_per_minute), (False, 500))
        self.assertEqual(self.default.updated_by, self.operator)
        for other in (self.cfg_a, self.cfg_b):
            other.refresh_from_db()
            self.assertTrue(other.firewall_enabled)

        rows = list(self._platform_audit())
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(
            (row.user, row.organization, row.resource, row.ip_address),
            (self.operator, None, PLATFORM_DEFAULT_RESOURCE, "127.0.0.1"),
        )
        details = json.loads(row.details)
        self.assertEqual(details["changed"], {"firewall_enabled": [True, False], "requests_per_minute": [1000, 500]})
        self.assertEqual(details["requested_fields"], ["firewall_enabled", "requests_per_minute"])
        self.assertEqual(details["x_forwarded_for_client_supplied"], XFF)
        self.assertFalse(details["created"])

        redis_client.set.assert_called_once()
        key, payload = redis_client.set.call_args.args
        self.assertEqual(key, "firewall:config:default")
        self.assertIs(json.loads(payload)["firewall_enabled"], False)
        redis_client.publish.assert_called_once_with(
            "config_updates", json.dumps({"action": "reload", "org_slug": "default"})
        )

    def test_platform_operator_put_creates_a_missing_default_in_the_audited_write(self):
        self.default.delete()
        res = self._call(self.operator, "PUT", OFF)
        self.assertEqual(res.status_code, 200, res.data)
        default = FirewallConfig.objects.get(organization__isnull=True)
        self.assertFalse(default.firewall_enabled)
        details = json.loads(self._platform_audit().get().details)
        self.assertTrue(details["created"])
        self.assertEqual(details["changed"], {"firewall_enabled": [True, False]})

    def test_platform_operator_invalid_put_leaves_no_row_and_no_audit(self):
        self.default.delete()
        res = self._call(self.operator, "PUT", {"requests_per_minute": 999999})
        self.assertEqual(res.status_code, 400, res.data)
        self.assertFalse(FirewallConfig.objects.filter(organization__isnull=True).exists())
        self.assertFalse(self._platform_audit().exists())

    def test_audit_row_and_write_commit_together(self):
        redis_client = mock.MagicMock()
        with (
            mock.patch("core.firewall_config_views.AuditLog.objects.create", side_effect=RuntimeError("audit down")),
            mock.patch("core.signals._get_redis_client", return_value=redis_client),
            self.captureOnCommitCallbacks(execute=True) as callbacks,
        ):
            res = self._call(self.operator, "PUT", OFF)
        # main_app.exception_handlers.safe_exception_handler answers a generic 500.
        self.assertEqual(res.status_code, 500)
        self.assertEqual(callbacks, [])
        redis_client.set.assert_not_called()
        self.default.refresh_from_db()
        self.assertTrue(self.default.firewall_enabled)
        self.assertIsNone(self.default.updated_by)

    def test_platform_operator_get_reads_the_default_without_creating_it(self):
        self.default.firewall_enabled = False
        self.default.save()
        res = self._call(self.operator, "GET")
        self.assertEqual(res.status_code, 200, res.data)
        self.assertIs(res.data["firewall_enabled"], False)

        self.default.delete()
        with self.captureOnCommitCallbacks(execute=False) as callbacks:
            res = self._call(self.operator, "GET")
        self.assertEqual(res.status_code, 200, res.data)
        self.assertIs(res.data["firewall_enabled"], True)  # model default, not a stored row
        self.assertFalse(FirewallConfig.objects.filter(organization__isnull=True).exists())
        self.assertEqual(callbacks, [])


class PlatformDefaultReadsDoNotCreateItTests(TestCase):
    def test_platform_default_returns_the_stored_row_or_an_unsaved_default(self):
        self.assertIsNone(FirewallConfig.platform_default().pk)
        self.assertFalse(FirewallConfig.objects.exists())
        row = FirewallConfig.objects.create(organization=None, retention_days=30)
        self.assertEqual(FirewallConfig.platform_default().pk, row.pk)

    def test_retention_cleanup_does_not_create_the_platform_default(self):
        from core.tasks import cleanup_old_audit_logs

        old = AuditLog.objects.create(action="t130-old", resource="x")
        recent = AuditLog.objects.create(action="t130-recent", resource="x")
        AuditLog.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(days=91))
        AuditLog.objects.filter(pk=recent.pk).update(created_at=timezone.now() - timedelta(days=89))
        cleanup_old_audit_logs()
        self.assertFalse(FirewallConfig.objects.filter(organization__isnull=True).exists())
        self.assertFalse(AuditLog.objects.filter(pk=old.pk).exists())  # model default retention: 90 days
        self.assertTrue(AuditLog.objects.filter(pk=recent.pk).exists())

    def test_retention_cleanup_keeps_platform_default_audit_rows(self):
        from core.tasks import cleanup_old_audit_logs

        # The operator whose changes are audited also sets the default's retention_days.
        FirewallConfig.objects.create(organization=None, retention_days=1)
        trail = AuditLog.objects.create(action=PLATFORM_DEFAULT_AUDIT_ACTION, resource=PLATFORM_DEFAULT_RESOURCE)
        other = AuditLog.objects.create(action="t130-orphan", resource="x")
        AuditLog.objects.filter(pk__in=[trail.pk, other.pk]).update(created_at=timezone.now() - timedelta(days=2))
        cleanup_old_audit_logs()
        self.assertTrue(AuditLog.objects.filter(pk=trail.pk).exists())
        self.assertFalse(AuditLog.objects.filter(pk=other.pk).exists())


class SocKpisDoesNotCreatePlatformDefaultTests(AnalyticsAPITestCase):
    def test_soc_kpis_does_not_create_the_platform_default(self):
        org = Organization.objects.create(name="T130 KPI Org", slug="t130-kpi-org")
        for user in (_user("kpi-member", org), _user("kpi-s0", superuser=True)):
            with self.subTest(user.username):
                client = APIClient()
                client.force_authenticate(user=user)
                res = client.get("/api/security/soc-kpis/?period=24h")
                # Org-less answers are #120/#122's business; this test only pins "no write".
                self.assertLess(res.status_code, 500)
                self.assertFalse(FirewallConfig.objects.filter(organization__isnull=True).exists())

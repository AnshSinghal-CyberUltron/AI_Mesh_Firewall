"""#122: customers can never make themselves (or anyone) superuser or staff; staff grants are audited.

Owner decision (12 Nov 2026): "superuser = ZeroShield staff"; customers are never
superusers or staff. One test per path that could set is_superuser / is_staff /
is_platform_operator (inventory in auth/privilege_audit.py):
- REST: user create, user update (another user, own account), self-service profile,
  the role choices. An org admin tries each; no flag changes and no privilege_change row.
- /admin/: anonymous, a customer org admin (session or login form), a terminated staff
  account and a non-staff superuser-to-be are kept out; active staff get in, each admin
  view writes admin_access, and a flag change there is attributed to the staff member.
- manage.py ensure_zeroshield_admin: audited; refuses to promote an existing non-superuser
  unless --promote-existing.
- Anything else (createsuperuser, shell): audited as "unattributed".
"""

from __future__ import annotations

import datetime as _dt
import json
from io import StringIO
from types import MappingProxyType

from django.contrib import admin as djadmin
from django.contrib.auth import get_user_model
from django.core.management import CommandError, call_command
from django.forms import MultiWidget
from django.test import Client, RequestFactory, TestCase
from rest_framework.test import APIClient

User = get_user_model()
STRONG_PW = "Zs-test-Pw-2026-long!"


def _admin_post_data(model, obj, user, **overrides):
    """A valid Django-admin change-form POST body built from the object's current values."""
    model_admin = djadmin.site._registry[model]
    req = RequestFactory().get("/")
    req.user = user
    form = model_admin.get_form(req, obj, change=True)(instance=obj)
    data = {}
    for name, field in form.fields.items():
        val = form[name].value()
        if isinstance(field.widget, MultiWidget):
            parts = field.widget.decompress(val) if not isinstance(val, list) else val
            for i, part in enumerate(parts):
                if isinstance(part, _dt.date) and not isinstance(part, _dt.datetime):
                    part = part.isoformat()
                elif isinstance(part, _dt.time):
                    part = part.strftime("%H:%M:%S")
                data[f"{name}_{i}"] = "" if part is None else part
            continue
        if val is None:
            continue
        if isinstance(val, bool):
            if val:
                data[name] = "on"
            continue
        if isinstance(val, (list, tuple)):
            data[name] = [str(v) for v in val]
            continue
        if isinstance(val, dict):
            data[name] = json.dumps(val)
            continue
        data[name] = val if isinstance(val, str) else str(val)
    for key, value in overrides.items():
        if value is False:
            data.pop(key, None)
        else:
            data[key] = value
    return data


class _Base(TestCase):
    def setUp(self):
        from auth.models import Organization, Role, UserProfile

        self.org_a = Organization.objects.create(name="Esc A", slug="esc-a")
        padmin, _ = Role.objects.get_or_create(name="platform_admin")
        puser, _ = Role.objects.get_or_create(name="platform_user")

        def user(name, org, *, superuser=False, staff=False, roles=()):
            u = User.objects.create_user(
                username=name, email=f"{name}@example.com", password=STRONG_PW,
                is_superuser=superuser, is_staff=staff or superuser,
            )
            profile, _ = UserProfile.objects.get_or_create(user=u)
            profile.organization = org
            profile.save(update_fields=["organization"])
            for role in roles:
                profile.roles.add(role)
            return u

        self.user = user
        self.org_admin = user("esc_org_admin", self.org_a, roles=(padmin,))
        self.member = user("esc_member", self.org_a, roles=(puser,))
        self.staff = user("esc_staff", None, superuser=True)
        from core.models import AuditLog

        # Setup's own superuser creation is audited too; start each test from a clean slate.
        AuditLog.objects.all().delete()

    def _rows(self, action="privilege_change"):
        from core.models import AuditLog

        return list(AuditLog.objects.filter(action=action).order_by("id"))

    def _flags(self, u):
        u.refresh_from_db()
        return (u.is_superuser, u.is_staff, u.profile.is_platform_operator)


class RestApiEscalationTests(_Base):
    """An org admin tries every REST path that writes users."""

    FLAGS = MappingProxyType({"is_superuser": True, "is_staff": True, "is_platform_operator": True})

    def _client(self):
        client = APIClient()
        client.force_authenticate(user=self.org_admin)
        return client

    def test_create_user_ignores_the_flags(self):
        resp = self._client().post(
            "/api/auth/users/",
            {"first_name": "N", "email": "esc-new@example.com", "password": STRONG_PW,
             "offering": "platform", "role": "admin", **self.FLAGS},
            format="json",
        )
        self.assertEqual(resp.status_code, 201, resp.content)
        created = User.objects.get(email="esc-new@example.com")
        self.assertEqual(self._flags(created), (False, False, False))
        self.assertEqual(resp.json()["platform_role"], "admin")  # an org role, not staff
        self.assertEqual(self._rows(), [])

    def test_create_user_with_a_staff_role_is_refused(self):
        for role in ("superuser", "staff", "platform_operator"):
            resp = self._client().post(
                "/api/auth/users/",
                {"first_name": "N", "email": f"esc-{role}@example.com", "password": STRONG_PW,
                 "offering": "platform", "role": role},
                format="json",
            )
            self.assertEqual(resp.status_code, 400, (role, resp.content))
        self.assertFalse(User.objects.filter(email__startswith="esc-s").exists())

    def test_update_another_user_ignores_the_flags(self):
        resp = self._client().patch(
            f"/api/auth/users/{self.member.id}/?offering=platform",
            {"offering": "platform", "role": "admin", **self.FLAGS}, format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(self._flags(self.member), (False, False, False))
        self.assertEqual(self._rows(), [])

    def test_update_own_account_ignores_the_flags(self):
        resp = self._client().patch(
            f"/api/auth/users/{self.org_admin.id}/?offering=platform",
            {"offering": "platform", **self.FLAGS}, format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(self._flags(self.org_admin), (False, False, False))
        self.assertEqual(self._rows(), [])

    def test_self_service_profile_ignores_the_flags(self):
        resp = self._client().patch("/api/auth/me/profile/", {"first_name": "X", **self.FLAGS}, format="json")
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(self._flags(self.org_admin), (False, False, False))
        resp = self._client().patch("/api/auth/me/profile/", self.FLAGS, format="json")
        self.assertEqual(resp.status_code, 400, resp.content)  # no writable field supplied
        self.assertEqual(self._flags(self.org_admin), (False, False, False))
        self.assertEqual(self._rows(), [])

    def test_me_reports_the_flag_read_only(self):
        body = self._client().get("/api/auth/me/").json()
        self.assertFalse(body["is_superuser"])

    def test_login_does_not_write_privilege_rows(self):
        resp = APIClient().post(
            "/api/auth/token/", {"email": self.staff.email, "password": STRONG_PW}, format="json"
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(self._rows(), [])


class AdminSiteTests(_Base):
    def test_anonymous_is_sent_to_login(self):
        resp = Client().get("/admin/")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/admin/login/", resp["Location"])

    def test_customer_org_admin_session_is_kept_out(self):
        client = Client()
        client.force_login(self.org_admin)
        resp = client.get("/admin/")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/admin/login/", resp["Location"])
        self.assertEqual(client.get("/admin/auth/user/").status_code, 302)
        self.assertEqual(self._rows("admin_access"), [])

    def test_customer_org_admin_cannot_log_in_with_the_form(self):
        client = Client()
        resp = client.post(
            "/admin/login/?next=/admin/", {"username": self.org_admin.username, "password": STRONG_PW}
        )
        self.assertEqual(resp.status_code, 200)  # the form again, with an error
        self.assertNotIn("_auth_user_id", client.session)

    def test_terminated_staff_is_kept_out(self):
        from auth.models import TerminatedSession

        client = Client()
        client.force_login(self.staff)
        self.assertEqual(client.get("/admin/").status_code, 200)
        TerminatedSession.objects.create(user=self.staff, reason="test")
        resp = client.get("/admin/")
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(client.get("/admin/policy/enforcementevent/").status_code, 302)

    def test_inactive_staff_is_kept_out(self):
        client = Client()
        client.force_login(self.staff)
        User.objects.filter(pk=self.staff.pk).update(is_active=False)
        self.assertEqual(client.get("/admin/").status_code, 302)

    def test_staff_get_in_and_each_view_is_audited(self):
        client = Client()
        client.force_login(self.staff)
        self.assertEqual(client.get("/admin/").status_code, 200)
        self.assertEqual(client.get("/admin/policy/enforcementevent/?q=x").status_code, 200)
        rows = self._rows("admin_access")
        self.assertEqual([r.resource for r in rows], ["/admin/", "/admin/policy/enforcementevent/"])
        self.assertEqual({r.user_id for r in rows}, {self.staff.id})
        self.assertEqual(json.loads(rows[1].details)["query"], "q=x")

    def test_staff_granting_superuser_in_admin_is_attributed(self):
        client = Client()
        client.force_login(self.staff)
        data = _admin_post_data(User, self.member, self.staff, is_superuser="on", is_staff="on")
        resp = client.post(f"/admin/auth/user/{self.member.pk}/change/", data)
        self.assertEqual(resp.status_code, 302, resp.content[:2000])
        self.assertEqual(self._flags(self.member)[:2], (True, True))
        rows = self._rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].user_id, self.staff.id)
        details = json.loads(rows[0].details)
        self.assertEqual(details["via"], "django_admin")
        self.assertEqual(details["changes"], {"is_staff": [False, True], "is_superuser": [False, True]})
        self.assertEqual(details["target_user_id"], self.member.id)

    def test_staff_setting_the_operator_flag_in_admin_is_attributed(self):
        from auth.models import UserProfile

        client = Client()
        client.force_login(self.staff)
        profile = UserProfile.objects.get(user=self.member)
        data = _admin_post_data(UserProfile, profile, self.staff, is_platform_operator="on")
        resp = client.post(f"/admin/auth_api/userprofile/{profile.pk}/change/", data)
        self.assertEqual(resp.status_code, 302, resp.content[:2000])
        rows = self._rows()
        self.assertEqual(len(rows), 1)
        details = json.loads(rows[0].details)
        self.assertEqual((rows[0].user_id, details["via"]), (self.staff.id, "django_admin"))
        self.assertEqual(details["changes"], {"is_platform_operator": [False, True]})


class ModelLevelAuditTests(_Base):
    def test_unattributed_grant_and_revoke_are_audited(self):
        u = User.objects.create_superuser(username="esc_cli", email="esc_cli@example.com", password=STRONG_PW)
        u.is_superuser = False
        u.save()
        rows = self._rows()
        self.assertEqual(len(rows), 2)
        first, second = (json.loads(r.details) for r in rows)
        self.assertEqual(first["via"], "unattributed")
        self.assertIsNone(rows[0].user_id)
        self.assertEqual(first["changes"], {"is_staff": [False, True], "is_superuser": [False, True]})
        self.assertEqual(second["changes"], {"is_superuser": [True, False]})

    def test_saves_that_do_not_touch_the_flags_write_nothing(self):
        self.member.first_name = "Renamed"
        self.member.save()
        # A save that names other fields (login's last_login) costs no extra query.
        with self.assertNumQueries(1):
            self.member.save(update_fields=["last_login"])
        self.assertEqual(self._rows(), [])


class EnsureZeroshieldAdminTests(_Base):
    def _run(self, *args):
        out = StringIO()
        call_command("ensure_zeroshield_admin", "--password", STRONG_PW, *args, stdout=out, stderr=StringIO())
        return out.getvalue()

    def test_new_staff_account_is_audited(self):
        self._run("--email", "esc-zs@example.com", "--org-slug", "esc-zeroshield")
        rows = self._rows()
        self.assertEqual(len(rows), 1)
        details = json.loads(rows[0].details)
        self.assertEqual(details["via"], "manage.py ensure_zeroshield_admin")
        self.assertEqual(details["changes"], {"is_staff": [False, True], "is_superuser": [False, True]})

    def test_rerun_for_existing_staff_is_idempotent(self):
        self._run("--email", self.staff.email, "--org-slug", "esc-zeroshield")
        self.assertEqual(self._rows(), [])

    def test_existing_customer_is_not_promoted_by_default(self):
        with self.assertRaises(CommandError):
            self._run("--email", self.member.email, "--org-slug", "esc-zeroshield")
        self.assertEqual(self._flags(self.member), (False, False, False))
        self.member.refresh_from_db()
        self.assertEqual(self.member.profile.organization_id, self.org_a.id)
        self.assertEqual(self._rows(), [])

    def test_existing_account_promoted_on_purpose_is_audited(self):
        self._run("--email", self.member.email, "--org-slug", "esc-zeroshield", "--promote-existing")
        self.assertEqual(self._flags(self.member)[:2], (True, True))
        rows = self._rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(json.loads(rows[0].details)["target_user_id"], self.member.id)
